# byte-level comparison of the Xbox and PC copies of one car: identical, the same after swapping 4-byte
# (or 2-byte) words, or different; with the first bytes of each
import os
import sys

name = sys.argv[1]
xd, pd = 'pair_xb/' + name, 'pair_pc/' + name


def swap(b, n):
    out = bytearray(b)
    for i in range(0, len(b) - len(b) % n, n):
        out[i:i + n] = b[i:i + n][::-1]
    return bytes(out)


for fn in sorted(os.listdir(xd)):
    x = open(os.path.join(xd, fn), 'rb').read()
    p = open(os.path.join(pd, fn), 'rb').read() if os.path.exists(os.path.join(pd, fn)) else b''
    if x == p:
        verdict = 'identical'
    elif len(x) == len(p):
        s4 = swap(x, 4)
        same4 = sum(a == b for a, b in zip(s4, p)) / len(p)
        same = sum(a == b for a, b in zip(x, p)) / len(p)
        verdict = 'same size: %.0f%% equal as is, %.0f%% after 4-byte swap' % (same * 100, same4 * 100)
    else:
        verdict = 'different size'
    print('%-40s %-60s' % (fn, verdict))
    print('    xb', x[:32].hex(' '))
    print('    pc', p[:32].hex(' '))
