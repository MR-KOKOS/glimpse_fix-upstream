# Split/Second .ark reader and writer (PC little-endian / Xbox 360 big-endian).
#
# Layout (same on both platforms, only the byte order differs):
#   0x00 u32 version 0x00010000
#   0x04 u32 hsize   = size of the block after these 8 bytes = 0x10 + 20 * count
#   0x08 u32 count   (table A: file entries)
#   0x0C u32 0x10    (offset of table A, relative to 0x08)
#   0x10 u32 0       (table B count, always 0)
#   0x14 u32 hsize   (table B offset = end of table A)
#   0x18 count x {u32 type, u32 file offset, u32 stored size, u32 size, u32 key}, sorted by key (ascending)
#   data from 8 + hsize on, entries back to back (no alignment)
# type 0 = stored (stored size == size), type 1 = LZSS: u32 size, then LZSS stream (4 KiB window, start 0xFEE).
# key = h*31 + c over the lower-case path (forward slashes, no leading slash).
# Main level / resident arks are zero-padded to a multiple of 0x800; sector arks (.static/.stream) are not.
import struct

VERSION = 0x00010000


def key(path):
    h = 0
    for c in path.replace('\\', '/').lower().encode('latin-1'):
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


def lzss_n(src, i, size, fill=0):
    win = bytearray([fill]) * 4096
    r = 0xFEE
    out = bytearray()
    flags = 0
    n_src = len(src)
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            if i >= n_src:
                break
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & 0xFFF
        else:
            a, b = src[i], src[i + 1]
            i += 2
            o = a | ((b & 0xF0) << 4)
            for k in range((b & 0x0F) + 3):
                c = win[(o + k) & 0xFFF]
                out.append(c)
                win[r] = c
                r = (r + 1) & 0xFFF
    return bytes(out[:size])


class Ark:
    def __init__(self, fn):
        self.fn = fn
        self.f = open(fn, 'rb')
        head = self.f.read(24)
        self.be = head[:4] == b'\x00\x01\x00\x00'
        self.E = '>' if self.be else '<'
        ver, hsize, cA, offA, cB, offB = struct.unpack_from(self.E + '6I', head, 0)
        if ver != VERSION:
            raise ValueError('%s: not an ark (version %08x)' % (fn, ver))
        self.f.seek(8 + offA)
        idx = self.f.read(20 * cA)
        self.ents = {}
        self.order = []
        for k in range(cA):
            t, off, cs, us, kk = struct.unpack_from(self.E + '5I', idx, 20 * k)
            self.ents[kk] = (t, off, cs, us)
            self.order.append(kk)

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def size(self, k):
        return self.ents[k][3]

    def get(self, k):
        if isinstance(k, str):
            k = key(k)
        e = self.ents.get(k)
        if e is None:
            return None
        t, off, cs, us = e
        self.f.seek(off)
        raw = self.f.read(cs)
        if t == 0:
            return raw[:us]
        if t == 1:
            # the u32 prefix is the unpacked size; the index size field usually equals it, but some
            # PC stream-ark textures have size == stored size there
            return lzss_n(raw + bytes(16), 4, struct.unpack_from(self.E + 'I', raw, 0)[0])
        raise ValueError('entry type %d' % t)


def write_ark(fn, files, pad=0x800):
    """files: iterable of (key or path, bytes); written stored (type 0), data in the given order,
    index sorted by key. pad: pad the file to this multiple (0x800 like PC main arks, 0 for sector arks)."""
    items = []
    seen = set()
    for k, data in files:
        if isinstance(k, str):
            k = key(k)
        if k in seen:
            raise ValueError('duplicate key %08x' % k)
        seen.add(k)
        items.append((k, bytes(data)))
    n = len(items)
    hsize = 0x10 + 20 * n
    pos = 8 + hsize
    offs = {}
    for k, data in items:
        offs[k] = pos
        pos += len(data)
    out = bytearray(struct.pack('<6I', VERSION, hsize, n, 0x10, 0, hsize))
    sizes = {k: len(d) for k, d in items}
    for k in sorted(offs):
        out += struct.pack('<5I', 0, offs[k], sizes[k], sizes[k], k)
    for k, data in items:
        out += data
    if pad and len(out) % pad:
        out += b'\0' * (pad - len(out) % pad)
    with open(fn, 'wb') as f:
        f.write(out)
    return n


def key_of_file(rel):
    """pack naming: '<8 hex digits>[.anything]' is that key, anything else is a path relative to the ark root"""
    rel = rel.replace('\\', '/')
    stem = rel.split('.')[0]
    if len(stem) == 8 and all(c in '0123456789abcdefABCDEF' for c in stem) and '/' not in rel:
        return int(stem, 16)
    return key(rel)


def main(argv):
    import os
    if len(argv) >= 2 and argv[0] == 'list':
        a = Ark(argv[1])
        print('%s, %d entries' % ('big-endian (Xbox)' if a.be else 'little-endian (PC)', len(a.ents)))
        for k in a.order:
            t, off, cs, us = a.ents[k]
            print('%08x type %d off %#x stored %d size %d' % (k, t, off, cs, us))
    elif len(argv) == 3 and argv[0] == 'extract':
        a = Ark(argv[1])
        os.makedirs(argv[2], exist_ok=True)
        for k in a.order:
            with open(os.path.join(argv[2], '%08x.bin' % k), 'wb') as f:
                f.write(a.get(k))
        print('%d files' % len(a.order))
    elif len(argv) >= 3 and argv[0] == 'pack':
        src, dst = argv[1], argv[2]
        files = []
        for root, _, names in os.walk(src):
            for nm in sorted(names):
                rel = os.path.relpath(os.path.join(root, nm), src)
                with open(os.path.join(root, nm), 'rb') as f:
                    files.append((key_of_file(rel), f.read()))
        n = write_ark(dst, files, pad=0 if '--nopad' in argv else 0x800)
        print('%s: %d entries' % (dst, n))
    else:
        print('usage: python -I arkio.py list <ark>\n'
              '       python -I arkio.py extract <ark> <dir>        (files named <key>.bin)\n'
              '       python -I arkio.py pack <dir> <out.ark> [--nopad]\n'
              '         pack: a file named <8 hex digits>[.ext] at the top level is stored under that key,\n'
              '         any other file under the key of its path relative to <dir> (lower-cased, "/")\n'
              '         --nopad: no 0x800 padding (like the PC .static/.stream sector arks)')
        return 1
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv[1:]))
