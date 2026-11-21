# Merges of the text files the PC game reads once from Resident (see DESIGN.md), and the overlay builder.
#
#   directory.txt              "Resident|directory.txt", parsed at 0x787F60 (archive mode only, ctor 0x788610):
#                              blocks "-<pattern>" / ":<SECTION>" / ":<0|1 dir flag>" / entries, tokens split on
#                              space, tab, CR, LF, ',', ':', ';'. Sections TRACKS, <track>_SUBTRACK, VEHICLES,
#                              GAMEMODES, NEMESIS, ... (looked up by CRC of the name, 0x7878A0). The Xbox DLC
#                              DIRECTORY.TXT has the same format and only the additions.
#   Vehicles/VehicleDatabase.xml  "Resident(noassert)|%s" (0x78A290), <Car ID=...> records parsed by 0x7FACA0 into
#                              a 0x1BC-byte table; the DLC file holds only replacements of Placeholder_NN records
#                              (IDs kept, so the table indices and the profile's per-vehicle data stay put).
#
# Usage:
#   python -I dlcmerge.py directory <pc directory.txt> <dlc DIRECTORY.TXT>... -o <out>
#   python -I dlcmerge.py vehicles <pc VehicleDatabase.xml> <dlc VEHICLEDATABASE.XML>... -o <out>
#   python -I dlcmerge.py overlay <folder> -o <out.ark>     (every file below folder, path relative to it)
import os
import re
import sys

import arkfmt

_SEP = re.compile(r'[ \t\r\n,:;]+')


def _split_blocks(text):
    """[(pattern, section, flag, [entries])] in file order. A block starts with '-' at the start of a
    line (the game treats any '-' as a block start; no entry name contains one)."""
    out, cur = [], None
    for line in text.replace('\r\n', '\n').split('\n'):
        if line.startswith('-'):
            cur = [line[1:].strip()]
            out.append(cur)
        elif cur is not None:
            cur.extend(t for t in _SEP.split(line) if t)
    return [(b[0], b[1], b[2], b[3:]) for b in out if len(b) >= 3]


def merge_directory(pc_text, dlc_texts):
    blocks = _split_blocks(pc_text)
    order = [b[1] for b in blocks]
    by_sec = {b[1]: [b[0], b[2], list(b[3])] for b in blocks}
    for t in dlc_texts:
        for pat, sec, flag, ents in _split_blocks(t):
            if sec not in by_sec:
                by_sec[sec] = [pat, flag, []]
                order.append(sec)
            have = {e.lower() for e in by_sec[sec][2]}
            for e in ents:
                if e.lower() not in have:
                    by_sec[sec][2].append(e)
                    have.add(e.lower())
    # The Onslaught DIRECTORY.TXT lists "Quarry" and "A" under TRACKS. Every PC track has a
    # <track>_SUBTRACK section; a TRACKS entry without one is dropped (quick play, 0x772FF0, would list a
    # track without routes).
    if 'TRACKS' in by_sec:
        subs = {s.lower() for s in by_sec}
        by_sec['TRACKS'][2] = [e for e in by_sec['TRACKS'][2] if (e + '_SUBTRACK').lower() in subs]
    lines = []
    for sec in order:
        pat, flag, ents = by_sec[sec]
        lines.append('-' + pat)
        lines.append('\t:' + sec)
        lines.append('\t:' + flag)
        lines.extend('\t' + e for e in ents)
    return '\r\n'.join(lines) + '\r\n'


_CAR = re.compile(r'<Car\s+ID="([^"]*)"[^>]*/>')


def merge_vehicles(pc_text, dlc_texts):
    """Replaces <Car ID="X" .../> records by the DLC's records with the same ID; IDs the PC file lacks
    are appended before </Root> (new table rows: see DESIGN.md, prefer the Placeholder slots)."""
    repl, extra = {}, []
    pc_ids = {m.group(1) for m in _CAR.finditer(pc_text)}
    for t in dlc_texts:
        for m in _CAR.finditer(t):
            repl[m.group(1)] = m.group(0)
            if m.group(1) not in pc_ids and m.group(1) not in extra:
                extra.append(m.group(1))
    out = _CAR.sub(lambda m: repl.get(m.group(1), m.group(0)), pc_text)
    if extra:
        add = ''.join(' %s\r\n' % repl[i] for i in extra)
        k = out.rfind('</Root>')
        out = out[:k] + add + out[k:]
    return out


def overlay_from_folder(folder):
    files = {}
    for dp, dn, fns in os.walk(folder):
        for n in fns:
            full = os.path.join(dp, n)
            rel = os.path.relpath(full, folder).replace('\\', '/')
            with open(full, 'rb') as f:
                files[rel] = f.read()
    return files


def _read(fn):
    with open(fn, 'rb') as f:
        return f.read().decode('latin-1')


def main(argv):
    if len(argv) < 3 or '-o' not in argv:
        print(__doc__ if __doc__ else 'see the header of dlcmerge.py')
        return 2
    o = argv.index('-o')
    out, args, cmd = argv[o + 1], argv[1:o], argv[0]
    if cmd == 'directory':
        res = merge_directory(_read(args[0]), [_read(a) for a in args[1:]]).encode('latin-1')
    elif cmd == 'vehicles':
        res = merge_vehicles(_read(args[0]), [_read(a) for a in args[1:]]).encode('latin-1')
    elif cmd == 'overlay':
        arkfmt.write_ark(out, overlay_from_folder(args[0]))
        print('wrote', out)
        return 0
    else:
        print('unknown command', cmd)
        return 2
    with open(out, 'wb') as f:
        f.write(res)
    print('wrote', out, len(res))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
