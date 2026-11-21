# Builds ONE overlay with several cars of a DLC pack (default: the four Deadline cars) and checks it.
#
# All cars of a pack touch the same shared files (VehicleDatabase.xml, VehicleColors.params,
# GameCompositingInfo.params, directory.txt, FE_vehicleSelect.gfx, Flash.textures/.filenames, the six
# dictionaries). Separate per-car overlays would each bring their own copy of those and replace each other's, so
# the pack is built in one go:
#   1. assemble_car.py --model A,B,C,D  -> <out dir>\<name>_cars.ark (+ manifest, report): every car's converted
#      files, and the three merged text files with all cars' records/blocks;
#   2. finish_car.py --model A --model B ...  -> <out>: records get ID = ModelFile and CarSelectOrder after the
#      PC's cars in the given order, VEHICLES list, frontend models, one gfx frame per car in both car sprites,
#      each DLC picture atlas once (FE_VSelectDLC1, ...), the same for the in-game car select
#      (VehicleSelect.gfx picture sprite, atlases FE_carSelectDLC1, ... in the resident InGame set), names and
#      descriptions in the dictionaries;
#   3. checks (below) and <out minus .ark>.manifest.tsv / .report.txt.
# --mode: the car pack is built and checked as <out minus .ark>_nomode.ark, then the game mode Deadline is added
# with ../mode/deadline_mode.apply (directory.txt and the dictionaries on the pack's versions; TrackFiltration.xml
# and the 11 track overlays from the PC; start times + --mode-extra-seconds, default 0) and written to <out>;
# deadline_mode.check plus archive and virtual resident.ark read-back; every car pack entry the mode does not
# touch stays byte-identical. Refuses deadline.ark / deadline_v2.ark as <out>.
# --check-single <model>=<ref.ark>: also rebuilds that car alone (assemble + finish --no-ingame, the version the
# references were made with) and requires it byte-identical to <ref.ark>; the pack's entries of that car that are
# not shared files must equal the reference too.
#
#   python -I build_pack.py [--models Musclecar_17,Musclecar_18,Musclecar_19,Truck_09] [--dlc <x_deadline>]
#       [--pc <PC resident.ark>] [--frontend <frontend_out>] [--out <deadline.ark>]
#       [--check-single Musclecar_17=<musclecar_17_full.ark>] [--verify-only]
#       [--mode [--mode-extra-seconds N] [--mode-no-per-track] [--mode-audio <dlcaudio.py output of AUDIO/RELEASE/MENU>]]
import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
DLC_TOOLS = os.path.dirname(HERE)
for _p in ('misc', 'tex', 'ui', 'inject'):
    sys.path.insert(0, os.path.join(DLC_TOOLS, _p))
import arkio     # noqa: E402
import convtex   # noqa: E402
import dictadd   # noqa: E402
import flashtex  # noqa: E402
import gfxcars   # noqa: E402
import vark      # noqa: E402
sys.path.insert(0, HERE)
import cartext   # noqa: E402

# development: the defaults of a manual run below this folder (set GLIMPSE_DLC_SCRATCH); without it --dlc, --frontend
# and --out must be given (build_all.py always gives them)
SCRATCH = os.environ.get('GLIMPSE_DLC_SCRATCH', '')
DEF_MODELS = 'Musclecar_17,Musclecar_18,Musclecar_19,Truck_09'
LANGS = ('ENGLISH_NTSC', 'GERMAN', 'FRENCH_NTSC', 'ITALIAN', 'SPANISH_NTSC', 'StringIDs')
FRONTEND_EXT = ('fxc', 'model', 'model.stream', 'shaderinst', 'shaders', 'streamtex', 'textures')
SHARED = ['vehicles/vehicledatabase.xml', 'vehicles/vehiclecolors.params', 'vehicles/compositing/gamecompositinginfo.params',
          'directory.txt', 'ui/frontend/flash/fe_vehicleselect.gfx', 'ui/frontend/flash/textures/flash.textures',
          'ui/frontend/flash/textures/flash.filenames'] + ['dictionary/%s_win32.dct' % l.lower() for l in LANGS] + \
         ['ui/ingame/vehicleselect/vehicleselect.gfx', 'ui/ingame/textures/ingame.textures', 'ui/ingame/textures/ingame.filenames']
# the in-game shared files (finish_car --no-ingame leaves them out)
INGAME = SHARED[-3:]
# the PC's UI texture sets: (archive path without extension, streamed); every image name is registered globally
# (0x533910, without _DXT5/_RAW), so an added name must be new to all of them
UI_SETS = (('ui/frontend/flash/textures/flash', False), ('ui/ingame/textures/ingame', False),
           ('ui/ingame/textures/ingame1', True), ('ui/ingame/textures/ingame2', True), ('ui/ingame/textures/ingame3', True),
           ('ui/hud/textures/hud', True))
DUMPS = (('vehicles/vehiclecolors.params', '/Vehicles/ColorPalettes'),
         ('vehicles/compositing/gamecompositinginfo.params', '/VehicleCompositor/GameCompositingInfo'))
CAR_RE = re.compile(r'<Car\s+ID="([^"]*)"[^>]*/>')
NAME_RE = re.compile(rb'(?i)\b(?:Musclecar|Supercar|Truck|Unique|Hypercar|Placeholder)_\d\d\b')


def run(script, args, log):
    # -X utf8: the tools print the accented description lines; the log is UTF-8
    cmd = [sys.executable, '-I', '-B', '-X', 'utf8', os.path.join(HERE, script)] + args   # -B: no __pycache__
    print('>', script, ' '.join(args))
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = r.stdout.decode('utf-8', 'replace')
    open(log, 'w', encoding='utf-8', newline='\r\n').write(out)
    if r.returncode:
        print(out[-3000:])
        sys.exit('%s failed (exit %d), log %s' % (script, r.returncode, log))
    return out


def build(models, dlc, pc_fn, frontend, out_fn, work):
    """assemble + finish; returns the assembled overlay's path"""
    base = os.path.splitext(out_fn)[0]
    asm = os.path.join(work, os.path.basename(base) + '_cars.ark')
    run('assemble_car.py', ['--model', ','.join(models), '--dlc', dlc, '--pc', pc_fn, '--out', asm],
        os.path.splitext(asm)[0] + '.log')
    args = [asm, os.path.splitext(asm)[0] + '.manifest.tsv', out_fn, '--dlc', dlc, '--pc', pc_fn, '--frontend', frontend]
    for m in models:
        args += ['--model', m]
    run('finish_car.py', args, base + '.finish.log')
    return asm


def manifest(path):
    rows = {}
    for line in open(path, encoding='ascii'):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 7 and re.fullmatch(r'[0-9a-f]{8}', f[0]):
            rows[int(f[0], 16)] = f
    return rows


def dump_blocks(b):
    ms = list(re.finditer(rb'(?m)^(/[^\r\n]*):\r\n', b))
    pre = b[:ms[0].start()]
    return pre, [(m.group(1).decode('latin-1'), b[m.start():(ms[i + 1].start() if i + 1 < len(ms) else len(b))])
                 for i, m in enumerate(ms)]


def vehicles_section(text):
    nl = '\r\n'
    s = text.find('-Vehicles/Bodies/*' + nl)
    e = text.find(nl + '-', s)
    return s, e + len(nl), [t.strip() for t in text[s:e].split(nl)[3:] if t.strip()]


def pc_images(tex, stream=None):
    """{registered name (lower case): (format, width, height, DDS)} of a PC texture container; a streamed one
    (stream = its .streamtex) at full size"""
    out = {}
    if stream is None:
        c = convtex.parse(tex)
        for i, (k, s) in enumerate(flashtex.names(c)):
            e = c['entries'][i]
            fmt, _, _, w, h = struct.unpack_from('<5I', e, 12)
            sz = struct.unpack_from('<I', c['blobs'][i], 0)[0]
            out[flashtex.registered(s).lower()] = (fmt, w, h, c['blobs'][i][4:4 + sz])
        return out
    ents, groups, nm = flashtex.streamed_groups(tex, '<')
    nmk = dict(flashtex.names({'names': nm}))
    for loc, slot, off in groups[1][1]:
        e = ents[(loc - 0x18) // 0x40]
        k = struct.unpack_from('<I', e, 4)[0]
        fmt, _, _, w, h = struct.unpack_from('<5I', e, 12)
        sz = struct.unpack_from('<I', stream, off)[0]
        out[flashtex.registered(nmk[k]).lower()] = (fmt, w, h, stream[off + 4:off + 4 + sz])
    return out


def xbox_images(tex, stream=None):
    """the same for an Xbox container, converted (flashtex.xbox_set); names without the format suffix"""
    out = {}
    for k, s, e, b in flashtex.xbox_set(tex, stream):
        fmt, _, _, w, h = struct.unpack_from('<5I', e, 12)
        sz = struct.unpack_from('<I', b, 0)[0]
        out[s.rsplit('_', 1)[0].lower()] = (fmt, w, h, b[4:4 + sz])
    return out


def cell_blocks(img, rect):
    """the DXT blocks of level 0 that cover rect (x0, x1, y0, y1 in pixels, cut to the image); None if not DXT"""
    fmt, w, h, dds = img
    bpb = {0x21: 8, 0x24: 16, 0x25: 16}.get(fmt)
    if bpb is None:
        return None
    x0, x1, y0, y1 = max(0, rect[0]), min(w, rect[1]), max(0, rect[2]), min(h, rect[3])
    data, bw = dds[128:], (w + 3) // 4
    return [data[(by * bw + bx) * bpb:(by * bw + bx + 1) * bpb]
            for by in range(y0 // 4, (y1 + 3) // 4) for bx in range(x0 // 4, (x1 + 3) // 4)]


def texture_keys(ark):
    """every texture key in the PC's SXET containers (entry headers only; stored and LZSS entries)"""
    keys = set()
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
        keys.update(struct.unpack_from('<I', hd, 0x28 + 0x40 * i + 4)[0] for i in range(n))
    return keys


class Check:
    def __init__(self):
        self.lines, self.errors = [], []

    def say(self, s=''):
        print(s)
        self.lines.append(s)

    def ok(self, cond, what):
        self.say('  [%s] %s' % ('ok' if cond else 'FAIL', what))
        if not cond:
            self.errors.append(what)
        return cond


def ingame_list_check(pget, get, nv, models, C):
    """The in-game car select's array (0x809850: 30 slots at +0x280, no bounds check; +0x2F8/+0x2FC behind it).
    It is filled from the unlock manager (object at [0xD64E48], ctor 0xA05410: six lists of capacity 0x100) list 1
    (0xA05360(1) -> +0x3C; filled by 0xA05FE0 from +0x34), and that list holds only cars of UI/Logic/Season.xml
    (loader 0xA05820): InitialCars (list +0x1C, copied into +0x04 and +0x34 by 0xA05560), UnlockableBasicCars (20
    slots +0x6C, unlocked by credits through 0xA05600) and UnlockableEliteCars (4 slots +0xBC, 0xA056C0). The profile's
    DLC-unlock cars (0xA05E30 -> 0xA05760) go only to list 2 (+0x04: front-end car select), UnlockableDLCCars (+0xCC) has
    no reader in the manager's code besides the loader, and CarSelectOrder (record +0x1B8) only sorts (0xA053D0). A DLC car added to the
    database therefore never reaches the array; its bound is the number of Season.xml cars."""
    say, ok = C.say, C.ok
    p = 'ui/logic/season.xml'
    ps, ns = pget(p), get(p)
    ok(ns is None or ns == ps, 'Season.xml not replaced by the pack (the in-game car list comes only from it)')
    s = (ns or ps).decode('latin-1')
    sec = lambda name: re.findall(r'<Car\s+Name\s*="([^"]+)"', (re.search(r'<%s>(.*?)</%s>' % (name, name), s, re.S) or [None, ''])[1])
    init, basic, elite, dlc_ = sec('InitialCars'), sec('UnlockableBasicCars'), sec('UnlockableEliteCars'), sec('UnlockableDLCCars')
    ids = set(CAR_RE.findall(nv))
    every = init + basic + elite
    ok(len(set(every)) == len(every) and set(every) <= ids,
       'Season.xml: InitialCars %d + UnlockableBasicCars %d + UnlockableEliteCars %d = %d distinct cars, all records'
       % (len(init), len(basic), len(elite), len(every)))
    ok(len(every) <= 30, 'the in-game car select (0x809850) lists at most these %d cars: within its 30 slots' % len(every))
    ok(not set(models) & set(every + dlc_), 'no car of the pack is in Season.xml (%s): the pack does not change the in-game '
                                           'list' % ', '.join(models))


def verify(models, dlc, pc_fn, frontend, out_fn, asm_fn, single):
    C = Check()
    say, ok = C.say, C.ok
    pc = arkio.Ark(pc_fn)
    ov = arkio.Ark(out_fn)
    K = arkio.key
    get = lambda p: ov.get(K(p))
    pget = lambda p: pc.get(K(p))
    asm_rows = manifest(os.path.splitext(asm_fn)[0] + '.manifest.tsv')
    asm = arkio.Ark(asm_fn)
    ml = [m.lower() for m in models]

    vdb_dlc = open(os.path.join(dlc, 'VEHICLES', 'VEHICLEDATABASE.XML'), 'rb').read().decode('latin-1')
    dlc_rec = {}
    for m in CAR_RE.finditer(vdb_dlc):
        mf = re.search(r'ModelFile="([^"]+)"', m.group(0)).group(1)
        dlc_rec[mf] = m.group(0)
    old_id = {mo: CAR_RE.match(dlc_rec[mo]).group(1) for mo in models}

    say('Archive')
    raw = open(out_fn, 'rb').read()
    hsize = struct.unpack_from('<I', raw, 4)[0]
    keys = ov.order
    ok(not ov.be and keys == sorted(keys) and len(set(keys)) == len(keys) and hsize == 0x10 + 20 * len(keys)
       and all(ov.ents[k][0] == 0 for k in keys),
       'read back with arkio: little-endian, %d entries, hsize %#x = 0x10 + 20n, keys sorted and unique, all type 0' % (len(keys), hsize))
    data = {k: ov.get(k) for k in keys}
    va = vark.VirtualArk(pc_fn, [out_fn])
    try:
        g = vark.GameArkReader(va.read, va.size)
        bad = [k for k in keys if g.read_member(k) != data[k]]
        sample = [k for k in list(pc.ents)[::250] if k not in data]
        bad += [k for k in sample if g.read_member(k) != pc.get(k)]
        ok(not bad, 'virtual resident.ark (inject/vark.py): %d keys (%d base + %d new); all %d overlay entries and %d base '
                    'samples read through GameArkReader equal' % (len(g.keys), len(pc.ents), len(g.keys) - len(pc.ents),
                                                                  len(keys), len(sample)))
    finally:
        va.close()

    # which entry is what
    paths = {}
    for k, f in asm_rows.items():
        paths[k] = f[4]
    for m in ml:
        for ext in FRONTEND_EXT:
            paths[K('vehicles/frontend/bodies/%s/%s.%s' % (m, m, ext))] = 'vehicles/frontend/bodies/%s/%s.%s' % (m, m, ext)
    for p in SHARED:
        paths[K(p)] = p
    ok(set(paths) == set(keys), 'every entry accounted for: %d assembled + %d frontend + %d shared added by finish_car = %d' % (
        len(asm_rows), 7 * len(models), len([p for p in SHARED if K(p) not in asm_rows]), len(keys)))
    repl = sorted(paths[k] for k in keys if k in pc.ents)
    ok(repl == sorted(SHARED), 'keys that replace resident.ark keys are exactly the %d shared files' % len(SHARED))
    same = [k for k in asm_rows if k != K('vehicles/vehicledatabase.xml') and data.get(k) == asm.get(k)]
    ok(len(same) == len(asm_rows) - 1, 'assembled entries carried over unchanged: %d of %d (VehicleDatabase.xml is changed by '
                                        'finish_car)' % (len(same), len(asm_rows) - 1))
    for m in ml:
        d = os.path.join(frontend, m)
        eq = all(data[K('vehicles/frontend/bodies/%s/%s.%s' % (m, m, e))] == open(os.path.join(d, '%s.%s' % (m, e)), 'rb').read()
                 for e in FRONTEND_EXT)
        ok(eq, 'frontend %s: the 7 files equal %s' % (m, d))

    say()
    say('VehicleDatabase.xml')
    pv, nv = pget('vehicles/vehicledatabase.xml').decode('latin-1'), get('vehicles/vehicledatabase.xml').decode('latin-1')
    prec, nrec = list(CAR_RE.finditer(pv)), list(CAR_RE.finditer(nv))
    ok(len(prec) == len(nrec), '%d records, as the PC file' % len(nrec))
    pids = [m.group(1) for m in prec]
    orders = {}
    for mo in models:
        i = pids.index(old_id[mo])
        r = nrec[i].group(0)
        want = dlc_rec[mo].replace(' ID="%s"' % old_id[mo], ' ID="%s"' % mo, 1)
        order = int(re.search(r' CarSelectOrder="(-?\d+)"', r).group(1))
        orders[mo] = order
        want = re.sub(r' CarSelectOrder="-?\d+"', ' CarSelectOrder="%d"' % order, want)
        ok(nrec[i].group(1) == mo and r == want,
           '%s: in slot %d (was %s), ID = ModelFile, CarSelectOrder %d, every other attribute as in the DLC record' % (
               mo, i, old_id[mo], order))
    others = [i for i, m in enumerate(prec) if m.group(1) not in old_id.values()]
    ok(all(prec[i].group(0) == nrec[i].group(0) for i in others), 'the other %d records are byte-identical to the PC file' % len(others))
    rest = nv
    for m in reversed(nrec):
        rest = rest[:m.start()] + '@' + rest[m.end():]
    prest = pv
    for m in reversed(prec):
        prest = prest[:m.start()] + '@' + prest[m.end():]
    ok(rest == prest, 'text between the records unchanged')
    pos = sorted(int(x) for x in re.findall(r' CarSelectOrder="(-?\d+)"', nv) if int(x) > 0)
    ok(pos == list(range(1, len(pos) + 1)), 'positive CarSelectOrders run 1..%d without gaps or doubles (%s)' % (
        len(pos), ', '.join('%s %d' % kv for kv in orders.items())))
    # The in-game car select (screen 0x54, 0x809850) appends each listed car's ID pointer to an inline array at
    # +0x280 with room for 30 (the constructor 0x809E00 stores the array's address at +0x2F8 right after it) and no
    # bounds check. Entries 31 and 32 land on +0x2F8 (written only by the constructor) and +0x2FC (read by no method
    # of the class: its vtables 0xC20CA8/0xC20C54, the destructor 0x80A120), and stay readable as array entries; a
    # 33rd would reach the flag at +0x300 and the timer at +0x304 (0x8095C0, 0x80A350).
    # What fills it is NOT every record with CarSelectOrder > 0 but the unlock manager's list 1 (0xA05360(1)), whose
    # cars all come from UI/Logic/Season.xml (0xA05820): see ingame_list_check. So the limit is on Season.xml.
    ingame_list_check(pget, get, nv, models, C)
    say('  (selectable records, CarSelectOrder > 0: %d; they are listed by the front-end car select only, capacity 256)'
        % len(pos))

    say()
    say('VehicleColors.params / GameCompositingInfo.params')
    for p, root in DUMPS:
        ppre, pbl = dump_blocks(pget(p))
        npre, nbl = dump_blocks(get(p))
        xpre, xbl = dump_blocks(open(os.path.join(dlc, *p.upper().split('/')), 'rb').read())
        mine = lambda h, mo: h == root + '/' + mo or h.startswith(root + '/' + mo + '/')
        new = [(h, t) for h, t in nbl if any(mine(h, mo) for mo in models)]
        kept = [(h, t) for h, t in nbl if not any(mine(h, mo) for mo in models)]
        ok(npre == ppre and kept == pbl, '%s: PC preamble and all %d PC blocks unchanged, in their order' % (p, len(pbl)))
        for mo in models:
            nb = [(h, t) for h, t in new if mine(h, mo)]
            xb = [(h, t) for h, t in xbl if mine(h, mo)]
            # the tweakable paths name the car: they must be its ModelFile (the name the game builds them from)
            ok(nb == xb and nb and all(h.split('/')[len(root.split('/'))] == mo for h, _ in nb),
               '%s: %d blocks %s/%s[/...], byte-identical to the DLC blocks' % (mo, len(nb), root, mo))
        heads = [h for h, _ in nbl if h.startswith(root + '/')]
        cars = [h[len(root) + 1:].split('/')[0] for h in heads]
        ok(cars == sorted(cars, key=lambda s: s.encode('latin-1')), '%s: blocks still ordered by car name' % p)
    say('  tweakable paths in the cars\' own .params files (they must carry the car\'s own model name):')
    for mo in models:
        names = set()
        for k in keys:
            pth = paths[k]
            if pth.endswith('.params') and (('/%s/' % mo.lower()) in pth or pth.endswith('/%s.params' % mo.lower())
                                            or pth.endswith('/%s_lights.params' % mo.lower())):
                hdr = re.findall(rb'(?m)^(/[^\r\n]*):\r\n|^!directory: ([^\r\n]*)', data[k])
                for h in hdr:
                    names.update(n.decode() for n in NAME_RE.findall(b''.join(h)))
        ok(names <= {mo}, '%s: model names in tweakable paths: %s' % (mo, ', '.join(sorted(names)) or 'none'))

    say()
    say('directory.txt')
    pt, nt = pget('directory.txt').decode('latin-1'), get('directory.txt').decode('latin-1')
    s1, e1, pnames = vehicles_section(pt)
    s2, e2, nnames = vehicles_section(nt)
    ok(nnames == pnames + models and len(nnames) == 54 if models == DEF_MODELS.split(',') else nnames == pnames + models,
       'VEHICLES: %d names = the PC\'s %d + %s appended' % (len(nnames), len(pnames), ', '.join(models)))
    ok(len(nnames) <= 64, 'VEHICLES within the compositor\'s 64 entries (0x422FD0)')
    ok(nt[:s2] == pt[:s1] and nt[e2:] == pt[e1:] and nt[s2:e2] == pt[s1:e1] + ''.join('\t%s\r\n' % m for m in models),
       'the rest of directory.txt unchanged (CRLF kept)')

    say()
    say('Dictionaries')
    texts = {}
    for lang in LANGS:
        p = 'dictionary/%s_win32.dct' % lang.lower()
        pd_, nd = dictadd.Dict(pget(p)), dictadd.Dict(get(p))
        before, after = pd_.all(), nd.all()
        unchanged = all(after.get(h) == v for h, v in before.items()) and len(after) == len(before) + 2 * len(models)
        res = []
        for mo in models:
            for kk in ('TEXT_CARDESCR_%s' % mo, 'TEXT_%s' % mo):
                v = nd.get(kk)
                res.append(v is not None and pd_.get(kk) is None)
                if lang == 'StringIDs':
                    res[-1] = res[-1] and v == kk.lower().encode()
                texts[(lang, kk)] = v.decode('utf-8') if v is not None else None
        ok(unchanged and all(res), '%s: all %d new keys resolve (new to the PC file), the %d PC entries unchanged' % (
            lang, 2 * len(models), len(before)))
    ok(not cartext.check_rule(), 'description rule (cartext.py) gives the first build\'s Musclecar_17 lines')
    tu4 = cartext.tu4_table()
    say('  descriptions: %s' % ('TU4 text table %s' % tu4.describe() if tu4 else 'no TU4 text table (GLIMPSE_TU4_TEXTS '
                                                                               'not set): generated lines'))
    for mo in models:
        ok(all(texts[(l, 'TEXT_%s' % mo)] == cartext.NAMES[mo] for l in LANGS[:5]),
           '%s: name %r in all 5 languages' % (mo, cartext.NAMES[mo]))
        ok(cartext.SLOT.get(mo) == old_id[mo], '%s: Xbox slot %s (cartext.SLOT %s, the key of its TU4 texts)' % (
            mo, old_id[mo], cartext.SLOT.get(mo)))
        if tu4:
            nk = cartext.official_keys(mo)[1]
            ok(all(tu4.get(nk, l) == cartext.NAMES[mo] for l in LANGS[:5]), '%s: TU4 %s is the name in all 5 languages'
               % (mo, nk))
        st = cartext.stats_of(dlc_rec[mo])
        say('  %s: Speed %d (%s), Drift %d (%s), Strength %d (%s)' % (
            mo, st['Speed'], cartext.band(st['Speed']), st['Drift'], cartext.band(st['Drift']), st['Strength'],
            cartext.band(st['Strength'])))
        for l in LANGS[:5]:
            t = texts[(l, 'TEXT_CARDESCR_%s' % mo)]
            want, src = cartext.description(l, st, mo, tu4)
            ok(t == want and t == t.upper(), '%s %s (%s): %s' % (mo, l, src, t))
    # the PC fonts have the letters the PC texts use (as deadline_mode.check): every non-ASCII letter of the new
    # names and descriptions must be one of them
    tu4text = cartext._tu4()
    for l in LANGS[:5]:
        p = 'dictionary/%s_win32.dct' % l.lower()
        pct = [v[0].decode('utf-8', 'replace') for v in dictadd.Dict(pget(p)).all().values()]
        odd = tu4text.missing_letters(pct, [texts[(l, k % mo)] for mo in models for k in ('TEXT_CARDESCR_%s', 'TEXT_%s')])
        ok(not odd, '%s: every non-ASCII letter of the car texts is used by the PC texts%s' % (
            l, ' (not: %s)' % ' '.join('U+%04X' % ord(c) for c in odd) if odd else ''))

    say()
    say('FE_vehicleSelect.gfx')
    pg, ng = pget('ui/frontend/flash/fe_vehicleselect.gfx'), get('ui/frontend/flash/fe_vehicleselect.gfx')
    tmp = tempfile.TemporaryDirectory(prefix='build_pack_')
    fp, fn_ = os.path.join(tmp.name, 'pc.gfx'), os.path.join(tmp.name, 'out.gfx')
    open(fp, 'wb').write(pg)
    open(fn_, 'wb').write(ng)
    _, pstart, ptags = gfxcars.load(fp)
    _, nstart, ntags = gfxcars.load(fn_)
    rt = ng[:nstart] + b''.join(gfxcars.write_tag(*t) for t in ntags)
    ok(rt == ng and struct.unpack_from('<I', ng, 4)[0] == len(ng), 'round-trips: re-read and re-written tag by tag gives the same '
                                                                    '%d bytes; header length right' % len(ng))
    psp, nsp = gfxcars.car_sprites(ptags), gfxcars.car_sprites(ntags)
    nnew = len(ntags) - len(ptags)
    ok(ntags[:psp[0]] == ptags[:psp[0]] and len(nsp) == 2 and nsp[0] == psp[0] + nnew and
       all(ntags[i + nnew] == ptags[i] for i in range(psp[0], len(ptags)) if i not in psp),
       'every PC tag kept in order; %d new definitions in front of the first car sprite' % nnew)
    dlc_gfx = os.path.join(dlc, 'UI', 'FRONTEND', 'FLASH', 'FE_VEHICLESELECT.GFX')
    defined = {gfxcars.char_id(t[0], t[1]) for t in ntags} | set(gfxcars.imports(ntags))
    for pi, ni in zip(psp, nsp):
        pb, nb = ptags[pi][1], ntags[ni][1]
        pfr = gfxcars.frames_of(gfxcars.read_tags(pb, 4, len(pb)))
        nfr = gfxcars.frames_of(gfxcars.read_tags(nb, 4, len(nb)))
        sid, nf = struct.unpack_from('<HH', nb, 0)
        labs = [f[0] for f in nfr]
        ok(nf == len(nfr) == len(pfr) + len(models) and labs[len(pfr):] == models and
           [f[1] for f in nfr[:len(pfr)]] == [f[1] for f in pfr],
           'sprite %d: %d -> %d frames, one new frame per car (%s), the PC frames unchanged' % (sid, len(pfr), nf, ', '.join(labs[len(pfr):])))
        used = {gfxcars.Place(t[1]).char for f in nfr[len(pfr):] for t in f[1] if t[0] == 26}
        ok(used <= defined, 'sprite %d: every character the new frames place is defined (%d)' % (sid, len(used)))
        gfxcars.display_lists(nfr)
    # the DLC atlases FE_VSelect0N get FE_VSelectDLC1, 2, ... in the order the cars (model order) first show them
    # (Deadline alone: FE_VSelect05 -> FE_VSelectDLC1); the PC's own images (badges) keep their names
    fe_ren = {}
    for mo in models:
        for _, _, ps in gfxcars.frame_info(dlc_gfx, old_id[mo]):
            for n, _, _, _ in ps:
                if re.fullmatch(r'FE_VSelect\d\d', n) and n not in fe_ren:
                    fe_ren[n] = 'FE_VSelectDLC%d' % (len(fe_ren) + 1)
    say('  DLC atlases: %s' % ', '.join('%s -> %s' % kv for kv in fe_ren.items()))
    for mo in models:
        want = gfxcars.frame_info(dlc_gfx, old_id[mo])
        got = gfxcars.frame_info(fn_, mo)
        ren = lambda info: [([t.strip() for t in tx], [(fe_ren.get(n, n), w, h, r) for n, w, h, r in ps])
                            for _, tx, ps in info]
        ok(ren(want) == [([t.strip() for t in tx], ps) for _, tx, ps in got],
           '%s: shows what DLC frame %s shows: name %r, pictures %s' % (
               mo, old_id[mo], ' '.join(t.strip() for _, tx, _ in got for t in tx),
               '; '.join('%s %dx%d cell x %d..%d y %d..%d' % ((n, w, h) + r) for _, _, ps in got for n, w, h, r in ps)))
    tmp_frontend, fn_frontend = tmp, fn_  # used again below (picture content), cleaned up after the in-game part

    say()
    say('Flash.textures / Flash.filenames')
    pc_c, n_c = convtex.parse(pget('ui/frontend/flash/textures/flash.textures')), convtex.parse(get('ui/frontend/flash/textures/flash.textures'))
    pn, nn = flashtex.names(pc_c), flashtex.names(n_c)
    ok([s.upper() for _, s in nn] == sorted(s.upper() for _, s in nn), '%d textures, in name order (upper-case compare)' % len(nn))
    added = [s for _, s in nn if s not in {x for _, x in pn}]
    pidx = {s: i for i, (_, s) in enumerate(pn)}
    keep = all(n_c['entries'][i] == pc_c['entries'][pidx[s]] and n_c['blobs'][i] == pc_c['blobs'][pidx[s]] and nn[i][0] == pn[pidx[s]][0]
               for i, (_, s) in enumerate(nn) if s in pidx)
    ok(keep and len(nn) == len(pn) + len(added), 'the %d PC textures unchanged (entry, data, key); added: %s' % (len(pn), ', '.join(added)))
    ok(len(set(k for k, _ in nn)) == len(nn), 'texture keys unique')
    ok(not any(s.startswith(('FE_VSelect01', 'FE_VSelect02', 'FE_VSelect03', 'FE_VSelect04', 'FE_VSelect05')) for s in added),
       'no PC atlas replaced; DLC atlases only under new names')
    pfl = [s for s in pget('ui/frontend/flash/textures/flash.filenames').split(b'\x00') if s]
    nfl = [s for s in get('ui/frontend/flash/textures/flash.filenames').split(b'\x00') if s]
    ok(nfl[:len(pfl)] == pfl and [s.decode() + '_DXT5' for s in nfl[len(pfl):]] == added,
       'Flash.filenames: the PC\'s %d names + %s' % (len(pfl), ', '.join(s.decode() for s in nfl[len(pfl):])))
    # the name the loader registers (0x533910 strips _DXT5/_RAW) is looked up across every texture set
    ui_names = {}
    for sp, streamed in UI_SETS:
        d = pget(sp + '.textures')
        nm = flashtex.streamed_groups(d, '<')[2] if streamed else convtex.parse(d)['names']
        ui_names[sp] = {flashtex.registered(s).lower() for _, s in flashtex.names({'names': nm})}
    pc_keys = texture_keys(pc)
    clash = [s for s in added if any(flashtex.registered(s).lower() in v for v in ui_names.values())]
    ok(not clash, 'added names new to every PC UI texture set (%s)' % ', '.join(os.path.basename(s) for s, _ in UI_SETS))
    nkeys = {s: k for k, s in nn}
    ok(not [s for s in added if nkeys[s] in pc_keys], 'added texture keys (%s) in no texture container of the PC resident.ark (%d keys)' % (
        ', '.join('%08x' % nkeys[s] for s in added), len(pc_keys)))
    fe_dlc_imgs = xbox_images(open(os.path.join(dlc, 'UI', 'FRONTEND', 'FLASH', 'TEXTURES', 'DLC.TEXTURES'), 'rb').read())
    fe_pack_imgs = pc_images(get('ui/frontend/flash/textures/flash.textures'))
    for mo in models:
        res = []
        for (_, _, wps), (_, _, gps) in zip(gfxcars.frame_info(dlc_gfx, old_id[mo]), gfxcars.frame_info(fn_frontend, mo)):
            for (wn, _, _, wr), (gn, _, _, gr) in zip(wps, gps):
                src = fe_dlc_imgs.get(wn.lower())
                if src is None:
                    res.append('%s: the PC\'s own image (not in DLC.textures)' % gn)
                    continue
                a_, b_ = cell_blocks(src, wr), cell_blocks(fe_pack_imgs.get(gn.lower(), (0, 0, 0, b'')), gr)
                res.append('%s: %s' % (gn, '%d blocks equal' % len(a_) if a_ and a_ == b_ else 'DIFFERENT'))
        ok(not any('DIFFERENT' in r for r in res), '%s: picture content: the cell\'s DXT blocks in the pack\'s Flash set equal the '
                                                   'DLC atlas\'s (%s)' % (mo, '; '.join(res)))

    say()
    say('In-game car select: VehicleSelect.gfx (screen 0x54 "Car Select", career in-game event select)')
    IG = 'ui/ingame/vehicleselect/vehicleselect.gfx'
    ipg, ing = pget(IG), get(IG)
    ip_fn, in_fn = os.path.join(tmp_frontend.name, 'ig_pc.gfx'), os.path.join(tmp_frontend.name, 'ig_out.gfx')
    open(ip_fn, 'wb').write(ipg)
    open(in_fn, 'wb').write(ing)
    _, _, iptags = gfxcars.load(ip_fn)
    _, instart, intags = gfxcars.load(in_fn)
    rt = ing[:instart] + b''.join(gfxcars.write_tag(*t) for t in intags)
    ok(rt == ing and struct.unpack_from('<I', ing, 4)[0] == len(ing), 'round-trips: re-read and re-written tag by tag gives the '
                                                                      'same %d bytes; header length right' % len(ing))
    psp, nsp = gfxcars.car_sprites(iptags), gfxcars.car_sprites(intags)
    nnew = len(intags) - len(iptags)
    ok(len(psp) == len(nsp) == 1 and intags[:psp[0]] == iptags[:psp[0]] and nsp[0] == psp[0] + nnew and
       all(intags[i + nnew] == iptags[i] for i in range(psp[0], len(iptags)) if i not in psp),
       'every PC tag kept in order (incl. all ActionScript); %d new definitions in front of the picture sprite' % nnew)
    ig_dlc = os.path.join(dlc, 'UI', 'INGAME', 'VEHICLESELECT', 'VEHICLESELECT.GFX')
    defined = {gfxcars.char_id(t[0], t[1]) for t in intags} | set(gfxcars.imports(intags))
    pdefs = {gfxcars.char_id(t[0], t[1]): (t[0], t[1]) for t in iptags}
    for pi, ni in zip(psp, nsp):
        pb, nb = iptags[pi][1], intags[ni][1]
        pfr = gfxcars.frames_of(gfxcars.read_tags(pb, 4, len(pb)))
        nfr = gfxcars.frames_of(gfxcars.read_tags(nb, 4, len(nb)))
        sid, nf = struct.unpack_from('<HH', nb, 0)
        labs = [f[0] for f in nfr]
        ok(nf == len(nfr) == len(pfr) + len(models) and labs[len(pfr):] == models and
           [f[1] for f in nfr[:len(pfr)]] == [f[1] for f in pfr],
           'picture sprite %d: %d -> %d frames, one new frame per car (%s), the PC frames unchanged' % (
               sid, len(pfr), nf, ', '.join(labs[len(pfr):])))
        used = {gfxcars.Place(t[1]).char for f in nfr[len(pfr):] for t in f[1] if t[0] == 26}
        ok(used <= defined, 'picture sprite %d: every character the new frames place is defined (%d)' % (sid, len(used)))
        gfxcars.display_lists(nfr)
        sel = []
        for m in CAR_RE.finditer(nv):
            o = re.search(r' CarSelectOrder="(-?\d+)"', m.group(0))
            if o and int(o.group(1)) > 0:
                sel.append(m.group(1))
        nolab = [s for s in sel if s not in labs]
        ok(set(nolab) <= {'Musclecar_Ghost'}, 'a frame for every record with CarSelectOrder > 0 (%d) but %s, which has none on the '
                                              'PC either' % (len(sel), ', '.join(nolab) or '-'))
    # the images the new frames show: the pack's InGame set, the PC's streamed sets
    ig_pack_imgs = pc_images(get('ui/ingame/textures/ingame.textures'))
    for sp, streamed in UI_SETS[2:]:
        for nm_, img in pc_images(pget(sp + '.textures'), pget(sp + '.streamtex')).items():
            ig_pack_imgs.setdefault(nm_, img)
    xdir = os.path.join(dlc, 'UI', 'INGAME', 'TEXTURES')
    ig_dlc_imgs = xbox_images(open(os.path.join(xdir, 'INGAME3.TEXTURES'), 'rb').read(),
                              open(os.path.join(xdir, 'INGAME3.STREAMTEX'), 'rb').read())
    pimg = {gfxcars.char_id(t[0], t[1]) for t in iptags if t[0] == 1001}
    pimg = {pdefs[c][1][9:9 + pdefs[c][1][8]].decode() for c in pimg}
    ig_added = []
    for mo in models:
        want = gfxcars.frame_info(ig_dlc, old_id[mo])
        got = gfxcars.frame_info(in_fn, mo)
        res, good = [], len(want) == len(got) and all(len(w[2]) == len(g[2]) and w[1] == g[1] for w, g in zip(want, got))
        for (_, _, wps), (_, _, gps) in zip(want, got):
            for (wn, ww, wh, wr), (gn, gw, gh, gr) in zip(wps, gps):
                if gn not in pimg:
                    ig_added.append(gn)
                src, dst = ig_dlc_imgs.get(wn.lower()), ig_pack_imgs.get(gn.lower())
                a_, b_ = (cell_blocks(src, wr) if src else None), (cell_blocks(dst, gr) if dst else None)
                same = bool(a_) and a_ == b_
                good &= (ww, wh, wr) == (gw, gh, gr) and same and (dst[1], dst[2]) == (gw, gh) and \
                    (gn == wn if gn in pimg else gn.startswith('FE_carSelectDLC'))
                res.append('%s -> %s %dx%d cell x %d..%d y %d..%d, %s' % ((wn, gn, gw, gh) + gr + (
                    '%d DXT blocks equal' % len(a_) if same else 'content DIFFERENT',)))
        ok(good, '%s: shows what DLC frame %s shows: %s' % (mo, old_id[mo], '; '.join(res)))
    ok(ig_added and all(n.lower() in {k for k in ig_pack_imgs} for n in ig_added),
       'every picture the new frames name is an image of the in-game texture sets')

    say()
    say('InGame.textures / InGame.filenames')
    pc_c, n_c = convtex.parse(pget('ui/ingame/textures/ingame.textures')), convtex.parse(get('ui/ingame/textures/ingame.textures'))
    pn, nn = flashtex.names(pc_c), flashtex.names(n_c)
    rule = flashtex.order_rule(pn)
    sk = flashtex.name_key(rule or 'lower')
    ok(rule == 'lower' and [sk(s) for _, s in nn] == sorted(sk(s) for _, s in nn),
       '%d textures, in the PC file\'s name order (%s-case compare: "_" before letters)' % (len(nn), rule))
    padd = [s for _, s in nn if s not in {x for _, x in pn}]
    pidx = {s: i for i, (_, s) in enumerate(pn)}
    keep = all(n_c['entries'][i] == pc_c['entries'][pidx[s]] and n_c['blobs'][i] == pc_c['blobs'][pidx[s]] and nn[i][0] == pn[pidx[s]][0]
               for i, (_, s) in enumerate(nn) if s in pidx)
    ok(keep and len(nn) == len(pn) + len(padd) and sorted(padd) == sorted(set(ig_added)),
       'the %d PC textures unchanged (entry, data, key); added: %s (the images the new frames show)' % (len(pn), ', '.join(padd)))
    nkeys = {s: k for k, s in nn}
    ok(len(set(nkeys.values())) == len(nn), 'texture keys unique')
    ok(not [s for s in padd if nkeys[s] in pc_keys], 'added texture keys (%s) in no texture container of the PC resident.ark' % (
        ', '.join('%08x' % nkeys[s] for s in padd)))
    clash = [s for s in padd if any(flashtex.registered(s).lower() in v for v in ui_names.values())]
    ok(not clash, 'added names new to every PC UI texture set; no PC atlas replaced')
    fm = []
    for s in padd:
        i = [x for _, x in nn].index(s)
        e, b = n_c['entries'][i], n_c['blobs'][i]
        fmt, one, mips = struct.unpack_from('<3I', e, 12)
        fm.append(fmt == 0x21 and s == flashtex.registered(s) and mips == 1 and b[4 + 84:4 + 88] == b'DXT1' and
                  struct.unpack_from('<I', b, 4 + 8)[0] == 0x1007 and struct.unpack_from('<I', b, 4 + 108)[0] == 0x1000)
    ok(all(fm), 'added images DXT1 (0x21) as the DLC\'s, named without a suffix and with the one-level DDS header, as the PC\'s '
                'own DXT1 image of the set (smalltex)')
    pfl = [s for s in pget('ui/ingame/textures/ingame.filenames').split(b'\x00') if s]
    nfl = [s for s in get('ui/ingame/textures/ingame.filenames').split(b'\x00') if s]
    ok(nfl[:len(pfl)] == pfl and sorted(s.decode() for s in nfl[len(pfl):]) == sorted(flashtex.registered(s) for s in padd),
       'InGame.filenames: the PC\'s %d names + %s' % (len(pfl), ', '.join(s.decode() for s in nfl[len(pfl):])))
    tmp_frontend.cleanup()

    say()
    say('Trucks (Truck_09 against the PC trucks)')
    for mo in [m for m in models if m.lower().startswith('truck')]:
        for p, root in DUMPS:
            _, pbl = dump_blocks(pget(p))
            _, nbl = dump_blocks(get(p))
            pt_ = [h[len(root) + len('/Truck_01'):] for h, _ in pbl if h == root + '/Truck_01' or h.startswith(root + '/Truck_01/')]
            nt_ = [h[len(root) + 1 + len(mo):] for h, _ in nbl if h == root + '/' + mo or h.startswith(root + '/' + mo + '/')]
            ok(pt_ == nt_, '%s: %s has the same %d blocks as Truck_01 (same sub-paths)' % (p, mo, len(nt_)))
        mine = sorted(re.sub(mo.lower(), '#', paths[k]) for k in keys if '/%s/' % mo.lower() in paths[k] and '/frontend/' not in paths[k])
        have = [p for p in mine if K(p.replace('#', 'truck_01')) in pc.ents]
        ok(len(have) == len(mine), 'body files: all %d have a Truck_01 counterpart in the PC resident.ark' % len(mine))

    if single:
        say()
        say('Single-car reference')
        for mo, ref_fn, rebuilt in single:
            ref = arkio.Ark(ref_fn)
            if rebuilt is not None:
                ok(open(rebuilt, 'rb').read() == open(ref_fn, 'rb').read(),
                   '%s rebuilt alone (assemble + finish --no-ingame) is byte-identical to %s' % (mo, ref_fn))
            shared = {K(p) for p in SHARED}
            diff = [k for k in ref.order if k not in shared and data.get(k) != ref.get(k)]
            n = sum(1 for k in ref.order if k not in shared)
            ok(not diff, '%s: all %d entries of %s that are not shared files are byte-identical in the pack' % (
                mo, n, os.path.basename(ref_fn)))
            ok(all(k in data for k in ref.order), 'every key of %s is in the pack' % os.path.basename(ref_fn))
            say('  shared files, compared with %s:' % os.path.basename(ref_fn))
            others = [m for m in models if m != mo]
            # VehicleDatabase: the other cars' records back to the PC's
            rv = ref.get(K('vehicles/vehicledatabase.xml')).decode('latin-1')
            back = nv
            for m in sorted(CAR_RE.finditer(nv), key=lambda m: -m.start()):
                if m.group(1) in others:
                    pr = prec[[x.group(1) for x in nrec].index(m.group(1))].group(0)
                    back = back[:m.start()] + pr + back[m.end():]
            ok(back == rv, 'VehicleDatabase.xml: equal once the records of %s are put back to the PC\'s' % ', '.join(others))
            for p, root in DUMPS:
                rb = ref.get(K(p))
                npre, nbl = dump_blocks(get(p))
                strip = npre + b''.join(t for h, t in nbl if not any(h == root + '/' + o or h.startswith(root + '/' + o + '/') for o in others))
                ok(strip == rb, '%s: equal once the blocks of the other cars are taken out' % p)
            rt_ = ref.get(K('directory.txt')).decode('latin-1')
            ok(nt.replace(''.join('\t%s\r\n' % o for o in others), '', 1) == rt_, 'directory.txt: equal once the other cars\' VEHICLES '
                                                                                   'lines are taken out')
            for p in ('ui/frontend/flash/textures/flash.textures', 'ui/frontend/flash/textures/flash.filenames'):
                ok(get(p) == ref.get(K(p)), '%s: byte-identical (the pack needs the same one atlas)' % p)
            for lang in LANGS:
                p = 'dictionary/%s_win32.dct' % lang.lower()
                ra, na = dictadd.Dict(ref.get(K(p))).all(), dictadd.Dict(get(p)).all()
                ok(all(na.get(h) == v for h, v in ra.items()) and len(na) == len(ra) + 2 * len(others),
                   '%s: every entry of the reference unchanged, %d more (the other cars)' % (os.path.basename(p), 2 * len(others)))
            rg = ref.get(K('ui/frontend/flash/fe_vehicleselect.gfx'))
            with tempfile.TemporaryDirectory(prefix='build_pack_') as td:
                a1, a2 = os.path.join(td, 'r.gfx'), os.path.join(td, 'n.gfx')
                open(a1, 'wb').write(rg)
                open(a2, 'wb').write(ng)
                _, _, rtags = gfxcars.load(a1)
                _, _, ntags2 = gfxcars.load(a2)
                same_fr = True
                for ri, ni in zip(gfxcars.car_sprites(rtags), gfxcars.car_sprites(ntags2)):
                    rf = gfxcars.frames_of(gfxcars.read_tags(rtags[ri][1], 4, len(rtags[ri][1])))
                    nf_ = gfxcars.frames_of(gfxcars.read_tags(ntags2[ni][1], 4, len(ntags2[ni][1])))
                    same_fr &= [f[1] for f in rf] == [f[1] for f in nf_[:len(rf)]]
                # the two car sprites themselves differ (more frames; compared frame by frame above)
                cs = {gfxcars.char_id(rtags[i][0], rtags[i][1]) for i in gfxcars.car_sprites(rtags)}
                rdefs = {gfxcars.char_id(t[0], t[1]): t for t in rtags if gfxcars.char_id(t[0], t[1]) not in cs | {None}}
                ndefs = {gfxcars.char_id(t[0], t[1]): t for t in ntags2 if gfxcars.char_id(t[0], t[1]) not in cs | {None}}
                same_defs = all(ndefs.get(c) == t for c, t in rdefs.items())
            ok(same_fr and same_defs, 'fe_vehicleselect.gfx: the reference\'s frames (incl. %s) and character definitions are '
                                      'byte-identical in the pack; the pack adds the other cars\' frames and characters' % mo)
    return C


def mode_step(cars_fn, out_fn, pc_fn, dlc, extra_seconds, per_track, C, menu_audio=None):
    """--mode: the game mode Deadline (../mode/deadline_mode.py) on top of the verified car pack -> out_fn.
    The mode's shared files (directory.txt, the dictionaries) are taken from the car pack, the others
    (TrackFiltration.xml, the 11 track overlays) from the PC. Returns {key: path} of what the mode wrote."""
    sys.path.insert(0, os.path.join(DLC_TOOLS, 'mode'))
    import deadline_mode  # noqa: E402
    say, ok = C.say, C.ok
    say()
    say('Game mode Deadline (TimeTrialPlus), deadline_mode.apply on %s, extra_seconds %s, per_track %s' % (
        os.path.basename(cars_fn), deadline_mode.fmt(extra_seconds), per_track))
    pc = arkio.Ark(pc_fn)
    cars = arkio.Ark(cars_fn)
    before = {k: cars.get(k) for k in cars.order}
    cars.close()
    try:
        after = deadline_mode.apply(before, pc, dlc, extra_seconds, per_track, log=lambda s: say('  ' + s),
                                    menu_audio=menu_audio)
    except deadline_mode.ModeError as e:
        ok(False, 'deadline_mode.apply: %s' % e)
        return {}
    # built and checked under a temporary name; <out> only appears when every check below passes
    errors_before = len(C.errors)
    tmp_fn = out_fn + '.tmp'
    n = arkio.write_ark(tmp_fn, sorted(after.items()))
    say('  built %s: %d entries, %d bytes' % (tmp_fn, n, os.path.getsize(tmp_fn)))
    rb = arkio.Ark(tmp_fn)
    raw = open(tmp_fn, 'rb').read(16)
    hsize = struct.unpack_from('<I', raw, 4)[0]
    ok(not rb.be and rb.order == sorted(rb.order) and len(set(rb.order)) == len(rb.order)
       and hsize == 0x10 + 20 * len(rb.order) and all(rb.ents[k][0] == 0 for k in rb.order),
       'archive read back with arkio: little-endian, %d entries, hsize %#x = 0x10 + 20n, keys sorted and unique, '
       'all type 0' % (len(rb.order), hsize))
    back = {k: rb.get(k) for k in rb.order}
    rb.close()
    ok(back == after, 'archive data as built')
    try:
        cnt = deadline_mode.check(before, back, pc, dlc, extra_seconds, per_track, say=lambda s: say('  ' + s),
                                  menu_audio=menu_audio)
        ok(True, 'deadline_mode.check: all %d mode checks pass' % cnt)
    except deadline_mode.ModeError as e:
        ok(False, 'deadline_mode.check: %s' % e)
    paths = {arkio.key(p): p for p in deadline_mode.mode_paths(dlc, per_track, menu_audio=bool(menu_audio))}
    changed = {k for k in back if before.get(k) != back[k]}
    in_pack = changed & set(before)
    added = set(back) - set(before)
    ok(changed == set(paths) and set(before) <= set(back) and all(k in pc.ents for k in in_pack),
       'car pack: %d of %d entries byte-identical; the mode wrote exactly its %d files: %d pack files changed (%s), '
       '%d added to the pack (%d replace resident.ark keys, %d new keys)' % (
           len(before) - len(in_pack), len(before), len(changed), len(in_pack),
           ', '.join(sorted(paths[k] for k in in_pack)), len(added), len([k for k in added if k in pc.ents]),
           len([k for k in added if k not in pc.ents])))
    va = vark.VirtualArk(pc_fn, [tmp_fn])
    try:
        g = vark.GameArkReader(va.read, va.size)
        bad = [k for k in back if g.read_member(k) != back[k]]
        sample = [k for k in list(pc.ents)[::250] if k not in back]
        bad += [k for k in sample if g.read_member(k) != pc.get(k)]
        ok(not bad, 'virtual resident.ark (PC + %s): all %d entries and %d base samples read through GameArkReader '
                    'equal' % (os.path.basename(out_fn), len(back), len(sample)))
    finally:
        va.close()
    pc.close()
    if len(C.errors) != errors_before:
        say('  NOT written %s: the mode checks failed, the build is left as %s for inspection' % (out_fn, tmp_fn))
        return {}
    os.replace(tmp_fn, out_fn)
    say('  written %s' % out_fn)
    return paths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default=DEF_MODELS)
    ap.add_argument('--dlc', default=os.path.join(SCRATCH, 'dlc', 'x_deadline') if SCRATCH else None,
                    required=not SCRATCH)
    game_dir = os.environ.get('GAME_DIR', '')  # a manual run's default: the game folder (build_all.py gives --pc)
    ap.add_argument('--pc', default=os.path.join(game_dir, 'data', 'resident.ark') if game_dir else None,
                    required=not game_dir, help='the PC data\\resident.ark (default: below GAME_DIR, the game folder)')
    ap.add_argument('--frontend', default=os.path.join(SCRATCH, 'frontend_out') if SCRATCH else None,
                    required=not SCRATCH)
    ap.add_argument('--out', default=os.path.join(SCRATCH, 'dlc', 'overlay', 'deadline.ark') if SCRATCH else None,
                    required=not SCRATCH)
    ap.add_argument('--check-single', action='append', default=[], help='<model>=<reference .ark of that car alone>')
    ap.add_argument('--verify-only', action='store_true')
    ap.add_argument('--mode', action='store_true',
                    help='also add the game mode Deadline (../mode/deadline_mode.py): the cars go to <out>_nomode.ark, '
                         'cars + mode to <out>')
    ap.add_argument('--mode-extra-seconds', type=float, default=0.0,
                    help='with --mode: added to every Deadline start time (default 0: the Xbox values; the DLL adds '
                         '[DLC] DeadlineBonus at run time when [DLC] Pickups=0)')
    ap.add_argument('--mode-no-per-track', action='store_true', help='with --mode: leave the track overlays alone')
    ap.add_argument('--mode-audio', help='with --mode: ../audio/dlcaudio.py\'s output folder of the Deadline package\'s '
                    'AUDIO/RELEASE/MENU (the DLCMenu bank, Deadline\'s sounds); without it the sounds are left out')
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(',') if m.strip()]
    out = os.path.abspath(a.out)
    work = os.path.dirname(out)
    base = os.path.splitext(out)[0]
    # the game folder (resident.ark is <game>\data\resident.ark) is never written to
    game = os.path.normcase(os.path.dirname(os.path.dirname(os.path.abspath(a.pc)))).rstrip('\\/')
    if os.path.normcase(out) == game or os.path.normcase(out).startswith(game + os.sep):
        sys.exit('refusing to write into the game folder: %s' % out)
    if a.mode and os.path.basename(out).lower() in ('deadline.ark', 'deadline_v2.ark'):
        sys.exit('--mode: refusing to overwrite %s (the car-only packs)' % out)
    # with --mode the car pack is built and verified as before, under another name; the mode goes on top of it
    cars_out = base + '_nomode.ark' if a.mode else out
    asm = os.path.join(work, os.path.basename(os.path.splitext(cars_out)[0]) + '_cars.ark')
    if not a.verify_only:
        build(models, a.dlc, a.pc, a.frontend, cars_out, work)
    single = []
    for s in a.check_single:
        mo, ref = s.split('=', 1)
        # the car alone, rebuilt next to the pack (kept for inspection; a rerun overwrites it)
        td = os.path.join(work, os.path.basename(base) + '_single_' + mo.lower())
        r_out = os.path.join(td, os.path.basename(ref))
        if not a.verify_only:
            os.makedirs(td, exist_ok=True)
            r_asm = os.path.join(td, mo.lower() + '.ark')
            run('assemble_car.py', ['--model', mo, '--dlc', a.dlc, '--pc', a.pc, '--out', r_asm], os.path.join(td, 'assemble.log'))
            # --no-ingame: the version the references were made with (the in-game car select came later)
            run('finish_car.py', [r_asm, os.path.splitext(r_asm)[0] + '.manifest.tsv', r_out, '--model', mo, '--dlc', a.dlc,
                                  '--pc', a.pc, '--frontend', os.path.join(a.frontend, mo.lower()), '--no-ingame'],
                os.path.join(td, 'finish.log'))
        single.append((mo, ref, r_out if os.path.isfile(r_out) else None))
    C = verify(models, a.dlc, a.pc, a.frontend, cars_out, asm, single)
    mode_keys = {}
    if a.mode:
        if C.errors:
            C.say()
            C.say('Game mode: not added, the car pack has errors')
        else:  # also with --verify-only: the mode step is cheap and rebuilds <out> from the verified car pack
            mode_keys = mode_step(cars_out, out, a.pc, a.dlc, a.mode_extra_seconds, not a.mode_no_per_track, C,
                                  menu_audio=a.mode_audio)

    # manifest: one line per entry; the car is found from the names in the path (model, AudioFile, physics file)
    pc = arkio.Ark(a.pc)
    # without --mode, or when the mode step failed before writing, the manifest lists the car pack
    ov_fn = out if (not a.mode or mode_keys) else cars_out
    ov = arkio.Ark(ov_fn)
    rows = manifest(os.path.splitext(asm)[0] + '.manifest.tsv')
    ml = [m.lower() for m in models]
    vdb = open(os.path.join(a.dlc, 'VEHICLES', 'VEHICLEDATABASE.XML'), 'rb').read().decode('latin-1')
    tokens = {}
    for mo in models:
        rec = re.search(r'<Car [^>]*ModelFile="%s"[^>]*/>' % re.escape(mo), vdb).group(0)
        tokens[mo] = {mo.lower()} | {v.lower() for v in re.findall(r'(?:AudioFile|HumanPhysicsFile)="([^"]+)"', rec)}
    with open(base + '.manifest.tsv', 'w', newline='\n', encoding='ascii') as f:
        f.write('# overlay %s: %d entries, %d bytes (stored, type 0); cars %s%s\n' % (
            os.path.basename(ov_fn), len(ov.order), os.path.getsize(ov_fn), ', '.join(models),
            '; game mode Deadline (TimeTrialPlus)' if mode_keys else ''))
        f.write('key\tsize\tresident\tcar\tmade by\tpath\tsource\n')
        for k in ov.order:
            d = ov.get(k)
            if k in mode_keys and mode_keys[k] not in SHARED:
                p = mode_keys[k]
                how = 'deadline_mode: ' + ('new' if k not in pc.ents else 'PC file + the mode')
                src = 'DLC RESIDENT_DLC.ARK / PC resident.ark (../mode/deadline_mode.py)'
                f.write('%08x\t%d\t%s\t%s\t%s\t%s\t%s\n' % (k, len(d), 'replaces' if k in pc.ents else 'new', 'mode', how, p, src))
                continue
            if k in rows and rows[k][4] != 'vehicles/vehicledatabase.xml':
                p, how, src = rows[k][4], 'assemble_car: ' + rows[k][3], rows[k][5]
            elif any(arkio.key(x) == k for x in SHARED):
                p = next(x for x in SHARED if arkio.key(x) == k)
                how, src = 'finish_car: shared file, all cars', 'PC resident.ark + all cars'
                if k in mode_keys:
                    how, src = how + ' + deadline_mode', src + ' + the mode'
            else:
                p = next('vehicles/frontend/bodies/%s/%s.%s' % (m, m, e) for m in ml for e in FRONTEND_EXT
                         if arkio.key('vehicles/frontend/bodies/%s/%s.%s' % (m, m, e)) == k)
                how, src = 'finish_car: frontend', os.path.join(a.frontend, p.split('/')[3], p.split('/')[4])
            words = set(re.split(r'[/._]', p)) | set(re.findall(r'[a-z]+_\d\d', p))
            # a vehicles/ path names its own model (Supercar_17's AudioFile is "Supercar_20", another car's model)
            own = [mo for mo in models if p.startswith('vehicles/') and mo.lower() in words]
            car = 'shared' if p in SHARED else (own[0] if own else next((mo for mo in models if tokens[mo] & words), '?'))
            f.write('%08x\t%d\t%s\t%s\t%s\t%s\t%s\n' % (k, len(d), 'replaces' if k in pc.ents else 'new', car, how, p, src))
    # what the cars reference and nobody has (from the assemble report): the exe reads these with (noassert) or
    # the base cars lack them too; listed so a missing file is a known one
    rep = open(os.path.splitext(asm)[0] + '.report.txt', encoding='utf-8').read().split('\n')
    i = next((n for n, l in enumerate(rep) if l.startswith('Referenced, in neither')), None)
    if i is not None:
        C.say()
        C.say('From %s:' % os.path.basename(os.path.splitext(asm)[0] + '.report.txt'))
        for l in rep[i:]:
            if not l.strip():
                break
            C.say(l.rstrip('\r'))
    per = {}
    for line in open(base + '.manifest.tsv', encoding='ascii'):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 7 and re.fullmatch(r'[0-9a-f]{8}', f[0]):
            per.setdefault(f[3], [0, 0])
            per[f[3]][0] += 1
            per[f[3]][1] += int(f[1])
    C.say()
    C.say('Entries per car: %s' % ', '.join('%s %d (%d bytes)' % (c, n, s) for c, (n, s) in sorted(per.items())))
    if a.mode:
        C.say('Car pack %s: %d entries, %d bytes' % (cars_out, len(arkio.Ark(cars_out).order), os.path.getsize(cars_out)))
    C.say('Overlay %s: %d entries, %d bytes' % (ov_fn, len(ov.order), os.path.getsize(ov_fn)))
    C.say('RESULT: %s' % ('OK' if not C.errors else 'ERRORS: %s' % C.errors))
    with open(base + '.report.txt', 'w', newline='\r\n', encoding='utf-8') as f:
        f.write('\n'.join(C.lines) + '\n')
    print('wrote', base + '.manifest.tsv', base + '.report.txt')
    return 0 if not C.errors else 1


if __name__ == '__main__':
    sys.exit(main())
