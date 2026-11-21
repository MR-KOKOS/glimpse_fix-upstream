# Offline test of the virtual archive (no game process): builds two overlay archives (merged text files from
# the PC resident.ark and the Xbox DLC, synthetic files, overrides), merges them with the real resident.ark
# and reads entries back the way the game's ArkLoader does. Also writes expect.txt for the C++ harness
# (test_vark.exe), which reads the same entries through the file I/O hooks.
#
#   python -I test_vark.py [--base <resident.ark>] [--dlc <unpacked DLC folder>]... [--out <dir>]
#
# Nothing in the game folder is written; output goes to --out (default: test_out next to this script).
import argparse
import os
import random
import struct
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # -I leaves the script's folder off sys.path
import arkfmt  # noqa: E402
import dlcmerge  # noqa: E402
import vark  # noqa: E402

# the PC resident.ark: --base, else below the game folder in GAME_DIR
DEFAULT_BASE = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None
DECODE_LIMIT = 64 * 1024  # pure-Python LZSS is slow: decode only entries up to this size here


def find_ci(folder, rel):
    """folder/rel with any letter case (the STFS dump is upper case)."""
    cur = folder
    for part in rel.split('/'):
        if not os.path.isdir(cur):
            return None
        hit = [n for n in os.listdir(cur) if n.lower() == part.lower()]
        if not hit:
            return None
        cur = os.path.join(cur, hit[0])
    return cur if os.path.isfile(cur) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE, required=not DEFAULT_BASE,
                    help='the PC data\\resident.ark (default: below GAME_DIR, the game folder)')
    ap.add_argument('--dlc', action='append', default=[])
    ap.add_argument('--out', default=os.path.join(HERE, 'test_out'))
    ap.add_argument('--samples', type=int, default=300)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rnd = random.Random(1234)
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)
            print('FAIL', what)

    t0 = time.time()
    base = arkfmt.Ark(a.base)
    print('base %s: %d entries, %d MB' % (a.base, len(base.entries), base.size >> 20))

    # merged text files: the PC originals + the DLC folders' additions
    pc_dir = base.get('directory.txt').decode('latin-1')
    pc_veh = base.get('Vehicles/VehicleDatabase.xml').decode('latin-1')
    dlc_dirs, dlc_vehs = [], []
    for d in a.dlc:
        f = find_ci(d, 'DIRECTORY.TXT')
        if f:
            dlc_dirs.append(open(f, 'rb').read().decode('latin-1'))
        f = find_ci(d, 'VEHICLES/VEHICLEDATABASE.XML')
        if f:
            dlc_vehs.append(open(f, 'rb').read().decode('latin-1'))
    m_dir = dlcmerge.merge_directory(pc_dir, dlc_dirs).encode('latin-1')
    m_veh = dlcmerge.merge_vehicles(pc_veh, dlc_vehs).encode('latin-1')
    print('merged directory.txt from %d DLC lists, VehicleDatabase.xml from %d' % (len(dlc_dirs), len(dlc_vehs)))

    season = base.get('UI/Logic/Season.xml')
    ov1_files = {
        'directory.txt': m_dir,
        'Vehicles/VehicleDatabase.xml': m_veh,
        'glimpse/test/small.bin': bytes(rnd.randrange(256) for _ in range(100)),
        'glimpse/test/big.bin': bytes(rnd.randrange(256) for _ in range(300000)),
        'glimpse/test/empty.bin': b'',
    }
    ov2_files = {
        'glimpse/test/small.bin': b'overridden by the second overlay\r\n',
        'UI/Logic/Season.xml': season + b'\r\n<!-- glimpse overlay test -->\r\n',
    }
    ov1 = os.path.join(a.out, 'overlay1.ark')
    ov2 = os.path.join(a.out, 'overlay2.ark')
    arkfmt.write_ark(ov1, ov1_files)
    arkfmt.write_ark(ov2, ov2_files)
    expect_files = dict(ov1_files)
    expect_files.update(ov2_files)

    va = vark.VirtualArk(a.base, [ov1, ov2])
    print('virtual: %d bytes (base %d), delta %d, %d segments' % (va.size, base.size, va.delta, len(va.segments)))
    for s in va.segments:
        print('   ', s)

    # 1. index invariants
    magic, hsize = struct.unpack_from('<2I', va.head)
    cA, offA, cB, offB = struct.unpack_from('<4I', va.head, 8)
    keys = [struct.unpack_from('<I', va.head, 8 + offA + 20 * k + 16)[0] for k in range(cA)]
    union = set(base.by_key) | {arkfmt.key(p) for p in expect_files}
    check(magic == arkfmt.MAGIC and hsize == 16 + 20 * cA and cB == 0, 'header')
    check(keys == sorted(keys) and len(set(keys)) == len(keys), 'keys sorted and unique')
    check(set(keys) == union, 'index holds exactly base + overlay keys')

    # 2. read through the game's way of reading
    rd = vark.GameArkReader(va.read, va.size)
    for p, want in sorted(expect_files.items()):
        got = rd.read_member(p)
        check(got == want, 'overlay file %s' % p)
    check(rd.read_member('Vehicles/VehicleDatabase.xml') != pc_veh.encode('latin-1') or not dlc_vehs,
          'VehicleDatabase.xml replaced')
    check(rd.read_member('glimpse/test/not_there.bin') is None, 'missing key')

    # 3. base entries not overridden: raw bytes identical (and decoded for small ones)
    over = {arkfmt.key(p) for p in expect_files}
    pool = [e for e in base.entries if e[4] not in over]
    samples = rnd.sample(pool, min(a.samples, len(pool)))
    # the first and last base entries sit next to the segment borders
    samples += [min(pool, key=lambda e: e[1]), max(pool, key=lambda e: e[1])]
    vent = {e[4]: e for e in rd.entries}
    bf = open(a.base, 'rb')
    expect_lines = []
    decoded = 0
    for e in samples:
        t, off, cs, us, k = e
        v = vent[k]
        check(v[0] == t and v[2] == cs and v[3] == us and v[1] == off + va.delta, 'entry %08X fields' % k)
        bf.seek(off)
        raw = bf.read(cs)
        rd.s.seek(v[1])
        vraw = rd.s.read(cs)
        check(raw == vraw, 'entry %08X raw bytes' % k)
        dcrc = '-'
        if us <= DECODE_LIMIT:
            dec = rd.read_member(k)
            check(dec == base.read_entry(e, bf), 'entry %08X decoded' % k)
            dcrc = '%08X' % zlib.crc32(dec)
            decoded += 1
        expect_lines.append('#%08X %d %d %d %08X %s' % (k, t, cs, us, zlib.crc32(raw), dcrc))
    bf.close()

    # 4. reads across the segment borders equal the pieces read separately
    for s in va.segments[1:]:
        for at in (s.vstart, s.vend):
            for back, fwd in ((1, 1), (7, 300), (4096, 4096)):
                lo = max(0, at - back)
                whole = va.read(lo, back + fwd)
                parts = va.read(lo, at - lo) + va.read(at, back + fwd - (at - lo))
                check(whole == parts, 'border read at %#x' % at)
    check(va.read(va.size - 10, 100) == va.read(va.size - 10, 10) and len(va.read(va.size, 10)) == 0, 'end of file')

    for p, data in sorted(expect_files.items()):
        e = vent[arkfmt.key(p)]
        expect_lines.append('%s %d %d %d %08X %08X' % (p, e[0], e[2], e[3], zlib.crc32(data), zlib.crc32(data)))
    with open(os.path.join(a.out, 'expect.txt'), 'w', newline='\n') as f:
        f.write('# virtual archive expectations written by test_vark.py\n')
        f.write('base %s\n' % a.base)
        f.write('overlay %s\noverlay %s\n' % (ov1, ov2))
        f.write('vsize %d delta %d headcrc %08X entries %d\n' % (va.size, va.delta, zlib.crc32(va.head), cA))
        f.write('\n'.join(expect_lines) + '\n')
    va.close()
    print('%d base samples (%d decoded), %d overlay files, %.1f s' % (len(samples), decoded, len(expect_files),
                                                                       time.time() - t0))
    print('wrote', os.path.join(a.out, 'expect.txt'))
    print('FAILED: %d' % len(fails) if fails else 'PASS')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
