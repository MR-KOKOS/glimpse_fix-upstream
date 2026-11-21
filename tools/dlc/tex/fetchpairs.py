# Extract every base-game car .textures file from the Xbox 360 and the PC resident.ark into a cache folder
# (CACHE/xb/<path> and CACHE/pc/<path>) so test_convtex.py can compare them.  Uses pair_lib (LZSS is pure Python,
# so the work is spread over processes).
#   python -I fetchpairs.py [cache_dir]
import os
import sys
sys.dont_write_bytecode = True
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pair_lib  # noqa: E402

# default cache: GLIMPSE_DLC_SCRATCH\dlc\texpairs (development); else give it as the argument
DEFAULT_CACHE = (os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc', 'texpairs')
                 if os.environ.get('GLIMPSE_DLC_SCRATCH') else '')
CARS = (['musclecar_%02d' % i for i in range(1, 17)] + ['supercar_%02d' % i for i in range(1, 17)] +
        ['truck_%02d' % i for i in range(1, 9)])
SUFFIXES = ['', '_bodypaint', '_damagemap', '_whls']


def paths():
    for car in CARS:
        for s in SUFFIXES:
            for low in ('', '.low'):
                yield 'vehicles/bodies/%s/%s%s%s.textures' % (car, car, s, low)


_arks = {}


def job(args):
    cache, path = args
    res = []
    for tag, fn in (('xb', pair_lib.XB), ('pc', pair_lib.PC)):
        out = os.path.join(cache, tag, path.replace('/', os.sep))
        if os.path.exists(out):
            res.append(tag)
            continue
        if tag not in _arks:
            _arks[tag] = pair_lib.Ark(fn)
        d = _arks[tag].get(path)
        if d is None:
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out + '.tmp', 'wb') as f:
            f.write(d)
        os.replace(out + '.tmp', out)
        res.append(tag)
    return path, res


def main():
    cache = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CACHE
    if not cache:
        sys.exit('fetchpairs.py: give the cache folder or set GLIMPSE_DLC_SCRATCH (GLIMPSE_XB_RESIDENT: the Xbox '
                 '360 resident.ark).')
    todo = [(cache, p) for p in paths()]
    n = 0
    with Pool(min(30, os.cpu_count() or 4)) as pool:
        for path, res in pool.imap_unordered(job, todo):
            if len(res) == 2:
                n += 1
            elif res:
                print('only', res, path)
    print('%d pairs in %s' % (n, cache))


if __name__ == '__main__':
    main()
