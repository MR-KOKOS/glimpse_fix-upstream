# Convert the audio of the user's own Xbox 360 Split/Second DLC packages into PC files.
#
#   python -I dlcaudio.py <out dir> <package dir> [<package dir> ...] [--codec mp2|pcm] [--no-reuse] [--check]
#
# <package dir> = an extracted DLC package (the folder holding AUDIO/, DLCINFO.TXT, ...). Output paths are the
# game's lower-case virtual paths (audio/release/..., audio/tonic/..., audio/locales/...), i.e. what
# resident.ark keys (h*31+c over the lower-case path) or loose files under data/ would be named.
#   AUDIO/TONIC/_OUTPUTFILES/*.TONIC|TONIC2  -> tonic.convert (byteswap, platform 0)
#   AUDIO/TONIC/**/*.XML, AUDIO/LOCALES/**    -> copied (text, identical format on both platforms); files
#                                                byte-identical to the PC resident.ark copy are left out
#   AUDIO/RELEASE/**/*.FEV                    -> copied (FMOD event files load unchanged in the PC FMOD)
#   AUDIO/RELEASE/**/*.FSB                    -> fsbconv (XMA -> MPEG Layer II); a bank whose sample list
#                                                equals a loose PC bank is replaced by that PC bank;
#                                                single samples byte-identical to Xbox base samples take the
#                                                PC base encoding (needs both resident.ark files)
#   AUDIO/SUBMISSION/**                       -> skipped (byte-identical copies of RELEASE; the PC reads Release)
# A manifest (manifest.txt) lists every output with its archive key.
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import fsbconv  # noqa: E402
import tonic  # noqa: E402
from fsb4 import FSB4  # noqa: E402

GAME = os.environ.get('GAME_DIR') or None  # a manual run's game folder; else --game (build_all.py gives it)
REUSE_BANKS = ['menu/menu', 'vehicles/generic/generic', 'genericpowerplays/genericpowerplays',
               'genericpowerplays/generic_lfe', 'particles/particles', 'surface/surface', 'collision/collision',
               'collision/collisioneffects', 'scrapes/scrapes', 'swooshes/swooshes', 'locales/nem/nem_helicopter',
               'locales/nem/nem_truck'] + ['locales/%s/%s' % (l, l) for l in (
                   'airport', 'docks', 'downtown', 'end', 'graveyard', 'powerplant', 'nem_training')]


def key(path):
    h = 0
    for c in path.lower().replace('\\', '/').encode('latin-1'):
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


def md5(fn):
    return hashlib.md5(open(fn, 'rb').read()).hexdigest()


def pc_loose_banks(game):
    out = []
    for fn in glob.glob(os.path.join(game, 'data', 'audio', 'release', '**', '*.fsb'), recursive=True):
        try:
            f = FSB4(open(fn, 'rb').read())
        except Exception:
            continue
        out.append((fn, [(s.sname.lower(), s.channels, s.samples) for s in f.samples]))
    return out


def same_bank(xbox, pc_list):
    """Xbox bank and PC bank hold the same sounds: same names, order, channels, lengths within a frame."""
    if len(xbox.samples) != len(pc_list):
        return False
    return all(s.sname.lower() == n and s.channels == c and abs(s.samples - l) <= 1152
               for s, (n, c, l) in zip(xbox.samples, pc_list))


def reuse_pairs(work):
    """Extract Xbox/PC base bank pairs from the two resident archives (pair_lib paths)."""
    try:
        from pair_lib import Ark, XB, PC
        xb, pc = Ark(XB), Ark(PC)
    except Exception as e:
        print('no base archives, sample reuse off:', e)
        return []
    pairs = []
    d = os.path.join(work, 'reuse')
    os.makedirs(d, exist_ok=True)
    for b in REUSE_BANKS:
        p = 'audio/release/%s.fsb' % b
        x, y = xb.get(p), pc.get(p)
        if x is None or y is None:
            continue
        fx, fy = os.path.join(d, b.replace('/', '_') + '.xbox.fsb'), os.path.join(d, b.replace('/', '_') + '.pc.fsb')
        open(fx, 'wb').write(x)
        open(fy, 'wb').write(y)
        pairs.append((fx, fy))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('packages', nargs='+')
    ap.add_argument('--codec', choices=('mp2', 'pcm'), default='mp2')
    ap.add_argument('--game', default=GAME, required=not GAME,
                    help='the Split/Second game folder (default: GAME_DIR)')
    ap.add_argument('--no-reuse', action='store_true')
    ap.add_argument('--check', action='store_true', help='load every bank and event file with the game FMOD DLLs')
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    work = os.path.join(out, '_work')
    os.makedirs(work, exist_ok=True)
    pcb = pc_loose_banks(a.game)
    pairs = [] if a.no_reuse else reuse_pairs(work)
    manifest, base_same = [], []
    try:
        from pair_lib import Ark, PC
        pcres = Ark(PC)
    except Exception:
        pcres = None

    def emit(rel, method, src):
        rel = rel.lower().replace('\\', '/')
        manifest.append('%08X %10d %-58s %-34s %s' % (key(rel), os.path.getsize(os.path.join(out, rel)), rel,
                                                      method, src))

    for pkg in a.packages:
        root = os.path.join(pkg, 'AUDIO')
        if not os.path.isdir(root):
            print('no AUDIO folder in', pkg)
            continue
        for dp, dn, fns in os.walk(root):
            for n in sorted(fns):
                src = os.path.join(dp, n)
                rel = 'audio/' + os.path.relpath(src, root).replace('\\', '/').lower()
                parts = rel.split('/')
                dst = os.path.join(out, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                ext = os.path.splitext(n)[1].lower()
                if parts[1] == 'submission':
                    twin = os.path.join(root, 'RELEASE', *os.path.relpath(src, os.path.join(root, 'SUBMISSION')).split(os.sep))
                    same = os.path.exists(twin) and md5(twin) == md5(src)
                    print('skip', rel, '(identical to Release)' if same else '(DIFFERS from Release!)')
                    continue
                if ext in ('.tonic', '.tonic2'):
                    data, info = tonic.convert(open(src, 'rb').read())
                    open(dst, 'wb').write(data)
                    emit(rel, 'tonic byteswap', src)
                elif ext == '.fsb':
                    x = FSB4(open(src, 'rb').read())
                    hit = [fn for fn, lst in pcb if same_bank(x, lst)]
                    if hit:
                        shutil.copyfile(hit[0], dst)
                        print('bank', rel, '= PC bank', hit[0])
                        emit(rel, 'PC bank copy', hit[0])
                        continue
                    args = ['--codec', a.codec, '--work', os.path.join(work, 'tmp')]
                    for fx, fy in pairs:
                        args += ['--reuse', fx, fy]
                    print('== convert', rel)
                    fsbconv.main([src, dst] + args)
                    emit(rel, 'fsbconv ' + a.codec, src)
                else:   # .fev, .xml, anything else: unchanged
                    data = open(src, 'rb').read()
                    if pcres is not None and pcres.get(rel) == data:
                        print('same as PC base, not written:', rel)
                        base_same.append(rel)
                        continue
                    open(dst, 'wb').write(data)
                    emit(rel, 'copy', src)
    shutil.rmtree(work, ignore_errors=True)
    with open(os.path.join(out, 'manifest.txt'), 'w') as f:
        f.write('# key(h*31+c, lower-case path)  size  path  method  source\n')
        f.write('\n'.join(manifest) + '\n')
        for rel in base_same:
            f.write('# identical in the PC resident.ark already (not written): %s\n' % rel)
    print('wrote', len(manifest), 'files, manifest', os.path.join(out, 'manifest.txt'))

    if a.check:
        exe = os.path.join(HERE, 'fmodcheck.exe')
        bad = 0
        for fn in glob.glob(os.path.join(out, 'audio', 'release', '**', '*.fsb'), recursive=True):
            r = subprocess.run([exe, a.game, 'fsb', fn], stdout=subprocess.PIPE)
            t = r.stdout.decode('latin-1')
            print('check fsb %-60s %s' % (os.path.relpath(fn, out), 'ok' if r.returncode == 0 else 'FAIL'),
                  [l for l in t.splitlines() if ': ok,' in l][:1])
            bad += r.returncode != 0
        for fn in glob.glob(os.path.join(out, 'audio', 'release', '**', '*.fev'), recursive=True):
            r = subprocess.run([exe, a.game, 'fev', os.path.dirname(fn) + os.sep, os.path.basename(fn)],
                               stdout=subprocess.PIPE)
            t = r.stdout.decode('latin-1').splitlines()
            print('check fev %-60s %s' % (os.path.relpath(fn, out), t[-1] if t else ''))
            bad += r.returncode != 0
        print('checks failed:', bad)


if __name__ == '__main__':
    main()
