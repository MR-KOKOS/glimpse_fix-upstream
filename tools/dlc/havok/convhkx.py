"""Split/Second Havok 5.5.0-r1 packfiles: Xbox 360 (big-endian, layout rules 04 00 00 01) -> PC (little-endian,
layout rules 04 01 00 01).

Method
  The Xbox files leave __types__ empty; the PC files carry hkClass/hkClassMember/hkClassEnum reflection there.
  "learn" collects the class layouts from PC packfiles (and, for classes no PC file uses, from the hkClass
  registry of the PC exe) into hkref.json. "convert" then
  - swaps the __classnames__ signatures,
  - walks every __data__ object listed in the virtual fixups with its class layout (inherited members, embedded
    structs, C arrays, hkArray/simple arrays, pointers to non-virtual structs, strings) and swaps each member by
    type (u16, u32/float, u64; u8/bool/char arrays such as MOPP code stay); gaps between blocks become 0x7F like
    the PC writer's,
  - does what the PC tool chain's XML round trip did: reals printed with %f (6 decimals), bools 0/1,
    hkTransform translation.w = 1 (--exact keeps the Xbox values instead),
  - swaps the fixup tables and adds the global fixups of hkVariant class pointers (into __types__),
  - writes a __types__ section for every class reachable from the data (depth-first in the PC writer's order:
    roots = object classes in post-order of the object graph; per class the hkClass, its name, declared enums,
    members and member names, standalone enums for member enum pointers; strings padded to 16 with 0x7F).

Usage
  python -I convhkx.py learn <pc file|dir|.ark|SplitSecond.exe> [...]   build/extend hkref.json
  python -I convhkx.py convert <xbox file> <out file> [--exact]
  python -I convhkx.py convert <xbox file|dir> [...] <out dir> [--exact]  (.phys .maincoll .hkx and any file
                                                                          starting with the packfile magic)
  python -I convhkx.py classes <file>                                    class names and __data__ objects
"""
import glob
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# hkref.json is read from the user's own SplitSecond.exe and PC archives, so it is not shipped: build_all.py makes it
# in its work folder ("learn", about 10 s) and points GLIMPSE_HKREF at it for every converter it runs. Without the
# variable (development, the tests): the copy next to this file.
REF = os.environ.get('GLIMPSE_HKREF') or os.path.join(HERE, 'hkref.json')

# hkClassMember::Type (Havok 5.5)
(T_VOID, T_BOOL, T_CHAR, T_INT8, T_UINT8, T_INT16, T_UINT16, T_INT32, T_UINT32, T_INT64, T_UINT64, T_REAL, T_VECTOR4,
 T_QUATERNION, T_MATRIX3, T_ROTATION, T_QSTRANSFORM, T_MATRIX4, T_TRANSFORM, T_ZERO, T_POINTER, T_FUNCTIONPOINTER,
 T_ARRAY, T_INPLACEARRAY, T_ENUM, T_STRUCT, T_SIMPLEARRAY, T_HOMOGENEOUSARRAY, T_VARIANT, T_CSTRING, T_ULONG,
 T_FLAGS) = range(32)
# (size, swap unit) of the plain types
BASIC = {T_BOOL: (1, 1), T_CHAR: (1, 1), T_INT8: (1, 1), T_UINT8: (1, 1), T_INT16: (2, 2), T_UINT16: (2, 2),
         T_INT32: (4, 4), T_UINT32: (4, 4), T_INT64: (8, 8), T_UINT64: (8, 8), T_REAL: (4, 4), T_VECTOR4: (16, 4),
         T_QUATERNION: (16, 4), T_MATRIX3: (48, 4), T_ROTATION: (48, 4), T_QSTRANSFORM: (48, 4), T_MATRIX4: (64, 4),
         T_TRANSFORM: (64, 4), T_POINTER: (4, 4), T_FUNCTIONPOINTER: (4, 4), T_CSTRING: (4, 4), T_ULONG: (4, 4)}
FLOATS = (T_REAL, T_VECTOR4, T_QUATERNION, T_MATRIX3, T_ROTATION, T_QSTRANSFORM, T_MATRIX4, T_TRANSFORM)
PAD = 0x7F  # the PC writer fills the gaps between blocks with 0x7F, the Xbox writer with 0x00
# Members that hold uninitialised tool memory in the shipped files (different garbage on Xbox and PC; Havok
# recomputes them at runtime). The converter keeps the (swapped) Xbox values; the test ignores them.
VOLATILE = {'hkpCollidableBoundingVolumeData': ('min', 'expansionMin', 'expansionShift', 'max', 'expansionMax',
                                                'padding'),
            'hkpMotion': ('savedQualityTypeIndex',),  # u16 + 2 padding bytes
            'hkpExtendedMeshShapeTrianglesSubpart': ('triangleOffset',),
            # exporter heap addresses (16-byte aligned, 0x17xxxxxx..0xbfxxxxxx, a few hundred bytes apart between
            # the builds; files that were not re-exported for the PC have the same values); the game sets its own
            # user data after loading
            'hkpWorldObject': ('userData',),
            'hkpPhysicsSystem': ('userData',),
            # export-time object number (Graveyard: 0x773 Xbox, 0x772 PC in two files); the Xbox value matches the
            # other Xbox level files, which are converted together
            'HavokAnimatedSystemExportUserData': ('id',)}
MAGIC = b'\x57\xe0\xe0\x57\x10\xc0\xc0\x10'
META = ('hkClass', 'hkClassMember', 'hkClassEnum', 'hkClassEnumItem')


# ---------------------------------------------------------------------------------------------- packfile reading
class Section:
    pass


class Packfile:
    def __init__(self, d):
        if d[:8] != MAGIC:
            raise ValueError('not a Havok packfile')
        self.d = d
        self.E = E = '<' if d[0x11] == 1 else '>'
        self.rules = d[0x10:0x14]
        self.user_tag, self.version = struct.unpack_from(E + 'iI', d, 8)
        nsec, self.contents_sec, self.contents_off, self.cn_sec, self.cn_off = struct.unpack_from(E + '5i', d, 0x14)
        self.flags, = struct.unpack_from(E + 'I', d, 0x38)
        self.secs = []
        for i in range(nsec):
            o = 0x40 + i * 0x30
            s = Section()
            s.name = d[o:o + 20].split(b'\0')[0].decode('latin-1')
            s.start, s.loc, s.glob, s.virt, s.exp, s.imp, s.end = struct.unpack_from(E + '7I', d, o + 20)
            s.raw = d[s.start:s.start + s.end] if s.end else b''
            s.data = s.raw[:s.loc]
            s.local = self._tab(s.raw, s.loc, s.glob, 2)
            s.globl = self._tab(s.raw, s.glob, s.virt, 3)
            s.virtual = self._tab(s.raw, s.virt, s.exp, 3)
            self.secs.append(s)

    def _tab(self, raw, a, b, n):
        out = []
        for o in range(a, b - 4 * n + 1, 4 * n):
            v = struct.unpack_from(self.E + '%di' % n, raw, o)
            if v[0] != -1:
                out.append(v)
        return out

    def sec(self, name):
        for i, s in enumerate(self.secs):
            if s.name == name:
                return i, s
        raise KeyError(name)

    def classnames(self):
        """[(offset of the name, signature, name)] of the __classnames__ section"""
        _, s = self.sec('__classnames__')
        out, o = [], 0
        while o + 5 < len(s.data) and s.data[o + 4] == 9:
            e = s.data.index(b'\0', o + 5)
            out.append((o + 5, struct.unpack_from(self.E + 'I', s.data, o)[0], s.data[o + 5:e].decode('latin-1')))
            o = e + 1
        return out

    def objects(self, secname='__data__'):
        """[(offset, class name)] from the virtual fixups"""
        _, s = self.sec(secname)
        return [(a, cstr(self.secs[b].data, c)) for a, b, c in s.virtual]


def cstr(d, o):
    return d[o:d.index(b'\0', o)].decode('latin-1')


# ---------------------------------------------------------------------------------------------- .ark reading
def lzss(src, i, size):
    win = bytearray(4096)
    r, out, flags = 0xFEE, bytearray(), 0
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & 0xFFF
        else:
            a, b = src[i], src[i + 1]
            i += 2
            o, n = a | ((b & 0xF0) << 4), (b & 0x0F) + 3
            for k in range(n):
                c = win[(o + k) & 0xFFF]
                out.append(c)
                win[r] = c
                r = (r + 1) & 0xFFF
    return bytes(out[:size])


def ark_havok(fn):
    """(key, data) of the Havok packfiles inside a Split/Second .ark"""
    f = open(fn, 'rb')
    head = f.read(24)
    E = '>' if head[:4] == b'\x00\x01\x00\x00' else '<'
    ver, hsize, ca, offa, cb, offb = struct.unpack_from(E + '6I', head, 0)
    f.seek(8 + offa)
    idx = f.read(20 * ca)
    for k in range(ca):
        t, off, cs, us, key = struct.unpack_from(E + '5I', idx, 20 * k)
        if t not in (0, 1) or us < 0x40:
            continue
        f.seek(off)
        raw = f.read(9)
        if t == 0 and raw[:8] == MAGIC:
            f.seek(off)
            yield key, f.read(us)
        elif t == 1 and raw[5:9] == MAGIC[:4]:  # 4-byte prefix, flag byte, then the magic as literals
            f.seek(off)
            yield key, lzss(f.read(cs + 16) + b'\0' * 16, 4, us)


# ---------------------------------------------------------------------------------------------- reflection
def read_types(pf):
    """Class layouts from the little-endian __types__ section of a PC packfile."""
    ti, s = pf.sec('__types__')
    d = s.data
    loc = {a: b for a, b in s.local}
    glob_ = {a: (b, c) for a, b, c in s.globl}

    def target(o):
        if o in loc:
            return loc[o]
        if o in glob_ and glob_[o][0] == ti:
            return glob_[o][1]
        return None

    objs = pf.objects('__types__')
    names = {a: cstr(d, loc[a]) for a, k in objs}
    classes = {}
    for a, k in objs:
        if k != 'hkClass':
            continue
        p = target(a + 4)
        size, nif = struct.unpack_from('<ii', d, a + 8)
        nenum, = struct.unpack_from('<i', d, a + 20)
        nmem, = struct.unpack_from('<i', d, a + 28)
        flags, ver = struct.unpack_from('<Ii', d, a + 40)
        if target(a + 32) is not None or target(a + 36) is not None:
            raise ValueError('class %s has defaults/attributes (not supported)' % names[a])
        eo = target(a + 16)
        ce = [read_enum(d, eo + 20 * k, loc) for k in range(nenum)]
        mem = []
        mo = target(a + 24)
        for k in range(nmem):
            m = mo + 24 * k
            t, st, ca, fl, off = struct.unpack_from('<BBhHH', d, m + 12)
            mc, me = target(m + 4), target(m + 8)
            if target(m + 20) is not None:
                raise ValueError('member attributes not supported')
            edef = read_enum(d, me, loc) if me is not None else None
            mem.append({'name': cstr(d, loc[m]), 'type': t, 'sub': st, 'carray': ca, 'flags': fl, 'offset': off,
                        'class': names[mc] if mc is not None else None, 'enum': edef['name'] if edef else None,
                        '_enumdef': edef})
        classes[names[a]] = {'parent': names[p] if p is not None else None, 'size': size, 'interfaces': nif,
                             'enums': ce, 'members': mem, 'flags': flags, 'version': ver}
    return classes


def read_enum(d, a, loc):
    n, = struct.unpack_from('<i', d, a + 8)
    flags, = struct.unpack_from('<I', d, a + 16)
    items = []
    if n:
        io = loc[a + 4]
        for k in range(n):
            v, = struct.unpack_from('<i', d, io + 8 * k)
            items.append([v, cstr(d, loc[io + 8 * k + 4])])
    return {'name': cstr(d, loc[a]), 'items': items, 'flags': flags}


def read_exe(fn):
    """hkClass registry of the Win32 game exe: the static initialisers push the hkClass constructor arguments
    (... push name; mov ecx, <hkClass object>; call), the member and enum tables are plain data. Member enum
    pointers are only set at run time (left out)."""
    d = open(fn, 'rb').read()
    pe, = struct.unpack_from('<I', d, 0x3c)
    nsec, = struct.unpack_from('<H', d, pe + 6)
    soh, = struct.unpack_from('<H', d, pe + 20)
    base, = struct.unpack_from('<I', d, pe + 24 + 28)
    secs = []
    for i in range(nsec):
        o = pe + 24 + soh + 40 * i
        vs, va, rs, raw = struct.unpack_from('<4I', d, o + 8)
        secs.append((d[o:o + 8].rstrip(b'\0'), va + base, min(vs, rs), raw))

    def v2o(v):
        for n, va, sz, raw in secs:
            if va <= v < va + sz:
                return raw + v - va
        return None

    def s(v):
        o = v2o(v)
        if o is None:
            return None
        e = d.find(b'\0', o, o + 256)
        t = d[o:e] if e > o else b''
        return t.decode() if t and all(32 < c < 127 for c in t) else None

    text = [x for x in secs if x[0] == b'.text'][0]
    regs = {}
    i, end = text[3], text[3] + text[2]
    while True:
        i = d.find(b'\xb9', i + 1, end)
        if i < 0:
            break
        p = i - 5
        if d[p] != 0x68 or d[i + 5] != 0xe8:
            continue
        name = s(struct.unpack_from('<I', d, p + 1)[0])
        if not name:
            continue
        obj, = struct.unpack_from('<I', d, i + 1)
        for st in range(p - 1, p - 100, -1):  # forward parse of the pushes from the function start
            if d[st - 1] not in (0xcc, 0xc3):
                continue
            args, q = [], st
            while q < p:
                if d[q] == 0x6a:
                    args.append(struct.unpack_from('<b', d, q + 1)[0])
                    q += 2
                elif d[q] == 0x68:
                    args.append(struct.unpack_from('<I', d, q + 1)[0])
                    q += 5
                else:
                    break
            if q == p and len(args) == 11:
                regs[obj] = (name, args[::-1])
                break
    cname = {o: n for o, (n, a) in regs.items()}

    def enum(v):
        o = v2o(v)
        nm, items, n, attr, fl = struct.unpack_from('<5I', d, o)
        io = v2o(items) if n else 0
        return {'name': s(nm), 'flags': fl,
                'items': [[struct.unpack_from('<i', d, io + 8 * k)[0], s(struct.unpack_from('<I', d, io + 8 * k + 4)[0])]
                          for k in range(n)]}
    classes = {}
    for obj, (name, a) in regs.items():
        parent, size, ifp, nif, en, nen, mem, nmem, dfl, attr, flags = a
        if dfl or attr:
            continue
        members = []
        mo = v2o(mem) if nmem else 0
        for k in range(nmem):
            nm, mc, me = struct.unpack_from('<3I', d, mo + 24 * k)
            t, sub, ca, fl, off, ma = struct.unpack_from('<BBhHHI', d, mo + 24 * k + 12)
            members.append({'name': s(nm), 'type': t, 'sub': sub, 'carray': ca, 'flags': fl, 'offset': off,
                            'class': cname.get(mc) if mc else None, 'enum': None, '_enumdef': None})
        classes[name] = {'parent': cname.get(parent) if parent else None, 'size': size, 'interfaces': nif,
                         'enums': [enum(en + 20 * k) for k in range(nen)], 'members': members, 'flags': flags,
                         'version': 0}
    return classes


# ---------------------------------------------------------------------------------------------- reference store
def load_ref(fn=REF):
    if os.path.exists(fn):
        return json.load(open(fn))
    return {'classes': {}, 'enums': {}, 'sources': {}}


def strip(v):
    v = dict(v)
    v['members'] = [{k: x for k, x in m.items() if k != '_enumdef'} for m in v['members']]
    return v


def merge(ref, classes, src):
    """add class layouts (and the enums their members point to) to the store; returns (new, equal, differing)"""
    new = same = diff = 0
    for k, v in classes.items():
        for m in v['members']:
            e = m.get('_enumdef')
            if e:
                if e['name'] in ref['enums'] and ref['enums'][e['name']] != e:
                    print('WARNING: enum %s differs in %s' % (e['name'], src))
                ref['enums'].setdefault(e['name'], e)
        v = strip(v)
        old = ref['classes'].get(k)
        if old is None:
            ref['classes'][k] = v
            ref['sources'][k] = os.path.basename(src)
            new += 1
        elif old == v or same_but_enums(old, v):
            same += 1
        else:
            diff += 1
            print('WARNING: class %s differs in %s (kept the layout from %s)' % (k, src, ref['sources'].get(k)))
    return new, same, diff


def same_but_enums(a, b):
    """equal except member enum pointers missing on one side (exe registry)"""
    if {k: v for k, v in a.items() if k != 'members'} != {k: v for k, v in b.items() if k != 'members'}:
        return False
    if len(a['members']) != len(b['members']):
        return False
    for x, y in zip(a['members'], b['members']):
        if x['enum'] and y['enum'] and x['enum'] != y['enum']:
            return False
        if {k: v for k, v in x.items() if k != 'enum'} != {k: v for k, v in y.items() if k != 'enum'}:
            return False
    return True


def learn(paths, fn=REF):
    ref = load_ref(fn)
    files, exes = [], []
    for p in paths:
        for q in sorted(glob.glob(p)) or [p]:
            if os.path.isdir(q):
                files += sorted(os.path.join(q, n) for n in os.listdir(q))
            elif q.lower().endswith('.exe'):
                exes.append(q)
            else:
                files.append(q)
    tot = [0, 0, 0]
    npk = 0
    for f in files:
        items = ark_havok(f) if f.lower().endswith('.ark') else [(None, open(f, 'rb').read())]
        for key, d in items:
            if d[:8] != MAGIC:
                continue
            pf = Packfile(d)
            if pf.E != '<' or not pf.sec('__types__')[1].data:
                continue
            npk += 1
            r = merge(ref, read_types(pf), f if key is None else '%s:%08x' % (f, key))
            tot = [a + b for a, b in zip(tot, r)]
    if npk:
        print('%d PC packfiles: %d classes added, %d seen again' % (npk, tot[0], tot[1]))
    for e in exes:   # after the packfiles: the packfile reflection has the member enum pointers
        r = merge(ref, read_exe(e), e)
        print('%s: %d classes added, %d equal to the packfile reflection, %d differ' % (e, r[0], r[1], r[2]))
    json.dump(ref, open(fn, 'w'), indent=0, sort_keys=True)
    print('%s: %d classes, %d enums' % (fn, len(ref['classes']), len(ref['enums'])))


# ---------------------------------------------------------------------------------------------- data walk
class Walker:
    """Annotates every byte of a __data__ section with its swap unit and member type."""

    def __init__(self, pf, classes):
        self.pf, self.classes = pf, classes
        self.di, s = pf.sec('__data__')
        self.d = s.data
        self.E = pf.E
        n = len(self.d)
        self.unit = bytearray(n)  # swap unit per byte (0 = not described by any member)
        self.kind = bytearray(n)  # member type per byte (T_REAL for all float types)
        self.ext = bytearray(n)   # 1 = inside an object, array or string (else block padding)
        self.vol = bytearray(n)   # 1 = member known to hold uninitialised memory (VOLATILE)
        self.loc = {a: b for a, b in s.local}
        self.glob = {a: (b, c) for a, b, c in s.globl}
        self.objs = dict(pf.objects())
        self.visited = set()
        self.variants = []    # (offset of the class pointer, object offset)
        self.transforms = []  # offsets of hkTransform members
        self.conflicts = []
        self.missing = set()

    def mark(self, o, n, u, t=T_VOID):
        seg = self.unit[o:o + n]
        if any(seg) and seg != bytes([u]) * n:
            self.conflicts.append((o, n, u))
            return
        self.unit[o:o + n] = bytes([u]) * n
        self.kind[o:o + n] = bytes([T_REAL if t in FLOATS else t]) * n
        self.ext[o:o + n] = b'\1' * n

    def target(self, o):
        if o in self.loc:
            return self.loc[o]
        g = self.glob.get(o)
        if g and g[0] == self.di:
            return g[1]
        return None

    def members(self, cname):
        out = []
        while cname:
            c = self.classes.get(cname)
            if c is None:
                self.missing.add(cname)
                break
            out = [dict(m, owner=cname) for m in c['members']] + out
            cname = c['parent']
        return out

    def size(self, t, sub, cname):
        if t in BASIC:
            return BASIC[t][0]
        if t in (T_ENUM, T_FLAGS):
            return BASIC[sub][0]
        if t == T_STRUCT:
            return self.classes[cname]['size']
        if t in (T_ARRAY, T_HOMOGENEOUSARRAY):
            return 12
        if t in (T_SIMPLEARRAY, T_VARIANT):
            return 8
        if t == T_ZERO:
            return self.size(sub, T_VOID, cname)
        raise ValueError('size of member type %d' % t)

    def run(self):
        for o, cname in sorted(self.objs.items()):
            self.struct(cname, o)
        return self

    def struct(self, cname, o):
        if (cname, o) in self.visited:
            return
        self.visited.add((cname, o))
        c = self.classes.get(cname)
        if c is None:
            self.missing.add(cname)
            return
        self.ext[o:o + c['size']] = b'\1' * c['size']
        root = cname   # vtable pointer of virtual classes (hkBaseObject)
        while self.classes.get(root, {}).get('parent'):
            root = self.classes[root]['parent']
        if self.classes.get(root, {}).get('interfaces'):
            self.mark(o, 4, 4)
        for m in self.members(cname):
            n = m['carray'] or 1
            es = self.size(m['type'], m['sub'], m['class'])
            if m['name'] in VOLATILE.get(m['owner'], ()):
                a, ln = o + m['offset'], (n * es + 3) & ~3
                self.vol[a:a + ln] = b'\1' * ln
            for k in range(n):
                self.value(m['type'], m['sub'], m['class'], o + m['offset'] + k * es)

    def value(self, t, sub, cname, o):
        if t in (T_ENUM, T_FLAGS):
            sz, u = BASIC[sub]
            self.mark(o, sz, u, sub)
        elif t == T_ZERO:
            self.mark(o, self.size(sub, T_VOID, cname), BASIC.get(sub, (0, 4))[1])
        elif t == T_STRUCT:
            self.struct(cname, o)
        elif t == T_POINTER:
            self.mark(o, 4, 4)
            p = self.target(o)
            if p is not None and cname and p not in self.objs:
                self.struct(cname, p)
        elif t == T_CSTRING:
            self.mark(o, 4, 4)
            p = self.target(o)
            if p is not None:
                e = self.d.index(b'\0', p)
                self.mark(p, e - p + 1, 1, T_CHAR)
        elif t in BASIC:
            sz, u = BASIC[t]
            self.mark(o, sz, u, t)
            if t == T_TRANSFORM:
                self.transforms.append(o)
        elif t in (T_ARRAY, T_SIMPLEARRAY):
            self.mark(o, 12 if t == T_ARRAY else 8, 4)
            n, = struct.unpack_from(self.E + 'i', self.d, o + 4)
            p = self.target(o)
            if p is not None and n > 0:
                es = self.size(sub, T_VOID, cname)
                self.ext[p:p + n * es] = b'\1' * (n * es)
                for k in range(n):
                    self.value(sub, T_VOID, cname, p + k * es)
        elif t == T_VARIANT:
            self.mark(o, 8, 4)
            p = self.target(o)
            if p is not None:
                self.variants.append((o + 4, p))
        else:
            raise ValueError('member type %d not supported' % t)

    # object graph, for the class order of __types__
    def children(self, cname, o, out):
        for m in self.members(cname):
            es = self.size(m['type'], m['sub'], m['class'])
            for k in range(m['carray'] or 1):
                self.child(m['type'], m['class'], o + m['offset'] + k * es, out, m['sub'])

    def child(self, t, cname, o, out, sub=T_VOID):
        if t == T_STRUCT:
            self.children(cname, o, out)
        elif t in (T_POINTER, T_VARIANT):
            p = self.target(o)
            if p is not None:
                if p in self.objs:
                    out.append(p)
                elif cname:
                    self.children(cname, p, out)
        elif t in (T_ARRAY, T_SIMPLEARRAY) and sub in (T_POINTER, T_STRUCT, T_VARIANT):
            n, = struct.unpack_from(self.E + 'i', self.d, o + 4)
            p = self.target(o)
            if p is not None and n > 0:
                es = self.size(sub, T_VOID, cname)
                for k in range(n):
                    self.child(sub, cname, p + k * es, out)

    def postorder(self, first):
        """object classes in post-order of a depth-first walk over the object graph from the contents object"""
        done, out = set(), []

        def dfs(o):
            stack = [(o, None)]
            while stack:
                o, ch = stack.pop()
                if ch is None:
                    if o in done:
                        continue
                    done.add(o)
                    ch = []
                    self.children(self.objs[o], o, ch)
                    ch = ch[::-1]
                if ch:
                    c = ch.pop()
                    stack.append((o, ch))
                    if c not in done:
                        stack.append((c, None))
                else:
                    if self.objs[o] not in out:
                        out.append(self.objs[o])
        if first in self.objs:
            dfs(first)
        for o in sorted(self.objs):
            dfs(o)
        return out


def round_floats(data, kind):
    """The PC files went through Havok's XML format, which prints reals with %f (6 decimals): do the same."""
    for a in range(0, len(data) - 3, 4):
        if kind[a] == T_REAL:
            v, = struct.unpack_from('<f', data, a)
            if v == v and abs(v) < 1e30:
                struct.pack_into('<f', data, a, float('%f' % v))


def swapped(d, unit):
    out = bytearray(d)
    i, n = 0, len(d)
    while i < n:
        u = unit[i]
        if u > 1:
            out[i:i + u] = d[i:i + u][::-1]
            i += u
        else:
            i += 1
    return out


def swap4(b):
    a = bytearray(b)
    for i in range(0, len(a) - 3, 4):
        a[i:i + 4] = a[i:i + 4][::-1]
    return bytes(a)


def table(entries, n):
    out = bytearray()
    for e in entries:
        out += struct.pack('<%di' % n, *e)
    return bytes(out + b'\xff' * (-len(out) % 16))


# ---------------------------------------------------------------------------------------------- __types__ build
class TypesWriter:
    """Builds a PC __types__ section in the PC writer's layout (verified byte for byte on 24 distinct PC files)."""

    def __init__(self, classes, enums, cn_offsets):
        self.classes, self.enums, self.cn = classes, enums, cn_offsets
        self.buf = bytearray()
        self.local, self.virtual, self.gpend = [], [], []
        self.at, self.enum_at = {}, {}

    def order(self, roots):
        """depth-first: class, its parent chain, then per member its standalone enum and its class"""
        out = []

        def visit(n):
            if n is None or ('c', n) in out:
                return
            if n not in self.classes:
                raise KeyError('no layout for class %s' % n)
            out.append(('c', n))
            c = self.classes[n]
            visit(c['parent'])
            for m in c['members']:
                if m.get('enum') and m['enum'] in self.enums and ('e', m['enum']) not in out:
                    out.append(('e', m['enum']))
                if m['class']:
                    visit(m['class'])
        for r in roots:
            visit(r)
        return out

    def pad(self):
        self.buf += bytes([PAD]) * (-len(self.buf) % 16)

    def string(self, src, s):
        self.local.append((src, len(self.buf)))
        self.buf += s.encode('latin-1') + b'\0'
        self.pad()

    def enum_record(self, o, e):
        struct.pack_into('<iiiiI', self.buf, o, 0, 0, len(e['items']), 0, e['flags'])

    def enum_body(self, o, e):
        self.string(o, e['name'])
        if e['items']:
            io = len(self.buf)
            self.local.append((o + 4, io))
            self.buf += b'\0' * (8 * len(e['items']))
            for k, (v, nm) in enumerate(e['items']):
                struct.pack_into('<i', self.buf, io + 8 * k, v)
            self.pad()
            for k, (v, nm) in enumerate(e['items']):
                self.string(io + 8 * k + 4, nm)

    def write_class(self, n):
        c = self.classes[n]
        o = len(self.buf)
        self.at[n] = o
        self.virtual.append((o, 'hkClass'))
        self.buf += b'\0' * 48
        struct.pack_into('<iiiiiiiiiiIi', self.buf, o, 0, 0, c['size'], c['interfaces'], 0, len(c['enums']), 0,
                         len(c['members']), 0, 0, c['flags'], c['version'])
        if c['parent']:
            self.gpend.append((o + 4, ('c', c['parent'])))
        self.string(o, n)
        if c['enums']:   # declared enums: the array is not padded, its strings and item arrays are
            eo = len(self.buf)
            self.local.append((o + 16, eo))
            self.buf += b'\0' * (20 * len(c['enums']))
            for k, e in enumerate(c['enums']):
                self.enum_record(eo + 20 * k, e)
            for k, e in enumerate(c['enums']):
                self.enum_body(eo + 20 * k, e)
        if c['members']:
            mo = len(self.buf)
            self.local.append((o + 24, mo))
            self.buf += b'\0' * (24 * len(c['members']))
            for k, m in enumerate(c['members']):
                r = mo + 24 * k
                struct.pack_into('<iiiBBhHHi', self.buf, r, 0, 0, 0, m['type'], m['sub'], m['carray'], m['flags'],
                                 m['offset'], 0)
                if m['class']:
                    self.gpend.append((r + 4, ('c', m['class'])))
                if m.get('enum') and m['enum'] in self.enums:
                    self.gpend.append((r + 8, ('e', m['enum'])))
            for k, m in enumerate(c['members']):
                self.string(mo + 24 * k, m['name'])

    def write_enum(self, n):
        e = self.enums[n]
        o = len(self.buf)
        self.enum_at[n] = o
        self.virtual.append((o, 'hkClassEnum'))
        self.buf += b'\0' * 20
        self.enum_record(o, e)
        self.pad()
        self.enum_body(o, e)

    def build(self, roots, sec_index=1):
        for kind, n in self.order(roots):
            (self.write_class if kind == 'c' else self.write_enum)(n)
        self.pad()
        g = sorted((src, sec_index, self.at[n] if kind == 'c' else self.enum_at[n]) for src, (kind, n) in self.gpend)
        loc = sorted(self.local, key=lambda x: x[1])
        v = [(o, 0, self.cn[k]) for o, k in self.virtual]
        data = bytes(self.buf)
        lt, gt, vt = table(loc, 2), table(g, 3), table(v, 3)
        raw = data + lt + gt + vt
        offs = (len(data), len(data) + len(lt), len(data) + len(lt) + len(gt), len(raw), len(raw), len(raw))
        return raw, offs, dict(self.at)


# ---------------------------------------------------------------------------------------------- conversion
def convert(xd, ref=None, report=None, xml_round=True):
    """Xbox 360 packfile bytes -> PC packfile bytes"""
    if ref is None:
        ref = load_ref()
    pf = Packfile(xd)
    if pf.E != '>':
        raise ValueError('not a big-endian (Xbox 360) packfile')
    if pf.rules != b'\x04\x00\x00\x01' or [s.name for s in pf.secs] != ['__classnames__', '__types__', '__data__']:
        raise ValueError('unexpected layout rules / sections: %s %s' % (pf.rules.hex(), [s.name for s in pf.secs]))
    cs, xs, ds = pf.secs
    if xs.end or ds.exp != ds.end or cs.loc != cs.end:
        raise ValueError('unexpected section contents (Xbox __types__ not empty, or exports/imports present)')
    classes = ref['classes']
    cns = pf.classnames()
    unknown = [n for _, _, n in cns if n not in classes and n not in META]
    # __data__ walk
    w = Walker(pf, classes).run()
    if w.missing:
        raise ValueError('no layout for classes %s (learn them from a PC file or the PC exe)' % sorted(w.missing))
    holes = sum(1 for i in range(len(w.d)) if w.ext[i] and not w.unit[i] and w.d[i])
    # __classnames__: swap the signatures
    cdata = bytearray(cs.data)
    for o, sig, n in cns:
        struct.pack_into('<I', cdata, o - 5, sig)
    craw = bytes(cdata)
    # __types__
    cn = {n: o for o, _, n in cns}
    if 'hkClass' not in cn or 'hkClassEnum' not in cn:
        raise ValueError('hkClass/hkClassEnum missing from __classnames__')
    roots = w.postorder(pf.contents_off if pf.contents_sec == 2 else None)
    traw, toffs, tclass = TypesWriter(classes, ref.get('enums', {}), cn).build(roots)
    # __data__ bytes
    data = swapped(ds.data, w.unit)
    for i, e in enumerate(w.ext):
        if not e:
            data[i] = PAD
    if xml_round:
        round_floats(data, w.kind)
        for i, k in enumerate(w.kind):
            if k == T_BOOL and data[i] > 1:
                data[i] = 1  # XML stores true/false
        for o in w.transforms:
            # the XML form stores 3 numbers per column; reading it back gives w = 0 for the three rotation columns
            # and translation.w = 1 (Graveyard: the Xbox hkMotionState.transform columns hold leftover SIMD lanes
            # in w, e.g. col1.w = col2.x; every PC copy has 0 there)
            for c in (0x0C, 0x1C, 0x2C):
                struct.pack_into('<f', data, o + c, 0.0)
            struct.pack_into('<f', data, o + 0x3c, 1.0)
    loc = swap4(ds.raw[ds.loc:ds.glob])
    g = list(ds.globl)
    for co, obj in w.variants:
        g.append((co, 1, tclass[w.objs[obj]]))
    g.sort()
    glob_ = table(g, 3)  # the Xbox writer sometimes reserves more space than it uses; the PC table is just aligned
    virt = swap4(ds.raw[ds.virt:ds.exp])
    draw = bytes(data) + loc + glob_ + virt
    doffs = (ds.loc, ds.glob, ds.glob + len(glob_), len(draw), len(draw), len(draw))
    # header and section headers
    out = bytearray(xd[:0x40 + 0x30 * 3])
    struct.pack_into('<iI', out, 8, pf.user_tag, pf.version)
    out[0x10:0x14] = b'\x04\x01\x00\x01'
    struct.pack_into('<5i', out, 0x14, 3, pf.contents_sec, pf.contents_off, pf.cn_sec, pf.cn_off)
    struct.pack_into('<I', out, 0x38, pf.flags)
    pos, body = len(out), bytearray()
    for i, (raw, offs) in enumerate([(craw, (len(craw),) * 6), (traw, toffs), (draw, doffs)]):
        struct.pack_into('<7I', out, 0x40 + 0x30 * i + 20, pos, *offs)
        body += raw
        pos += len(raw)
    if report is not None:
        report.update({'unit': w.unit, 'kind': w.kind, 'ext': w.ext, 'vol': w.vol, 'conflicts': w.conflicts,
                       'unknown': unknown, 'holes': holes, 'objects': len(w.objs), 'roots': roots,
                       'types_classes': len(tclass)})
    return bytes(out + body)


def convert_file(src, dst, ref, xml_round=True):
    rep = {}
    out = convert(open(src, 'rb').read(), ref, rep, xml_round)
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    u = rep['unit']
    print('%s -> %s: %d bytes, %d objects, %d classes in __types__, data described %.1f %%%s%s' % (
        src, dst, len(out), rep['objects'], rep['types_classes'], 100.0 * sum(1 for x in u if x) / max(1, len(u)),
        ', %d CONFLICTS' % len(rep['conflicts']) if rep['conflicts'] else '',
        ', WARNING: %d non-zero bytes not described by any member (left unswapped)' % rep['holes']
        if rep['holes'] else ''))
    return rep


def main(a):
    exact = '--exact' in a
    a = [x for x in a if x != '--exact']
    if not a:
        print(__doc__)
    elif a[0] == 'learn':
        learn(a[1:])
    elif a[0] == 'convert':
        ref = load_ref()
        if not ref['classes']:
            sys.exit('hkref.json is empty: run "learn" first')
        srcs, dst = a[1:-1], a[-1]
        if len(srcs) == 1 and os.path.isfile(srcs[0]) and not os.path.isdir(dst):
            convert_file(srcs[0], dst, ref, not exact)
            return
        for s in srcs:
            files = [os.path.join(dp, n) for dp, dn, fn in os.walk(s) for n in fn] if os.path.isdir(s) else [s]
            for f in sorted(files):
                with open(f, 'rb') as fh:
                    if fh.read(8) != MAGIC:
                        continue
                rel = os.path.relpath(f, s) if os.path.isdir(s) else os.path.basename(f)
                convert_file(f, os.path.join(dst, rel), ref, not exact)
    elif a[0] == 'classes':
        pf = Packfile(open(a[1], 'rb').read())
        for o, s, n in pf.classnames():
            print('%08x %s' % (s, n))
        for o, n in pf.objects():
            print('  %6x %s' % (o, n))
    else:
        print(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
