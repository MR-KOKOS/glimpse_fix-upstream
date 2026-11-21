# Split/Second car select ("frontend") body, Xbox 360 -> PC.  See README.md in this folder for the formats.
#
#   python -I convfrontend.py <xbox frontend folder> [<xbox frontend folder> ...] --out <dir> [--pc <PC resident.ark>]
#       <xbox frontend folder> = e.g. ...\VEHICLES\FRONTEND\BODIES\MUSCLECAR_17 (any case). For each folder the PC
#       files are written to <dir>\<car lower case>\: <car>.model, .model.stream, .textures, .streamtex, .fxc,
#       .shaders, .shaderinst, chassisname.txt. Exit code 1 if anything could not be converted exactly or a self
#       check fails.
#
# Files and what is done (details in README.md):
#   .model + .model.stream   the in-game .model rules of ../convmodel.py, applied to the data of .model.stream
#                            (the .model itself has no data chunk); PC platform block = 16 zero bytes; index lists
#                            longer than 0xFFFF indices are stored with 32-bit indices (record flag 2).
#   .textures + .streamtex   TEXS container with two data groups (resident low mips in .textures, full size in
#                            .streamtex); each texture is converted with ../tex/convtex.py.
#   .fxc/.shaders/.shaderinst  ../shaders/convshaders.py; Xbox shader IDs without a PC version are replaced by the
#                            closest PC frontend shader (SUBST below), the .shaderinst instances follow it.
#   chassisname.txt          copied (identical on both platforms for all base cars).
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DLC = os.path.dirname(HERE)
sys.path.insert(0, DLC)
sys.path.insert(0, os.path.join(DLC, 'tex'))
sys.path.insert(0, os.path.join(DLC, 'shaders'))
from mdl import Model                                    # noqa: E402
from convmodel import strip_to_list, swap_words          # noqa: E402
import convtex                                           # noqa: E402
import convshaders as cs                                 # noqa: E402

# a manual run's PC resident.ark: below the game folder in GAME_DIR, else --pc must be given (build_all.py gives it)
PC_ARK = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None


class FrontendError(Exception):
    pass


# ============================================================================================ .model + .model.stream
PLATFORM_BLOCK = bytes(16)     # every PC frontend .model has u32 16 + 16 zero bytes here
IB32 = 2                       # index record +0x0C bit 1: 32-bit indices (PC lists longer than 0xFFFF)


def convert_model(xm, xs):
    """Xbox .model + .model.stream -> (PC .model, PC .model.stream, info)."""
    x = Model(xm, True)
    if x.chunk_counts != (1, 1) or x.chunks or x.end != len(xm):
        raise FrontendError('model: expected no data chunk in the .model (chunk counts %s)' % (x.chunk_counts,))
    sidx, ssize, sextra = struct.unpack_from('>3I', xs, 0)
    if 12 + ssize != len(xs):
        raise FrontendError('model.stream: size field %d, file %d' % (ssize, len(xs)))
    data = xs[12:]
    blk = bytearray(swap_words(x.block, 4))
    W = lambda o: struct.unpack_from('<I', blk, o)[0]
    put = lambda o, v: struct.pack_into('<I', blk, o, v)
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    out = bytearray()
    ibs = []
    for i in range(nib):
        r = ibp + 0x10 * i
        sub, count = W(r), W(r + 4)
        raw = x.block[sub:sub + 0x20]
        off, size = struct.unpack_from('>2I', raw, 0x18)
        if raw[:0x18] != bytes.fromhex('2000000200000001000000000000000000000000ffff0000') or size != 2 * count:
            raise FrontendError('model: index sub-record %d unexpected: %s' % (i, raw.hex()))
        if off + size > len(data):
            raise FrontendError('model: index buffer %d beyond the stream' % i)
        # PC keeps the sub-record bytes as on the Xbox, except the offset at +0x18 (little-endian)
        blk[sub:sub + 0x20] = raw
        struct.pack_into('<I', blk, sub + 0x18, off)
        lst = strip_to_list(struct.unpack_from('>%dH' % count, data, off))
        wide = len(lst) > 0xFFFF
        put(r, 0)
        put(r + 4, len(lst))
        put(r + 12, (len(out) << 2) | (IB32 if wide else 0))
        out += struct.pack('<%d%s' % (len(lst), 'I' if wide else 'H'), *lst)
        ibs.append((i, count, len(lst), wide))
    vbs = []
    for i in range(nvb):
        r = vbp + 0x1C * i
        ptr, off, size, stride, count = W(r), W(r + 4), W(r + 8), W(r + 12), W(r + 16)
        if size != (stride * count + 15) & ~15 or off + stride * count > len(data):
            raise FrontendError('model: vertex buffer %d: size %#x stride %#x count %#x' % (i, size, stride, count))
        if ptr:
            blk[ptr:ptr + 0x20] = bytes(0x20)
        put(r, 0)
        put(r + 4, len(out))
        out += swap_words(data[off:off + stride * count], 2)
        vbs.append((i, stride, count))
    # every vertex element of the car declarations is FLOAT16_2/4 (16-bit words); anything else needs other swaps
    types = {e[2] for h, els in x.decls for e in els}
    if not types <= {15, 16}:
        raise FrontendError('model: vertex element types %s (only FLOAT16_2/4 handled)' % sorted(types))
    ib_recs = {ibp + 0x10 * i for i in range(nib)}
    vb_recs = {vbp + 0x1C * i for i in range(nvb)}
    ndraw = 0
    for o in range(0, len(blk) - 15, 4):
        if W(o + 4) in ib_recs and W(o + 8) in vb_recs:
            if W(o + 12) == 6:          # triangle strip -> triangle list
                put(o + 12, 4)
            ndraw += 1
    res = bytearray([x.version, x.flags]) + struct.pack('<H', x.u16)
    res += struct.pack('<I', len(PLATFORM_BLOCK)) + PLATFORM_BLOCK
    res += struct.pack('<I', len(x.decls))
    for head, els in x.decls:
        res += swap_words(head, 4) + struct.pack('<I', len(els))
        for e in els:
            res += struct.pack('<5I', *e)
    res += struct.pack('<I', len(x.fixups)) + struct.pack('<%dI' % len(x.fixups), *x.fixups)
    res += struct.pack('<I', len(blk))
    while len(res) % 16:
        res += b'\0'
    res += blk + struct.pack('<2I', *x.chunk_counts)
    stream = struct.pack('<3I', sidx, len(out), sextra) + out
    return bytes(res), bytes(stream), dict(ibs=ibs, vbs=vbs, draws=ndraw)


def model_semantic(m_bytes, s_bytes):
    """What the PC loader gets: (index lists, vertex buffer bytes, (stride, count) per vertex buffer)."""
    m = Model(m_bytes, False)
    W = lambda o: struct.unpack_from('<I', m.block, o)[0]
    data = s_bytes[12:]
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    ib = []
    for i in range(nib):
        r = ibp + 0x10 * i
        cnt, word = W(r + 4), W(r + 12)
        fmt = 'I' if word & IB32 else 'H'
        ib.append(list(struct.unpack_from('<%d%s' % (cnt, fmt), data, word >> 2)))
    vb, shape = [], []
    for i in range(nvb):
        r = vbp + 0x1C * i
        off, stride, count = W(r + 4), W(r + 12), W(r + 16)
        vb.append(data[off:off + stride * count])
        shape.append((stride, count))
    return ib, vb, shape


# ============================================================================================ .textures + .streamtex
# header (from 0x10): n, 0x18, nslot, slot table offset, ngroup, group table offset; 0x40-byte entries; nslot x 8
# zero bytes; ngroup x {data size, 0, nfix, fixup offset}; fixups {entry offset, slot offset, data offset}.
# group 0 data follows the header in .textures, group 1 data is the whole .streamtex.

def parse_tex(d, E):
    n, eoff, nslot, slotoff, ngroup, groupoff = struct.unpack_from(E + '6I', d, 0x10)
    hs = struct.unpack_from(E + 'I', d, 0xC)[0]
    if eoff != 0x18 or slotoff != eoff + 0x40 * n or groupoff != slotoff + 8 * nslot or ngroup != 2:
        raise FrontendError('textures: unexpected header %x %x %x %x %x %x' % (n, eoff, nslot, slotoff, ngroup, groupoff))
    if d[0x10 + slotoff:0x10 + groupoff] != bytes(8 * nslot):
        raise FrontendError('textures: slot table not zero')
    groups = []
    pos = groupoff + 0x10 * ngroup
    for g in range(ngroup):
        dsize, z, nfix, fixoff = struct.unpack_from(E + '4I', d, 0x10 + groupoff + 0x10 * g)
        if z != 0 or fixoff != pos:
            raise FrontendError('textures: group %d table %x %x %x %x' % (g, dsize, z, nfix, fixoff))
        fix = [struct.unpack_from(E + '3I', d, 0x10 + fixoff + 12 * k) for k in range(nfix)]
        groups.append((dsize, fix))
        pos = fixoff + 12 * nfix
    if pos != hs:
        raise FrontendError('textures: header size %x, tables end at %x' % (hs, pos))
    ents = [d[0x28 + 0x40 * i:0x28 + 0x40 * (i + 1)] for i in range(n)]
    return dict(n=n, nslot=nslot, hs=hs, ents=ents, groups=groups, head=d[:0x28])


def _blobs(fix, data, end):
    """data of each fixup: from its offset to the next larger offset (or end)."""
    offs = sorted(set(f[2] for f in fix)) + [end]
    nxt = {offs[i]: offs[i + 1] for i in range(len(offs) - 1)}
    return [data[f[2]:nxt[f[2]]] for f in fix]


def convert_textures(xt, xst):
    """Xbox .textures + .streamtex -> (PC .textures, PC .streamtex, info)."""
    if xt[:4] != b'TEXS':
        raise FrontendError('textures: magic %r' % xt[:4])
    c = parse_tex(xt, '>')
    n, hs = c['n'], c['hs']
    data0 = 0x10 + hs
    (ds0, fix0), (ds1, fix1) = c['groups']
    if ds1 != 0:
        raise FrontendError('textures: streamed group with data size %d' % ds1)
    seen = sorted(f[0] for f in fix0 + fix1)
    if seen != [0x18 + 0x40 * i for i in range(n)]:
        raise FrontendError('textures: fixups do not cover every entry once')
    srcs = [(fix0, _blobs(fix0, xt[data0:data0 + ds0], ds0)), (fix1, _blobs(fix1, xst, len(xst)))]
    pe = {}
    outdata = [bytearray(), bytearray()]
    newfix = [[], []]
    info = []
    for g, (fix, blobs) in enumerate(srcs):
        for (loc, slot, off), blob in zip(fix, blobs):
            i = (loc - 0x18) // 0x40
            e = c['ents'][i]
            ptr, key, ffff, kind, one = struct.unpack_from('>2I4s2I', e, 0)
            if ptr != 0 or kind != 3 or one != 1 or e[0x14:0x20] != bytes(12) or e[0x20:0x28] != b'\xff\xff\0\0' * 2:
                raise FrontendError('textures: entry %d unexpected fields' % i)
            t, pcf, nlev, srgb, dds = convtex.xb_texture(e, blob)
            pe[i] = (struct.pack('<2I', 0, key) + ffff + struct.pack('<6I', pcf, 1, nlev, t['width'], t['height'], srgb)).ljust(0x40, b'\0')
            newfix[g].append((loc, slot, len(outdata[g])))
            outdata[g] += struct.pack('<I', len(dds)) + dds
            info.append((g, '%08x' % key, t['width'], t['height'], nlev, {0x21: 'DXT1', 0x23: 'DXT3', 0x24: 'DXT4'}[pcf], dds))
    out = bytearray(b'SXET' + struct.pack('<3I', 0xC, 0, hs))
    out += struct.pack('<6I', *struct.unpack_from('>6I', xt, 0x10))
    for i in range(n):
        out += pe[i]
    out += bytes(8 * c['nslot'])
    pos = len(out) - 0x10 + 0x20
    for g in range(2):
        out += struct.pack('<4I', len(outdata[0]) if g == 0 else 0, 0, len(newfix[g]), pos)
        pos += 12 * len(newfix[g])
    for g in range(2):
        for f in newfix[g]:
            out += struct.pack('<3I', *f)
    if len(out) != 0x10 + hs:
        raise FrontendError('textures: header size mismatch')
    out += outdata[0]
    out += convtex.names_to_pc(xt[data0 + ds0:])
    return bytes(out), bytes(outdata[1]), info


def textures_pc(pt, pst):
    """List (group, key, DDS bytes, entry) of a PC frontend .textures + .streamtex pair."""
    c = parse_tex(pt, '<')
    data0 = 0x10 + c['hs']
    res = []
    for g, (dsize, fix) in enumerate(c['groups']):
        src = pt[data0:data0 + dsize] if g == 0 else pst
        for loc, slot, off in fix:
            sz = struct.unpack_from('<I', src, off)[0]
            res.append((g, struct.unpack_from('<I', c['ents'][(loc - 0x18) // 0x40], 4)[0], src[off + 4:off + 4 + sz],
                        c['ents'][(loc - 0x18) // 0x40]))
    return res


# ============================================================================================ shaders
# Xbox shader IDs of the DLC frontends that no PC file has, and the PC shader used instead (README.md: why):
#   id -> (PC id, instance constant renames, instance constants to add with their value)
NO_LIGHT = (4.0, 0.0, 0.0, 0.0)   # lightIndex outside 0..3: the one-hot select gives 0, so no emissive term
AO_RENAME = {'ambient_occlusion_color_1_AO_adjust': 'ambient_occlusion_color_2_AO_adjust',
             'ambient_occlusion_color_1_color_adjust': 'ambient_occlusion_color_2_color_adjust'}
SUBST = {
    '14f9e572': ('8f95b125', AO_RENAME, {'VehicleEmissiveLight_1_lightIndex': NO_LIGHT}),   # paint, gbuffer pass
    'f19dbb0a': ('4db9561b', AO_RENAME, {'VehicleEmissiveLight_1_lightIndex': NO_LIGHT}),   # paint, sslighting pass
    'c48f2d34': ('73a79516', {}, {}),                                                         # position only, white
}


def load_pool(pc_ark=None):
    """The convshaders entry pool: PC base car bodies plus every other PC .fxc/.shaders (frontends included)."""
    import pair_lib
    if not (pc_ark or PC_ARK):
        raise FrontendError('no PC resident.ark: give --pc <game>\\data\\resident.ark (or set GAME_DIR to the '
                            'Split/Second game folder)')
    ark = pair_lib.Ark(pc_ark or PC_ARK)
    pool = cs.Pool(ark=ark)
    pool.add_ark_scan(ark)
    return pool


def _subst(pool, xid):
    """(PC substitute id, renames, additions) for an Xbox id the pool lacks, else None."""
    h = xid[2:]
    if h in pool.variants or h not in SUBST:
        return None
    sub = SUBST[h]
    if sub[0] not in pool.variants:
        raise FrontendError('substitute 0x%s not in the PC pool' % sub[0])
    return sub


def _sub_names(pool, xid, sub):
    """(new entry name, pool entry name) per PC variant of the substitute."""
    return [(n[:2] + xid[2:], n) for n in pool.variants[sub[0]]]


def convert_fxc(pool, data, notes):
    f = cs.parse_fxc(data)
    ents = []
    for e in f['ents']:
        sub = _subst(pool, e['id'])
        if sub:
            for new, old in _sub_names(pool, e['id'], sub):
                ents.append({'id': new, 'shaders': pool.fxc[old]['shaders']})
            notes.append('fxc %s <- PC %s' % (e['id'], '0x' + sub[0]))
            continue
        names, known = pool.pc_names(e)
        for n in names:
            pe = pool.fxc.get(n)
            if pe is None:
                raise FrontendError('fxc: no PC entry %s and no substitute' % n)
            ents.append({'id': n, 'shaders': [(xs[0], ps[1]) for xs, ps in zip(e['shaders'], pe['shaders'])]})
    return cs.build_fxc({'ver': f['ver'], 'flags': f['flags'], 'ents': ents})


def convert_shdr(pool, data, notes):
    f = cs.parse_shdr(data)
    ents = []
    for e in f['ents']:
        sub = _subst(pool, e['id'])
        if sub:
            for new, old in _sub_names(pool, e['id'], sub):
                ents.append({k: v for k, v in pool.shdr[old].items() if k != 'raw'} | {'id': new})
            notes.append('shaders %s <- PC %s' % (e['id'], '0x' + sub[0]))
            continue
        names, known = pool.pc_names(e)
        if not known:
            raise FrontendError('shaders: variant list of %s unknown' % e['id'])
        for n in names:
            ents.append({k: v for k, v in e.items() if k != 'raw'} | {'id': n})
    return cs.build_shdr({'ver': f['ver'], 'ents': ents})


def parse_sdri(data):
    """.shaderinst -> dict (values kept as u32 words)."""
    E = cs.endian(data, cs.SDRI_MAGIC)
    r = cs.Reader(data, E)
    if r.tag() != cs.SDRI_MAGIC:
        raise FrontendError('shaderinst magic')
    ver = r.u32()
    if r.tag() != b'INSS':
        raise FrontendError('shaderinst INSS')
    insts = []
    for _ in range(r.u32()):
        h, name, sid = r.u32(), r.str(), r.str()
        cg = []
        for _ in range(2):
            n, used = r.u32(), r.u32()
            items = []
            for _ in range(n):
                nm, cl, reg, cnt = r.str(), r.u32(), r.u32(), r.u32()
                items.append([nm, cl, reg, [tuple(r.u32() for _ in range(4)) for _ in range(cnt)]])
            cg.append([used, items])
        tg = []
        for _ in range(2):
            items = []
            for _ in range(r.u32()):
                nm, cl, reg, th = r.str(), r.u32(), r.u32(), r.u32()
                items.append([nm, cl, reg, th, [(r.u32(), r.u32()) for _ in range(r.u32())]])
            tg.append(items)
        insts.append(dict(hash=h, name=name, sid=sid, cg=cg, tg=tg))
    if r.tag() != b'INSE':
        raise FrontendError('shaderinst INSE')
    tail = []
    if r.p < len(data):
        if r.tag() != b'SMAP' or (len(data) - r.p) % 4:
            raise FrontendError('shaderinst SMAP')
        while r.p < len(data):
            tail.append(r.u32())
        tail = [None] + tail
    return dict(ver=ver, insts=insts, tail=tail)


def build_sdri(f, E='<'):
    w = cs.Writer(E)
    w.tag(cs.SDRI_MAGIC)
    w.u32(f['ver'])
    w.tag(b'INSS')
    w.u32(len(f['insts']))
    for i in f['insts']:
        w.u32(i['hash'])
        w.str(i['name'])
        w.str(i['sid'])
        for used, items in i['cg']:
            w.u32(len(items))
            w.u32(used)
            for nm, cl, reg, vals in items:
                w.str(nm)
                w.u32(cl)
                w.u32(reg)
                w.u32(len(vals))
                for v in vals:
                    for x in v:
                        w.u32(x)
        for items in i['tg']:
            w.u32(len(items))
            for nm, cl, reg, th, ss in items:
                w.str(nm)
                w.u32(cl)
                w.u32(reg)
                w.u32(th)
                w.u32(len(ss))
                for a, b in ss:
                    w.u32(a)
                    w.u32(b)
    w.tag(b'INSE')
    if f['tail']:
        w.tag(b'SMAP')
        for v in f['tail'][1:]:
            w.u32(v)
    return bytes(w.b)


def _fwords(v):
    return tuple(struct.unpack('<I', struct.pack('<f', x))[0] for x in v)


def convert_sdri(pool, data, notes):
    f = parse_sdri(data)
    for inst in f['insts']:
        sub = _subst(pool, inst['sid'])
        if not sub:
            continue
        pid, ren, add = sub
        meta = pool.shdr['0x' + pid]
        for k, (used, items) in enumerate(inst['cg']):
            have = {ren.get(it[0], it[0]): it for it in items}
            new = []
            for reg, cnt, name in meta['cg'][1 if k == 0 else 3][1]:
                if name in have:
                    old = have.pop(name)
                    vals = old[3]
                    cl = old[1]
                elif name in add:
                    vals, cl = [_fwords(add[name])], 2
                else:
                    raise FrontendError('shaderinst: no value for %s of 0x%s' % (name, pid))
                if len(vals) != cnt:
                    raise FrontendError('shaderinst: %s has %d registers, 0x%s wants %d' % (name, len(vals), pid, cnt))
                new.append([name, cl, reg, vals])
            if have:
                raise FrontendError('shaderinst: 0x%s lacks %s' % (pid, sorted(have)))
            inst['cg'][k] = [meta['cg'][1 if k == 0 else 3][0], new]
        for k, items in enumerate(inst['tg']):
            want = meta['sg'][1 if k == 0 else 3]
            if sorted((reg, nm) for nm, cl, reg, th, ss in items) != sorted(want):
                raise FrontendError('shaderinst: samplers of %s differ from 0x%s' % (inst['sid'], pid))
        notes.append('shaderinst instance of %s: constants mapped to PC %s' % (inst['sid'], '0x' + pid))
    return build_sdri(f, '<')


# ============================================================================================ folder
EXTS = ('.model', '.model.stream', '.textures', '.streamtex', '.fxc', '.shaders', '.shaderinst')


def read_folder(src):
    """{lower-case file name: bytes} of an Xbox frontend folder, and the car name."""
    files = {fn.lower(): open(os.path.join(src, fn), 'rb').read() for fn in os.listdir(src)
             if os.path.isfile(os.path.join(src, fn))}
    car = os.path.basename(os.path.normpath(src)).lower()
    return car, files


def convert_folder(pool, car, files):
    """Xbox frontend files -> ({PC file name: bytes}, notes, info)."""
    g = lambda ext: files.get(car + ext)
    for ext in EXTS:
        if g(ext) is None:
            raise FrontendError('missing %s%s' % (car, ext))
    out, notes = {}, []
    m, s, minfo = convert_model(g('.model'), g('.model.stream'))
    out[car + '.model'], out[car + '.model.stream'] = m, s
    t, st, tinfo = convert_textures(g('.textures'), g('.streamtex'))
    out[car + '.textures'], out[car + '.streamtex'] = t, st
    out[car + '.fxc'] = convert_fxc(pool, g('.fxc'), notes)
    out[car + '.shaders'] = convert_shdr(pool, g('.shaders'), notes)
    out[car + '.shaderinst'] = convert_sdri(pool, g('.shaderinst'), notes)
    if 'chassisname.txt' in files:
        out['chassisname.txt'] = files['chassisname.txt']
    return out, notes, dict(model=minfo, textures=tinfo)


def main(argv):
    if '--out' not in argv:
        print('usage: python -I convfrontend.py <xbox frontend folder> [...] --out <dir> [--pc <PC resident.ark>]')
        return 2
    a = list(argv)
    pc = None
    if '--pc' in a:
        i = a.index('--pc')
        pc = a[i + 1]
        del a[i:i + 2]
    i = a.index('--out')
    dst = a[i + 1]
    del a[i:i + 2]
    pool = load_pool(pc)
    bad = 0
    for src in a:
        car, files = read_folder(src)
        try:
            out, notes, info = convert_folder(pool, car, files)
        except (FrontendError, convtex.FormatError, ValueError) as ex:
            print('FAIL %s: %s' % (car, ex))
            bad += 1
            continue
        d = os.path.join(dst, car)
        os.makedirs(d, exist_ok=True)
        for fn, b in out.items():
            with open(os.path.join(d, fn), 'wb') as f:
                f.write(b)
        print('%s: %s' % (car, ', '.join('%s %d' % (fn, len(b)) for fn, b in out.items())))
        mi = info['model']
        print('  model: %d index buffers (%d 32-bit), %d vertex buffers, %d draws; %d textures' % (
            len(mi['ibs']), sum(w for *_, w in mi['ibs']), len(mi['vbs']), mi['draws'], len(info['textures'])))
        for n in notes:
            print('  ' + n)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
