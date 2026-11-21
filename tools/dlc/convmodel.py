# Xbox 360 -> PC Split/Second .model converter (first version) and a semantic comparison with the PC file.
# Usage: python -I convmodel.py <xbox.model> <out.model> [<pc.model to compare with>]
import math
import struct
import sys
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.abspath(__file__)))
from mdl import Model

DECL_BYTES = {0: 4, 1: 8, 2: 12, 3: 16, 4: 4, 5: 4, 6: 4, 7: 8, 8: 4, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 14: 4, 15: 4, 16: 8}
HALF_TYPES = {15, 16}            # FLOAT16_2/4: 16-bit words
SHORT_TYPES = {6, 7, 9, 10, 11, 12}  # SHORT/USHORT: 16-bit words
BYTE_TYPES = {4, 5, 8}           # D3DCOLOR, UBYTE4, UBYTE4N: bytes as they are
# FLOAT1..4, UDEC3, DEC3N: 32-bit words


def u32(b, o, E): return struct.unpack_from(E + 'I', b, o)[0]


def swap_words(b, n):
    a = bytearray(b)
    for i in range(0, len(b) - len(b) % n, n):
        a[i:i + n] = b[i:i + n][::-1]
    return a


def strip_to_list(idx):
    # Xbox triangle strips with 0xFFFF as the restart index -> the PC's triangle lists: even triangles
    # (i, i+1, i+2), odd ones (i, i+2, i+1); degenerate triangles stay, as in the PC files
    out = []
    strips, cur = [], []
    for v in idx:
        if v == 0xFFFF:
            strips.append(cur)
            cur = []
        else:
            cur.append(v)
    strips.append(cur)
    for s in strips:
        for i in range(len(s) - 2):
            a, b, c = s[i], s[i + 1], s[i + 2]
            out +=[a, b, c] if i % 2 == 0 else [a, c, b]
    return out


def convert(xdata):
    x = Model(xdata, True)
    blk = bytearray(swap_words(x.block, 4))  # default: every word swapped
    W = lambda o: struct.unpack_from('<I', blk, o)[0]
    put = lambda o, v: struct.pack_into('<I', blk, o, v)
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    # Xbox index sub-records (0x20 bytes, pointed to by the index records): the PC keeps their bytes as on
    # the Xbox, except the data offset at +0x18
    ibs = []
    for i in range(nib):
        r = ibp + 0x10 * i
        sub, count = W(r), W(r + 4)
        raw = x.block[sub:sub + 0x20]
        off = u32(x.block, sub + 0x18, '>')
        size = u32(x.block, sub + 0x1C, '>')
        blk[sub:sub + 0x20] = raw
        struct.pack_into('<I', blk, sub + 0x18, off)
        ibs.append((r, sub, count, off, size))
    vbs = []
    for i in range(nvb):
        r = vbp + 0x1C * i
        ptr, off, size, stride, count = W(r), W(r + 4), W(r + 8), W(r + 12), W(r + 16)
        vbs.append((r, ptr, off, size, stride, count))
    data = x.chunks[0][2]
    # PC chunk: index lists first, then the vertex buffers
    out = bytearray()
    for r, sub, count, off, size in ibs:
        strip = list(struct.unpack_from('>%dH' % count, data, off))
        lst = strip_to_list(strip)
        put(r, 0)
        put(r + 4, len(lst))
        put(r + 12, len(out) << 2)
        out += struct.pack('<%dH' % len(lst), *lst)
    for r, ptr, off, size, stride, count in vbs:
        if ptr:
            blk[ptr:ptr + 0x20] = bytes(0x20)  # the Xbox's own fetch header, not used on the PC
        put(r, 0)
        put(r + 4, len(out))
        out += swap_words(data[off:off + stride * count], 2)  # FLOAT16 vertices: 16-bit words
    # draw records (0x10 bytes: 0, index record, vertex record, primitive type): Xbox triangle strip (6)
    # -> the PC's triangle list (4)
    ib_recs = {r for r, *_ in ibs}
    vb_recs = {r for r, *_ in vbs}
    for o in range(0, len(blk) - 15, 4):
        if W(o + 4) in ib_recs and W(o + 8) in vb_recs and W(o + 12) == 6:
            put(o + 12, 4)
    # header
    res = bytearray()
    res += bytes([x.version, x.flags]) + struct.pack('<H', x.u16) + struct.pack('<I', 0)
    res += struct.pack('<I', len(x.decls))
    for head, els in x.decls:
        res += swap_words(head, 4)
        res += struct.pack('<I', len(els))
        for e in els:
            res += struct.pack('<5I', *e)
    res += struct.pack('<I', len(x.fixups)) + struct.pack('<%dI' % len(x.fixups), *x.fixups)
    res += struct.pack('<I', len(blk))
    while len(res) % 16:
        res += b'\0'
    res += blk
    a, b = x.chunk_counts
    res += struct.pack('<2I', a, b)
    res += struct.pack('<3I', 0, len(out), x.chunks[0][1]) + out
    return bytes(res), ibs, vbs


# ============================================================================================ level models
# Level models (main/powerplay arks, <level>.static.ark) are converted by convert_level(); convert() above stays the
# car rule set (all car declarations are FLOAT16 only, so it swaps every vertex word as 16 bits).
#
# Main block (in addition to the records convert() handles): mesh records start at each pointer fixup location
# (word 0 = 0x88888888, filled with a declaration pointer by the loader; +0x40 = draw record), and a material record
# table of 0x20-byte records {mesh record offset, 0, declaration index, hash, ...}. A mesh can have several material
# records (powerplay models: a second, shorter declaration for another pass).
#
# The PC exporter's vertex data, reconstructed from the 1605 Graveyard model pairs (every vertex of the main,
# powerplay and static/stream arks is reproduced byte for byte):
#   - every material record, in table order, converts the words its declaration covers for the vertices its index
#     list references; a later record overwrites an earlier one. A record whose declaration has another stride than
#     the vertex buffer (a pass declaration) only takes part if each of its elements has the offset and PC type of
#     an element of the same mesh's own-stride declarations. Words of referenced vertices that no declaration
#     covers are swapped as u32.
#   - vertices no index list references are written as zero (PC: 908 such vertices, all zero).
#   - vertex buffers no draw record uses at all: converted with the first declaration of the same stride.
#   - element conversion: FLOAT1..4, UBYTE4N, D3DCOLOR: u32 swap; FLOAT16_2/4, SHORT*: u16 swap; DEC3N (Xbox only)
#     -> UBYTE4N (the declaration type changes 14 -> 8), see dec3n_ubyte4n().
#   - buffer order in the data: index lists first, then vertex buffers, each in the order the material record
#     table first uses them (then the unused ones in record order), packed without padding.
#   - index lists: strips -> lists as for cars; 32-bit indices (record flag 2) exactly when the list has more than
#     0xFFFF indices (as in the frontend models).
DECL_TYPE_PC = {14: 8}           # DEC3N has no PC (D3D9 baseline) support: UBYTE4N
_SWAP2 = {6, 7, 9, 10, 11, 12, 15, 16}
_DEC3N = 14


def dec3n_ubyte4n(w):
    """Xbox DEC3N word (x, y, z 10-bit signed, w 2 bit) -> PC UBYTE4N bytes (x, y, z, 255), as the PC exporter did
    it: each component decoded as (2v + 1) / 1023, the vector normalised, then trunc((f + 1) * 127.5).
    Exact for all 210692 converted normals of the Graveyard main ark (a zero word gives (201, 201, 201): the
    normalised (1, 1, 1) / 1023). Without the renormalisation, v/511 would be off by one in ~30 % of the bytes."""
    f = []
    for c in range(3):
        v = (w >> (10 * c)) & 0x3FF
        if v & 0x200:
            v -= 1024
        f.append((2 * v + 1) / 1023.0)
    n = math.sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2])
    return bytes([min(255, max(0, int((c / n + 1.0) * 127.5))) for c in f] + [255])


def _word_rules(els, stride):
    """{word offset: 's4' | 's2' | 'dec'} for the words a declaration covers inside one vertex."""
    rules = {}
    for e in els:
        t = e[2]
        how = 'dec' if t == _DEC3N else 's2' if t in _SWAP2 else 's4'
        for w in range(e[1], e[1] + DECL_BYTES[t], 4):
            if w < stride:
                rules[w] = how
    return rules


def _convert_vertices(data, stride, count, rules_of, referenced, zero_unref):
    """rules_of(n) -> word rules for vertex n (None: default u32 swap)."""
    out = bytearray(stride * count)
    for n in range(count):
        if zero_unref and n not in referenced:
            continue
        v = data[n * stride:(n + 1) * stride]
        r = rules_of(n) or {}
        o = n * stride
        for w in range(0, stride - stride % 4, 4):
            a = v[w:w + 4]
            how = r.get(w, 's4')
            if how == 's2':
                out[o + w:o + w + 4] = a[1::-1] + a[3:1:-1]
            elif how == 'dec':
                out[o + w:o + w + 4] = dec3n_ubyte4n(struct.unpack('>I', a)[0])
            else:
                out[o + w:o + w + 4] = a[::-1]
        if stride % 4:
            out[o + stride - stride % 4:o + stride] = v[stride - stride % 4:]
    return out


class LevelModelError(Exception):
    pass


def convert_level(xd, xstream=None, platform=bytes(16)):
    """Xbox level .model (+ its .stream.ark entry when the model has no data chunk) -> (PC .model, PC stream entry
    or None, info). PC platform block: 16 zero bytes in every PC level model."""
    x = Model(xd, True)
    if x.end != len(xd):
        raise LevelModelError('trailing bytes after the chunks')
    if x.chunks:
        if x.chunk_counts != (1, 0) or len(x.chunks) != 1:
            raise LevelModelError('chunk counts %s' % (x.chunk_counts,))
        data = x.chunks[0][2]
        sidx = sextra = None
    else:
        if xstream is None:
            raise LevelModelError('no data chunk and no stream entry')
        sidx, ssize, sextra = struct.unpack_from('>3I', xstream, 0)
        if 12 + ssize != len(xstream):
            raise LevelModelError('stream size field %d, entry %d' % (ssize, len(xstream)))
        data = xstream[12:]
    for h, els in x.decls:
        for e in els:
            if e[2] not in DECL_BYTES:
                raise LevelModelError('vertex element type %d' % e[2])
    blk = bytearray(swap_words(x.block, 4))
    W = lambda o: struct.unpack_from('<I', blk, o)[0]
    put = lambda o, v: struct.pack_into('<I', blk, o, v)
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    ib_recs = {ibp + 0x10 * i: i for i in range(nib)}
    vb_recs = {vbp + 0x1C * i: i for i in range(nvb)}
    # draw records (0, index record, vertex record, primitive type): strip (6) -> list (4)
    draws = {}
    for o in range(0, len(blk) - 15, 4):
        if W(o + 4) in ib_recs and W(o + 8) in vb_recs:
            draws[o] = (ib_recs[W(o + 4)], vb_recs[W(o + 8)])
            if W(o + 12) == 6:
                put(o + 12, 4)
    # material records {mesh, 0, decl, hash}: mesh = a fixup location whose +0x40 is a draw record
    fix = set(x.fixups)
    mats = []
    for o in range(0, len(blk) - 15, 4):
        if W(o) in fix and W(o + 4) == 0 and W(o + 8) < len(x.decls):
            dp = W(W(o) + 0x40) if W(o) + 0x44 <= len(blk) else None
            if dp in draws:
                mats.append((o, W(o + 8), draws[dp], W(o)))
    # index lists
    lists = []
    for i in range(nib):
        r = ibp + 0x10 * i
        sub, count = W(r), W(r + 4)
        raw = x.block[sub:sub + 0x20]
        off = struct.unpack_from('>I', raw, 0x18)[0]
        if off + 2 * count > len(data):
            raise LevelModelError('index buffer %d beyond the data' % i)
        blk[sub:sub + 0x20] = raw                    # sub-record bytes as on the Xbox, except the offset
        struct.pack_into('<I', blk, sub + 0x18, off)
        strip = struct.unpack_from('>%dH' % count, data, off)
        lists.append((r, strip_to_list(strip), strip))
    # vertex buffers
    vbs = []
    for i in range(nvb):
        r = vbp + 0x1C * i
        ptr, off, size, stride, count = W(r), W(r + 4), W(r + 8), W(r + 12), W(r + 16)
        if off + stride * count > len(data):
            raise LevelModelError('vertex buffer %d beyond the data' % i)
        if ptr:
            blk[ptr:ptr + 0x20] = bytes(0x20)           # the Xbox's own fetch header, not used on the PC
        vbs.append((r, off, stride, count))
    # per vertex buffer: referenced vertices and the material records that write them
    ref = [set() for _ in range(nvb)]
    for o, (ib, vb) in draws.items():
        ref[vb].update(i for i in lists[ib][2] if i != 0xFFFF)
    writers = [[] for _ in range(nvb)]
    for o, dj, (ib, vb), mesh in mats:
        writers[vb].append((o, dj, {i for i in lists[ib][2] if i != 0xFFFF}, mesh))
    vdata = []
    for v, (r, off, stride, count) in enumerate(vbs):
        src = data[off:off + stride * count]
        if writers[v]:
            # declarations of the buffer's own stride always apply; another (pass) declaration only when every
            # element of it has the offset and the PC type of an element of the same mesh's own-stride
            # declarations (DEC3N counts as UBYTE4N)
            pct = lambda e: (e[1], DECL_TYPE_PC.get(e[2], e[2]))
            own = {}
            for o, dj, verts, mesh in writers[v]:
                if struct.unpack_from('>I', x.decls[dj][0], 0)[0] == stride:
                    own.setdefault(mesh, set()).update(pct(e) for e in x.decls[dj][1])
            per = {}
            for o, dj, verts, mesh in sorted(writers[v]):
                if struct.unpack_from('>I', x.decls[dj][0], 0)[0] != stride and \
                        not all(pct(e) in own.get(mesh, ()) for e in x.decls[dj][1]):
                    continue
                rules = _word_rules(x.decls[dj][1], stride)
                for n in verts:
                    if n in per:
                        per[n] = {**per[n], **rules}
                    else:
                        per[n] = rules
            vdata.append(_convert_vertices(src, stride, count, per.get, ref[v], True))
        else:
            same = [els for h, els in x.decls if struct.unpack_from('>I', h, 0)[0] == stride]
            rules = _word_rules(same[0], stride) if same else {}
            vdata.append(_convert_vertices(src, stride, count, lambda n: rules, ref[v], bool(ref[v])))
    # buffer order: first use in the material record table
    ib_order, vb_order = [], []
    for o, dj, (ib, vb), mesh in sorted(mats):
        if ib not in ib_order:
            ib_order.append(ib)
        if vb not in vb_order:
            vb_order.append(vb)
    ib_order += [i for i in range(nib) if i not in ib_order]
    vb_order += [i for i in range(nvb) if i not in vb_order]
    out = bytearray()
    wide = 0
    for i in ib_order:
        r, lst, strip = lists[i]
        w32 = len(lst) > 0xFFFF
        wide += w32
        put(r, 0)
        put(r + 4, len(lst))
        put(r + 12, (len(out) << 2) | (2 if w32 else 0))
        out += struct.pack('<%d%s' % (len(lst), 'I' if w32 else 'H'), *lst)
    for v in vb_order:
        r = vbs[v][0]
        put(r, 0)
        put(r + 4, len(out))
        out += vdata[v]
    res = bytearray([x.version, x.flags]) + struct.pack('<H', x.u16)
    res += struct.pack('<I', len(platform)) + platform
    res += struct.pack('<I', len(x.decls))
    for head, els in x.decls:
        res += swap_words(head, 4) + struct.pack('<I', len(els))
        for e in els:
            res += struct.pack('<5I', e[0], e[1], DECL_TYPE_PC.get(e[2], e[2]), e[3], e[4])
    res += struct.pack('<I', len(x.fixups)) + struct.pack('<%dI' % len(x.fixups), *x.fixups)
    res += struct.pack('<I', len(blk))
    while len(res) % 16:
        res += b'\0'
    res += blk + struct.pack('<2I', *x.chunk_counts)
    stream = None
    if x.chunks:
        res += struct.pack('<3I', 0, len(out), x.chunks[0][1]) + out
    else:
        stream = struct.pack('<3I', sidx, len(out), sextra) + bytes(out)
    info = dict(ibs=nib, vbs=nvb, wide=wide, mats=len(mats), draws=len(draws),
                unref=sum(vbs[v][3] - len(ref[v]) for v in range(nvb) if ref[v]))
    return bytes(res), stream, info


def stream_key(model_key):
    """.stream.ark key of a chunkless level model: h31 continued over '.stream' (= key of '<model path>.stream';
    187/187 Quarry and 568/568 Graveyard static models)."""
    h = model_key
    for c in b'.stream':
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


def semantic(m):
    """index lists per index record and vertex bytes per vertex buffer, as the PC loader sees them"""
    blk = m.block
    W = lambda o: struct.unpack_from('<I', blk, o)[0]
    data = m.chunks[0][2]
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    ib = []
    for i in range(nib):
        r = ibp + 0x10 * i
        cnt, word = W(r + 4), W(r + 12)
        ib.append(list(struct.unpack_from('<%dH' % cnt, data, word >> 2)))
    vb = []
    for i in range(nvb):
        r = vbp + 0x1C * i
        off, stride, count = W(r + 4), W(r + 12), W(r + 16)
        vb.append(data[off:off + stride * count])
    return ib, vb


if __name__ == '__main__':
    xd = open(sys.argv[1], 'rb').read()
    res, ibs, vbs = convert(xd)
    open(sys.argv[2], 'wb').write(res)
    print('written', len(res), 'bytes;', len(ibs), 'index buffers,', len(vbs), 'vertex buffers')
    if len(sys.argv) > 3:
        pc = Model(open(sys.argv[3], 'rb').read(), False)
        me = Model(res, False)
        print('decls equal', pc.decls == me.decls, '| fixups equal', pc.fixups == me.fixups)
        a, b = semantic(pc), semantic(me)
        for i, (p, q) in enumerate(zip(a[0], b[0])):
            same_set = sorted(tuple(sorted(p[k:k + 3])) for k in range(0, len(p), 3)) == sorted(tuple(sorted(q[k:k + 3])) for k in range(0, len(q), 3))
            print('  index buffer %d: pc %d, ours %d, identical %s, same triangles %s' % (i, len(p), len(q), p == q, same_set))
        for i, (p, q) in enumerate(zip(a[1], b[1])):
            print('  vertex buffer %d: %d bytes, identical %s' % (i, len(p), p == q))
        diff = [o for o in range(0, len(pc.block), 4) if pc.block[o:o + 4] != me.block[o:o + 4]]
        print('  main block words that differ: %d %s' % (len(diff), [hex(o) for o in diff[:24]]))
