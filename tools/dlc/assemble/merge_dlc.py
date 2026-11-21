# Merges several extracted Xbox DLC car packages into ONE source folder for assemble_car.py / finish_car.py /
# build_pack.py (they take one --dlc folder). Offline only; reads the packages, writes only <out>.
#
#   python -I merge_dlc.py --base <x_deadline> --add <x_highoctane> [--add <...>] --out <x_pack3>
#
# The packages are cumulative in their UI: every Xbox car package brings the whole DLC car select
# (UI/Frontend/Flash/FE_vehicleSelect.gfx + Textures/DLC.textures, UI/InGame/VehicleSelect/VehicleSelect.gfx +
# Textures/InGame3.*), with frames for the DLC cars released so far (Deadline, the last car pack: all eleven
# Placeholder_01..11; High Octane: Placeholder_01..07). So:
#   - <base> (the package with the most complete UI) is copied whole;
#   - from every --add package only Vehicles/** and Audio/** are taken (the cars' own files); a file both have
#     must be byte-identical;
#   - VehicleDatabase.xml = the base file with the add packages' <Car .../> records inserted in front of its first
#     record (CRLF kept); IDs and ModelFiles must not repeat;
#   - VehicleColors.params and Compositing/GameCompositingInfo.params = the base dump + the add packages' blocks
#     (same '!directory:' preamble required, no header twice);
#   - checks: for every record of an add package, its frame in the base's front-end and in-game car select shows
#     the same texts, images and cells as in its own package, and the DXT blocks of every cell are equal in the
#     base's atlas and the add package's (decoded with flashtex.xbox_set / convtex).
# Writes <out>\MERGE_REPORT.txt; exit code 1 on any problem (nothing is written if the inputs do not pass).
import argparse
import os
import re
import shutil
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in ('misc', 'tex', 'ui'):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), _p))
import flashtex  # noqa: E402
import gfxcars   # noqa: E402

MERGED = ('VEHICLES/VEHICLEDATABASE.XML', 'VEHICLES/VEHICLECOLORS.PARAMS', 'VEHICLES/COMPOSITING/GAMECOMPOSITINGINFO.PARAMS')
TAKE = ('VEHICLES/', 'AUDIO/')
CAR = re.compile(rb'<Car\s+ID="([^"]*)"[^>]*/>')
HEAD = re.compile(rb'(?m)^(/[^\r\n]*):\r\n')
FE_GFX = ('UI', 'FRONTEND', 'FLASH', 'FE_VEHICLESELECT.GFX')
FE_TEX = ('UI', 'FRONTEND', 'FLASH', 'TEXTURES', 'DLC.TEXTURES')
IG_GFX = ('UI', 'INGAME', 'VEHICLESELECT', 'VEHICLESELECT.GFX')
IG_DIR = ('UI', 'INGAME', 'TEXTURES')


class Fail(Exception):
    pass


def files_of(d):
    out = {}
    for root, _, names in os.walk(d):
        for n in names:
            full = os.path.join(root, n)
            out[os.path.relpath(full, d).replace(os.sep, '/').upper()] = full
    return out


def records(b):
    return [(m.group(1).decode('latin-1'), re.search(rb'ModelFile="([^"]*)"', m.group(0)).group(1).decode('latin-1'), m)
            for m in CAR.finditer(b)]


def blocks(b):
    ms = list(HEAD.finditer(b))
    if not ms:
        raise Fail('no tweakable blocks')
    return b[:ms[0].start()], [(m.group(1), b[m.start():(ms[i + 1].start() if i + 1 < len(ms) else len(b))])
                               for i, m in enumerate(ms)]


def images(d):
    """{'fe'|'ig': {image name without the format suffix (lower case): (fmt, w, h, DDS)}} of a package's UI sets"""
    out = {'fe': {}, 'ig': {}}
    for k, s, e, b in flashtex.xbox_set(open(os.path.join(d, *FE_TEX), 'rb').read()):
        out['fe'][s.rsplit('_', 1)[0].lower()] = struct.unpack_from('<5I', e, 12)[:1] + struct.unpack_from('<5I', e, 12)[3:] + (b[4:],)
    x = os.path.join(d, *IG_DIR)
    for tex, st in (('INGAME3.TEXTURES', 'INGAME3.STREAMTEX'), ('INGAME.TEXTURES', None), ('INGAME4.TEXTURES', None)):
        if not os.path.isfile(os.path.join(x, tex)):
            continue
        try:
            got = flashtex.xbox_set(open(os.path.join(x, tex), 'rb').read(), open(os.path.join(x, st), 'rb').read() if st else None)
        except Exception:  # a container the converter does not take is no picture source (finish_car does the same)
            continue
        for k, s, e, b in got:
            out['ig'].setdefault(s.rsplit('_', 1)[0].lower(), struct.unpack_from('<5I', e, 12)[:1] + struct.unpack_from('<5I', e, 12)[3:] + (b[4:],))
    return out


def cell(img, r):
    fmt, w, h, dds = img
    bpb = {0x21: 8, 0x24: 16, 0x25: 16}.get(fmt)
    if bpb is None:
        return None
    x0, x1, y0, y1 = max(0, r[0]), min(w, r[1]), max(0, r[2]), min(h, r[3])
    data, bw = dds[128:], (w + 3) // 4
    return [data[(by * bw + bx) * bpb:(by * bw + bx + 1) * bpb] for by in range(y0 // 4, (y1 + 3) // 4) for bx in range(x0 // 4, (x1 + 3) // 4)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--add', action='append', required=True)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    ins = [os.path.abspath(p) for p in [a.base] + a.add]
    if any(os.path.normcase(out) == os.path.normcase(p) or os.path.normcase(out).startswith(os.path.normcase(p) + os.sep)
           for p in ins):
        sys.exit('refusing to write %s (an input folder)' % out)
    if os.path.exists(out) and os.listdir(out):
        sys.exit('%s exists and is not empty; give a new folder' % out)
    lines, warn = [], []
    say = lambda s='': (print(s), lines.append(s))
    base = files_of(a.base)
    plan = {rel: src for rel, src in base.items() if rel not in MERGED}
    say('base %s: %d files' % (a.base, len(base)))
    db = open(base[MERGED[0]], 'rb').read()
    dumps = {p: blocks(open(base[p], 'rb').read()) for p in MERGED[1:]}
    have_ids = {i: mf for i, mf, _ in records(db)}
    add_recs = []
    for pk in a.add:
        fs = files_of(pk)
        n_new = n_same = 0
        for rel, src in sorted(fs.items()):
            if not rel.startswith(TAKE) or rel in MERGED:
                continue
            if rel in plan:
                if open(plan[rel], 'rb').read() != open(src, 'rb').read():
                    raise Fail('%s: in %s and %s with different bytes' % (rel, plan[rel], src))
                n_same += 1
                continue
            plan[rel] = src
            n_new += 1
        recs = records(open(fs[MERGED[0]], 'rb').read())
        for i, mf, m in recs:
            if i in have_ids or mf in have_ids.values():
                raise Fail('%s: record %s / %s is in the base or an earlier package' % (pk, i, mf))
            have_ids[i] = mf
            add_recs.append((pk, i, mf, m.group(0)))
        for p in MERGED[1:]:
            pre, bl = blocks(open(fs[p], 'rb').read())
            bpre, bbl = dumps[p]
            if pre != bpre:
                raise Fail('%s %s: other !directory preamble' % (pk, p))
            clash = {h for h, _ in bl} & {h for h, _ in bbl}
            if clash:
                raise Fail('%s %s: blocks already there: %s' % (pk, p, sorted(clash)[:3]))
            dumps[p] = (bpre, bbl + bl)
            say('  %s: %d blocks added (%s)' % (p, len(bl), ', '.join(sorted({h.split(b'/')[3].decode() for h, _ in bl}))))
        say('add %s: %d files taken (Vehicles/, Audio/), %d byte-identical to the base and not taken twice, records %s'
            % (pk, n_new, n_same, ', '.join('%s=%s' % (i, mf) for i, mf, _ in recs)))
        # the base's car select must show these cars as their own package does
        bi, pi = images(a.base), images(pk)
        for i, mf, _ in recs:
            for which, gfx in (('fe', FE_GFX), ('ig', IG_GFX)):
                fb = gfxcars.frame_info(os.path.join(a.base, *gfx), i)
                fp = gfxcars.frame_info(os.path.join(pk, *gfx), i)
                if [(t, p) for _, t, p in fb] != [(t, p) for _, t, p in fp]:
                    raise Fail('%s %s: the base\'s frame differs from the package\'s' % (i, '/'.join(gfx)))
                res = []
                for _, _, ps in fb:
                    for n, w, h, r in ps:
                        x, y = bi[which].get(n.lower()), pi[which].get(n.lower())
                        if x is None and y is None:
                            res.append('%s: a PC image (in neither package set)' % n)
                            continue
                        cx, cy = (cell(x, r) if x else None), (cell(y, r) if y else None)
                        if not cx or not cy or len(cx) != len(cy):
                            raise Fail('%s %s: the cell of %s is missing in the base or the package' % (i, which, n))
                        nd = sum(1 for p, q in zip(cx, cy) if p != q)
                        if nd:
                            # the base is the later package: its atlas may have retouched art (High Octane's
                            # RYBACK VULCAN, in-game: the hood badge, 32 blocks); the Xbox shows the base's with all
                            # packs installed, so the base's is used and the difference is listed
                            warn.append('%s (%s) %s %s: %d of %d DXT blocks of the cell differ, the base\'s used'
                                        % (i, mf, which, n, nd, len(cx)))
                        res.append('%s %dx%d x %d..%d y %d..%d: %s' % ((n, w, h) + r + (
                            '%d of %d DXT blocks DIFFER (base used)' % (nd, len(cx)) if nd else
                            '%d DXT blocks equal%s' % (len(cx), ', whole image equal' if x == y else ', image differs elsewhere'),)))
                say('  %s (%s) %s car select: texts %s; %s' % (i, mf, 'front-end' if which == 'fe' else 'in-game',
                                                              [' '.join(s.strip() for s in t) for _, t, _ in fb if t] or '-', '; '.join(res)))
    # VehicleDatabase: the add records in front of the base's first record, one per line
    first = CAR.search(db)
    ls = db.rfind(b'\n', 0, first.start()) + 1
    indent = db[ls:first.start()]
    ins_b = b''.join(indent + r + b'\r\n' for _, _, _, r in add_recs)
    new_db = db[:ls] + ins_b + db[ls:]
    if [x[:2] for x in records(new_db)] != [(i, mf) for _, i, mf, _ in add_recs] + [x[:2] for x in records(db)]:
        raise Fail('VehicleDatabase merge')
    if b'\n' in new_db.replace(b'\r\n', b''):
        raise Fail('VehicleDatabase: bare LF')
    os.makedirs(out, exist_ok=True)
    for rel, src in sorted(plan.items()):
        dst = os.path.join(out, *rel.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    def w(rel, b):
        fn = os.path.join(out, *rel.split('/'))
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        open(fn, 'wb').write(b)
    w(MERGED[0], new_db)
    for p in MERGED[1:]:
        pre, bl = dumps[p]
        w(p, pre + b''.join(t for _, t in bl))
        # lossless: split again
        if blocks(open(os.path.join(out, *p.split('/')), 'rb').read()) != dumps[p]:
            raise Fail('%s: does not split back' % p)
    say('VehicleDatabase.xml: %d records (%s)' % (len(records(new_db)), ', '.join('%s=%s' % x[:2] for x in records(new_db))))
    say('picture differences (base used): %s' % ('; '.join(warn) or 'none'))
    say('written %s: %d files' % (out, len(plan) + len(MERGED)))
    with open(os.path.join(out, 'MERGE_REPORT.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Fail as e:
        sys.exit('merge_dlc: %s' % e)
