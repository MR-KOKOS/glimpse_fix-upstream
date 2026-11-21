# Validation of the Xenos -> SM3 translator on shader IDs that exist on both platforms.
#
#   python -I validate.py collect <work dir>         read the Xbox / PC archives (read only) into <work>/all.pkl
#   python -I validate.py run <work dir> [filter]     translate + compare, writes <work>/validate.txt / .pkl
#
# Per shader (VS, PS) of each ID:
#   fidelity : Xbox microcode (xinterp) vs the translated bytecode (sm3interp)  -> tests the translator
#   truth    : PC bytecode of the variant the game uses on current GPUs (1x if it exists, else 0x; INTZ path)
#              vs the translated bytecode                                       -> tests the whole method
# Inputs are random but keyed by constant name / semantic / sampler name (xcompare.py), L lanes per run.
import collections
import glob
import os
import pickle
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'shaders'))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'misc'))
sys.path.insert(0, os.path.dirname(HERE))

import xshader      # noqa: E402
import xlate        # noqa: E402
import xcompare     # noqa: E402

# development inputs: the Xbox archives below GLIMPSE_DLC_SCRATCH\dlc (track_probe\xb_gy, xb_disc, x_onslaught) and the
# Xbox resident.ark in GLIMPSE_XB_RESIDENT (as pair_lib.py)
SCRATCH = os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc') if os.environ.get('GLIMPSE_DLC_SCRATCH') else ''
XRES = os.environ.get('GLIMPSE_XB_RESIDENT', '')
# the PC game data: below the game folder in the environment variable GAME_DIR (collect only)
GAME = os.path.join(os.environ['GAME_DIR'], 'data') if os.environ.get('GAME_DIR') else None


def collect(work, xb_arks=None, pc_dir=GAME):
    import arkio
    import convshaders as cs
    if not pc_dir:
        sys.exit('validate.py collect needs GAME_DIR (the Split/Second game folder) for the PC archives.')

    def scan(fn, tag):
        a = arkio.Ark(fn)
        out = []
        for k in a.order:
            if a.ents[k][3] < 8:
                continue
            d = a.get(k)
            if d[:4] in (b'\0FXC', b'CXF\0'):
                out.append((tag, k, 'fxc', cs.parse_fxc(d)))
            elif d[:4] in (b'SHDR', b'RDHS'):
                out.append((tag, k, 'shaders', cs.parse_shdr(d)))
        a.close()
        return out
    if xb_arks is None:
        if not XRES or not SCRATCH:
            sys.exit('validate.py needs GLIMPSE_XB_RESIDENT (the Xbox 360 resident.ark) and GLIMPSE_DLC_SCRATCH '
                     '(the folder holding dlc\\xb_disc, dlc\\x_onslaught, dlc\\track_probe).')
        xb_arks = [(XRES, 'xres')]
        for f in glob.glob(SCRATCH + r'\track_probe\xb_gy\*.ARK') + glob.glob(SCRATCH + r'\xb_disc\*.ARK'):
            xb_arks.append((f, 'x:' + os.path.basename(f)))
        for f in glob.glob(SCRATCH + r'\x_onslaught\*.ARK'):
            xb_arks.append((f, 'q:' + os.path.basename(f)))
    pc = [(os.path.join(pc_dir, 'resident.ark'), 'pres')]
    for a in glob.glob(pc_dir + r'\*.ark') + glob.glob(pc_dir + r'\Environments\Levels\*.ark'):
        if not a.lower().endswith('resident.ark'):
            pc.append((a, 'p:' + os.path.basename(a)))
    res = {'xb': [], 'pc': []}
    for fn, tag in xb_arks:
        res['xb'] += scan(fn, tag)
    for fn, tag in pc:
        res['pc'] += scan(fn, tag)
    os.makedirs(work, exist_ok=True)
    pickle.dump(res, open(os.path.join(work, 'all.pkl'), 'wb'))


def car_keys():
    import pair_lib
    import convshaders as cs
    ks = set()
    for car in cs.CARS:
        for lod in cs.LODS:
            ks.add(pair_lib.key('vehicles/bodies/%s/%s%s.fxc' % (car, car, lod)))
    return ks


def category(tag, k, cars):
    if tag == 'xres':
        return 'car' if k in cars else 'resident'
    t = tag.split(':', 1)[1].lower().replace('.ark', '')
    return t


def load(work):
    db = pickle.load(open(os.path.join(work, 'all.pkl'), 'rb'))
    cars = car_keys()
    xb = {}             # id -> (category, entry)
    order = ['car', 'graveyard', 'graveyardpowerplays', 'nem_storm', 'nem_stormpowerplays', 'resident']
    for tag, k, ext, f in db['xb']:
        if ext != 'fxc' or tag.startswith('q:'):
            continue
        cat = category(tag, k, cars)
        pri = order.index(cat) if cat in order else 99
        for e in f['ents']:
            i = e['id'][2:]
            if i not in xb or pri < xb[i][2]:
                xb[i] = (cat, e, pri)
    xb = {i: v[:2] for i, v in xb.items()}
    pc = {}
    for tag, k, ext, f in db['pc']:
        if ext == 'fxc':
            for e in f['ents']:
                pc.setdefault(e['id'], e)
    return xb, pc, db


def check_stage(xcode, tcode, pcode, L=256, seed=1):
    x = xshader.XShader(xcode)
    xr = xcompare.XRun(x, L=L, seed=seed)
    xr.depth_flip = True           # the translation converts scene depth to the Xbox convention
    a = xr.run()
    b = xcompare.PRun(tcode, L=L, seed=seed).run()
    fid = xcompare.compare_outputs(a, b)
    tru = None
    if pcode is not None:
        c = xcompare.PRun(pcode, L=L, seed=seed).run()
        tru = xcompare.compare_outputs(c, b)
    return fid, tru


def vs_input_search(xcode, pcode, L=128, seed=1, max_combos=64):
    """For a VS whose truth check fails: find vertex elements whose PC data is the Xbox value encoded as
    (v+1)/2 (PC shader decodes with *2-1), i.e. Xbox(2p-1) == PC(p). Returns the element list or None."""
    import itertools
    x = xshader.XShader(xcode)
    els = [(v['usage'], v['index']) for v in x.vertex_elements if v['usage'] != 0]
    if not els or 2 ** len(els) > max_combos:
        return None
    c = xcompare.PRun(pcode, L=L, seed=seed).run()
    for k in range(1, len(els) + 1):
        for combo in itertools.combinations(els, k):
            r = xcompare.XRun(x, L=L, seed=seed)
            r.vin_xform = {e: (lambda v: v * 2 - 1) for e in combo}
            rep = xcompare.compare_outputs(c, r.run())
            if verdict(rep) == 'ok':
                return list(combo)
    return None


def verdict(rep):
    if rep is None:
        return 'n/a'
    bad = [r for r in rep if r[1] in ('DIFF', 'MISSING')]
    return 'ok' if not bad else 'FAIL'


def run(work, flt=None):
    xb, pc, db = load(work)
    cache_fn = os.path.join(work, 'xlate_cache3.pkl')
    cache = pickle.load(open(cache_fn, 'rb')) if os.path.exists(cache_fn) else {}
    results = {}
    t0 = time.time()
    ids = sorted(i for i in xb if ('0x' + i) in pc and (not flt or flt in i))
    for n, i in enumerate(ids):
        cat, e = xb[i]
        names = [p + i for p in ('1x', '0x')]
        r = {'cat': cat}
        for j, st in ((0, 'vs'), (1, 'ps')):
            xcode = e['shaders'][j][1]
            key = (st, xcode)
            if key not in cache:
                try:
                    code, notes, src = xlate.translate_container(xcode, verify=True)
                    cache[key] = ('ok', code, notes)
                except xlate.Unsupported as err:
                    cache[key] = ('unsupported', None, [str(err)])
                except Exception as err:
                    cache[key] = ('error', None, ['%s: %s' % (type(err).__name__, err)])
            status, code, notes = cache[key]
            rr = {'xlate': status, 'notes': notes}
            if code is not None:
                pv = pc.get(names[0]) if j == 0 else None
                pv = pv or pc.get('0x' + i)
                try:
                    fid, tru = check_stage(xcode, code, pv['shaders'][j][1])
                    rr['fid'], rr['truth'] = fid, tru
                    if j == 0 and verdict(tru) != 'ok':
                        rr['vin_decode'] = vs_input_search(xcode, pv['shaders'][j][1])
                except Exception as err:
                    rr['xlate'] = 'check-error'
                    rr['notes'] = rr['notes'] + ['%s: %s' % (type(err).__name__, err)]
            r[st] = rr
        results[i] = r
        if n % 50 == 0:
            print('%d/%d %.0fs' % (n, len(ids), time.time() - t0), flush=True)
            pickle.dump(cache, open(cache_fn, 'wb'))
    pickle.dump(cache, open(cache_fn, 'wb'))
    pickle.dump(results, open(os.path.join(work, 'validate.pkl'), 'wb'))
    report(results, os.path.join(work, 'validate.txt'))


def report(results, fn):
    tab = collections.defaultdict(collections.Counter)
    causes = collections.Counter()
    lines = []
    for i, r in sorted(results.items()):
        for st in ('vs', 'ps'):
            rr = r[st]
            c = (r['cat'], st)
            tab[c]['ids'] += 1
            if rr['xlate'] != 'ok':
                tab[c]['not translated'] += 1
                causes[(st, rr['notes'][0][:60] if rr['notes'] else rr['xlate'])] += 1
                lines.append('%s %s %s: %s %s' % (i, r['cat'], st, rr['xlate'], rr['notes']))
                continue
            tab[c]['translated'] += 1
            fv, tv = verdict(rr.get('fid')), verdict(rr.get('truth'))
            if tv != 'ok' and rr.get('vin_decode'):
                tv = 'ok-with-vertex-decode'
                causes[('vs decode', tuple(rr['vin_decode']))] += 1
            if any('without optimisation' in n for n in rr['notes']):
                tab[c]['unoptimised'] += 1
            tab[c]['fidelity ' + fv] += 1
            tab[c]['truth ' + tv] += 1
            if fv != 'ok' or tv != 'ok':
                lines.append('%s %s %s: fidelity %s truth %s | %s | %s' % (
                    i, r['cat'], st, fv, tv, [x for x in rr.get('fid') or [] if x[1] != 'ok'],
                    [x for x in rr.get('truth') or [] if x[1] != 'ok']))
    out = ['# per category / stage']
    for c in sorted(tab):
        out.append('%-22s %s  %s' % (c[0], c[1], dict(tab[c])))
    out.append('# untranslated causes')
    for k, v in causes.most_common():
        out.append('%5d %s' % (v, k))
    out.append('# details')
    open(fn, 'w').write('\n'.join(out + lines) + '\n')
    print('\n'.join(out))


if __name__ == '__main__':
    if len(sys.argv) >= 3 and sys.argv[1] == 'collect':
        collect(sys.argv[2])
    elif len(sys.argv) >= 3 and sys.argv[1] == 'run':
        run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__ if __doc__ else 'usage: validate.py collect|run <work>')
