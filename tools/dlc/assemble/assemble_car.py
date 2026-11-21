# Assemble everything the PC game needs for one Xbox 360 DLC car (or several cars of a pack) into one PC overlay
# archive (.ark), using the existing converters (none of them is changed here). Offline only.
#
#   python -I assemble_car.py [--model Musclecar_17[,Musclecar_18,...]] [--dlc <Deadline package dir>]
#                             [--pc <PC resident.ark>] [--out <overlay .ark>]
#
# Several models: one overlay with all their files, and the merged text files (VehicleDatabase.xml,
# VehicleColors.params, GameCompositingInfo.params) hold all of them (default out: overlay\deadline_cars.ark).
# Writes <out>, <out minus .ark>.manifest.tsv (one line per entry: key, size, replaces/new, converter, game
# path, source) and <out minus .ark>.report.txt (checks, merges, skipped and missing files). See README.md.
import sys
sys.dont_write_bytecode = True

import math
import os
import re
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
DLC_TOOLS = os.path.dirname(HERE)
for _p in ('misc', 'tex', 'havok', 'shaders', 'audio', 'inject', ''):
    _d = os.path.join(DLC_TOOLS, _p) if _p else DLC_TOOLS
    if _d not in sys.path:
        sys.path.insert(0, _d)

import arkio          # misc/arkio.py: .ark reader / writer, key
import convmisc       # misc/convmisc.py: .deform, .parts, text, .rdata (swap4), detect()
import convtex        # tex/convtex.py
import convhkx        # havok/convhkx.py
import convshaders    # shaders/convshaders.py
import convmodel      # convmodel.py
import tonic          # audio/tonic.py
import dlcmerge       # inject/dlcmerge.py: merge_vehicles
import vark           # inject/vark.py: virtual resident.ark (read-back check)
from mdl import Model

# development: the defaults of a manual run below this folder (set GLIMPSE_DLC_SCRATCH); without it --dlc and --out
# must be given (build_pack.py always gives them)
SCRATCH = os.environ.get('GLIMPSE_DLC_SCRATCH', '')
DEF_DLC = os.path.join(SCRATCH, 'dlc', 'x_deadline') if SCRATCH else None
# the PC resident.ark of a manual run: below the game folder in GAME_DIR, else --pc must be given
GAME_DIR = os.environ.get('GAME_DIR', '')
DEF_PC = os.path.join(GAME_DIR, 'data', 'resident.ark') if GAME_DIR else None
DEF_OUT = os.path.join(SCRATCH, 'dlc', 'overlay', 'musclecar_17.ark') if SCRATCH else None

LODS = ('_xlow', '_low', '_mid', '_high', '_bw_high', '_bw_mid', '_bw_low', '_fw_high', '_fw_mid', '_fw_low')


class Fail(Exception):
    pass


# ------------------------------------------------------------------------------------------------- helpers
def gpath(dlc, full):
    """game path of a DLC file: path relative to the package folder, lower case, '/'"""
    return os.path.relpath(full, dlc).replace(os.sep, '/').lower()


def srel(dlc, full):
    """source file as written in the manifest: path relative to the DLC package folder"""
    return 'DLC:' + os.path.relpath(full, dlc).replace(os.sep, '/')


def find_ci(dlc, rel):
    """the DLC file for a game path (the package's names are upper case; match case-insensitively)"""
    cur = dlc
    for part in rel.replace('\\', '/').split('/'):
        if not os.path.isdir(cur):
            return None
        names = {n.lower(): n for n in os.listdir(cur)}
        if part.lower() not in names:
            return None
        cur = os.path.join(cur, names[part.lower()])
    return cur if os.path.isfile(cur) else None


def read(fn):
    with open(fn, 'rb') as f:
        return f.read()


def line_endings(b):
    crlf = b.count(b'\r\n')
    return crlf, b.count(b'\n') - crlf, b.count(b'\r') - crlf


def sdri_textures(data):
    """(instance, sampler name, texture hash) of every texture slot of a .shaderinst (structure as in
    convshaders.convert_sdri)"""
    E = convshaders.endian(data, convshaders.SDRI_MAGIC)
    r = convshaders.Reader(data, E)
    r.raw(12)
    out = []
    for _ in range(r.u32()):
        r.u32()
        inst = r.str()
        r.str()
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
                nm = r.str()
                r.u32()
                r.u32()
                h = r.u32()
                r.raw(8 * r.u32())
                out.append((inst, nm, h))
    return out


def pc_texture_keys(ark):
    """texture key -> archive keys of the PC SXET containers that hold it (headers only)"""
    res = {}
    for k, (t, off, cs, us) in ark.ents.items():
        if us < 0x28:
            continue
        ark.f.seek(off)
        if t == 0:
            hd = ark.f.read(0x28)
            if hd[:4] != b'SXET':
                continue
            n = struct.unpack_from('<I', hd, 0x10)[0]
            ark.f.seek(off)
            hd = ark.f.read(0x28 + 0x40 * n)
        elif t == 1:
            raw = ark.f.read(min(cs, 64))
            if arkio.lzss_n(raw + bytes(16), 4, 4) != b'SXET':
                continue
            ark.f.seek(off)
            raw = ark.f.read(cs) + bytes(16)
            n = struct.unpack_from('<I', arkio.lzss_n(raw, 4, 0x28), 0x10)[0]
            hd = arkio.lzss_n(raw, 4, 0x28 + 0x40 * n)
        else:
            continue
        for i in range(n):
            res.setdefault(struct.unpack_from('<I', hd, 0x28 + 0x40 * i + 4)[0], []).append(k)
    return res


# ------------------------------------------------------------------------------- tweakable dump merge
_HEAD = re.compile(rb'(?m)^(/[^\r\n]*):\r\n')


def dump_blocks(b):
    """'!directory:' tweakable dump -> (preamble bytes, [(header, block bytes)]); lossless"""
    ms = list(_HEAD.finditer(b))
    if not ms:
        raise Fail('no blocks')
    pre = b[:ms[0].start()]
    blocks = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(b)
        blocks.append((m.group(1).decode('latin-1'), b[m.start():end]))
    return pre, blocks


def merge_dump(pc, dlc, root, model):
    """Insert the DLC blocks '<root>/<model>' and '<root>/<model>/...' into the PC dump, as one group in front
    of the first PC block whose car segment sorts after <model> (the PC files are ordered by car name).
    Returns (merged bytes, inserted headers)."""
    ppre, pbl = dump_blocks(pc)
    dpre, dbl = dump_blocks(dlc)
    if ppre != dpre:
        raise Fail('different !directory preamble: %r / %r' % (ppre, dpre))
    me = root + '/' + model
    mine = [(h, t) for h, t in dbl if h == me or h.startswith(me + '/')]
    if not mine:
        return None, [], (None, None)
    have = {h for h, _ in pbl}
    clash = [h for h, _ in mine if h in have]
    if clash:
        raise Fail('DLC blocks already in the PC file: %s' % clash[:3])
    for h, t in mine:
        c, lf, cr = line_endings(t)
        if lf or cr:
            raise Fail('DLC block %s has bare LF/CR' % h)
    def car(h):
        rest = h[len(root) + 1:]
        return rest.split('/')[0]
    at = len(pbl)
    for i, (h, _) in enumerate(pbl):
        if h.startswith(root + '/') and car(h).encode('latin-1') > model.encode('latin-1'):
            at = i
            break
    out = ppre + b''.join(t for _, t in pbl[:at]) + b''.join(t for _, t in mine) + b''.join(t for _, t in pbl[at:])
    # check: removing the inserted blocks gives the PC file back
    if ppre + b''.join(t for _, t in pbl) != pc:
        raise Fail('PC dump does not split losslessly')
    return out, [h for h, _ in mine], (pbl[at - 1][0] if at else None, pbl[at][0] if at < len(pbl) else None)


# --------------------------------------------------------------------------------------------- converters
class Ctx:
    def __init__(self, dlc, pc_fn):
        self.dlc, self.pc_fn = dlc, pc_fn
        self.pc = arkio.Ark(pc_fn)
        self._pool = None
        self._ref = None

    @property
    def pool(self):
        if self._pool is None:
            import pair_lib  # the converter's own reader (convshaders.main uses it); needs the old scratchpad ark.py
            ark = pair_lib.Ark(self.pc_fn)
            self._pool = convshaders.Pool(ark=ark)
            self._pool.add_ark_scan(ark)
            if self._pool.conflicts:
                raise Fail('shader pool conflicts: %s' % self._pool.conflicts[:3])
        return self._pool

    @property
    def ref(self):
        if self._ref is None:
            self._ref = convhkx.load_ref()
        return self._ref


_HEADER_NO_COLON = re.compile(rb'(?m)^(/[A-Za-z0-9_\-/]+)[ \t]*(?=\r?$)')


def fix_params_headers(data):
    """a tweakable-dump block header written without its trailing ':' ("/Vehicles/Coronas/Unique_21", only in the
    Elite pack's Unique_21 Coronas.params; every PC file has the colon). The PC reader (FUN_00abc460) takes a path
    token as a block header only when ':' follows (then 0x41D1A0 sets the current block); otherwise it drops the
    header and the token after it (the block's first key) and the following keys land outside the block, so the
    car keeps default coronas. Returns (data, [fixed header paths])."""
    fixed = []

    def rep(m):
        fixed.append(m.group(1).decode('ascii'))
        return m.group(1) + b':'
    return _HEADER_NO_COLON.sub(rep, data), fixed


def convert_body_file(ctx, name, xd):
    """Xbox car file -> (PC bytes, converter label, extra info dict)"""
    n = name.lower()
    if n.endswith('.model'):
        out = convmodel.convert(xd)[0]
        return out, 'convmodel.convert', {}
    if n.endswith('.textures'):
        return convtex.convert(xd), 'convtex.convert', {}
    if n.endswith('.phys') or n.endswith('.maincoll'):
        rep = {}
        out = convhkx.convert(xd, ctx.ref, rep)
        if rep['conflicts'] or rep['holes'] or rep['unknown']:
            raise Fail('havok %s: conflicts %d holes %d unknown %s' % (name, len(rep['conflicts']), rep['holes'], rep['unknown']))
        return out, 'convhkx.convert', {'havok': rep}
    if n.endswith(('.fxc', '.shaders', '.shaderinst')):
        out, probs = convshaders.convert_file(ctx.pool, n, xd)
        if probs:
            raise Fail('shaders %s: %s' % (name, '; '.join(probs)))
        return out, 'convshaders.convert_file(%s)' % os.path.splitext(n)[1][1:], {}
    kind, how = convmisc.detect(xd, n)
    if how == 'foreign':
        raise Fail('%s: kind %s has no converter here' % (name, kind))
    return convmisc.convert(xd, kind), 'convmisc:%s (%s)' % (kind, how), {'kind': kind}


# ------------------------------------------------------------------------------------------------- verify
def verify_entry(ctx, e):
    """re-run the converters' own parsers on the PC output; returns a short note, raises Fail"""
    out, src, conv, path = e['data'], e.get('xbox'), e['conv'], e['path']
    if conv == 'convmodel.convert':
        m = Model(out, False)
        if m.end != len(out) or m.version != 1:
            raise Fail('model parse end %d of %d' % (m.end, len(out)))
        ib, vb = convmodel.semantic(m)
        x = Model(src, True)
        W = lambda o: struct.unpack_from('<I', m.block, o)[0]
        nvb, vbp = W(0), W(4)
        maxv = max(W(vbp + 0x1C * i + 16) for i in range(nvb)) if nvb else 0
        top = max((max(l) for l in ib if l), default=-1)
        if top >= maxv:
            raise Fail('index %d >= vertex count %d' % (top, maxv))
        if any(len(l) % 3 for l in ib):
            raise Fail('index list not a multiple of 3')
        if m.decls != [(bytes(convmodel.swap_words(h, 4)), els) for h, els in x.decls] or m.fixups != x.fixups:
            raise Fail('decls/fixups differ from the Xbox file')
        return '%d index lists (%d tris), %d vertex buffers' % (len(ib), sum(len(l) for l in ib) // 3, len(vb))
    if conv == 'convtex.convert':
        c = convtex.parse(out)
        if c['E'] != '<':
            raise Fail('not a PC container')
        texs = convtex.textures(out)
        xs = convtex.textures(src)
        if [t[0] for t in texs] != [t[0] for t in xs]:
            raise Fail('texture keys differ')
        for (k, dds, _), (_, xdds, _) in zip(texs, xs):
            if dds[:4] != b'DDS ' or dds != xdds:
                raise Fail('texture %08x: DDS differs from the Xbox decode' % k)
        return '%d textures' % len(texs)
    if conv == 'convhkx.convert':
        pf = convhkx.Packfile(out)
        if pf.E != '<' or pf.rules != b'\x04\x01\x00\x01':
            raise Fail('not a PC packfile')
        w = convhkx.Walker(pf, ctx.ref['classes']).run()
        rep = e['info']['havok']
        if w.missing or w.conflicts:
            raise Fail('walk: missing %s conflicts %d' % (sorted(w.missing), len(w.conflicts)))
        if bytes(w.unit) != bytes(rep['unit']):
            raise Fail('member map of the output differs from the input')
        ti, ts = pf.sec('__types__')
        cn = {nm: o for o, _, nm in pf.classnames()}
        raw, offs, _ = convhkx.TypesWriter(ctx.ref['classes'], ctx.ref['enums'], cn).build(
            w.postorder(pf.contents_off if pf.contents_sec == 2 else None))
        if raw != ts.raw or offs != (ts.loc, ts.glob, ts.virt, ts.exp, ts.imp, ts.end):
            raise Fail('__types__ not reproducible from the output')
        bad = vol = 0
        for a in range(0, len(w.d) - 3, 4):
            if w.kind[a] == convhkx.T_REAL and w.unit[a] == 4:
                v = struct.unpack_from('<f', w.d, a)[0]
                if not math.isfinite(v):
                    if w.vol[a]:
                        vol += 1
                    else:
                        bad += 1
        if bad:
            raise Fail('%d non-finite reals outside volatile members' % bad)
        return '%d objects, member map identical, __types__ reproducible%s' % (
            len(w.objs), ', %d non-finite volatile reals' % vol if vol else '')
    if conv.startswith('convshaders'):
        ext = os.path.splitext(path)[1]
        if ext == '.fxc':
            f = convshaders.parse_fxc(out)
            if f['E'] != '<' or convshaders.build_fxc(f, '<') != out:
                raise Fail('fxc does not re-parse')
            return '%d entries' % len(f['ents'])
        if ext == '.shaders':
            f = convshaders.parse_shdr(out)
            if f['E'] != '<' or convshaders.build_shdr(f, '<') != out:
                raise Fail('shaders does not re-parse')
            return '%d entries' % len(f['ents'])
        if convshaders.convert_sdri(out, '<') != out or convshaders.convert_sdri(out, '>') != src:
            raise Fail('shaderinst does not round-trip')
        return '%d instances, round-trips to the Xbox bytes' % len(convshaders.sdri_shader_ids(out))
    if conv == 'tonic.convert':
        d2, info = tonic.convert(out)
        back, _ = tonic.convert(out, platform_out=1, endian_out='>')
        if out[:4] != b'\x08\0\0\0' or struct.unpack_from('<I', out, 4)[0] != 0 or back != src:
            raise Fail('tonic does not round-trip')
        return '%d sections, %d sounds, round-trips to the Xbox bytes' % (len(info['sections']), len(info['sounds']))
    if conv.startswith('convmisc:'):
        kind = e['info']['kind']
        if kind == 'deform':
            magic, nv, nt, ne, nbox, npt, npanel, nname = struct.unpack_from('<8I', out, 0)
            size = 32 + 16 * nv + 6 * nt + 10 * ne + 168 * npt + 136 * npanel + 100 * nbox + 32 * nname
            if magic != 0x44464D02 or size != len(out) or struct.unpack_from('>8I', src, 0)[1:] != (nv, nt, ne, nbox, npt, npanel, nname):
                raise Fail('deform layout check failed (%d vs %d)' % (size, len(out)))
            return 'DFM2 %d verts %d tris %d edges %d points %d panels %d boxes %d names' % (nv, nt, ne, npt, npanel, nbox, nname)
        if kind in ('parts', 'swap4'):
            if len(out) % 4 or bytes(convmisc.swap4(out)) != src:
                raise Fail('swap4 does not invert')
            return '%d dwords, swap4 inverts' % (len(out) // 4)
        if kind == 'text':
            c, lf, cr = line_endings(out)
            if not convmisc.is_text(out) or lf or cr:
                raise Fail('text check (bare LF %d, CR %d)' % (lf, cr))
            return 'text, %d CRLF lines%s' % (c, ', unchanged' if out == src else ', LF->CRLF')
        raise Fail('no verifier for kind ' + kind)
    if conv.startswith('merge'):
        c, lf, cr = line_endings(out)
        if lf or cr:
            raise Fail('merged text has bare LF/CR')
        return 'CRLF %d' % c
    raise Fail('no verifier for ' + conv)


# ------------------------------------------------------------------------------------------- one car's files
def collect_car(ctx, dlc, model, attr, add, say, skipped, missing):
    """the car's own files (steps 2-4): body folder, handling, lights, frontend chassis name, Tonic sound"""
    pc = ctx.pc
    # ---- 2. every file of Vehicles/Bodies/<model>
    body_dir = os.path.dirname(find_ci(dlc, 'Vehicles/Bodies/%s/ChassisName.txt' % model))
    for fn in sorted(os.listdir(body_dir)):
        full = os.path.join(body_dir, fn)
        xd = read(full)
        data, conv, info = convert_body_file(ctx, fn, xd)
        if fn.lower().endswith('.params'):
            data, fixed = fix_params_headers(data)
            if fixed:
                conv += ' + block header colon added (%s)' % ', '.join(fixed)
                say('  %s: block header without the trailing ":" (Xbox source bug) -> "%s:"; without it the PC params '
                    'reader drops the block (header not taken, first key eaten, the rest set outside the block)' % (
                        gpath(dlc, full), '", "'.join(fixed)))
        add(gpath(dlc, full), data, conv, srel(dlc, full), xd, info)

    # ---- 3. handling, lights, frontend chassis name
    for ref_name, what in ((attr['HumanPhysicsFile'], 'HumanPhysicsFile'), (attr['AIPhysicsFile'], 'AIPhysicsFile')):
        for ext in ('.params', '.rdata'):
            p = 'vehicles/handling/configs/%s%s' % (ref_name.lower(), ext)
            src = find_ci(dlc, p)
            if src:
                xd = read(src)
                kind, how = convmisc.detect(xd, p)
                conv = convmisc.convert(xd, kind)
                if arkio.key(p) in pc.ents and pc.get(arkio.key(p)) == conv:
                    # a base car's handling the DLC ships again unchanged (the Elite livery pack: Unique_01/02.params)
                    say('  %s %s: %s is byte-identical to the PC file once converted, not added' % (what, ref_name, p))
                    continue
                add(p, conv, 'convmisc:%s (%s)' % (kind, how), srel(dlc, src), xd, {'kind': kind})
            elif arkio.key(p) in pc.ents:
                say('  %s %s: in the PC resident.ark (%s), not needed' % (what, ref_name, p))
            elif ext == '.params':
                missing.append((p, 'DLC, PC resident.ark', what))
    # the exe loads the handling .rdata by the record's ModelFile, not by its physics file: FUN_008c3590 reads
    # "%s%s.params" with HumanPhysicsFile (+0x50) / AIPhysicsFile (+0x78), then "%s%s.rdata" with ModelFile (+0x28,
    # format push at 0x8C37B2; record layout from the serializer at 0x7FACD9). For most cars ModelFile is the physics
    # file and the loop above already took it; the Elite cars (ModelFile Unique_21..23, HumanPhysicsFile
    # Unique_01/02) ship a per-model UNIQUE_2x.RDATA that it never asked for ("Resident(noassert)": missing = silent).
    mf = attr.get('ModelFile', model)
    if mf.lower() not in (attr['HumanPhysicsFile'].lower(), attr['AIPhysicsFile'].lower()):
        p = 'vehicles/handling/configs/%s.rdata' % mf.lower()
        src = find_ci(dlc, p)
        if src:
            xd = read(src)
            kind, how = convmisc.detect(xd, p)
            conv = convmisc.convert(xd, kind)
            if arkio.key(p) in pc.ents and pc.get(arkio.key(p)) == conv:
                say('  ModelFile %s: %s is byte-identical to the PC file once converted, not added' % (mf, p))
            else:
                add(p, conv, 'convmisc:%s (%s)' % (kind, how), srel(dlc, src), xd, {'kind': kind})
                say('  ModelFile %s: %s added (the exe loads the handling .rdata by ModelFile, physics file %s)' % (
                    mf, p, attr['HumanPhysicsFile']))
        elif arkio.key(p) in pc.ents:
            say('  ModelFile %s: in the PC resident.ark (%s), not needed' % (mf, p))
        else:
            say('  ModelFile %s: no %s in the DLC or the PC (the exe asks for it with noassert)' % (mf, p))
    for p in ('vehicles/lights/%s_lights.params' % model.lower(), 'vehicles/frontend/bodies/%s/chassisname.txt' % model.lower()):
        src = find_ci(dlc, p)
        if not src:
            missing.append((p, 'DLC', 'read by the exe'))
            continue
        xd = read(src)
        kind, how = convmisc.detect(xd, p)
        add(p, convmisc.convert(xd, kind), 'convmisc:%s (%s)' % (kind, how), srel(dlc, src), xd, {'kind': kind})

    # ---- 4. Tonic engine sound (AudioFile): state engine bundle + .tonic/.tonic2
    af = attr['AudioFile']
    se = 'audio/tonic/_stateengine/%s.xml' % af.lower()
    se_src = find_ci(dlc, se)
    if arkio.key(se) in pc.ents:
        say('  AudioFile %s is a PC sound, nothing to add' % af)
    elif not se_src:
        missing.append((se, 'DLC, PC resident.ark', 'AudioFile'))
    else:
        xd = read(se_src)
        kind, how = convmisc.detect(xd, se)
        add(se, convmisc.convert(xd, kind), 'convmisc:%s (%s)' % (kind, how), srel(dlc, se_src), xd, {'kind': kind})
        for rel in re.findall(rb'Filename="([^"]+)"', xd):
            p = os.path.normpath(os.path.join('audio/tonic/_stateengine', rel.decode('latin-1'))).replace(os.sep, '/').lower()
            src = find_ci(dlc, p)
            data = None
            if src:
                x2 = read(src)
                k2, h2 = convmisc.detect(x2, p)
                data = convmisc.convert(x2, k2)
            if arkio.key(p) in pc.ents:
                same = data is not None and pc.get(p) == data
                say('  bundle file %s: in the PC resident.ark%s' % (p, ' (byte-identical to the DLC copy), not added' if same else
                                                                   ' (DIFFERS from the DLC copy; PC copy kept)' if data else ''))
                if data is not None:
                    skipped.append((p, 'byte-identical in the PC resident.ark' if same else 'PC copy differs; kept (not replaced)'))
            elif data is not None:
                add(p, data, 'convmisc:%s (%s)' % (k2, h2), srel(dlc, src), x2, {'kind': k2})
            else:
                missing.append((p, 'DLC, PC resident.ark', 'Tonic bundle of ' + af))
        for ext in ('tonic', 'tonic2'):
            p = 'audio/tonic/_outputfiles/%s.%s' % (af.lower(), ext)
            src = find_ci(dlc, p)
            if not src:
                missing.append((p, 'DLC', 'AudioFile'))
                continue
            xd = read(src)
            add(p, tonic.convert(xd)[0], 'tonic.convert', srel(dlc, src), xd)


def skipped_frontend(ctx, dlc, model, skipped):
    """the car's frontend (car select) files: not converted here (tools/dlc/frontend does, finish_car.py adds them)"""
    fe_dir = os.path.dirname(find_ci(dlc, 'Vehicles/Frontend/Bodies/%s/ChassisName.txt' % model) or '')
    if fe_dir:
        for fn in sorted(os.listdir(fe_dir)):
            p = gpath(dlc, os.path.join(fe_dir, fn))
            if p.endswith('chassisname.txt'):
                continue
            xd = read(os.path.join(fe_dir, fn))
            why = None
            try:
                if p.endswith(('.fxc', '.shaders', '.shaderinst')):
                    o, probs = convshaders.convert_file(ctx.pool, p, xd)
                    if probs:
                        why = ('convshaders runs (%d bytes) but INCOMPLETE: %s; held back with the frontend model'
                               % (len(o), '; '.join(probs)))
                    else:
                        why = 'convshaders converts it (%d bytes); held back, only useful with the frontend model' % len(o)
                elif p.endswith('.textures'):
                    convtex.convert(xd)
                    why = 'convtex converts it, held back with the model'
                elif p.endswith('.model'):
                    convmodel.convert(xd)
                    why = 'convmodel accepted it unexpectedly; held back (data lives in .model.stream)'
                else:
                    why = 'no converter for this format (frontend streamed data)'
            except Exception as ex:
                why = 'NOT CONVERTIBLE with the existing converters: %s: %s' % (type(ex).__name__, ex)
            skipped.append((p, why))


# --------------------------------------------------------------------------------------------------- main
def main(argv):
    args = {'--model': 'Musclecar_17', '--dlc': DEF_DLC, '--pc': DEF_PC, '--out': DEF_OUT}
    i = 0
    while i < len(argv):
        if argv[i] in args and i + 1 < len(argv):
            args[argv[i]] = argv[i + 1]
            i += 2
        else:
            print(__doc__ or open(os.path.abspath(__file__)).read().split('\nimport')[0])
            return 2
    # --model takes one model or a comma-separated list (Musclecar_17,Musclecar_18,...): several cars of a pack go
    # into ONE overlay, because they share the merged text files (VehicleDatabase.xml, VehicleColors.params,
    # GameCompositingInfo.params); separate overlays would each bring their own copy and replace each other's.
    models = [m_.strip() for m_ in args['--model'].split(',') if m_.strip()]
    if not models or len({m_.lower() for m_ in models}) != len(models):
        raise Fail('--model: give one or more different models')
    dlc, pc_fn, out_fn = args['--dlc'], args['--pc'], args['--out']
    if not dlc or not out_fn:
        raise Fail('--dlc and --out are needed (or set GLIMPSE_DLC_SCRATCH for the development defaults)')
    if not pc_fn:
        raise Fail('--pc is needed: the PC data\\resident.ark (or set GAME_DIR to the Split/Second game folder)')
    if len(models) > 1 and SCRATCH and out_fn == DEF_OUT:
        out_fn = os.path.join(SCRATCH, 'dlc', 'overlay', 'deadline_cars.ark')
    base = os.path.splitext(out_fn)[0]
    rep = []

    def say(s=''):
        print(s)
        rep.append(s)

    ctx = Ctx(dlc, pc_fn)
    pc = ctx.pc
    entries = []      # dicts: path, data, conv, source, xbox (input bytes), info
    skipped = []      # (path/what, reason)
    missing = []      # (reference, where it was looked for, note)
    by_path = {}

    def add(path, data, conv, source, xbox=None, info=None):
        # with several cars a file can be named by more than one of them (a Tonic bundle file): it goes in once,
        # and only if every car brings the same bytes
        if path in by_path:
            if by_path[path]['data'] != data:
                raise Fail('%s: two cars bring different data' % path)
            say('  %s: also used by another car of this overlay, added once' % path)
            return
        e = {'path': path, 'data': data, 'conv': conv, 'source': source, 'xbox': xbox, 'info': info or {}}
        entries.append(e)
        by_path[path] = e

    # ---- 1. the cars' database records
    vdb_dlc_fn = find_ci(dlc, 'Vehicles/VehicleDatabase.xml')
    vdb_dlc = read(vdb_dlc_fn).decode('latin-1')
    cars = []         # (model, record text, attributes, record ID)
    for model in models:
        recs = [m for m in dlcmerge._CAR.finditer(vdb_dlc) if re.search(r'\bModelFile="%s"' % re.escape(model), m.group(0), re.I)]
        if len(recs) != 1:
            raise Fail('expected one DLC record with ModelFile=%s, found %d' % (model, len(recs)))
        rec = recs[0].group(0)
        attr = dict(re.findall(r'(\w+)="([^"]*)"', rec))
        cars.append((model, rec, attr, attr['ID']))
        say('Car: ModelFile %s -> PC record %s (AudioFile %s, HumanPhysicsFile %s, AIPhysicsFile %s)' % (
            model, attr['ID'], attr['AudioFile'], attr['HumanPhysicsFile'], attr['AIPhysicsFile']))
    car_ids = [c[3] for c in cars]
    others = [m.group(1) for m in dlcmerge._CAR.finditer(vdb_dlc) if m.group(1) not in car_ids]
    for model, rec, attr, car_id in cars:
        collect_car(ctx, dlc, model, attr, add, say, skipped, missing)

    # ---- 5. merged text files: all cars of this overlay in each one
    merged_paths = []
    pc_vdb = pc.get('vehicles/vehicledatabase.xml')
    pc_ids = [m.group(1) for m in dlcmerge._CAR.finditer(pc_vdb.decode('latin-1'))] if pc_vdb is not None else []
    for car_id in car_ids:
        if car_id not in pc_ids:
            raise Fail('PC VehicleDatabase.xml has no record %s' % car_id)
    vt = dlcmerge.merge_vehicles(pc_vdb.decode('latin-1'), [c[1] for c in cars])
    vb = vt.encode('latin-1')
    olds = {m.group(1): m.group(0) for m in dlcmerge._CAR.finditer(pc_vdb.decode('latin-1')) if m.group(1) in car_ids}
    back = vt
    for model, rec, attr, car_id in cars:
        back = back.replace(rec, olds[car_id], 1)
    if back.encode('latin-1') != pc_vdb or vt.count('<Car ') != pc_vdb.decode('latin-1').count('<Car '):
        raise Fail('VehicleDatabase merge changed more than the %s record%s' % (', '.join(car_ids), 's' if len(cars) > 1 else ''))
    if line_endings(vb) [1:] != (0, 0) or line_endings(vb)[0] != line_endings(pc_vdb)[0]:
        raise Fail('VehicleDatabase line endings changed')
    add('vehicles/vehicledatabase.xml', vb, 'merge: dlcmerge.merge_vehicles (%s only)' % ', '.join(car_ids),
        'PC resident.ark + ' + srel(dlc, vdb_dlc_fn) + ' (' + ', '.join(car_ids) + ')')
    merged_paths.append('vehicles/vehicledatabase.xml')
    for model, rec, attr, car_id in cars:
        say('Merged vehicles/vehicledatabase.xml: %s replaced (PC: %s)' % (car_id, re.search(r'ModelFile="[^"]*"', olds[car_id]).group(0)))
        say('  new record: %s' % rec)
    say('  PC file: %d bytes, latin-1/ASCII, no BOM, CRLF %d, bare LF 0 -> merged %d bytes, CRLF %d' % (
        len(pc_vdb), line_endings(pc_vdb)[0], len(vb), line_endings(vb)[0]))
    for p, root in (('vehicles/vehiclecolors.params', '/Vehicles/ColorPalettes'),
                    ('vehicles/compositing/gamecompositinginfo.params', '/VehicleCompositor/GameCompositingInfo')):
        src = find_ci(dlc, p)
        if not src:
            continue
        pcb = pc.get(p)
        # one car after the other over the result so far: each car's group goes in front of the first block whose
        # car sorts after it, so the order of the models does not change the result (the file stays sorted)
        mb, took = pcb, []
        for model in models:
            res = merge_dump(mb, read(src), root, model)
            if res[0] is None:
                say('Merge %s: the DLC file has no %s blocks, not added' % (p, model))
                continue
            mb, heads, (before, after) = res
            took += heads
            say('Merged %s: %d DLC blocks %s/%s[/...] inserted between %s and %s; PC blocks kept (%d), CRLF %d -> %d' % (
                p, len(heads), root, model, before, after, len(dump_blocks(pcb)[1]), line_endings(pcb)[0], line_endings(mb)[0]))
        if not took:
            continue
        # check: the merged file is the PC file plus exactly the blocks taken, the PC blocks in their order
        mpre, mbl = dump_blocks(mb)
        if [h for h, _ in mbl if h not in set(took)] != [h for h, _ in dump_blocks(pcb)[1]] or \
                mpre + b''.join(t for h, t in mbl if h not in set(took)) != pcb:
            raise Fail('%s: merging the cars one after the other changed PC blocks' % p)
        add(p, mb, 'merge: tweakable-dump blocks (assemble_car.merge_dump)', 'PC resident.ark + ' + srel(dlc, src) +
            ' (%s blocks)' % ', '.join(models))
        merged_paths.append(p)
        dbl = dump_blocks(read(src))[1]
        say('  DLC blocks of other cars not taken: %d' % (len(dbl) - len(took)))

    # ---- 6. skipped / not convertible (UI, frontend model, package files)
    for model in models:
        skipped_frontend(ctx, dlc, model, skipped)
    ui = [('ui/frontend/flash/fe_vehicleselect.gfx', 'Xbox build of the PC file (PC copy has mouse UI: mc_VSpip*, miniMenu); '
           'the DLC car name/image are hard-wired per ID in its ActionScript (Placeholder_01..11, FE_VSelect05, dlcIcon). '
           'Using it would replace a PC file with an Xbox one; no GFX editor/converter exists'),
          ('ui/frontend/flash/textures/dlc.textures', 'FE_VSelect01..05 + FE_QuickPlay_Imgs_SET2 (car select atlas); only the '
           'Xbox GFX references FE_VSelect05 and the PC frontend loads only the "Flash" texture pack'),
          ('ui/frontend/flash/textures/dlc.filenames', 'goes with dlc.textures'),
          ('ui/ingame/vehicleselect/vehicleselect.gfx', 'Xbox build of the PC file; adds FE_carSelect_set4 and the Placeholder IDs '
           'in ActionScript; same problem as fe_vehicleselect.gfx'),
          ('ui/ingame/textures/ingame3.textures', 'Xbox ingame3 set incl. FE_carSelect_set4; would replace the PC ingame3 files '
           '(PC: 2432-byte .textures + .streamtex split)'),
          ('ui/ingame/textures/ingame3.filenames', 'goes with ingame3.textures (adds FE_carSelect_set4)'),
          ('ui/ingame/textures/ingame3.streamtex', 'Xbox streamed texture data; no converter'),
          ('ui/ingame/textures/ingame4.textures', 'FE_DLC_event_ICONS_RAW: Deadline event icons, not car-specific'),
          ('ui/ingame/textures/ingame.textures', 'Xbox build of a PC UI pack, not car-specific'),
          ('ui/hud/dialsinfo.gfx, ui/ingame/eventobjectives.gfx, ui/ingame/ig_gamemodeinstructions.gfx, ui/ingame/raceresults.gfx',
           'Xbox builds of PC UI files (mode/event UI), not car-specific'),
          ('ui/logic/trackfiltration.xml, ui/network/leaderboard_def.xml, directory.txt', 'Deadline mode/track lists, not car-specific'),
          ('audio/release|submission/menu/dlcmenu.*', 'DLC menu bank, never requested by the PC exe, not car-specific'),
          ('movies/screens/quickplay/fe_deadline.bik', 'quick play movie of the pack, not car-specific')]
    for p, why in ui:
        if p.endswith('.textures') and find_ci(dlc, p):
            try:
                o = convtex.convert(read(find_ci(dlc, p)))
                why += '; convtex converts it (%d bytes)' % len(o)
            except Exception as ex:
                why += '; convtex: %s' % ex
        skipped.append((p, why))
    # the other DLC cars
    other_models = set(re.findall(r'ModelFile="([^"]*)"', vdb_dlc)) - set(models)
    if other_models:
        skipped.append(('other Deadline cars %s' % ', '.join(sorted(other_models)),
                        'not part of this overlay (their records/blocks are left out of the merges)'))
    # RESIDENT_DLC.ARK: make sure nothing in it is named after these cars
    rd = find_ci(dlc, 'RESIDENT_DLC.ARK')
    rd_keys = set()
    if rd:
        kinds = {}
        with arkio.Ark(rd) as a:
            rd_keys = set(a.order)
            hits = {c[0]: [] for c in cars}
            for k in a.order:
                d = a.get(k)
                for model, rec, attr, car_id in cars:
                    if re.search(rb'(?i)' + model.encode() + rb'|' + car_id.encode() + rb'|' + attr['AudioFile'].encode(), d):
                        hits[model].append(k)
                if k not in pc.ents:
                    m4 = d[:4]
                    kd = {b'<Roo': 'XML', b'!dir': 'tweakable dump', b'TEXS': 'TEXS', b'\x00FXC': 'fxc', b'SHDR': 'shaders',
                          b'SDRI': 'shaderinst', b'\x01\x00\x00\x08': 'model'}.get(m4, 'unknown')
                    kinds[kd] = kinds.get(kd, 0) + 1
        for model, rec, attr, car_id in cars:
            say('RESIDENT_DLC.ARK: %d entries, %d mention %s/%s/%s' % (len(rd_keys), len(hits[model]), model, car_id, attr['AudioFile']))
        skipped.append(('resident_dlc.ark', 'Deadline pack content: %d entries, %d keys not in the PC resident.ark (%s); no entry '
                        'mentions %s, and none of %s paths is among its keys' % (
                            len(rd_keys), sum(kinds.values()), ', '.join('%d %s' % (n, k) for k, n in sorted(kinds.items())),
                            '; '.join('%s, %s or %s' % (c[0], c[3], c[2]['AudioFile']) for c in cars),
                            'this car\'s' if len(cars) == 1 else 'these cars\'')))
        clash = [e['path'] for e in entries if arkio.key(e['path']) in rd_keys]
        if clash:
            raise Fail('RESIDENT_DLC.ARK also has keys of %s' % clash)

    # ---- 7. write the overlay
    entries.sort(key=lambda e: e['path'])
    os.makedirs(os.path.dirname(os.path.abspath(out_fn)), exist_ok=True)
    arkio.write_ark(out_fn, [(e['path'], e['data']) for e in entries], pad=0x800)

    # ---- 8. checks
    say()
    say('Checks')
    errors = []
    with arkio.Ark(out_fn) as a:
        keys = a.order
        hsize = struct.unpack_from('<I', read(out_fn)[:8], 4)[0]
        ok_sorted = keys == sorted(keys) and len(set(keys)) == len(keys)
        say('  read back with arkio: %s, %d entries, hsize %#x (= 0x10 + 20n: %s), keys sorted ascending and unique: %s' % (
            'little-endian' if not a.be else 'BIG-ENDIAN', len(keys), hsize, hsize == 0x10 + 20 * len(keys), ok_sorted))
        if a.be or not ok_sorted or hsize != 0x10 + 20 * len(keys) or len(keys) != len(entries):
            errors.append('archive structure')
        for e in entries:
            k = arkio.key(e['path'])
            e['key'] = k
            t = a.ents[k][0]
            got = a.get(k)
            if t != 0 or got != e['data']:
                errors.append('%s: read-back differs' % e['path'])
                continue
            e['data'] = got
            try:
                e['note'] = verify_entry(ctx, e)
            except Exception as ex:
                e['note'] = 'FAIL %s' % ex
                errors.append('%s: %s' % (e['path'], ex))
    # replace vs new
    repl = sorted(e['path'] for e in entries if e['key'] in pc.ents)
    say('  keys that replace resident.ark keys: %s' % repl)
    say('  new keys: %d' % (len(entries) - len(repl)))
    if repl != sorted(merged_paths):
        errors.append('replaced keys are not exactly the merged text files')
    # the other cars must not leak in
    for e in entries:
        if e['conv'].startswith('merge'):
            for o in other_models:
                if o.encode() in e['data'] and o.encode() not in pc.get(e['path']):
                    errors.append('%s contains %s' % (e['path'], o))
    if 'vehicles/vehicledatabase.xml' in merged_paths:
        mv = [e for e in entries if e['path'] == 'vehicles/vehicledatabase.xml'][0]['data'].decode('latin-1')
        pv = pc_vdb.decode('latin-1')
        for oid in others:
            a1 = [m.group(0) for m in dlcmerge._CAR.finditer(mv) if m.group(1) == oid]
            a2 = [m.group(0) for m in dlcmerge._CAR.finditer(pv) if m.group(1) == oid]
            if a1 != a2:
                errors.append('record %s changed' % oid)
        say('  merged VehicleDatabase.xml: only %s differs from the PC file%s' % (
            ', '.join(car_ids), '; %s unchanged' % ', '.join(others) if others else ''))
    # virtual resident.ark (inject/vark.py) built from the real resident.ark + this overlay, read as the game does
    va = vark.VirtualArk(pc_fn, [out_fn])
    try:
        g = vark.GameArkReader(va.read, va.size)
        bad = [e['path'] for e in entries if g.read_member(e['key']) != e['data']]
        sample = [k for k in list(pc.ents)[::500] if k not in {e['key'] for e in entries}]
        bad += ['base %08x' % k for k in sample if g.read_member(k) != pc.get(k)]
        say('  virtual resident.ark (vark.py): %d keys (%d base + %d new), %d overlay entries and %d base samples read '
            'back through GameArkReader: %s' % (len(g.keys), len(pc.ents), len(g.keys) - len(pc.ents), len(entries), len(sample),
                                                'all equal' if not bad else 'DIFFER %s' % bad[:5]))
        errors += bad
    finally:
        va.close()
    # per-entry verification
    for e in entries:
        if e['note'].startswith('FAIL'):
            say('  FAIL %s: %s' % (e['path'], e['note']))
    say('  per-entry converter verification: %d of %d pass' % (sum(1 for e in entries if not e['note'].startswith('FAIL')), len(entries)))

    # ---- 9. references
    say()
    say('References')
    ov_tex = {}
    for e in entries:
        if e['conv'] == 'convtex.convert':
            for k, _, _ in convtex.textures(e['data']):
                ov_tex.setdefault(k, []).append(e['path'])
    pc_tex = pc_texture_keys(pc)
    tref, unres = 0, {}
    sids_missing = []
    for e in entries:
        if e['path'].endswith('.shaderinst'):
            stem = e['path'][:-len('.shaderinst')]
            ids = set()
            for ext in ('.fxc', '.shaders'):
                d = [x for x in entries if x['path'] == stem + ext]
                if d:
                    f = (convshaders.parse_fxc if ext == '.fxc' else convshaders.parse_shdr)(d[0]['data'])
                    ids.update((ext, x['id']) for x in f['ents'])
            for sid in convshaders.sdri_shader_ids(e['data']):
                for ext in ('.fxc', '.shaders'):
                    if (ext, sid) not in ids:
                        sids_missing.append((e['path'], ext, sid))
            for inst, nm, h in sdri_textures(e['data']):
                tref += 1
                if h not in ov_tex and h not in pc_tex:
                    unres.setdefault('%08x' % h, set()).add('%s (%s)' % (os.path.basename(e['path']), nm))
    n_own = sum(1 for e in entries if e['path'].endswith('.shaderinst') for _, _, h in sdri_textures(e['data']) if h in ov_tex)
    say('  shaderinst texture slots: %d; in this car\'s textures %d, in PC texture containers %d, unresolved %d' % (
        tref, n_own, tref - n_own - sum(len(v) for v in unres.values()), len(unres)))
    for h, where in sorted(unres.items()):
        missing.append(('texture key %s' % h, 'overlay + all PC SXET containers', ', '.join(sorted(where))))
    say('  shaderinst shader IDs missing from the same LOD\'s .fxc/.shaders: %d %s' % (len(sids_missing), sids_missing[:4]))
    if sids_missing:
        errors.append('shader ids missing')
    pv = pc_vdb.decode('latin-1')
    cams = pc.get('vehicles/cameras/cameras.xml') or b''
    season = pc.get('ui/logic/season.xml') or b''
    have_ov = {e['key'] for e in entries}
    # per-car paths the exe builds (strings in the PC exe) that are in neither the DLC nor the PC archives
    probe = [('ui/ingame/carunlock/%s/carunlock_%s.gfx', 'Resident|UI\\InGame\\CarUnlock\\%s\\CarUnlock_%s.gfx'),
             ('vehicles/bodies/%s/%s.rdata', 'Resident(noassert)|Vehicles/Bodies/%s/%s.rdata'),
             ('vehicles/bodies/%s/%s.xml', 'Resident(noassert)|Vehicles/Bodies/%s/%s.xml'),
             ('vehicles/bodies/%s/coronas.params', 'Vehicles/Bodies/<car>/Coronas.params (base cars with lights have one)')]
    for model, rec, attr, car_id in cars:
        if len(cars) > 1:
            say('  %s (%s):' % (model, car_id))
        for a_ in ('ChaseCamFile', 'BumperCamFile', 'BonnetCamFile', 'AIPhysicsFile'):
            users = [m.group(1) for m in dlcmerge._CAR.finditer(pv) if re.search(r'\b%s="%s"' % (a_, re.escape(attr[a_])), m.group(0))]
            say('  %s=%s: used by %d PC cars (%s)' % (a_, attr[a_], len(users), ', '.join(users[:4])))
            if not users:
                missing.append((attr[a_], 'PC VehicleDatabase.xml', a_))
        for a_ in ('ChaseCamFile', 'BumperCamFile', 'BonnetCamFile'):
            if (attr[a_] + '.params').encode() not in cams:
                missing.append((attr[a_] + '.params', 'PC Vehicles/Cameras/Cameras.xml', a_))
        for tmpl, exe in probe:
            for nm in ((model, car_id) if 'carunlock' in tmpl else (model,)):
                p = tmpl.replace('%s', nm.lower())
                k = arkio.key(p)
                if k in have_ov or k in pc.ents or k in rd_keys or find_ci(dlc, p):
                    continue
                b16 = tmpl.replace('%s', 'musclecar_16')
                b05 = tmpl.replace('%s', 'musclecar_05')
                note = 'base Musclecar_16 has it: %s, Musclecar_05: %s' % (arkio.key(b16) in pc.ents, arkio.key(b05) in pc.ents)
                missing.append((p, 'DLC, DLC RESIDENT_DLC.ARK, PC resident.ark', '%s; %s' % (exe, note)))
        lists = [m.group(1).decode() for m in re.finditer(rb'(?s)<(\w+)>(.*?)</\1>', season)
                 if ('Name="%s"' % car_id).encode() in m.group(2) or ('Name="%s"' % model).encode() in m.group(2)]
        say('  UI/Logic/Season.xml (PC): %s/%s listed in %s; the DLC ships no Season.xml (open whether the car select needs it)' % (
            car_id, model, lists or 'no list'))

    # ---- 10. manifest and report
    total = os.path.getsize(out_fn)
    with open(base + '.manifest.tsv', 'w', newline='\n', encoding='ascii') as f:
        f.write('# overlay %s: %d entries, %d bytes (stored, type 0; padded to 0x800)\n' % (os.path.basename(out_fn), len(entries), total))
        f.write('key\tsize\tresident\tconverter\tpath\tsource\tcheck\n')
        for e in sorted(entries, key=lambda e: e['key']):
            f.write('%08x\t%d\t%s\t%s\t%s\t%s\t%s\n' % (e['key'], len(e['data']), 'replaces' if e['key'] in pc.ents else 'new',
                                                    e['conv'], e['path'], e['source'], e['note']))
    say()
    say('Overlay %s: %d entries, %d bytes; data %d bytes' % (out_fn, len(entries), total, sum(len(e['data']) for e in entries)))
    groups = {}
    for e in entries:
        g_ = e['conv'].split(' ')[0].split('(')[0]
        groups.setdefault(g_, [0, 0])
        groups[g_][0] += 1
        groups[g_][1] += len(e['data'])
    for g_, (n, sz) in sorted(groups.items()):
        say('  %-36s %3d entries %10d bytes' % (g_, n, sz))
    say()
    say('Skipped')
    for p, why in skipped:
        say('  %s: %s' % (p, why))
    say()
    say('Referenced, in neither the DLC nor the PC archives')
    for p, where, note in missing:
        say('  %s (looked in: %s) - %s' % (p, where, note))
    if not missing:
        say('  none')
    say()
    say('RESULT: %s' % ('OK' if not errors else 'ERRORS: %s' % errors))
    with open(base + '.report.txt', 'w', newline='\r\n', encoding='utf-8') as f:
        f.write('\n'.join(rep) + '\n')
    print('wrote', out_fn, base + '.manifest.tsv', base + '.report.txt')
    return 0 if not errors else 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as ex:
        print('FAILED:', ex)
        sys.exit(1)
