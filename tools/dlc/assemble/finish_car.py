# Turns the overlay assemble_car.py wrote for one DLC car (or several cars of a pack) into the overlay that makes
# each car its own, selectable entry of the PC car select.
#
#  - VehicleDatabase.xml: the DLC record keeps its table slot (the Placeholder_NN record it replaced, so the
#    profile's per-car data stays aligned) but its ID becomes its ModelFile. The car select registers a car's
#    colour palettes under the record ID (0x41E810) and looks them up under the ModelFile (0x484664); every PC
#    car has both equal, and a record with different ones crashes the car select.
#  - the car select's model: the converted frontend files (tools/dlc/frontend) or, for a test, a PC car's
#    frontend files stored under the DLC car's paths (--frontend-standin Musclecar_10).
#  - the car select's name plate and picture: the PC's FE_vehicleSelect.gfx with one frame per DLC car added
#    (../ui/gfxcars.py) and the pictures' atlases added to the PC's Flash texture set (../ui/flashtex.py).
#  - the in-game car select's picture (career: in-game event select -> event without a fixed car -> screen 0x54
#    "Car Select", UI/InGame/VehicleSelect/VehicleSelect.gfx): one frame per DLC car in its picture sprite
#    (gfxcars.py) and the DLC atlases (the Xbox in-game FE_carSelect_set3/set4, streamed set InGame3) added to
#    the PC's resident in-game set UI/InGame/Textures/InGame.textures + .filenames as FE_carSelectDLC1, 2, ...
#    The name under the picture comes from the exe (AddVehicle(ID, TEXT_<ID>), see step 4). --no-ingame leaves
#    this part out (the output of the earlier version, for the byte-identical reference checks).
#
#   python -I finish_car.py <assembled overlay.ark> <its manifest.tsv> <out.ark>
#       [--model Musclecar_17 [--model Musclecar_18 ...]] [--dlc <Deadline package dir>]
#       [--frontend <dir> | --frontend-standin <PC car>] [--no-ingame]
#
# Several --model: the assembled overlay must hold all of them (assemble_car.py --model A,B,...); every shared
# file (VehicleDatabase.xml, directory.txt, FE_vehicleSelect.gfx, Flash.textures/.filenames, the dictionaries)
# then gets all of them in one go, and the cars get their CarSelectOrder in the order the --model options give.
# --frontend <dir> holds <model>.<ext> (one car) or <model>/<model>.<ext> (the frontend_out layout).
import argparse
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'misc'))
sys.path.insert(0, os.path.join(HERE, '..', 'ui'))
from arkio import Ark, key, write_ark  # noqa: E402
import dictadd  # noqa: E402
import flashtex  # noqa: E402
import gfxcars  # noqa: E402
sys.path.insert(0, HERE)
import cartext  # noqa: E402

# a manual run's PC resident.ark: below the game folder in GAME_DIR, else --pc must be given (build_pack.py gives it)
PC_RESIDENT = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None
FRONTEND_EXT = ('fxc', 'model', 'model.stream', 'shaderinst', 'shaders', 'streamtex', 'textures')
# the DLC's picture atlases are renamed so they do not replace the PC's own atlases of the same name: the first
# one copied is FE_VSelectDLC1, the next FE_VSelectDLC2, ... (gfxcars --image-prefix). FE_VSelect05 is a 3-column
# atlas of the seven Xbox DLC cars (Placeholder_05..11); each car frame shows its cell through the picture
# sprite's clip mask. All four Deadline cars are on it, so the pack needs one: FE_VSelect05 -> FE_VSelectDLC1.
IMAGE_PREFIX = 'FE_VSelectDLC'
# the in-game car select: the Xbox DLC's VehicleSelect.gfx shows Placeholder_09/10 on its FE_carSelect_set3
# (1024x1024; the PC's set3 is a 1024x256 atlas of other cars) and Placeholder_08/11 on FE_carSelect_set4
# (1024x256), both in the Xbox's streamed set InGame3. They are copied under FE_carSelectDLC1, FE_carSelectDLC2, ...
# into the resident in-game set, which the game loads with the in-game UI (0x79AA70: TextureSet 4, while InGame1/2/3
# and Hud are streamed sets 5/6/7/3); the images of one movie come from all of them (VehicleSelect.gfx uses
# InGame, InGame2 and InGame3 images), so a name is found in any of them.
IG_IMAGE_PREFIX = 'FE_carSelectDLC'
IG_GFX = 'ui/ingame/vehicleselect/vehicleselect.gfx'
IG_SET = 'ui/ingame/textures/ingame'
# the DLC's in-game texture containers an image may come from: (file, its .streamtex or None)
IG_SOURCES = (('INGAME3.TEXTURES', 'INGAME3.STREAMTEX'), ('INGAME.TEXTURES', None), ('INGAME4.TEXTURES', None))

# the names and the description rule (TEXT_<ID>, TEXT_CARDESCR_<ID>): cartext.py
LANGS = cartext.LANGS + ('StringIDs',)
NAMES = cartext.NAMES
if cartext.check_rule():
    sys.exit('the description rule no longer gives the first build\'s lines: %s' % cartext.check_rule())

ap = argparse.ArgumentParser()
ap.add_argument('overlay')
ap.add_argument('manifest')
ap.add_argument('out')
ap.add_argument('--model', action='append', help='a car of the overlay; repeat for several (default Musclecar_17)')
ap.add_argument('--dlc', required=True, help='the Deadline package folder (x_deadline)')
ap.add_argument('--pc', default=PC_RESIDENT)
g = ap.add_mutually_exclusive_group(required=True)
g.add_argument('--frontend', help='folder with the converted frontend files <model>.<ext> or <model>/<model>.<ext>')
g.add_argument('--frontend-standin', help='a PC car whose frontend files stand in (test only)')
ap.add_argument('--no-ingame', action='store_true', help='leave the in-game car select out (the earlier output)')
a = ap.parse_args()
if not a.pc:
    ap.error('--pc is needed: the PC data\\resident.ark (or set GAME_DIR to the Split/Second game folder)')
models = a.model or ['Musclecar_17']
if len({m.lower() for m in models}) != len(models):
    sys.exit('a --model is given twice')
for model in models:
    if model not in NAMES:
        sys.exit('%s: no name in NAMES' % model)

pc = Ark(a.pc)
ov = Ark(a.overlay)
files = {}
for line in open(a.manifest, encoding='utf-8'):
    f = line.rstrip('\n').split('\t')
    if len(f) >= 5 and re.fullmatch(r'[0-9a-f]{8}', f[0]):
        k = int(f[0], 16)
        files[k] = ov.get(k)
if len(files) != len(ov.ents):
    sys.exit('manifest lists %d entries, the overlay has %d' % (len(files), len(ov.ents)))

# 1. the records: ID = ModelFile, in the slot of the Placeholder record each replaced
dbkey = key('vehicles/vehicledatabase.xml')
db = files[dbkey].decode('latin-1')
spans = []
for model in models:
    recs = list(re.finditer(r'<Car [^>]*ModelFile="%s"[^>]*/>' % re.escape(model), db))
    if len(recs) != 1:
        sys.exit('%d records with ModelFile %s' % (len(recs), model))
    if re.search(r' ID="%s"' % re.escape(model), db):
        sys.exit('a record with ID %s exists already' % model)
    spans.append(recs[0])
# the car select position: after the PC's cars (their orders run 1..N without gaps), not the DLC's numbers, which
# count the Xbox's other DLC cars; the cars of this overlay follow one another in the --model order
rest = db
for m in sorted(spans, key=lambda m: -m.start()):
    rest = rest[:m.start()] + rest[m.end():]
orders = [int(x) for x in re.findall(r' CarSelectOrder="(-?\d+)"', rest) if int(x) > 0]
order0 = max(orders) + 1
old_ids, new_db, pos = {}, [], 0
for m in sorted(spans, key=lambda m: m.start()):
    model = re.search(r' ModelFile="([^"]+)"', m.group(0)).group(1)
    model = next(x for x in models if x == model)
    old_id = re.search(r' ID="([^"]+)"', m.group(0)).group(1)
    rec = m.group(0).replace(' ID="%s"' % old_id, ' ID="%s"' % model, 1)
    order = order0 + models.index(model)
    rec, nsub = re.subn(r' CarSelectOrder="-?\d+"', ' CarSelectOrder="%d"' % order, rec)
    if nsub != 1:
        sys.exit('the record has %d CarSelectOrder attributes' % nsub)
    new_db += [db[pos:m.start()], rec]
    pos = m.end()
    old_ids[model] = old_id
files[dbkey] = (''.join(new_db) + db[pos:]).encode('latin-1')
for model in models:
    print('record %s -> ID %s (slot kept), CarSelectOrder %d' % (old_ids[model], model, order0 + models.index(model)))

# 1b. directory.txt: the VEHICLES section lists the body folders the vehicle compositor sets up (0x422B50 ->
# 0x422FD0: one 0x580-byte entry per name, room for 64; the PC lists 50). A car missing there is drawn without
# its paint job (no stripes, no stickers). The names are appended at the end of the section, so every existing
# entry keeps its position.
dirkey = key('directory.txt')
dtext = (files.get(dirkey) or pc.get(dirkey)).decode('latin-1')
nl = '\r\n' if '\r\n' in dtext else '\n'
for model in models:
    start = dtext.find('-Vehicles/Bodies/*' + nl)
    if start < 0:
        sys.exit('directory.txt: no -Vehicles/Bodies/* block')
    end = dtext.find(nl + '-', start)
    end = len(dtext) if end < 0 else end + len(nl)
    block = dtext[start:end]
    names = [t.strip() for t in block.split(nl)[3:] if t.strip()]
    if ':VEHICLES' not in block:
        sys.exit('directory.txt: the Vehicles/Bodies block is not the VEHICLES section')
    if model.lower() in (n.lower() for n in names):
        print('directory.txt: %s is listed already' % model)
        continue
    if len(names) + 1 > 64:
        sys.exit('directory.txt: VEHICLES would have %d names, the compositor holds 64' % (len(names) + 1))
    body = block if block.endswith(nl) else block + nl
    dtext = dtext[:start] + body + '\t' + model + nl + dtext[end:]
    files[dirkey] = dtext.encode('latin-1')
    print('directory.txt: %s appended to VEHICLES (%d names)' % (model, len(names) + 1))

# 2. the frontend models
for model in models:
    ml = model.lower()
    fdir = 'vehicles/frontend/bodies/%s/' % ml
    if a.frontend:
        src_dir = a.frontend
        if not os.path.isfile(os.path.join(src_dir, '%s.model' % ml)):
            src_dir = os.path.join(a.frontend, ml)
        for ext in FRONTEND_EXT:
            src = os.path.join(src_dir, '%s.%s' % (ml, ext))
            if not os.path.isfile(src):
                sys.exit('frontend file missing: %s' % src)
            files[key(fdir + '%s.%s' % (ml, ext))] = open(src, 'rb').read()
        print('frontend %s: converted files from %s' % (model, src_dir))
    else:
        sl = a.frontend_standin.lower()
        for ext in FRONTEND_EXT:
            d = pc.get(key('vehicles/frontend/bodies/%s/%s.%s' % (sl, sl, ext)))
            if d is None:
                sys.exit('PC car %s has no frontend .%s' % (a.frontend_standin, ext))
            files[key(fdir + '%s.%s' % (ml, ext))] = d
        print('frontend %s: STAND-IN, the PC files of %s' % (model, a.frontend_standin))
    if key(fdir + 'chassisname.txt') not in files:
        sys.exit('the assembled overlay lacks %schassisname.txt' % fdir)

# 3. name plates, pictures and the pictures' atlases
dlc_gfx = os.path.join(a.dlc, 'UI', 'FRONTEND', 'FLASH', 'FE_VEHICLESELECT.GFX')
for model in models:
    info = gfxcars.frame_info(dlc_gfx, old_ids[model])
    texts = [t.strip() for _, tx, _ in info for t in tx if t.strip()]
    if ' '.join(texts) != NAMES[model]:
        sys.exit('%s: the DLC name plate %s says %r, NAMES has %r' % (model, old_ids[model], ' '.join(texts), NAMES[model]))
    pics = [p for _, _, ps in info for p in ps]
    print('DLC frame %s (%s): name %r, pictures %s' % (old_ids[model], model, ' '.join(texts), ', '.join(
        '%s %dx%d [%d..%d, %d..%d]' % ((n, w, h) + r) for n, w, h, r in pics)))
tmpdir = tempfile.TemporaryDirectory(prefix='finish_car_')
tmp = tmpdir.name
pc_gfx = os.path.join(tmp, 'pc.gfx')
open(pc_gfx, 'wb').write(pc.get(key('ui/frontend/flash/fe_vehicleselect.gfx')))
out_gfx = os.path.join(tmp, 'fe_vehicleselect.gfx')
images = gfxcars.main([pc_gfx, dlc_gfx, out_gfx, '--image-prefix', IMAGE_PREFIX]
                      + ['%s=%s' % (old_ids[m], m) for m in models])
files[key('ui/frontend/flash/fe_vehicleselect.gfx')] = open(out_gfx, 'rb').read()

tin = os.path.join(tmp, 'in')
os.makedirs(tin)
open(os.path.join(tin, 'Flash.textures'), 'wb').write(pc.get(key('ui/frontend/flash/textures/flash.textures')))
open(os.path.join(tin, 'Flash.filenames'), 'wb').write(pc.get(key('ui/frontend/flash/textures/flash.filenames')))
tout = os.path.join(tmp, 'out')
flashtex.main([os.path.join(tin, 'Flash.textures'), os.path.join(tin, 'Flash.filenames'),
               os.path.join(a.dlc, 'UI', 'FRONTEND', 'FLASH', 'TEXTURES', 'DLC.TEXTURES'), tout]
              + ['%s=%s' % kv for kv in images.items()])
files[key('ui/frontend/flash/textures/flash.textures')] = open(os.path.join(tout, 'Flash.textures'), 'rb').read()
files[key('ui/frontend/flash/textures/flash.filenames')] = open(os.path.join(tout, 'Flash.filenames'), 'rb').read()

# 3b. the in-game car select: picture frames and their atlases
if not a.no_ingame:
    ig_dlc = os.path.join(a.dlc, 'UI', 'INGAME', 'VEHICLESELECT', 'VEHICLESELECT.GFX')
    for model in models:
        info = gfxcars.frame_info(ig_dlc, old_ids[model])
        pics = [p for _, _, ps in info for p in ps]
        if not pics:
            sys.exit('%s: the DLC in-game frame %s shows no picture' % (model, old_ids[model]))
        print('DLC in-game frame %s (%s): pictures %s' % (old_ids[model], model, ', '.join(
            '%s %dx%d [%d..%d, %d..%d]' % ((n, w, h) + r) for n, w, h, r in pics)))
    ig_pc = os.path.join(tmp, 'ig_pc.gfx')
    open(ig_pc, 'wb').write(pc.get(key(IG_GFX)))
    ig_out = os.path.join(tmp, 'vehicleselect.gfx')
    ig_images = gfxcars.main([ig_pc, ig_dlc, ig_out, '--image-prefix', IG_IMAGE_PREFIX]
                             + ['%s=%s' % (old_ids[m], m) for m in models])
    files[key(IG_GFX)] = open(ig_out, 'rb').read()
    # each image from the DLC in-game container that has it (one call per container), into the resident set
    xdir = os.path.join(a.dlc, 'UI', 'INGAME', 'TEXTURES')
    cur = os.path.join(tmp, 'ig0')
    os.makedirs(cur)
    open(os.path.join(cur, 'InGame.textures'), 'wb').write(pc.get(key(IG_SET + '.textures')))
    open(os.path.join(cur, 'InGame.filenames'), 'wb').write(pc.get(key(IG_SET + '.filenames')))
    todo = dict(ig_images)
    for n, (tex, st) in enumerate(IG_SOURCES):
        if not todo or not os.path.isfile(os.path.join(xdir, tex)):
            continue
        try:
            have = {s.rsplit('_', 1)[0] for _, s, _, _ in flashtex.xbox_set(
                open(os.path.join(xdir, tex), 'rb').read(), open(os.path.join(xdir, st), 'rb').read() if st else None)}
        except flashtex.convtex.FormatError as ex:
            print('%s: not converted (%s), not a source' % (tex, ex))
            continue
        mine = {o: nw for o, nw in todo.items() if o in have}
        if not mine:
            continue
        nxt = os.path.join(tmp, 'ig%d' % (n + 1))
        flashtex.main([os.path.join(cur, 'InGame.textures'), os.path.join(cur, 'InGame.filenames'), os.path.join(xdir, tex), nxt]
                      + (['--stream', os.path.join(xdir, st)] if st else []) + ['%s=%s' % kv for kv in mine.items()])
        cur = nxt
        for o in mine:
            del todo[o]
    if todo:
        sys.exit('in-game images in no DLC in-game texture container: %s' % ', '.join(todo))
    files[key(IG_SET + '.textures')] = open(os.path.join(cur, 'InGame.textures'), 'rb').read()
    files[key(IG_SET + '.filenames')] = open(os.path.join(cur, 'InGame.filenames'), 'rb').read()
tmpdir.cleanup()

# 4. the car select's description line (TEXT_CARDESCR_<ID>): the PC's dictionaries have none for the DLC
# cars and the DLC packages bring no dictionary, so a short line made from the record's stats is added
# (StringIDs maps the key to its lower-case form, as for every other key)
# The car's name for every screen that writes it as text (TEXT_<ID>: HUD, results, lobby) is added too: the
# PC's dictionaries have the brand and model words but not this key; the name is the one on the DLC's plate.
ndb = files[dbkey].decode('latin-1')
descr = {}
# the official descriptions of Title Update 4 when a developer's table is set (GLIMPSE_TU4_TEXTS, ../mode/tu4text.py)
tu4 = cartext.tu4_table()
print('descriptions: %s' % ('TU4 text table %s' % tu4.describe() if tu4 else
                            'no TU4 text table (%s not set), generated lines' % 'GLIMPSE_TU4_TEXTS'))
for model in models:
    rec = re.search(r'<Car ID="%s"[^>]*/>' % re.escape(model), ndb).group(0)
    stats = cartext.stats_of(rec)
    got = {lang: cartext.description(lang, stats, model, tu4) for lang in cartext.LANGS}
    descr[model] = {lang: t for lang, (t, _) in got.items()}
    src = {s for _, s in got.values()}
    if len(src) != 1:
        sys.exit('%s: mixed description sources %s' % (model, sorted(src)))
    src = src.pop()
    if src == 'generated':
        for lang, want in cartext.CHECK.get(model, {}).items():
            if descr[model][lang] != want:
                sys.exit('%s %s: the rule gives %r, the first build had %r' % (model, lang, descr[model][lang], want))
    for lang in cartext.LANGS:
        print('%s %s (%s): %s' % (model, lang, src, descr[model][lang]))
for lang in LANGS:
    path = 'dictionary/%s_win32.dct' % lang.lower()
    d = pc.get(key(path))
    if d is None:
        sys.exit('no %s' % path)
    pairs = []
    for model in models:
        if lang == 'StringIDs':
            pairs += [(k, k.lower()) for k in ('TEXT_CARDESCR_%s' % model, 'TEXT_%s' % model)]
        else:
            pairs += [('TEXT_CARDESCR_%s' % model, descr[model][lang]), ('TEXT_%s' % model, NAMES[model])]
    files[key(path)] = dictadd.add_checked(d, pairs)
print('names and descriptions of %d car%s added to %d dictionaries' % (len(models), 's' if len(models) > 1 else '', len(LANGS)))

n = write_ark(a.out, sorted(files.items()))
# read back: every entry as written
chk = Ark(a.out)
bad = [k for k, v in files.items() if chk.get(k) != v]
if bad:
    sys.exit('read back differs for %d entries' % len(bad))
print('written %s: %d entries, %d bytes' % (a.out, n, os.path.getsize(a.out)))
