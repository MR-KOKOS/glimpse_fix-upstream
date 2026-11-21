# Split/Second .ark archives (PC, little-endian): reading, writing and the key hash.
#
# Layout as the game's ArkLoader reads it (0x571940 -> 0x5718E0 -> 0x571660 -> 0x5712F0):
#   u32 magic      0x00010000 = version 1 << 16 | stream compression 0 (0x571200: 1 lzss, 2 lzhuf, 3 zlib)
#   u32 hsize      size of the index block that follows; must be 16 + 20 * (cA + cB) (checked at 0x5712F0)
#   index block:   u32 cA, u32 offA, u32 cB, u32 offB  (offsets relative to the block start, i.e. file offset - 8)
#                  cA entries {u32 type, u32 offset, u32 packed size, u32 size, u32 key}, sorted by key (unsigned),
#                  looked up by binary search (0x5710C0); cB name-keyed entries (always 0 on PC)
#   data:          entry offsets are absolute file offsets; type 0 = stored, 1 = LZSS (u32 size + stream)
# Key (0x571080): h = h * 31 + tolower(c) over the path below the device ("Vehicles/VehicleDatabase.xml"),
# with '/' as separator (the archive device's separator, set at 0x7A9580 / 0x401F50: 0x2F).
import struct

MAGIC = 0x00010000
ENTRY = struct.Struct('<5I')


def key(path):
    h = 0
    for c in path.replace('\\', '/').lower().encode('latin-1'):
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


def lzss_decode(src, size, start=0):
    """The archives' LZSS (as texdump.cpp): flag byte, bit set = literal, low bit first; a reference is
    two bytes, 12-bit window position (window starts at 0xFEE, zero filled) and 4-bit length + 3."""
    win = bytearray(4096)
    pos, flags, i, out = 0xFEE, 0, start, bytearray()
    n = len(src)
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            if i >= n:
                raise ValueError('lzss: truncated')
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            win[pos] = c
            pos = (pos + 1) & 0xFFF
        else:
            a, b = src[i], src[i + 1]
            i += 2
            o, ln = a | ((b & 0xF0) << 4), (b & 0x0F) + 3
            for k in range(ln):
                if len(out) >= size:
                    break
                c = win[(o + k) & 0xFFF]
                out.append(c)
                win[pos] = c
                pos = (pos + 1) & 0xFFF
    return bytes(out)


class Ark:
    """Index of one archive file, entries as (type, offset, csize, size, key)."""

    def __init__(self, fn):
        self.fn = fn
        with open(fn, 'rb') as f:
            head = f.read(24)
            magic, hsize, cA, offA, cB, offB = struct.unpack_from('<6I', head)
            if magic != MAGIC:
                raise ValueError('%s: not a PC archive (magic %08X)' % (fn, magic))
            if hsize != 16 + 20 * (cA + cB) or cB:
                raise ValueError('%s: unexpected index (hsize %X, cA %d, cB %d)' % (fn, hsize, cA, cB))
            f.seek(8 + offA)
            raw = f.read(20 * cA)
            f.seek(0, 2)
            self.size = f.tell()
        self.hsize = hsize
        self.entries = [ENTRY.unpack_from(raw, 20 * k) for k in range(cA)]
        self.by_key = {e[4]: e for e in self.entries}

    def read_entry(self, e, f=None):
        t, off, cs, us, k = e
        own = f is None
        if own:
            f = open(self.fn, 'rb')
        try:
            f.seek(off)
            if t == 0:
                return f.read(us)
            if t == 1:
                blob = f.read(cs)
                return lzss_decode(blob, us, 4)
            raise ValueError('entry type %d' % t)
        finally:
            if own:
                f.close()

    def get(self, path):
        e = self.by_key.get(key(path))
        return None if e is None else self.read_entry(e)


def write_ark(fn, files):
    """Writes a PC archive with stored (type 0) entries. files: {path or int key: bytes}."""
    items = sorted(((k if isinstance(k, int) else key(k)), v) for k, v in files.items())
    keys = [k for k, _ in items]
    if len(set(keys)) != len(keys):
        raise ValueError('duplicate keys')
    n = len(items)
    hsize = 16 + 20 * n
    data_start = 8 + hsize
    index = bytearray(struct.pack('<4I', n, 16, 0, hsize))
    blobs, off = [], data_start
    for k, v in items:
        index += ENTRY.pack(0, off, len(v), len(v), k)
        blobs.append(v)
        off += len(v)
    with open(fn, 'wb') as f:
        f.write(struct.pack('<2I', MAGIC, hsize))
        f.write(index)
        for b in blobs:
            f.write(b)
