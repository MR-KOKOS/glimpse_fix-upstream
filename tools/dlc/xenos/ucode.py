# Xenos (Xbox 360 GPU) shader microcode: decoding and disassembly.
#
# The instruction bit layouts, opcode tables and operand rules are ported from the rexglue SDK
# (include/rex/graphics/format/ucode.h, src/graphics/format/ucode.cpp, src/graphics/pipeline/shader/
# translator.cpp), which is derived from Xenia:
#   Copyright (c) 2026, Tom Clay <tomc@tctechstuff.com>
#   Portions Copyright (c) 2022, Ben Vanik and Xenia project contributors (https://xenia.jp)
#   Parts from freedreno, Copyright (c) 2012 Rob Clark <robdclark@gmail.com>
#   BSD 3-clause licence, see LICENSE-rexglue.txt next to this file.
#
# Microcode words are big endian in the Xbox files; the bit fields below are LSB-first within each u32
# (as in ucode.h after the words are read in host order).
import struct

# ------------------------------------------------------------------------------------- control flow
CF_NOP, CF_EXEC, CF_EXEC_END, CF_COND_EXEC, CF_COND_EXEC_END, CF_COND_EXEC_PRED, CF_COND_EXEC_PRED_END, \
    CF_LOOP_START, CF_LOOP_END, CF_COND_CALL, CF_RETURN, CF_COND_JMP, CF_ALLOC, CF_COND_EXEC_PRED_CLEAN, \
    CF_COND_EXEC_PRED_CLEAN_END, CF_MARK_VS_FETCH_DONE = range(16)
CF_NAMES = ['nop', 'exec', 'exece', 'cexec', 'cexece', 'pexec', 'pexece', 'loop', 'endloop', 'ccall', 'ret',
            'cjmp', 'alloc', 'cexec_pc', 'cexece_pc', 'vfetch_done']
CF_EXECS = (CF_EXEC, CF_EXEC_END, CF_COND_EXEC, CF_COND_EXEC_END, CF_COND_EXEC_PRED, CF_COND_EXEC_PRED_END,
            CF_COND_EXEC_PRED_CLEAN, CF_COND_EXEC_PRED_CLEAN_END)
CF_ENDS = (CF_EXEC_END, CF_COND_EXEC_END, CF_COND_EXEC_PRED_END, CF_COND_EXEC_PRED_CLEAN_END)


def bits(v, lo, n, signed=False):
    x = (v >> lo) & ((1 << n) - 1)
    if signed and x >> (n - 1):
        x -= 1 << n
    return x


class CF:
    """One control flow instruction (48 bits: d0 = 32 bits, d1 = 16 bits)."""

    def __init__(self, d0, d1, index):
        self.index = index
        self.d0, self.d1 = d0, d1
        op = self.op = bits(d1, 12, 4)
        self.addr_mode = bits(d1, 11, 1)        # 1 = absolute (a0), 0 = relative (aL)
        if op in CF_EXECS:
            self.address = bits(d0, 0, 12)
            self.count = bits(d0, 12, 3)
            self.yield_ = bits(d0, 15, 1)
            self.sequence = bits(d0, 16, 12)
            if op in (CF_COND_EXEC, CF_COND_EXEC_END, CF_COND_EXEC_PRED_CLEAN, CF_COND_EXEC_PRED_CLEAN_END):
                self.bool_address = bits(d1, 2, 8)
                self.condition = bits(d1, 10, 1)
            elif op in (CF_COND_EXEC_PRED, CF_COND_EXEC_PRED_END):
                self.pred_clean = bits(d1, 9, 1)
                self.condition = bits(d1, 10, 1)
            else:
                self.pred_clean = bits(d1, 9, 1)
        elif op == CF_LOOP_START:
            self.address = bits(d0, 0, 13)
            self.is_repeat = bits(d0, 13, 1)
            self.loop_id = bits(d0, 16, 5)
        elif op == CF_LOOP_END:
            self.address = bits(d0, 0, 13)
            self.loop_id = bits(d0, 16, 5)
            self.pred_break = bits(d0, 21, 1)
            self.condition = bits(d1, 10, 1)
        elif op in (CF_COND_CALL, CF_COND_JMP):
            self.address = bits(d0, 0, 13)
            self.unconditional = bits(d0, 13, 1)
            self.predicated = bits(d0, 14, 1)
            self.bool_address = bits(d1, 2, 8)
            self.condition = bits(d1, 10, 1)
        elif op == CF_ALLOC:
            self.size = bits(d0, 0, 3)
            self.alloc_type = bits(d1, 9, 2)    # 1 position, 2 interpolators / colors, 3 memory

    def __repr__(self):
        s = '%2d %s' % (self.index, CF_NAMES[self.op])
        op = self.op
        if op in CF_EXECS:
            if op in (CF_COND_EXEC, CF_COND_EXEC_END, CF_COND_EXEC_PRED_CLEAN, CF_COND_EXEC_PRED_CLEAN_END):
                s += ' %sb%d' % ('' if self.condition else '!', self.bool_address)
            elif op in (CF_COND_EXEC_PRED, CF_COND_EXEC_PRED_END):
                s += ' %sp0' % ('' if self.condition else '!')
            s += ' addr=%d cnt=%d seq=%s' % (self.address, self.count, ''.join(
                'F' if (self.sequence >> (2 * i)) & 1 else 'A' for i in range(self.count)))
        elif op == CF_LOOP_START:
            s += ' i%d skip=%d%s' % (self.loop_id, self.address, ' repeat' if self.is_repeat else '')
        elif op == CF_LOOP_END:
            s += ' i%d body=%d%s' % (self.loop_id, self.address,
                                     (' break %sp0' % ('!' if not self.condition else '')) if self.pred_break else '')
        elif op in (CF_COND_CALL, CF_COND_JMP):
            c = '' if self.unconditional else (('%sp0' if self.predicated else '%%sb%d' % self.bool_address)
                                               % ('' if self.condition else '!'))
            s += ' %s -> %d' % (c, self.address)
        elif op == CF_ALLOC:
            s += ' %s %d' % ({1: 'position', 2: 'interp/colors', 3: 'memory'}.get(self.alloc_type, '?'), self.size)
        return s


def parse_cf(words):
    """Control flow instructions from the start of the microcode, up to the first instruction slot used by
    an exec (the CF block ends where the first ALU/fetch instruction starts)."""
    cfs = []
    limit = len(words) // 3
    i = 0
    while i // 2 < limit:
        w = words[3 * (i >> 1): 3 * (i >> 1) + 3]
        if i & 1:
            d0, d1 = (w[1] >> 16) | ((w[2] << 16) & 0xFFFFFFFF), w[2] >> 16
        else:
            d0, d1 = w[0], w[1] & 0xFFFF
        c = CF(d0, d1, i)
        cfs.append(c)
        if c.op in CF_EXECS and c.count:
            limit = min(limit, c.address)
        i += 1
        if (i // 2) >= limit and not (i & 1):
            break
    # trailing nops after the end are padding
    return cfs


# ----------------------------------------------------------------------------------------- fetch
FETCH_VERTEX, FETCH_TEXTURE = 0, 1
FETCH_GET_BORDER_FRAC, FETCH_GET_COMP_LOD, FETCH_GET_GRADIENTS, FETCH_GET_WEIGHTS = 16, 17, 18, 19
FETCH_SET_LOD, FETCH_SET_GRAD_H, FETCH_SET_GRAD_V = 24, 25, 26
FETCH_NAMES = {0: 'vfetch', 1: 'tfetch', 16: 'getBCF', 17: 'getCompTexLOD', 18: 'getGradients',
               19: 'getWeights', 24: 'setTexLOD', 25: 'setGradientH', 26: 'setGradientV'}
DIM_1D, DIM_2D, DIM_3D, DIM_CUBE = range(4)


class Fetch:
    def __init__(self, w):
        w0, w1, w2 = w
        self.words = w
        self.op = bits(w0, 0, 5)
        self.src = bits(w0, 5, 6)
        self.src_rel = bits(w0, 11, 1)
        self.dst = bits(w0, 12, 6)
        self.dst_rel = bits(w0, 18, 1)
        self.const_index = bits(w0, 20, 5)
        self.dst_swiz = bits(w1, 0, 12)
        self.predicated = bits(w1, 31, 1)
        self.pred_cond = bits(w2, 31, 1)
        if self.op == FETCH_VERTEX:
            self.const_sel = bits(w0, 25, 2)
            self.fetch_const = self.const_index * 3 + self.const_sel
            self.prefetch = bits(w0, 27, 3)
            self.src_swiz = bits(w0, 30, 2)
            self.signed = bits(w1, 12, 1)
            self.normalized = bits(w1, 13, 1) == 0
            self.signed_rf = bits(w1, 14, 1)
            self.index_rounded = bits(w1, 15, 1)
            self.format = bits(w1, 16, 6)
            self.exp_adjust = bits(w1, 24, 6, True)
            self.mini = bits(w1, 30, 1)
            self.stride = bits(w2, 0, 8)
            self.offset = bits(w2, 8, 23, True)
        else:
            self.fetch_valid_only = bits(w0, 19, 1)
            self.unnormalized = bits(w0, 25, 1)
            self.src_swiz = bits(w0, 26, 6)
            self.mag_filter = bits(w1, 12, 2)
            self.min_filter = bits(w1, 14, 2)
            self.mip_filter = bits(w1, 16, 2)
            self.aniso = bits(w1, 18, 3)
            self.vol_mag = bits(w1, 24, 2)
            self.vol_min = bits(w1, 26, 2)
            self.use_comp_lod = bits(w1, 28, 1)
            self.use_reg_lod = bits(w1, 29, 1)
            self.use_reg_grad = bits(w2, 0, 1)
            self.sample_loc = bits(w2, 1, 1)
            self.lod_bias = bits(w2, 2, 7, True) / 16.0
            self.dim = bits(w2, 14, 2)
            self.off = (bits(w2, 16, 5, True) * 0.5, bits(w2, 21, 5, True) * 0.5, bits(w2, 26, 5, True) * 0.5)

    def dst_components(self):
        """per destination component: 0..3 = fetched x..w, 4 = 0, 5 = 1, 6 = invalid (0), 7 = keep"""
        return [(self.dst_swiz >> (3 * i)) & 7 for i in range(4)]

    def src_components(self, n):
        if self.op == FETCH_VERTEX:
            return [self.src_swiz]
        return [(self.src_swiz >> (2 * i)) & 3 for i in range(n)]

    def __repr__(self):
        dc = ''.join('xyzw01?_'[c] for c in self.dst_components())
        p = ('(%sp0) ' % ('' if self.pred_cond else '!')) if self.predicated else ''
        if self.op == FETCH_VERTEX:
            return '%s%s%s r%d.%s, r%d.%s, vf%d fmt=%d off=%d stride=%d%s%s%s' % (
                p, 'vfetch' + ('_mini' if self.mini else '_full'), '', self.dst, dc, self.src, 'xyzw'[self.src_swiz],
                self.fetch_const, self.format, self.offset, self.stride, ' signed' if self.signed else '',
                '' if self.normalized else ' int', (' exp%+d' % self.exp_adjust) if self.exp_adjust else '')
        n = {DIM_1D: 1, DIM_2D: 2, DIM_3D: 3, DIM_CUBE: 3}[self.dim]
        sc = ''.join('xyzw'[c] for c in self.src_components(3 if self.op in (FETCH_SET_GRAD_H, FETCH_SET_GRAD_V)
                                                             else 1 if self.op == FETCH_SET_LOD else n))
        extra = []
        if self.op in (FETCH_TEXTURE, FETCH_GET_WEIGHTS, FETCH_GET_COMP_LOD, FETCH_GET_BORDER_FRAC):
            extra.append(['1D', '2D', '3D', 'Cube'][self.dim])
            if self.use_reg_lod:
                extra.append('RegLOD')
            if not self.use_comp_lod:
                extra.append('noCompLOD')
            if self.use_reg_grad:
                extra.append('RegGrad')
            if self.lod_bias:
                extra.append('bias=%g' % self.lod_bias)
            if any(self.off):
                extra.append('off=%g,%g,%g' % self.off)
            if self.unnormalized:
                extra.append('unnorm')
            f = [self.mag_filter, self.min_filter, self.mip_filter]
            if f != [3, 3, 3]:
                extra.append('filt=%s' % f)
            if self.aniso != 7:
                extra.append('aniso=%d' % self.aniso)
        return '%s%s r%d.%s, r%d.%s, tf%d %s' % (p, FETCH_NAMES.get(self.op, 'fetch%d' % self.op), self.dst, dc,
                                                 self.src, sc, self.const_index, ' '.join(extra))


# -------------------------------------------------------------------------------------------- ALU
VEC_NAMES = ['add', 'mul', 'max', 'min', 'seq', 'sgt', 'sge', 'sne', 'frc', 'trunc', 'floor', 'mad', 'cndeq',
             'cndge', 'cndgt', 'dp4', 'dp3', 'dp2add', 'cube', 'max4', 'setp_eq_push', 'setp_ne_push',
             'setp_gt_push', 'setp_ge_push', 'kill_eq', 'kill_gt', 'kill_ge', 'kill_ne', 'dst', 'maxa',
             'op30', 'op31']
# operand components used per vector opcode (0 = operand absent)
VEC_OPERANDS = [(15, 15), (15, 15), (15, 15), (15, 15), (15, 15), (15, 15), (15, 15), (15, 15), (15,), (15,),
                (15,), (15, 15, 15), (15, 15, 15), (15, 15, 15), (15, 15, 15), (15, 15), (7, 7), (3, 3, 1),
                (15, 15), (15,), (9, 9), (9, 9), (9, 9), (9, 9), (15, 15), (15, 15), (15, 15), (15, 15),
                (6, 10), (15, 15), (), ()]
V_ADD, V_MUL, V_MAX, V_MIN, V_SEQ, V_SGT, V_SGE, V_SNE, V_FRC, V_TRUNC, V_FLOOR, V_MAD, V_CNDEQ, V_CNDGE, \
    V_CNDGT, V_DP4, V_DP3, V_DP2ADD, V_CUBE, V_MAX4, V_SETP_EQ_PUSH, V_SETP_NE_PUSH, V_SETP_GT_PUSH, \
    V_SETP_GE_PUSH, V_KILL_EQ, V_KILL_GT, V_KILL_GE, V_KILL_NE, V_DST, V_MAXA = range(30)

# (name, operand count, single operand uses two components)
SCL_INFO = [('adds', 1, 1), ('adds_prev', 1, 0), ('muls', 1, 1), ('muls_prev', 1, 0), ('muls_prev2', 1, 1),
            ('maxs', 1, 1), ('mins', 1, 1), ('seqs', 1, 0), ('sgts', 1, 0), ('sges', 1, 0), ('snes', 1, 0),
            ('frcs', 1, 0), ('truncs', 1, 0), ('floors', 1, 0), ('exp', 1, 0), ('logc', 1, 0), ('log', 1, 0),
            ('rcpc', 1, 0), ('rcpf', 1, 0), ('rcp', 1, 0), ('rsqc', 1, 0), ('rsqf', 1, 0), ('rsq', 1, 0),
            ('maxas', 1, 1), ('maxasf', 1, 1), ('subs', 1, 1), ('subs_prev', 1, 0), ('setp_eq', 1, 0),
            ('setp_ne', 1, 0), ('setp_gt', 1, 0), ('setp_ge', 1, 0), ('setp_inv', 1, 0), ('setp_pop', 1, 0),
            ('setp_clr', 0, 0), ('setp_rstr', 1, 0), ('kills_eq', 1, 0), ('kills_gt', 1, 0), ('kills_ge', 1, 0),
            ('kills_ne', 1, 0), ('kills_one', 1, 0), ('sqrt', 1, 0), ('op41', 0, 0), ('mulsc', 2, 0),
            ('mulsc', 2, 0), ('addsc', 2, 0), ('addsc', 2, 0), ('subsc', 2, 0), ('subsc', 2, 0), ('sin', 1, 0),
            ('cos', 1, 0), ('retain_prev', 0, 0)] + [('op%d' % i, 0, 0) for i in range(51, 64)]
S_ADDS, S_ADDS_PREV, S_MULS, S_MULS_PREV, S_MULS_PREV2, S_MAXS, S_MINS, S_SEQS, S_SGTS, S_SGES, S_SNES, S_FRCS, \
    S_TRUNCS, S_FLOORS, S_EXP, S_LOGC, S_LOG, S_RCPC, S_RCPF, S_RCP, S_RSQC, S_RSQF, S_RSQ, S_MAXAS, S_MAXASF, \
    S_SUBS, S_SUBS_PREV, S_SETP_EQ, S_SETP_NE, S_SETP_GT, S_SETP_GE, S_SETP_INV, S_SETP_POP, S_SETP_CLR, \
    S_SETP_RSTR, S_KILLS_EQ, S_KILLS_GT, S_KILLS_GE, S_KILLS_NE, S_KILLS_ONE, S_SQRT, S_OP41, S_MULSC0, \
    S_MULSC1, S_ADDSC0, S_ADDSC1, S_SUBSC0, S_SUBSC1, S_SIN, S_COS, S_RETAIN_PREV = range(51)

# export registers
EXP_VS_POS, EXP_VS_PSIZE, EXP_PS_DEPTH, EXP_ADDR = 62, 63, 61, 32


class Operand:
    """source operand: kind 'r' (temp) or 'c' (float constant); comps = absolute component indices;
    rel: None, 'aL' or 'a0'"""

    def __init__(self, kind, index, rel, comps, neg, abs_):
        self.kind, self.index, self.rel, self.comps, self.neg, self.abs = kind, index, rel, comps, neg, abs_

    def __repr__(self):
        r = '%s%d' % (self.kind, self.index) if not self.rel else '%s[%d+%s]' % (self.kind, self.index, self.rel)
        if self.abs:
            r = '|%s|' % r
        return ('-' if self.neg else '') + r + '.' + ''.join('xyzw'[c] for c in self.comps)


class Alu:
    def __init__(self, w):
        w0, w1, w2 = w
        self.words = w
        self.vdst = bits(w0, 0, 6)
        self.vdst_rel = bits(w0, 6, 1)
        self.abs_constants = bits(w0, 7, 1)
        self.sdst = bits(w0, 8, 6)
        self.sdst_rel = bits(w0, 14, 1)
        self.export = bits(w0, 15, 1)
        self.vmask = bits(w0, 16, 4)
        self.smask = bits(w0, 20, 4)
        self.vclamp = bits(w0, 24, 1)
        self.sclamp = bits(w0, 25, 1)
        self.sop = bits(w0, 26, 6)
        self.swiz = [bits(w1, 16, 8), bits(w1, 8, 8), bits(w1, 0, 8)]     # src1, src2, src3
        self.neg = [bits(w1, 26, 1), bits(w1, 25, 1), bits(w1, 24, 1)]
        self.pred_cond = bits(w1, 27, 1)
        self.predicated = bits(w1, 28, 1)
        self.const_a0 = bits(w1, 29, 1)       # constant relative addressing uses a0 (else aL)
        self.const1_rel = bits(w1, 30, 1)
        self.const0_rel = bits(w1, 31, 1)
        self.reg = [bits(w2, 16, 8), bits(w2, 8, 8), bits(w2, 0, 8)]
        self.vop = bits(w2, 24, 5)
        self.sel = [bits(w2, 31, 1), bits(w2, 30, 1), bits(w2, 29, 1)]    # 1 = temp register

    # write masks (ucode.h GetVectorOpResultWriteMask etc.)
    def vec_result_mask(self):
        return self.vmask & ~self.smask if self.export else self.vmask

    def scl_result_mask(self):
        return self.smask & ~self.vmask if self.export else self.smask

    def const0_mask(self):
        return (15 & ~(self.vmask | self.smask)) if (self.export and self.sdst_rel) else 0

    def const1_mask(self):
        return (self.vmask & self.smask) if self.export else 0

    def const_is_addressed(self, i):     # i = 0..2 (src1..src3)
        if i == 0:
            return self.const0_rel
        if i == 1:
            return self.const0_rel if self.sel[0] else self.const1_rel
        return self.const0_rel if (self.sel[0] and self.sel[1]) else self.const1_rel

    def operand(self, i, ncomp):
        """i = 0..2; ncomp 4 (vector), 1 (scalar a = W), 2 (scalar a, b = W, X)"""
        reg, sw = self.reg[i], self.swiz[i]
        if ncomp == 4:
            comps = [((sw >> (2 * j)) + j) & 3 for j in range(4)]
        elif ncomp == 1:
            comps = [((sw >> 6) + 3) & 3]
        else:
            comps = [((sw >> 6) + 3) & 3, (sw + 0) & 3]
        if self.sel[i]:
            return Operand('r', reg & 0x3F, 'aL' if reg & 0x40 else None, comps, self.neg[i], bool(reg & 0x80))
        rel = None
        if self.const_is_addressed(i):
            rel = 'a0' if self.const_a0 else 'aL'
        return Operand('c', reg, rel, comps, self.neg[i], bool(self.abs_constants))

    def vec_operands(self):
        return [self.operand(i, 4) for i in range(len(VEC_OPERANDS[self.vop]))]

    def scl_operands(self):
        name, n, two = SCL_INFO[self.sop]
        if n == 0:
            return []
        if n == 1:
            return [self.operand(2, 2 if two else 1)]
        # mulsc/addsc/subsc: constant src3.W and temp register (built from opcode bit, src3_sel, swizzle bits) .X
        sw = self.swiz[2]
        rel = None
        if self.const_is_addressed(2):
            rel = 'a0' if self.const_a0 else 'aL'
        c = Operand('c', self.reg[2], rel, [((sw >> 6) + 3) & 3], self.neg[2], bool(self.abs_constants))
        treg = (self.sop & 1) | (self.sel[2] << 1) | (sw & 0x3C)
        t = Operand('r', treg, None, [sw & 3], self.neg[2], bool(self.abs_constants))
        return [c, t]

    def vec_changes_state(self):
        return self.vop in (V_SETP_EQ_PUSH, V_SETP_NE_PUSH, V_SETP_GT_PUSH, V_SETP_GE_PUSH, V_KILL_EQ, V_KILL_GT,
                            V_KILL_GE, V_KILL_NE, V_MAXA)

    def vec_is_nop(self):
        """vector op writes nothing and changes no state"""
        return not (self.vec_result_mask() or self.const0_mask() or self.const1_mask()) and \
            not self.vec_changes_state()

    def __repr__(self):
        p = ('(%sp0) ' % ('' if self.pred_cond else '!')) if self.predicated else ''
        if self.export:
            d = 'o%d' % self.vdst
            if self.vdst == EXP_VS_POS:
                d = 'oPos'
            elif self.vdst == 63:
                d = 'oPts'
        else:
            d = 'r%d' % self.vdst if not self.vdst_rel else 'r[%d+aL]' % self.vdst
        vm = self.vec_result_mask() | self.const0_mask() | self.const1_mask()
        m = ''.join('xyzw'[j] if (self.vec_result_mask() >> j) & 1 else
                    '0' if (self.const0_mask() >> j) & 1 else '1' if (self.const1_mask() >> j) & 1 else '_'
                    for j in range(4))
        s = '%s%s%s %s.%s, %s' % (p, VEC_NAMES[self.vop], '_sat' if self.vclamp else '', d, m,
                                  ', '.join(map(repr, self.vec_operands())))
        sd = ('o%d' % self.vdst) if self.export else ('r%d' % self.sdst if not self.sdst_rel else
                                                      'r[%d+aL]' % self.sdst)
        sm = ''.join('xyzw'[j] if (self.scl_result_mask() >> j) & 1 else '_' for j in range(4))
        s += '  +  %s%s %s.%s, %s' % (SCL_INFO[self.sop][0], '_sat' if self.sclamp else '', sd, sm,
                                     ', '.join(map(repr, self.scl_operands())))
        return s


class Program:
    """Decoded microcode: cfs (list of CF) and instruction slots (decoded lazily per exec)."""

    def __init__(self, code_bytes):
        if len(code_bytes) % 4:
            raise ValueError('microcode size')
        self.words = list(struct.unpack('>%dI' % (len(code_bytes) // 4), code_bytes))
        self.cfs = parse_cf(self.words)
        self.slots = {}
        for c in self.cfs:
            if c.op in CF_EXECS:
                for k in range(c.count):
                    a = c.address + k
                    w = self.words[3 * a: 3 * a + 3]
                    if len(w) != 3:
                        raise ValueError('exec beyond microcode')
                    self.slots[a] = Fetch(w) if (c.sequence >> (2 * k)) & 1 else Alu(w)

    def exec_instrs(self, c):
        return [(c.address + k, self.slots[c.address + k], (c.sequence >> (2 * k + 1)) & 1) for k in range(c.count)]

    def disasm(self):
        out = []
        for c in self.cfs:
            out.append(repr(c))
            if c.op in CF_EXECS:
                for a, ins, ser in self.exec_instrs(c):
                    out.append('      %3d %s' % (a, ins))
        return '\n'.join(out)
