# Test variant of a car overlay for a first in-game check: an existing, selectable PC car (default
# Musclecar_01, the first car of the car select) is driven with the DLC car's body, handling and engine sound.
#
# The DLC car's converted body files are stored under the donor's file names
# (vehicles/bodies/<donor>/<donor>*.*, and its lights), so the donor's record keeps its ID and ModelFile:
# the car select's frontend model and colour palettes stay the PC's. That matters: the game registers the
# colour palettes under each record's ID (0x41E810) but the car select looks them up under the ModelFile
# (0x484664, record + 0x28), so a record whose ModelFile differs from its ID crashes the car select.
# Only HumanPhysicsFile (the DLC car's handling) and AudioFile (its engine sound) change in the record.
# The renamed .params files are tweakable dumps whose paths carry the model name: those paths are renamed too.
#   python -I swaptest.py <car overlay.ark> <its manifest.tsv> <out.ark> [--donor Musclecar_01] [--dlc Musclecar_17]
import argparse
import os
import re
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'misc'))
from arkio import Ark, key, write_ark

# the PC resident.ark: --pc, else below the game folder in GAME_DIR
PC_RESIDENT = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None

ap = argparse.ArgumentParser()
ap.add_argument('overlay')
ap.add_argument('manifest')
ap.add_argument('out')
ap.add_argument('--donor', default='Musclecar_01')
ap.add_argument('--dlc', default='Musclecar_17')
ap.add_argument('--pc', default=PC_RESIDENT, required=not PC_RESIDENT,
                help='the PC data\\resident.ark (default: below GAME_DIR, the game folder)')
a = ap.parse_args()
donor, dlc = a.donor.lower(), a.dlc.lower()

pc = Ark(a.pc)
ov = Ark(a.overlay)
paths = {}
for line in open(a.manifest, encoding='utf-8'):
    f = line.rstrip('\n').split('\t')
    if len(f) >= 5 and re.fullmatch(r'[0-9a-f]{8}', f[0]):
        paths[int(f[0], 16)] = f[4]

# both records, which also give the names as the game spells them (the tweakable paths use that spelling)
dbkey = key('vehicles/vehicledatabase.xml')
db = pc.get(dbkey).decode('latin-1')
dlc_db = ov.get(dbkey).decode('latin-1')
m = re.search(r'<Car [^>]*ModelFile="(%s)"[^>]*/>' % re.escape(a.dlc), dlc_db, re.I)
if not m:
    sys.exit('no record with ModelFile %s in the overlay' % a.dlc)
dlc_name, dlc_rec = m.group(1), m.group(0)
audio = re.search(r'AudioFile="([^"]+)"', dlc_rec).group(1)
phys = re.search(r'HumanPhysicsFile="([^"]+)"', dlc_rec).group(1)
md = re.search(r'<Car [^>]*ID="(%s)"[^>]*/>' % re.escape(a.donor), db, re.I)
if not md:
    sys.exit('no PC record with ID %s' % a.donor)
donor_name, rec = md.group(1), md.group(0)

files = {}
for k, p in paths.items():
    data = ov.get(k)
    if p.startswith('vehicles/bodies/%s/' % dlc):
        q = p.replace(dlc, donor)  # folder and file names
    elif p == 'vehicles/lights/%s_lights.params' % dlc:
        q = p.replace(dlc, donor)
    elif p.startswith('audio/tonic/') or p.startswith('vehicles/handling/configs/%s.' % dlc):
        q = p  # looked up through the record's AudioFile and HumanPhysicsFile
    else:
        continue  # merged lists and the frontend name: the PC's stay
    if q != p and q.endswith('.params'):
        # tweakable dumps register their values under the model's name ('!directory:
        # /Vehicles/DynamicParts/Musclecar_17', /VehicleLights/..., /Vehicles/DeformationControl/...,
        # /Vehicles/SurfaceDamage/...) and the game looks them up under the donor's: without this the
        # spoiler, deformation and lights run on defaults (the extending spoiler flies off)
        data, n = re.subn(re.escape(('/%s' % dlc_name).encode()), ('/%s' % donor_name).encode(), data, flags=re.I)
        if n == 0:
            sys.exit('%s: no /%s path inside' % (p, dlc_name))
    files[key(q)] = data
    print('%-60s <- %s' % (q, p))

# the record: the donor keeps ID and ModelFile, takes the DLC car's handling and engine sound
new = re.sub(r'HumanPhysicsFile="[^"]+"', 'HumanPhysicsFile="%s"' % phys, rec)
new = re.sub(r'AudioFile="[^"]+"', 'AudioFile="%s"' % audio, new)
files[dbkey] = (db[:md.start()] + new + db[md.end():]).encode('latin-1')
print('record', donor_name, ': HumanPhysicsFile', phys, 'AudioFile', audio)

n = write_ark(a.out, sorted(files.items()))
print('written', a.out, n, 'entries', os.path.getsize(a.out), 'bytes')
