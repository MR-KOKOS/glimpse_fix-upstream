# Shader hashes of the frame dump (Log=1, F9) -> Split/Second shader entries.
#
# The fix's frame dump (src/diag.cpp) names every vertex and pixel shader by a 64-bit FNV-1a hash of its
# D3D9 bytecode: the token stream the game passes to CreateVertexShader / CreatePixelShader, from the
# version token up to and including the end token 0x0000FFFF (found by walking the tokens: comments by
# their length, SM2+ instructions by the length in bits 24-27). This script computes the same hash over
# the code of every PC .fxc entry and prints hash -> (kind, entry ids, files), so the log can be read
# with the game's shader IDs.
#
# Sources (read only): the car bodies of the PC resident.ark (vehicles/bodies/<car>/<car><lod>.fxc),
# optionally every other .fxc in it found by magic (--all), and any further .ark given with --ark (a DLC
# overlay; its .fxc files found by magic as well).
#
#   python -I pshash.py [--all] [--ark <ark>]... [--find <text>] [--ini]
#       prints the table (or only the entries whose id contains <text>); --ini: the DumpWatchPS= line
#       with the pixel shader hashes of the shown entries
#   python -I pshash.py [--ark <ark>]... --cso <file or folder>...
#       hashes .cso files (a dump's f<n>_ps_<ptr>.cso, written from the runtime's GetFunction) and looks
#       them up: shows that what the runtime holds is the .fxc code byte for byte
#   python -I pshash.py [--ark <ark>]... --annotate <SplitSecondPad.log>
#       the log with the entry ids added behind every hash it knows
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'misc'))
import convshaders  # noqa: E402
from arkio import Ark, key  # noqa: E402

# the PC resident.ark: below the game folder in the environment variable GAME_DIR
PC_ARK = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None
FNV_OFFSET = 0xcbf29ce484222325
FNV_PRIME = 0x100000001b3
MASK = 0xFFFFFFFFFFFFFFFF


def token_length(code):
    """Bytes from the version token through the end token, as diag.cpp's ShaderLength counts them;
    None when the stream has no end token."""
    n = len(code) // 4
    if n < 2:
        return None
    toks = struct.unpack_from('<%dI' % n, code)
    major = (toks[0] >> 8) & 0xFF
    i = 1
    while i < n:
        t = toks[i]
        if t == 0x0000FFFF:
            return (i + 1) * 4
        if (t & 0xFFFF) == 0xFFFE:
            i += 1 + ((t >> 16) & 0x7FFF)
        elif major >= 2:
            i += 1 + ((t >> 24) & 0x0F)
        else:  # SM1: no length in the token, the end token is searched for (diag.cpp does the same)
            i += 1
    return None


def fnv64(data):
    h = FNV_OFFSET
    for b in data:
        h = ((h ^ b) * FNV_PRIME) & MASK
    return h


def shader_hash(code):
    """(hash, bytes hashed, note) for one shader's code."""
    n = token_length(code)
    note = ''
    if n is None:
        n, note = len(code), 'no end token'
    elif n != len(code):
        note = '%d bytes after the end token' % (len(code) - n)
    return fnv64(code[:n]), n, note


def fxc_files(arks, scan_all):
    """Yield (source name, data) of every .fxc to hash."""
    if not PC_ARK:
        raise SystemExit('pshash: set GAME_DIR to the Split/Second game folder (its data\\resident.ark is read)')
    pc = Ark(PC_ARK)
    seen = set()
    for car in convshaders.CARS:
        for lod in convshaders.LODS:
            p = 'vehicles/bodies/%s/%s%s.fxc' % (car, car, lod)
            d = pc.get(p)
            if d:
                seen.add(key(p))
                yield 'resident.ark:' + p, d
    sources = [(pc, 'resident.ark', seen)] if scan_all else []
    for fn in arks:
        sources.append((Ark(fn), os.path.basename(fn), set()))
    for ark, name, skip in sources:
        known = {}
        if name != 'resident.ark':  # the car body names of the overlay, where it has them
            for car in convshaders.CARS + ['musclecar_17', 'musclecar_18', 'supercar_17', 'supercar_18',
                                           'truck_09', 'truck_10']:
                for lod in convshaders.LODS:
                    p = 'vehicles/bodies/%s/%s%s.fxc' % (car, car, lod)
                    known[key(p)] = p
        for k, _magic, d in convshaders.scan_ark(ark, (convshaders.FXC_MAGIC[::-1],)):
            if k not in skip:
                yield '%s:%s' % (name, known.get(k, 'key %08x' % k)), d


def build_table(arks, scan_all):
    """hash -> {'kind', 'size', 'ids': set, 'files': [..], 'note'}; plus a list of problems."""
    table, problems, by_code = {}, [], {}
    for src, data in fxc_files(arks, scan_all):
        try:
            f = convshaders.parse_fxc(data)
        except ValueError as e:
            problems.append('%s: %s' % (src, e))
            continue
        for e in f['ents']:
            for kind, (_consts, code) in zip(('vs', 'ps'), e['shaders']):
                if not code:
                    continue
                r = by_code.get(code)
                if r is None:
                    r = by_code[code] = shader_hash(code)
                h, n, note = r
                t = table.get(h)
                if t is None:
                    t = table[h] = {'kind': kind, 'size': n, 'ids': set(), 'files': [], 'note': note, 'code': code}
                elif t['code'][:n] != code[:n]:
                    problems.append('hash collision %016x: %s %s and %s' % (h, src, e['id'], sorted(t['ids'])))
                t['ids'].add(e['id'])
                if src not in t['files']:
                    t['files'].append(src)
                if note:
                    problems.append('%s %s %s: %s' % (src, e['id'], kind, note))
    return table, problems


def describe(t):
    ids = sorted(t['ids'], key=lambda i: (i[2:], i))
    files = t['files']
    where = files[0] + (' (+%d files)' % (len(files) - 1) if len(files) > 1 else '')
    return '%s %5d bytes  %s  in %s' % (t['kind'], t['size'], ' '.join(ids), where)


def main(argv):
    arks, cso, annotate, find, scan_all, ini = [], [], None, None, False, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--ark':
            arks.append(argv[i + 1]); i += 2
        elif a == '--cso':
            i += 1
            while i < len(argv) and not argv[i].startswith('--'):
                cso.append(argv[i]); i += 1
        elif a == '--annotate':
            annotate = argv[i + 1]; i += 2
        elif a == '--find':
            find = argv[i + 1]; i += 2
        elif a == '--all':
            scan_all = True; i += 1
        elif a == '--ini':
            ini = True; i += 1
        else:
            with open(os.path.abspath(__file__)) as f:  # the comment at the top is the usage
                for ln in f:
                    if not ln.startswith('#'):
                        break
                    print(ln[2:].rstrip())
            return 1
    table, problems = build_table(arks, scan_all)
    for p in problems:
        print('# ' + p, file=sys.stderr)
    if cso:
        files = []
        for c in cso:
            if os.path.isdir(c):
                files += [os.path.join(c, n) for n in sorted(os.listdir(c)) if n.lower().endswith('.cso')]
            else:
                files.append(c)
        hit = 0
        for fn in files:
            code = open(fn, 'rb').read()
            h, n, note = shader_hash(code)
            t = table.get(h)
            hit += t is not None
            print('%016x %s %s%s' % (h, os.path.basename(fn), describe(t) if t else '(no .fxc entry)',
                                     ' [%s]' % note if note else ''))
        print('# %d of %d .cso files are .fxc code' % (hit, len(files)))
        return 0
    if annotate:
        pat = re.compile(r'\b([0-9a-f]{16})\b')
        for line in open(annotate, 'r', encoding='latin-1'):
            line = line.rstrip('\n')
            names = []
            for m in pat.finditer(line):
                t = table.get(int(m.group(1), 16))
                if t:
                    names.append('%s=%s' % (m.group(1)[:8], '/'.join(sorted(t['ids'], key=lambda i: (i[2:], i)))))
            print(line + ('    <- ' + ' '.join(names) if names else ''))
        return 0
    shown = []
    for h in sorted(table, key=lambda h: (min(i[2:] for i in table[h]['ids']), table[h]['kind'], h)):
        t = table[h]
        if find and not any(find.lower() in i.lower() for i in t['ids']):
            continue
        shown.append(h)
        print('%016x %s' % (h, describe(t)))
    print('# %d hashes (%d vs, %d ps)%s' % (len(shown), sum(table[h]['kind'] == 'vs' for h in shown),
                                            sum(table[h]['kind'] == 'ps' for h in shown),
                                            ', %d problems' % len(problems) if problems else ''))
    if ini:
        print('DumpWatchPS=' + ','.join('%016x' % h for h in shown if table[h]['kind'] == 'ps'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
