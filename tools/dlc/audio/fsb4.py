# FMOD Ex FSB4 bank reader/writer for Split/Second (FSB version 0x00040000, header flags 0x40).
# The container is little-endian on Xbox 360 and PC alike; the platforms differ in the codec and layout:
#   Xbox: XMA (mode bit 0x01000000), per-sample extra data after the 0x50-byte header (XMA seek table, and a
#         "SYNC" chunk when the mode has SYNCPOINTS), sample data in whole 2048-byte XMA blocks.
#   PC  : MPEG-1/2 Layer II (mode 0x200|0x40000), plain 0x50-byte headers (no extra, no sync data even when the
#         mode says SYNCPOINTS), frames back to back, each sample's data padded to 32 bytes and the header area
#         padded so the data starts 32-aligned.
import struct

# sample mode bits (FSOUND_*)
M = dict(LOOP_OFF=0x1, LOOP_NORMAL=0x2, LOOP_BIDI=0x4, BITS8=0x8, BITS16=0x10, MONO=0x20, STEREO=0x40,
         UNSIGNED=0x80, SIGNED=0x100, MPEG=0x200, ALLMONO=0x400, ALLSTEREO=0x800, HW3D=0x1000, D2=0x2000,
         SYNCPOINTS_NONAME=0x4000, DUPLICATE=0x8000, MPEG_L2=0x40000, HW2D=0x80000, D3=0x100000,
         IMAADPCM=0x400000, VAG=0x800000, XMA=0x1000000, GCADPCM=0x2000000, MULTICHANNEL=0x4000000,
         OGG=0x8000000, MPEG_L3=0x10000000, IMAADPCMSTEREO=0x20000000, IGNORETAGS=0x40000000,
         SYNCPOINTS=0x80000000)
CODEC_BITS = (M['MPEG'] | M['MPEG_L2'] | M['MPEG_L3'] | M['IMAADPCM'] | M['VAG'] | M['XMA'] | M['GCADPCM'] |
              M['OGG'] | M['BITS8'] | M['BITS16'] | M['SIGNED'] | M['UNSIGNED'])
# 0x48 holds the header size again as u32 in these banks (FMOD's 'varfreq' slot; equal to 'size' in every
# Xbox and PC bank checked)
SHDR = '<H30s5I iHhHH ff IHh'   # 0x50 bytes
SHDR_LEN = struct.calcsize(SHDR)
assert SHDR_LEN == 0x50
FIELDS = ('size', 'name', 'samples', 'csize', 'loopstart', 'loopend', 'mode', 'freq', 'vol', 'pan', 'pri',
          'channels', 'mindist', 'maxdist', 'size2', 'varvol', 'varpan')


def modestr(m):
    return '|'.join(k for k, v in M.items() if m & v) or '0'


def codec(m):
    for k in ('XMA', 'IMAADPCM', 'MPEG', 'GCADPCM', 'VAG', 'OGG'):
        if m & M[k]:
            return k
    return 'PCM8' if m & M['BITS8'] else 'PCM16'


def align(n, a):
    return -(-n // a) * a if a > 1 else n


class Sample:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    @property
    def sname(self):
        return self.name.rstrip(b'\0').decode('latin-1')

    def hdr(self):
        """Full sample header (0x50 bytes + extra), size fields recomputed."""
        n = SHDR_LEN + len(self.extra)
        b = struct.pack(SHDR, n, self.name.ljust(30, b'\0')[:30], self.samples, self.csize, self.loopstart,
                        self.loopend, self.mode, self.freq, self.vol, self.pan, self.pri, self.channels,
                        self.mindist, self.maxdist, n, self.varvol, self.varpan)
        return b + self.extra


class FSB4:
    def __init__(self, blob=None):
        self.samples = []
        self.version = 0x40000
        self.flags = 0x40
        self.zero = b'\0' * 8
        self.hash = b'\0' * 16
        self.align = 1
        if blob is not None:
            self.parse(blob)

    def parse(self, d):
        if d[:4] != b'FSB4':
            raise ValueError('not an FSB4 bank (%r)' % d[:4])
        n, shsize, dsize, self.version, self.flags = struct.unpack_from('<5I', d, 4)
        self.zero, self.hash = d[0x18:0x20], d[0x20:0x30]
        self.raw_shsize, self.raw_dsize = shsize, dsize
        p, first = 0x30, None
        for k in range(n):
            if self.flags & 2 and k:        # basic headers: only lengths, rest as the first sample
                ls, cs = struct.unpack_from('<2I', d, p)
                p += 8
                s = Sample(**{f: getattr(first, f) for f in FIELDS})
                s.samples, s.csize, s.extra = ls, cs, b''
            else:
                s = Sample(**dict(zip(FIELDS, struct.unpack_from(SHDR, d, p))))
                s.extra = d[p + SHDR_LEN:p + s.size]
                p += s.size
                first = first or s
            self.samples.append(s)
        # per-sample data alignment: the one that makes the sizes add up (PC 32, Xbox XMA 2048 = any)
        cs = [s.csize for s in self.samples]
        for a in (1, 16, 32, 64, 128, 2048):
            if sum(align(c, a) for c in cs) == dsize:
                self.align = a
                break
        else:
            raise ValueError('cannot place sample data (sizes do not add up to 0x%X)' % dsize)
        dpos = 0x30 + shsize
        for s in self.samples:
            s.offset = dpos
            s.data = d[dpos:dpos + s.csize]
            dpos += align(s.csize, self.align)
        self.tail = d[dpos:]
        return self

    def build(self, align_to=32):
        """Serialise with full headers, header area and every sample's data padded to align_to (PC: 32)."""
        hd = b''.join(s.hdr() for s in self.samples)
        hd += b'\0' * (align(0x30 + len(hd), align_to) - 0x30 - len(hd))
        body = bytearray()
        for s in self.samples:
            assert len(s.data) == s.csize
            body += s.data
            body += b'\0' * (align(len(body), align_to) - len(body))
        head = b'FSB4' + struct.pack('<5I', len(self.samples), len(hd), len(body), self.version,
                                     self.flags & ~2) + self.zero + self.hash
        return head + hd + bytes(body)

    def describe(self, limit=None):
        out = ['FSB4 n=%d shdr=0x%X data=0x%X ver=%08X flags=%X align=%d hash=%s' % (
            len(self.samples), self.raw_shsize, self.raw_dsize, self.version, self.flags, self.align,
            self.hash.hex())]
        for k, s in enumerate(self.samples[:limit]):
            out.append('%3d %-30s %-6s ch=%d f=%d smp=%d cb=%d loop=%d-%d mode=%s extra=%d' % (
                k, s.sname, codec(s.mode), s.channels, s.freq, s.samples, s.csize, s.loopstart, s.loopend,
                modestr(s.mode), len(s.extra)))
        return '\n'.join(out)


if __name__ == '__main__':
    import sys
    for fn in sys.argv[1:]:
        print('==', fn)
        print(FSB4(open(fn, 'rb').read()).describe())
