# Survival Race (Xbox DLC "Survival At The Rock" game mode "NemesisRace") as data for the PC game. Offline only.
#
#   python -B -I -X utf8 nemesis_mode.py --base <overlay .ark> --dlc <Rock package dir> --out <new .ark>
#       [--data-out <folder>] [--game <PC data dir>] [--no-movie]
#
# Takes an overlay archive (e.g. the combined car pack + Quarry + nem_quarry archive of
# ../assemble/assemble_track.py) and writes a new one with the mode added; every other entry is copied unchanged.
# --data-out: the mode's quick play movie (FE_Survival_Race.bik, copied as is) goes to <folder>\movies\screens\
# quickplay\fe_survival_race.bik (a folder the DLL's redirect serves as data\, e.g. glimpse_dlc\nem_quarry\data).
#   apply(files, pc_ark, dlc_dir) -> (files, info)   check(before, after, pc_ark, dlc_dir, info) -> [(ok, text)]
#
# What the mode is (Rock package, RESIDENT_DLC.ARK, compared with the PC files):
# - GameLogic/GameModes/NemesisRace.params: a 2-player race (Players 'Max players' 2, 5 laps, End Condition Laps,
#   ranking by distance raced then race time) on the nem_* tracks with the nemesis trucks of
#   GameLogic/Nemesis/TruckSurvivalRace.params (/RaceSetup/Nemesis Selection0..7), a countdown of 20 s that each
#   truck overtake raises by 5 s ('Truck Overtake' mechanic, which the PC has: NemesisMulti has it disabled).
#   Type 'Survival Race' is the TU4 enum value 6 (0 Race .. 5 Air Revenge, 6 Survival Race, 7 King of the Air,
#   8 Time Trial Plus, 9 Other: TU4_RULES.md); the PC enum (string table 0xD36A68) ends at Air Revenge, Other. The
#   PC's nemesis modes Nemesis / NemesisMulti / NemesisSplitScreen are Type 'Survival', so 'Survival' is used here
#   (the PC's truck spawning, respot and HUD code for nemesis modes). Every other line stays as on the Xbox: keys
#   the PC never registers are dropped by its params parser (0xABC460 -> 0x41D630, see README.md).
# - GameModeHUDs/NemesisRace.params, GameLogic/Nemesis/TruckSurvivalRace.params: as on the Xbox.
# - directory.txt: NemesisRace.params in GAMEMODES, TruckSurvivalRace.params in NEMESIS (sorted, as the PC lists).
# - UI/Logic/TrackFiltration.xml: the DLC lists the mode for the quick play screen online and split screen
#   (PlayModes MultiPlayer, SplitScreen; no SinglePlayer entry: 'Max players' 2). Here it goes into SplitScreen only:
#   online the PC's sessions take up to 8 players (the mode file says 2) and every peer's mode list must be the same,
#   which a player without the pack would not have. Its GameMode element with the five tracks (nem_quarry only when
#   the base lists it in directory.txt TRACKS). DLCPack / DLCGameModeIndex are left out (no PC reader). Split screen
#   quick play modes are never locked (0x77A9F0: two players flag 2), so no unlock is needed.
# - Texts TEXT_GAMEMODE_NAME_NemesisRace, TEXT_GAMEMODE_NemesisRace_HELP_LINE1..5 (none in the package): the official
#   ones from Title Update 4's table when a developer's table is set (tu4text.py, GLIMPSE_TU4_TEXTS), else TEXTS.
# Not converted: the TU4 UI files of the package (UI/InGame EventObjectives.gfx, IG_GameModeInstructions.gfx,
# RaceResults.gfx, InGame / InGame4 textures: the Xbox TU4 code's Survival Race screens; the PC files stay) and
# UI/Network/Leaderboard_Def.xml (Xbox leaderboard ids).
import argparse
import os
import re
import shutil
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(HERE, '..', 'misc'), os.path.join(HERE, '..', 'ui'), os.path.join(HERE, '..', 'inject')):
    sys.path.insert(0, _p)
from arkio import Ark, key, write_ark  # noqa: E402
import dictadd  # noqa: E402

# a manual run's game data folder: below the game folder in GAME_DIR, else --game must be given (build_all.py gives it)
DEF_GAME = os.path.join(os.environ['GAME_DIR'], 'data') if os.environ.get('GAME_DIR') else None
NAME = 'NemesisRace'
P_MODE = 'GameLogic/GameModes/NemesisRace.params'
P_HUD = 'GameModeHUDs/NemesisRace.params'
P_TRUCK = 'GameLogic/Nemesis/TruckSurvivalRace.params'
P_DIR = 'directory.txt'
P_TF = 'UI/Logic/TrackFiltration.xml'
DLC_MOVIE = 'MOVIES/SCREENS/QUICKPLAY/FE_SURVIVAL_RACE.BIK'
MOVIE_REL = 'movies/screens/quickplay/fe_survival_race.bik'
TYPE_XB, TYPE_PC = "Type = 'Survival Race';", 'Type = Survival;'
LANGS = ('ENGLISH_NTSC', 'GERMAN', 'FRENCH_NTSC', 'ITALIAN', 'SPANISH_NTSC')
# no official texts are known; the PC's Survival texts are the model (TEXT_GAMEMODE_Nemesis_HELP_LINE1..4)
TEXTS = {
    'ENGLISH_NTSC': ('SURVIVAL RACE', (
        'RACE YOUR RIVAL WHILE THE SPLIT/SECOND RIGS DROP EXPLOSIVE BARRELS.',
        'PASS RIGS TO ADD BONUS SECONDS TO YOUR TIMER.',
        'FINISH THE LAPS AHEAD OF YOUR RIVAL TO WIN!')),
    'GERMAN': ('ÜBERLEBENSRENNEN', (
        'FAHRE GEGEN DEINEN RIVALEN, WÄHREND DIE TRUCKS EXPLOSIVE FÄSSER ABWERFEN.',
        'ÜBERHOLE TRUCKS FÜR BONUSSEKUNDEN AUF DEINEM TIMER.',
        'BEENDE DIE RUNDEN VOR DEINEM RIVALEN, UM ZU GEWINNEN!')),
    'FRENCH_NTSC': ('COURSE DE SURVIE', (
        'AFFRONTE TON RIVAL PENDANT QUE LES CAMIONS LARGUENT DES BARILS EXPLOSIFS.',
        'DÉPASSE LES CAMIONS POUR GAGNER DES SECONDES BONUS.',
        'TERMINE LES TOURS AVANT TON RIVAL POUR GAGNER !')),
    'ITALIAN': ('GARA DI SOPRAVVIVENZA', (
        'SFIDA IL TUO RIVALE MENTRE I TIR SGANCIANO BARILI ESPLOSIVI.',
        'SORPASSA I TIR PER AGGIUNGERE SECONDI BONUS AL TIMER.',
        'COMPLETA I GIRI PRIMA DEL TUO RIVALE PER VINCERE!')),
    'SPANISH_NTSC': ('CARRERA DE SUPERVIVENCIA', (
        'CORRE CONTRA TU RIVAL MIENTRAS LOS CAMIONES SUELTAN BARRILES EXPLOSIVOS.',
        'ADELANTA CAMIONES PARA SUMAR SEGUNDOS A TU TIEMPO.',
        '¡TERMINA LAS VUELTAS ANTES QUE TU RIVAL PARA GANAR!')),
}
K_NAME = 'TEXT_GAMEMODE_NAME_%s' % NAME
K_HELP = 'TEXT_GAMEMODE_%s_HELP_LINE%%d' % NAME
# the official texts: Title Update 4's built-in table (tu4text.py, only with a developer's table, GLIMPSE_TU4_TEXTS):
# the name and help lines 1..5 (an empty line -> 'BLANK', the PC's empty line). Without it: TEXTS above.
TU4_NAME = 'TEXT_GAMEMODE_NAME_NEMESISRACE'
TU4_HELP = 'TEXT_GAMEMODE_nemesisrace_HELP_LINE%d'
sys.path.insert(0, HERE)
import tu4text  # noqa: E402  (this folder)


def official_texts():
    """{lang: [text of K_NAME, HELP_LINE1..5]} from the TU4 table, or None (no table, or a text missing in it)"""
    t = tu4text.load()
    if t is None:
        return None
    xk = [TU4_NAME] + [TU4_HELP % i for i in range(1, 6)]
    if any(t.get(k, l) is None for k in xk for l in LANGS) or not all(t.get(TU4_NAME, l) for l in LANGS):
        return None
    return {l: [t.get(k, l) or 'BLANK' for k in xk] for l in LANGS}


def text_source():
    t = tu4text.load()
    if t is None:
        return 'generated texts (no TU4 text table, %s not set)' % tu4text.ENV
    return ('TU4 text table %s' % t.describe()) if official_texts() is not None else \
        'generated texts (the TU4 table %s lacks a Survival Race text)' % t.path


class ModeError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise ModeError(msg)


def find_ci(root, rel):
    cur = root
    for part in rel.replace('\\', '/').split('/'):
        if not os.path.isdir(cur):
            return None
        names = {n.lower(): n for n in os.listdir(cur)}
        if part.lower() not in names:
            return None
        cur = os.path.join(cur, names[part.lower()])
    return cur


def get(files, pc, p):
    k = key(p)
    d = files[k] if k in files else pc.get(k)
    need(d is not None, 'no %s in the base or the PC resident.ark' % p)
    return d


def section(text, pattern, what):
    """(start, end, [names]) of a directory.txt list: the tab lines after the section head"""
    m = re.search(pattern, text)
    need(m, 'directory.txt: no %s section' % what)
    pos, names = m.end(), []
    while pos < len(text) and text[pos] == '\t':
        e = text.index('\r\n', pos)
        names.append(text[pos + 1:e])
        pos = e + 2
    return m.end(), pos, names


def insert_sorted(text, pattern, what, entry):
    start, end, names = section(text, pattern, what)
    need(entry.lower() not in [n.strip().lower() for n in names], 'directory.txt already lists %s' % entry)
    pos = start
    for n in names:
        if n.strip().lower() > entry.lower():
            break
        pos = text.index('\r\n', pos) + 2
    line = '\t%s\r\n' % entry
    return text[:pos] + line + text[pos:], (pos, line)


RE_GAMEMODES = r'-GameLogic/GameModes/\*\.params\r\n\t:GAMEMODES\r\n\t:0\r\n'
RE_NEMESIS = r'-GameLogic/Nemesis/\*\.params\r\n\t:NEMESIS\r\n\t:0\r\n'
RE_TRACKS = r'-Environments/Levels/\*\r\n\t:TRACKS\r\n\t:1\r\n'


def build_trackfiltration(pc_tf, dlc_tf, tracks):
    """PlayableGameMode NemesisRace after the Nemesis line of PlayMode SplitScreen (not MultiPlayer); the GameMode
    element (without the DLC-only attributes) before </GameModes>"""
    t = pc_tf.decode('latin-1')
    need('Name="%s"' % NAME not in t, 'TrackFiltration.xml already has %s' % NAME)
    lines = t.split('\r\n')
    ins = []
    for pm in ('SplitScreen',):
        s = next(i for i, x in enumerate(lines) if '<PlayMode Name="%s">' % pm in x)
        e = next(i for i in range(s, len(lines)) if '</PlayMode>' in lines[i])
        nem = [i for i in range(s, e) if re.search(r'<PlayableGameMode Name="Nemesis"\s*/>', lines[i])]
        need(len(nem) == 1, 'PC TrackFiltration %s: %d Nemesis lines' % (pm, len(nem)))
        ind = re.match(r'[ \t]*', lines[nem[0]]).group(0)
        lines.insert(nem[0] + 1, '%s<PlayableGameMode Name="%s"/> <!-- survival race (Xbox DLC Nem_Quarry) -->' % (ind, NAME))
        ins.append((pm, nem[0] + 1))
    # the DLC element's tracks, in its order (DLCPack attributes dropped)
    dl = dlc_tf.decode('latin-1')
    m = re.search(r'<GameMode Name="%s"[^>]*>(.*?)</GameMode>' % NAME, dl, re.S)
    need(m, 'DLC TrackFiltration.xml: no GameMode %s' % NAME)
    dtracks = re.findall(r'<Track Name="([^"]+)" SubTrack="([^"]+)"', m.group(1))
    use = [x for x in dtracks if x[0].lower() in tracks]
    gme = [i for i, x in enumerate(lines) if x.strip() == '</GameModes>']
    need(len(gme) == 1, 'PC TrackFiltration: %d </GameModes>' % len(gme))
    el = ['\t<!-- Survival Race game mode (Xbox DLC Nem_Quarry, NemesisRace) -->',
          '\t<GameMode Name="%s" SplitScreenName="%s" MultiplayerName="%s">' % (NAME, NAME, NAME), '\t\t<Tracks>']
    el += ['\t\t\t<Track Name="%s" SubTrack="%s"/>' % x for x in use]
    el += ['\t\t</Tracks>', '\t</GameMode>']
    lines[gme[0]:gme[0]] = el
    return '\r\n'.join(lines).encode('latin-1'), ins, dtracks, use


def apply(files, pc, dlc_dir):
    """files: {key: bytes} of the base overlay; returns (new files, info)"""
    xb = Ark(find_ci(dlc_dir, 'RESIDENT_DLC.ARK'))
    out = dict(files)
    info = {}
    mode = xb.get(key(P_MODE))
    need(mode is not None and mode.count(TYPE_XB.encode()) == 1, 'DLC %s: no single %s line' % (P_MODE, TYPE_XB))
    out[key(P_MODE)] = mode.replace(TYPE_XB.encode(), TYPE_PC.encode())
    for p in (P_HUD, P_TRUCK):
        d = xb.get(key(p))
        need(d is not None, 'DLC: no %s' % p)
        out[key(p)] = d
    for p in (P_MODE, P_HUD, P_TRUCK):
        # the track build (assemble_track.py) brings every RESIDENT_DLC.ARK key the PC lacks, these three
        # unchanged; nothing names them until directory.txt does
        need(pc.get(key(p)) is None, '%s is in the PC resident.ark already' % p)
        need(key(p) not in files or files[key(p)] == xb.get(key(p)), '%s is in the base with other bytes' % p)
    info_base = [p for p in (P_MODE, P_HUD, P_TRUCK) if key(p) in files]
    d = get(files, pc, P_DIR).decode('latin-1')
    d, i1 = insert_sorted(d, RE_GAMEMODES, 'GAMEMODES', NAME + '.params')
    d, i2 = insert_sorted(d, RE_NEMESIS, 'NEMESIS', 'TruckSurvivalRace.params')
    out[key(P_DIR)] = d.encode('latin-1')
    tracks = {n.strip().lower() for n in section(d, RE_TRACKS, 'TRACKS')[2]}
    tf, tins, dtracks, use = build_trackfiltration(get(files, pc, P_TF), open(find_ci(dlc_dir, P_TF), 'rb').read(), tracks)
    out[key(P_TF)] = tf
    pairs_by = {}
    off = official_texts()
    for lang in LANGS + ('StringIDs',):
        p = 'dictionary/%s_win32.dct' % lang.lower()
        keys = [K_NAME] + [K_HELP % i for i in range(1, 6)]
        if lang == 'StringIDs':
            texts = [k.lower() for k in keys]
        elif off is not None:
            texts = off[lang]
        else:
            name, helps = TEXTS[lang]
            texts = [name] + list(helps) + ['BLANK'] * (5 - len(helps))
        pairs = list(zip(keys, texts))
        out[key(p)] = dictadd.add_checked(get(files, pc, p), pairs)
        pairs_by[p] = pairs
    info.update(text_source=text_source(), in_base=info_base, dir_inserts=(i1, i2), tf_inserts=tins, dlc_tracks=dtracks, tracks=use, dicts=pairs_by,
                changed=[P_MODE, P_HUD, P_TRUCK, P_DIR, P_TF] + list(pairs_by))
    return out, info


def check(before, after, pc, dlc_dir, info):
    """[(ok, text)]"""
    res = []
    ok = lambda c, t: res.append((bool(c), t))
    xb = Ark(find_ci(dlc_dir, 'RESIDENT_DLC.ARK'))
    changed = {key(p) for p in info['changed']}
    ok(set(after) == set(before) | changed and all(after[k] == before[k] for k in before if k not in changed),
       'entries: the base\'s %d unchanged, %d new or replaced (%s; %s already in the base as the Xbox files)' % (
           len(before), len(changed), ', '.join(info['changed']), ', '.join(info['in_base']) or 'none'))
    m0, m1 = xb.get(key(P_MODE)), after[key(P_MODE)]
    ok(m1.replace(TYPE_PC.encode(), TYPE_XB.encode()) == m0 and m1.count(TYPE_PC.encode()) == 1,
       '%s: the Xbox file with only %s -> %s (%d bytes)' % (P_MODE, TYPE_XB, TYPE_PC, len(m1)))
    for p in (P_HUD, P_TRUCK):
        ok(after[key(p)] == xb.get(key(p)), '%s: the Xbox file (%d bytes)' % (p, len(after[key(p)])))
    sel = dict(re.findall(rb"\tSelection(\d+) = ([^;]+);", m1))
    used = sorted(int(i) for i, v in sel.items() if v != b'None')
    ok(used == list(range(8)) and all(sel[str(i).encode()] == b'TruckSurvivalRace.params' for i in used),
       'mode: Nemesis Selection0..7 = TruckSurvivalRace.params, Selection8..%d = None' % (len(sel) - 1))
    for p in (P_MODE, P_HUD, P_TRUCK):
        d = after[key(p)]
        ok(d.count(b'\n') == d.count(b'\r\n') and d.endswith(b'\r\n'), '%s: CRLF only' % p)
    # directory.txt: base + the two lines, as the game's block rule reads it
    d0 = get(before, pc, P_DIR).decode('latin-1')
    d1 = after[key(P_DIR)].decode('latin-1')
    (p1, l1), (p2, l2) = info['dir_inserts']
    ok(d1[:p1] + d1[p1 + len(l1):p2] + d1[p2 + len(l2):] == d0 if p2 > p1 else False,
       'directory.txt: base bytes + %s and %s' % (l1.strip(), l2.strip()))
    gm = section(d1, RE_GAMEMODES, 'GAMEMODES')[2]
    nm = section(d1, RE_NEMESIS, 'NEMESIS')[2]
    ok(NAME + '.params' in gm and 'TruckSurvivalRace.params' in nm and
       [x.lower() for x in gm] == sorted(x.lower() for x in gm) and [x.lower() for x in nm] == sorted(x.lower() for x in nm),
       'directory.txt GAMEMODES (%d) and NEMESIS (%d) lists sorted, with the new names' % (len(gm), len(nm)))
    tf = after[key(P_TF)].decode('latin-1')
    t0 = get(before, pc, P_TF).decode('latin-1')
    import xml.etree.ElementTree as ET
    root = ET.fromstring(tf)
    pms = {pm.get('Name'): [g.get('Name') for g in pm.iter('PlayableGameMode')] for pm in root.iter('PlayMode')}
    root0 = ET.fromstring(t0)
    pms0 = {pm.get('Name'): [g.get('Name') for g in pm.iter('PlayableGameMode')] for pm in root0.iter('PlayMode')}
    ok(all(pms[n] == pms0[n] for n in pms0 if n != 'SplitScreen') and
       all(pms[n] == pms0[n][:pms0[n].index('Nemesis') + 1] + [NAME] + pms0[n][pms0[n].index('Nemesis') + 1:]
           for n in ('SplitScreen',)) and
       all(len(v) <= 16 for v in pms.values()),
       'TrackFiltration.xml PlayModes: %s after Nemesis in SplitScreen (%s), SinglePlayer and MultiPlayer unchanged'
       % (NAME, '; '.join('%s %d' % (k, len(v)) for k, v in pms.items())))
    gms = [g for g in root.iter('GameMode') if g.get('Name') == NAME]
    tr = [(t.get('Name'), t.get('SubTrack')) for t in gms[0].iter('Track')] if len(gms) == 1 else []
    ok(len(gms) == 1 and tr == info['tracks'] and len(tr) <= 16 and gms[0].get('SplitScreenName') == NAME and
       gms[0].get('MultiplayerName') == NAME,
       'TrackFiltration.xml GameMode %s: %d tracks %s (DLC: %s)' % (NAME, len(tr), ', '.join('%s %s' % x for x in tr),
                                                                   ', '.join('%s %s' % x for x in info['dlc_tracks'])))
    old_gm = [(g.get('Name'), [(t.get('Name'), t.get('SubTrack')) for t in g.iter('Track')]) for g in root0.iter('GameMode')]
    new_gm = [(g.get('Name'), [(t.get('Name'), t.get('SubTrack')) for t in g.iter('Track')]) for g in root.iter('GameMode')
              if g.get('Name') != NAME]
    ok(old_gm == new_gm and tf.count('\n') == tf.count('\r\n'), 'TrackFiltration.xml: every other GameMode unchanged, CRLF only')
    bad = []
    for p, pairs in info['dicts'].items():
        if 'stringids' in p:
            continue
        have = [v[0].decode('utf-8', 'replace') for v in dictadd.Dict(pc.get(key(p))).all().values()]
        miss = tu4text.missing_letters(have, [v for _, v in pairs])
        if miss:
            bad.append('%s: %s' % (p, ''.join(miss)))
    ok(not bad, 'texts (%s): every non-ASCII letter is one the PC dictionary of that language uses (the PC fonts '
       'have those)%s' % (info['text_source'], ': ' + '; '.join(bad) if bad else ''))
    for p, pairs in info['dicts'].items():
        a0, a1 = dictadd.Dict(get(before, pc, p)), dictadd.Dict(after[key(p)])
        old = a0.all()
        new = a1.all()
        ok(all(new.get(h) == v for h, v in old.items()) and len(new) == len(old) + len(pairs) and
           all(a1.get(k) == v.encode('utf-8') for k, v in pairs),
           '%s: %d entries unchanged, + %s' % (p, len(old), ', '.join('%s=%r' % kv for kv in pairs[:2])))
    return res


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--dlc', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--data-out', default=None)
    ap.add_argument('--game', default=DEF_GAME, required=not DEF_GAME,
                    help='the game\'s data folder (read only; default: below GAME_DIR, the game folder)')
    ap.add_argument('--no-movie', action='store_true')
    a = ap.parse_args(argv)
    gd = os.path.normcase(os.path.dirname(os.path.abspath(a.game)))
    for p in [a.out] + ([a.data_out] if a.data_out else []):
        q = os.path.normcase(os.path.abspath(p))
        if q == gd or q.startswith(gd + os.sep):
            raise SystemExit('refusing to write into the game folder: %s' % p)
    if os.path.normcase(os.path.abspath(a.out)) == os.path.normcase(os.path.abspath(a.base)):
        raise SystemExit('--out must not be --base')
    pc = Ark(os.path.join(a.game, 'resident.ark'))
    base = Ark(a.base)
    before = {k: base.get(k) for k in base.order}
    lines = []
    say = lambda s='': (lines.append(s), print(s, flush=True))
    say('Survival Race (NemesisRace) on %s' % os.path.basename(a.base))
    after, info = apply(before, pc, a.dlc)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    write_ark(a.out, sorted(after.items()), pad=0x800)
    res = check(before, after, pc, a.dlc, info)
    # archive read back, and the virtual resident.ark (PC + this archive) through the game's reader
    o = Ark(a.out)
    res.append((not o.be and o.order == sorted(set(o.order)) and all(o.ents[k][0] == 0 for k in o.order) and
                set(o.order) == set(after) and all(o.get(k) == after[k] for k in o.order) and
                os.path.getsize(a.out) % 0x800 == 0,
                '%s: %d entries, %d bytes; keys sorted, all stored, data equal, padded to 0x800' % (
                    os.path.basename(a.out), len(o.order), os.path.getsize(a.out))))
    o.close()
    import vark
    va = vark.VirtualArk(os.path.join(a.game, 'resident.ark'), [a.out])
    try:
        gr = vark.GameArkReader(va.read, va.size)
        bad = [p for p in info['changed'] if gr.read_member(key(p)) != after[key(p)]]
        res.append((not bad, 'virtual resident.ark (PC + %s): the %d mode entries read back through GameArkReader' % (
            os.path.basename(a.out), len(info['changed']))))
    finally:
        va.close()
    if not a.no_movie:
        src = find_ci(a.dlc, DLC_MOVIE)
        if a.data_out and src:
            dst = os.path.join(a.data_out, *MOVIE_REL.split('/'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            pcm = os.path.join(a.game, *MOVIE_REL.split('/'))
            res.append((open(dst, 'rb').read() == open(src, 'rb').read() and not os.path.exists(pcm),
                        '%s: the package movie (%d bytes, Bink %r), name not taken by a PC file' % (
                            os.path.relpath(dst, a.data_out), os.path.getsize(dst), open(dst, 'rb').read(3))))
        elif a.data_out:
            res.append((False, 'the package has %s' % DLC_MOVIE))
    for c, t in res:
        say('  [%s] %s' % ('ok' if c else 'FAIL', t))
    nfail = sum(1 for c, _ in res if not c)
    say('RESULT: %s' % ('OK' if not nfail else 'ERRORS: %d' % nfail))
    with open(os.path.splitext(a.out)[0] + '.nemesis_mode.txt', 'w', newline='\r\n', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    return 0 if not nfail else 1


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except ModeError as e:
        sys.exit('nemesis_mode: %s' % e)
