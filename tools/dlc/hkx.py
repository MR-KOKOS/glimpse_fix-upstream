# Havok packfile headers: version, layout rules and the sections with their sizes
import struct
import sys


def info(fn):
    d = open(fn, 'rb').read()
    be = d[8:12] != b'\x00\x00\x00\x00' and d[17] == 0  # layout rules byte 1 = little endian flag
    E = '<' if d[0x11] == 1 else '>'
    user_tag, version = struct.unpack_from(E + 'iI', d, 8)
    rules = d[16:20]
    nsec, contents_sec, contents_off, cls_sec, cls_off = struct.unpack_from(E + '5i', d, 20)
    ver = d[0x28:0x38].split(b'\0')[0]
    print('%s: %d bytes, %s, version %d %s, rules %s, %d sections' % (fn, len(d), 'LE' if E == '<' else 'BE', version, ver,
                                                                       rules.hex(), nsec))
    base = 0x40
    for i in range(nsec):
        o = base + i * 0x30
        name = d[o:o + 20].split(b'\0')[0].decode('latin-1')
        start, loc, glob, virt, exp, imp, end = struct.unpack_from(E + '7I', d, o + 20)
        print('   %-14s start %6d data %6d local %5d global %5d virtual %5d end %6d' % (name, start, loc, glob - loc, virt - glob,
                                                                                       exp - virt, end))
    # class names
    return d


for fn in sys.argv[1:]:
    info(fn)
