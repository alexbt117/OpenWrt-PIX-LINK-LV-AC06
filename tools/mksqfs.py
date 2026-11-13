"""Minimal SquashFS 4.0 (xz) writer: builds an image from a directory tree."""
import os, struct, lzma, stat, sys
BS = 1 << 20; BLOG = 20; META = 8192; NOFRAG = 0xffffffff
def xz(data):
    return lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32,
        filters=[{'id': lzma.FILTER_LZMA2, 'preset': 9 | lzma.PRESET_EXTREME, 'dict_size': BS, 'lc': 0, 'lp': 2, 'pb': 2}])
class Meta:
    def __init__(s): s.out = bytearray(); s.buf = bytearray()
    def pos(s): return len(s.out), len(s.buf)
    def write(s, data):
        s.buf += data
        while len(s.buf) >= META: s._flush(META)
    def _flush(s, n):
        chunk = bytes(s.buf[:n]); del s.buf[:n]
        c = xz(chunk)
        if len(c) < len(chunk): s.out += struct.pack('<H', len(c)) + c
        else: s.out += struct.pack('<H', len(chunk) | 0x8000) + chunk
    def finish(s):
        if s.buf: s._flush(len(s.buf))
        return bytes(s.out)
def table(items, per, base):
    """lookup table: metadata blocks of packed items, then index of absolute block positions"""
    out = bytearray(); idx = []
    for i in range(0, len(items), per):
        chunk = b''.join(items[i:i + per]); idx.append(base + len(out))
        c = xz(chunk)
        if len(c) < len(chunk): out += struct.pack('<H', len(c)) + c
        else: out += struct.pack('<H', len(chunk) | 0x8000) + chunk
    start = base + len(out)
    out += b''.join(struct.pack('<Q', p) for p in idx)
    return bytes(out), start
def build(src, mkfs_time, overrides=None):
    overrides = overrides or {}
    src = os.fsencode(src)
    data = bytearray()            # data area, starts at file offset 96
    inodes = Meta(); dirs = Meta()
    frags = []; fragbuf = bytearray()
    ids = []; refs = {}
    counter = [0]
    def idx(i):
        if i not in ids: ids.append(i)
        return ids.index(i)
    def flush_frag():
        if not fragbuf: return
        c = xz(bytes(fragbuf))
        if len(c) < len(fragbuf): frags.append((96 + len(data), len(c))); data.extend(c)
        else: frags.append((96 + len(data), len(fragbuf) | (1 << 24))); data.extend(fragbuf)
        fragbuf.clear()
    def hdr(t, st, ino): return struct.pack('<HHHHII', t, stat.S_IMODE(st.st_mode), idx(st.st_uid), idx(st.st_gid), int(st.st_mtime), ino)
    def ref(m): b, o = m.pos(); return (b << 16) | o
    def process(path, rel, parent_ino):
        st = os.lstat(path); counter[0] += 1; ino = counter[0]; m = st.st_mode
        assert st.st_nlink == 1 or stat.S_ISDIR(m), 'hard links not supported: %r' % path
        if stat.S_ISDIR(m):
            kids = []
            for name in sorted(os.listdir(path)):
                kids.append((name,) + process(path + b'/' + name, rel + b'/' + name, ino))
            dblk, doff = dirs.pos(); size = 0; i = 0
            while i < len(kids):
                blk = kids[i][1] >> 16; base = kids[i][2]; j = i
                while j < len(kids) and j - i < 256 and kids[j][1] >> 16 == blk and -32768 <= kids[j][2] - base <= 32767: j += 1
                h = struct.pack('<III', j - i - 1, blk, base)
                for name, r, kino, t in kids[i:j]:
                    h += struct.pack('<HhHH', r & 0xffff, kino - base, t, len(name) - 1) + name
                dirs.write(h); size += len(h); i = j
            nlink = 2 + sum(1 for k in kids if k[3] == 1)
            r = ref(inodes)
            if size + 3 <= 0xffff:
                inodes.write(hdr(1, st, ino) + struct.pack('<IIHHI', dblk, nlink, size + 3, doff, parent_ino))
            else:
                inodes.write(hdr(8, st, ino) + struct.pack('<IIIIHHI', nlink, size + 3, dblk, parent_ino, 0, doff, 0xffffffff))
            t = 1
        elif stat.S_ISREG(m):
            content = overrides[rel] if rel in overrides else open(path, 'rb').read()
            nfull = len(content) // BS; start = 96 + len(data) if nfull else 0; sizes = []
            for b in range(nfull):
                blk = content[b * BS:(b + 1) * BS]; c = xz(blk)
                if len(c) < len(blk): sizes.append(len(c)); data.extend(c)
                else: sizes.append(len(blk) | (1 << 24)); data.extend(blk)
            tail = content[nfull * BS:]; fi, fo = NOFRAG, 0
            if tail:
                if len(fragbuf) + len(tail) > BS: flush_frag()
                fi, fo = len(frags), len(fragbuf); fragbuf.extend(tail)
            r = ref(inodes)
            inodes.write(hdr(2, st, ino) + struct.pack('<IIII', start, fi, fo, len(content)) + b''.join(struct.pack('<I', s) for s in sizes))
            t = 2
        elif stat.S_ISLNK(m):
            tgt = os.readlink(path); r = ref(inodes)
            inodes.write(hdr(3, st, ino) + struct.pack('<II', 1, len(tgt)) + tgt); t = 3
        elif stat.S_ISCHR(m) or stat.S_ISBLK(m):
            ma, mi = os.major(st.st_rdev), os.minor(st.st_rdev)
            rdev = (ma << 8) | (mi & 0xff) | ((mi & ~0xff) << 12)
            t = 5 if stat.S_ISCHR(m) else 4; r = ref(inodes)
            inodes.write(hdr(t, st, ino) + struct.pack('<II', 1, rdev))
        else:
            t = 6 if stat.S_ISFIFO(m) else 7; r = ref(inodes)
            inodes.write(hdr(t, st, ino) + struct.pack('<I', 1))
        refs[ino] = r
        return r, ino, t
    total = 1 + sum(len(d) + len(f) for _, d, f in os.walk(src))
    root_ref, _, _ = process(src, b'', total + 1)
    assert counter[0] == total
    flush_frag()
    itab = inodes.finish(); dtab = dirs.finish()
    p = 96 + len(data)
    inode_start = p; p += len(itab)
    dir_start = p; p += len(dtab)
    ftab, frag_start = table([struct.pack('<QII', s, z, 0) for s, z in frags], 512, p); p += len(ftab)
    etab, exp_start = table([struct.pack('<Q', refs[i]) for i in range(1, total + 1)], 1024, p); p += len(etab)
    idtab, id_start = table([struct.pack('<I', i) for i in ids], 2048, p); p += len(idtab)
    sb = struct.pack('<IIIIIHHHHHHQQQQQQQQ', 0x73717368, total, mkfs_time, BS, len(frags), 4, BLOG, 0x2c0, len(ids), 4, 0,
                     root_ref, p, id_start, 0xffffffffffffffff, inode_start, dir_start, frag_start, exp_start)
    img = sb + bytes(data) + itab + dtab + ftab + etab + idtab
    assert len(img) == p
    return img
