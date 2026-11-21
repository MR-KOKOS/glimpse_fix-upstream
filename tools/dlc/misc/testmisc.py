# Test oracle for convmisc.py / arkio.py: converts the Xbox copies of base-game files and compares them with
# the PC copies byte for byte (cars from the resident archives, nem_storm level + powerplays entries by key),
# then checks DLC entries by content: a converted Xbox DLC entry that is byte-identical to some PC base entry
# (the Onslaught RESIDENT_DLC.ARK carries copies of base track files) counts as verified.
# Usage: python -I testmisc.py [cars] [level] [pp] [dlc] [ark]      (default: all)
# Inputs from the environment: GAME_DIR (the PC game folder), GLIMPSE_XB_RESIDENT, GLIMPSE_DLC_SCRATCH (below).
import glob
import hashlib
import os
import struct
import sys
import tempfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convmisc as C
from arkio import Ark, write_ark, key

# development inputs: the Xbox resident.ark in GLIMPSE_XB_RESIDENT (as pair_lib.py), the Xbox dumps below
# GLIMPSE_DLC_SCRATCH\dlc (xb_disc, x_deadline, x_onslaught)
XB_RES = os.environ.get('GLIMPSE_XB_RESIDENT', '')
DLC = os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc') if os.environ.get('GLIMPSE_DLC_SCRATCH') else ''
# the PC game data: below the game folder in the environment variable GAME_DIR
if not os.environ.get('GAME_DIR'):
    sys.exit('testmisc: set GAME_DIR to the Split/Second game folder')
PCD = os.path.join(os.environ['GAME_DIR'], 'data')
LEV = PCD + r'\environments\levels'
LEVEL_PAIRS = [
    (DLC + r'\xb_disc\NEM_STORM.ARK', LEV + r'\nem_storm.ark', 'main'),
    (DLC + r'\xb_disc\DEFERRED\ENVIRONMENTS\LEVELS\NEM_STORM\SECTORS\NEM_STORM.STATIC.ARK',
     LEV + r'\nem_storm\sectors\nem_storm.static.ark', 'static'),
    (DLC + r'\xb_disc\DEFERRED\ENVIRONMENTS\LEVELS\NEM_STORM\SECTORS\NEM_STORM.STREAM.ARK',
     LEV + r'\nem_storm\sectors\nem_storm.stream.ark', 'stream'),
]
CARS = ['musclecar_%02d' % n for n in range(1, 17)] + ['supercar_%02d' % n for n in range(1, 17)] + \
       ['truck_%02d' % n for n in range(1, 9)]


def masked(a, b, dc):
    a, b = bytearray(a), bytearray(b)
    for s, e in dc:
        a[s:e] = bytes(e - s)
        b[s:e] = bytes(e - s)
    return a == b


def records_reordered(x, p):
    """pool files whose records are the same set in another order (PC rebuilt the pool)"""
    ver, n = struct.unpack_from('<I', p, 0)[0], struct.unpack_from('<I', p, 16)[0]
    S = C.POOL_STRIDE.get(ver)
    if S is None:
        return False
    recs = lambda d: sorted(d[0x24 + i * S:0x24 + (i + 1) * S] for i in range(n))
    return x[:0x24] == p[:0x24] and recs(x) == recs(p) and x[0x24 + n * S:] == p[0x24 + n * S:]


def compare(kind, x, p, res, tag):
    dc = []
    if kind == 'deform':
        o = C.conv_deform(x, dc)
    elif kind == 'nodes':
        o = C.conv_nodes(x, dc)
    else:
        o = C.convert(x, kind)
    if o == p:
        res[tag]['identical'] += 1
    elif len(o) == len(p) and dc and masked(o, p, dc):
        res[tag]['equal except exporter garbage'] += 1
    elif kind == 'pool' and records_reordered(o, p):
        res[tag]['same records, other order'] += 1
    else:
        res[tag]['MISMATCH'] += 1
        d = [i for i in range(min(len(o), len(p))) if o[i] != p[i]]
        print('  mismatch %s: %d vs %d bytes, %d differ, first %s' % (tag, len(o), len(p), len(d),
              hex(d[0]) if d else '-'))
        return False
    return True


def test_cars():
    print('== cars (Xbox resident vs PC resident)')
    xb, pc = Ark(XB_RES), Ark(PCD + r'\resident.ark')
    res = defaultdict(Counter)
    for car in CARS:
        for sfx, kind in (('.deform', 'deform'), ('.parts', 'parts'), ('_dynamicparts.params', 'text')):
            path = 'vehicles/bodies/%s/%s%s' % (car, car, sfx)
            x, p = xb.get(path), pc.get(path)
            if x is not None and p is not None:
                compare(kind, x, p, res, sfx)
        for fn in ('deformationcontrol.params', 'surfacedamage.params'):
            path = 'vehicles/bodies/%s/%s' % (car, fn)
            x, p = xb.get(path), pc.get(path)
            if x is not None and p is not None:
                compare('text', x, p, res, fn)
        path = 'vehicles/frontend/bodies/%s/chassisname.txt' % car
        x, p = xb.get(path), pc.get(path)
        if x is not None and p is not None:
            compare('text', x, p, res, 'chassisname.txt')
    for t in sorted(res):
        print('  %-28s %s' % (t, dict(res[t])))
    # the DLC cars: parse check only (no PC copy exists)
    for d in sorted(glob.glob(DLC + r'\x_deadline\VEHICLES\BODIES\*')):
        car = os.path.basename(d)
        for sfx in ('.DEFORM', '.PARTS'):
            f = os.path.join(d, car + sfx)
            if os.path.exists(f):
                out = C.convert(open(f, 'rb').read(), name=f)
                print('  DLC %s%s converts (%d bytes)' % (car, sfx, len(out)))


def test_level():
    print('== nem_storm level arks (Xbox disc vs PC, paired by key)')
    res = defaultdict(Counter)
    for xa, pa, tag in LEVEL_PAIRS:
        xb, pc = Ark(xa), Ark(pa)
        for k in sorted(xb.ents):
            if k not in pc.ents:
                continue
            x = xb.get(k)
            if tag == 'stream':
                res['stream (textures, model streams)']['foreign'] += 1
                continue
            kind, how = C.detect(x, key=k)
            if how == 'foreign':
                res[kind]['foreign (other converter)'] += 1
                continue
            p = pc.get(k)
            compare(kind, x, p, res, '%s/%s' % (kind, how))
        only = [k for k in xb.ents if k not in pc.ents]
        if only:
            print('  %s: %d Xbox-only entries (%s)' % (tag, len(only), ', '.join('%08x' % k for k in only)))
    for t in sorted(res):
        print('  %-28s %s' % (t, dict(res[t])))


def test_pp():
    print('== NEM_STORMPOWERPLAYS.ARK entries vs PC powerplays arks (by key)')
    xb = Ark(DLC + r'\xb_disc\NEM_STORMPOWERPLAYS.ARK')
    pcs = [Ark(f) for f in glob.glob(PCD + r'\*powerplays.ark')]
    res = defaultdict(Counter)
    for k in sorted(xb.ents):
        p = next((a.get(k) for a in pcs if k in a.ents), None)
        if p is None:
            continue
        x = xb.get(k)
        kind, how = C.detect(x, key=k)
        if how != 'foreign':
            compare(kind, x, p, res, '%s/%s' % (kind, how))
    for t in sorted(res):
        print('  %-28s %s' % (t, dict(res[t])))


def pc_hashes():
    hs = set()
    for f in glob.glob(LEV + r'\*.ark') + glob.glob(PCD + r'\*powerplays.ark'):
        a = Ark(f)
        for k in a.ents:
            if a.ents[k][3] <= 4 << 20:
                d = a.get(k)
                hs.add(hashlib.sha1(d).digest())
    return hs


def test_dlc():
    print('== Onslaught DLC arks: detected kinds, converted entries found byte-identical in a PC base ark')
    hs = pc_hashes()
    for fn in ('QUARRY.ARK', 'QUARRYPOWERPLAYS.ARK', 'RESIDENT_DLC.ARK',
               r'ENVIRONMENTS\LEVELS\QUARRY\SECTORS\QUARRY.STATIC.ARK', r'..\xb_disc\NEM_STORMPOWERPLAYS.ARK'):
        a = Ark(os.path.join(DLC, 'x_onslaught', fn))
        res = defaultdict(Counter)
        for k in a.ents:
            x = a.get(k)
            kind, how = C.detect(x, key=k)
            if how == 'foreign':
                res[kind]['foreign'] += 1
                continue
            try:
                o = C.convert(x, kind)
            except Exception as e:
                res['%s/%s' % (kind, how)]['FAILED'] += 1
                print('  %s %08x %s: %s' % (fn, k, kind, e))
                continue
            found = hashlib.sha1(o).digest() in hs
            res['%s/%s' % (kind, how)]['in PC' if found else 'new'] += 1
        print('  ' + fn)
        for t in sorted(res):
            print('    %-22s %s' % (t, dict(res[t])))


def test_ark():
    print('== ark writer round trip')
    files = [('environments/levels/x/a.txt', b'hello\r\n'), (0x12345678, b'\x01\x02\x03'), ('b/c.bin', bytes(5000))]
    with tempfile.TemporaryDirectory() as td:
        for pad in (0x800, 0):
            fn = os.path.join(td, 't.ark')
            write_ark(fn, files, pad=pad)
            with Ark(fn) as a:
                ok = all(a.get(k) == d for k, d in files) and not a.be
                keys = a.order
            raw = open(fn, 'rb').read()
            ver, hs, n, oA, nB, oB = struct.unpack_from('<6I', raw)
            print('  pad %#x: %s, size %d, header ver %#x hsize %#x (16+20n %s) tableB (%d, %#x), index sorted %s'
                  % (pad, 'read back OK' if ok else 'FAILED', len(raw), ver, hs, hs == 16 + 20 * n, nB, oB,
                     keys == sorted(keys)))
    # same layout as a PC sector ark: rebuild nem_storm.static.ark from its own entries and compare
    pa = LEV + r'\nem_storm\sectors\nem_storm.static.ark'
    a = Ark(pa)
    order = sorted(a.ents, key=lambda k: a.ents[k][1])
    with tempfile.TemporaryDirectory() as td:
        fn = os.path.join(td, 's.ark')
        write_ark(fn, [(k, a.get(k)) for k in order], pad=0)
        same = open(fn, 'rb').read() == open(pa, 'rb').read()
    print('  rebuilt nem_storm.static.ark (all stored, file order kept, no padding): %s' %
          ('byte-identical to the PC file' if same else 'DIFFERENT'))


if __name__ == '__main__':
    if not XB_RES or not DLC:
        sys.exit('testmisc.py needs GLIMPSE_XB_RESIDENT (the Xbox 360 resident.ark) and GLIMPSE_DLC_SCRATCH (the folder '
                 'holding dlc\\xb_disc, dlc\\x_deadline, dlc\\x_onslaught).')
    what = sys.argv[1:] or ['cars', 'level', 'pp', 'dlc', 'ark']
    for w in what:
        globals()['test_' + w]()
