#!/usr/bin/env python3
"""Build the PIX-LINK LV-AC06 OpenWrt image from the official ASUS RT-AC1200 one.

Input : openwrt-24.10.5-ramips-mt76x8-asus_rt-ac1200-squashfs-sysupgrade.bin
Output: a sysupgrade image with the PIX-LINK device tree and network defaults.

  1. extract the root filesystem so it can be mounted:
       ./build_image.py extract  <official.bin> rootfs.sqfs
       sudo mount -t squashfs -o loop,ro rootfs.sqfs /mnt/owrt
  2. build (as root, so every file in the mounted tree is readable):
       sudo ./build_image.py build <official.bin> /mnt/owrt <output.bin>

Image layout:
  [uImage header | LZMA(kernel + DTB)] [SquashFS] [0xFF pad to 64 KiB] [DEADC0DE] [metadata + FWx0 trailers]
"""
import hashlib, lzma, struct, sys, zlib
import fdt, mksqfs

BOARD = b'asus,rt-ac1200'          # board name the image keeps (first DT compatible)
MODEL = b'PIX-LINK LV-AC06'
RESET_GPIO, WPS_GPIO = 11, 38      # buttons, active low
STATUS_LED_GPIO, WLAN2G_LED_GPIO = 37, 44   # LEDs, active low
PORTMAP = 0x2f                     # switch ports 0-3 LAN, port 4 WAN

NETWORK_CASE = b'''\tasus,rt-ac1200)
\t\t# PIX-LINK LV-AC06: one VLAN per port (switch ports 0-3 = LAN, 4 = WAN)
\t\tjson_select_object switch
\t\t\tjson_select_object "switch0"
\t\t\t\tjson_add_boolean enable 1
\t\t\t\tjson_add_boolean reset 1
\t\t\t\tjson_select_array roles
\t\t\t\t\tfor port in 0 1 2 3 4; do
\t\t\t\t\t\tjson_add_object
\t\t\t\t\t\t\tjson_add_string ports "$port 6t"
\t\t\t\t\t\tjson_close_object
\t\t\t\t\tdone
\t\t\t\tjson_select ..
\t\t\tjson_select ..
\t\tjson_select ..
\t\tucidef_set_interface_lan "eth0.1 eth0.2 eth0.3 eth0.4"
\t\tucidef_set_interface_wan "eth0.5"
\t\t;;
'''

def crc_fwtool(data):
    """fwtool's CRC32: initial value 0xFFFFFFFF, no final XOR."""
    return (~zlib.crc32(data, 0)) & 0xffffffff

class Image:
    """Splits an OpenWrt ramips sysupgrade image into its parts."""
    def __init__(self, blob):
        self.blob = blob
        magic, _, _, ksize = struct.unpack('>IIII', blob[:16])
        assert magic == 0x27051956, 'not a uImage'
        self.header = blob[:64]
        self.kernel_lzma = blob[64:64 + ksize]
        assert zlib.crc32(self.kernel_lzma) == struct.unpack('>I', blob[24:28])[0], 'kernel CRC mismatch'
        self.sq_start = 64 + ksize
        assert blob[self.sq_start:self.sq_start + 4] == b'hsqs', 'SquashFS not found after kernel'
        self.sq_len = struct.unpack('<Q', blob[self.sq_start + 40:self.sq_start + 48])[0]
        self.mkfs_time = struct.unpack('<I', blob[self.sq_start + 8:self.sq_start + 12])[0]
        marker = (self.sq_start + self.sq_len + 0xffff) & ~0xffff
        assert blob[marker:marker + 4] == b'\xde\xad\xc0\xde', 'end marker not found'
        # metadata blocks, each ending in a 16-byte trailer: magic, crc32, type, pad, size
        self.meta = []; end = len(blob)
        while end > marker + 4:
            magic, crc, size = struct.unpack('>II4xI', blob[end - 16:end])
            assert magic == 0x46577830, 'bad metadata trailer'
            assert crc_fwtool(blob[:end - 16]) == crc, 'metadata CRC mismatch'
            self.meta.insert(0, (blob[end - size:end - 16], blob[end - 16:end]))
            end -= size
        assert end == marker + 4

def patch_dtb(dtb):
    root, rsv, meta = fdt.parse(dtb)
    assert root.get('compatible').split(b'\0')[0] == BOARD
    gpio = root.kid('palmbus').kid('gpio')
    ph = struct.unpack('>I', gpio.get('phandle'))[0]
    pin = lambda n: struct.pack('>III', ph, n, 1)        # <&gpio n GPIO_ACTIVE_LOW>
    root.set('model', MODEL + b'\0')
    keys = root.kid('keys')
    keys.kid('reset').set('gpios', pin(RESET_GPIO))
    keys.kid('wps').set('gpios', pin(WPS_GPIO))
    leds = root.kid('leds')
    leds.kids = [n for n in leds.kids if n.name in ('wps', 'wlan2g')]   # drop the USB LED
    status = leds.kid('wps')                    # node name kept: the led-* aliases point at it
    status.rm('function'); status.rm('color'); status.props.insert(0, ['label', b'status\0'])
    status.set('gpios', pin(STATUS_LED_GPIO))
    wlan = leds.kid('wlan2g'); wlan.set('label', b'wlan2g\0'); wlan.set('gpios', pin(WLAN2G_LED_GPIO))
    gpio.kids = [n for n in gpio.kids if n.name != 'enable-leds']       # ASUS-only GPIO hog
    root.kid('pinctrl').kid('pinctrl0').kid('gpio').set('groups', b'refclk\0wdt\0wled_an\0')
    for n in ('usbphy', 'ehci', 'ohci'): root.kid(n).set('status', b'disabled\0')   # no USB port
    root.kid('esw').set('mediatek,portmap', struct.pack('>I', PORTMAP))
    out = fdt.build(root, rsv, meta); fdt.parse(out)
    return out

def patch_kernel(header, kernel_lzma):
    k = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(kernel_lzma)
    off = k.rfind(b'\xd0\x0d\xfe\xed')
    assert off > 0 and off + struct.unpack('>I', k[off + 4:off + 8])[0] == len(k), 'appended DTB not found'
    k = k[:off] + patch_dtb(k[off:])
    props = kernel_lzma[0]; pb, rem = divmod(props, 45); lp, lc = divmod(rem, 9)
    filt = [{'id': lzma.FILTER_LZMA1, 'dict_size': struct.unpack('<I', kernel_lzma[1:5])[0],
             'lc': lc, 'lp': lp, 'pb': pb, 'preset': 9 | lzma.PRESET_EXTREME}]
    c = bytearray(lzma.compress(k, format=lzma.FORMAT_ALONE, filters=filt))
    assert c[:5] == kernel_lzma[:5]
    c[5:13] = struct.pack('<Q', len(k))        # U-Boot needs the real size, not "unknown"
    c = bytes(c)
    assert lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(c) == k
    h = bytearray(header)
    h[12:16] = struct.pack('>I', len(c)); h[24:28] = struct.pack('>I', zlib.crc32(c))
    h[4:8] = b'\0' * 4; h[4:8] = struct.pack('>I', zlib.crc32(bytes(h)))
    return bytes(h) + c

def patch_network(script):
    def swap(s, old, new):
        assert s.count(old) == 1, 'unexpected 02_network content (different OpenWrt version?)'
        return s.replace(old, new)
    s = swap(script, b'\tasus,rt-ac1200|\\\n\tasus,rt-ac1200-v2|\\\n', NETWORK_CASE + b'\tasus,rt-ac1200-v2|\\\n')
    return swap(s, b'\tasus,rt-ac1200)\n\t\twan_mac=$(mtd_get_mac_binary factory 0x22)\n',
                   b'\tasus,rt-ac1200)\n\t\twan_mac=$(mtd_get_mac_binary factory 0x2e)\n')

def build(official, rootfs_dir, output):
    img = Image(open(official, 'rb').read())
    script = open(rootfs_dir.rstrip('/') + '/etc/board.d/02_network', 'rb').read()
    sq = mksqfs.build(rootfs_dir, img.mkfs_time, {b'/etc/board.d/02_network': patch_network(script)})
    out = patch_kernel(img.header, img.kernel_lzma) + sq
    out += b'\xff' * ((-len(out)) % 0x10000) + b'\xde\xad\xc0\xde'
    for data, trailer in img.meta:
        t = bytearray(trailer); t[4:8] = struct.pack('>I', crc_fwtool(out + data))
        out += data + bytes(t)
    Image(out)                                   # re-parse: checks every CRC and offset
    open(output, 'wb').write(out)
    print('%s  %d bytes\nsha256 %s' % (output, len(out), hashlib.sha256(out).hexdigest()))

def extract(official, output):
    img = Image(open(official, 'rb').read())
    sq = img.blob[img.sq_start:img.sq_start + img.sq_len]
    open(output, 'wb').write(sq + b'\0' * ((-len(sq)) % 4096))   # pad so a loop device sees it all
    print('wrote %s; now:  sudo mount -t squashfs -o loop,ro %s /mnt/owrt' % (output, output))

if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) == 3 and a[0] == 'extract': extract(a[1], a[2])
    elif len(a) == 4 and a[0] == 'build': build(a[1], a[2], a[3])
    else: sys.exit(__doc__)
