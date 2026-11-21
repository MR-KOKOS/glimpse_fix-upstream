# Tests for the Xenos -> SM3 translator.
#   python -I test_xenos.py <work dir> [--full]
# 1. collects the Xbox / PC shader files into <work>/all.pkl if missing (validate.py collect; read only)
# 2. interpreter self tests (cube helpers, a D3DCompiler optimiser bug repro)
# 3. translates a sample (or, with --full, every shared ID: validate.py run) and prints fidelity / truth
import os
import sys
import random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np      # noqa: E402
import validate         # noqa: E402
import xlate            # noqa: E402
import xinterp          # noqa: E402
import sm3              # noqa: E402
import sm3interp        # noqa: E402


def test_cube():
    """xe_cube / xe_cubedir (HLSL, compiled, run by sm3interp) vs the Python reference"""
    src = xlate.HELPERS + '''
struct IN { float4 a : TEXCOORD0; float4 c : TEXCOORD1; };
struct OUT { float4 o0 : COLOR0; float4 o1 : COLOR1; };
OUT main(IN I) { OUT O; O.o0 = xe_cube(I.a); O.o1 = float4(xe_cubedir(I.c.xyz), 0); return O; }
'''
    code = xlate.compile_hlsl(src, 'ps_3_0')
    L = 64
    r = np.random.default_rng(2)
    a = r.uniform(-1, 1, (L, 4)).astype(np.float32)
    c = np.stack([r.uniform(1, 2, L), r.uniform(1, 2, L), r.integers(0, 6, L), np.zeros(L)], 1).astype(np.float32)
    o, k = sm3interp.run(sm3.Shader(code), L, np.zeros((256, L, 4), np.float32), {0: a, 1: c})
    d = xinterp.cube_dir(c)
    err = np.abs(o[(sm3.T_COLOROUT, 1)][0][:, :3] - d).max()
    print('cube helpers: max error %g' % err)
    return err < 1e-5


OPT_BUG = '''
struct IN { float4 i0 : TEXCOORD0; float4 i1 : TEXCOORD1; float4 i2 : TEXCOORD2; };
struct OUT { float4 c1 : COLOR1; float4 c2 : COLOR2; float4 c3 : COLOR3; float4 c0 : COLOR0; };
OUT main(IN I) {
    OUT O = (OUT)0; float4 r0 = I.i0, r1 = I.i1, r2 = I.i2, r3 = 0, vr; float pv;
    vr = dot(r0.wyz, r0.wyz).xxxx; r0.x = vr.x; pv = rsqrt(abs(r0.x)); r0.x = pv;
    vr = r0.yyzw * r0.xxxx; r1.yzw = vr.yzw;
    vr = dot(r1.wyz, r1.wyz).xxxx; r0.x = vr.x; pv = rsqrt(abs(r0.x)); r0.x = pv;
    vr = r1.yzww * r0.xxxx; r3.xyz = vr.xyz;
    O.c3 = r3;
    vr = dot(r3.zxy, r2.zxy).xxxx; r3.w = vr.w;
    vr = dot(r3.zxyw, float4(r2.zxy, 1)).xxxx; O.c2.z = vr.z;
    vr = r1.yzzw * 0.5; r0.xzw = vr.xzw; vr = r0.xzww + 0.5; O.c1.xyz = vr.xyz;
    return O;
}
'''


def test_optimiser_bug():
    """documents the D3DCompiler (41/43/47) optimiser bug the translator works around: optimised and
    unoptimised code of the same HLSL give different COLOR3 (component rotation)"""
    L = 32
    r = np.random.default_rng(1)
    ins = {k: r.uniform(-1, 1, (L, 4)).astype(np.float32) for k in range(3)}
    res = []
    for opt in (False, True):
        code = xlate.compile_hlsl(OPT_BUG, 'ps_3_0', optimize=opt)
        sh = sm3.Shader(code)
        regs = {}
        for i in sh.instrs:
            if i.op == 'dcl' and sm3.dcl_info(i)[0] == 'v':
                regs[i.dst.num] = ins[sm3.dcl_info(i)[3]]
        o, k = sm3interp.run(sh, L, np.zeros((256, L, 4), np.float32), regs)
        res.append(o[(sm3.T_COLOROUT, 3)][0][:, :3])
    ref = ins[0][:, 1:4] / np.sqrt((ins[0][:, 1:4] ** 2).sum(1, keepdims=True))
    e0, e1 = np.abs(res[0] - ref).max(), np.abs(res[1] - ref).max()
    print('optimiser bug repro: unoptimised error %g, optimised error %g (%s)'
          % (e0, e1, 'bug present' if e1 > 1e-3 else 'not reproduced'))
    return e0 < 1e-4


def main(argv):
    work = argv[0] if argv else os.path.join(HERE, '_work')
    if not os.path.exists(os.path.join(work, 'all.pkl')):
        print('collecting shader files into', work)
        validate.collect(work)
    ok = test_cube() & test_optimiser_bug()
    if '--full' in argv:
        validate.run(work)
        return 0 if ok else 1
    xb, pc, db = validate.load(work)
    ids = sorted(i for i in xb if ('0x' + i) in pc)
    random.Random(1).shuffle(ids)
    n = {'ok': 0, 'fail': 0, 'unsupported': 0}
    for i in ids[:60]:
        e = xb[i][1]
        for j in (0, 1):
            try:
                code, notes, src = xlate.translate_container(e['shaders'][j][1], verify=True)
                n['ok' if 'fidelity ok' in notes else 'fail'] += 1
            except xlate.Unsupported:
                n['unsupported'] += 1
            except ValueError:
                n['unsupported'] += 1
    print('sample of 60 IDs (120 shaders): fidelity', n)
    return 0 if ok and n['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
