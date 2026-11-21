# Oracle test: convert every base-game Xbox .tonic/.tonic2 and compare byte for byte with the PC file.
# usage: python -I test_tonic.py [xbox resident.ark] [pc resident.ark]
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from pair_lib import Ark, XB, PC  # noqa: E402
import tonic  # noqa: E402

xb = Ark(sys.argv[1] if len(sys.argv) > 1 else XB)
pc = Ark(sys.argv[2] if len(sys.argv) > 2 else PC)

names = set()
db = pc.get('vehicles/vehicledatabase.xml') or b''
names.update(n.decode().lower() for n in re.findall(rb'AudioFile="([^"]+)"', db))
for pre in ('hypercar', 'hypercar2', 'muscle', 'muscle2', 'supercar', 'supercar2', 'truck', 'truck2'):
    for k in range(1, 31):
        names.add('%s_%02d' % (pre, k))

ok = bad = 0
for n in sorted(names):
    for ext in ('tonic', 'tonic2'):
        p = 'audio/tonic/_outputfiles/%s.%s' % (n, ext)
        a, b = xb.get(p), pc.get(p)
        if a is None or b is None:
            if a is not None or b is not None:
                print('only on', 'xbox' if a is not None else 'pc', p)
            continue
        try:
            c, info = tonic.convert(a)
        except tonic.TonicError as e:
            print('FAIL parse', p, e)
            bad += 1
            continue
        if c == b:
            ok += 1
        else:
            d = [i for i in range(min(len(b), len(c))) if b[i] != c[i]]
            print('DIFF', p, len(c), len(b), len(d), 'first at 0x%X' % d[0] if d else '')
            bad += 1
print('identical', ok, 'failed', bad)
