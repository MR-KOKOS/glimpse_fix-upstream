# Xbox 360 (big-endian) -> PC (little-endian) converters for the small Split/Second binary formats:
# car .deform / .parts and the level entry kinds (see README.md for the rules and the match counts).
# Usage: python -I convmisc.py <kind|auto> <in> <out>
#        python -I convmisc.py kinds          (list the kinds)
import struct
import sys


def swap4(b, start=0, end=None):
    """4-byte swap of b[start:end] (whole dwords only; a tail < 4 bytes stays)."""
    b = bytearray(b)
    end = len(b) if end is None else end
    n = (end - start) // 4
    if n > 0:
        b[start:start + 4 * n] = struct.pack('<%dI' % n, *struct.unpack_from('>%dI' % n, b, start))
    return b


# Embedded C strings in otherwise u32/float data (emitter ids '1268686511.1578698973', names 'DEB_Rockfall_...',
# 'game8' in fixed char buffers of particle/effect records): the PC files keep their bytes. A string here starts on a
# dword boundary, is a run of _STRCH characters ended by a NUL, and is either >= 8 characters long or >= 5 with a
# lower-case letter. Big-endian floats almost never give that: their first byte is the exponent (0x3F/0x40-0x4F
# for usual values, lower-case letters only for |x| > 2^66) and round values end in 00 00.
_STRCH = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-/ ')
_LOWER = set(b'abcdefghijklmnopqrstuvwxyz')


def string_words(b, start=0, end=None):
    """Offsets of the dwords in b[start:end] that belong to embedded strings (see _STRCH)."""
    end = len(b) if end is None else end
    res = set()
    o = start
    while o + 4 <= end:
        if b[o] in _STRCH:
            e = o
            while e < end and b[e] in _STRCH:
                e += 1
            n = e - o
            if (n >= 8 or (n >= 5 and any(c in _LOWER for c in b[o:e]))) and e < end and b[e] == 0:
                # o is dword aligned: the dwords from o up to the one holding the NUL at e
                res.update(range(o, (e & ~3) + 4, 4))
                o = (e & ~3) + 4
                continue
        o += 4
    return {w for w in res if w + 4 <= end}


def swap4_strings(b, start=0, end=None):
    """swap4 of b[start:end] that keeps the dwords of embedded strings as they are, and the dwords 01 00 00 00
    (a bool byte + 3 padding bytes in the same emitter records; as a u32 it would be 16777216, as a float 2.4e-38;
    Graveyard: every one of them is kept on the PC)."""
    end = len(b) if end is None else end
    keep = string_words(b, start, end)
    out = swap4(b, start, end)
    for w in keep:
        out[w:w + 4] = b[w:w + 4]
    for w in range(start, end - 3, 4):
        if b[w:w + 4] == b'\x01\x00\x00\x00':
            out[w:w + 4] = b[w:w + 4]
    return out


class Swapper:
    """Walks a big-endian stream and swaps it field by field into a little-endian copy."""

    def __init__(self, data):
        self.src = bytes(data)
        self.out = bytearray(data)
        self.o = 0

    def u32(self, n=1):
        v = struct.unpack_from('>%dI' % n, self.src, self.o)
        struct.pack_into('<%dI' % n, self.out, self.o, *v)
        self.o += 4 * n
        return v[0] if n == 1 else v

    def u16(self, n=1):
        v = struct.unpack_from('>%dH' % n, self.src, self.o)
        struct.pack_into('<%dH' % n, self.out, self.o, *v)
        self.o += 2 * n
        return v[0] if n == 1 else v

    def raw(self, n):
        if self.o + n > len(self.src):
            raise ValueError('read past the end')
        self.o += n

    def done(self):
        if self.o != len(self.src):
            raise ValueError('parsed %d of %d bytes' % (self.o, len(self.src)))
        return bytes(self.out)


# ---------------------------------------------------------------- car .deform
# PC loader 0xAD6380 ("%s/%s/%s.deform"), every field through the stream reader (u8/u16/u32/float), strings
# as raw 32-byte buffers (stale bytes after the NUL are kept, they are the same on both platforms):
#   u32 magic 'DFM\2' (0x44464D02), u32 nVert, nTri, nEdge, nBox, nPoint, nPanel, nName
#   nVert  x float[4]
#   nTri   x u16[3]
#   nEdge  x {u16 a, u16 b, float rest length, u8, u8}
#   nPoint x {char name[32], float m0[16], float m1[16] (both read as raw 64 bytes on PC, 4-byte swapped
#             on Xbox), u32 a, u32 b}
#   nPanel x {char name[32], u32, u32, 3 x {u32, float, float}, 3 x float[5]}                (0x88)
#   nBox   x {char name[32], u32[7], float[10]}                                              (100)
#   nName  x char name[32]
# Not reproducible (exporter memory garbage, differs between the Xbox and the PC build of the same car):
#   - the bytes after the NUL of the 32-byte name buffers (stale bytes of earlier names/floats),
#   - nPoint.b when nPoint.a == 0xFFFFFFFF (a per-file constant like 0xFFDA447A / 0xFFE16C28).
# The loader ignores both (names are used up to the NUL, b only when a != -1); the Xbox bytes are kept
# (b swapped). conv_deform(..., dontcare=list) collects these byte ranges for the comparison.


def _name(s, dontcare):
    if dontcare is not None:
        z = s.src.find(b'\0', s.o, s.o + 32)
        if z >= 0 and z + 1 < s.o + 32:
            dontcare.append((z + 1, s.o + 32))
    s.raw(32)


def conv_deform(data, dontcare=None):
    s = Swapper(data)
    magic, nv, nt, ne, nbox, npt, npanel, nname = s.u32(8)
    if magic != 0x44464D02:
        raise ValueError('not a DFM2 file')
    s.u32(4 * nv)
    s.u16(3 * nt)
    for _ in range(ne):
        s.u16(2)
        s.u32()
        s.raw(2)
    for _ in range(npt):
        _name(s, dontcare)
        s.u32(32)
        a = s.u32()
        if a == 0xFFFFFFFF and dontcare is not None:
            dontcare.append((s.o, s.o + 4))
        s.u32()
    for _ in range(npanel):
        _name(s, dontcare)
        s.u32(2 + 9 + 15)
    for _ in range(nbox):
        _name(s, dontcare)
        s.u32(17)
    for _ in range(nname):
        _name(s, dontcare)
    return s.done()


# ---------------------------------------------------------------- car .parts
# 40 of 40 base cars: a plain 4-byte swap (all fields are u32/float).
def conv_parts(data):
    return bytes(swap4(data))


# ---------------------------------------------------------------- text kinds
# XML ('<?xml', '<Root'), tweakable dumps ('!directory:'), .params/.txt: the PC copy has CRLF where the Xbox
# copy has bare LF (nothing else differs). Files that already have CRLF or no line break are the same on
# both. Path lists (one path per line, e.g. a level's dependency list 'Environments/Levels/nem_storm/...')
# keep LF but are lower-cased on PC.
_PATHCH = set(b'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-')


def is_text(data):
    if not data:
        return False
    head = data[:4096].rstrip(b'\0')
    if not head or b'\0' in head:
        return False
    # short u32 hash lists without a zero byte (powerplay entries like f4c2e6ba daa46034) are not text: more
    # than 1/8 of their bytes are >= 0x80, real text has at most a few Latin-1 letters
    if sum(1 for c in head if c >= 0x80) * 8 > len(head):
        return False
    return all(c in (9, 10, 13) or 32 <= c < 127 or c >= 0xA0 for c in head)


def is_strtable(data):
    """NUL-separated names ('1\0midday_v02\0', ColorCubes/dirlist): the same on both platforms"""
    return (len(data) >= 2 and 32 < data[0] < 127 and b'\0\0' not in data and b'\0' in data
            and all(c == 0 or 32 <= c < 127 for c in data))


def is_pathlist(data):
    lines = [l for l in data.split(b'\n') if l]
    return bool(lines) and all(b'/' in l and all(c in _PATHCH for c in l) for l in lines)


def conv_text(data):
    if b'\r\n' in data or b'\n' not in data:
        return bytes(data)
    if is_pathlist(data):
        return data.lower()
    return data.replace(b'\n', b'\r\n')


# ---------------------------------------------------------------- .entities ('ENTS')
# 'ENTS' (not reversed on Xbox), u32 0, u32 version 3, u32 count, 16 bytes 0, then per entity:
# NUL-terminated name, u32 n, n bytes of floats (position/orientation, 64 bytes for start positions).
def conv_ents(data):
    s = Swapper(data)
    if data[:4] != b'ENTS':
        raise ValueError('not ENTS')
    s.raw(4)
    s.u32(7)
    while s.o < len(data):
        z = data.index(b'\0', s.o)
        s.raw(z + 1 - s.o)
        n = s.u32()
        if n % 4:
            raise ValueError('ENTS block size %d' % n)
        s.u32(n // 4)
    return s.done()


# ---------------------------------------------------------------- LightRigs/<rig>/<rig>.gbx
# u32 count, count x {u32 type hash, u32 len + chars (no NUL), u32 hash, u32 len + chars, float[12]}
# (fog boxes, ambient light boxes: a box transform).
def _lenstr(s):
    n = s.u32()
    if n > 256:
        raise ValueError('bad string length %d' % n)
    s.raw(n)


def conv_gbx(data):
    s = Swapper(data)
    n = s.u32()
    for _ in range(n):
        s.u32()
        _lenstr(s)
        s.u32()
        _lenstr(s)
        s.u32(12)
    return s.done()


# ---------------------------------------------------------------- u32 streams with length-prefixed names
# Powerplay object lists ('baggerrc_mpr.cat' ...): u32 count, records of u32 hashes/flags, u32 len + chars
# (no NUL, no padding) and floats. Tokenised: a u32 n (3..128) followed by n name characters is a string,
# everything else a 4-byte value. Only used when the whole file tokenises this way and holds a string.
_NAMECH = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_ .-/')


def _lenstr_tokens(data):
    s = Swapper(data)
    names = 0
    while s.o + 4 <= len(data):
        n = struct.unpack_from('>I', data, s.o)[0]
        if 3 <= n <= 128 and s.o + 4 + n <= len(data) and all(c in _NAMECH for c in data[s.o + 4:s.o + 4 + n]):
            s.u32()
            s.raw(n)
            names += 1
        else:
            s.u32()
    return s.done(), names


def _lenstr_records(data):
    """Powerplay placement lists ('pp22_rock_blast4_mpr.cat' ...): u32 count, count x {u32 hash, u32 n + n chars,
    12 floats (3x3 rotation + position), u32, u32}. The PC copy (Graveyard 84c8868f) has the counts, hashes,
    lengths and the two u32s little-endian but the 12 floats still big-endian (the loader takes them as raw bytes).
    Returns the converted bytes, or None if the file does not have this layout."""
    s = Swapper(data)
    try:
        n = s.u32()
        if not 0 < n < 0x10000:
            return None
        for _ in range(n):
            s.u32()
            ln = s.u32()
            if not 3 <= ln <= 128 or not all(c in _NAMECH for c in data[s.o:s.o + ln]):
                return None
            s.raw(ln)
            s.raw(48)
            s.u32(2)
        return s.done()
    except (ValueError, struct.error):
        return None


def conv_lenstr(data):
    r = _lenstr_records(data)
    return r if r is not None else _lenstr_tokens(data)[0]


def is_lenstr(data):
    try:
        return _lenstr_tokens(data)[1] > 0
    except ValueError:
        return False


# ---------------------------------------------------------------- track sideways links
# u32 count, count x {u8 n, n x u16 link index} (the binary of the 'Total Links = ...' text dump).
def conv_links(data):
    s = Swapper(data)
    n = s.u32()
    for _ in range(n):
        if s.o >= len(data):
            raise ValueError('short')
        c = data[s.o]
        if c > 32:
            raise ValueError('link count %d' % c)
        s.raw(1)
        s.u16(c)
    return s.done()


# ---------------------------------------------------------------- 'i' lists (powerplay / route names)
# u8 'i', u8 count, count x {u16 id, NUL-terminated name, u8 flag}. PC copies of the same lists in the
# base levels (e.g. powerplant.ark 38bd7598) have the u16 little-endian.
def conv_ilist(data):
    s = Swapper(data)
    if data[0] != 0x69:
        raise ValueError('not an i-list')
    n = data[1]
    s.raw(2)
    for _ in range(n):
        s.u16()
        z = data.index(b'\0', s.o)
        s.raw(z + 2 - s.o)
    return s.done()


# ---------------------------------------------------------------- particle pools (memory images)
# Particles/EnvironmentParticles/default/Standard.data (version 3) and version-1 pools: header of 9 u32
# {version, byte size, 0, 0, count, 0x10, 0, 0, 0}, count fixed-size C records, then u32s.
# Record = floats/u32s except: +0x18 bool byte (+3 pad), char id[32] at +0x1FC ('1257756479.433359021'),
# char name[32] at +0x21C; version 3 records are 0x2E0 (the version-1 0x240 record + 0xA0) with more
# bool bytes at +0x240, +0x258, +0x260. +0x154 is a u32 that can hold 0x01000000 (swapped).
# Other dwords that read 01 00 00 00 / 01 01 00 00 on Xbox are kept as bool bytes (guess for unseen ones).
POOL_STRIDE = {1: 0x240, 3: 0x2E0}
POOL_RAW = {1: (0x18,), 3: (0x18, 0x240, 0x258, 0x260)}
POOL_U32 = (0x154,)


def _boolish(w):
    return w[2:] == b'\0\0' and 0 < w[0] < 0x10 and w[1] < 0x10


def is_pool(data):
    if len(data) < 0x24:
        return False
    ver, size, z0, z1, n, sixteen = struct.unpack_from('>6I', data)
    if ver not in POOL_STRIDE or z0 or z1 or sixteen != 0x10:
        return False
    S = POOL_STRIDE[ver]
    if n == 0 or 0x24 + n * S > len(data):
        return False
    return all(data[0x24 + i * S + 0x1FC:0x24 + i * S + 0x1FD].isdigit() for i in range(n))


def conv_pool(data):
    ver, n = struct.unpack_from('>I', data, 0)[0], struct.unpack_from('>I', data, 16)[0]
    S = POOL_STRIDE[ver]
    out = swap4(data, 0, 0x24)
    for i in range(n):
        b = 0x24 + i * S
        for d in range(0, S, 4):
            w = data[b + d:b + d + 4]
            if 0x1FC <= d < 0x23C or d in POOL_RAW[ver] or (d not in POOL_U32 and _boolish(w)):
                continue
            out[b + d:b + d + 4] = w[::-1]
    out = swap4(out, 0x24 + n * S)
    return bytes(out)


# ---------------------------------------------------------------- id lists (.emitterControllers etc.)
# u32 version (2 or 5), u32 count, count x {char id[40], char name[40]}, then u32/float data.
def is_idlist(data):
    if len(data) < 8:
        return False
    ver, n = struct.unpack_from('>2I', data)
    if ver not in (2, 5) or n == 0 or 8 + 80 * n > len(data):
        return False
    return all(data[8 + 80 * i:9 + 80 * i].isdigit() and b'.' in data[8 + 80 * i:8 + 80 * i + 40]
               for i in range(min(n, 4)))


def conv_idlist(data):
    # the data after the id table holds more emitter records with char id[]/name[] buffers (Graveyard: 4 of 13
    # lists): those strings stay as they are
    n = struct.unpack_from('>I', data, 4)[0]
    out = swap4(data, 0, 8)
    keep = string_words(data, 8 + 80 * n)
    out = swap4(out, 8 + 80 * n)
    for w in keep:
        out[w:w + 4] = data[w:w + 4]
    return bytes(out)


# ---------------------------------------------------------------- AI racing line (first u32 0x50)
# header u32 {sizeA 0x50, sizeB 0x100, sizeC 0x34, sizeD 0x30, sizeE 8, sizeF 0x10, nA, nB, nC}, then
# A[nA], B[nB], C[nC], D[nA], E[nB], F[nC]; u32/float everywhere except u8 pairs at A+0x44, B+0xF0, D+0x28
# (kept), {u8, u8, u16} at C+0x0C, and u8 x4 at D+0x2C and F+0x0C (kept; Graveyard: '00 01 00 00' / '01 00 00 00'
# on both platforms, zero in nem_storm).
AI_SIZES = (0x50, 0x100, 0x34, 0x30, 8, 0x10)
AI_RAW = {0: (0x44,), 1: (0xF0,), 3: (0x28, 0x2C), 5: (0x0C,)}
AI_U8U8U16 = {2: (0x0C,)}


def _ai_layout(data):
    if len(data) < 0x24 or struct.unpack_from('>6I', data) != AI_SIZES:
        return None
    nA, nB, nC = struct.unpack_from('>3I', data, 24)
    counts = (nA, nB, nC, nA, nB, nC)
    if 0x24 + sum(c * z for c, z in zip(counts, AI_SIZES)) != len(data):
        return None
    return counts


def conv_ailine(data):
    counts = _ai_layout(data)
    if counts is None:
        raise ValueError('not an AI line file')
    out = swap4(data)
    o = 0x24
    for sec, (c, z) in enumerate(zip(counts, AI_SIZES)):
        for i in range(c):
            for d in AI_RAW.get(sec, ()):
                p = o + i * z + d
                out[p:p + 4] = data[p:p + 4]
            for d in AI_U8U8U16.get(sec, ()):
                p = o + i * z + d
                out[p:p + 4] = data[p:p + 2] + data[p + 3:p + 1:-1]
        o += c * z
    return bytes(out)


# ---------------------------------------------------------------- named node list (first float 2.1)
# float, float, u32 n, n x {0x4C raw bytes: char name[8] ('q000'...) + exporter pointers/flags stored
# little-endian on both platforms, then 456 bytes of floats/u32}, then u32s (u32 m, m indices, ...).
# The pointer words (+0x08, +0x10, +0x38 ...) differ between the builds (heap addresses): not reproducible;
# conv_nodes(..., dontcare=list) marks +0x08..+0x4C of every record for the comparison.
NODE_STRIDE, NODE_RAW = 532, 0x4C


def _node_layout(data):
    if len(data) < 16:
        return None
    n = struct.unpack_from('>I', data, 8)[0]
    if n == 0 or 12 + n * NODE_STRIDE + 4 > len(data):
        return None
    for i in range(n):
        name = data[12 + i * NODE_STRIDE:12 + i * NODE_STRIDE + 8].split(b'\0')[0]
        if not name or not name.isalnum():
            return None
    return n


def conv_nodes(data, dontcare=None):
    n = _node_layout(data)
    if n is None:
        raise ValueError('not a node list')
    out = swap4(data)
    for i in range(n):
        b = 12 + i * NODE_STRIDE
        out[b:b + NODE_RAW] = data[b:b + NODE_RAW]
        if dontcare is not None:
            dontcare.append((b + 8, b + NODE_RAW))
    return bytes(out)


def conv_swap4(data):
    # u32/float data; embedded C strings (emitter records in Graveyard 2bb039e2, a25c2d23, d034c782) are kept
    return bytes(swap4_strings(data))


def conv_copy(data):
    return bytes(data)


CONVERTERS = {
    'deform': conv_deform,
    'parts': conv_parts,
    'text': conv_text,
    'ents': conv_ents,
    'gbx': conv_gbx,
    'links': conv_links,
    'ilist': conv_ilist,
    'pool': conv_pool,
    'idlist': conv_idlist,
    'ailine': conv_ailine,
    'nodes': conv_nodes,
    'lenstr': conv_lenstr,
    'swap4': conv_swap4,
    'copy': conv_copy,
}

# Kinds other converters handle (left alone here): textures, shaders, shader instances, FXC, Havok, models.
FOREIGN = {
    b'TEXS': 'textures', b'SHDR': 'shaders', b'SDRI': 'shaderinst', b'\x00FXC': 'fxc',
    b'\x57\xe0\xe0\x57': 'havok', b'\x01\x00\x00\x08': 'model',
}

# Level entries whose name (hence key) is the same in every level, verified on nem_storm.
KEY_KINDS = {
    0x384BF0C8: 'gbx',      # LightRigs/midday_v02/midday_v02.gbx
    0x71C673DC: 'links',
    0xA3D8BB52: 'ailine',
    0x5E1AF73A: 'pool',     # Particles/EnvironmentParticles/default/Standard.data
    0xB77C0FB9: 'idlist',   # Particles/EnvironmentParticles/default/Standard.emitterControllers
}
# nem_storm entries that are exactly a 4-byte swap (u32/float only), e.g. 0x562DB472 = LightRigs/midday_v02/
# midday_v02.lights, 0x940AB544 = .../Standard.emittersurfaces, 0xC1C26292 = track link distances.
for _k in (0x1176D3E8, 0x2174BD19, 0x2BB039E2, 0x335E081B, 0x455045AA, 0x49842DBA, 0x562DB472, 0x5825B84A,
           0x80B911A9, 0x87C7043F, 0x92CE1C88, 0x940AB544, 0x9515E3BB, 0xA25C2D23, 0xA68C699E, 0xA71194BB,
           0xC1C26292, 0xCCDFC28B, 0xDF6CE4E8):
    KEY_KINDS[_k] = 'swap4'


def _parses(fn, data):
    try:
        fn(data)
        return True
    except Exception:
        return False


def detect(data, name='', key=None):
    """Kind of an Xbox file from its name, key or content. Returns (kind, how) with how in
    'name', 'key', 'content', 'foreign' (not handled here) or 'default' (no rule: plain 4-byte swap)."""
    n = name.lower().replace('\\', '/')
    for ext, kd in (('.deform', 'deform'), ('.parts', 'parts'), ('.entities', 'ents'), ('.gbx', 'gbx'),
                    ('.xml', 'text'), ('.params', 'text'), ('.txt', 'text')):
        if n.endswith(ext):
            return kd, 'name'
    m = data[:4]
    if m in FOREIGN:
        return FOREIGN[m], 'foreign'
    if m == b'<?xm' and b'<hkpackfile' in data[:200]:
        return 'havok-xml', 'foreign'
    if key in KEY_KINDS:
        return KEY_KINDS[key], 'key'
    if m == b'DFM\x02':
        return 'deform', 'content'
    if m == b'ENTS':
        return 'ents', 'content'
    if is_text(data):
        return 'text', 'content'
    if is_strtable(data):
        return 'copy', 'content'
    if data[:1] == b'i' and len(data) >= 2 and _parses(conv_ilist, data):
        return 'ilist', 'content'
    if _ai_layout(data):
        return 'ailine', 'content'
    if is_pool(data):
        return 'pool', 'content'
    if is_idlist(data):
        return 'idlist', 'content'
    if _node_layout(data):
        return 'nodes', 'content'
    if len(data) >= 12:
        cnt = struct.unpack_from('>I', data)[0]
        if len(data) != 4 + 4 * cnt and 0 < cnt < 0x10000 and _parses(conv_links, data):
            return 'links', 'content'
        if 0 < cnt < 0x1000 and 0 < struct.unpack_from('>I', data, 8)[0] < 64 and _parses(conv_gbx, data):
            return 'gbx', 'content'
    if is_lenstr(data):
        return 'lenstr', 'content'
    return 'swap4', 'default'


def convert(data, kind=None, name='', key=None):
    if kind is None:
        kind, how = detect(data, name, key)
        if how == 'foreign':
            raise ValueError('%s: handled by another converter' % kind)
    return CONVERTERS[kind](data)


def convert_ark(src, outdir, stream=False):
    """Converts every entry of an Xbox .ark this module handles into <outdir>/<key>.<kind>; entries of
    other converters' kinds are listed in <outdir>.foreign.txt (key, kind, size) for them to fill in.
    stream=True: a <level>.stream.ark (streamed textures / model chunks, all foreign)."""
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from arkio import Ark
    from collections import Counter
    a = Ark(src)
    os.makedirs(outdir, exist_ok=True)
    stats, foreign = Counter(), []
    for k in a.order:
        d = a.get(k)
        if stream:
            kind, how = 'streamed', 'foreign'
        else:
            kind, how = detect(d, key=k)
        if how == 'foreign':
            foreign.append('%08x %s %d' % (k, kind, len(d)))
        else:
            with open(os.path.join(outdir, '%08x.%s' % (k, kind)), 'wb') as f:
                f.write(convert(d, kind))
        stats['%s (%s)' % (kind, how)] += 1
    with open(outdir.rstrip('/\\') + '.foreign.txt', 'w') as f:
        f.write('\n'.join(foreign) + '\n')
    for s, n in sorted(stats.items()):
        print('%6d %s' % (n, s))


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'kinds':
        print(' '.join(sorted(CONVERTERS)))
        sys.exit(0)
    if len(sys.argv) >= 4 and sys.argv[1] == 'ark':
        convert_ark(sys.argv[2], sys.argv[3], '--stream' in sys.argv)
        sys.exit(0)
    if len(sys.argv) != 4:
        print('usage: python -I convmisc.py <kind|auto> <in> <out>   (kinds: %s)\n'
              '       python -I convmisc.py ark <xbox.ark> <outdir> [--stream]' % ' '.join(sorted(CONVERTERS)))
        sys.exit(1)
    k, src, dst = sys.argv[1:]
    d = open(src, 'rb').read()
    if k == 'auto':
        k, how = detect(d, src)
        print('%s: %s (%s)' % (src, k, how))
        if how == 'foreign':
            sys.exit(2)
    open(dst, 'wb').write(convert(d, k))
