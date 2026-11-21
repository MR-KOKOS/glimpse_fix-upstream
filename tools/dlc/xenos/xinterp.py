# Vectorised Xenos microcode interpreter (numpy, one lane per vertex / pixel) for validation.
#
# Semantics follow the rexglue / Xenia interpreter (src/graphics/pipeline/shader/interpreter.cpp, BSD
# licence, see ucode.py and LICENSE-rexglue.txt): legacy multiply (0 * x = +0), max/min via >= / <, the
# previous-scalar register, predicate, a0, loops with aL. Control flow is run per lane (each lane has its own
# CF program counter), so predicated jumps and loops diverge correctly.
#
# Inputs are given by the caller:
#   consts  float32 (256, L, 4) float constants of this shader (already including literals)
#   bools   (256,) bool,  ints (32, 4) int loop constants (count, start, step)
#   regs    {register: (L, 4)} initial temporaries (PS interpolators / param gen)
#   vfetch  callable(slot) -> (L, 4) fetched vertex element (D3D9 semantics, w defaults applied)
#   tex     callable(fetch_const, op, dim, coords (L,4), lod (L,)|None, bias float, grad) -> (L, 4)
# Result: exports {('pos'|'psize'|'o'|'color'|'depth', index): (values (L,4), written mask (L,4))}, kill (L,)
import numpy as np
import ucode as U

F = np.float32
FLT_MAX = np.float32(3.402823466e38)


def _flush(x):
    return np.where(np.abs(x) < np.float32(1.1754944e-38), np.float32(0) * np.sign(x), x).astype(F)


def lmul(a, b):
    return np.where((a == 0) | (b == 0), F(0), a * b).astype(F)


class Result:
    def __init__(self):
        self.exports = {}
        self.kill = None


def cube_dir(c):
    """tfetchCube coordinates (sc+1.5 scaled, tc, face) -> direction vector"""
    s = (c[:, 0] - F(1.5)) * 2
    t = (c[:, 1] - F(1.5)) * 2
    f = np.clip(np.floor(c[:, 2] + F(0.5)), 0, 5).astype(int)
    one = np.ones_like(s)
    d = np.zeros((len(s), 3), F)
    tab = {0: (one, -t, -s), 1: (-one, -t, s), 2: (s, one, t), 3: (s, -one, -t), 4: (s, -t, one), 5: (-s, -t, -one)}
    for k, (x, y, z) in tab.items():
        m = f == k
        d[m, 0], d[m, 1], d[m, 2] = x[m], y[m], z[m]
    return d


TRACE = None      # debugging: set to a dict to get {slot: copy of all temporaries after that slot}


def run(prog, is_ps, L, consts, regs=None, vfetch=None, tex=None, bools=None, ints=None, max_steps=100000):
    with np.errstate(all='ignore'):
        return _run(prog, is_ps, L, consts, regs, vfetch, tex, bools, ints, max_steps)


def _run(prog, is_ps, L, consts, regs, vfetch, tex, bools, ints, max_steps):
    R = np.zeros((128, L, 4), F)
    for k, v in (regs or {}).items():
        R[k] = v
    ps = np.zeros(L, F)
    p0 = np.zeros(L, bool)
    a0 = np.zeros(L, int)
    lod_reg = np.zeros(L, F)
    grad_h = np.zeros((L, 4), F)
    grad_v = np.zeros((L, 4), F)
    loop_depth = np.zeros(L, int)
    loop_iter = np.zeros((L, 5), int)
    loop_al = np.zeros((L, 5), int)
    loop_cnt = np.zeros((L, 5), int)
    loop_step = np.zeros((L, 5), int)
    kill = np.zeros(L, bool)
    res = Result()
    lpc = np.zeros(L, int)
    live = np.ones(L, bool)
    lanes = np.arange(L)
    bools = np.zeros(256, bool) if bools is None else bools
    ints = np.zeros((32, 4), int) if ints is None else ints
    cfs = prog.cfs
    ncf = len(cfs)

    def aL():
        return loop_al[lanes, np.maximum(loop_depth - 1, 0)]

    def temp_index(idx, rel):
        if rel:
            return np.clip(idx + aL(), 0, 127)
        return idx

    def read_temp(idx, rel):
        if rel:
            return R[temp_index(idx, rel), lanes]
        return R[idx]

    def read_operand(o):
        if o.kind == 'r':
            v = read_temp(o.index, o.rel == 'aL')
        else:
            if o.rel:
                off = a0 if o.rel == 'a0' else aL()
                ix = o.index + off
                ok = (ix >= 0) & (ix < 256)
                v = np.where(ok[:, None], consts[np.clip(ix, 0, 255), lanes], F(0))
            else:
                v = consts[o.index]
        v = _flush(v[:, o.comps])
        if o.abs:
            v = np.abs(v)
        if o.neg:
            v = -v
        return v

    def write_temp(idx, rel, mask_lanes, comps_mask, val):
        """val (L,4); comps_mask 4-bit; writes lanes in mask_lanes"""
        cm = np.array([(comps_mask >> j) & 1 for j in range(4)], bool)
        if rel:
            ix = temp_index(idx, True)
            cur = R[ix, lanes]
            new = np.where(mask_lanes[:, None] & cm[None, :], val, cur)
            R[ix, lanes] = new
        else:
            R[idx] = np.where(mask_lanes[:, None] & cm[None, :], val, R[idx])

    def export(key, mask_lanes, cm, val):
        if key not in res.exports:
            res.exports[key] = (np.zeros((L, 4), F), np.zeros((L, 4), bool))
        v, w = res.exports[key]
        m = mask_lanes[:, None] & cm[None, :]
        res.exports[key] = (np.where(m, val, v), w | m)

    def alu(ins, m):
        nonlocal ps, p0, a0
        vop = ins.vop
        vres = np.zeros((L, 4), F)
        vmask = ins.vec_result_mask()
        if vmask or ins.vec_changes_state():
            ops = ins.vec_operands()
            a = read_operand(ops[0]) if len(ops) > 0 else None
            b = read_operand(ops[1]) if len(ops) > 1 else None
            c = read_operand(ops[2]) if len(ops) > 2 else None
            if vop == U.V_ADD:
                vres = a + b
            elif vop == U.V_MUL:
                vres = lmul(a, b)
            elif vop == U.V_MAX or vop == U.V_MAXA:
                vres = np.where(a >= b, a, b)
                if vop == U.V_MAXA:
                    a0n = np.floor(np.clip(a[:, 3], -256, 255) + F(0.5)).astype(int)
                    a0 = np.where(m, a0n, a0)
            elif vop == U.V_MIN:
                vres = np.where(a < b, a, b)
            elif vop == U.V_SEQ:
                vres = (a == b).astype(F)
            elif vop == U.V_SGT:
                vres = (a > b).astype(F)
            elif vop == U.V_SGE:
                vres = (a >= b).astype(F)
            elif vop == U.V_SNE:
                vres = (a != b).astype(F)
            elif vop == U.V_FRC:
                vres = a - np.floor(a)
            elif vop == U.V_TRUNC:
                vres = np.trunc(a)
            elif vop == U.V_FLOOR:
                vres = np.floor(a)
            elif vop == U.V_MAD:
                vres = lmul(a, b) + c
            elif vop == U.V_CNDEQ:
                vres = np.where(a == 0, b, c)
            elif vop == U.V_CNDGE:
                vres = np.where(a >= 0, b, c)
            elif vop == U.V_CNDGT:
                vres = np.where(a > 0, b, c)
            elif vop in (U.V_DP4, U.V_DP3, U.V_DP2ADD):
                n = {U.V_DP4: 4, U.V_DP3: 3, U.V_DP2ADD: 2}[vop]
                s = np.zeros(L, F)
                for j in range(n):
                    s = s + lmul(a[:, j], b[:, j])
                if vop == U.V_DP2ADD:
                    s = s + c[:, 0]
                vres = np.repeat(s[:, None], 4, 1)
            elif vop == U.V_CUBE:
                x, y, z = a[:, 2], a[:, 3], a[:, 0]
                ax, ay, az = np.abs(x), np.abs(y), np.abs(z)
                zm = (az >= ax) & (az >= ay)
                ym = ~zm & (ay >= ax)
                xm = ~zm & ~ym
                r0 = np.where(zm, -y, np.where(ym, np.where(y < 0, -z, z), -y))
                r1 = np.where(zm, np.where(z < 0, -x, x), np.where(ym, x, np.where(x < 0, z, -z)))
                r2 = np.where(zm, z, np.where(ym, y, x)) * 2
                r3 = np.where(zm, np.where(z < 0, 5, 4), np.where(ym, np.where(y < 0, 3, 2),
                                                                   np.where(x < 0, 1, 0))).astype(F)
                vres = np.stack([r0, r1, r2, r3], 1).astype(F)
            elif vop == U.V_MAX4:
                x, y, z, w = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
                r = np.where((x >= y) & (x >= z) & (x >= w), x, np.where((y >= z) & (y >= w), y,
                                                                          np.where(z >= w, z, w)))
                vres = np.repeat(r[:, None], 4, 1)
            elif vop in (U.V_SETP_EQ_PUSH, U.V_SETP_NE_PUSH, U.V_SETP_GT_PUSH, U.V_SETP_GE_PUSH):
                cmpf = {U.V_SETP_EQ_PUSH: lambda v: v == 0, U.V_SETP_NE_PUSH: lambda v: v != 0,
                        U.V_SETP_GT_PUSH: lambda v: v > 0, U.V_SETP_GE_PUSH: lambda v: v >= 0}[vop]
                pn = (a[:, 3] == 0) & cmpf(b[:, 3])
                p0 = np.where(m, pn, p0)
                r = np.where((a[:, 0] == 0) & cmpf(b[:, 0]), F(0), a[:, 0] + 1)
                vres = np.repeat(r[:, None], 4, 1)
            elif vop in (U.V_KILL_EQ, U.V_KILL_GT, U.V_KILL_GE, U.V_KILL_NE):
                cmpf = {U.V_KILL_EQ: np.equal, U.V_KILL_GT: np.greater, U.V_KILL_GE: np.greater_equal,
                        U.V_KILL_NE: np.not_equal}[vop]
                k = cmpf(a, b).any(1)
                kill[:] = kill | (m & k)
                vres = np.repeat(k.astype(F)[:, None], 4, 1)
            elif vop == U.V_DST:
                vres = np.stack([np.ones(L, F), lmul(a[:, 1], b[:, 1]), a[:, 2], b[:, 3]], 1)
            else:
                raise NotImplementedError('vector op %d' % vop)
            vres = vres.astype(F)
        # scalar
        sop = ins.sop
        sops = ins.scl_operands()
        name, nops, two = U.SCL_INFO[sop]
        if nops == 1:
            o = sops[0]
            v = read_operand(o)
            sa = v[:, 0]
            sb = v[:, 1] if two else None
        elif nops == 2:
            sa = read_operand(sops[0])[:, 0]
            sb = read_operand(sops[1])[:, 0]
        newps = ps
        if sop in (U.S_ADDS, U.S_ADDSC0, U.S_ADDSC1):
            newps = sa + sb
        elif sop == U.S_ADDS_PREV:
            newps = sa + ps
        elif sop in (U.S_MULS, U.S_MULSC0, U.S_MULSC1):
            newps = lmul(sa, sb)
        elif sop == U.S_MULS_PREV:
            newps = lmul(sa, ps)
        elif sop == U.S_MULS_PREV2:
            bad = (ps == -FLT_MAX) | ~np.isfinite(ps) | ~np.isfinite(sb) | (sb <= 0)
            newps = np.where(bad, -FLT_MAX, lmul(sa, ps))
        elif sop == U.S_MAXS:
            newps = np.where(sa >= sb, sa, sb)
        elif sop == U.S_MINS:
            newps = np.where(sa < sb, sa, sb)
        elif sop == U.S_SEQS:
            newps = (sa == 0).astype(F)
        elif sop == U.S_SGTS:
            newps = (sa > 0).astype(F)
        elif sop == U.S_SGES:
            newps = (sa >= 0).astype(F)
        elif sop == U.S_SNES:
            newps = (sa != 0).astype(F)
        elif sop == U.S_FRCS:
            newps = sa - np.floor(sa)
        elif sop == U.S_TRUNCS:
            newps = np.trunc(sa)
        elif sop == U.S_FLOORS:
            newps = np.floor(sa)
        elif sop == U.S_EXP:
            newps = np.exp2(sa)
        elif sop == U.S_LOGC:
            t = np.log2(sa)
            newps = np.where(t == -np.inf, -FLT_MAX, t)
        elif sop == U.S_LOG:
            newps = np.log2(sa)
        elif sop in (U.S_RCPC, U.S_RCPF, U.S_RCP):
            t = F(1) / sa
            if sop == U.S_RCPC:
                t = np.clip(t, -FLT_MAX, FLT_MAX)
            elif sop == U.S_RCPF:
                t = np.where(np.isinf(t), F(0) * np.sign(t), t)
            newps = t
        elif sop in (U.S_RSQC, U.S_RSQF, U.S_RSQ):
            t = F(1) / np.sqrt(sa)
            if sop == U.S_RSQC:
                t = np.clip(t, -FLT_MAX, FLT_MAX)
            elif sop == U.S_RSQF:
                t = np.where(np.isinf(t), F(0) * np.sign(t), t)
            newps = t
        elif sop in (U.S_MAXAS, U.S_MAXASF):
            if sop == U.S_MAXAS:
                a0n = np.floor(np.clip(sa, -256, 255) + F(0.5)).astype(int)
            else:
                a0n = np.floor(np.clip(sa, -256, 255)).astype(int)
            a0 = np.where(m, a0n, a0)
            newps = np.where(sa >= sb, sa, sb)
        elif sop in (U.S_SUBS, U.S_SUBSC0, U.S_SUBSC1):
            newps = sa - sb
        elif sop == U.S_SUBS_PREV:
            newps = sa - ps
        elif sop in (U.S_SETP_EQ, U.S_SETP_NE, U.S_SETP_GT, U.S_SETP_GE):
            pn = {U.S_SETP_EQ: sa == 0, U.S_SETP_NE: sa != 0, U.S_SETP_GT: sa > 0, U.S_SETP_GE: sa >= 0}[sop]
            p0 = np.where(m, pn, p0)
            newps = (~pn).astype(F)
        elif sop == U.S_SETP_INV:
            pn = sa == 1
            p0 = np.where(m, pn, p0)
            newps = np.where(pn, F(0), np.where(sa == 0, F(1), sa))
        elif sop == U.S_SETP_POP:
            nc = sa - 1
            pn = nc <= 0
            p0 = np.where(m, pn, p0)
            newps = np.where(pn, F(0), nc)
        elif sop == U.S_SETP_CLR:
            p0 = np.where(m, False, p0)
            newps = np.full(L, FLT_MAX, F)
        elif sop == U.S_SETP_RSTR:
            pn = sa == 0
            p0 = np.where(m, pn, p0)
            newps = np.where(pn, F(0), sa)
        elif sop in (U.S_KILLS_EQ, U.S_KILLS_GT, U.S_KILLS_GE, U.S_KILLS_NE, U.S_KILLS_ONE):
            k = {U.S_KILLS_EQ: sa == 0, U.S_KILLS_GT: sa > 0, U.S_KILLS_GE: sa >= 0, U.S_KILLS_NE: sa != 0,
                 U.S_KILLS_ONE: sa == 1}[sop]
            kill[:] = kill | (m & k)
            newps = k.astype(F)
        elif sop == U.S_SQRT:
            newps = np.sqrt(sa)
        elif sop == U.S_SIN:
            newps = np.sin(sa)
        elif sop == U.S_COS:
            newps = np.cos(sa)
        elif sop == U.S_RETAIN_PREV:
            pass
        else:
            raise NotImplementedError('scalar op %d' % sop)
        newps = np.asarray(newps, F)
        if sop != U.S_RETAIN_PREV:
            ps = np.where(m, newps, ps)
        if ins.vclamp:
            vres = np.clip(vres, 0, 1)
        sres = np.clip(ps, 0, 1) if ins.sclamp else ps
        smask = ins.scl_result_mask()
        if ins.export:
            c0, c1 = ins.const0_mask(), ins.const1_mask()
            val = np.zeros((L, 4), F)
            for j in range(4):
                if (vmask >> j) & 1:
                    val[:, j] = vres[:, j]
                elif (smask >> j) & 1:
                    val[:, j] = sres
                elif (c1 >> j) & 1:
                    val[:, j] = 1
            em = vmask | smask | c0 | c1
            cm = np.array([(em >> j) & 1 for j in range(4)], bool)
            d = ins.vdst
            if is_ps:
                key = ('depth', 0) if d == U.EXP_PS_DEPTH else ('color', d)
            else:
                key = ('pos', 0) if d == U.EXP_VS_POS else ('psize', 0) if d == U.EXP_VS_PSIZE else ('o', d)
            export(key, m, cm, val)
        else:
            if vmask:
                write_temp(ins.vdst, ins.vdst_rel, m, vmask, vres)
            if smask:
                write_temp(ins.sdst, ins.sdst_rel, m, smask, np.repeat(sres[:, None], 4, 1))

    def fetch(ins, m):
        nonlocal lod_reg, grad_h, grad_v
        if ins.op == U.FETCH_VERTEX:
            val = vfetch(ins)
            dc = ins.dst_components()
            out = read_temp(ins.dst, ins.dst_rel).copy()
            cm = 0
            for j in range(4):
                c = dc[j]
                if c == 7:
                    continue
                cm |= 1 << j
                out[:, j] = val[:, c] if c < 4 else (1 if c == 5 else 0)
            write_temp(ins.dst, ins.dst_rel, m, cm, out)
            return
        src = read_temp(ins.src, ins.src_rel)
        if ins.op == U.FETCH_SET_LOD:
            lod_reg = np.where(m, src[:, ins.src_components(1)[0]], lod_reg)
            return
        if ins.op in (U.FETCH_SET_GRAD_H, U.FETCH_SET_GRAD_V):
            g = src[:, ins.src_components(3)]
            g = np.concatenate([g, np.zeros((L, 1), F)], 1)
            if ins.op == U.FETCH_SET_GRAD_H:
                grad_h = np.where(m[:, None], g, grad_h)
            else:
                grad_v = np.where(m[:, None], g, grad_v)
            return
        n = {U.DIM_1D: 1, U.DIM_2D: 2, U.DIM_3D: 3, U.DIM_CUBE: 3}[ins.dim]
        co = src[:, ins.src_components(n)]
        coords = np.zeros((L, 4), F)
        coords[:, :n] = co
        lod = None
        if ins.op == U.FETCH_TEXTURE:
            if ins.use_reg_lod:
                lod = lod_reg
            val = tex(ins.const_index, ins.op, ins.dim, coords, lod, ins.lod_bias, ins.use_comp_lod,
                      (grad_h, grad_v) if ins.use_reg_grad else None, ins)
        else:
            val = tex(ins.const_index, ins.op, ins.dim, coords, None, 0.0, True, None, ins)
        dc = ins.dst_components()
        out = np.zeros((L, 4), F)
        cm = 0
        for j in range(4):
            c = dc[j]
            if c == 7:
                continue
            cm |= 1 << j
            out[:, j] = val[:, c] if c < 4 else (1 if c == 5 else 0)
        write_temp(ins.dst, ins.dst_rel, m, cm, out)

    steps = 0
    while live.any():
        steps += 1
        if steps > max_steps:
            raise RuntimeError('step limit')
        pc = lpc[live].min()
        m = live & (lpc == pc)
        if pc >= ncf:
            live &= ~m
            continue
        c = cfs[pc]
        op = c.op
        nxt = np.where(m, pc + 1, lpc)
        if op in U.CF_EXECS:
            em = m.copy()
            if op in (U.CF_COND_EXEC, U.CF_COND_EXEC_END, U.CF_COND_EXEC_PRED_CLEAN, U.CF_COND_EXEC_PRED_CLEAN_END):
                if bool(bools[c.bool_address]) != bool(c.condition):
                    em[:] = False
            elif op in (U.CF_COND_EXEC_PRED, U.CF_COND_EXEC_PRED_END):
                em &= (p0 == bool(c.condition))
            for a, ins, ser in prog.exec_instrs(c):
                im = em
                if ins.predicated:
                    im = em & (p0 == bool(ins.pred_cond))
                if not im.any():
                    continue
                if isinstance(ins, U.Fetch):
                    fetch(ins, im)
                else:
                    alu(ins, im)
                if TRACE is not None:
                    TRACE[a] = R.copy()
            if op in U.CF_ENDS:
                live &= ~em
            lpc = nxt
        elif op == U.CF_COND_JMP:
            if c.unconditional:
                j = m
            elif c.predicated:
                j = m & (p0 == bool(c.condition))
            else:
                j = m & (bool(bools[c.bool_address]) == bool(c.condition))
            lpc = np.where(j, c.address, nxt)
        elif op == U.CF_LOOP_START:
            cnt, start, step = ints[c.loop_id][:3]
            d = loop_depth
            mm = m
            loop_cnt[lanes[mm], d[mm]] = cnt
            loop_step[lanes[mm], d[mm]] = step
            if not c.is_repeat:
                loop_iter[lanes[mm], d[mm]] = 0
                loop_al[lanes[mm], d[mm]] = start
            skip = mm & (loop_iter[lanes, d] >= cnt)
            loop_depth = np.where(mm & ~skip, d + 1, d)
            lpc = np.where(skip, c.address, nxt)
        elif op == U.CF_LOOP_END:
            d = np.maximum(loop_depth - 1, 0)
            it = loop_iter[lanes, d] + 1
            loop_iter[lanes[m], d[m]] = it[m]
            loop_al[lanes[m], d[m]] += loop_step[lanes[m], d[m]]
            cont = it < loop_cnt[lanes, d]
            if c.pred_break:
                cont &= (p0 != bool(c.condition))
            cont &= m
            lpc = np.where(cont, c.address, nxt)
            loop_depth = np.where(m & ~cont, d, loop_depth)
        elif op in (U.CF_NOP, U.CF_ALLOC, U.CF_MARK_VS_FETCH_DONE):
            lpc = nxt
        else:
            raise NotImplementedError('cf ' + U.CF_NAMES[op])
    res.kill = kill
    res.R = R
    return res
