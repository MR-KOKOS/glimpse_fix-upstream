# Split/Second Tonic engine-sound files (.tonic = 44.1 kHz set, .tonic2 = 21 kHz set): Xbox 360 -> PC.
#
# Layout (all fields u32 unless noted; Xbox big-endian, PC little-endian; sample data is a byte stream
# (4-bit ADPCM-like, platform neutral) and is identical on both platforms):
#   header  : version (8), platform (PC 0 / Xbox 1), n_sections, n_sounds, n_loops, rate, 0
#   section : count, channels, index, then count x grain
#   grain   : n_sub, then n_sub x { samples, bytes, f32 a, f32 b, f32 c, u8 data[bytes] }
#   sound   : n_sounds + n_loops x { samples, bytes, channels, rate, flags, u8 data[bytes] }
# Conversion = byteswap of every header/table word + platform 1 -> 0.
import struct
import sys


class TonicError(Exception):
    pass


def convert(src, platform_out=0, endian_out='<'):
    if len(src) < 28:
        raise TonicError('too short')
    if src[:4] == b'\0\0\0\x08':
        E = '>'
    elif src[:4] == b'\x08\0\0\0':
        E = '<'
    else:
        raise TonicError('unknown version/magic %s' % src[:4].hex())
    n = len(src)
    out = bytearray(src)

    def word(o, fmt='I'):
        if o + 4 > n:
            raise TonicError('truncated at 0x%X' % o)
        v = struct.unpack_from(E + fmt, src, o)[0]
        struct.pack_into(endian_out + fmt, out, o, v)
        return v

    ver = word(0)
    word(4)
    struct.pack_into(endian_out + 'I', out, 4, platform_out)
    nsec, nsnd, nloop, rate, z = (word(8 + 4 * i) for i in range(5))
    p = 0x1C
    info = dict(version=ver, sections=[], sounds=[], rate=rate)
    for s in range(nsec):
        cnt, ch, idx = word(p), word(p + 4), word(p + 8)
        p += 12
        subs = 0
        for g in range(cnt):
            k = word(p)
            p += 4
            if not 1 <= k <= 16:
                raise TonicError('bad grain sub count %d at 0x%X' % (k, p - 4))
            for _ in range(k):
                word(p)
                by = word(p + 4)
                for j in range(3):
                    word(p + 8 + 4 * j, 'f')
                p += 20 + by
                subs += 1
        info['sections'].append((cnt, ch, idx, subs))
    for s in range(nsnd + nloop):
        smp, by, ch, rt, fl = (word(p + 4 * i) for i in range(5))
        p += 20 + by
        info['sounds'].append((smp, by, ch, rt, fl))
    if p != n:
        raise TonicError('parse ended at 0x%X, file size 0x%X' % (p, n))
    return bytes(out), info


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: tonic.py <xbox .tonic/.tonic2> <out pc file>')
        sys.exit(1)
    data, info = convert(open(sys.argv[1], 'rb').read())
    open(sys.argv[2], 'wb').write(data)
    print('ok', sys.argv[2], len(data), 'sections', info['sections'], 'sounds', len(info['sounds']))
