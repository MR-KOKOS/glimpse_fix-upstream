# Convert Xbox 360 base-game Havok files and compare them byte for byte with the PC files.
# Usage:
#   python -I test_convhkx.py ark  [-v] [--learn] [--extract <dir>]
#       base cars (vehicles/bodies/<car>/<car>.phys/.maincoll) from the Xbox and PC resident.ark (pair_lib.py)
#       plus every Havok entry of the NEM_STORM level archives
#   python -I test_convhkx.py dirs <xbox dir> <pc dir> [-v] [--learn]    pairs of equally named files
# --learn first rebuilds hkref.json from all PC archives and the PC exe (PC_LEARN); --types also rebuilds the
# __types__ section of every PC packfile in the PC archives from its own data and compares; -v lists all pairs.
# Verdicts: identical | volatile (differs only in members that hold uninitialised memory, convhkx.VOLATILE) |
#           DIFF (anything else; listed per member)
import os
import struct
import sys
from collections import Counter, defaultdict

sys.dont_write_bytecode = True  # no __pycache__ next to pair_lib.py / convhkx.py
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convhkx

# development input: the Xbox NEM_STORM.ARK below GLIMPSE_DLC_SCRATCH\dlc\xb_disc (only for "ark")
SCRATCH = os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc') if os.environ.get('GLIMPSE_DLC_SCRATCH') else ''
XB_LEVEL = os.path.join(SCRATCH, 'xb_disc', 'NEM_STORM.ARK') if SCRATCH else ''
# the PC game folder: the environment variable GAME_DIR ("ark" and --learn need it)
GAME = os.environ.get('GAME_DIR', '')
PC_LEVEL = os.path.join(GAME, 'data', 'environments', 'levels', 'nem_storm.ark') if GAME else ''
PC_LEARN = [os.path.join(GAME, 'data', '*.ark'), os.path.join(GAME, 'data', 'environments', 'levels', '*.ark'),
            os.path.join(GAME, 'SplitSecond.exe')] if GAME else []
CARS = (['musclecar_%02d' % i for i in range(1, 17)] + ['supercar_%02d' % i for i in range(1, 17)] +
        ['truck_%02d' % i for i in range(1, 9)])


def ark_pairs():
    sys.path.insert(0, os.path.dirname(HERE))
    import pair_lib
    xb, pc = pair_lib.Ark(pair_lib.XB), pair_lib.Ark(pair_lib.PC)
    for c in CARS:
        for ext in ('.phys', '.maincoll'):
            p = 'vehicles/bodies/%s/%s%s' % (c, c, ext)
            x, y = xb.get(p), pc.get(p)
            if x is not None and y is not None:
                yield c + ext, x, y
    if not XB_LEVEL:
        sys.exit('test_convhkx.py ark: set GLIMPSE_DLC_SCRATCH (the folder holding dlc\\xb_disc\\NEM_STORM.ARK) and '
                 'GLIMPSE_XB_RESIDENT (the Xbox 360 resident.ark).')
    xl, pl = pair_lib.Ark(XB_LEVEL), pair_lib.Ark(PC_LEVEL)

    def get(a, k):
        t, off, cs, us = a.ents[k]
        a.f.seek(off)
        raw = a.f.read(cs + 16)
        return raw[:us] if t == 0 else pair_lib.lzss_n(raw + b'\0' * 16, 4, us)[0] if t == 1 else b''
    for k in sorted(xl.ents):
        if k in pl.ents:
            x = get(xl, k)
            if x[:8] == convhkx.MAGIC:
                yield 'nem_storm_%08x.hkx' % k, x, get(pl, k)


def types_test(ref):
    """rebuild the __types__ section of every PC packfile in the PC archives from its own __data__"""
    import glob
    n = ok = 0
    for fn in sorted(glob.glob(PC_LEARN[0]) + glob.glob(PC_LEARN[1])):
        for key, d in convhkx.ark_havok(fn):
            pf = convhkx.Packfile(d)
            ti, ts = pf.sec('__types__')
            if pf.E != '<' or not ts.data:
                continue
            w = convhkx.Walker(pf, ref['classes'])
            cn = {nm: o for o, _, nm in pf.classnames()}
            raw, offs, at = convhkx.TypesWriter(ref['classes'], ref['enums'], cn).build(
                w.postorder(pf.contents_off if pf.contents_sec == 2 else None))
            n += 1
            if raw == ts.raw and offs == (ts.loc, ts.glob, ts.virt, ts.exp, ts.imp, ts.end):
                ok += 1
            else:
                print('__types__ differs: %s %08x' % (os.path.basename(fn), key))
    print('__types__ rebuilt byte-identical for %d of %d PC packfiles' % (ok, n))


def dir_pairs(xd, pd):
    for n in sorted(os.listdir(xd)):
        if os.path.exists(os.path.join(pd, n)):
            yield n, open(os.path.join(xd, n), 'rb').read(), open(os.path.join(pd, n), 'rb').read()


def labels(x, classes):
    """byte offset in __data__ -> 'Class.member' (innermost)"""
    lab = {}

    class W(convhkx.Walker):
        def struct(self, cname, o):
            if cname in self.classes and (cname, o) not in self.visited:
                for m in self.members(cname):
                    es = self.size(m['type'], m['sub'], m['class'])
                    a = o + m['offset']
                    for b in range(a, a + es * (m['carray'] or 1)):
                        lab[b] = '%s.%s' % (m['owner'], m['name'])
            super().struct(cname, o)
    W(convhkx.Packfile(x), classes).run()
    return lab


def compare(x, out, pc, rep, classes):
    if len(out) != len(pc):
        return 'DIFF', 'size %d vs %d' % (len(out), len(pc))
    if out == pc:
        return 'identical', ''
    ds = convhkx.Packfile(pc).secs[2]
    vol, hard = 0, []
    for i in range(len(pc)):
        if out[i] != pc[i]:
            r = i - ds.start
            if 0 <= r < ds.loc and rep['vol'][r]:
                vol += 1
            else:
                hard.append(i)
    if not hard:
        return 'volatile', '%d bytes in volatile members' % vol
    lab = labels(x, classes)
    per = defaultdict(list)
    for i in hard:
        r = i - ds.start
        per['header/__classnames__/__types__' if r < 0 else lab.get(r, 'data (unlabelled)') if r < ds.loc
            else 'fixup tables'].append(i)
    msg = '; '.join('%s: %d bytes (first at %x: %s vs %s)' % (k, len(v), v[0], out[v[0]:v[0] + 4].hex(),
                                                              pc[v[0]:v[0] + 4].hex()) for k, v in per.items())
    return 'DIFF', '%d volatile bytes, %d other: %s' % (vol, len(hard), msg)


def main(a):
    if not GAME and (not (a and a[0] == 'dirs') or '--learn' in a):
        sys.exit('test_convhkx: set GAME_DIR to the Split/Second game folder')
    verbose = '-v' in a
    pairs = list(dir_pairs(a[1], a[2])) if a and a[0] == 'dirs' else list(ark_pairs())
    if '--extract' in a:
        d = a[a.index('--extract') + 1]
        for sub in ('xb', 'pc'):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        for n, x, p in pairs:
            open(os.path.join(d, 'xb', n), 'wb').write(x)
            open(os.path.join(d, 'pc', n), 'wb').write(p)
    if '--learn' in a:
        if os.path.exists(convhkx.REF):
            os.remove(convhkx.REF)
        convhkx.learn(PC_LEARN)
    ref = convhkx.load_ref()
    if '--types' in a:
        types_test(ref)
    total = Counter()
    for n, x, p in pairs:
        rep = {}
        try:
            out = convhkx.convert(x, ref, rep)
        except Exception as e:
            total['ERROR'] += 1
            print('%-26s ERROR %s' % (n, e))
            continue
        v, msg = compare(x, out, p, rep, ref['classes'])
        total[v] += 1
        if verbose or v != 'identical':
            print('%-26s %-9s %s' % (n, v, msg))
    print('%d pairs: %s' % (len(pairs), dict(total)))


if __name__ == '__main__':
    main(sys.argv[1:])
