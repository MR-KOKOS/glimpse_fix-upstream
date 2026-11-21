# Do the DLC car's shader IDs exist on the PC already? Collect the "0x........" IDs from the DLC car's
# Xbox .shaders/.fxc and from the PC base cars' .shaders files.
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location('pair', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pair_lib.py'))

ID = re.compile(rb'0x[0-9a-f]{8}')
dlc_dir = 'x_deadline/VEHICLES/BODIES/MUSCLECAR_17'
dlc = {}
for fn in os.listdir(dlc_dir):
    if fn.endswith('.SHADERS') or fn.endswith('.FXC'):
        for m in ID.findall(open(os.path.join(dlc_dir, fn), 'rb').read()):
            dlc.setdefault(m, set()).add(fn)
print('DLC MUSCLECAR_17 shader ids:', len(dlc))

from pair_lib import Ark, PC  # noqa
pc = Ark(PC)
found = {}
names = ['musclecar_%02d' % n for n in range(1, 17)] + ['supercar_%02d' % n for n in range(1, 17)] + ['truck_%02d' % n for n in range(1, 9)]
lods = ['_high', '_mid', '_low', '_xlow', '_bw_high', '_bw_mid', '_bw_low', '_fw_high', '_fw_mid', '_fw_low']
for car in names:
    for lod in lods:
        data = pc.get('vehicles/bodies/%s/%s%s.shaders' % (car, car, lod))
        if not data:
            continue
        for m in ID.findall(data):
            found.setdefault(m, '%s%s' % (car, lod))
print('PC base car shader ids:', len(found))
miss = [m for m in dlc if m not in found]
print('DLC ids found on PC: %d of %d' % (len(dlc) - len(miss), len(dlc)))
for m in miss:
    print('  missing', m.decode(), sorted(dlc[m])[:3])
