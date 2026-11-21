# Pair the entries of an Xbox 360 and a PC .ark by key (same path = same key) and classify the file kinds:
# identical, the same after a 4-byte (or 2-byte) swap, same size but different, or different size.
# Usage: python -I arkcmp.py <xbox.ark> <pc.ark> [samples per kind]
import os
import struct
import sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pair_lib import lzss_n  # noqa: E402  (the same LZSS as the old scratchpad ark.py)

MAXSIZE = 1 << 20


class Ark:
    def __init__(self, fn):
        self.f = open(fn, 'rb')
        head = self.f.read(24)
        self.E = '>' if head[:4] == b'\x00\x01\x00\x00' else '<'
        ver, hsize, cA, offA, cB, offB = struct.unpack_from(self.E + '6I', head, 0)
        self.f.seek(8 + offA)
        idx = self.f.read(20 * cA)
        self.ents = {}
        for k in range(cA):
            t, off, cs, us, kk = struct.unpack_from(self.E + '5I', idx, 20 * k)
            self.ents[kk] = (t, off, cs, us)

    def get(self, k):
        t, off, cs, us = self.ents[k]
        self.f.seek(off)
        raw = self.f.read(cs + 16)
        if t == 0:
            return raw[:us]
        if t == 1:
            return lzss_n(raw + b'\0' * 16, 4, us)[0]
        return None


def swap(b, n):
    a = bytearray(b)
    for i in range(0, len(b) - len(b) % n, n):
        a[i:i + n] = b[i:i + n][::-1]
    return bytes(a)


def kind(data, be):
    m = data[:4]
    if be and m[:1] != b'\0' and m.isalpha() and m.upper() == m:
        return m[::-1].decode()  # Xbox magics are the PC's reversed
    try:
        s = m.decode('ascii')
        if all(32 <= c < 127 for c in m):
            return s
    except Exception:
        pass
    return m.hex()


xb, pc = Ark(sys.argv[1]), Ark(sys.argv[2])
per = int(sys.argv[3]) if len(sys.argv) > 3 else 4
common = [k for k in xb.ents if k in pc.ents]
print('xbox %d entries, pc %d, common %d, only xbox %d, only pc %d' % (len(xb.ents), len(pc.ents), len(common),
      len(xb.ents) - len(common), len(pc.ents) - len(common)))
stats = defaultdict(lambda: defaultdict(int))
seen = defaultdict(int)
for k in common:
    if xb.ents[k][3] > MAXSIZE or pc.ents[k][3] > MAXSIZE:
        continue
    x = xb.get(k)
    p = pc.get(k)
    if x is None or p is None:
        continue
    kd = kind(p, False)
    if seen[kd] >= per:
        continue
    seen[kd] += 1
    if x == p:
        v = 'identical'
    elif len(x) == len(p):
        s4 = sum(a == b for a, b in zip(swap(x, 4), p)) / max(1, len(p))
        s2 = sum(a == b for a, b in zip(swap(x, 2), p)) / max(1, len(p))
        v = 'swap4 %.0f%%' % (s4 * 100) if s4 >= s2 else 'swap2 %.0f%%' % (s2 * 100)
        v = 'same size, ' + v
    else:
        v = 'size %d vs %d' % (len(x), len(p))
    stats[kd][v] += 1
for kd in sorted(stats):
    print('%-12s %s' % (kd, dict(stats[kd])))
