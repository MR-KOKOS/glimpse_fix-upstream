# Tests for convfrontend.py.
#
#   python -I test_frontend.py [--dlc <dir with the Xbox frontend folders>] [--out <dir>] [-v]
#
# 1. Base cars: every vehicles/frontend/bodies/<car>/ of the Xbox resident.ark (pair_lib.XB) is converted and compared
#    with the PC resident.ark (pair_lib.PC). The shader pool leaves the car's own PC frontend .fxc/.shaders out.
# 2. PC relations: a set of structural checks (check_pc) is run on every PC base frontend; the checks that hold for
#    all of them are then required of every converted DLC frontend (--dlc, default: the session's Deadline dump).
# 3. With --out, the converted DLC files are written to <out>\<car>\ and the textures to <out>\<car>\dds\*.dds.
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'misc'))
import convfrontend as cf          # noqa: E402
import convshaders as cs           # noqa: E402
from mdl import Model              # noqa: E402
from arkio import Ark, key         # noqa: E402
import pair_lib                    # noqa: E402

# default of --dlc: the Deadline dump below GLIMPSE_DLC_SCRATCH (development)
DLC = (os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc', 'x_deadline', 'VEHICLES', 'FRONTEND', 'BODIES')
       if os.environ.get('GLIMPSE_DLC_SCRATCH') else '')
CARS = (['musclecar_%02d' % n for n in range(1, 17)] + ['supercar_%02d' % n for n in range(1, 17)] +
        ['truck_%02d' % n for n in range(1, 9)])
NAMES = ['.model', '.model.stream', '.textures', '.streamtex', '.fxc', '.shaders', '.shaderinst']


def fe_files(ark, car):
    res = {}
    for ext in NAMES:
        b = ark.get('vehicles/frontend/bodies/%s/%s%s' % (car, car, ext))
        if b is not None:
            res[car + ext] = b
    b = ark.get('vehicles/frontend/bodies/%s/chassisname.txt' % car)
    if b is not None:
        res['chassisname.txt'] = b
    return res


# ---------------------------------------------------------------------------------------------- PC relations
def check_pc(car, f):
    """Structural relations of one PC frontend set -> {relation: True/False}."""
    R = {}

    def rel(name, ok):
        R[name] = R.get(name, True) and bool(ok)

    md, sd = f[car + '.model'], f[car + '.model.stream']
    m = Model(md, False)
    rel('model: parses to the end', m.end == len(md))
    rel('model: version 1, u16 8', (m.version, m.u16) == (1, 8))
    rel('model: platform block = 16 zero bytes', m.platform == bytes(16))
    rel('model: chunk counts (1, 1), no chunk', m.chunk_counts == (1, 1) and not m.chunks)
    rel('model: block 16-aligned', m.block_at % 16 == 0)
    rel('model: decl element types FLOAT16_2/4 only', all(e[2] in (15, 16) for h, els in m.decls for e in els))
    sidx, ssize, sextra = struct.unpack_from('<3I', sd, 0)
    rel('stream: header (0, file size - 12, 0)', (sidx, sextra) == (0, 0) and ssize + 12 == len(sd))
    W = lambda o: struct.unpack_from('<I', m.block, o)[0]
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    regions = []
    ibcount = {}
    for i in range(nib):
        r = ibp + 0x10 * i
        sub, cnt, z, word = W(r), W(r + 4), W(r + 8), W(r + 12)
        wide = word & 2
        rel('ib: record +0 and +8 zero', sub == 0 and z == 0)
        rel('ib: flags only bit 1', word & 1 == 0)
        rel('ib: 32-bit iff count > 0xFFFF', bool(wide) == (cnt > 0xFFFF))
        rel('ib: count multiple of 3', cnt % 3 == 0)
        regions.append((word >> 2, cnt * (4 if wide else 2), 'i%d' % i))
        ibcount[r] = cnt
    vbcount = {}
    for i in range(nvb):
        r = vbp + 0x1C * i
        ptr, off, size, stride, cnt, z1, z2 = struct.unpack_from('<7I', m.block, r)
        rel('vb: record +0, +0x14, +0x18 zero', ptr == 0 and z1 == 0 and z2 == 0)
        rel('vb: size field = stride*count rounded up to 16', size == (stride * cnt + 15) & ~15)
        regions.append((off, stride * cnt, 'v%d' % i))
        vbcount[r] = cnt
    regions.sort()
    pos, ok = 0, True
    for a, n, nm in regions:
        ok &= a == pos
        pos = a + n
    rel('stream: buffers packed, no gap, no overlap, nothing after', ok and pos == ssize)
    first_v = min(i for i, x in enumerate(regions) if x[2][0] == 'v')
    rel('stream: all index lists before all vertex buffers', all(x[2][0] == 'v' for x in regions[first_v:]))
    # draw records (x, ib record, vb record, primitive type)
    ib, vb, shape = cf.model_semantic(md, sd)
    seen = {}
    for o in range(0, len(m.block) - 15, 4):
        if W(o + 4) in ibcount and W(o + 8) in vbcount:
            rel('draw: primitive type 4 (list)', W(o + 12) == 4)
            rel('draw: +0 zero', W(o) == 0)
            seen[W(o + 4)] = seen.get(W(o + 4), 0) + 1
            k = (W(o + 4) - ibp) // 0x10
            rel('draw: indices < vertex count of its buffer', not ib[k] or max(ib[k]) < vbcount[W(o + 8)])
    rel('draw: every index record used by exactly one draw', sorted(seen) == sorted(ibcount) and set(seen.values()) == {1})
    # textures
    td, std = f[car + '.textures'], f[car + '.streamtex']
    c = cf.parse_tex(td, '<')
    rel('tex: magic SXET, version 0xC', td[:8] == b'SXET\x0c\0\0\0')
    rel('tex: n == 2 * nslot (each texture once resident, once streamed)', c['n'] == 2 * c['nslot'])
    (ds0, fix0), (ds1, fix1) = c['groups']
    rel('tex: group 1 data size 0', ds1 == 0)
    rel('tex: fixups cover every entry once', sorted(x[0] for x in fix0 + fix1) == [0x18 + 0x40 * i for i in range(c['n'])])
    rel('tex: group 0 = entries 0..nslot-1, group 1 = the rest, in order',
        [x[0] for x in fix0 + fix1] == [0x18 + 0x40 * i for i in range(c['n'])] and len(fix0) == c['nslot'])
    rel('tex: fixup slot = slot table + 8*k in both groups',
        [x[1] for x in fix0] == [x[1] for x in fix1] == [0x18 + 0x40 * c['n'] + 8 * k for k in range(c['nslot'])])
    for g, (fix, total) in enumerate(((fix0, ds0), (fix1, len(std)))):
        pos, ok = 0, True
        src = td[0x10 + c['hs']:0x10 + c['hs'] + ds0] if g == 0 else std
        for loc, slot, off in fix:
            ok &= off == pos
            sz = struct.unpack_from('<I', src, off)[0]
            ok &= src[off + 4:off + 8] == b'DDS '
            pos = off + 4 + sz
        rel('tex: group %d blobs u32 size + DDS, packed, filling the data exactly' % g, ok and pos == total)
    texs = cf.textures_pc(td, std)
    keys = [k for g, k, dds, e in texs]
    rel('tex: same key order in both groups', keys[:c['nslot']] == keys[c['nslot']:])
    for g, k, dds, e in texs:
        h, w = struct.unpack_from('<2I', dds, 12)
        mips = struct.unpack_from('<I', dds, 28)[0]
        fcc = dds[84:88]
        fmt, one, nl, ew, eh = struct.unpack_from('<5I', e, 0x0C)
        bpb = 8 if fcc == b'DXT1' else 16
        need = sum(max(1, (max(1, w >> L) + 3) // 4) * max(1, (max(1, h >> L) + 3) // 4) * bpb for L in range(mips))
        rel('tex: DDS size = header + mips', len(dds) == 128 + need)
        rel('tex: entry (format, mips, w, h) = DDS', (fmt, nl, ew, eh) == ({b'DXT1': 0x21, b'DXT4': 0x24}.get(fcc), mips, w, h))
        rel('tex: entry +0x24..0x40 zero', e[0x24:] == bytes(0x1C))
    lo = {k: (struct.unpack_from('<2I', e, 0x18)) for g, k, dds, e in texs if g == 0}
    hi = {k: (struct.unpack_from('<2I', e, 0x18)) for g, k, dds, e in texs if g == 1}
    rel('tex: resident copy smaller than the streamed one', all(lo[k][0] * lo[k][1] < hi[k][0] * hi[k][1] for k in lo))
    nm = td[0x10 + c['hs'] + ds0:]
    rel('tex: name table MN2C after group 0 data', nm[:4] == b'MN2C')
    tk, i = [], 4
    while i + 8 <= len(nm):
        k, ln = struct.unpack_from('<2I', nm, i)
        tk.append(k)
        i += 8 + ln
    rel('tex: name table = keys of one group, in order, nothing after', tk == keys[:c['nslot']] and i == len(nm))
    # shaders
    fx = cs.parse_fxc(f[car + '.fxc'])
    sh = cs.parse_shdr(f[car + '.shaders'])
    rel('fxc/shaders: re-encode byte-identical', cs.build_fxc(fx, '<') == f[car + '.fxc'] and cs.build_shdr(sh, '<') == f[car + '.shaders'])
    rel('fxc/shaders: same entry names in the same order', [e['id'] for e in fx['ents']] == [e['id'] for e in sh['ents']])
    si = cf.parse_sdri(f[car + '.shaderinst'])
    rel('shaderinst: re-encode byte-identical', cf.build_sdri(si, '<') == f[car + '.shaderinst'])
    meta = {e['id']: e for e in sh['ents']}
    for inst in si['insts']:
        e = meta.get(inst['sid'])
        rel('shaderinst: every shader id in .shaders', e is not None)
        if e is None:
            continue
        for k in range(2):
            used, items = inst['cg'][k]
            mu, mi = e['cg'][1 if k == 0 else 3]
            rel('shaderinst: constant groups = .shaders material groups (used, reg, count, name)',
                used == mu and [(reg, len(v), nm) for nm, cl, reg, v in items] == mi)
            rel('shaderinst: constant class 2', all(cl == 2 for nm, cl, reg, v in items))
            rel('shaderinst: sampler groups = .shaders material samplers',
                [(reg, nm) for nm, cl, reg, th, ss in inst['tg'][k]] == e['sg'][1 if k == 0 else 3])
    return R


# ---------------------------------------------------------------------------------------------- base pairs
def compare_base(car, xf, pf, out):
    """Converted Xbox base frontend vs the PC files -> list of (file, result)."""
    res = []
    pm, ps = pf[car + '.model'], pf[car + '.model.stream']
    om, os_ = out[car + '.model'], out[car + '.model.stream']
    a, b = cf.model_semantic(pm, ps), cf.model_semantic(om, os_)
    P, O = Model(pm, False), Model(om, False)
    # everything but the buffer offsets must be byte-identical
    W = lambda m, o: struct.unpack_from('<I', m.block, o)[0]
    nvb, vbp, nib, ibp = (W(P, k) for k in (0, 4, 8, 12))
    mask = {ibp + 0x10 * i + 12 for i in range(nib)} | {vbp + 0x1C * i + 4 for i in range(nvb)}
    blk_diff = [o for o in range(0, len(P.block), 4) if o not in mask and P.block[o:o + 4] != O.block[o:o + 4]]
    flags_eq = all((W(P, o) & 3) == (W(O, o) & 3) for o in mask if o < ibp + 0x10 * nib and o >= ibp)
    head_eq = pm[:P.block_at] == om[:O.block_at] and pm[P.chunk_head_at:] == om[O.chunk_head_at:]
    # known PC exporter quirk (also musclecar_05_high in-game): some PC vertices are all zero where the Xbox has data
    zeroed = []
    vb_ok = a[2] == b[2]
    for i, (p, q) in enumerate(zip(a[1], b[1])):
        st = a[2][i][0]
        for v in sorted(set(k // st for k in range(0, len(p), 2) if p[k:k + 2] != q[k:k + 2])):
            vb_ok &= p[v * st:(v + 1) * st] == bytes(st)
            zeroed.append((i, v))
    ok = (len(pm) == len(om) and head_eq and not blk_diff and flags_eq and a[0] == b[0] and vb_ok and len(ps) == len(os_)
          and ps[:12] == os_[:12])
    msg = 'size %d/%d head %s block words %s ib %s vb %s' % (len(pm), len(om), head_eq, [hex(o) for o in blk_diff[:8]],
                                                             a[0] == b[0], a[1] == b[1])
    if ok and zeroed:
        msg = 'equal except %d vertices that are all zero in the PC file %s' % (len(zeroed), zeroed[:4])
    res.append(('model+stream', ok, msg if (zeroed or not ok) else ''))
    res.append(('model+stream bytes', pm == om and ps == os_, 'identical' if pm == om and ps == os_ else 'buffer order differs'))
    for ext in ('.textures', '.streamtex', '.fxc', '.shaders', '.shaderinst'):
        same = pf[car + ext] == out[car + ext]
        res.append((ext, same, '' if same else 'differs (%d vs %d bytes)' % (len(pf[car + ext]), len(out[car + ext]))))
    if 'chassisname.txt' in pf:
        res.append(('chassisname.txt', pf['chassisname.txt'] == out.get('chassisname.txt'), ''))
    return res


def main(argv):
    verbose = '-v' in argv
    dlc = argv[argv.index('--dlc') + 1] if '--dlc' in argv else DLC
    if not dlc:
        sys.exit('test_frontend.py: give --dlc <...\\VEHICLES\\FRONTEND\\BODIES> or set GLIMPSE_DLC_SCRATCH; '
                 'GLIMPSE_XB_RESIDENT must name the Xbox 360 resident.ark.')
    outdir = argv[argv.index('--out') + 1] if '--out' in argv else None
    xa, pa = Ark(pair_lib.XB), Ark(pair_lib.PC)
    pk = pair_lib.Ark(pair_lib.PC)
    cache, scan = {}, {}
    fails = 0
    # ---- 1 + 2: base cars
    rels = {}
    tally = {}
    fp = []

    def full_pool():
        if not fp:
            fp.append(cs.Pool(ark=pk, cache=cache))
            fp[0].add_ark_scan(pk, cache=scan)
        return fp[0]
    for car in CARS:
        xf, pf = fe_files(xa, car), fe_files(pa, car)
        if car + '.model' not in xf or car + '.model' not in pf:
            continue
        for k, v in check_pc(car, pf).items():
            rels.setdefault(k, []).append(v)
        pool = cs.Pool(ark=pk, cache=cache)
        pool.add_ark_scan(pk, skip_keys={key('vehicles/frontend/bodies/%s/%s%s' % (car, car, e)) for e in ('.fxc', '.shaders')}, cache=scan)
        try:
            out, notes, info = cf.convert_folder(pool, car, xf)
        except cf.FrontendError as ex:
            # an ID only this car's own PC file has: convert with the full pool (then .fxc is not an independent test)
            notes = ['leave-one-out: %s; converted with the full pool' % ex]
            out, n2, info = cf.convert_folder(full_pool(), car, xf)
            notes += n2
        res = compare_base(car, xf, pf, out)
        for nm, ok, msg in res:
            tally.setdefault(nm, [0, 0])[0 if ok else 1] += 1
        line = ' '.join('%s=%s' % (nm, 'ok' if ok else 'NO') for nm, ok, msg in res)
        print('%-13s %s%s' % (car, line, '' if not notes else '  [' + '; '.join(notes) + ']'))
        for nm, ok, msg in res:
            if (not ok or msg.startswith('equal except')) and (verbose or nm != 'model+stream bytes'):
                print('    %s: %s' % (nm, msg))
            if not ok and nm != 'model+stream bytes':
                fails += 1
    print('\nbase cars, converted Xbox vs PC (equal / different):')
    for nm, (y, n) in tally.items():
        print('  %-20s %d / %d' % (nm, y, n))
    hold = [k for k, v in rels.items() if all(v)]
    print('\nPC relations: %d checked, %d hold in all %d PC frontends' % (len(rels), len(hold), len(next(iter(rels.values())))))
    for k, v in rels.items():
        if not all(v):
            print('  does not always hold (%d of %d): %s' % (sum(v), len(v), k))
    # ---- 3: DLC
    pool = full_pool()
    print('\nDLC frontends (%s):' % dlc)
    for d in sorted(os.listdir(dlc)):
        src = os.path.join(dlc, d)
        if not os.path.isdir(src):
            continue
        car, files = cf.read_folder(src)
        try:
            out, notes, info = cf.convert_folder(pool, car, files)
        except Exception as ex:
            print('FAIL %s: %r' % (car, ex))
            fails += 1
            continue
        r = check_pc(car, out)
        bad = [k for k in hold if not r.get(k, False)]
        ib, vb, shape = cf.model_semantic(out[car + '.model'], out[car + '.model.stream'])
        print('  %-13s %d PC relations hold, %d violated %s; %d index lists (%d indices), %d vertex buffers (%s), %d textures' % (
            car, len(hold) - len(bad), len(bad), bad, len(ib), sum(map(len, ib)), len(vb),
            '+'.join(str(c) for s, c in shape), len(info['textures'])))
        for n in notes:
            print('      ' + n)
        fails += len(bad)
        # vertex positions finite and in a car-sized box (FLOAT16 x, y, z at the start of each element 0)
        import array
        for (stride, cnt), v in zip(shape, vb):
            h = array.array('H', v)
            pos = [struct.unpack('<e', struct.pack('<H', h[i * stride // 2 + k]))[0] for i in range(cnt) for k in range(3)]
            lo, hi = min(pos), max(pos)
            ok = all(abs(p) < 10 for p in pos)
            print('      vertex buffer stride %#x: %d vertices, position range %.3f .. %.3f %s' % (stride, cnt, lo, hi, '' if ok else 'OUT OF RANGE'))
            fails += not ok
        if outdir:
            d2 = os.path.join(outdir, car)
            os.makedirs(os.path.join(d2, 'dds'), exist_ok=True)
            for fn, b in out.items():
                open(os.path.join(d2, fn), 'wb').write(b)
            for g, k, w, h, nl, fmt, dds in info['textures']:
                open(os.path.join(d2, 'dds', '%s_%s_%dx%d.dds' % ('res' if g == 0 else 'str', k, w, h)), 'wb').write(dds)
    print('\n%s' % ('ALL OK' if not fails else '%d FAILURES' % fails))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
