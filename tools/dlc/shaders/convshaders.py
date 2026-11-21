# Split/Second shader files, Xbox 360 -> PC.
#
#   .fxc        ('\0FXC' / 'CXF\0')  compiled shader pairs (Xenos microcode on the Xbox, D3D9 bytecode on the PC)
#   .shaders    ('SHDR' / 'RDHS')    per-shader metadata (constant/sampler register maps, vertex inputs)
#   .shaderinst ('SDRI' / 'IRDS')    material instances (constant values, textures, sampler states)
#
# All three are streams of u32 and strings (u32 length incl. NUL, bytes, then (length & 3) zero bytes;
# the padding is length & 3, not the usual 4-aligning amount). Xbox = big endian, PC = little endian.
#
# .fxc / .shaders: per Xbox entry "0x<id>" the PC file holds the PC variants of that shader in the Xbox
# order: "0x<id>", "2x<id>" (most shaders without deformation), "0x".."4x<id>" (most with the vertex
# input "indices", DeformVertexPosition). The variant list per ID is the same in every PC file.
# .shaders metadata and .fxc constant tables are platform independent (taken from the Xbox file); only
# the shader code comes from the PC data: the entry of the same name in any PC .fxc (the code is the same
# in every PC file that has it). .shaderinst converts by structure.
#
# Usage:
#   python -I convshaders.py <out dir> <xbox car dir> [<xbox car dir> ...] [--base-only]
#       converts every *.fxc/*.shaders/*.shaderinst of each Xbox car folder (DLC layout, any case) into
#       <out dir>/<car name lower case>/, file names lower case. The entry pool is the PC base car bodies
#       plus (unless --base-only) every other .fxc/.shaders file in the PC resident.ark.
#       --translate: entry names the pool lacks are translated from the Xbox microcode (../xenos/xenos2pc.py,
#       with the vertex inputs of the .fxc's sibling .shaders file) instead of being left out.
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

CARS = (['musclecar_%02d' % n for n in range(1, 17)] + ['supercar_%02d' % n for n in range(1, 17)] +
        ['truck_%02d' % n for n in range(1, 9)])
LODS = ['_xlow', '_low', '_mid', '_high', '_bw_high', '_bw_mid', '_bw_low', '_fw_high', '_fw_mid', '_fw_low']
PC_PREFIXES = ('0x', '2x')
DEFORM_PREFIXES = ('0x', '1x', '2x', '3x', '4x')


class Reader:
    def __init__(self, data, E):
        self.d, self.E, self.p = data, E, 0

    def u32(self):
        v = struct.unpack_from(self.E + 'I', self.d, self.p)[0]
        self.p += 4
        return v

    def tag(self):
        t = self.d[self.p:self.p + 4]
        self.p += 4
        return t if self.E == '>' else t[::-1]

    def str(self):
        n = self.u32()
        t = self.d[self.p:self.p + n]
        if n == 0 or len(t) != n or t[-1] != 0 or self.d[self.p + n:self.p + n + (n & 3)] != b'\0' * (n & 3):
            raise ValueError('bad string at 0x%x' % (self.p - 4))
        self.p += n + (n & 3)
        return t[:-1].decode('latin-1')

    def raw(self, n):
        t = self.d[self.p:self.p + n]
        if len(t) != n:
            raise ValueError('short read at 0x%x' % self.p)
        self.p += n
        return t


class Writer:
    def __init__(self, E):
        self.E, self.b = E, bytearray()

    def u32(self, v):
        self.b += struct.pack(self.E + 'I', v)

    def tag(self, t):
        self.b += t if self.E == '>' else t[::-1]

    def str(self, s):
        t = s.encode('latin-1') + b'\0'
        self.u32(len(t))
        self.b += t + b'\0' * (len(t) & 3)

    def raw(self, t):
        self.b += t


def endian(data, be_magic):
    if data[:4] == be_magic:
        return '>'
    if data[:4] == be_magic[::-1]:
        return '<'
    raise ValueError('magic %r' % data[:4])


# ---------------------------------------------------------------------------------------------- .fxc
# u32 magic '\0FXC', u32 version 2, u32 flags 0x2060, u32 count
# count x entry: str id; 2 x (u32 n; n x (str name, u32 class, u32 register, u32 count); u32 size; code)
#                (vertex shader, then pixel shader)
# u32 '\0END'
FXC_MAGIC = b'\0FXC'
END = b'\0END'


def parse_fxc(data):
    E = endian(data, FXC_MAGIC)
    r = Reader(data, E)
    r.tag()
    ver, flags, cnt = r.u32(), r.u32(), r.u32()
    ents = []
    for _ in range(cnt):
        st = r.p
        ident = r.str()
        shaders = []
        for _ in range(2):
            consts = [(r.str(), r.u32(), r.u32(), r.u32()) for _ in range(r.u32())]
            code = r.raw(r.u32())
            shaders.append((consts, code))
        ents.append({'id': ident, 'raw': data[st:r.p], 'shaders': shaders})
    if r.tag() != END or r.p != len(data):
        raise ValueError('fxc trailer')
    return {'E': E, 'ver': ver, 'flags': flags, 'ents': ents}


def build_fxc(f, E='<'):
    w = Writer(E)
    w.tag(FXC_MAGIC)
    w.u32(f['ver'])
    w.u32(f['flags'])
    w.u32(len(f['ents']))
    for e in f['ents']:
        if E == f.get('E') and 'raw' in e:
            w.raw(e['raw'])
            continue
        w.str(e['id'])
        for consts, code in e['shaders']:
            w.u32(len(consts))
            for c in consts:
                w.str(c[0])
                w.u32(c[1])
                w.u32(c[2])
                w.u32(c[3])
            w.u32(len(code))
            w.raw(code)
    w.tag(END)
    return bytes(w.b)


# ------------------------------------------------------------------------------------------ .shaders
# u32 magic 'SHDR', u32 version 6, u32 count
# count x entry: str id; 'HDRB'
#   4 x constant group (u32 n, u32 registers used, n x (u32 register, u32 count, str name)):
#       vertex shader global, vertex shader material, pixel shader global, pixel shader material
#   4 x sampler group (u32 n, n x (u32 register, str name)), same order
#   (the two material groups are the ones a .shaderinst instance fills)
#   u32 n, n x vertex input (str name, u32 type, u32 usage, u32 usage index)
#   'HDRE'; 2 x ('SDRS', u32 size, size bytes, 'SDRE')  (always empty in the car files)
# u32 '\0END'
SHDR_MAGIC = b'SHDR'


def parse_shdr(data):
    E = endian(data, SHDR_MAGIC)
    r = Reader(data, E)
    r.tag()
    ver, cnt = r.u32(), r.u32()
    ents = []
    for _ in range(cnt):
        st = r.p
        ident = r.str()
        if r.tag() != b'HDRB':
            raise ValueError('HDRB at 0x%x' % r.p)
        cg = []
        for _ in range(4):
            n, used = r.u32(), r.u32()
            cg.append((used, [(r.u32(), r.u32(), r.str()) for _ in range(n)]))
        sg = [[(r.u32(), r.str()) for _ in range(r.u32())] for _ in range(4)]
        vin = [(r.str(), r.u32(), r.u32(), r.u32()) for _ in range(r.u32())]
        if r.tag() != b'HDRE':
            raise ValueError('HDRE at 0x%x' % r.p)
        blobs = []
        for _ in range(2):
            if r.tag() != b'SDRS':
                raise ValueError('SDRS at 0x%x' % r.p)
            blobs.append(r.raw(r.u32()))
            if r.tag() != b'SDRE':
                raise ValueError('SDRE at 0x%x' % r.p)
        ents.append({'id': ident, 'raw': data[st:r.p], 'cg': cg, 'sg': sg, 'vin': vin, 'blobs': blobs})
    if r.tag() != END or r.p != len(data):
        raise ValueError('shaders trailer')
    return {'E': E, 'ver': ver, 'ents': ents}


def build_shdr(f, E='<'):
    w = Writer(E)
    w.tag(SHDR_MAGIC)
    w.u32(f['ver'])
    w.u32(len(f['ents']))
    for e in f['ents']:
        if E == f.get('E') and 'raw' in e:
            w.raw(e['raw'])
            continue
        w.str(e['id'])
        w.tag(b'HDRB')
        for used, items in e['cg']:
            w.u32(len(items))
            w.u32(used)
            for reg, n, name in items:
                w.u32(reg)
                w.u32(n)
                w.str(name)
        for items in e['sg']:
            w.u32(len(items))
            for reg, name in items:
                w.u32(reg)
                w.str(name)
        w.u32(len(e['vin']))
        for name, a, b, c in e['vin']:
            w.str(name)
            w.u32(a)
            w.u32(b)
            w.u32(c)
        w.tag(b'HDRE')
        for blob in e['blobs']:
            w.tag(b'SDRS')
            w.u32(len(blob))
            w.raw(blob)
            w.tag(b'SDRE')
    w.tag(END)
    return bytes(w.b)


def shdr_meta(e):
    """Platform independent content of a .shaders entry (for comparing Xbox and PC entries)."""
    return (e['cg'], e['sg'], e['vin'], e['blobs'])


# --------------------------------------------------------------------------------------- .shaderinst
# u32 magic 'SDRI', u32 version 8, 'INSS', u32 count
# count x instance: u32 hash, str name, str shader id,
#   2 x constant group (u32 n, u32 registers used, n x (str name, u32 class, u32 register, u32 count,
#                       count x 4 floats))
#   2 x texture group (u32 n, n x (str name, u32 class, u32 register, u32 texture hash, u32 m,
#                      m x (u32 sampler state, u32 value)))
# 'INSE', optionally 'SMAP' and u32 words only (u32 1, u32 n, u32, n x (hash, u32, u32, u32))
SDRI_MAGIC = b'SDRI'


def convert_sdri(data, E_out='<'):
    """Re-encode a .shaderinst in the other byte order, walking its structure (strings stay as bytes)."""
    E = endian(data, SDRI_MAGIC)
    r, w = Reader(data, E), Writer(E_out)

    def u():
        v = r.u32()
        w.u32(v)
        return v

    def s():
        w.str(r.str())

    def t(expect):
        x = r.tag()
        if x != expect:
            raise ValueError('%r at 0x%x' % (expect, r.p - 4))
        w.tag(x)

    t(SDRI_MAGIC)
    u()
    t(b'INSS')
    for _ in range(u()):
        u()
        s()
        s()
        for _ in range(2):
            n = u()
            u()
            for _ in range(n):
                s()
                u()
                u()
                for _ in range(4 * u()):
                    u()
        for _ in range(2):
            for _ in range(u()):
                s()
                u()
                u()
                u()
                for _ in range(2 * u()):
                    u()
    t(b'INSE')
    if r.p == len(data):     # files without a shader map end here
        return bytes(w.b)
    t(b'SMAP')
    if (len(data) - r.p) % 4:
        raise ValueError('SMAP size')
    while r.p < len(data):
        u()
    return bytes(w.b)


def sdri_shader_ids(data):
    E = endian(data, SDRI_MAGIC)
    r = Reader(data, E)
    r.raw(12)
    ids = []
    for _ in range(r.u32()):
        r.u32()
        r.str()
        ids.append(r.str())
        for _ in range(2):
            n = r.u32()
            r.u32()
            for _ in range(n):
                r.str()
                r.u32()
                r.u32()
                r.raw(16 * r.u32())
        for _ in range(2):
            for _ in range(r.u32()):
                r.str()
                r.raw(12)
                r.raw(8 * r.u32())
    return ids


# ------------------------------------------------------------------------------- PC entry pool, build
def scan_ark(ark, magics):
    """Yield (key, magic, data) for every archive entry whose first 4 bytes are one of magics."""
    import pair_lib
    for k, (t, off, cs, us) in sorted(ark.ents.items()):
        if t not in (0, 1) or us < 8:
            continue
        ark.f.seek(off)
        raw = ark.f.read(min(cs, 64) + 16)
        head = raw[:4] if t == 0 else pair_lib.lzss_n(raw + b'\0' * 16, 4, 4)[0]
        if head not in magics:
            continue
        ark.f.seek(off)
        raw = ark.f.read(cs + 16)
        yield k, head, raw[:us] if t == 0 else pair_lib.lzss_n(raw + b'\0' * 16, 4, us)[0]


class Pool:
    """PC .fxc/.shaders entries by entry name ("0x..." .. "4x..."): the PC base car bodies first, then
    optionally every other PC .fxc/.shaders file of the archive (add_ark_scan)."""

    def __init__(self, exclude=(), ark=None, cache=None):
        """exclude: car names to leave out; ark: PC resident.ark (pair_lib.Ark); cache: optional dict
        path -> parsed file, shared between pools (used by the leave-one-out test)."""
        cache = {} if cache is None else cache
        if ark is None and not cache:
            from pair_lib import Ark, PC
            ark = Ark(PC)
        self.ark = ark
        self.fxc, self.shdr, self.src = {}, {}, {}
        self.variants = {}      # hash -> tuple of PC entry names in file order, e.g. ('0x..', '2x..')
        self.fxc_hdr = None
        self.conflicts = []     # same entry name, different bytes, or different variant lists
        for car in CARS:
            if car in exclude:
                continue
            for lod in LODS:
                base = 'vehicles/bodies/%s/%s%s' % (car, car, lod)
                for ext, parse in (('.fxc', parse_fxc), ('.shaders', parse_shdr)):
                    if base + ext not in cache:
                        data = ark.get(base + ext)
                        cache[base + ext] = parse(data) if data else None
                    if cache[base + ext] is not None:
                        self._add(ext, cache[base + ext], car + lod)

    def _add(self, ext, f, src):
        """Add a parsed file's entries; returns the number of new entry names."""
        if ext == '.fxc':
            self.fxc_hdr = self.fxc_hdr or (f['ver'], f['flags'])
        table = self.fxc if ext == '.fxc' else self.shdr
        groups = {}
        for e in f['ents']:
            groups.setdefault(e['id'][2:], []).append(e['id'])
        added = 0
        for e in f['ents']:
            old = table.get(e['id'])
            if old is None:
                table[e['id']] = e
                self.src[(ext, e['id'])] = src
                added += 1
            elif old['raw'] != e['raw']:
                # .fxc: the code must agree (constant tables differ by context, the Xbox file's are used);
                # .shaders: only the Xbox metadata is used, differences are harmless
                if ext == '.fxc' and [s[1] for s in old['shaders']] != [s[1] for s in e['shaders']]:
                    self.conflicts.append((ext, e['id'], self.src[(ext, e['id'])], src))
        for h, names in groups.items():
            old = self.variants.setdefault(h, tuple(names))
            if old != tuple(names):
                self.conflicts.append((ext, 'variants ' + h, old, src))
        return added

    def add_ark_scan(self, ark=None, skip_keys=(), cache=None):
        """Add the entries of every .fxc/.shaders file in the archive found by magic (names unknown, only
        keys); entries already in the pool keep priority, differences are recorded in conflicts.
        skip_keys: archive keys to leave out; cache: dict key -> (ext, parsed), shared between pools."""
        ark = ark or self.ark
        cache = {} if cache is None else cache
        if not cache:
            for k, magic, data in scan_ark(ark, (FXC_MAGIC[::-1], SHDR_MAGIC[::-1])):
                ext = '.fxc' if magic == FXC_MAGIC[::-1] else '.shaders'
                cache[k] = (ext, (parse_fxc if ext == '.fxc' else parse_shdr)(data))
        added = 0
        for k in sorted(cache):
            if k not in skip_keys:
                added += self._add(cache[k][0], cache[k][1], 'key 0x%08x' % k)
        return added

    def pc_names(self, e):
        """PC entry names for an Xbox entry: the variant list the PC files have for that ID; for an ID
        they lack, the usual rule (0x..4x with the vertex input "indices", else 0x 2x). Returns
        (names, known)."""
        i = e['id']
        if not i.startswith('0x'):
            raise ValueError('unexpected Xbox id ' + i)
        if i[2:] in self.variants:
            return self.variants[i[2:]], True
        deform = any(v[0] == 'indices' for v in e['vin']) if 'vin' in e else None
        if deform is None:   # .fxc entry: vertex shader constant names tell
            deform = any(c[0].startswith('DeformVertexPosition') for c in e['shaders'][0][0])
        return tuple(p + i[2:] for p in (DEFORM_PREFIXES if deform else PC_PREFIXES)), False


_XLATE = {}     # (Xbox entry bytes, vin, variant names) -> (PC entries by name, status), see translate_entry


def translate_entry(e, vin, names):
    """PC entries for an Xbox .fxc entry by translating its Xenos microcode (../xenos/xenos2pc.py): the
    Xbox constant tables plus translated vs_3_0/ps_3_0 code, one entry per name in names. vin: the vertex
    input list of the same ID from the .shaders file (vertex decode rule; None if unknown). Cached per
    entry, so an ID shared by many files is translated once. Returns ({name: entry}, status, problems)."""
    k = (e['raw'], None if vin is None else tuple(vin), tuple(names))
    if k not in _XLATE:
        xdir = os.path.join(os.path.dirname(HERE), 'xenos')
        if xdir not in sys.path:
            sys.path.insert(0, xdir)
        import xenos2pc
        ents, st = xenos2pc.pc_entries(e, vin=vin, variants=tuple(names))
        probs = ['translation of %s: %s %s' % (e['id'], what, st.get(what, '-')) for what in ('vs', 'ps', 'pipeline')
                 if st.get(what) != 'ok']
        _XLATE[k] = ({x['id']: x for x in ents}, st, probs)
    return _XLATE[k]


def convert_fxc(pool, data, shaders=None, translate=False, notes=None):
    """Xbox .fxc -> (PC bytes, problems). Per variant: the Xbox entry's constant tables (identical to the
    PC ones on all base cars) with the PC code of the same entry name from the pool.
    translate=True: an entry name the pool lacks is made by translating the Xbox microcode (translate_entry)
    instead of being left out. shaders: the matching Xbox .shaders file (bytes or parse_shdr result), for the
    vertex input lists; for an ID unknown to the pool its variant list then follows the .shaders rule, as in
    convert_shdr. notes (list): gets one line per translated ID. Without translate the result is the same as
    before the translator existed."""
    f = parse_fxc(data)
    vins = {}
    if shaders is not None:
        sf = shaders if isinstance(shaders, dict) else parse_shdr(shaders)
        vins = {x['id']: x['vin'] for x in sf['ents']}
    ents, probs = [], []
    for e in f['ents']:
        names, known = pool.pc_names(e)
        if translate and not known and e['id'] in vins:
            names, known = pool.pc_names({'id': e['id'], 'vin': vins[e['id']]})
        tr = None
        for n in names:
            pe = pool.fxc.get(n)
            if pe is None and translate:
                if tr is None:
                    tr = translate_entry(e, vins.get(e['id']), names)
                    probs += tr[2]
                    if notes is not None:
                        notes.append('translated %s (%s), vs %s, ps %s, pipeline %s, vertex decode %s' % (
                            e['id'], '/'.join(x[:2] for x in names), tr[1].get('vs'), tr[1].get('ps'),
                            tr[1].get('pipeline', '-'), tr[1].get('vin_decode') or '-'))
                te = tr[0].get(n)
                if te is not None:
                    ents.append({'id': n, 'shaders': te['shaders']})
                    continue
            if pe is None:
                probs.append('missing fxc entry ' + n)
                continue
            ents.append({'id': n, 'shaders': [(xs[0], ps[1]) for xs, ps in zip(e['shaders'], pe['shaders'])]})
    return build_fxc({'ver': f['ver'], 'flags': f['flags'], 'ents': ents}), probs


def convert_shdr(pool, data):
    """Xbox .shaders -> (PC bytes, problems). The metadata is platform independent: each Xbox entry is
    re-encoded once per PC variant name; the pool only supplies the variant list."""
    f = parse_shdr(data)
    ents, probs = [], []
    for e in f['ents']:
        names, known = pool.pc_names(e)
        if not known:
            probs.append('variant list guessed for ' + e['id'])
        for n in names:
            ents.append({k: v for k, v in e.items() if k != 'raw'} | {'id': n})
    return build_shdr({'ver': f['ver'], 'ents': ents}), probs


def convert_file(pool, name, data, shaders=None, translate=False, notes=None):
    """Xbox file -> (PC bytes, list of problems). shaders/translate/notes: see convert_fxc (.fxc only)."""
    ext = os.path.splitext(name)[1].lower()
    if ext == '.shaderinst':
        return convert_sdri(data), []
    if ext == '.fxc':
        return convert_fxc(pool, data, shaders, translate, notes)
    if ext == '.shaders':
        return convert_shdr(pool, data)
    raise ValueError(name)


def main(argv):
    args = [a for a in argv if not a.startswith('--')]
    if len(args) < 2:
        print('usage: convshaders.py <out dir> <xbox car dir> [<xbox car dir> ...] [--base-only] [--translate]')
        return 2
    from pair_lib import Ark, PC
    ark = Ark(PC)
    pool = Pool(ark=ark)
    if '--base-only' not in argv:
        n = pool.add_ark_scan(ark)
        print('pool: %d fxc / %d shaders entries (%d from other PC shader files), %d conflicts'
              % (len(pool.fxc), len(pool.shdr), n, len(pool.conflicts)))
    bad = 0
    translate = '--translate' in argv
    for src in args[1:]:
        dst = os.path.join(args[0], os.path.basename(os.path.normpath(src)).lower())
        os.makedirs(dst, exist_ok=True)
        files = {fn.lower(): fn for fn in os.listdir(src)}
        for fn in sorted(os.listdir(src)):
            if os.path.splitext(fn)[1].lower() not in ('.fxc', '.shaders', '.shaderinst'):
                continue
            shd, notes = None, []
            if translate and fn.lower().endswith('.fxc'):
                sib = files.get(fn.lower()[:-4] + '.shaders')
                shd = open(os.path.join(src, sib), 'rb').read() if sib else None
            out, probs = convert_file(pool, fn, open(os.path.join(src, fn), 'rb').read(), shd, translate, notes)
            open(os.path.join(dst, fn.lower()), 'wb').write(out)
            print('%-40s %8d bytes%s' % (fn.lower(), len(out), '' if not probs else '  ' + '; '.join(probs)))
            for n in notes:
                print('    ' + n)
            bad += bool(probs)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
