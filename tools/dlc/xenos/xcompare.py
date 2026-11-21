# Semantic comparison helpers: run Xenos shaders on random inputs that are keyed by NAME / SEMANTIC (constant
# names from the CTAB, vertex inputs and interpolators by D3DDECLUSAGE + index, samplers by name), so two
# shaders with different register allocation / constant addresses get the same inputs.
import zlib
import numpy as np
import ucode
import xinterp
import xshader

F = np.float32


def rng_for(*key):
    return np.random.default_rng(zlib.crc32(repr(key).encode()) & 0xFFFFFFFF)


def const_values(name, count, L, seed=0):
    r = rng_for('c', name, seed)
    return r.uniform(-1.5, 1.5, (count, L, 4)).astype(F)


def semantic_values(usage, index, L, seed=0):
    r = rng_for('sem', usage, index, seed)
    return r.uniform(-1.0, 1.0, (L, 4)).astype(F)


def tex_params(name):
    r = rng_for('tex', name)
    return r.uniform(-4, 4, (4, 4)).astype(F), r.uniform(0, 6.3, 4).astype(F)


def proc_texture(name, coords, dim, lod):
    """smooth procedural texture in [0,1], depending on the sampler name, coordinates and LOD"""
    A, ph = tex_params(name)
    c = coords.astype(F)
    if dim == ucode.DIM_CUBE or dim == 'cube':
        n = np.sqrt((c[:, :3] ** 2).sum(1, keepdims=True))
        c = np.concatenate([c[:, :3] / np.where(n == 0, 1, n), np.zeros((len(c), 1), F)], 1)
    elif dim in (ucode.DIM_2D, '2d'):
        c = c.copy()
        c[:, 2:] = 0
    elif dim in (ucode.DIM_1D, '1d'):
        c = c.copy()
        c[:, 1:] = 0
    else:
        c = c.copy()
        c[:, 3] = 0
    v = F(0.5) + F(0.5) * np.sin(c @ A.T + ph)
    if lod is not None:
        v = v * (F(1) + F(0.05) * np.asarray(lod, F))[:, None]
    return v.astype(F)


class XRun:
    """Run one Xbox shader container with name/semantic keyed random inputs."""

    def __init__(self, x, L=256, seed=0, faces=None, pos=None):
        self.x = x
        self.L = L
        self.seed = seed
        self.prog = ucode.Program(x.code)
        self.faces = faces
        self.pos = pos
        self.vin_xform = None      # {(usage, index): f(values) -> values} applied to fetched vertex elements
        self.depth_flip = False    # scene depth samplers return 1 - texture (PC depth convention)
        self.reg_override = None   # PS: {register: (L, 4)} initial values replacing the interpolators

    def consts(self):
        L = self.L
        C = np.zeros((256, L, 4), F)
        for c in self.x.ctab['consts']:
            if c['set'] == 2:
                C[c['reg']:c['reg'] + c['count']] = const_values(c['name'], c['count'], L, self.seed)[
                    :max(0, min(c['count'], 256 - c['reg']))]
        for reg in range(256):
            lit = self.x.literal(reg)
            if lit is not None:
                C[reg] = np.array(lit, F)[None, :]
        return C

    def sampler_name(self, fetch_const):
        reg = fetch_const - (0 if self.x.is_ps else 16)
        for c in self.x.ctab['consts']:
            if c['set'] == 3 and c['reg'] <= reg < c['reg'] + c['count']:
                return c['name']
        return 'tf%d' % fetch_const

    def tex(self, fc, op, dim, coords, lod, bias, comp, grad, ins):
        name = self.sampler_name(fc)
        L = self.L
        if op == ucode.FETCH_GET_GRADIENTS:
            import sm3interp
            dx, dy = sm3interp.quad_ddx(coords), sm3interp.quad_ddy(coords)
            return np.stack([dx[:, 0], dy[:, 0], dx[:, 1], dy[:, 1]], 1).astype(F)
        if op != ucode.FETCH_TEXTURE:
            return np.zeros((L, 4), F)
        if dim == ucode.DIM_CUBE:
            coords = np.concatenate([xinterp.cube_dir(coords), np.zeros((L, 1), F)], 1)
        tl = None
        if lod is not None or bias or not comp:
            tl = np.zeros(L, F) + F(bias)
            if lod is not None:
                tl = tl + lod
        v = proc_texture(name, coords, dim, tl)
        if self.depth_flip:
            import xlate
            if xlate.is_scene_depth(name):
                v = F(1) - v          # the PC texture holds 1 - (Xbox depth)
        return v

    def run(self):
        x, L = self.x, self.L
        np.seterr(all='ignore')
        regs = {}
        vf = None
        if x.is_ps:
            for it in x.interps:
                regs[it['reg']] = semantic_values(it['usage'], it['index'], L, self.seed)
            if self.reg_override:
                regs.update(self.reg_override)
            if x.param_gen:
                px, py, face = pixel_params(L, self.seed)
                pg = np.zeros((L, 4), F)
                pg[:, 0] = np.where(face, px, -px)
                pg[:, 1] = py
                regs[x.param_gen_reg] = pg
        else:
            by_slot = {v['slot']: v for v in x.vertex_elements}

            def vf(ins):
                # vfetch_mini reuses the last full fetch's element; elements are listed per fetch slot
                for a, i2 in self.prog.slots.items():
                    if i2 is ins:
                        v = by_slot.get(a)
                        break
                else:
                    v = None
                if v is None:
                    return np.zeros((L, 4), F)
                val = semantic_values(v['usage'], v['index'], L, self.seed)
                if self.vin_xform and (v['usage'], v['index']) in self.vin_xform:
                    val = self.vin_xform[(v['usage'], v['index'])](val)
                return val
        r = xinterp.run(self.prog, x.is_ps, L, self.consts(), regs=regs, vfetch=vf, tex=self.tex)
        out = {}
        for k, (v, w) in r.exports.items():
            if not x.is_ps and k[0] == 'o':
                it = [i for i in x.interps if i['reg'] == k[1]]
                if not it:
                    continue
                key = ('interp', it[0]['usage'], it[0]['index'])
                mk = np.array([(it[0]['mask'] >> j) & 1 for j in range(4)], bool)
                w = w & mk[None, :]
            else:
                key = k
            out[key] = (v, w)
        return out, r.kill


def pixel_params(L, seed):
    r = rng_for('vpos', seed)
    px = np.floor(r.uniform(0, 1280, L)).astype(F)
    py = np.floor(r.uniform(0, 720, L)).astype(F)
    face = r.uniform(-1, 1, L) > 0
    return px, py, face


USAGE_BY_NAME = {n: i for i, n in enumerate(['position', 'blendweight', 'blendindices', 'normal', 'psize',
                                             'texcoord', 'tangent', 'binormal', 'tessfactor', 'positiont',
                                             'color', 'fog', 'depth', 'sample'])}


class PRun:
    """Run D3D9 SM3 bytecode with the same name/semantic keyed inputs as XRun."""

    def __init__(self, code, L=256, seed=0, ctab=None):
        import sm3
        self.sh = sm3.Shader(code)
        self.L, self.seed = L, seed
        self.ct = ctab or self.sh.ctab()
        self.input_override = None    # {(usage, index): (L, 4)} input values by semantic

    def run(self):
        import sm3
        import sm3interp
        np.seterr(all='ignore')
        sh, L = self.sh, self.L
        C = np.zeros((256, L, 4), F)
        ints = np.zeros((16, 4), int)
        bools = np.zeros(16, bool)
        snames = {}
        for c in self.ct['consts']:
            if c['set'] == 2:
                n = max(0, min(c['count'], 256 - c['reg']))
                C[c['reg']:c['reg'] + n] = const_values(c['name'], c['count'], L, self.seed)[:n]
            elif c['set'] == 3:
                for k in range(c['count']):
                    snames[c['reg'] + k] = c['name']
            elif c['set'] == 0:
                bools[c['reg']] = bool(rng_for('b', c['name']).integers(0, 2))
        inputs, misc = {}, {}
        outs = {}
        for ins in sh.instrs:
            if ins.op != 'dcl':
                continue
            d = sm3.dcl_info(ins)
            if d[0] == 'v':
                key = (USAGE_BY_NAME[d[2]], d[3])
                if self.input_override and key in self.input_override:
                    inputs[d[1]] = self.input_override[key]
                else:
                    inputs[d[1]] = semantic_values(key[0], key[1], L, self.seed)
            elif d[0] == 'misc':
                px, py, face = pixel_params(L, self.seed)
                if d[1] == 0:
                    misc[0] = np.stack([px, py, np.zeros(L, F), np.zeros(L, F)], 1)
                else:
                    misc[1] = np.repeat(np.where(face, F(1), F(-1))[:, None], 4, 1)
            elif d[0] == 'o':
                outs[d[1]] = (USAGE_BY_NAME[d[2]], d[3])

        def tex(samp, kind, c, lod):
            nm = snames.get(samp, 's%d' % samp)
            if kind == 'cube':
                return proc_texture(nm, c, 'cube', lod)
            return proc_texture(nm, c, '2d' if kind == '2d' else '3d', lod)
        o, kill = sm3interp.run(sh, L, C, inputs, misc, tex, ints, bools)
        res = {}
        for (t, num), v in o.items():
            if t == sm3.T_OUTPUT:
                u, i = outs.get(num, (None, None))
                key = ('pos', 0) if u == 0 else ('interp', u, i)
            elif t == sm3.T_RASTOUT:
                key = ('pos', 0) if num == 0 else ('psize', 0)
            elif t == sm3.T_COLOROUT:
                key = ('color', num)
            elif t == sm3.T_DEPTHOUT:
                key = ('depth', 0)
            else:
                key = (t, num)
            res[key] = v
        return res, kill


def compare_outputs(a, b, tol=1e-3):
    """a, b: (outputs, kill). Returns list of (key, status, detail)."""
    np.seterr(all='ignore')
    oa, ka = a
    ob, kb = b
    rep = []
    if (ka != kb).any():
        rep.append(('kill', 'DIFF', '%d of %d lanes differ' % ((ka != kb).sum(), len(ka))))
    alive = ~(ka | kb)
    for k in sorted(set(oa) | set(ob), key=str):
        if k not in oa or k not in ob:
            rep.append((k, 'MISSING', 'only in %s' % ('A' if k in oa else 'B')))
            continue
        va, wa = oa[k]
        vb, wb = ob[k]
        if k[0] in ('depth', 'psize'):          # scalar outputs: only x is meaningful
            wa, wb = wa.copy(), wb.copy()
            wa[:, 1:] = False
            wb[:, 1:] = False
        wm = wa.any(0), wb.any(0)
        if (wm[0] != wm[1]).any():
            rep.append((k, 'MASK', 'written A %s B %s' % (wm[0].astype(int), wm[1].astype(int))))
        both = wa & wb & alive[:, None]
        # lanes where either side is NaN/inf are numeric edge cases of the random inputs (Xenos legacy
        # 0 * inf = 0 vs IEEE NaN on PC, exp overflow, ...): excluded, but counted
        fin = np.isfinite(va) & np.isfinite(vb)
        nonfin = both & ~fin & ~(va == vb) & ~(np.isnan(va) & np.isnan(vb))
        both = both & fin
        d = np.abs(va - vb)
        scale = np.maximum(1, np.maximum(np.abs(va), np.abs(vb)))
        bad = both & ~(va == vb) & ~(d <= tol * scale)
        if nonfin.any() and not bad.any():
            rep.append((k, 'nonfinite', '%d lanes differ only in NaN/inf' % nonfin.any(1).sum()))
            continue
        if bad.any():
            comp = bad.any(0)
            rep.append((k, 'DIFF', 'components %s, %d of %d lanes, max |d| %.4g' %
                        (''.join('xyzw'[j] for j in range(4) if comp[j]), bad.any(1).sum(), len(bad),
                         np.nanmax(np.where(bad, d, 0)))))
        else:
            rep.append((k, 'ok', ''))
    return rep
