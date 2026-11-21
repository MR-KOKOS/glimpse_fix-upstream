# Split/Second texture container (.textures) converter: Xbox 360 'TEXS' (big-endian, Xenos tiled data) -> PC 'SXET'
# (little-endian, one DDS file per texture).  See README.md for the format.
#
#   python -I convtex.py IN [OUT]          IN = file or folder (all *.textures below it, any case)
#   python -I convtex.py --dds IN OUTDIR   also / only dump the textures as .dds files (Xbox or PC container)
#
# Level archives also hold one streamed container (two groups per texture: resident low mips in the container,
# full size in <level>.stream.ark under key h31('<level>.streamtex.%03d' % slot)); convert() converts the
# container, convert_streamed() one .stream.ark entry (stream_key() gives its key).
import os
import struct
import sys
from array import array

# Xenos texture formats -> (PC container format code, block w, block h, bytes per block, DDS pixel format)
# DDS pixel format: (flags, fourcc, rgb bit count, r mask, g mask, b mask, a mask)
FMT = {
    0x12: (0x21, 4, 4, 8, (0x4, b'DXT1', 0, 0, 0, 0, 0)),
    0x13: (0x23, 4, 4, 16, (0x4, b'DXT3', 0, 0, 0, 0, 0)),   # guess: no DXT2_3 texture in any level or car
    0x14: (0x24, 4, 4, 16, (0x4, b'DXT4', 0, 0, 0, 0, 0)),   # the PC files say DXT4 for every DXT4_5 texture
    # k_8_8_8_8 (endian 8-in-32): PC format 2 = A8R8G8B8 (DDS RGB|ALPHAPIXELS, 32 bit, masks ff0000/ff00/ff/ff000000);
    # after the 32-bit swap the texel bytes are already B, G, R, A as the PC files store them (all 43 level textures)
    0x06: (0x02, 1, 1, 4, (0x41, b'', 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)),
}


class FormatError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- container
def parse(d):
    """Parse a one-group TEXS (Xbox) or SXET (PC) container (cars, frontend groups, most level containers)."""
    if d[:4] == b'TEXS':
        E = '>'
    elif d[:4] == b'SXET':
        E = '<'
    else:
        raise FormatError('not a texture container')
    ver, z, hs = struct.unpack_from(E + '3I', d, 4)
    if ver != 0xC:
        raise FormatError('version %x' % ver)
    B = 0x10
    n, eoff, z1, z2, one, dsoff = struct.unpack_from(E + '6I', d, B)
    if eoff != 0x18 or dsoff != 0x18 + 0x40 * n or one != 1:
        raise FormatError('unexpected header %x %x %x' % (eoff, one, dsoff))
    ents = [d[B + eoff + 0x40 * i:B + eoff + 0x40 * (i + 1)] for i in range(n)]
    dsize, z3, nfix, fixoff = struct.unpack_from(E + '4I', d, B + dsoff)
    if fixoff != dsoff + 0x10 or B + fixoff + 12 * nfix != B + hs:
        raise FormatError('unexpected fixup table')
    fix = [struct.unpack_from(E + '3I', d, B + fixoff + 12 * k) for k in range(nfix)]
    data0 = B + hs
    # one fixup per texture: the data pointer at the start of its entry
    offs = []
    for i, (loc, zz, tgt) in enumerate(fix):
        if loc != eoff + 0x40 * i or zz != 0:
            raise FormatError('unexpected fixup %x %x %x' % (loc, zz, tgt))
        offs.append(tgt)
    if nfix != n:
        raise FormatError('fixups %d != textures %d' % (nfix, n))
    blobs = []
    for i in range(n):
        end = offs[i + 1] if i + 1 < n else dsize
        blobs.append(d[data0 + offs[i]:data0 + end])
    names = d[data0 + dsize:]
    return dict(E=E, version=ver, hs=hs, head=d[:B + dsoff], entries=ents, dsize=dsize, offs=offs, blobs=blobs,
                names=names, raw=d)


def parse_groups(d):
    """Parse the general container form: n entries, nslot slots, ngroup data groups, each group
    {data size, 0, nfix, fixup table offset} with fixups {entry offset, slot offset, data offset in the group}.
    A group with data size 0 has no data here (level streamed textures: the data is in <level>.stream.ark).
    Group data follows the header back to back, then the name table."""
    if d[:4] == b'TEXS':
        E = '>'
    elif d[:4] == b'SXET':
        E = '<'
    else:
        raise FormatError('not a texture container')
    ver, z, hs = struct.unpack_from(E + '3I', d, 4)
    if ver != 0xC:
        raise FormatError('version %x' % ver)
    n, eoff, nslot, slotoff, ngroup, goff = struct.unpack_from(E + '6I', d, 0x10)
    if eoff != 0x18 or (nslot and slotoff != eoff + 0x40 * n) or goff != (slotoff + 8 * nslot if nslot else eoff + 0x40 * n):
        raise FormatError('unexpected header %x %x %x %x %x %x' % (n, eoff, nslot, slotoff, ngroup, goff))
    if d[0x10 + slotoff:0x10 + slotoff + 8 * nslot] != bytes(8 * nslot):
        raise FormatError('slot table not zero')
    ents = [d[0x28 + 0x40 * i:0x28 + 0x40 * (i + 1)] for i in range(n)]
    groups = []
    pos = goff + 0x10 * ngroup
    data = 0x10 + hs
    for g in range(ngroup):
        dsize, zz, nfix, fixoff = struct.unpack_from(E + '4I', d, 0x10 + goff + 0x10 * g)
        if zz != 0 or fixoff != pos:
            raise FormatError('group %d table %x %x %x %x' % (g, dsize, zz, nfix, fixoff))
        fix = [struct.unpack_from(E + '3I', d, 0x10 + fixoff + 12 * k) for k in range(nfix)]
        for loc, slot, off in fix:
            if (loc - eoff) % 0x40 or not 0 <= (loc - eoff) // 0x40 < n:
                raise FormatError('group %d: fixup at %x is no entry' % (g, loc))
        blobs = []
        if dsize:
            offs = sorted(set(f[2] for f in fix)) + [dsize]
            nxt = {offs[i]: offs[i + 1] for i in range(len(offs) - 1)}
            blobs = [d[data + f[2]:data + nxt[f[2]]] for f in fix]
        groups.append(dict(dsize=dsize, fix=fix, blobs=blobs))
        pos = fixoff + 12 * nfix
        data += dsize
    if pos != hs:
        raise FormatError('header size %x, tables end at %x' % (hs, pos))
    return dict(E=E, hs=hs, n=n, nslot=nslot, slotoff=slotoff, ngroup=ngroup, head=d[:0x28], entries=ents,
                groups=groups, names=d[data:])


def names_to_pc(nm):
    """Name table: 'C2NM' then (key, length, name) records; swap the dwords."""
    if not nm:
        return nm
    if nm[:4] != b'C2NM':
        raise FormatError('name table magic %r' % nm[:4])
    if len(nm) == 4:
        return b''          # empty container: the PC files have no name table at all
    out = bytearray(b'MN2C')
    i = 4
    while i < len(nm):
        if i + 8 > len(nm):
            raise FormatError('name table tail')
        k, ln = struct.unpack_from('>2I', nm, i)
        out += struct.pack('<2I', k, ln) + nm[i + 8:i + 8 + ln]
        i += 8 + ln
    if i != len(nm):
        raise FormatError('name table length')
    return bytes(out)


# ---------------------------------------------------------------------------------------------- Xenos
def fetch_info(c):
    """Decode the 6-dword Xenos texture fetch constant."""
    d0, d1, d2, d3, d4, d5 = c
    t = dict(
        type=d0 & 3,
        sign=[(d0 >> (2 + 2 * k)) & 3 for k in range(4)],
        pitch=((d0 >> 22) & 0x1FF) * 32,
        tiled=d0 >> 31,
        fmt=d1 & 0x3F,
        endian=(d1 >> 6) & 3,
        base=(d1 >> 12) << 12,
        width=(d2 & 0x1FFF) + 1,
        height=((d2 >> 13) & 0x1FFF) + 1,
        depth=(d2 >> 26) + 1,          # 2D: array size (cube: 6 faces)
        swizzle=(d3 >> 1) & 0xFFF,
        mip_min=(d4 >> 2) & 0xF,
        mip_max=(d4 >> 6) & 0xF,
        dim=(d5 >> 9) & 3,             # 1 = 2D, 2 = 3D, 3 = cube
        packed=(d5 >> 11) & 1,
        mip_addr=(d5 >> 12) << 12,
    )
    if t['dim'] == 2:
        # 3D textures pack dword2 as 11 + 11 + 10 bits
        t['width'], t['height'], t['depth'] = (d2 & 0x7FF) + 1, ((d2 >> 11) & 0x7FF) + 1, (d2 >> 22) + 1
    return t


def log2_ceil(v):
    return max(0, (v - 1).bit_length())


def next_pow2(v):
    return 1 << log2_ceil(v)


def tiled_block(x, y, pitch, lb):
    """XGAddress2DTiledOffset: block (x, y) in a surface 'pitch' blocks wide -> block index. lb = log2(bytes/block)."""
    aw = (pitch + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aw >> 5)) << (lb + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << lb
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + lb)) + ((y & 1) << 4)
    off = (((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7) +
           (((((y & 8) >> 2) + (x >> 3)) & 3) << 6))
    return off >> lb


def tiled_block_3d(x, y, z, pitch, height, lb):
    """Xenos 3D tiling: block (x, y, z) of a volume 'pitch' x 'height' blocks (both multiples of 32) -> block index.
    Macro tiles of 32 x 16 x 4 blocks; fitted on the Graveyard volume textures (every block of all levels of the 17
    DXT1/DXT5 volumes lands where the PC DDS has it): the 2D bank/channel bits take y bit 3 xor z bit 2."""
    macro = ((z >> 2) * (height >> 4) + (y >> 4)) * (pitch >> 5) + (x >> 5)
    micro = (((y & 6) << 2) + (x & 7)) << lb
    off = (macro << (8 + lb)) + ((micro & ~15) << 1) + (micro & 15) + ((z & 3) << (6 + lb)) + ((y & 1) << 4)
    t = ((y >> 3) ^ (z >> 2)) & 1
    off = ((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + (t << 11) + ((((t << 1) + (x >> 3)) & 3) << 6)
    return off >> lb


def packed_offset(w, h, mip, bw, bh):
    """Block offset of mip 'mip' inside the packed mip tail tile (Xenia GetPackedMipOffset), or None if not packed.
    w, h: size of level 0 in texels."""
    l2w, l2h = log2_ceil(w), log2_ceil(h)
    l2s = min(l2w, l2h)
    if l2s > 4 + mip:
        return None
    base = l2s - 4 if l2s > 4 else 0
    pm = mip - base
    if pm < 3:
        if l2w > l2h:
            x, y = 0, 16 >> pm
        else:
            x, y = 16 >> pm, 0
    else:
        if l2w > l2h:
            x, y = (1 << (l2w - base)) >> (pm - 2), 0
        else:
            x, y = 0, (1 << (l2h - base)) >> (pm - 2)
    return x // bw, y // bh


def swap_endian(blob, mode):
    if mode == 0:
        return bytes(blob)
    b = bytes(blob) + b'\0' * (-len(blob) % 4)
    if mode == 1:      # 8 in 16
        a = array('H', b)
        a.byteswap()
        return a.tobytes()
    if mode == 2:      # 8 in 32
        a = array('I', b)
        a.byteswap()
        return a.tobytes()
    if mode == 3:      # 16 in 32
        a = array('H', b)
        a[0::2], a[1::2] = a[1::2], a[0::2]
        return a.tobytes()
    raise FormatError('endian %d' % mode)


def xenos_surfaces(t, blob):
    """Untile all mip levels (and cube faces / volume slices).  Returns out[level][face] = linear data (PC block
    order; a volume level is its slices one after the other, a 2D/cube face one surface).
    Memory layout: level 0 at the base address, levels >= 1 at the mip address one after the other with pow2 sizes,
    each face/level padded to 32x32-block tiles (volumes: depth to 4) and 4 KB; once the smaller side is <= 16 texels
    the remaining levels share one packed tile (per face), placed like in Xenia's GetPackedMipOffset."""
    if t['fmt'] not in FMT:
        raise FormatError('Xenos format 0x%x not supported' % t['fmt'])
    if t['dim'] not in (1, 2, 3):
        raise FormatError('dimension %d not supported' % t['dim'])
    if not t['tiled']:
        raise FormatError('linear (untiled) textures not supported')   # none in the cars or levels
    if t['mip_min'] != 0:
        raise FormatError('mip_min %d' % t['mip_min'])
    vol = t['dim'] == 2
    faces = t['depth'] if t['dim'] == 3 else 1
    if t['dim'] == 3 and faces != 6:
        raise FormatError('cube with %d faces' % faces)
    if t['dim'] == 1 and t['depth'] != 1:
        raise FormatError('2D array textures not supported')
    pcf, bw, bh, bpb, pf = FMT[t['fmt']]
    lb = bpb.bit_length() - 1
    data = swap_endian(blob, t['endian'])
    w, h = t['width'], t['height']
    dep = t['depth'] if vol else 1
    nlev = t['mip_max'] - t['mip_min'] + 1
    out = []
    mip_off = t['mip_addr']           # running address of unpacked levels >= 1
    tail = None                       # (address, pitch, height, face size) of the packed tile once reached
    for L in range(nlev):
        lw, lh, ld = max(1, w >> L), max(1, h >> L), max(1, dep >> L)      # logical size
        nbx, nby = (lw + bw - 1) // bw, (lh + bh - 1) // bh
        if L == 0:
            sw, sh, sd = w, h, dep
        else:
            sw, sh, sd = max(1, next_pow2(w) >> L), max(1, next_pow2(h) >> L), max(1, next_pow2(dep) >> L)  # storage
        sbx, sby = (sw + bw - 1) // bw, (sh + bh - 1) // bh
        po = packed_offset(w, h, L, bw, bh) if t['packed'] else None
        if po is not None:
            if tail is None:
                addr = t['base'] if L == 0 else mip_off
                pitch = (max(sbx, t['pitch'] // bw) + 31) & ~31 if L == 0 else (sbx + 31) & ~31
                hgt = (sby + 31) & ~31
                fsize = (pitch * hgt * (((sd + 3) & ~3) if vol else 1) * bpb + 4095) & ~4095
                tail = (addr, pitch, hgt, fsize)
            addr, pitch, hgt, fsize = tail
            ox, oy = po
        else:
            ox = oy = 0
            if L == 0:
                addr = t['base']
                pitch = max((t['pitch'] // bw + 31) & ~31, (sbx + 31) & ~31)
            else:
                addr = mip_off
                pitch = (sbx + 31) & ~31
            hgt = (sby + 31) & ~31
            fsize = (pitch * hgt * (((sd + 3) & ~3) if vol else 1) * bpb + 4095) & ~4095
            if L > 0:
                mip_off += fsize * faces
        lev = []
        for f in range(faces):
            fa = addr + f * fsize
            surf = bytearray(nbx * nby * ld * bpb)
            o = 0
            for z in range(ld):
                for y in range(nby):
                    yy = y + oy
                    for x in range(nbx):
                        if vol:
                            s = fa + tiled_block_3d(x + ox, yy, z, pitch, hgt, lb) * bpb
                        else:
                            s = fa + tiled_block(x + ox, yy, pitch, lb) * bpb
                        if s + bpb > len(data):
                            raise FormatError('level %d face %d block (%d,%d,%d) beyond the texture data' % (L, f, x, y, z))
                        surf[o:o + bpb] = data[s:s + bpb]
                        o += bpb
            lev.append(bytes(surf))
        out.append(lev)
    return out


def xenos_levels(t, blob):
    """Untile all mip levels of a 2D texture.  Returns list of linear level data (PC block order)."""
    if t['dim'] != 1:
        raise FormatError('dimension %d not supported' % t['dim'])
    return [lev[0] for lev in xenos_surfaces(t, blob)]


# ---------------------------------------------------------------------------------------------- DDS
def dds_header(w, h, nlev, pf, depth=1, cube=False):
    # as the PC files have it: MIPMAPCOUNT flag and COMPLEX|MIPMAP caps only with more than one level (the count
    # field is 1 then), DEPTH flag + VOLUME caps2 for volumes, all six CUBEMAP face bits for cubes
    flags = 0x1 | 0x2 | 0x4 | 0x1000                  # CAPS HEIGHT WIDTH PIXELFORMAT
    caps = 0x1000                                     # TEXTURE
    if nlev > 1:
        flags |= 0x20000                              # MIPMAPCOUNT
        caps |= 0x8 | 0x400000                        # COMPLEX MIPMAP
    caps2 = 0
    if depth > 1:
        flags |= 0x800000                             # DEPTH
        caps |= 0x8
        caps2 = 0x200000                              # VOLUME
    if cube:
        caps |= 0x8
        caps2 = 0xFE00                                # CUBEMAP + all faces
    pff, fourcc, bits, rm, gm, bm, am = pf
    return (b'DDS ' + struct.pack('<7I', 0x7C, flags, h, w, 0, depth if depth > 1 else 0, nlev) + b'\0' * 44 +
            struct.pack('<2I', 0x20, pff) + fourcc.ljust(4, b'\0') + struct.pack('<5I', bits, rm, gm, bm, am) +
            struct.pack('<5I', caps, caps2, 0, 0, 0))


def xb_texture(ent, blob):
    """Xbox entry + data -> (fetch info, PC format, mip count, sRGB flag, DDS bytes)."""
    c = struct.unpack_from('>6I', ent, 0x28)
    t = fetch_info(c)
    pcf, bw, bh, bpb, pf = FMT.get(t['fmt'], (None,) * 5)
    surf = xenos_surfaces(t, blob)
    nlev = len(surf)
    if t['dim'] == 3:
        body = b''.join(surf[L][f] for f in range(6) for L in range(nlev))     # DDS cube: face by face
    else:
        body = b''.join(lev[0] for lev in surf)
    dds = dds_header(t['width'], t['height'], nlev, pf, t['depth'] if t['dim'] == 2 else 1, t['dim'] == 3) + body
    srgb = 1 if t['sign'][0] == 3 else 0
    return t, pcf, nlev, srgb, dds


def pc_entry(xent):
    """PC 0x40-byte entry for an Xbox entry: format, depth (1 for 2D, 6 for a cube, slices for a volume), mips,
    width, height, sRGB (1 when the Xbox sign bits of channel 0 are 3 = gamma)."""
    ptr, key, ffff, kind, one = struct.unpack_from('>2I4s2I', xent, 0)
    if ptr != 0 or kind != 3 or one != 1:
        raise FormatError('entry: unexpected fields %x %x %x' % (ptr, kind, one))
    t = fetch_info(struct.unpack_from('>6I', xent, 0x28))
    if t['fmt'] not in FMT:
        raise FormatError('Xenos format 0x%x not supported' % t['fmt'])
    srgb = 1 if t['sign'][0] == 3 else 0
    nlev = t['mip_max'] - t['mip_min'] + 1
    dep = t['depth'] if t['dim'] in (2, 3) else 1
    pe = struct.pack('<2I', 0, key) + ffff + struct.pack('<6I', FMT[t['fmt']][0], dep, nlev, t['width'], t['height'], srgb)
    return pe.ljust(0x40, b'\0'), t


def convert(d):
    """Xbox TEXS container bytes -> PC SXET container bytes (any number of groups; streamed groups keep no data)."""
    c = parse_groups(d)
    if c['E'] != '>':
        raise FormatError('input is not an Xbox container')
    n = c['n']
    pe = [None] * n
    gdata, gfix = [], []
    for g in c['groups']:
        out, fixes = bytearray(), []
        streamed = g['dsize'] == 0
        for k, (loc, slot, off) in enumerate(g['fix']):
            i = (loc - 0x18) // 0x40
            e = c['entries'][i]
            if streamed:
                ent, t = pc_entry(e)
                fixes.append((loc, slot, 0))
            else:
                ent, t = pc_entry(e)
                t, pcf, nlev, srgb, dds = xb_texture(e, g['blobs'][k])
                fixes.append((loc, slot, len(out)))
                out += struct.pack('<I', len(dds)) + dds
            if pe[i] is not None and pe[i] != ent:
                raise FormatError('entry %d used twice with different data' % i)
            pe[i] = ent
        gdata.append(bytes(out))
        gfix.append(fixes)
    if any(e is None for e in pe):
        raise FormatError('entry without data')
    res = bytearray(b'SXET' + struct.pack('<3I', 0xC, 0, c['hs']))
    res += struct.pack('<6I', *struct.unpack_from('>6I', d, 0x10))
    for e in pe:
        res += e
    res += bytes(8 * c['nslot'])
    pos = struct.unpack_from('>I', d, 0x24)[0] + 0x10 * c['ngroup']
    for g, fx in zip(gdata, gfix):
        res += struct.pack('<4I', len(g), 0, len(fx), pos)
        pos += 12 * len(fx)
    for fx in gfix:
        for f in fx:
            res += struct.pack('<3I', *f)
    if len(res) != 0x10 + c['hs']:
        raise FormatError('header size mismatch %x %x' % (len(res), 0x10 + c['hs']))
    for g in gdata:
        res += g
    res += names_to_pc(c['names'])
    return bytes(res)


def stream_key(level, slot):
    """.stream.ark key of a streamed texture: the PC exe formats "%s.%03d" (0xC18E54, FUN_007C3D60 at 0x7C3EDB) with
    '<level>.streamtex' and the slot (739/739 Graveyard, 436/436 Quarry, 168/168 nem_storm, 639/639 powerplant,
    914/914 docks)."""
    h = 0
    for ch in ('%s.streamtex.%03d' % (level, slot)).lower().encode('latin-1'):
        h = (h * 31 + ch) & 0xFFFFFFFF
    return h


def streamed_slots(d):
    """[(slot, entry index)] of the streamed (data size 0) groups of a container, either platform."""
    c = parse_groups(d)
    res = []
    for g in c['groups']:
        if g['dsize'] == 0:
            for loc, slot, off in g['fix']:
                res.append(((slot - c['slotoff']) // 8, (loc - 0x18) // 0x40))
    return res


def convert_streamed(d, slot, xblob):
    """Xbox container + slot + Xbox .stream.ark entry (raw Xenos data) -> PC .stream.ark entry (u32 size + DDS)."""
    c = parse_groups(d)
    for i in [i for s, i in streamed_slots(d) if s == slot]:
        t, pcf, nlev, srgb, dds = xb_texture(c['entries'][i], xblob)
        return struct.pack('<I', len(dds)) + dds
    raise FormatError('slot %d is not streamed' % slot)


def textures(d):
    """List (key, DDS bytes or None, info) for a container of either platform (streamed entries: None)."""
    c = parse_groups(d)
    res = []
    for g in c['groups']:
        for k, (loc, slot, off) in enumerate(g['fix']):
            e = c['entries'][(loc - 0x18) // 0x40]
            key = struct.unpack_from(c['E'] + 'I', e, 4)[0]
            if not g['dsize']:
                res.append((key, None, None))
            elif c['E'] == '<':
                b = g['blobs'][k]
                sz = struct.unpack_from('<I', b, 0)[0]
                res.append((key, b[4:4 + sz], None))
            else:
                t, pcf, nlev, srgb, dds = xb_texture(e, g['blobs'][k])
                res.append((key, dds, t))
    return res


# ---------------------------------------------------------------------------------------------- CLI
def files_under(p):
    if os.path.isfile(p):
        yield p, os.path.basename(p)
        return
    for root, dirs, fs in os.walk(p):
        for f in sorted(fs):
            if f.lower().endswith('.textures'):
                full = os.path.join(root, f)
                yield full, os.path.relpath(full, p)


def main(argv):
    dump = False
    if argv and argv[0] == '--dds':
        dump = True
        argv = argv[1:]
    if not argv or len(argv) > 2:
        print('usage: python -I convtex.py IN [OUT]  |  python -I convtex.py --dds IN OUTDIR')
        return 2
    src = argv[0]
    single = os.path.isfile(src)
    dst = argv[1] if len(argv) > 1 else (src + '.pc' if single else src.rstrip('\\/') + '_pc')
    bad = 0
    for full, rel in files_under(src):
        d = open(full, 'rb').read()
        try:
            if dump:
                base = os.path.join(dst, os.path.splitext(rel)[0])
                os.makedirs(os.path.dirname(base) or '.', exist_ok=True)
                for i, (key, dds, t) in enumerate(textures(d)):
                    if dds is None:
                        continue
                    with open('%s_%d_%08x.dds' % (base, i, key), 'wb') as f:
                        f.write(dds)
                print('dds', rel)
                continue
            out = convert(d)
        except FormatError as ex:
            print('FAIL %s: %s' % (rel, ex))
            bad += 1
            continue
        op = dst if single else os.path.join(dst, rel)
        os.makedirs(os.path.dirname(op) or '.', exist_ok=True)
        with open(op, 'wb') as f:
            f.write(out)
        print('ok  %s (%d -> %d bytes)' % (rel, len(d), len(out)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
