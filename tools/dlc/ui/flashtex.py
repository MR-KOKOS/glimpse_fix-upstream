# Adds images from an Xbox DLC Flash texture container to one of the PC's resident Flash texture sets
# (front end: UI/Frontend/Flash/Textures/Flash.textures + Flash.filenames; in game: UI/InGame/Textures/InGame.textures
# + InGame.filenames), under new names.
#
# The DLC's car select images (FE_VSelect01..05, in game FE_carSelect_set1..4) are newer versions of atlases the PC
# already has under the same names, with other cars on them, so they are not replaced: each one wanted is converted
# to the PC format and inserted under a new name (FE_VSelect05 -> FE_VSelectDLC1), and the car select Flash file
# refers to that name (gfxcars.py --image FE_VSelect05=FE_VSelectDLC1).
#   python -I flashtex.py <pc .textures> <pc .filenames> <xbox .textures> <out folder> [--stream <xbox .streamtex>]
#                         FE_VSelect05=FE_VSelectDLC1 [...]
# --stream: the Xbox container is a streamed set (two groups, e.g. UI/InGame/Textures/InGame3.textures +
# .streamtex): the full-size copy of each image (group 1, its data in the .streamtex) is taken.
# The PC set is a one-group container (the resident sets; the streamed PC sets are not written here). The added
# image follows the PC set's own rules:
#   - name: <new>_DXT5 for DXT4/5, <new>_RAW for 32-bit, <new> for DXT1, as the PC's sets name them; the loader
#     (0x533910) registers a texture under its name without "_DXT5" / "_RAW", which is the name the GFX asks for;
#   - DXT4/5 code: the Flash set has all its DXT4_5 images as 0x25 with a DXT5 header, the InGame set as 0x24 with
#     DXT4; the image takes the one the set uses (same blocks);
#   - name order: the set's own order, upper-case compare (Flash) or lower-case compare (InGame: "_" before
#     letters); the one the PC file is in is used;
#   - key: the DLC's, changed only if the set has it already.
import os
import struct
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tex'))
import convtex  # noqa: E402


def names(c):
    """[(key, name)] of a PC container's name table."""
    n, out, i = c['names'], [], 4
    if n[:4] != b'MN2C':
        raise SystemExit('name table magic %r' % n[:4])
    while i < len(n):
        k, ln = struct.unpack_from('<2I', n, i)
        out.append((k, n[i + 8:i + 8 + ln].decode('ascii')))
        i += 8 + ln
    return out


def build(c, ents, blobs, nm):
    """A PC container from its parts, laid out as the PC's own files are."""
    n = len(ents)
    offs, o = [], 0
    for b in blobs:
        offs.append(o)
        o += len(b)
    dsoff = 0x18 + 0x40 * n
    hs = dsoff + 0x10 + 12 * n
    out = bytearray(b'SXET' + struct.pack('<3I', 0xC, 0, hs))
    out += struct.pack('<6I', n, 0x18, 0, 0, 1, dsoff)
    for e in ents:
        out += e
    out += struct.pack('<4I', o, 0, n, dsoff + 0x10)
    for i in range(n):
        out += struct.pack('<3I', 0x18 + 0x40 * i, 0, offs[i])
    for b in blobs:
        out += b
    out += b'MN2C'
    for k, s in nm:
        out += struct.pack('<2I', k, len(s)) + s.encode('ascii')
    return bytes(out)


def streamed_groups(d, E):
    """A streamed (two-group) container: (entries, [(data size, [(entry offset, slot offset, data offset)])] x 2,
    names bytes). Layout: frontend/README.md (.textures + .streamtex)."""
    n, eoff, nslot, slotoff, ngroup, groupoff = struct.unpack_from(E + '6I', d, 0x10)
    hs = struct.unpack_from(E + 'I', d, 0xC)[0]
    if eoff != 0x18 or slotoff != eoff + 0x40 * n or groupoff != slotoff + 8 * nslot or ngroup != 2 or n != 2 * nslot:
        raise SystemExit('streamed container: unexpected header %x %x %x %x %x %x' % (n, eoff, nslot, slotoff, ngroup, groupoff))
    groups, pos = [], groupoff + 0x20
    for g in range(2):
        dsize, z, nfix, fixoff = struct.unpack_from(E + '4I', d, 0x10 + groupoff + 0x10 * g)
        if z or fixoff != pos:
            raise SystemExit('streamed container: group %d table' % g)
        groups.append((dsize, [struct.unpack_from(E + '3I', d, 0x10 + fixoff + 12 * k) for k in range(nfix)]))
        pos = fixoff + 12 * nfix
    if pos != hs or groups[1][0] != 0:
        raise SystemExit('streamed container: tables end at %x, header size %x' % (pos, hs))
    ents = [d[0x28 + 0x40 * i:0x68 + 0x40 * i] for i in range(n)]
    return ents, groups, d[0x10 + hs + groups[0][0]:]


def xbox_set(tex, stream=None):
    """[(key, name, PC entry, PC blob = u32 size + DDS)] of an Xbox container, converted with convtex: a one-group
    container as a whole (convtex.convert), a streamed one (stream = its .streamtex) at full size (group 1)."""
    if stream is None:
        xc = convtex.parse(convtex.convert(tex))
        return [(k, s, xc['entries'][i], xc['blobs'][i]) for i, (k, s) in enumerate(names(xc))]
    if tex[:4] != b'TEXS':
        raise SystemExit('not an Xbox container')
    ents, groups, nm = streamed_groups(tex, '>')
    nmk = dict((k, s) for k, s in names({'names': convtex.names_to_pc(nm)}))
    fix = groups[1][1]
    offs = sorted(set(f[2] for f in fix)) + [len(stream)]
    nxt = {offs[i]: offs[i + 1] for i in range(len(offs) - 1)}
    out = []
    for loc, slot, off in fix:
        e = ents[(loc - 0x18) // 0x40]
        ptr, k, ffff, kind, one = struct.unpack_from('>2I4s2I', e, 0)
        if ptr or kind != 3 or one != 1:
            raise SystemExit('streamed container: entry %d unexpected fields' % ((loc - 0x18) // 0x40))
        t, pcf, nlev, srgb, dds = convtex.xb_texture(e, stream[off:nxt[off]])
        pe = (struct.pack('<2I', 0, k) + ffff + struct.pack('<6I', pcf, 1, nlev, t['width'], t['height'], srgb)).ljust(0x40, b'\0')
        out.append((k, nmk[k], pe, struct.pack('<I', len(dds)) + dds))
    return out


SUFFIX = {0x21: '', 0x24: '_DXT5', 0x25: '_DXT5', 0x02: '_RAW'}


def registered(s):
    """the name the loader (0x533910) registers a texture under: without _DXT5 / _RAW"""
    for suf in ('_DXT5', '_RAW'):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def name_key(rule):
    return (lambda s: s.upper()) if rule == 'upper' else (lambda s: s.lower())


def order_rule(nm):
    """'upper' or 'lower': the compare the PC set's names are sorted by (upper first, as the Flash set)."""
    for rule in ('upper', 'lower'):
        f = name_key(rule)
        if [f(s) for _, s in nm] == sorted(f(s) for _, s in nm):
            return rule
    return None


def main(argv):
    stream = None
    if '--stream' in argv:
        i = argv.index('--stream')
        stream = open(argv[i + 1], 'rb').read()
        argv = argv[:i] + argv[i + 2:]
    if len(argv) < 5:
        sys.exit('usage: flashtex.py pc.textures pc.filenames dlc.textures outdir [--stream dlc.streamtex] OLD=NEW ...')
    pc_tex, pc_fn, dlc_tex, outdir = argv[:4]
    pairs = [a.split('=', 1) for a in argv[4:]]
    pd = open(pc_tex, 'rb').read()
    pc = convtex.parse(pd)
    if pc['E'] != '<':
        sys.exit('%s: not a PC container' % pc_tex)
    ents, blobs, nm = list(pc['entries']), list(pc['blobs']), names(pc)
    if build(pc, ents, blobs, nm) != pd:
        sys.exit('the PC container does not rebuild byte for byte: layout not understood')
    rule = order_rule(nm)
    if rule is None:
        sys.exit('the PC set is not in name order: where to insert is unknown')
    sk = name_key(rule)
    fmts = {struct.unpack_from('<I', e, 12)[0] for e in ents}
    dxt5 = 0x25 in fmts and 0x24 not in fmts   # the set's own code for DXT4_5
    xs = xbox_set(open(dlc_tex, 'rb').read(), stream)
    filenames = open(pc_fn, 'rb').read()
    fl = [s for s in filenames.split(b'\x00') if s]
    keys = {k for k, _ in nm}
    pc_names = {s for _, s in nm}
    added = []
    for old, new in pairs:
        hit = [x for x in xs if x[1].rsplit('_', 1)[0] == old]
        if len(hit) != 1:
            sys.exit('%s: %d textures of that name in the DLC set' % (old, len(hit)))
        k, s, xe, xb = hit[0]
        e = bytearray(xe)
        blob = bytearray(xb)
        # the converter writes Xenos DXT4_5 as the car textures have it (0x24, DXT4); a set that has its DXT4_5
        # images as 0x25 with a DXT5 header (Flash) gets that, the same blocks
        if dxt5 and struct.unpack_from('<I', e, 12)[0] == 0x24 and blob[4 + 84:4 + 88] == b'DXT4':
            struct.pack_into('<I', e, 12, 0x25)
            blob[4 + 84:4 + 88] = b'DXT5'
        fmt = struct.unpack_from('<I', e, 12)[0]
        if fmt not in SUFFIX:
            sys.exit('%s: format %#x not handled' % (s, fmt))
        ns = new + SUFFIX[fmt]
        if ns in pc_names or new.encode() in fl or any(registered(x).lower() == new.lower() for x in pc_names):
            sys.exit('%s is already in the PC set' % new)
        while k in keys:  # the key only has to be unique in the set
            k = (k * 31 + 1) & 0xFFFFFFFF
        keys.add(k)
        struct.pack_into('<I', e, 4, k)
        # one level, as the PC's own Flash images: their DDS headers have flags 0x1007, no mip count and
        # caps 0x1000 (the converter writes the mip-map form 0x21007 / 1 / 0x401008)
        if struct.unpack_from('<I', e, 20)[0] == 1 and struct.unpack_from('<I', blob, 4 + 28)[0] == 1:
            struct.pack_into('<I', blob, 4 + 8, 0x1007)
            struct.pack_into('<I', blob, 4 + 28, 0)
            struct.pack_into('<I', blob, 4 + 108, 0x1000)
        # the set is kept in its name order: the loader may search it
        at = len(nm)
        for j, (_, s2) in enumerate(nm):
            if sk(s2) > sk(ns):
                at = j
                break
        ents.insert(at, bytes(e))
        blobs.insert(at, bytes(blob))
        nm.insert(at, (k, ns))
        fl.append(new.encode())
        added.append(ns)
        print('%s (%s) -> %s, key %08x, %d bytes' % (old, s, ns, k, len(xb)))
    os.makedirs(outdir, exist_ok=True)
    out_tex = os.path.join(outdir, os.path.basename(pc_tex))
    out_fn = os.path.join(outdir, os.path.basename(pc_fn))
    open(out_tex, 'wb').write(build(pc, ents, blobs, nm))
    # the PC's list: names separated and ended by a NUL
    open(out_fn, 'wb').write(b'\x00'.join(fl) + b'\x00' if filenames.endswith(b'\x00') else b'\x00'.join(fl))
    print('written %s (%d textures), %s (%d names)' % (out_tex, len(ents), out_fn, len(fl)))
    return added


if __name__ == '__main__':
    main(sys.argv[1:])
