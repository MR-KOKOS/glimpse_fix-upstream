# Checks that a larger car pack keeps an earlier pack unchanged (offline, read only).
#
#   python -I compare_packs.py <old pack.ark> <new pack.ark> [--pc <PC resident.ark>]
#
# - every entry of the old pack that is not one of build_pack.SHARED is byte-identical in the new pack;
# - VehicleDatabase.xml: every record the old pack changed is byte-identical in the new one; the records the new pack
#   changes on top replace PC records (Placeholder slots) only;
# - VehicleColors.params / GameCompositingInfo.params: the new file minus the blocks of the added cars = the old file;
# - directory.txt: the new file minus the added cars' VEHICLES lines = the old file;
# - dictionaries: every old entry unchanged, 2 more per added car;
# - FE_vehicleSelect.gfx / VehicleSelect.gfx: every character definition of the old file is in the new one unchanged
#   (car sprites aside), and each car sprite's old frames are the first frames of the new one, unchanged;
# - Flash.textures / InGame.textures (+ .filenames): every old texture unchanged (entry, data, key), old names first.
# Exit code 1 on any difference.
import argparse
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_pack as bp  # noqa: E402  (SHARED, LANGS, DUMPS, CAR_RE, dump_blocks; its imports set the paths)

arkio, gfxcars, dictadd, convtex, flashtex = bp.arkio, bp.gfxcars, bp.dictadd, bp.convtex, bp.flashtex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('old')
    ap.add_argument('new')
    game_dir = os.environ.get('GAME_DIR', '')  # the default: below the game folder in GAME_DIR
    ap.add_argument('--pc', default=os.path.join(game_dir, 'data', 'resident.ark') if game_dir else None,
                    required=not game_dir, help='the PC data\\resident.ark (default: below GAME_DIR, the game folder)')
    a = ap.parse_args()
    C = bp.Check()
    say, ok = C.say, C.ok
    K = arkio.key
    old, new, pc = arkio.Ark(a.old), arkio.Ark(a.new), arkio.Ark(a.pc)
    shared = {K(p): p for p in bp.SHARED}
    say('%s (%d entries) against %s (%d entries)' % (os.path.basename(a.old), len(old.order), os.path.basename(a.new), len(new.order)))
    miss = [k for k in old.order if k not in new.ents]
    ok(not miss, 'every key of the old pack is in the new one (%d missing)' % len(miss))
    own = [k for k in old.order if k not in shared]
    diff = [k for k in own if new.get(k) != old.get(k)]
    ok(not diff, '%d entries that are not shared files: byte-identical (%d differ)' % (len(own), len(diff)))
    ok(set(shared) & set(old.order) == set(shared) & set(new.order), 'the same shared files in both (%d)' % len(set(shared) & set(old.order)))

    g = lambda ark, p: ark.get(K(p))
    ov, nv, pv = (g(x, 'vehicles/vehicledatabase.xml').decode('latin-1') for x in (old, new, pc))
    orec, nrec, prec = (list(bp.CAR_RE.finditer(x)) for x in (ov, nv, pv))
    oc = [i for i in range(len(prec)) if orec[i].group(0) != prec[i].group(0)]
    nc = [i for i in range(len(prec)) if nrec[i].group(0) != prec[i].group(0)]
    added = [nrec[i].group(1) for i in nc if i not in oc]
    ok(len(orec) == len(nrec) == len(prec) and all(nrec[i].group(0) == orec[i].group(0) for i in oc) and set(oc) <= set(nc)
       and all(prec[i].group(1).startswith('Placeholder_') for i in nc),
       'VehicleDatabase.xml: the old pack\'s %d records unchanged (%s); added in Placeholder slots: %s' % (
           len(oc), ', '.join(orec[i].group(1) for i in oc), ', '.join(added)))
    for p, root in bp.DUMPS:
        opre, obl = bp.dump_blocks(g(old, p))
        npre, nbl = bp.dump_blocks(g(new, p))
        mine = lambda h: any(h == root + '/' + m or h.startswith(root + '/' + m + '/') for m in added)
        ok(npre == opre and [x for x in nbl if not mine(x[0])] == obl,
           '%s: equal to the old file once the %d blocks of the added cars are taken out' % (p, len([x for x in nbl if mine(x[0])])))
    od, nd = g(old, 'directory.txt').decode('latin-1'), g(new, 'directory.txt').decode('latin-1')
    ok(nd.replace(''.join('\t%s\r\n' % m for m in added), '', 1) == od, 'directory.txt: equal to the old file once the added '
                                                                       'cars\' VEHICLES lines are taken out')
    for lang in bp.LANGS:
        p = 'dictionary/%s_win32.dct' % lang.lower()
        oa, na = dictadd.Dict(g(old, p)).all(), dictadd.Dict(g(new, p)).all()
        ok(all(na.get(h) == v for h, v in oa.items()) and len(na) == len(oa) + 2 * len(added),
           '%s: every old entry unchanged, %d more' % (os.path.basename(p), 2 * len(added)))
    with tempfile.TemporaryDirectory(prefix='compare_packs_') as td:
        for p in ('ui/frontend/flash/fe_vehicleselect.gfx', 'ui/ingame/vehicleselect/vehicleselect.gfx'):
            f1, f2 = os.path.join(td, 'o.gfx'), os.path.join(td, 'n.gfx')
            open(f1, 'wb').write(g(old, p))
            open(f2, 'wb').write(g(new, p))
            _, _, ot = gfxcars.load(f1)
            _, _, nt = gfxcars.load(f2)
            osp, nsp = gfxcars.car_sprites(ot), gfxcars.car_sprites(nt)
            old_cars = [orec[i].group(1) for i in oc]
            same_fr, same_info = len(osp) == len(nsp), True
            for oi, ni in zip(osp, nsp):
                of = gfxcars.frames_of(gfxcars.read_tags(ot[oi][1], 4, len(ot[oi][1])))
                nf = gfxcars.frames_of(gfxcars.read_tags(nt[ni][1], 4, len(nt[ni][1])))
                # the PC frames byte for byte; the old pack's car frames place copied characters, whose ids gfxcars
                # numbers anew when more cars are copied, so those are compared by what they show (frame_info)
                same_fr &= len(nf) == len(of) + len(added) and [f[0] for f in nf] == [f[0] for f in of] + added and \
                    all(nf[i] == of[i] for i in range(len(of)) if of[i][0] not in old_cars)
            for car in old_cars:
                fo, fn = gfxcars.frame_info(f1, car), gfxcars.frame_info(f2, car)
                same_info &= [(t, ps) for _, t, ps in fo] == [(t, ps) for _, t, ps in fn]
            ok(same_fr and same_info, '%s: the PC frames byte-identical, the old cars\' frames (%s) show the same texts, images '
                                      'and cells, then one frame per added car' % (p, ', '.join(old_cars)))
    for base in ('ui/frontend/flash/textures/flash', 'ui/ingame/textures/ingame'):
        oc_, nc_ = convtex.parse(g(old, base + '.textures')), convtex.parse(g(new, base + '.textures'))
        on, nn = flashtex.names(oc_), flashtex.names(nc_)
        nidx = {s: i for i, (_, s) in enumerate(nn)}
        keep = all(s in nidx and nc_['entries'][nidx[s]] == oc_['entries'][i] and nc_['blobs'][nidx[s]] == oc_['blobs'][i]
                   and nn[nidx[s]][0] == k for i, (k, s) in enumerate(on))
        ofl = [s for s in g(old, base + '.filenames').split(b'\x00') if s]
        nfl = [s for s in g(new, base + '.filenames').split(b'\x00') if s]
        ok(keep and nfl[:len(ofl)] == ofl, '%s.textures: the old %d textures unchanged (entry, data, key), added: %s; .filenames: '
                                           'old names first' % (base, len(on), ', '.join(s for _, s in nn if s not in {x for _, x in on}) or 'none'))
    say('RESULT: %s' % ('OK' if not C.errors else 'ERRORS: %s' % C.errors))
    return 0 if not C.errors else 1


if __name__ == '__main__':
    sys.exit(main())
