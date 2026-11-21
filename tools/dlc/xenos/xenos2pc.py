# Xbox .fxc entries -> PC .fxc entries (all PC variants) by translating the Xenos microcode.
#
# API (for convshaders.py):
#   pc_entries(xbox_entry, vin=None, variants=None) -> (list of PC entries, status dict)
#       xbox_entry: a convshaders.parse_fxc() entry ('0x<id>', [(consts, vs container), (consts, ps container)])
#       vin: the .shaders vertex input list of that ID (for the variant rule and the vertex decode rule)
#       variants: PC entry names to produce (default: 0x..4x with the vertex input 'indices', else 0x 2x)
#   Each PC entry is {'id': name, 'shaders': [(consts, vs bytecode), (consts, ps bytecode)]}, constant tables
#   copied from the Xbox entry (as convshaders.convert_fxc does).
#
# CLI:
#   python -I xenos2pc.py <out dir> <xbox ark|fxc> [...] [--missing-only] [--ids a,b,...]
#       translates every .fxc entry of the inputs (Xbox archives are scanned by magic); --missing-only keeps
#       the IDs no PC file has (needs the PC game data, read only). Writes <out>/translated.fxc (one PC .fxc
#       with every variant entry), <out>/status.txt, <out>/hlsl/<id>_{vs,ps}.hlsl.
#
# Variants (from the PC exe, FUN_0050d340 / FUN_005105f0): the game loads "<n>x<id>" with n = depth texture
# path chosen at start-up: 1 INTZ, 2 RAWZ, 3 DF24/DF16, 0 none (5 = DF without R32F -> falls back to 0x);
# a missing variant falls back to 0x. Current GPUs take 1x (deforming shaders) / 0x (the others).
#   0x/1x/3x/4x: translated VS + PS.  2x: PS samples of depth textures RAWZ-decoded (dot(t.wxy, 255/256..)),
#   as the PC's 2x shaders do.  0x of a deforming shader: the PC compiles deformation out there (only used
#   without INTZ/RAWZ/DF); it gets the same deforming VS as 1x (known limit).
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'shaders'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'misc'))
sys.path.insert(0, os.path.dirname(HERE))

import xshader      # noqa: E402
import xlate        # noqa: E402
import ucode        # noqa: E402

PC_PREFIXES = ('0x', '2x')
DEFORM_PREFIXES = ('0x', '1x', '2x', '3x', '4x')


def variant_names(xid, vin):
    deform = vin is not None and any(v[0] == 'indices' for v in vin)
    return tuple(p + xid[2:] for p in (DEFORM_PREFIXES if deform else PC_PREFIXES))


def normal_elements(x, names=('NormalTrans',)):
    """vertex elements the VS uses as normals (their value reaches a product with NormalTrans)"""
    p = ucode.Program(x.code)
    regs = set()
    for c in x.ctab['consts']:
        if c['set'] == 2 and c['name'] in names:
            regs |= set(range(c['reg'], c['reg'] + c['count']))
    out = []
    order = sorted(p.slots)
    for v in x.vertex_elements:
        if v['usage'] == 0:
            continue
        taint = {p.slots[v['slot']].dst}
        for a in order:
            if a <= v['slot']:
                continue
            ins = p.slots[a]
            if isinstance(ins, ucode.Fetch):
                if ins.op == ucode.FETCH_VERTEX:
                    taint.discard(ins.dst)
                continue
            ops = ins.vec_operands() if (ins.vec_result_mask() or ins.export) else []
            ut = any(o.kind == 'r' and o.index in taint for o in ops)
            if ut and any(o.kind == 'c' and o.index in regs for o in ops) and \
                    ins.vop in (ucode.V_DP3, ucode.V_DP4, ucode.V_MUL, ucode.V_MAD):
                out.append((v['usage'], v['index']))
                break
            if ut and not ins.export and ins.vec_result_mask():
                taint.add(ins.vdst)
            if any(o.kind == 'r' and o.index in taint for o in ins.scl_operands()) and not ins.export \
                    and ins.scl_result_mask():
                taint.add(ins.sdst)
    return out


def vertex_decode(x, vin):
    """Default rule for vertex elements whose PC data is stored as (v + 1) / 2 (D3DCOLOR normals of level
    geometry; the PC shaders decode them with *2-1): the elements used as normals, when the .shaders input
    list has a D3DCOLOR TEXCOORD input. Matches the PC shaders in 905 of 927 checked base VS (no false
    positives; 22 decodes missed)."""
    if vin is None or not any(v[1] == 4 and v[2] == 5 for v in vin):
        return []
    return normal_elements(x)


MAX_VS_INTERP = 10          # vs_3_0: 12 output registers, 11 without PSIZE, one of them the position


def ps_live_in(x):
    """per PS input register: components read before the shader writes them (straight-line view; a
    predicated write does not count as a kill)"""
    p = ucode.Program(x.code)
    written = {}
    live = {}
    regs = {i['reg'] for i in x.interps}

    def read(r, comps):
        if r in regs:
            for c in comps:
                if c not in written.get(r, set()):
                    live.setdefault(r, set()).add(c)

    def write(r, mask, pred):
        if not pred:
            written.setdefault(r, set()).update(j for j in range(4) if (mask >> j) & 1)
    for cf in p.cfs:
        if cf.op not in ucode.CF_EXECS:
            continue
        for a, ins, ser in p.exec_instrs(cf):
            if isinstance(ins, ucode.Fetch):
                if ins.op == ucode.FETCH_VERTEX:
                    continue
                if ins.op in (ucode.FETCH_SET_GRAD_H, ucode.FETCH_SET_GRAD_V):
                    read(ins.src, ins.src_components(3))
                elif ins.op == ucode.FETCH_SET_LOD:
                    read(ins.src, ins.src_components(1))
                else:
                    read(ins.src, ins.src_components({0: 1, 1: 2, 2: 3, 3: 3}[ins.dim]))
                    m = sum(1 << j for j, c in enumerate(ins.dst_components()) if c != 7)
                    write(ins.dst, m, ins.predicated)
                continue
            for o in (ins.vec_operands() if (ins.vec_result_mask() or ins.vec_changes_state()) else []) + \
                    ins.scl_operands():
                if o.kind == 'r':
                    read(o.index, o.comps)
            if not ins.export:
                write(ins.vdst, ins.vec_result_mask(), ins.predicated)
                write(ins.sdst, ins.scl_result_mask(), ins.predicated)
    return live


def plan_packing(xvs, xps):
    """interpolator packing when the Xbox shader pair uses more interpolators than vs_3_0 allows: only the
    components the PS reads are kept, first-fit into as few float4 registers as needed. Returns
    (pack {reg: [(name, comp)|None]*4}, sems {name: semantic}) or (None, None)."""
    if len(xvs.interps) <= MAX_VS_INTERP:
        return None, None
    live = ps_live_in(xps)
    items = sorted(((r, sorted(c)) for r, c in live.items()), key=lambda t: -len(t[1]))
    bins = []                    # [used components]
    place = {}
    for r, comps in items:
        for b, used in enumerate(bins):
            if used + len(comps) <= 4:
                place[r] = (b, used)
                bins[b] += len(comps)
                break
        else:
            place[r] = (len(bins), 0)
            bins.append(len(comps))
    if len(bins) > MAX_VS_INTERP:
        return None, None
    sems = {}
    order = sorted(xps.interps, key=lambda i: (i['usage'], i['index']))
    for b in range(len(bins)):
        sems['p%d' % b] = 'TEXCOORD%d' % b
    pack = {}
    for r, comps in items:
        b, off = place[r]
        pk = [None] * 4
        for k, c in enumerate(comps):
            pk[c] = ('p%d' % b, 'xyzw'[off + k])
        pack[r] = pk
    return pack, sems


def pipeline_check(e, vs, ps, vin_decode=(), L=256, seed=3):
    """VS -> PS check: Xbox VS (xinterp) feeds the Xbox PS by interpolator semantic; the translated VS
    (sm3interp) feeds the translated PS by its output semantics (this also covers interpolator packing).
    Compares the PS outputs (colours, depth, kill). Returns (verdict, report)."""
    import numpy as np
    import xcompare
    (vc, vcode), (pc_, pcode) = e['shaders']
    xvs, xps = xshader.XShader(vcode), xshader.XShader(pcode)
    xr = xcompare.XRun(xvs, L=L, seed=seed)
    if vin_decode:
        xr.vin_xform = {k: (lambda v: v * 2 - 1) for k in vin_decode}
    xo, _ = xr.run()
    regs = {}
    for it in xps.interps:
        k = ('interp', it['usage'], it['index'])
        regs[it['reg']] = xo[k][0] if k in xo else np.zeros((L, 4), np.float32)
    xp = xcompare.XRun(xps, L=L, seed=seed)
    xp.reg_override = regs
    xp.depth_flip = True
    a = xp.run()
    po, _ = xcompare.PRun(vs, L=L, seed=seed).run()
    pr = xcompare.PRun(ps, L=L, seed=seed)
    pr.input_override = {k[1:]: v[0] for k, v in po.items() if k[0] == 'interp'}
    b = pr.run()
    rep = xcompare.compare_outputs(a, b)
    bad = [r for r in rep if r[1] in ('DIFF', 'MISSING')]
    return ('ok' if not bad else 'FAIL'), bad


def translate_stage(code, verify=True, vin_decode=(), rawz=False, pack=None, sems=None):
    try:
        c, notes, src = xlate.translate_container(code, verify=verify, vin_decode=vin_decode, rawz=rawz,
                                                  interp_pack=pack, pack_sems=sems)
        st = 'ok'
        if any(n.startswith('fidelity FAIL') for n in notes):
            st = 'check-failed'
        return c, st, notes, src
    except xlate.Unsupported as err:
        return None, 'unsupported', [str(err)], None
    except Exception as err:      # malformed / unknown container versions
        return None, 'error', ['%s: %s' % (type(err).__name__, err)], None


def pc_entries(e, vin=None, variants=None, verify=True, vin_decode=None):
    """Xbox .fxc entry -> (PC entries, status). status: {'vs': ..., 'ps': ..., 'ps_rawz': ..., 'notes': [...]}
    Entries are only returned when both stages translated."""
    names = variants or variant_names(e['id'], vin)
    (vc, vcode), (pc_, pcode) = e['shaders']
    xvs = xshader.XShader(vcode)
    xps = xshader.XShader(pcode)
    dec = vertex_decode(xvs, vin) if vin_decode is None else list(vin_decode)
    # the VS and PS interpolator registers must agree: Xbox VS export o<reg> -> PS input r<reg> by semantic
    pack, sems = plan_packing(xvs, xps)
    if pack:
        # VS interpolator registers map to the PS registers of the same semantic
        by_sem = {(i['usage'], i['index']): i['reg'] for i in xps.interps}
        vpack = {}
        for i in xvs.interps:
            r = by_sem.get((i['usage'], i['index']))
            if r in pack:
                vpack[i['reg']] = pack[r]
    vs, vst, vnotes, vsrc = translate_stage(vcode, verify, dec, pack=vpack if pack else None, sems=sems)
    ps, pst, pnotes, psrc = translate_stage(pcode, verify, pack=pack, sems=sems)
    status = {'vs': vst, 'ps': pst, 'vs_notes': vnotes, 'ps_notes': pnotes, 'vin_decode': dec,
              'variants': list(names), 'hlsl': (vsrc, psrc)}
    if vs is None or ps is None:
        return [], status
    if verify:
        try:
            status['pipeline'], status['pipeline_report'] = pipeline_check(e, vs, ps, dec)
        except Exception as err:
            status['pipeline'], status['pipeline_report'] = 'error', ['%s: %s' % (type(err).__name__, err)]
    ps_rawz = None
    if any(n.startswith('2x') for n in names):
        ps_rawz, rst, rnotes, _ = translate_stage(pcode, False, rawz=True, pack=pack, sems=sems)
        status['ps_rawz'] = 'RAWZ decode' if rnotes and 'RAWZ depth decode' in rnotes else 'same as 0x'
    ents = []
    for n in names:
        p = ps_rawz if (n.startswith('2x') and ps_rawz is not None) else ps
        ents.append({'id': n, 'shaders': [(vc, vs), (pc_, p)]})
    return ents, status


# ------------------------------------------------------------------------------------------------- CLI
def _scan_inputs(paths):
    import convshaders as cs
    import arkio
    fxcs, shds = [], {}
    for p in paths:
        with open(p, 'rb') as f:
            head = f.read(4)
        if head in (b'\0FXC', b'CXF\0'):
            fxcs.append((p, cs.parse_fxc(open(p, 'rb').read())))
            continue
        a = arkio.Ark(p)
        for k in a.order:
            if a.ents[k][3] < 8:
                continue
            d = a.get(k)
            if d[:4] == b'\0FXC':
                fxcs.append(('%s:%08x' % (os.path.basename(p), k), cs.parse_fxc(d)))
            elif d[:4] == b'SHDR':
                for se in cs.parse_shdr(d)['ents']:
                    shds.setdefault(se['id'], se)
        a.close()
    return fxcs, shds


def _pc_ids(game):
    import glob
    import arkio
    ids = set()
    arks = [os.path.join(game, 'resident.ark')] + glob.glob(game + r'\*.ark') + \
        glob.glob(game + r'\Environments\Levels\*.ark')
    for fn in sorted(set(arks)):
        a = arkio.Ark(fn)
        for k in a.order:
            if a.ents[k][3] < 8:
                continue
            t, off, csz, us = a.ents[k]
            a.f.seek(off)
            raw = a.f.read(min(csz, 64))
            if t == 1:
                head = arkio.lzss_n(raw + bytes(16), 4, 4)
            else:
                head = raw[:4]
            if head != b'CXF\0':
                continue
            import convshaders as cs
            for e in cs.parse_fxc(a.get(k))['ents']:
                ids.add(e['id'][2:])
        a.close()
    return ids


def main(argv):
    import convshaders as cs
    args = [a for a in argv if not a.startswith('--')]
    if len(args) < 2:
        print(__doc__ if __doc__ else 'usage')
        print('usage: xenos2pc.py <out dir> <xbox ark|fxc> [...] [--missing-only] [--ids a,b] [--game <dir>]')
        return 2
    out = args[0]
    # the game's data folder (only --missing-only reads it): --game, else below the game folder in GAME_DIR
    game = os.path.join(os.environ['GAME_DIR'], 'data') if os.environ.get('GAME_DIR') else None
    want = None
    if '--ids' in argv:
        want = set(x.lower().replace('0x', '') for x in argv[argv.index('--ids') + 1].split(','))
        args = [a for a in args if a != argv[argv.index('--ids') + 1]]
    if '--game' in argv:
        game = argv[argv.index('--game') + 1]
        args = [a for a in args if a != game]
    fxcs, shds = _scan_inputs(args[1:])
    pc_ids = set()
    if '--missing-only' in argv:
        cache = os.path.join(out, 'pc_ids.txt')        # IDs present in the PC data (cached scan)
        if os.path.exists(cache):
            pc_ids = set(open(cache).read().split())
        else:
            if not game:
                print('--missing-only needs --game <the game\'s data folder> (or GAME_DIR set to the Split/Second '
                      'game folder)')
                return 2
            pc_ids = _pc_ids(game)
            os.makedirs(out, exist_ok=True)
            open(cache, 'w').write('\n'.join(sorted(pc_ids)))
    os.makedirs(os.path.join(out, 'hlsl'), exist_ok=True)
    seen, ents, lines = set(), [], []
    hdr = None
    for src, f in fxcs:
        hdr = hdr or (f['ver'], f['flags'])
        for e in f['ents']:
            i = e['id'][2:]
            if i in seen or (want and i not in want) or i in pc_ids:
                continue
            seen.add(i)
            se = shds.get(e['id'])
            es, st = pc_entries(e, se['vin'] if se else None)
            vs_ok = st['vs'] == 'ok'
            ps_ok = st['ps'] == 'ok'
            for j, s in enumerate(st['hlsl']):
                if s:
                    open(os.path.join(out, 'hlsl', '%s_%s.hlsl' % (i, ('vs', 'ps')[j])), 'w').write(s)
            ents += es
            lines.append('%s %-12s vs %-12s ps %-12s pipeline %-5s variants %-14s vin_decode %s | %s | %s' % (
                e['id'], src.split(':')[0], st['vs'], st['ps'], st.get('pipeline', '-'),
                '/'.join(v[:2] for v in st['variants']), st['vin_decode'] or '-', '; '.join(st['vs_notes']),
                '; '.join(st['ps_notes']) + ('' if st.get('pipeline') in (None, 'ok') else
                                             ' | pipeline: %s' % st.get('pipeline_report'))))
            print(lines[-1], flush=True)
    data = cs.build_fxc({'ver': hdr[0] if hdr else 2, 'flags': hdr[1] if hdr else 0x2060, 'ents': ents}, '<')
    open(os.path.join(out, 'translated.fxc'), 'wb').write(data)
    n_ok = sum(1 for l in lines if ' vs ok ' in l and ' ps ok ' in l)
    n_pipe = sum(1 for l in lines if ' pipeline ok ' in l)
    lines.append('# %d IDs, %d fully translated and self-checked (VS and PS), %d also pass the VS->PS pipeline '
                 'check, %d PC entries in translated.fxc' % (len(lines), n_ok, n_pipe, len(ents)))
    open(os.path.join(out, 'status.txt'), 'w').write('\n'.join(lines) + '\n')
    print(lines[-1])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
