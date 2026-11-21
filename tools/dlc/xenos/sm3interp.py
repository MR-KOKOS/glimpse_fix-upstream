# Vectorised D3D9 shader model 3 bytecode interpreter (numpy; one lane per vertex / pixel) for validation.
# Lanes form 2x2 quads (lane 4k + (0,1,2,3) = (x,y) offsets (0,0),(1,0),(0,1),(1,1)) for dsx/dsy.
#
# run(shader, L, consts (256,L,4), inputs {reg: (L,4)}, misc {0: vPos (L,4), 1: vFace (L,4)}, tex callable,
#     ints (16,4), bools (16,))  ->  (outputs {('o'|'oC'|'oDepth'|'oPos', n): (values, written)}, kill)
import numpy as np
import sm3 as S

F = np.float32


class Unsupported(Exception):
    pass


def quad_ddx(v):
    q = v.reshape((-1, 4) + v.shape[1:])
    d = (q[:, 1] - q[:, 0])
    return np.repeat(d, 4, axis=0).reshape(v.shape)


def quad_ddy(v):
    q = v.reshape((-1, 4) + v.shape[1:])
    d = (q[:, 2] - q[:, 0])
    return np.repeat(d, 4, axis=0).reshape(v.shape)


TRACE = None      # debugging: set to a list to get (index, instruction, temporaries) after each instruction


def run(sh, L, consts, inputs=None, misc=None, tex=None, ints=None, bools=None, max_steps=200000):
    with np.errstate(all='ignore'):
        return Interp(sh, L, consts, inputs, misc, tex, ints, bools).go(max_steps)


class Interp:
    def __init__(self, sh, L, consts, inputs, misc, tex, ints, bools):
        self.sh, self.L = sh, L
        self.C = consts.copy()
        self.I = np.zeros((16, 4), int) if ints is None else np.array(ints, int).copy()
        self.B = np.zeros(16, bool) if bools is None else np.array(bools, bool).copy()
        for ins in sh.instrs:
            if ins.op == 'def':
                self.C[ins.dst.num] = np.array(ins.extra, F)[None, :]
            elif ins.op == 'defi':
                self.I[ins.dst.num] = [int(np.int32(np.uint32(v))) for v in ins.extra]
            elif ins.op == 'defb':
                self.B[ins.dst.num] = bool(ins.extra[0])
        self.R = np.zeros((32, L, 4), F)
        self.V = np.zeros((16, L, 4), F)
        for k, v in (inputs or {}).items():
            self.V[k] = v
        self.misc = misc or {}
        self.O = {}
        self.A0 = np.zeros((L, 4), int)
        self.aL = np.zeros(L, int)
        self.P = np.zeros((L, 4), bool)
        self.tex = tex
        self.kill = np.zeros(L, bool)
        self.samplers = {}
        self.out_dcl = {}
        for ins in sh.instrs:
            if ins.op == 'dcl':
                d = S.dcl_info(ins)
                if d[0] == 's':
                    self.samplers[d[1]] = d[2]

    # ---------------------------------------------------------------- operands
    def reg_value(self, p, comps=None):
        t, n = p.type, p.num
        L = self.L
        if p.relative:
            r = p.rel
            if r is None:
                raise Unsupported('relative without address token')
            if r.type == S.T_LOOP:
                off = self.aL
            elif r.type == S.T_ADDR:
                off = self.A0[:, r.swz[0]]
            else:
                raise Unsupported('relative register type %d' % r.type)
            ix = n + off
        else:
            ix = None
        if t == S.T_TEMP:
            return self.R[n]
        if t == S.T_INPUT:
            if ix is not None:
                return self.V[np.clip(ix, 0, 15), np.arange(L)]
            return self.V[n]
        if t in (S.T_CONST, S.T_CONST2, S.T_CONST3, S.T_CONST4):
            base = {S.T_CONST: 0, S.T_CONST2: 2048, S.T_CONST3: 4096, S.T_CONST4: 6144}[t]
            if ix is not None:
                ok = (ix >= 0) & (ix < 256)
                return np.where(ok[:, None], self.C[np.clip(ix, 0, 255), np.arange(L)], F(0))
            return self.C[n + base]
        if t == S.T_CONSTINT:
            return np.repeat(self.I[n][None, :].astype(F), L, 0)
        if t == S.T_CONSTBOOL:
            return np.full((L, 4), F(self.B[n]))
        if t == S.T_MISC:
            return self.misc.get(n, np.zeros((L, 4), F))
        if t == S.T_LOOP:
            return np.repeat(self.aL[:, None].astype(F), 4, 1)
        if t == S.T_PRED:
            return self.P.astype(F)
        if t == S.T_ADDR:
            return self.A0.astype(F)
        raise Unsupported('source register type %d' % t)

    def src(self, p):
        v = self.reg_value(p)[:, p.swz]
        m = p.srcmod
        if m == 0:
            return v
        if m == 1:
            return -v
        if m == 11:
            return np.abs(v)
        if m == 12:
            return -np.abs(v)
        raise Unsupported('source modifier %s' % S.SRCMOD[m])

    def write(self, d, val, mask_lanes, pred=None):
        val = np.asarray(val, F)
        if val.ndim == 1:
            val = np.repeat(val[:, None], 4, 1)
        if d.mod & 1:
            val = np.clip(val, 0, 1)
        cm = np.array([(d.mask >> j) & 1 for j in range(4)], bool)
        m = mask_lanes[:, None] & cm[None, :]
        if pred is not None:
            m = m & pred
        t = d.type
        if t == S.T_TEMP:
            self.R[d.num] = np.where(m, val, self.R[d.num])
        elif t in (S.T_OUTPUT, S.T_RASTOUT, S.T_ATTROUT, S.T_COLOROUT, S.T_DEPTHOUT):
            if d.relative:
                raise Unsupported('relative output')
            key = (t, d.num)
            if key not in self.O:
                self.O[key] = (np.zeros((self.L, 4), F), np.zeros((self.L, 4), bool))
            v, w = self.O[key]
            self.O[key] = (np.where(m, val, v), w | m)
        elif t == S.T_ADDR:
            r = np.floor(val + F(0.5)).astype(int)     # mova rounds to nearest
            self.A0 = np.where(m, r, self.A0)
        elif t == S.T_PRED:
            self.P = np.where(m, val != 0, self.P)
        else:
            raise Unsupported('destination register type %d' % t)

    # ---------------------------------------------------------------- execution
    def go(self, max_steps):
        sh = self.sh
        ins_list = [i for i in sh.instrs if i.op not in ('dcl', 'def', 'defi', 'defb')]
        n = len(ins_list)
        labels = {}
        for k, ins in enumerate(ins_list):
            if ins.op == 'label':
                labels[ins.srcs[0].num] = k
        # matching structure
        match = {}
        stack = []
        for k, ins in enumerate(ins_list):
            if ins.op in ('if', 'ifc', 'loop', 'rep'):
                stack.append(k)
            elif ins.op == 'else':
                match[stack[-1]] = ('else', k)
                match[k] = ('endif', None)
            elif ins.op in ('endif', 'endloop', 'endrep'):
                s = stack.pop()
                if ins_list[s].op in ('if', 'ifc'):
                    if s in match:
                        e = match[s][1]
                        match[e] = ('endif', k)
                    else:
                        match[s] = ('else', k)
                match[k] = ('start', s)
        L = self.L
        m = np.ones(L, bool)
        cs = []            # control stack
        pc = 0
        steps = 0
        while pc < n:
            steps += 1
            if steps > max_steps:
                raise RuntimeError('step limit')
            ins = ins_list[pc]
            op = ins.op
            pc1 = pc + 1
            if op == 'label':
                # reached a subroutine body by falling through: main program ended
                if not cs or cs[-1][0] != 'call':
                    break
            if op in ('if', 'ifc'):
                if op == 'if':
                    c = self.src(ins.srcs[0])[:, 0] != 0
                else:
                    a, b = self.src(ins.srcs[0])[:, 0], self.src(ins.srcs[1])[:, 0]
                    c = _cmp(ins.ctrl, a, b)
                cs.append(['if', m.copy(), c])
                m = m & c
            elif op == 'else':
                e = cs[-1]
                m = e[1] & ~e[2]
            elif op == 'endif':
                m = cs.pop()[1]
            elif op in ('loop', 'rep'):
                iv = self.I[ins.srcs[-1].num]
                cnt = int(iv[0])
                ent = ['loop', m.copy(), pc, 0, cnt, int(iv[2]) if op == 'loop' else 0, self.aL.copy()]
                if op == 'loop':
                    self.aL = np.full(L, int(iv[1]))
                cs.append(ent)
                if cnt <= 0 or not m.any():
                    pc1 = match[pc][1] if False else self._end_of(ins_list, pc, match)
                    m = cs.pop()[1]
                    self.aL = ent[6]
                    pc = pc1 + 1
                    continue
            elif op in ('endloop', 'endrep'):
                e = cs[-1]
                e[3] += 1
                self.aL = self.aL + e[5]
                if e[3] < e[4] and m.any():
                    pc1 = e[2] + 1
                else:
                    cs.pop()
                    m = e[1]
                    self.aL = e[6]
            elif op in ('break', 'breakc', 'breakp'):
                if op == 'break':
                    c = m.copy()
                elif op == 'breakc':
                    a, b = self.src(ins.srcs[0])[:, 0], self.src(ins.srcs[1])[:, 0]
                    c = m & _cmp(ins.ctrl, a, b)
                else:
                    c = m & (self.src(ins.srcs[0])[:, 0] != 0)
                if ins.pred is not None:
                    c = c & (self.src(ins.pred)[:, 0] != 0)
                # remove lanes from everything up to and including the innermost loop's continue mask
                for e in reversed(cs):
                    if e[0] == 'loop':
                        break
                    e[1] = e[1] & ~c
                m = m & ~c
            elif op in ('call', 'callnz'):
                if op == 'callnz':
                    c = m & (self.src(ins.srcs[1])[:, 0] != 0)
                else:
                    c = m.copy()
                cs.append(['call', m.copy(), pc1])
                m = c
                pc1 = labels[ins.srcs[0].num] + 1
            elif op == 'ret':
                if cs and cs[-1][0] == 'call':
                    e = cs.pop()
                    m = e[1]
                    pc1 = e[2]
                else:
                    break
            elif op == 'texkill':
                v = self.reg_value(ins.dst)
                cm = np.array([(ins.dst.mask >> j) & 1 for j in range(4)], bool)
                k = ((v < 0) & cm[None, :]).any(1)
                self.kill |= m & k
            elif op == 'nop':
                pass
            else:
                if m.any():
                    self.alu(ins, m)
            if TRACE is not None:
                TRACE.append((pc, ins, self.R.copy()))
            pc = pc1
        out = {}
        for (t, num), v in self.O.items():
            out[(t, num)] = v
        return out, self.kill

    def _end_of(self, ins_list, pc, match):
        depth = 0
        for k in range(pc, len(ins_list)):
            o = ins_list[k].op
            if o in ('loop', 'rep'):
                depth += 1
            elif o in ('endloop', 'endrep'):
                depth -= 1
                if depth == 0:
                    return k
        raise RuntimeError('unmatched loop')

    def alu(self, ins, m):
        op = ins.op
        d = ins.dst
        pred = None
        if ins.pred is not None:
            pv = self.reg_value(ins.pred)[:, ins.pred.swz] != 0
            if ins.pred.srcmod == 13:
                pv = ~pv
            pred = pv
        s = [self.src(p) for p in ins.srcs] if op not in ('tex', 'texldl', 'texldd') else None
        L = self.L
        if op == 'mov' or op == 'mova':
            r = s[0]
        elif op == 'add':
            r = s[0] + s[1]
        elif op == 'sub':
            r = s[0] - s[1]
        elif op == 'mul':
            r = s[0] * s[1]
        elif op == 'mad':
            r = s[0] * s[1] + s[2]
        elif op == 'rcp':
            r = np.repeat((F(1) / s[0][:, 3 if False else 0])[:, None], 4, 1)
        elif op == 'rsq':
            r = np.repeat((F(1) / np.sqrt(np.abs(s[0][:, 0])))[:, None], 4, 1)
        elif op == 'dp3':
            r = np.repeat((s[0][:, :3] * s[1][:, :3]).sum(1, keepdims=True), 4, 1)
        elif op == 'dp4':
            r = np.repeat((s[0] * s[1]).sum(1, keepdims=True), 4, 1)
        elif op == 'dp2add':
            r = np.repeat((s[0][:, :2] * s[1][:, :2]).sum(1, keepdims=True) + s[2][:, :1], 4, 1)
        elif op == 'min':
            r = np.minimum(s[0], s[1])
        elif op == 'max':
            r = np.maximum(s[0], s[1])
        elif op == 'slt':
            r = (s[0] < s[1]).astype(F)
        elif op == 'sge':
            r = (s[0] >= s[1]).astype(F)
        elif op == 'exp':
            r = np.repeat(np.exp2(s[0][:, :1]), 4, 1)
        elif op == 'log':
            r = np.repeat(np.log2(np.abs(s[0][:, :1])), 4, 1)
        elif op == 'pow':
            r = np.repeat(np.power(np.abs(s[0][:, :1]), s[1][:, :1]), 4, 1)
        elif op == 'frc':
            r = s[0] - np.floor(s[0])
        elif op == 'abs':
            r = np.abs(s[0])
        elif op == 'sgn':
            r = np.sign(s[0])
        elif op == 'nrm':
            l2 = (s[0][:, :3] ** 2).sum(1, keepdims=True)
            r = s[0] * (F(1) / np.sqrt(l2))
        elif op == 'crs':
            a, b = s[0], s[1]
            r = np.zeros((L, 4), F)
            r[:, :3] = np.cross(a[:, :3], b[:, :3])
        elif op == 'sincos':
            r = np.zeros((L, 4), F)
            r[:, 0] = np.cos(s[0][:, 3 if ins.srcs[0].swz[0] == 3 and False else 0])
            r[:, 1] = np.sin(s[0][:, 0])
        elif op == 'lrp':
            r = s[0] * (s[1] - s[2]) + s[2]
        elif op == 'cmp':
            r = np.where(s[0] >= 0, s[1], s[2])
        elif op == 'cnd':
            r = np.where(s[0] > 0.5, s[1], s[2])
        elif op == 'lit':
            a = s[0]
            r = np.zeros((L, 4), F)
            r[:, 0] = 1
            r[:, 3] = 1
            r[:, 1] = np.maximum(a[:, 0], 0)
            p = np.clip(a[:, 3], -128, 128)
            r[:, 2] = np.where(a[:, 0] > 0, np.power(np.maximum(a[:, 1], 0), p), 0)
        elif op == 'dst':
            a, b = s[0], s[1]
            r = np.stack([np.ones(L, F), a[:, 1] * b[:, 1], a[:, 2], b[:, 3]], 1)
        elif op in ('m4x4', 'm4x3', 'm3x4', 'm3x3', 'm3x2'):
            rows = {'m4x4': 4, 'm4x3': 3, 'm3x4': 4, 'm3x3': 3, 'm3x2': 2}[op]
            n = 4 if op[1] == '4' else 3
            r = np.zeros((L, 4), F)
            p = ins.srcs[1]
            for k in range(rows):
                q = S.Param(p.tok, False, p.rel)
                q.num = p.num + k
                b = self.src(q)
                r[:, k] = (s[0][:, :n] * b[:, :n]).sum(1)
        elif op == 'dsx':
            r = quad_ddx(s[0])
        elif op == 'dsy':
            r = quad_ddy(s[0])
        elif op == 'setp':
            r = _cmp(ins.ctrl, s[0], s[1]).astype(F)
        elif op in ('tex', 'texldl', 'texldd'):
            c = self.src(ins.srcs[0])
            samp = ins.srcs[1].num
            kind = self.samplers.get(samp, '2d')
            lod = None
            mode = 'tex'
            if op == 'texldl':
                lod = c[:, 3]
            elif op == 'tex':
                ctl = ins.ctrl & 3
                if ctl == 1:
                    c = c / c[:, 3:4]
                elif ctl == 2:
                    lod = c[:, 3]          # bias (implicit LOD ignored by the procedural texture)
            r = self.tex(samp, kind, c, lod)
            sw = ins.srcs[1].swz
            r = r[:, sw]
        else:
            raise Unsupported('opcode ' + op)
        self.write(d, r, m, pred)


def _cmp(ctrl, a, b):
    return {1: a > b, 2: a == b, 3: a >= b, 4: a < b, 5: a != b, 6: a <= b}[ctrl]
