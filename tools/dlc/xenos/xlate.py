# Xenos microcode (Xbox 360 shader container) -> HLSL for vs_3_0 / ps_3_0, compiled with fxc.
#
# Register model: every Xenos temporary becomes a float4 local, the previous-scalar register `pv`, the
# predicate `p0` (bool), the address register `a0` (int). Each ALU instruction evaluates both co-issued
# operations from the old register values, then writes (Xenos semantics), wrapped in `if (p0 == cond)` when
# predicated. Constants keep their Xbox registers (the .fxc constant table registers, which the game binds);
# literal constants from the container's definition table become HLSL literals. Vertex fetches read the D3D9
# input declared with the container's vertex element semantic; interpolators and PS inputs use the container's
# interpolator semantics; the PS param-gen register is rebuilt from VPOS/VFACE. Texture fetch: tex2D/tex2Dbias/
# tex2Dlod/tex2Dgrad (+ cube via reconstructed direction, 3D). Control flow: exec/pexec, forward predicated
# jumps (as if blocks), loops are not supported (raise Unsupported).
import os
import struct
import subprocess
import tempfile
import ucode as U
import xshader
import sm3

FXC_CANDIDATES = [
    r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\fxc.exe',
    r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\fxc.exe',
    r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x86\fxc.exe',
    r'C:\Program Files (x86)\Windows Kits\10\bin\10.0.19041.0\x64\fxc.exe',
]


class Unsupported(Exception):
    pass


# HLSL helpers: Xenos cube (face coordinates), the tfetchCube coordinates back to a direction, max4, the
# rcpf/rsqf "clamp to zero" rule
HELPERS = '''float4 xe_cube(float4 a) {
    float x = a.z, y = a.w, z = a.x; float3 r = abs(float3(x, y, z));
    if (r.z >= r.x && r.z >= r.y) return float4(-y, z < 0.0 ? -x : x, 2.0 * z, z < 0.0 ? 5.0 : 4.0);
    if (r.y >= r.x) return float4(y < 0.0 ? -z : z, x, 2.0 * y, y < 0.0 ? 3.0 : 2.0);
    return float4(-y, x < 0.0 ? z : -z, 2.0 * x, x < 0.0 ? 1.0 : 0.0);
}
float3 xe_cubedir(float3 c) {
    float s = (c.x - 1.5) * 2.0, t = (c.y - 1.5) * 2.0; float f = floor(c.z + 0.5);
    if (f < 0.5) return float3(1.0, -t, -s);
    if (f < 1.5) return float3(-1.0, -t, s);
    if (f < 2.5) return float3(s, 1.0, t);
    if (f < 3.5) return float3(s, -1.0, -t);
    if (f < 4.5) return float3(s, -t, 1.0);
    return float3(-s, -t, -1.0);
}
float xe_max4(float4 a) {
    return (a.x >= a.y && a.x >= a.z && a.x >= a.w) ? a.x : ((a.y >= a.z && a.y >= a.w) ? a.y : (a.z >= a.w ? a.z : a.w));
}
float xe_ff(float v) { return isinf(v) ? 0.0 : v; }'''


def is_depth_texture(name):
    """samplers that read the scene depth buffer (PC: INTZ/RAWZ/DF24 depth texture). Learned from the PC 2x
    (RAWZ) variants, which decode exactly these."""
    n = name.lower()
    return 'scenedepth' in n or 'depthtex' in n or 'ztex' in n


def is_scene_depth(name):
    """depth reads whose value must be converted: the Xbox renders with a reversed depth buffer, so the Xbox
    shader expects 1 - (PC depth). Checked on the base game: StandardParticle_SceneDepthTex (39 of 39 PS),
    blend_using_ztex_SceneDepth, SimpleMotionBlur_SceneDepth, dof_24bit_FinalSceneDepthTex match the PC
    shaders only with this conversion."""
    n = name.lower()
    return 'scenedepth' in n or 'ztex' in n


USAGE_SEM = {0: 'POSITION', 1: 'BLENDWEIGHT', 2: 'BLENDINDICES', 3: 'NORMAL', 4: 'PSIZE', 5: 'TEXCOORD',
             6: 'TANGENT', 7: 'BINORMAL', 8: 'TESSFACTOR', 9: 'POSITIONT', 10: 'COLOR', 11: 'FOG', 12: 'DEPTH',
             13: 'SAMPLE'}
SW = 'xyzw'


def find_fxc():
    env = os.environ.get('FXC')
    if env and os.path.exists(env):
        return env
    for c in FXC_CANDIDATES:
        if os.path.exists(c):
            return c
    base = r'C:\Program Files (x86)\Windows Kits\10\bin'
    if os.path.isdir(base):
        for v in sorted(os.listdir(base), reverse=True):
            for a in ('x64', 'x86'):
                p = os.path.join(base, v, a, 'fxc.exe')
                if os.path.exists(p):
                    return p
    raise RuntimeError('fxc.exe not found (set FXC)')


def flit(v):
    if v != v:
        return '(0.0/0.0)'
    if v in (float('inf'), float('-inf')):
        return '(%s1.0/0.0)' % ('-' if v < 0 else '')
    r = repr(float(struct.unpack('<f', struct.pack('<f', v))[0]))
    if 'e' in r or 'E' in r:
        r = '%.9e' % v
    return r


class Translator:
    def __init__(self, x, notes=None):
        self.x = x
        self.prog = U.Program(x.code)
        self.is_ps = x.is_ps
        self.notes = notes if notes is not None else []
        self.lines = []
        self.ind = 1
        self.used_consts = set()
        self.rel_consts = False
        self.samplers = {}            # register -> kind
        self.temps = set()
        self.inputs = {}              # vs: slot -> (name, semantic)
        self.outputs = {}             # name -> (semantic, type)
        self.ve = {v['slot']: v for v in x.vertex_elements}
        self.interp_by_reg = {i['reg']: i for i in x.interps}
        self.uses_vpos = False
        self.vin_decode = set()       # {(usage, index)} vertex elements to decode as 2v-1
        self.rawz = False             # 2x variant: depth textures hold RAWZ-encoded depth (dot(t.wxy, k))
        self.pc_depth = False         # convert sampled depth (samplers named *depth*) to the Xbox convention
        self.interp_pack = None       # {xbox interpolator reg: [(pc name, component) or None] x 4}
        self.pack_sems = {}           # pc name -> semantic

    # ------------------------------------------------------------------ helpers
    def emit(self, s):
        self.lines.append('    ' * self.ind + s)

    def note(self, s):
        if s not in self.notes:
            self.notes.append(s)

    def temp(self, i):
        self.temps.add(i)
        return 'r%d' % i

    def const_expr(self, idx, rel):
        if rel:
            if self.is_ps:
                raise Unsupported('relative constant addressing in a pixel shader')
            if rel == 'aL':
                raise Unsupported('aL-relative constant addressing')
            self.rel_consts = True
            return 'C[a0 + %d]' % idx
        lit = self.x.literal(idx)
        if lit is not None:
            return 'float4(%s)' % ', '.join(flit(v) for v in lit)
        self.used_consts.add(idx)
        return 'c%d' % idx

    def operand(self, o, ncomp=None):
        if o.kind == 'r':
            if o.rel:
                raise Unsupported('aL-relative temporary register')
            base = self.temp(o.index)
        else:
            base = self.const_expr(o.index, o.rel)
        e = '%s.%s' % (base, ''.join(SW[c] for c in o.comps))
        if o.abs:
            e = 'abs(%s)' % e
        if o.neg:
            e = '(-%s)' % e
        return e

    # ------------------------------------------------------------------ ALU
    def alu(self, ins):
        vm = ins.vec_result_mask()
        sm = ins.scl_result_mask()
        c0m, c1m = ins.const0_mask(), ins.const1_mask()
        body = []
        vop = ins.vop
        need_vec = bool(vm) or ins.vec_changes_state()
        if need_vec:
            vops = ins.vec_operands()
            if vop in (U.V_DP4, U.V_DP3) and len(vops) == 2:
                # dot products with the same permutation on both sides (the Xenos compiler's zxyw habit) are
                # written unpermuted: same value, and it avoids a D3DCompiler optimiser bug with permuted dots
                n = 4 if vop == U.V_DP4 else 3
                a_, b_ = vops
                if a_.comps[:n] == b_.comps[:n] and sorted(a_.comps[:n]) == list(range(n)):
                    vops = [U.Operand(o.kind, o.index, o.rel, [0, 1, 2, 3], o.neg, o.abs) for o in vops]
            ops = [self.operand(o) for o in vops]
            a = ops[0] if ops else None
            b = ops[1] if len(ops) > 1 else None
            c = ops[2] if len(ops) > 2 else None
            e = None
            if vop == U.V_ADD:
                e = '%s + %s' % (a, b)
            elif vop == U.V_MUL:
                e = '%s * %s' % (a, b)
            elif vop in (U.V_MAX, U.V_MAXA):
                if vop == U.V_MAXA:
                    body.append('a0 = (int)clamp(floor((%s).w + 0.5), -256.0, 255.0);' % a)
                e = 'max(%s, %s)' % (a, b) if a != b else a
            elif vop == U.V_MIN:
                e = 'min(%s, %s)' % (a, b) if a != b else a
            elif vop == U.V_SEQ:
                e = '(float4)(%s == %s)' % (a, b)
            elif vop == U.V_SGT:
                e = '(float4)(%s > %s)' % (a, b)
            elif vop == U.V_SGE:
                e = '(float4)(%s >= %s)' % (a, b)
            elif vop == U.V_SNE:
                e = '(float4)(%s != %s)' % (a, b)
            elif vop == U.V_FRC:
                e = 'frac(%s)' % a
            elif vop == U.V_TRUNC:
                e = 'trunc(%s)' % a
            elif vop == U.V_FLOOR:
                e = 'floor(%s)' % a
            elif vop == U.V_MAD:
                e = '%s * %s + %s' % (a, b, c)
            elif vop == U.V_CNDEQ:
                e = '((%s) == 0.0 ? %s : %s)' % (a, b, c)
            elif vop == U.V_CNDGE:
                e = '((%s) >= 0.0 ? %s : %s)' % (a, b, c)
            elif vop == U.V_CNDGT:
                e = '((%s) > 0.0 ? %s : %s)' % (a, b, c)
            elif vop == U.V_DP4:
                e = 'dot(%s, %s).xxxx' % (a, b)
            elif vop == U.V_DP3:
                e = 'dot((%s).xyz, (%s).xyz).xxxx' % (a, b)
            elif vop == U.V_DP2ADD:
                e = '(dot((%s).xy, (%s).xy) + (%s).x).xxxx' % (a, b, c)
            elif vop == U.V_CUBE:
                e = 'xe_cube(%s)' % a
            elif vop == U.V_MAX4:
                e = 'xe_max4(%s).xxxx' % a
            elif vop in (U.V_SETP_EQ_PUSH, U.V_SETP_NE_PUSH, U.V_SETP_GT_PUSH, U.V_SETP_GE_PUSH):
                cmpo = {U.V_SETP_EQ_PUSH: '==', U.V_SETP_NE_PUSH: '!=', U.V_SETP_GT_PUSH: '>',
                        U.V_SETP_GE_PUSH: '>='}[vop]
                body.append('va = %s; vb = %s;' % (a, b))
                body.append('p0 = (va.w == 0.0) && (vb.w %s 0.0);' % cmpo)
                e = '(((va.x == 0.0) && (vb.x %s 0.0)) ? 0.0 : va.x + 1.0).xxxx' % cmpo
            elif vop in (U.V_KILL_EQ, U.V_KILL_GT, U.V_KILL_GE, U.V_KILL_NE):
                if not self.is_ps:
                    raise Unsupported('kill in a vertex shader')
                cmpo = {U.V_KILL_EQ: '==', U.V_KILL_GT: '>', U.V_KILL_GE: '>=', U.V_KILL_NE: '!='}[vop]
                body.append('va = (float4)(any(%s %s %s));' % (a, cmpo, b))
                body.append('if (va.x != 0.0) discard;')
                e = 'va'
            elif vop == U.V_DST:
                e = 'float4(1.0, (%s).y * (%s).y, (%s).z, (%s).w)' % (a, b, a, b)
            else:
                raise Unsupported('vector opcode %d' % vop)
            if ins.vclamp:
                e = 'saturate(%s)' % e
            body.append('vr = %s;' % e)
        # scalar
        sop = ins.sop
        name, nops, two = U.SCL_INFO[sop]
        sops = ins.scl_operands()
        if nops == 1:
            o = sops[0]
            sa =self.operand(U.Operand(o.kind, o.index, o.rel, [o.comps[0]], o.neg, o.abs))
            sb = self.operand(U.Operand(o.kind, o.index, o.rel, [o.comps[1]], o.neg, o.abs)) if two else None
        elif nops == 2:
            sa = self.operand(sops[0])
            sb = self.operand(sops[1])
        se = None
        cmp_ops = {U.S_SETP_EQ: '==', U.S_SETP_NE: '!=', U.S_SETP_GT: '>', U.S_SETP_GE: '>='}
        kill_ops = {U.S_KILLS_EQ: '== 0.0', U.S_KILLS_GT: '> 0.0', U.S_KILLS_GE: '>= 0.0', U.S_KILLS_NE: '!= 0.0',
                    U.S_KILLS_ONE: '== 1.0'}
        if sop in (U.S_ADDS, U.S_ADDSC0, U.S_ADDSC1):
            se = '%s + %s' % (sa, sb)
        elif sop == U.S_ADDS_PREV:
            se = '%s + pv' % sa
        elif sop in (U.S_MULS, U.S_MULSC0, U.S_MULSC1):
            se = '%s * %s' % (sa, sb)
        elif sop == U.S_MULS_PREV:
            se = '%s * pv' % sa
        elif sop == U.S_MULS_PREV2:
            se = '((pv == -3.402823466e38 || !isfinite(pv) || !isfinite(%s) || %s <= 0.0) ? -3.402823466e38 : %s * pv)' \
                 % (sb, sb, sa)
        elif sop == U.S_MAXS:
            se = 'max(%s, %s)' % (sa, sb) if sa != sb else sa
        elif sop == U.S_MINS:
            se = 'min(%s, %s)' % (sa, sb) if sa != sb else sa
        elif sop == U.S_SEQS:
            se = '(float)(%s == 0.0)' % sa
        elif sop == U.S_SGTS:
            se = '(float)(%s > 0.0)' % sa
        elif sop == U.S_SGES:
            se = '(float)(%s >= 0.0)' % sa
        elif sop == U.S_SNES:
            se = '(float)(%s != 0.0)' % sa
        elif sop == U.S_FRCS:
            se = 'frac(%s)' % sa
        elif sop == U.S_TRUNCS:
            se = 'trunc(%s)' % sa
        elif sop == U.S_FLOORS:
            se = 'floor(%s)' % sa
        elif sop == U.S_EXP:
            se = 'exp2(%s)' % sa
        elif sop in (U.S_LOG, U.S_LOGC):
            se = 'log2(%s)' % sa
            if sop == U.S_LOGC:
                se = 'max(%s, -3.402823466e38)' % se
        elif sop in (U.S_RCP, U.S_RCPC, U.S_RCPF):
            se = '(1.0 / %s)' % sa
            if sop == U.S_RCPC:
                se = 'clamp(%s, -3.402823466e38, 3.402823466e38)' % se
            elif sop == U.S_RCPF:
                se = 'xe_ff(%s)' % se
        elif sop in (U.S_RSQ, U.S_RSQC, U.S_RSQF):
            se = 'rsqrt(%s)' % sa
            if sop == U.S_RSQC:
                se = 'min(%s, 3.402823466e38)' % se
            elif sop == U.S_RSQF:
                se = 'xe_ff(%s)' % se
        elif sop in (U.S_MAXAS, U.S_MAXASF):
            body.append('a0 = (int)clamp(floor(%s%s), -256.0, 255.0);' % (sa, ' + 0.5' if sop == U.S_MAXAS else ''))
            se = 'max(%s, %s)' % (sa, sb) if sa != sb else sa
        elif sop in (U.S_SUBS, U.S_SUBSC0, U.S_SUBSC1):
            se = '%s - %s' % (sa, sb)
        elif sop == U.S_SUBS_PREV:
            se = '%s - pv' % sa
        elif sop in cmp_ops:
            body.append('sa = %s;' % sa)
            body.append('pn = sa %s 0.0;' % cmp_ops[sop])
            se = '(pn ? 0.0 : 1.0)'
        elif sop == U.S_SETP_INV:
            body.append('sa = %s;' % sa)
            body.append('pn = sa == 1.0;')
            se = '(pn ? 0.0 : (sa == 0.0 ? 1.0 : sa))'
        elif sop == U.S_SETP_POP:
            body.append('sa = %s - 1.0;' % sa)
            body.append('pn = sa <= 0.0;')
            se = '(pn ? 0.0 : sa)'
        elif sop == U.S_SETP_CLR:
            body.append('pn = false;')
            se = '3.402823466e38'
        elif sop == U.S_SETP_RSTR:
            body.append('sa = %s;' % sa)
            body.append('pn = sa == 0.0;')
            se = '(pn ? 0.0 : sa)'
        elif sop in kill_ops:
            if not self.is_ps:
                raise Unsupported('kill in a vertex shader')
            body.append('sa = (float)(%s %s);' % (sa, kill_ops[sop]))
            body.append('if (sa != 0.0) discard;')
            se = 'sa'
        elif sop == U.S_SQRT:
            se = 'sqrt(%s)' % sa
        elif sop == U.S_SIN:
            se = 'sin(%s)' % sa
        elif sop == U.S_COS:
            se = 'cos(%s)' % sa
        elif sop == U.S_RETAIN_PREV:
            se = None
        else:
            raise Unsupported('scalar opcode %d' % sop)
        if se is not None:
            body.append('pv = %s;' % se)
        if sop in cmp_ops or sop in (U.S_SETP_INV, U.S_SETP_POP, U.S_SETP_CLR, U.S_SETP_RSTR):
            body.append('p0 = pn;')
        sval = 'saturate(pv)' if ins.sclamp else 'pv'
        # writes
        if ins.export:
            tgt = self.export_target(ins.vdst)
            if tgt is None:
                body.append('// export to %d dropped' % ins.vdst)
            else:
                for j in range(4):
                    if isinstance(tgt, tuple):          # packed interpolator: per component target
                        pk = self.interp_pack[tgt[1]][j]
                        if pk is None:
                            continue
                        dst = 'O.%s.%s' % pk
                    else:
                        dst = '%s.%s' % (tgt, SW[j])
                    if (vm >> j) & 1:
                        body.append('%s = vr.%s;' % (dst, SW[j]))
                    elif (sm >> j) & 1:
                        body.append('%s = %s;' % (dst, sval))
                    elif (c1m >> j) & 1:
                        body.append('%s = 1.0;' % dst)
                    elif (c0m >> j) & 1:
                        body.append('%s = 0.0;' % dst)
        else:
            if vm:
                d = self.temp(ins.vdst)
                if ins.vdst_rel:
                    raise Unsupported('aL-relative destination')
                m = ''.join(SW[j] for j in range(4) if (vm >> j) & 1)
                body.append('%s.%s = vr.%s;' % (d, m, m))
            if sm:
                d = self.temp(ins.sdst)
                if ins.sdst_rel:
                    raise Unsupported('aL-relative destination')
                m = ''.join(SW[j] for j in range(4) if (sm >> j) & 1)
                body.append('%s.%s = %s;' % (d, m, sval if len(m) == 1 else '(%s).%s' % (sval, 'x' * len(m))))
        if not body:
            return
        self.block(ins, body)

    def block(self, ins, body):
        if ins.predicated:
            self.emit('if (%sp0) {' % ('' if ins.pred_cond else '!'))
            self.ind += 1
            for b in body:
                self.emit(b)
            self.ind -= 1
            self.emit('}')
        else:
            for b in body:
                self.emit(b)

    def export_target(self, d):
        if self.is_ps:
            if d == U.EXP_PS_DEPTH:
                self.outputs['oDepth'] = ('DEPTH', 'float4')
                return 'O.oDepth'
            if 0 <= d <= 3:
                self.outputs['oC%d' % d] = ('COLOR%d' % d, 'float4')
                return 'O.oC%d' % d
            return None
        if d == U.EXP_VS_POS:
            self.outputs['oPos'] = ('POSITION', 'float4')
            return 'O.oPos'
        if d == U.EXP_VS_PSIZE:
            self.outputs['oPts'] = ('PSIZE', 'float4')
            self.note('point size export')
            return 'O.oPts'
        if d < 16:
            it = self.interp_by_reg.get(d)
            if it is None:
                return None
            if self.interp_pack:
                if d not in self.interp_pack:
                    return None
                for name, sem in self.pack_sems.items():
                    self.outputs[name] = (sem, 'float4')
                return ('PACK', d)
            nm = 'o%d' % d
            self.outputs[nm] = ('%s%d' % (USAGE_SEM[it['usage']], it['index']), 'float4')
            return 'O.' + nm
        return None

    # ------------------------------------------------------------------ fetch
    def fetch(self, ins, slot):
        body = []
        dc = ins.dst_components()
        if ins.op == U.FETCH_VERTEX:
            if ins.mini:
                raise Unsupported('vfetch_mini')
            v = self.ve.get(slot)
            if v is None:
                raise Unsupported('vfetch without vertex element')
            nm = 'v%d' % slot
            self.inputs[slot] = (nm, '%s%d' % (USAGE_SEM[v['usage']], v['index']))
            src = 'I.' + nm
            if (v['usage'], v['index']) in self.vin_decode:      # PC data stored as (x + 1) / 2
                src = '(I.%s * 2.0 - 1.0)' % nm
            d = self.temp(ins.dst)
            for j in range(4):
                c = dc[j]
                if c == 7:
                    continue
                body.append('%s.%s = %s;' % (d, SW[j], ('%s.%s' % (src, SW[c])) if c < 4 else
                                              ('1.0' if c == 5 else '0.0')))
            self.block(ins, body)
            return
        if ins.dst_rel or ins.src_rel:
            raise Unsupported('relative fetch register')
        s = self.temp(ins.src)
        if ins.op == U.FETCH_SET_LOD:
            body.append('lodr = %s.%s;' % (s, SW[ins.src_components(1)[0]]))
            self.block(ins, body)
            return
        if ins.op in (U.FETCH_SET_GRAD_H, U.FETCH_SET_GRAD_V):
            g = 'gh' if ins.op == U.FETCH_SET_GRAD_H else 'gv'
            body.append('%s = float4(%s.%s, 0.0);' % (g, s, ''.join(SW[c] for c in ins.src_components(3))))
            self.block(ins, body)
            return
        if ins.op == U.FETCH_GET_GRADIENTS:
            if not self.is_ps:
                raise Unsupported('getGradients in a vertex shader')
            c = ins.src_components(2)
            body.append('ft = float4(ddx(%s.%s), ddy(%s.%s), ddx(%s.%s), ddy(%s.%s));' %
                        (s, SW[c[0]], s, SW[c[0]], s, SW[c[1]], s, SW[c[1]]))
        elif ins.op == U.FETCH_TEXTURE:
            body.append('ft = %s;' % self.tex_expr(ins, s))
            sn = self.sampler_name(ins.const_index)
            if is_depth_texture(sn):
                if self.rawz:
                    # RAWZ (GeForce 6/7 depth texture) decode, as the PC's 2x variants do
                    body.append('ft = dot(ft.wxy, float3(0.99609375, 0.0038909912109375, '
                                '1.519918441772461e-05)).xxxx;')
                    self.note('RAWZ depth decode')
                if self.pc_depth and is_scene_depth(sn):
                    # the Xbox depth buffer is reversed (1 = near): Xbox depth = 1 - PC depth
                    body.append('ft = 1.0 - ft;')
                    self.note('depth texture reversed (1 - d)')
        else:
            raise Unsupported('fetch op %s' % U.FETCH_NAMES.get(ins.op, ins.op))
        d = self.temp(ins.dst)
        for j in range(4):
            c = dc[j]
            if c == 7:
                continue
            body.append('%s.%s = %s;' % (d, SW[j], ('ft.%s' % SW[c]) if c < 4 else ('1.0' if c == 5 else '0.0')))
        self.block(ins, body)

    def sampler_name(self, fetch_const):
        reg = fetch_const - (0 if self.is_ps else 16)
        for c in self.x.ctab['consts']:
            if c['set'] == 3 and c['reg'] <= reg < c['reg'] + c['count']:
                return c['name']
        return ''

    def tex_expr(self, ins, s):
        reg = ins.const_index - (0 if self.is_ps else 16)
        if reg < 0 or reg > 15 or (not self.is_ps and reg > 3):
            raise Unsupported('texture fetch constant %d' % ins.const_index)
        kind = {U.DIM_1D: '1D', U.DIM_2D: '2D', U.DIM_3D: '3D', U.DIM_CUBE: 'CUBE'}[ins.dim]
        if kind == '1D':
            kind = '2D'
            self.note('1D fetch done as 2D')
        old = self.samplers.get(reg)
        if old and old != kind:
            raise Unsupported('sampler %d used as %s and %s' % (reg, old, kind))
        self.samplers[reg] = kind
        if ins.unnormalized:
            raise Unsupported('unnormalized texture coordinates')
        if any(ins.off):
            raise Unsupported('texel offsets')
        n = {U.DIM_1D: 1, U.DIM_2D: 2, U.DIM_3D: 3, U.DIM_CUBE: 3}[ins.dim]
        comps = ins.src_components(n)
        co = '%s.%s' % (s, ''.join(SW[c] for c in comps))
        if ins.dim == U.DIM_1D:
            co = 'float2(%s, 0.5)' % co
        if ins.dim == U.DIM_CUBE:
            co = 'xe_cubedir(%s)' % co
        sname = 's%d' % reg
        lod = None
        if ins.use_reg_lod:
            lod = 'lodr'
        if ins.lod_bias:
            lod = ('%s + %s' % (lod, flit(ins.lod_bias))) if lod else flit(ins.lod_bias)
        fn = {'2D': 'tex2D', 'CUBE': 'texCUBE', '3D': 'tex3D'}[kind]
        c3 = co if kind != '2D' else '%s, 0.0' % co
        if ins.use_reg_grad:
            if not self.is_ps:
                raise Unsupported('register gradients in a vertex shader')
            if lod:
                self.note('LOD bias with register gradients ignored')
            nc = 2 if kind == '2D' else 3
            return '%sgrad(%s, %s, gh.%s, gv.%s)' % (fn, sname, co, SW[:nc], SW[:nc])
        if not self.is_ps or not ins.use_comp_lod:
            return '%slod(%s, float4(%s, %s))' % (fn, sname, c3, lod or '0.0')
        if lod:
            return '%sbias(%s, float4(%s, %s))' % (fn, sname, c3, lod)
        return '%s(%s, %s)' % (fn, sname, co)

    # ------------------------------------------------------------------ control flow
    def exec_block(self, c):
        for a, ins, ser in self.prog.exec_instrs(c):
            if isinstance(ins, U.Fetch):
                self.fetch(ins, a)
            else:
                self.alu(ins)
            if getattr(self, 'dump_at', None) and self.dump_at[0] == a:     # debugging
                self.emit('O.oDbg = r%d;' % self.dump_at[1])

    def cf_range(self, lo, hi, top):
        """emit CF instructions lo..hi-1; returns True if the range ends the shader unconditionally"""
        cfs = self.prog.cfs
        i = lo
        while i < hi:
            c = cfs[i]
            op = c.op
            if op in U.CF_EXECS:
                if op in (U.CF_COND_EXEC, U.CF_COND_EXEC_END, U.CF_COND_EXEC_PRED_CLEAN,
                          U.CF_COND_EXEC_PRED_CLEAN_END):
                    self.used_bools.add(c.bool_address)
                    self.emit('if (%sb%d) {' % ('' if c.condition else '!', c.bool_address))
                    self.ind += 1
                    self.exec_block(c)
                    if op in U.CF_ENDS:
                        self.emit('return O;')
                    self.ind -= 1
                    self.emit('}')
                elif op in (U.CF_COND_EXEC_PRED, U.CF_COND_EXEC_PRED_END):
                    self.emit('if (%sp0) {' % ('' if c.condition else '!'))
                    self.ind += 1
                    self.exec_block(c)
                    if op in U.CF_ENDS:
                        self.emit('return O;')
                    self.ind -= 1
                    self.emit('}')
                else:
                    self.exec_block(c)
                    if op == U.CF_EXEC_END:
                        if not top:
                            self.emit('return O;')
                        return True
            elif op == U.CF_COND_JMP:
                t = c.address
                if t <= i:
                    raise Unsupported('backward jump')
                if t > hi:
                    raise Unsupported('jump out of a block')
                if c.unconditional:
                    i = t
                    continue
                if c.predicated:
                    cond = '%sp0' % ('!' if c.condition else '')      # executed when the jump is not taken
                else:
                    self.used_bools.add(c.bool_address)
                    cond = '%sb%d' % ('!' if c.condition else '', c.bool_address)
                self.emit('if (%s) {' % cond)
                self.ind += 1
                self.cf_range(i + 1, t, False)
                self.ind -= 1
                self.emit('}')
                i = t
                continue
            elif op in (U.CF_NOP, U.CF_ALLOC, U.CF_MARK_VS_FETCH_DONE):
                pass
            elif op == U.CF_LOOP_START:
                raise Unsupported('loop')
            else:
                raise Unsupported('control flow %s' % U.CF_NAMES[op])
            i += 1
        return False

    # ------------------------------------------------------------------ whole shader
    def translate(self):
        x = self.x
        self.used_bools = set()
        self.ind = 1
        body_start = len(self.lines)
        if self.is_ps:
            for it in x.interps:
                if self.interp_pack:
                    pk = self.interp_pack.get(it['reg'])
                    for j in range(4):
                        if pk and pk[j] is not None:
                            self.emit('%s.%s = I.%s.%s;' % (self.temp(it['reg']), SW[j], pk[j][0], pk[j][1]))
                    continue
                self.emit('%s = I.i%d;' % (self.temp(it['reg']), it['reg']))
            if x.param_gen:
                r = x.param_gen_reg
                self.uses_vpos = True
                self.emit('%s = float4(I.face >= 0.0 ? abs(I.vpos.x) : -abs(I.vpos.x), I.vpos.y, 0.0, 0.0);'
                          % self.temp(r))
        self.cf_range(0, len(self.prog.cfs), True)
        body = self.lines[body_start:]
        self.lines = self.lines[:body_start]
        out = ['// translated from Xbox 360 microcode by tools/dlc/xenos/xlate.py'] + HELPERS.split('\n')
        if self.rel_consts:
            out.append('float4 C[256] : register(c0);')
        for i in sorted(self.used_consts):
            if self.rel_consts:
                out.append('#define c%d C[%d]' % (i, i))
            else:
                if self.is_ps and i > 223:
                    raise Unsupported('pixel shader constant c%d > c223' % i)
                out.append('float4 c%d : register(c%d);' % (i, i))
        for b in sorted(self.used_bools):
            out.append('bool b%d : register(b%d);' % (b, b - (128 if self.is_ps and b >= 128 else 0)))
        for reg, kind in sorted(self.samplers.items()):
            out.append('sampler%s s%d : register(s%d);' % (kind, reg, reg))
        # IO structs
        out.append('struct IN {')
        if self.is_ps:
            if self.interp_pack:
                for name, sem in sorted(self.pack_sems.items()):
                    out.append('    float4 %s : %s;' % (name, sem))
            for it in (x.interps if not self.interp_pack else []):
                out.append('    float4 i%d : %s%d;' % (it['reg'], USAGE_SEM[it['usage']], it['index']))
            if self.uses_vpos:
                out.append('    float2 vpos : VPOS;')
                out.append('    float face : VFACE;')
        else:
            for slot, (nm, sem) in sorted(self.inputs.items()):
                out.append('    float4 %s : %s;' % (nm, sem))
        if len(out) and out[-1] == 'struct IN {':
            out.append('    float4 dummy_ : %s;' % ('TEXCOORD0' if self.is_ps else 'POSITION0'))
        out.append('};')
        out.append('struct OUT {')
        if self.is_ps:
            if not any(k.startswith('oC') for k in self.outputs):
                self.outputs['oC0'] = ('COLOR0', 'float4')
        else:
            if 'oPos' not in self.outputs:
                self.outputs['oPos'] = ('POSITION', 'float4')
        if getattr(self, 'dump_reg', None) is not None or getattr(self, 'dump_at', None):
            self.temps.add(self.dump_reg if getattr(self, 'dump_reg', None) is not None else self.dump_at[1])
            self.outputs['oDbg'] = ('COLOR3' if self.is_ps else 'TEXCOORD15', 'float4')
        for nm, (sem, ty) in sorted(self.outputs.items()):
            if nm == 'oDepth':
                out.append('    float oDepth : DEPTH;')
            elif nm == 'oPts':
                out.append('    float oPts : PSIZE;')
            else:
                out.append('    %s %s : %s;' % (ty, nm, sem))
        out.append('};')
        out.append('OUT main(IN I) {')
        out.append('    OUT O = (OUT)0;')
        for t in sorted(self.temps):
            out.append('    float4 r%d = 0.0;' % t)
        out.append('    float4 vr = 0.0, va = 0.0, vb = 0.0, ft = 0.0, gh = 0.0, gv = 0.0;')
        out.append('    float pv = 0.0, sa = 0.0, lodr = 0.0; bool p0 = false, pn = false; int a0 = 0;')
        body = [l.replace('O.oDepth.x', 'O.oDepth').replace('O.oPts.x', 'O.oPts') for l in body]
        body = [l for l in body if not (('O.oDepth.' in l) or ('O.oPts.' in l))]
        out += body
        if getattr(self, 'dump_reg', None) is not None:      # debugging: final value of a temporary
            out.append('    O.oDbg = r%d;' % self.dump_reg)
        out.append('    return O;')
        out.append('}')
        return '\n'.join(out) + '\n'


def compile_hlsl(src, profile, fxc=None, extra=(), optimize=True):
    """D3DCompiler_41.dll (the compiler the PC shaders were built with) through ctypes; fxc.exe as fallback."""
    if fxc is None and not extra:
        try:
            import d3dcomp
            code, msg = d3dcomp.compile(src, profile, flags=0 if optimize else (1 << 2))
            if code is not None:
                return code
            first_error = msg.strip()[:1000]
        except OSError:
            first_error = None
        try:
            return compile_hlsl(src, profile, fxc=find_fxc(), optimize=optimize)
        except (Unsupported, RuntimeError) as err:
            raise Unsupported('compile failed: %s / %s' % (first_error, err))
    fxc = fxc or find_fxc()
    if not optimize:
        extra = list(extra) + ['/Od']
    d = tempfile.mkdtemp(prefix='xenos_')
    try:
        fi, fo = os.path.join(d, 's.hlsl'), os.path.join(d, 's.bin')
        with open(fi, 'w') as f:
            f.write(src)
        p = subprocess.run([fxc, '/nologo', '/T', profile, '/E', 'main', '/Fo', fo] + list(extra) + [fi],
                           capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(fo):
            raise Unsupported('fxc failed: ' + (p.stderr or p.stdout).strip().replace(fi, 'shader')[:2000])
        return open(fo, 'rb').read()
    finally:
        for fn in os.listdir(d):
            os.remove(os.path.join(d, fn))
        os.rmdir(d)


def pc_ctab(x, creator):
    """The Xbox container CTAB as a PC (little endian) table for vs_3_0 / ps_3_0."""
    ct = dict(x.ctab)
    ct['target'] = 'ps_3_0' if x.is_ps else 'vs_3_0'
    ct['creator'] = creator
    ct['version'] = 0xFFFF0300 if x.is_ps else 0xFFFE0300
    consts = []
    for c in ct['consts']:
        c = dict(c)
        if c.get('default'):
            d = c['default']
            c['default'] = struct.pack('<%dI' % (len(d) // 4), *struct.unpack('>%dI' % (len(d) // 4), d))
        consts.append(c)
    ct['consts'] = consts
    return ct


def _finish(x, code):
    old = sm3.Shader(code).ctab()
    creator = old['creator'] if old else 'Microsoft (R) HLSL Shader Compiler'
    ct = pc_ctab(x, creator)
    if old:
        ct['flags'] = old['flags']
    return sm3.replace_ctab(code, sm3.build_ctab(ct))


def _sem_split(sem):
    import re
    m = re.match(r'([A-Z]+)(\d*)$', sem)
    usage = {v: k for k, v in USAGE_SEM.items()}[m.group(1)]
    return usage, int(m.group(2) or 0)


def self_check(x, code, seeds=(1, 2), L=256, vin_decode=(), depth_flip=True, pack=None, pack_sems=None):
    """Fidelity check of a translation: Xbox microcode (xinterp) vs the bytecode (sm3interp), inputs keyed by
    name/semantic. With interpolator packing the PC side is mapped back to the Xbox interpolators.
    Returns (ok, report of the first failing seed)."""
    import numpy as np
    import xcompare
    for s in seeds:
        xr = xcompare.XRun(x, L=L, seed=s)
        xr.depth_flip = depth_flip
        if vin_decode:
            xr.vin_xform = {e: (lambda v: v * 2 - 1) for e in vin_decode}
        if pack and x.is_ps:
            vals = {n: xcompare.semantic_values(*_sem_split(sem), L, s) for n, sem in pack_sems.items()}
            regs = {}
            for reg, pk in pack.items():
                v = np.zeros((L, 4), np.float32)
                for j, p in enumerate(pk):
                    if p is not None:
                        v[:, j] = vals[p[0]][:, SW.index(p[1])]
                regs[reg] = v
            xr.reg_override = regs
        a = xr.run()
        b = xcompare.PRun(code, L=L, seed=s).run()
        if pack and not x.is_ps:
            sem_of = {n: _sem_split(sem) for n, sem in pack_sems.items()}
            outs, kill = b
            nb = {k: v for k, v in outs.items() if k[0] != 'interp'}
            for it in x.interps:
                pk = pack.get(it['reg'])
                if not pk:
                    continue
                v = np.zeros((L, 4), np.float32)
                w = np.zeros((L, 4), bool)
                for j, p in enumerate(pk):
                    if p is None:
                        continue
                    key = ('interp',) + sem_of[p[0]]
                    if key in outs:
                        v[:, j] = outs[key][0][:, SW.index(p[1])]
                        w[:, j] = outs[key][1][:, SW.index(p[1])]
                nb[('interp', it['usage'], it['index'])] = (v, w)
            b = (nb, kill)
            # only the packed (= used) components count
            oa, ka = a
            for it in x.interps:
                k = ('interp', it['usage'], it['index'])
                pk = pack.get(it['reg'])
                if k in oa and not pk:
                    del oa[k]               # not read by the PS: dropped
                elif k in oa:
                    m = np.array([pk is not None and pk[j] is not None for j in range(4)], bool)
                    oa[k] = (oa[k][0], oa[k][1] & m[None, :])
        rep = xcompare.compare_outputs(a, b)
        if any(r[1] in ('DIFF', 'MISSING') for r in rep):
            return False, rep
    return True, rep


def translate_container(data, verify=False, vin_decode=(), rawz=False, pc_depth=True, interp_pack=None,
                        pack_sems=None):
    """Xbox shader container bytes -> (PC bytecode, notes, hlsl). Raises Unsupported.
    verify: run the fidelity self check; if the optimised code fails it, the unoptimised compile is tried
    (the HLSL optimiser of every D3DCompiler version produced code that disagrees for a few shaders); the
    notes then say which was kept and whether the check passed ('fidelity ok' / 'fidelity FAIL')."""
    x = xshader.XShader(data)
    notes = []
    t = Translator(x, notes)
    t.vin_decode = set(vin_decode)
    t.rawz = rawz
    t.pc_depth = pc_depth
    if interp_pack:
        t.interp_pack, t.pack_sems = interp_pack, pack_sems
        notes.append('interpolators packed into %d registers' % len(pack_sems))
    src = t.translate()
    prof = 'ps_3_0' if x.is_ps else 'vs_3_0'
    code = _finish(x, compile_hlsl(src, prof))
    if verify and rawz and 'RAWZ depth decode' in notes:
        verify = False          # the reference (Xbox semantics) differs on purpose
        notes.append('not self-checked (RAWZ depth decoding)')
    if verify:
        ok, rep = self_check(x, code, vin_decode=vin_decode, depth_flip=pc_depth, pack=interp_pack, pack_sems=pack_sems)
        if not ok:
            try:
                code2 = _finish(x, compile_hlsl(src, prof, optimize=False))
                ok2, rep2 = self_check(x, code2, vin_decode=vin_decode, depth_flip=pc_depth, pack=interp_pack,
                                       pack_sems=pack_sems)
            except Unsupported:
                ok2 = False
            if ok2:
                code, ok = code2, True
                notes.append('compiled without optimisation (optimised code failed the check)')
        notes.append('fidelity ok' if ok else 'fidelity FAIL: %s' % [r for r in rep if r[1] != 'ok'][:3])
    return code, notes, src
