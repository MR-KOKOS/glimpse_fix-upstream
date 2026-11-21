# Convert every Xbox base-car .textures file (cache made by fetchpairs.py) and compare with the PC original.
#   python -I test_convtex.py [cache_dir] [-v]
# Per file: container byte-identical?  Per texture: DDS header equal, per-mip data equal.
import os
import struct
import sys
sys.dont_write_bytecode = True
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convtex  # noqa: E402
from fetchpairs import DEFAULT_CACHE  # noqa: E402


def mip_sizes(dds):
    h, w = struct.unpack_from('<2I', dds, 12)
    n = struct.unpack_from('<I', dds, 28)[0] or 1
    fourcc = dds[84:88]
    bpb = 8 if fourcc == b'DXT1' else 16
    s = []
    for L in range(n):
        lw, lh = max(1, w >> L), max(1, h >> L)
        s.append(((lw + 3) // 4) * ((lh + 3) // 4) * bpb)
    return s


def check(args):
    cache, rel = args
    xb = open(os.path.join(cache, 'xb', rel), 'rb').read()
    pc = open(os.path.join(cache, 'pc', rel), 'rb').read()
    res = dict(rel=rel, same=False, tex=[], err=None)
    try:
        out = convtex.convert(xb)
    except convtex.FormatError as ex:
        res['err'] = str(ex)
        return res
    res['same'] = out == pc
    a = convtex.textures(out)
    b = convtex.textures(pc)
    if len(a) != len(b):
        res['err'] = 'texture count %d != %d' % (len(a), len(b))
        return res
    ca, cb = convtex.parse(out), convtex.parse(pc)
    for i, ((ka, da, _), (kb, db, _)) in enumerate(zip(a, b)):
        r = dict(i=i, key=ka, ent=ca['entries'][i] == cb['entries'][i], hdr=da[:128] == db[:128],
                 data=da[128:] == db[128:], badmips=[], info=da[12:20].hex() + ' ' + da[84:88].decode('latin-1'))
        if not r['ent']:
            r['ent_pc'] = cb['entries'][i].hex()
            r['ent_cv'] = ca['entries'][i].hex()
        if not r['hdr']:
            r['hdr_pc'] = db[:128].hex()
            r['hdr_cv'] = da[:128].hex()
        if not r['data'] and r['hdr']:
            o = 128
            for L, s in enumerate(mip_sizes(db)):
                if da[o:o + s] != db[o:o + s]:
                    nb = s // (8 if db[84:88] == b'DXT1' else 16)
                    bs = s // nb
                    diff = sum(1 for k in range(nb) if da[o + k * bs:o + k * bs + bs] != db[o + k * bs:o + k * bs + bs])
                    r['badmips'].append((L, diff, nb))
                o += s
        res['tex'].append(r)
    res['names'] = ca['names'] == cb['names']
    return res


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    verbose = '-v' in sys.argv
    cache = args[0] if args else DEFAULT_CACHE
    rels = []
    for root, dirs, fs in os.walk(os.path.join(cache, 'xb')):
        for f in fs:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, os.path.join(cache, 'xb'))
            if os.path.exists(os.path.join(cache, 'pc', rel)):
                rels.append(rel)
    rels.sort()
    nfile = nsame = ntex = ndata = nhdr = nent = 0
    with Pool(min(30, os.cpu_count() or 4)) as pool:
        for res in pool.imap(check, [(cache, r) for r in rels]):
            nfile += 1
            nsame += res['same']
            if res['err']:
                print('ERR ', res['rel'], res['err'])
                continue
            for t in res['tex']:
                ntex += 1
                ndata += t['data']
                nhdr += t['hdr']
                nent += t['ent']
                if not (t['data'] and t['hdr'] and t['ent']):
                    print('DIFF %s #%d %08x %s ent=%d hdr=%d data=%d %s' % (res['rel'], t['i'], t['key'], t['info'],
                                                                         t['ent'], t['hdr'], t['data'], t['badmips']))
                    if verbose and not t['ent']:
                        print('   pc', t['ent_pc'])
                        print('   cv', t['ent_cv'])
                    if verbose and not t['hdr']:
                        print('   pc', t['hdr_pc'])
                        print('   cv', t['hdr_cv'])
            if not res['same'] and verbose:
                print('     file differs:', res['rel'], 'names equal' if res.get('names') else 'NAMES DIFFER')
    print('files: %d, byte-identical containers: %d' % (nfile, nsame))
    print('textures: %d, pixel data equal: %d, DDS header equal: %d, entry equal: %d' % (ntex, ndata, nhdr, nent))


if __name__ == '__main__':
    main()
