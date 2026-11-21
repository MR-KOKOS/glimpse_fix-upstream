# Deadline pickup files (.pkps, Xbox DLC TimeTrialPlus): parser and checks. Offline only.
#
# Layout (XML, latin-1, LF line ends; the Xbox loads it at race setup through 0x882AA4E0, see TU4_RULES.md section 2):
#   <Root FileVersion="1.0000000">
#      <Pickup pickup_colour="green" node_id="813486647" group_node_id="1841683827">
#         <transform>
#            <Row0 X=".." Y=".." Z=".."/>   rotation row 0 (the box's x axis in world space)
#            <Row1 .../>                    row 1 (y axis, up in every file)
#            <Row2 .../>                    row 2 (z axis)
#            <Row3 .../>                    translation: the box's centre (the collision box is centred on it)
#         </transform>
#      </Pickup> ...
#   </Root>
# Row-vector convention as everywhere in the game: world = local * [Row0; Row1; Row2] + Row3.
# Colours: green, yellow, red, white, black (lookup 0x882A8858; anything else is "none"). The 11 DLC files have
# 496 pickups, all green, one group per file.
#
# The DLL (src/dlcpickups.cpp, [DLC] Pickups=1) has its own reader of the same format; parse() here is the
# reference it was checked against (python -I pkps.py <file.pkps | RESIDENT_DLC.ARK> [...]).
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True

COLOURS = ('green', 'yellow', 'red', 'white', 'black')
# the 11 Deadline tracks (level, subtrack) and where the game looks for their file (Xbox 0x882AA4E0; the
# PC overlay keeps the same path, deadline_mode.py)
TRACKS = (('airport_test_03', 'A'), ('docks', 'A'), ('docks', 'B'), ('docks', 'C'), ('downtown', 'A'),
          ('downtown', 'B'), ('downtown', 'C'), ('graveyard', 'A'), ('graveyard', 'B'), ('powerplant', 'A'),
          ('powerplant', 'B'))
PATH = 'Environments/Levels/%s/Subtracks/%s/Pickups/%s.pkps'


class PkpsError(Exception):
    pass


def path(level, sub):
    return PATH % (level, sub, level)


def parse(data):
    """bytes -> list of dicts {colour, node, group, rows: [[x,y,z] * 4]}"""
    try:
        root = ET.fromstring(data.decode('latin-1'))
    except ET.ParseError as e:
        raise PkpsError('not well-formed: %s' % e)
    if root.tag != 'Root':
        raise PkpsError('root element %r' % root.tag)
    out = []
    for p in root:
        if p.tag != 'Pickup':
            raise PkpsError('element %r under Root' % p.tag)
        t = p.find('transform')
        if t is None:
            raise PkpsError('pickup without transform')
        rows = []
        for i in range(4):
            r = t.find('Row%d' % i)
            if r is None:
                raise PkpsError('transform without Row%d' % i)
            rows.append([float(r.get(c)) for c in 'XYZ'])
        out.append({'colour': p.get('pickup_colour', ''), 'node': int(p.get('node_id', '0')),
                    'group': int(p.get('group_node_id', '0')), 'rows': rows})
    return out


def check(pk, name='pkps'):
    """the properties the DLL relies on; raises PkpsError"""
    for i, p in enumerate(pk):
        if p['colour'] not in COLOURS:
            raise PkpsError('%s pickup %d: colour %r' % (name, i, p['colour']))
        r = p['rows']
        for k in range(3):
            n = sum(c * c for c in r[k]) ** 0.5
            if abs(n - 1.0) > 1e-3:
                raise PkpsError('%s pickup %d: row %d length %.4f (not a rotation)' % (name, i, k, n))
        for v in r[3]:
            if not -20000.0 < v < 20000.0:
                raise PkpsError('%s pickup %d: position %r' % (name, i, r[3]))


def from_dlc_ark(ark_path):
    """{(level, sub): bytes} for the 11 tracks from the DLC's RESIDENT_DLC.ARK"""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'misc'))
    from arkio import Ark, key
    a = Ark(ark_path)
    try:
        out = {}
        for t in TRACKS:
            d = a.get(key(path(*t)))
            if d is None:
                raise PkpsError('%s: %s missing' % (ark_path, path(*t)))
            out[t] = d
        return out
    finally:
        a.close()


def summary(pk):
    cols = {}
    for p in pk:
        cols[p['colour']] = cols.get(p['colour'], 0) + 1
    groups = len(set(p['group'] for p in pk))
    up = min(p['rows'][1][1] for p in pk) if pk else 0
    return '%d pickups (%s), %d group(s), min up.y %.4f' % (
        len(pk), ', '.join('%s %d' % kv for kv in sorted(cols.items())), groups, up)


def main(argv):
    total = 0
    for f in argv:
        if f.lower().endswith('.ark'):
            for t, d in from_dlc_ark(f).items():
                pk = parse(d)
                check(pk, path(*t))
                total += len(pk)
                print('%-18s %s: %s' % ('%s %s' % t, path(*t), summary(pk)))
        else:
            pk = parse(open(f, 'rb').read())
            check(pk, f)
            total += len(pk)
            print('%s: %s' % (f, summary(pk)))
    print('total %d pickups' % total)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except PkpsError as e:
        sys.exit('ERROR: %s' % e)
