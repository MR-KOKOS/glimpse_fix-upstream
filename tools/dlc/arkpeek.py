# peek into a Split/Second .ark (PC little-endian or Xbox 360 big-endian): entry types and the magic of each file
import os
import struct
import sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pair_lib import lzss_n  # noqa: E402  (the same LZSS as the old scratchpad ark.py)

fn = sys.argv[1]
d = open(fn, 'rb').read()
be = d[:4] == b'\x00\x01\x00\x00'
E = '>' if be else '<'
ver, hsize, cA, offA, cB, offB = struct.unpack_from(E + '6I', d, 0)
ents = [struct.unpack_from(E + '5I', d, 8 + offA + 20 * k) for k in range(cA)]
print('big-endian' if be else 'little-endian', 'entries', cA, 'types', Counter(e[0] for e in ents))
mag = Counter()
samples = {}
for e in ents:
    t, off, cs, us, key = e
    try:
        if t == 0:
            data = d[off:off + min(us, 256)]
        elif t == 1:
            data, _ = lzss_n(d + b'\0' * 16, off + 4, min(us, 256))
        else:
            continue
    except Exception as ex:
        continue
    m = data[:4]
    mag[m] += 1
    samples.setdefault(m, (key, us, data[:96]))
for m, n in mag.most_common(20):
    key, us, data = samples[m]
    print(m, n, 'e.g. key %08X size %d' % (key, us))
    print('   ', data[:96].hex(' '))
