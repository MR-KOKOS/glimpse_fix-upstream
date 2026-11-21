# Oracle test for fsbconv: convert base-game Xbox banks (from the Xbox resident.ark) and check them against
# the PC banks of the same path: structure (cmpfsb), loading with the game's FMOD Ex (fmodcheck fsb), and
# loading every event of the Xbox .fev against the converted bank (fmodcheck fev).
# usage: python -I test_fsb.py <work dir> [bank ...]      (bank = path under audio/release without .fsb)
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from pair_lib import Ark, XB, PC  # noqa: E402
import cmpfsb  # noqa: E402
import fsbconv  # noqa: E402

GAME = os.environ.get('GAME_DIR', '')  # the game folder (the game's FMOD DLLs for fmodcheck)
CHECK = os.path.join(HERE, 'fmodcheck.exe')
BANKS = ['tonic/tonic', 'menu/menu', 'global/reverbandgroups', 'vehicles/generic/generic', 'vehicles/cpu/muscle_25',
         'genericpowerplays/genericpowerplays', 'genericpowerplays/generic_lfe', 'particles/particles',
         'surface/surface', 'collision/collision', 'collision/collisioneffects', 'scrapes/scrapes',
         'swooshes/swooshes', 'locales/nem/nem_helicopter', 'locales/nem/nem_truck'] + [
    'locales/%s/%s' % (l, l) for l in ('airport', 'docks', 'downtown', 'end', 'graveyard', 'powerplant', 'nem_training')]


def fmodcheck(*a):
    r = subprocess.run([CHECK, GAME] + list(a), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode, r.stdout.decode('latin-1')


def main():
    if not GAME:
        sys.exit('test_fsb: set GAME_DIR to the Split/Second game folder')
    work = os.path.abspath(sys.argv[1])
    banks = sys.argv[2:] or BANKS
    xb, pc = Ark(XB), Ark(PC)
    summary = []
    for b in banks:
        p = 'audio/release/%s' % b
        name = b.split('/')[-1]
        dx, dp = xb.get(p + '.fsb'), pc.get(p + '.fsb')
        fx = xb.get(p + '.fev')
        if dx is None or dp is None:
            print('skip', b, 'xbox' if dx is None else 'pc', 'bank missing')
            continue
        d = os.path.join(work, b.replace('/', '_'))
        os.makedirs(os.path.join(d, 'media'), exist_ok=True)
        open(os.path.join(d, 'xbox.fsb'), 'wb').write(dx)
        open(os.path.join(d, 'pc.fsb'), 'wb').write(dp)
        out = os.path.join(d, 'media', name + '.fsb')
        print('==', b)
        fsbconv.main([os.path.join(d, 'xbox.fsb'), out])
        probs = cmpfsb.compare(out, os.path.join(d, 'pc.fsb'))
        rc1, t1 = fmodcheck('fsb', out)
        n_ok = t1.count(': ok,')
        rc2, t2 = 0, ''
        if fx is not None:
            open(os.path.join(d, 'media', name + '.fev'), 'wb').write(fx)
            rc2, t2 = fmodcheck('fev', os.path.join(d, 'media') + os.sep, name + '.fev')
        ev = [l for l in t2.splitlines() if l.startswith('events ok') or l.startswith('load')]
        print('fmod fsb:', 'ok' if rc1 == 0 else 'FAIL', '(%d of 3 open modes)' % n_ok, '| fmod fev:',
              ev[-1] if ev else 'no fev', '' if rc2 == 0 else 'FAIL')
        if rc1 or rc2:
            print(t1[-1500:], t2[-1500:])
        summary.append((b, len(probs), rc1, rc2, ev[-1] if ev else ''))
    print('\nSUMMARY bank / structural differences vs PC / fmod fsb rc / fmod fev rc / events')
    for s in summary:
        print('  %-40s %3d  %d  %d  %s' % s)


if __name__ == '__main__':
    main()
