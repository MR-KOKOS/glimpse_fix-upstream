# Virtual archive: a base .ark (the game's resident.ark) plus overlay .arks (converted DLC files, merged
# text/XML files), presented as ONE archive file. Reference model for the C++ hook (vark.cpp); both use
# the same layout, so offsets agree.
#
# Virtual file layout:
#   [0, H)                header (magic, hsize) + merged index, built in memory; H = 8 + 16 + 20 * n
#   [H, H + baseData)     the base file's data region [8 + base.hsize, base.size), shifted by delta = H - (8 + base.hsize)
#   [o_i, o_i + len_i)    overlay i's data region [8 + ov.hsize, ov.size), each starting 16-aligned after the previous
# Merged index: every key of base and overlays, overlays override base and later overlays override earlier
# ones (that is how merged directory.txt / VehicleDatabase.xml replace the originals); sorted by unsigned key.
# Overridden base data stays in the file, unreferenced.
import struct

import arkfmt

ALIGN = 16
LIMIT = 0x7FFFFFFF  # the game's stream uses signed 32-bit positions (seek 0x5728E0) and GetFileSize without the high part


class Segment:
    __slots__ = ('vstart', 'vend', 'src', 'srcoff')

    def __init__(self, vstart, vend, src, srcoff):
        self.vstart, self.vend, self.src, self.srcoff = vstart, vend, src, srcoff

    def __repr__(self):
        return 'Segment(%#x-%#x src %s @%#x)' % (self.vstart, self.vend, self.src, self.srcoff)


class VirtualArk:
    def __init__(self, base_fn, overlay_fns):
        self.arks = [arkfmt.Ark(base_fn)] + [arkfmt.Ark(f) for f in overlay_fns]
        merged = {}  # key -> (source index, entry)
        for si, a in enumerate(self.arks):
            for e in a.entries:
                merged[e[4]] = (si, e)
        n = len(merged)
        H = 8 + 16 + 20 * n
        base = self.arks[0]
        self.segments = [Segment(0, H, None, 0)]
        shift = {}  # source index -> value added to its entry offsets
        base_data = 8 + base.hsize
        delta = H - base_data
        shift[0] = delta
        self.segments.append(Segment(H, base.size + delta, 0, base_data))
        cur = base.size + delta
        for si in range(1, len(self.arks)):
            a = self.arks[si]
            cur = (cur + ALIGN - 1) & ~(ALIGN - 1)
            d0 = 8 + a.hsize
            shift[si] = cur - d0
            self.segments.append(Segment(cur, cur + (a.size - d0), si, d0))
            cur += a.size - d0
        if cur > LIMIT:
            raise ValueError('virtual archive too large: %#x' % cur)
        self.size = cur
        self.delta = delta
        idx = bytearray(struct.pack('<4I', n, 16, 0, 16 + 20 * n))
        self.sources = {}
        for k in sorted(merged):
            si, (t, off, cs, us, key) = merged[k]
            idx += arkfmt.ENTRY.pack(t, off + shift[si], cs, us, key)
            self.sources[key] = si
        self.head = struct.pack('<2I', arkfmt.MAGIC, 16 + 20 * n) + bytes(idx)
        assert len(self.head) == H
        self._files = [open(a.fn, 'rb') for a in self.arks]

    def close(self):
        for f in self._files:
            f.close()

    def read(self, pos, n):
        """Bytes [pos, pos + n) of the virtual file (shorter at the end), as ReadFile would return them."""
        out = bytearray()
        end = min(pos + n, self.size)
        for s in self.segments:
            if s.vend <= pos or s.vstart >= end:
                continue
            a, b = max(pos, s.vstart), min(end, s.vend)
            # gaps (alignment padding) between segments read as zeros
            if a > pos + len(out):
                out += bytes(a - pos - len(out))
            if s.src is None:
                out += self.head[a:b]
            else:
                f = self._files[s.src]
                f.seek(s.srcoff + (a - s.vstart))
                out += f.read(b - a)
        if end > pos and len(out) < end - pos:
            out += bytes(end - pos - len(out))
        return bytes(out)


class GameStream:
    """The game's buffered read stream over a file (0x5728E0 seek / 0x572850 read): fills a buffer at
    buffer-aligned positions. Used here to read the virtual file the way the game does, so reads cross
    segment borders like the game's would."""

    def __init__(self, readfn, size, bufsize=0x10000):
        self.readfn, self.size, self.bufsize = readfn, size, bufsize
        self.pos = 0
        self.bufpos, self.buf = -1, b''

    def seek(self, pos):
        self.pos = pos

    def read(self, n):
        out = bytearray()
        while n > 0:
            if not (self.bufpos <= self.pos < self.bufpos + len(self.buf)):
                self.bufpos = self.pos - self.pos % self.bufsize
                self.buf = self.readfn(self.bufpos, self.bufsize)
                if self.pos >= self.bufpos + len(self.buf):
                    raise EOFError('read past the end at %#x' % self.pos)
            o = self.pos - self.bufpos
            take = min(n, len(self.buf) - o)
            out += self.buf[o:o + take]
            self.pos += take
            n -= take
        return bytes(out)

    def u32(self):
        return struct.unpack('<I', self.read(4))[0]


class GameArkReader:
    """ArkLoader as the game runs it (0x571940 open, 0x571660 index, 0x5712F0 checks, 0x5710C0 lookup,
    0x5713A0 member open), over any read function."""

    def __init__(self, readfn, size):
        self.s = GameStream(readfn, size)
        magic = self.s.u32()
        if magic >> 16 != 1 or magic & 0xFFFF:
            raise ValueError('archive version/compression %08X' % magic)
        hsize = self.s.u32()
        block = self.s.read(hsize)
        cA, offA, cB, offB = struct.unpack_from('<4I', block)
        if cB * 20 + 16 + cA * 20 != hsize:  # 0x5712F0
            raise ValueError('index size check failed')
        self.entries = [arkfmt.ENTRY.unpack_from(block, offA + 20 * k) for k in range(cA)]
        self.keys = [e[4] for e in self.entries]

    def lookup(self, path_or_key):
        k = path_or_key if isinstance(path_or_key, int) else arkfmt.key(path_or_key)
        lo, hi = 0, len(self.entries) - 1
        while lo <= hi:  # unsigned compare, as 0x5710C0
            mid = (lo + hi) // 2
            v = self.keys[mid]
            if v == k:
                return self.entries[mid]
            if k < v:
                hi = mid - 1
            else:
                lo = mid + 1
        return None

    def read_member(self, path_or_key):
        e = self.lookup(path_or_key)
        if e is None:
            return None
        t, off, cs, us, k = e
        self.s.seek(off)
        if t == 0:
            return self.s.read(us)
        if t == 1:
            blob = self.s.read(cs)
            return arkfmt.lzss_decode(blob, us, 4)
        raise ValueError('entry type %d' % t)
