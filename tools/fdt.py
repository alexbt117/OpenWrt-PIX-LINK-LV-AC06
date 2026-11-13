"""Minimal flattened device tree (DTB) reader/writer, pure Python.

parse(blob) -> (root, reserve_map, meta); build(root, reserve_map, meta) -> blob
Node: .name, .props [[name, bytes], ...], .kids; helpers kid(), get(), set(), rm()
show(node) prints a DTS-like dump.
"""
import struct
BEGIN,END_NODE,PROP,NOP,END=1,2,3,4,9
class Node:
    def __init__(s,name): s.name=name; s.props=[]; s.kids=[]
    def kid(s,n):
        for k in s.kids:
            if k.name==n or k.name.split('@')[0]==n: return k
    def get(s,n):
        for p in s.props:
            if p[0]==n: return p[1]
    def set(s,n,v):
        for p in s.props:
            if p[0]==n: p[1]=v; return
        s.props.append([n,v])
    def rm(s,n): s.props=[p for p in s.props if p[0]!=n]
def parse(b):
    (magic,total,off_st,off_str,off_rsv,ver,lastcomp,boot,sz_str,sz_st)=struct.unpack('>10I',b[:40])
    assert magic==0xd00dfeed
    strs=b[off_str:off_str+sz_str]
    rsv=[];o=off_rsv
    while True:
        a,s=struct.unpack('>QQ',b[o:o+16]);o+=16
        if a==0 and s==0:break
        rsv.append((a,s))
    o=off_st;stack=[];root=None
    while True:
        tok=struct.unpack('>I',b[o:o+4])[0];o+=4
        if tok==BEGIN:
            e=b.index(b'\0',o);n=Node(b[o:e].decode());o=(e+4)&~3
            if stack: stack[-1].kids.append(n)
            else: root=n
            stack.append(n)
        elif tok==END_NODE: stack.pop()
        elif tok==PROP:
            ln,no=struct.unpack('>II',b[o:o+8]);o+=8
            name=strs[no:strs.index(b'\0',no)].decode()
            stack[-1].props.append([name,b[o:o+ln]]);o=(o+ln+3)&~3
        elif tok==NOP: pass
        elif tok==END: break
    return root,rsv,(ver,lastcomp,boot)
def build(root,rsv,meta):
    strs=bytearray();idx={}
    def sid(n):
        if n not in idx: idx[n]=len(strs); strs.extend(n.encode()+b'\0')
        return idx[n]
    out=bytearray()
    def emit(n):
        out.extend(struct.pack('>I',BEGIN));nm=n.name.encode()+b'\0';out.extend(nm+b'\0'*((-len(nm))%4))
        for k,v in n.props:
            out.extend(struct.pack('>III',PROP,len(v),sid(k)));out.extend(v+b'\0'*((-len(v))%4))
        for c in n.kids: emit(c)
        out.extend(struct.pack('>I',END_NODE))
    emit(root);out.extend(struct.pack('>I',END))
    rs=b''.join(struct.pack('>QQ',a,s) for a,s in rsv)+b'\0'*16
    off_rsv=40;off_st=off_rsv+len(rs);off_str=off_st+len(out);total=off_str+len(strs)
    pad=(-total)%4
    hdr=struct.pack('>10I',0xd00dfeed,total+pad,off_st,off_str,off_rsv,meta[0],meta[1],meta[2],len(strs),len(out))
    return hdr+rs+bytes(out)+bytes(strs)+b'\0'*pad
def show(n,ind=0,path=''):
    p=path+'/'+n.name if n.name else ''
    print('  '*ind+(n.name or '/')+' {')
    for k,v in n.props:
        if v and v[-1]==0 and all(32<=c<127 or c==0 for c in v) and b'\0\0' not in v[:-1] and v[0]!=0: s='"'+v[:-1].decode().replace('\0','","')+'"'
        elif len(v)%4==0 and v: s='<'+' '.join(hex(x) for x in struct.unpack('>%dI'%(len(v)//4),v))+'>'
        else: s=v.hex()
        print('  '*(ind+1)+k+' = '+s)
    for c in n.kids: show(c,ind+1,p)
    print('  '*ind+'}')
