# Direct3D 9 shader model 3 bytecode: parsing, disassembly, constant table (CTAB) read/write.
# Token format as documented in the Windows Driver Kit (d3d9types.h D3DSHADER_* / D3DSPR_* / D3DSIO_*).
import struct

OPC = {0: 'nop', 1: 'mov', 2: 'add', 3: 'sub', 4: 'mad', 5: 'mul', 6: 'rcp', 7: 'rsq', 8: 'dp3', 9: 'dp4',
       10: 'min', 11: 'max', 12: 'slt', 13: 'sge', 14: 'exp', 15: 'log', 16: 'lit', 17: 'dst', 18: 'lrp',
       19: 'frc', 20: 'm4x4', 21: 'm4x3', 22: 'm3x4', 23: 'm3x3', 24: 'm3x2', 25: 'call', 26: 'callnz',
       27: 'loop', 28: 'ret', 29: 'endloop', 30: 'label', 31: 'dcl', 32: 'pow', 33: 'crs', 34: 'sgn', 35: 'abs',
       36: 'nrm', 37: 'sincos', 38: 'rep', 39: 'endrep', 40: 'if', 41: 'ifc', 42: 'else', 43: 'endif',
       44: 'break', 45: 'breakc', 46: 'mova', 47: 'defb', 48: 'defi', 64: 'texcoord', 65: 'texkill', 66: 'tex',
       78: 'expp', 79: 'logp', 80: 'cnd', 81: 'def', 88: 'cmp', 90: 'dp2add', 91: 'dsx', 92: 'dsy',
       93: 'texldd', 94: 'setp', 95: 'texldl', 96: 'breakp'}
OPN = {v: k for k, v in OPC.items()}
# number of source operands (destination always present unless noted in NODST)
NSRC = {'nop': 0, 'mov': 1, 'add': 2, 'sub': 2, 'mad': 3, 'mul': 2, 'rcp': 1, 'rsq': 1, 'dp3': 2, 'dp4': 2,
        'min': 2, 'max': 2, 'slt': 2, 'sge': 2, 'exp': 1, 'log': 1, 'lit': 1, 'dst': 2, 'lrp': 3, 'frc': 1,
        'm4x4': 2, 'm4x3': 2, 'm3x4': 2, 'm3x3': 2, 'm3x2': 2, 'call': 1, 'callnz': 2, 'loop': 2, 'ret': 0,
        'endloop': 0, 'label': 1, 'pow': 2, 'crs': 2, 'sgn': 3, 'abs': 1, 'nrm': 1, 'sincos': 1, 'rep': 1,
        'endrep': 0, 'if': 1, 'ifc': 2, 'else': 0, 'endif': 0, 'break': 0, 'breakc': 2, 'mova': 1,
        'texkill': 0, 'tex': 2, 'expp': 1, 'logp': 1, 'cnd': 3, 'cmp': 3, 'dp2add': 3, 'dsx': 1, 'dsy': 1,
        'texldd': 4, 'setp': 2, 'texldl': 2, 'breakp': 1}
NODST = {'nop', 'call', 'callnz', 'loop', 'ret', 'endloop', 'label', 'rep', 'endrep', 'if', 'ifc', 'else',
         'endif', 'break', 'breakc', 'breakp'}
RT = {0: 'r', 1: 'v', 2: 'c', 3: 'a', 4: 'o', 5: 'oD', 6: 'o', 7: 'i', 8: 'oC', 9: 'oDepth', 10: 's', 11: 'c',
      12: 'c', 13: 'c', 14: 'b', 15: 'aL', 16: 'half', 17: 'misc', 18: 'l', 19: 'p'}
T_TEMP, T_INPUT, T_CONST, T_ADDR, T_RASTOUT, T_ATTROUT, T_OUTPUT, T_CONSTINT, T_COLOROUT, T_DEPTHOUT, \
    T_SAMPLER, T_CONST2, T_CONST3, T_CONST4, T_CONSTBOOL, T_LOOP, T_TEMPF16, T_MISC, T_LABEL, T_PRED = range(20)
USAGE = ['position', 'blendweight', 'blendindices', 'normal', 'psize', 'texcoord', 'tangent', 'binormal',
         'tessfactor', 'positiont', 'color', 'fog', 'depth', 'sample']
CMP = {1: 'gt', 2: 'eq', 3: 'ge', 4: 'lt', 5: 'ne', 6: 'le'}
SRCMOD = ['', 'neg', 'bias', 'biasneg', 'sign', 'signneg', 'comp', 'x2', 'x2neg', 'dz', 'dw', 'abs', 'absneg',
          'not']
TEXTYPE = {2: '2d', 3: 'cube', 4: 'volume'}


def regtype(t):
    return ((t >> 28) & 7) | ((t >> 8) & 0x18)


class Param:
    def __init__(self, tok, is_dst, rel=None):
        self.tok = tok
        self.type = regtype(tok)
        self.num = tok & 0x7FF
        self.relative = bool(tok & 0x2000)
        self.rel = rel                     # source param of the relative address register (a0 / aL)
        if is_dst:
            self.mask = (tok >> 16) & 15
            self.mod = (tok >> 20) & 15    # 1 saturate, 2 pp, 4 centroid
            self.shift = (tok >> 24) & 15
        else:
            sw = (tok >> 16) & 0xFF
            self.swz = [(sw >> (2 * i)) & 3 for i in range(4)]
            self.srcmod = (tok >> 24) & 15

    def name(self):
        t = self.type
        if t == T_RASTOUT:
            return ['oPos', 'oFog', 'oPts'][self.num]
        if t == T_MISC:
            return ['vPos', 'vFace'][self.num]
        if t == T_OUTPUT:
            return 'o%d' % self.num
        n = RT.get(t, '?%d' % t)
        if n == 'aL':
            return 'aL'
        if self.relative and self.rel is not None:
            return '%s[%s%s + %d]' % (n, self.rel.name(), '.' + 'xyzw'[self.rel.swz[0]], self.num)
        return '%s%d' % (n, self.num)


class Instr:
    def __init__(self, op, ctrl, pred, dst, srcs, extra=None, raw=None):
        self.op, self.ctrl, self.pred, self.dst, self.srcs, self.extra = op, ctrl, pred, dst, srcs, extra
        self.raw = raw

    def __repr__(self):
        return disasm_instr(self)


class Shader:
    def __init__(self, data):
        self.data = data
        w = struct.unpack('<%dI' % (len(data) // 4), data[:len(data) // 4 * 4])
        self.words = w
        self.version = w[0]
        self.is_ps = (w[0] >> 16) == 0xFFFF
        self.major, self.minor = (w[0] >> 8) & 0xFF, w[0] & 0xFF
        self.instrs = []
        self.comments = []          # (word index, payload bytes)
        i = 1
        while i < len(w):
            t = w[i]
            if t == 0x0000FFFF:
                break
            op = t & 0xFFFF
            if op == 0xFFFE:
                n = (t >> 16) & 0x7FFF
                self.comments.append((i, data[4 * (i + 1): 4 * (i + 1 + n)]))
                i += 1 + n
                continue
            ln = (t >> 24) & 15
            toks = w[i + 1: i + 1 + ln]
            name = OPC.get(op, 'op%d' % op)
            ctrl = (t >> 16) & 0xFF
            pred = None
            ins = None
            if name == 'dcl':
                ins = Instr(name, ctrl, None, Param(toks[1], True), [], extra=toks[0])
            elif name in ('def', 'defi', 'defb'):
                vals = toks[1:]
                if name == 'def':
                    vals = struct.unpack('<%df' % len(vals), struct.pack('<%dI' % len(vals), *vals))
                ins = Instr(name, ctrl, None, Param(toks[0], True), [], extra=tuple(vals))
            else:
                params = []
                k = 0
                has_dst = name not in NODST
                first = True
                while k < len(toks):
                    tok = toks[k]
                    k += 1
                    isd = first and has_dst
                    rel = None
                    if tok & 0x2000 and not isd or (isd and tok & 0x2000 and self.major >= 3):
                        if k < len(toks) and (toks[k] >> 31):
                            rel = Param(toks[k], False)
                            k += 1
                    params.append(Param(tok, isd, rel))
                    first = False
                dst = params[0] if has_dst else None
                srcs = params[1:] if has_dst else params
                if t & 0x10000000:           # predicated: predicate register is the first source token
                    pred = srcs[0] if not has_dst else srcs[0]
                    srcs = srcs[1:]
                ins = Instr(name, ctrl, pred, dst, srcs)
            ins.raw = (i, t, toks)
            self.instrs.append(ins)
            i += 1 + ln

    def ctab(self):
        for _, c in self.comments:
            if c[:4] == b'CTAB':
                return parse_ctab(c[4:])
        return None

    def dcls(self):
        out = []
        for ins in self.instrs:
            if ins.op == 'dcl':
                out.append(dcl_info(ins))
        return out

    def disasm(self):
        lines = ['%s_%d_%d' % ('ps' if self.is_ps else 'vs', self.major, self.minor)]
        for ins in self.instrs:
            lines.append(disasm_instr(ins))
        return '\n'.join(lines)


def dcl_info(ins):
    tok = ins.extra
    p = ins.dst
    if p.type == T_SAMPLER:
        return ('s', p.num, TEXTYPE.get((tok >> 27) & 15, '?'))
    return (RT.get(p.type, '?') if p.type != T_MISC else 'misc', p.num, USAGE[tok & 31] if (tok & 31) < len(USAGE)
            else '?', (tok >> 16) & 15, p.mask, p.mod)


def fmt_src(p):
    s = p.name()
    sw = ''.join('xyzw'[c] for c in p.swz)
    if sw == 'xyzw':
        sw = ''
    elif len(set(p.swz)) == 1:
        sw = 'xyzw'[p.swz[0]]
    m = p.srcmod
    if m == 1:
        s = '-' + s
    elif m == 11:
        s = s + '_abs'
    elif m == 12:
        s = '-' + s + '_abs'
    elif m == 13:
        s = '!' + s
    elif m:
        s = s + '_' + SRCMOD[m]
    return s + ('.' + sw if sw else '')


def fmt_dst(p):
    s = p.name()
    if p.mask != 15:
        s += '.' + ''.join('xyzw'[i] for i in range(4) if (p.mask >> i) & 1)
    return s


def disasm_instr(ins):
    n = ins.op
    if n == 'dcl':
        d = dcl_info(ins)
        if d[0] == 's':
            return 'dcl_%s %s' % (d[2], fmt_dst(ins.dst))
        return 'dcl_%s%s %s' % (d[2], d[3] or '', fmt_dst(ins.dst)) + ('_centroid' if d[5] & 4 else '')
    if n in ('def', 'defi', 'defb'):
        return '%s %s, %s' % (n, fmt_dst(ins.dst), ', '.join('%g' % v for v in ins.extra))
    if n == 'tex':
        n = ['texld', 'texldp', 'texldb'][ins.ctrl & 3]
    elif n in ('ifc', 'breakc', 'setp'):
        n = n.rstrip('c') + '_' + CMP.get(ins.ctrl, '?')
    s = n
    if ins.dst is not None:
        if ins.dst.mod & 1:
            s += '_sat'
        if ins.dst.mod & 2:
            s += '_pp'
    ops = ([fmt_dst(ins.dst)] if ins.dst is not None else []) + [fmt_src(p) for p in ins.srcs]
    pre = ('(%s) ' % fmt_src(ins.pred)) if ins.pred is not None else ''
    return pre + s + (' ' + ', '.join(ops) if ops else '')


# ---------------------------------------------------------------------------------------- CTAB
# D3DXSHADER_CONSTANTTABLE: Size, Creator, Version, Constants, ConstantInfo, Flags, Target (offsets from the
# start of the table); D3DXSHADER_CONSTANTINFO: Name, RegisterSet(u16), RegisterIndex(u16), RegisterCount(u16),
# Reserved(u16), TypeInfo, DefaultValue; D3DXSHADER_TYPEINFO: Class, Type, Rows, Columns, Elements,
# StructMembers (u16 each), StructMemberInfo (u32).
def _cstr(b, o):
    e = b.index(b'\0', o)
    return b[o:e].decode('latin-1')


def parse_ctab(b, E='<'):
    size, creator, ver, n, info, flags, target = struct.unpack_from(E + '7I', b, 0)
    out = {'creator': _cstr(b, creator), 'version': ver, 'flags': flags, 'target': _cstr(b, target), 'consts': []}

    def typeinfo(o):
        cls, typ, rows, cols, elems, nmem, memo = struct.unpack_from(E + '6HI', b, o)
        mem = []
        for k in range(nmem):
            nm, to = struct.unpack_from(E + '2I', b, memo + 8 * k)
            mem.append((_cstr(b, nm), typeinfo(to)))
        return (cls, typ, rows, cols, elems, tuple(mem))

    for k in range(n):
        nm, rs, ri, rc, res, ti, dv = struct.unpack_from(E + 'I4H2I', b, info + 20 * k)
        dval = None
        if dv:
            dval = b[dv: dv + 16 * rc]
        out['consts'].append({'name': _cstr(b, nm), 'set': rs, 'reg': ri, 'count': rc, 'type': typeinfo(ti),
                              'default': dval})
    return out


def build_ctab(ct, E='<'):
    """CTAB payload (without the 'CTAB' fourcc) from a parse_ctab dict. Default values are converted as
    float/int arrays when E differs from the source (they are u32 words)."""
    hdr = 28
    info_off = hdr
    blob = bytearray(b'\0' * (hdr + 20 * len(ct['consts'])))
    strings = {}

    def add(data, align=4):
        while len(blob) % align:
            blob.append(0xAB)
        o = len(blob)
        blob.extend(data)
        return o

    def addstr(s):
        if s not in strings:
            strings[s] = add(s.encode('latin-1') + b'\0', 1)
        return strings[s]

    def addtype(t):
        cls, typ, rows, cols, elems, mem = t
        memo = 0
        if mem:
            # members first (each 8 bytes: name, typeinfo)
            names = [addstr(m[0]) for m in mem]
            types = [addtype(m[1]) for m in mem]
            memo = add(b''.join(struct.pack(E + '2I', a, b) for a, b in zip(names, types)))
        return add(struct.pack(E + '6HI', cls, typ, rows, cols, elems, len(mem), memo))

    recs = []
    for c in ct['consts']:
        nm = addstr(c['name'])
        ti = addtype(c['type'])
        dv = 0
        if c.get('default'):
            dv = add(c['default'])
        recs.append((nm, c['set'], c['reg'], c['count'], 0, ti, dv))
    target = addstr(ct['target'])
    creator = addstr(ct['creator'])
    for k, r in enumerate(recs):
        struct.pack_into(E + 'I4H2I', blob, info_off + 20 * k, *r)
    struct.pack_into(E + '7I', blob, 0, hdr, creator, ct['version'], len(recs), info_off, ct['flags'], target)
    while len(blob) % 4:
        blob.append(0xAB)
    return bytes(blob)


def replace_ctab(code, payload):
    """Return bytecode with its CTAB comment replaced by 'CTAB' + payload (inserted after the version token
    if there is none)."""
    w = list(struct.unpack('<%dI' % (len(code) // 4), code))
    body = b'CTAB' + payload
    while len(body) % 4:
        body += b'\0'
    n = len(body) // 4
    new = [0xFFFE | (n << 16)] + list(struct.unpack('<%dI' % n, body))
    i = 1
    while i < len(w):
        t = w[i]
        if t & 0xFFFF == 0xFFFE:
            ln = (t >> 16) & 0x7FFF
            if bytes(struct.pack('<I', w[i + 1]))[:4] == b'CTAB':
                w[i:i + 1 + ln] = new
                return struct.pack('<%dI' % len(w), *w)
            i += 1 + ln
            continue
        break
    w[1:1] = new
    return struct.pack('<%dI' % len(w), *w)
