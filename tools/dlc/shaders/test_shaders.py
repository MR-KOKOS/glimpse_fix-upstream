# Verify convshaders.py on the base cars (both resident.ark files, see ../pair_lib.py):
#  1. every PC .fxc/.shaders file of the PC resident.ark parses and re-encodes byte-identically, and the
#     pool is consistent (same entry name = same bytes, same variant list per ID, in all files);
#  2. leave-one-out: each base car's PC .fxc/.shaders rebuilt from its Xbox file's ID list plus the OTHER
#     cars' PC entries (a: base car bodies only, b: plus all other PC shader files of the archive, minus the
#     car's own files), compared with the real PC file;
#  3. .shaderinst Xbox -> PC by structure, compared with the real PC file; every .shaderinst of both
#     archives (found by magic) round-trips.
# Usage: python -I test_shaders.py [-v]
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import convshaders as C               # noqa: E402
from pair_lib import Ark, XB, PC, key  # noqa: E402

verbose = '-v' in sys.argv
xb, pc = Ark(XB), Ark(PC)
cache, scan = {}, {}
same = 0
for k, magic, data in C.scan_ark(pc, (C.FXC_MAGIC[::-1], C.SHDR_MAGIC[::-1])):
    ext = '.fxc' if magic == C.FXC_MAGIC[::-1] else '.shaders'
    f = (C.parse_fxc if ext == '.fxc' else C.parse_shdr)(data)
    scan[k] = (ext, f)
    same += (C.build_fxc if ext == '.fxc' else C.build_shdr)(dict(f, E=None)) == data
print('PC .fxc/.shaders files in resident.ark (by magic): %d parsed, %d re-encode identically' % (len(scan), same))
full = C.Pool(ark=pc, cache=cache)
n = full.add_ark_scan(pc, cache=scan)
print('pool: %d fxc / %d shaders entry names (%d fxc+shaders names only outside the car bodies), %d code/variant conflicts'
      % (len(full.fxc), len(full.shdr), n, len(full.conflicts)))
for c in full.conflicts[:20]:
    print('  conflict', c)

st = collections.Counter()
unique = {'a': set(), 'b': set()}   # IDs no other PC file has (cannot be rebuilt leave-one-out)
for car in C.CARS:
    own = set(key('vehicles/bodies/%s/%s%s%s' % (car, car, lod, ext))
              for lod in C.LODS for ext in ('.fxc', '.shaders'))
    pools = {'a': C.Pool(exclude=[car], cache=cache)}
    pools['b'] = C.Pool(exclude=[car], cache=cache)
    pools['b'].add_ark_scan(pc, skip_keys=own, cache=scan)
    for lod in C.LODS:
        base = 'vehicles/bodies/%s/%s%s' % (car, car, lod)
        for ext in ('.fxc', '.shaders', '.shaderinst'):
            a, b = xb.get(base + ext), pc.get(base + ext)
            if a is None or b is None:
                if a is not None or b is not None:
                    st[ext + ' only on one platform'] += 1
                continue
            if ext == '.shaderinst':
                st[ext + ' files'] += 1
                st[ext + (' identical' if C.convert_sdri(a) == b else ' DIFFERENT')] += 1
                continue
            for mode in ('a', 'b'):
                tag = '%s (%s) ' % (ext, 'base cars' if mode == 'a' else 'all PC files')
                st[tag + 'files'] += 1
                out, probs = C.convert_file(pools[mode], base + ext, a)
                if out == b:
                    st[tag + 'identical'] += 1
                    st[tag + 'identical, variant list guessed'] += any('guessed' in p for p in probs)
                    continue
                hashes = set(p.split()[-1][2:] for p in probs if p.startswith('missing'))
                if ext == '.shaders' or not hashes:
                    st[tag + 'DIFFERENT'] += 1
                    print('DIFF', mode, base + ext, len(out), len(b), probs[:3])
                    continue
                # compare what could be built: the real file minus the IDs no other file has
                real = cache[base + ext]
                unique[mode] |= hashes
                kept = [e for e in real['ents'] if e['id'][2:] not in hashes]
                ref = (C.build_fxc if ext == '.fxc' else C.build_shdr)(dict(real, ents=kept))
                st[tag + ('identical except IDs unique to this car' if out == ref else 'DIFFERENT')] += 1
                if verbose:
                    print('%s %s%s: %d IDs unique to this car' % (mode, base, ext, len(hashes)))

# every .shaderinst in both archives
for name, ark, magic in (('PC', pc, C.SDRI_MAGIC[::-1]), ('Xbox', xb, C.SDRI_MAGIC)):
    cnt = ok = 0
    for k, _, data in C.scan_ark(ark, (magic,)):
        cnt += 1
        E = '<' if name == 'PC' else '>'
        try:
            ok += C.convert_sdri(C.convert_sdri(data, '>' if E == '<' else '<'), E) == data
        except ValueError as e:
            print('  %s .shaderinst key 0x%08x: %s' % (name, k, e))
    st['.shaderinst %s archive files round-trip ok (of %d)' % (name, cnt)] = ok

for k in sorted(st):
    print('%-68s %d' % (k, st[k]))
for mode in ('a', 'b'):
    print('IDs found in no other PC file (%s):' % ('base cars' if mode == 'a' else 'all PC files'),
          len(unique[mode]), ' '.join(sorted(unique[mode])) if verbose else '')
