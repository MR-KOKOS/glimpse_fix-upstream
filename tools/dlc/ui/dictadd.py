# Adds text entries to the PC game's string dictionaries (Dictionary/<LANG>_WIN32.dct).
#
# Format (parser 0xA15220, lookup 0xA151D0/0xA15BF0):
#   u32 'DICT', u32 0x2000, u32 seed, u32 rel -> table, u32 slots, u32 key mode, u32 rel -> table2, u32 count2
#   table: <slots> x {u32 hash, u32 rel -> string, u32 rel -> formatting runs}, an open-addressing hash table
#   (home slot = hash % slots, linear probing, an entry with no string ends the probe). Relative pointers point
#   to (address of the field + value + 1). The hash is Bob Jenkins' lookup2 (0xA180B0) of the key with the
#   seed as init value; the key is read up to the first character that is not printable ASCII, and key mode 1
#   lower-cases A-Z (0xA15AC0). Strings are UTF-8 with a NUL. table2 is empty in every PC file.
# A new key goes into the first free slot from its home slot on, its string is appended at the end of the file.
# Only keys the file does not have are added, so the game's own texts never change.
#   python -I dictadd.py <in.dct> <out.dct> KEY=TEXT [...]
import re
import struct
import sys

sys.dont_write_bytecode = True
M = 0xFFFFFFFF


def _mix(a, b, c):
    a = (a - b - c) & M; a ^= c >> 13
    b = (b - c - a) & M; b ^= (a << 8) & M
    c = (c - a - b) & M; c ^= b >> 13
    a = (a - b - c) & M; a ^= c >> 12
    b = (b - c - a) & M; b ^= (a << 16) & M
    c = (c - a - b) & M; c ^= b >> 5
    a = (a - b - c) & M; a ^= c >> 3
    b = (b - c - a) & M; b ^= (a << 10) & M
    c = (c - a - b) & M; c ^= b >> 15
    return a, b, c


def lookup2(k, init):
    a = b = 0x9e3779b9
    c = init
    n, i = len(k), 0
    while n - i >= 12:
        a = (a + int.from_bytes(k[i:i + 4], 'little')) & M
        b = (b + int.from_bytes(k[i + 4:i + 8], 'little')) & M
        c = (c + int.from_bytes(k[i + 8:i + 12], 'little')) & M
        a, b, c = _mix(a, b, c)
        i += 12
    c = (c + n) & M
    r = k[i:]
    for j, sh in ((10, 24), (9, 16), (8, 8)):
        if len(r) > j: c = (c + (r[j] << sh)) & M
    for j, sh in ((7, 24), (6, 16), (5, 8), (4, 0)):
        if len(r) > j: b = (b + (r[j] << sh)) & M
    for j, sh in ((3, 24), (2, 16), (1, 8), (0, 0)):
        if len(r) > j: a = (a + (r[j] << sh)) & M
    return _mix(a, b, c)[2]


class Dict:
    def __init__(self, data):
        self.d = bytearray(data)
        w = struct.unpack_from('<8I', self.d, 0)
        if w[0] != 0x54434944 or w[1] != 0x2000:
            raise SystemExit('not a PC dictionary')
        self.seed, self.slots, self.mode = w[2], w[4], w[5]
        self.table = 12 + w[3] + 1
        if self.mode not in (0, 1) or w[7] != 0:
            raise SystemExit('dictionary with key mode %d / table2 count %d: not handled' % (self.mode, w[7]))

    def norm(self, k):
        # the game's rule (0xA15AC0): read while the character is printable ASCII (0x531270/0x5312A0),
        # in mode 1 lower-case A-Z (0x5312F0)
        m = re.match(r'[\x20-\x7e]*', k).group(0)
        if self.mode == 1:
            m = ''.join(chr(ord(c) + 32) if 'A' <= c <= 'Z' else c for c in m)
        return m.encode('ascii')

    def entry(self, s):
        e = self.table + 12 * s
        return e, struct.unpack_from('<3I', self.d, e)

    def string_at(self, e, rel):
        p = e + 4 + rel + 1
        return bytes(self.d[p:self.d.index(b'\x00', p)])

    def get(self, k):
        h = lookup2(self.norm(k), self.seed)
        s = h % self.slots
        for _ in range(self.slots):
            e, (eh, so, fo) = self.entry(s)
            if so == 0:
                return None
            if eh == h:
                return self.string_at(e, so)
            s = (s + 1) % self.slots
        return None

    def all(self):
        out = {}
        for s in range(self.slots):
            e, (eh, so, fo) = self.entry(s)
            if so:
                out[eh] = (self.string_at(e, so), fo)
        return out

    def add(self, k, text):
        h = lookup2(self.norm(k), self.seed)
        s = h % self.slots
        for _ in range(self.slots):
            e, (eh, so, fo) = self.entry(s)
            if so == 0:
                break
            if eh == h:
                raise SystemExit('%s: the dictionary has this key (or one with the same hash)' % k)
            s = (s + 1) % self.slots
        else:
            raise SystemExit('the dictionary is full')
        p = len(self.d)
        self.d += text.encode('utf-8') + b'\x00'
        struct.pack_into('<3I', self.d, e, h, p - (e + 4) - 1, 0)
        return s


def add_checked(data, pairs):
    """The dictionary with pairs [(key, text)] added, read back: every new key gives its text and every
    existing entry is unchanged."""
    dct = Dict(data)
    before = dct.all()
    for k, t in pairs:
        dct.add(k, t)
    out = bytes(dct.d)
    chk = Dict(out)
    for k, t in pairs:
        if chk.get(k) != t.encode('utf-8'):
            raise SystemExit('read back of %s failed' % k)
    after = chk.all()
    if any(after.get(h) != v for h, v in before.items()) or len(after) != len(before) + len(pairs):
        raise SystemExit('existing entries changed')
    return out


def main(argv):
    if len(argv) < 3:
        sys.exit('usage: dictadd.py in.dct out.dct KEY=TEXT ...')
    pairs = [tuple(a.split('=', 1)) for a in argv[2:]]
    out = add_checked(open(argv[0], 'rb').read(), pairs)
    for k, t in pairs:
        print('%s: %r' % (k, t))
    open(argv[1], 'wb').write(out)
    print('written %s, %d entries' % (argv[1], len(Dict(out).all())))


if __name__ == '__main__':
    main(sys.argv[1:])
