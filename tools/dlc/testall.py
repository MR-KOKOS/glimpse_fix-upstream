# Converts every base car model of the Xbox resident archive and compares it with the PC's, as the PC loader
# reads it: index lists, vertex bytes, the main block apart from the buffer offsets, declarations, fixups.
import os
import struct
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pair_lib import Ark, XB, PC
from mdl import Model
import convmodel as C

xb, pc = Ark(XB), Ark(PC)
cars = ['musclecar_%02d' % n for n in range(1, 17)] + ['supercar_%02d' % n for n in range(1, 17)] + ['truck_%02d' % n for n in range(1, 9)]
lods = ['_xlow', '_low', '_mid', '_high', '_bw_high', '_bw_mid', '_bw_low', '_fw_high', '_fw_mid', '_fw_low']
ok = bad = 0
t0 = time.time()
for car in cars:
    for lod in lods:
        path = 'vehicles/bodies/%s/%s%s.model' % (car, car, lod)
        xd = xb.get(path)
        pd = pc.get(path)
        if xd is None or pd is None:
            continue
        try:
            res, ibs, vbs = C.convert(xd)
            me = Model(res, False)
            p = Model(pd, False)
            a, b = C.semantic(p), C.semantic(me)
            skip = {r + 12 for r, *_ in ibs} | {r + 4 for r, *_ in vbs}
            blockdiff = [o for o in range(0, len(p.block), 4) if p.block[o:o + 4] != me.block[o:o + 4] and o not in skip]
            good = a == b and not blockdiff and p.decls == me.decls and p.fixups == me.fixups and len(p.block) == len(me.block)
        except Exception as e:
            good = False
            blockdiff = ['exception %s' % e]
        if good:
            ok += 1
        else:
            bad += 1
            print('MISMATCH', car + lod, 'ib', a[0] == b[0] if 'a' in dir() else '?', 'vb', a[1] == b[1] if 'a' in dir() else '?',
                  'block', [hex(o) if isinstance(o, int) else o for o in blockdiff[:6]])
print('models equal as the PC reads them: %d, mismatches: %d (%.0f s)' % (ok, bad, time.time() - t0))
