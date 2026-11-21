# Deadline (Xbox DLC game mode "TimeTrialPlus"), stage 1: a data-only countdown time trial for the PC game.
# Stage 2 (the green pickup boxes that stop the clock) is code in the DLL (src/dlcpickups.cpp, [DLC] Pickups=1);
# this script only adds their data, the 11 .pkps files at the Xbox path (pkps.py). Offline only.
#
#   apply(files, pc_ark, dlc_dir, extra_seconds=0.0, per_track=True, log=print) -> files
# takes the overlay entries {key: bytes} a pack build has produced (they may already hold a merged
# directory.txt and dictionaries: every shared file is taken from files[k] if present, else from the PC
# resident.ark) and returns a new dict with the mode added. check(before, after, pc_ark, dlc_dir) re-reads
# everything. The CLI (main) applies it on top of an existing overlay .ark and writes a test .ark.
#
# What the PC does with a game mode (SplitSecond.exe, Ghidra, read-only; see README.md):
# - directory.txt GAMEMODES (0x78EF00 parses every listed file at start-up; the profile lists 0x7732C0 /
#   0x773B10 iterate it by name into sorted maps, no fixed count).
# - UI/Logic/TrackFiltration.xml: PlayableGameMode Name (0x77B830, at most 16 per PlayMode: 0x77B970
#   allocates 16 x 0x48 and the append has no bounds check), GameMode Name/SplitScreenName/MultiplayerName
#   and Tracks (0x77B6C0, 16 tracks: 0x77B620). Other attributes (DLCPack, DLCGameModeIndex) are not read.
# - Race setup (0x793550): GameLogic/GameModes/<Name>.params, then RaceSetupStatic.params, then the track
#   overlay (0x790D90) GameLogic/Tracks/<track>/Params/RaceSetupOverlay_<sub>[<variation>].params, then
#   GameModeHUDs/<Name>.params (fallback QuickRace.params). The PC never builds a mode-specific overlay name,
#   so the Xbox RaceSetupOverlay_<sub>_TimeTrialPlus.params files are never read on the PC.
# - Params parser 0xABC460: '!directory' lines go to an empty handler (0x8881C0); a section line sets an
#   absolute path (0x41D1A0); a key is offered to the registered tweakables and ignored when none matches
#   (0x41D630 / 0x41D9B0 / 0x41DB10). The PC's own TimeTrial.params ships 25 keys the exe never registers
#   (Lessons/.../AdviceSlowMo, Ranking/'Calculate Overall Rank').
# - Countdown: '/RaceSetup/Logic/Countdown' Enabled (0xD6BC44) and 'Initial Countdown' (0xD0B7E0); the timer
#   is only run when Enabled (0x4454F0, jz at 0x44551E).
# - Texts: TEXT_GAMEMODE_NAME_<Name> (0x77B4F0, 0x804A70) and TEXT_GAMEMODE_<Name>_HELP_LINE1..5 (0x804A70,
#   'BLANK' = empty line).
# - Quick-play movie: exe table only (0x479680): ids 0-5 = table 0xD0F998 ("data|Movies/Screens/QuickPlay/
#   FE_<mode>.bik", loose files, not resident.ark), 6-20 = track movies (0xD6FC40), 21 = FE_Locked. The id of
#   a mode is its index in the SinglePlayer PlayMode (0x77A9F0, element +4), so a 7th mode gets id 6 =
#   track table entry 0 = FE_QP_Track10.bik. No data file names a movie: FE_DEADLINE.BIK cannot be shown
#   without code. The movie is only checked here (movie_check), not put into the archive.
# - Lock (0x77A9F0): a single-player quick-play mode is locked until an unlocked season event (12 episodes)
#   has GameMode == the mode's Name, unless DLCUnlocks11 or DLCUnlocks12 is set (0x486DE0, the game's DLC
#   unlock flags 0xA062D0, kept in the profile 0x41ACA0 / 0x41ACD0). No season event uses TimeTrialPlus, so
#   the mode is listed but locked; nothing here changes that (see README, "Unlock").
import os
import re
import struct
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'misc'))
sys.path.insert(0, os.path.join(HERE, '..', 'ui'))
from arkio import Ark, key, write_ark  # noqa: E402
import dictadd  # noqa: E402
sys.path.insert(0, HERE)
import pkps  # noqa: E402  (this folder)
import pickupmodel  # noqa: E402  (this folder)
import modeicon  # noqa: E402  (this folder)
import tu4text  # noqa: E402  (this folder)

# a manual run's PC resident.ark: below the game folder in GAME_DIR, else --pc must be given (build_pack.py gives it)
PC_RESIDENT = os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else None
# development: the defaults of a manual run below GLIMPSE_DLC_SCRATCH\dlc; without it give --dlc, --base and --out
# (build_pack.py calls apply/check with its own paths)
SCRATCH = os.path.join(os.environ['GLIMPSE_DLC_SCRATCH'], 'dlc') if os.environ.get('GLIMPSE_DLC_SCRATCH') else ''
DLC_DIR = os.path.join(SCRATCH, 'x_deadline') if SCRATCH else None
BASE_ARK = os.path.join(SCRATCH, 'overlay', 'deadline.ark') if SCRATCH else None
OUT_ARK = os.path.join(SCRATCH, 'overlay', 'deadline_mode_test.ark') if SCRATCH else None
PROTECTED = ('deadline.ark', 'deadline_v2.ark')

NAME = 'TimeTrialPlus'
P_MODE = 'GameLogic/GameModes/TimeTrialPlus.params'
P_MODE_PC = 'GameLogic/GameModes/TimeTrial.params'
P_HUD = 'GameModeHUDs/TimeTrialPlus.params'
P_HUD_PC = 'GameModeHUDs/TimeTrial.params'
P_DIR = 'directory.txt'
P_TF = 'UI/Logic/TrackFiltration.xml'
P_OVL = 'GameLogic/Tracks/%s/Params/RaceSetupOverlay_%s.params'
DLC_ARK = 'RESIDENT_DLC.ARK'
DLC_TF = 'UI/LOGIC/TRACKFILTRATION.XML'
DLC_DIRTXT = 'DIRECTORY.TXT'
DLC_MOVIE = 'MOVIES/SCREENS/QUICKPLAY/FE_DEADLINE.BIK'
PC_MOVIES = ('FE_Race', 'FE_TimeTrial', 'FE_Nemesis', 'FE_Chopper_Survival', 'FE_Chopper_Revenge', 'FE_Eliminator')

# the Xbox per-track files (paths as the DLC names them; the keys are in RESIDENT_DLC.ARK)
XB_OVERLAYS = (
    'GameLogic/Tracks/airport_test_03/Params/RaceSetupOverlay_a_TimeTrialPlus.params',
    'GameLogic/Tracks/docks/Params/RaceSetupOverlay_A_TimeTrialPlus.params',
    'GameLogic/Tracks/Docks/Params/RaceSetupOverlay_b_TimeTrialPlus.params',
    'GameLogic/Tracks/docks/Params/RaceSetupOverlay_c_TimeTrialPlus.params',
    'GameLogic/Tracks/downtown/Params/RaceSetupOverlay_A_TimeTrialPlus.params',
    'GameLogic/Tracks/Downtown/Params/RaceSetupOverlay_b_TimeTrialPlus.params',
    'GameLogic/Tracks/Downtown/Params/RaceSetupOverlay_C_TimeTrialPlus.params',
    'GameLogic/Tracks/Graveyard/Params/RaceSetupOverlay_a_TimeTrialPlus.params',
    'GameLogic/Tracks/graveyard/Params/RaceSetupOverlay_B_TimeTrialPlus.params',
    'GameLogic/Tracks/Powerplant/Params/RaceSetupOverlay_A_TimeTrialPlus.params',
    'GameLogic/Tracks/powerplant/Params/RaceSetupOverlay_B_TimeTrialPlus.params',
)

# PC 'Type' enum (string table 0xD36A68): no 'Time Trial Plus'
PC_TYPES = ('Race', 'Elimination', 'Time Trial', 'Survival', 'Air Strike', 'Air Revenge', 'Other')
TYPE_XB, TYPE_PC = "'Time Trial Plus'", "'Time Trial'"
# the values the Xbox file has for keys PC TimeTrial.params has too, where they differ (Type is mapped)
EXPECTED_DIFF = {
    ('/RaceSetup/Game Mode', 'Type'),
    ('/RaceSetup/Logic/Countdown', "'Initial Countdown'"),
    ('/RaceSetup/Logic/Countdown', 'Enabled'),
    ('/RaceSetup/Logic/EndGame', "'End Condition Countdown'"),
    ('/RaceSetup/Logic/Laps', "'Start Timer On Lap Start'"),
    ('/RaceSetup/Scoring/Drifting', "'Power enabled'"),
    ('/RaceSetup/Scoring/Drifting', "'Score enabled'"),
}
COUNTDOWN = ('/RaceSetup/Logic/Countdown', "'Initial Countdown'")
XB_COUNTDOWN = ('/RaceSetupOverlay', 'TimeTrialPlusCountdown')
XB_RIVALS = '/RaceSetupOverlay/TimeTrialPlus'   # <Rival>Time keys: the time left when that rival finishes
# The rival standings (src/dlcrivals.cpp): the DLL reads the Xbox keys from the overlay the PC reads for the track
# (the PC's parser drops them), the official names from P_RIVAL_NAMES (TU4 table, tu4text.py; without it the DLL
# makes them from the keys). The Frozen Time post presets (src/dlcpickups.cpp): the DLC's two files as they are.
P_RIVAL_NAMES = 'glimpse/deadline/RivalNames.txt'
P_PPD = ('PostProcessDatabase/Frozen_Time_Heavy.params', 'PostProcessDatabase/Frozen_Time_Subtle.params')
PPD_KEYS = ("'b; Fade In Time'", "'c; Fade Out Time'", "'e; Overwrite Color Cube Configuration'",
            "'i; Overwrite Vignette Configuration'", "'h; Overwrite Gradient Configuration'")

LANGS = ('ENGLISH_NTSC', 'GERMAN', 'FRENCH_NTSC', 'ITALIAN', 'SPANISH_NTSC')
K_NAME = 'TEXT_GAMEMODE_NAME_%s' % NAME
K_HELP = 'TEXT_GAMEMODE_%s_HELP_LINE%%d' % NAME
# official English name "Deadline"; mode names are upper case on the PC; no translated name is known, so the
# name is the same in every language (as for the DLC car names). Three help lines + 'BLANK', like TimeTrial.
TEXTS = {
    'ENGLISH_NTSC': ('DEADLINE', (
        'BEAT THE CLOCK - THE TIMER COUNTS DOWN FROM THE START.',
        'FINISH THE LAP BEFORE THE COUNTDOWN HITS ZERO.',
        'DRIFT & DODGE POWER PLAYS TO KEEP YOUR SPEED UP!')),
    'GERMAN': ('DEADLINE', (
        'SCHLAGE DIE UHR - DER TIMER LÄUFT AB DEM START RÜCKWÄRTS.',
        'BEENDE DIE RUNDE, BEVOR DER COUNTDOWN NULL ERREICHT.',
        'DRIFTE UND WEICHE POWERPLAYS AUS, UM SCHNELL ZU BLEIBEN!')),
    'FRENCH_NTSC': ('DEADLINE', (
        'BATS LE CHRONO : LE COMPTE À REBOURS DÉMARRE AU DÉPART',
        'TERMINE LE TOUR AVANT QUE LE CHRONO ATTEIGNE ZÉRO',
        'DÉRAPE ET ÉVITE LES ATTAQUES POUR GARDER TA VITESSE !')),
    'ITALIAN': ('DEADLINE', (
        'BATTI IL CRONOMETRO: IL CONTO ALLA ROVESCIA PARTE DAL VIA.',
        'COMPLETA IL GIRO PRIMA CHE IL TEMPO ARRIVI A ZERO.',
        'DERAPA ED EVITA I POWER PLAY PER RESTARE VELOCE!')),
    'SPANISH_NTSC': ('DEADLINE', (
        'BATE AL RELOJ: LA CUENTA ATRÁS EMPIEZA EN LA SALIDA',
        'TERMINA LA VUELTA ANTES DE QUE EL TIEMPO LLEGUE A CERO',
        '¡DERRAPA Y ESQUIVA JUGADAS PARA MANTENER LA VELOCIDAD!')),
}


class ModeError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise ModeError(msg)


def dict_path(lang):
    return 'dictionary/%s_win32.dct' % lang.lower()


def text_pairs(lang):
    off = official_texts()
    if off is not None:
        return [(k, k.lower()) for k, _ in off['ENGLISH_NTSC']] if lang == 'StringIDs' else off[lang]
    if lang == 'StringIDs':
        return [(k, k.lower()) for k in [K_NAME] + [K_HELP % i for i in range(1, 6)]]
    name, lines = TEXTS[lang]
    return [(K_NAME, name)] + [(K_HELP % (i + 1), t) for i, t in enumerate(lines + ('BLANK', 'BLANK'))]


# The official texts: Title Update 4's built-in text table (tu4text.py; only when a developer's table is set,
# GLIMPSE_TU4_TEXTS): the name, help lines 1..5 (an empty line -> 'BLANK', the PC's empty line) and the four
# pickup / hint texts of the TU4 HUD (TU4_RULES.md section 3; no PC code reads them yet, the DLL draws its own
# feedback). Without the table: TEXTS above and no pickup texts, as before.
TU4_NAME = 'TEXT_GAMEMODE_NAME_TIMETRIALPLUS'
TU4_HELP = 'TEXT_GAMEMODE_timetrialplus_HELP_LINE%d'
TU4_EXTRA = ('TEXT_TIMETRIALPLUS_PICKUP', 'TEXT_TIMETRIALPLUS_DRIFTPICKUP', 'TEXT_TIMETRIALPLUS_OBJECTIVE1',
             'TEXT_TIMETRIALPLUS_OBJECTIVE2')


def official_texts():
    """{lang: [(PC key, text)]} from the TU4 table, or None (no table set, or a text missing in it)"""
    t = tu4text.load()
    if t is None:
        return None
    keys = [(K_NAME, TU4_NAME)] + [(K_HELP % i, TU4_HELP % i) for i in range(1, 6)] + [(k, k) for k in TU4_EXTRA]
    if any(t.get(xk, l) is None for _, xk in keys for l in LANGS):
        return None
    return {l: [(pk, t.get(xk, l) or 'BLANK') for pk, xk in keys] for l in LANGS}


def text_source():
    t = tu4text.load()
    if t is None:
        return 'generated texts (no TU4 text table, %s not set)' % tu4text.ENV
    return ('TU4 text table %s' % t.describe()) if official_texts() is not None else \
        'generated texts (the TU4 table %s lacks a Deadline text)' % t.path


# ---- helpers ----

def open_pc(pc_ark):
    return pc_ark if isinstance(pc_ark, Ark) else Ark(pc_ark)


def find_ci(root, rel):
    """the file rel (any case) below root; the Xbox dump keeps the package's upper-case names"""
    cur = root
    for part in rel.replace('\\', '/').split('/'):
        hit = [n for n in os.listdir(cur) if n.lower() == part.lower()]
        need(hit, '%s: no %s in %s' % (rel, part, cur))
        cur = os.path.join(cur, hit[0])
    return cur


def params(text):
    """[(section, key, value)] of a .params text (sections absolute, one key per line)"""
    out, sec = [], None
    for ln in text.split('\r\n'):
        st = ln.strip()
        if st.startswith('/') and st.endswith(':') and ' = ' not in st:  # one PC file indents a section line
            sec = st.rstrip(':')
        elif st and ln.startswith('\t') and sec:
            m = re.match(r'^\t(.+?) = (.*);[ \t]*$', ln)
            need(m, 'unexpected params line %r' % ln)
            out.append((sec, m.group(1), m.group(2)))
        else:
            need(st == '' or st.startswith('!'), 'unexpected params line %r' % ln)
    return out


def pdict(text):
    d = {}
    for s, k, v in params(text):
        need((s, k) not in d, 'duplicate key %s %s' % (s, k))
        d[(s, k)] = v
    return d


def blocks(text):
    """'!directory' head + [[header, line, ...]] of a CRLF .params text; blocks are separated by one empty line"""
    need(text.endswith('\r\n'), 'params text does not end with CRLF')
    parts = text[:-2].split('\r\n\r\n')
    head, rest = parts[0], parts[1:]
    need(head.startswith('!directory: ') and '\r\n' not in head, 'unexpected params head %r' % head[:60])
    bl = [p.split('\r\n') for p in rest]
    for b in bl:
        need(b[0].startswith('/') and b[0].endswith(':'), 'unexpected section line %r' % b[0])
    return head, bl


def join_blocks(head, bl):
    return '\r\n\r\n'.join([head] + ['\r\n'.join(b) for b in bl]) + '\r\n'


def number(v):
    m = re.match(r'^(-?[0-9.]+)( \(.*\))?$', v)
    need(m, 'not a number value %r' % v)
    return float(m.group(1)), m.group(2) or ''


def fmt(x):
    s = repr(float(x))
    return s if '.' in s else s + '.0'


class Src:
    """reads a game path: the pack's entry if present, else the PC resident.ark"""

    def __init__(self, files, pc):
        self.files, self.pc = files, pc

    def get(self, p):
        k = key(p)
        if k in self.files:
            return self.files[k]
        d = self.pc.get(k)
        need(d is not None, 'no %s in the pack or the PC resident.ark' % p)
        return d

    def origin(self, p):
        return 'pack' if key(p) in self.files else 'PC'


def pc_mode_names(pc):
    d = pc.get(P_DIR).decode('latin-1')
    sec = gamemodes_section(d)
    return [n for n in sec[2] if n.lower().endswith('.params')]


def gamemodes_section(text):
    """(start, end, [names]) of the GAMEMODES list in directory.txt: the lines after '\\t:GAMEMODES', '\\t:0'"""
    m = re.search(r'-GameLogic/GameModes/\*\.params\r\n\t:GAMEMODES\r\n\t:0\r\n', text)
    need(m, 'directory.txt: no GAMEMODES section')
    pos, names = m.end(), []
    while pos < len(text) and text[pos] == '\t':
        e = text.index('\r\n', pos)
        names.append(text[pos + 1:e])
        pos = e + 2
    need(pos == len(text) or text[pos] == '-', 'directory.txt: GAMEMODES list does not end at a section')
    return m.end(), pos, names


def dlc_tracks(dlc_tf):
    root = ET.fromstring(dlc_tf)
    gm = [g for g in root.iter('GameMode') if g.get('Name') == NAME]
    need(len(gm) == 1, 'DLC TrackFiltration.xml: %d GameMode %s' % (len(gm), NAME))
    return [(t.get('Name'), t.get('SubTrack')) for t in gm[0].iter('Track')]


def xb_overlay_map(xb):
    """{(track lower, sub upper): (Xbox path, countdown seconds, value text)}"""
    out = {}
    for p in XB_OVERLAYS:
        d = xb.get(key(p))
        need(d is not None, 'DLC: no %s' % p)
        m = re.match(r'^GameLogic/Tracks/([^/]+)/Params/RaceSetupOverlay_([A-Za-z])_TimeTrialPlus\.params$', p)
        v = pdict(d.decode('latin-1')).get(XB_COUNTDOWN)
        need(v is not None, '%s: no TimeTrialPlusCountdown' % p)
        out[(m.group(1).lower(), m.group(2).upper())] = (p, number(v)[0], v)
    return out


# ---- builders ----

def build_mode(src, pc, xb, extra_seconds):
    """Xbox TimeTrialPlus.params without the keys no PC mode file has, Type -> 'Time Trial'"""
    xbt = xb.get(key(P_MODE))
    need(xbt is not None, 'DLC: no %s' % P_MODE)
    xbt = xbt.decode('latin-1')
    union = set()
    for n in pc_mode_names(pc):
        union |= set(pdict(pc.get('GameLogic/GameModes/' + n).decode('latin-1')))
    head, bl = blocks(xbt)
    dropped, keep = [], []
    for b in bl:
        lines = [b[0]]
        for ln in b[1:]:
            s, k, v = params(b[0] + '\r\n' + ln)[0]
            if (s, k) in union:
                if (s, k) == ('/RaceSetup/Game Mode', 'Type'):
                    need(v == TYPE_XB, 'DLC Type is %s' % v)
                    ln = ln.replace(TYPE_XB, TYPE_PC)
                if (s, k) == COUNTDOWN and extra_seconds:
                    x, rng = number(v)
                    ln = '\t%s = %s%s;' % (k, fmt(x + extra_seconds), rng)
                lines.append(ln)
            else:
                dropped.append((s, k, v))
        if len(lines) > 1:
            keep.append(lines)
    out = join_blocks(head, keep)
    return out.encode('latin-1'), dropped, union


def build_hud(src, xb):
    pc = src.get(P_HUD_PC)
    old, new = b"'Is Timer Using Countdown' = false;", b"'Is Timer Using Countdown' = true;"
    n = pc.count(old)
    out = pc.replace(old, new)
    xbd = xb.get(key(P_HUD))
    need(xbd is not None, 'DLC: no %s' % P_HUD)
    need(n == 5 and out == xbd, 'PC TimeTrial HUD with the countdown timer (%d places) is not the DLC HUD file' % n)
    return out


def countdown_block(seconds):
    return "\r\n/RaceSetup/Logic/Countdown:\r\n\t'Initial Countdown' = %s (0.0, 3600.0);\r\n" % fmt(seconds)


def rivals_block(xbt):
    """the Xbox overlay's /RaceSetupOverlay/TimeTrialPlus section (the 7 <Rival>Time lines) and its
    TimeTrialPlusCountdown line, as in the Xbox file (the format Quarry's converted overlay has them in) -> (text, n)"""
    riv, cd, sec = [], [], None
    for ln in xbt.split('\r\n'):
        st = ln.strip()
        if st.startswith('/') and st.endswith(':'):
            sec = st[:-1]
        elif sec == XB_RIVALS and re.match(r'^\t[A-Z][A-Za-z]+Time = ', ln):
            riv.append(ln)
        elif sec == XB_COUNTDOWN[0] and ln.startswith('\t%s = ' % XB_COUNTDOWN[1]):
            cd.append(ln)
    need(len(riv) == 7 and len(cd) == 1, 'Xbox overlay: %d rival times, %d countdowns (7 and 1 expected)' % (len(riv), len(cd)))
    return '\r\n%s:\r\n%s\r\n\r\n%s:\r\n%s\r\n' % (XB_RIVALS, '\r\n'.join(riv), XB_COUNTDOWN[0], cd[0]), len(riv)


def rival_names():
    """{<Rival>Time key: name} from the TU4 table (tu4text.py), or None"""
    t = tu4text.load()
    r = getattr(t, 'rivals', None) if t is not None else None
    return dict(r) if r and len(r) == 7 else None


def build_rival_names(names):
    return ('# Deadline rival names (TU4, tu4text.py): <Rival>Time key = the name the results show\r\n' +
            ''.join('%s=%s\r\n' % kv for kv in sorted(names.items()))).encode('ascii')


def build_presets(xb):
    """{path: bytes}: the DLC's Frozen Time post presets as they are (read only by the DLL)"""
    out = {}
    for p in P_PPD:
        d = xb.get(key(p))
        need(d is not None, 'DLC %s: %s missing' % (DLC_ARK, p))
        pd = pdict(d.decode('latin-1'))
        name = preset_section(p)
        need(all((name, k) in pd for k in PPD_KEYS) and (name + '/Vignette', "'a; Vignette Inner Distance'") in pd
             and (name + '/ColorCube', "'b; Color Cube Blending'") in pd, '%s: not the expected preset' % p)
        out[p] = d
    return out


def preset_section(p):
    """PostProcessDatabase/Frozen_Time_Heavy.params -> /PostProcessDatabase/Frozen Time Heavy"""
    return '/PostProcessDatabase/' + p.split('/')[1][:-len('.params')].replace('_', ' ')


def build_overlays(src, xbmap, tracks, extra_seconds, xb):
    """per-track start time: one block appended to the PC overlay the PC reads for that track (variation 1); then
    the Xbox overlay's rival times and TimeTrialPlusCountdown (for the DLL's rival standings, the PC drops them)"""
    out = {}
    for t, s in tracks:
        k = (t.lower(), s.upper())
        need(k in xbmap, 'no Xbox overlay for %s %s' % (t, s))
        p = P_OVL % (t.lower(), s.upper())
        base = src.get(p)
        need(base.endswith(b'\r\n') and b'/RaceSetup/Logic/Countdown' not in base and XB_RIVALS.encode() not in base,
             '%s: unexpected (already merged?)' % p)
        sec = xbmap[k][1] + extra_seconds
        rb, _ = rivals_block(xb.get(key(xbmap[k][0])).decode('latin-1'))
        out[p] = (base + (countdown_block(sec) + rb).encode('latin-1'), len(base), sec)
    need(len(out) == len(xbmap), 'Xbox overlays without a Deadline track: %s' % sorted(set(xbmap) - {(t.lower(), s.upper()) for t, s in tracks}))
    return out


def build_directory(src):
    d = src.get(P_DIR).decode('latin-1')
    start, end, names = gamemodes_section(d)
    entry = NAME + '.params'
    need(entry.lower() not in [n.lower() for n in names], 'directory.txt already lists %s' % entry)
    pos = start
    for n in names:
        if n.lower() > entry.lower():
            break
        pos = d.index('\r\n', pos) + 2
    line = '\t%s\r\n' % entry
    return (d[:pos] + line + d[pos:]).encode('latin-1'), pos, len(line)


def build_trackfiltration(src, dlc_tf_bytes):
    pc = src.get(P_TF).decode('latin-1')
    dl = dlc_tf_bytes.decode('latin-1')
    lines = pc.split('\r\n')
    # the DLC lines: the PlayableGameMode of SinglePlayer and the whole GameMode element
    dll = dl.split('\r\n')
    pg = [x.rstrip() for x in dll if '<PlayableGameMode' in x and 'Name="%s"' % NAME in x]
    need(len(pg) == 1, 'DLC TrackFiltration: %d PlayableGameMode lines' % len(pg))
    i0 = [i for i, x in enumerate(dll) if '<GameMode ' in x and 'Name="%s"' % NAME in x]
    need(len(i0) == 1, 'DLC TrackFiltration: no GameMode %s' % NAME)
    i1 = next(i for i in range(i0[0], len(dll)) if '</GameMode>' in dll[i])
    gm = [x.rstrip() for x in dll[i0[0]:i1 + 1]]
    # PC: after the last PlayableGameMode of PlayMode SinglePlayer, before </GameModes>
    sp = next(i for i, x in enumerate(lines) if '<PlayMode Name="SinglePlayer">' in x)
    spe = next(i for i in range(sp, len(lines)) if '</PlayMode>' in lines[i])
    last = max(i for i in range(sp, spe) if '<PlayableGameMode' in lines[i])
    gme = [i for i, x in enumerate(lines) if x.strip() == '</GameModes>']
    need(len(gme) == 1, 'PC TrackFiltration: %d </GameModes>' % len(gme))
    ins1 = [pg[0]]
    ins2 = ['\t<!-- Deadline game mode (Xbox DLC Pack4, TimeTrialPlus) -->'] + gm
    new = lines[:last + 1] + ins1 + lines[last + 1:gme[0]] + ins2 + lines[gme[0]:]
    return '\r\n'.join(new).encode('latin-1'), ((last + 1, ins1), (gme[0] + 1, ins2))


def build_dicts(src):
    out = {}
    for lang in LANGS + ('StringIDs',):
        p = dict_path(lang)
        out[p] = dictadd.add_checked(src.get(p), text_pairs(lang))
    return out


def build_pickups(xb):
    """{path: (bytes, pickups)}: the 11 .pkps files as the DLC has them, at the path the Xbox reads
    (pkps.PATH). The PC game never asks for them; the DLL's [DLC] Pickups=1 (src/dlcpickups.cpp) reads them
    from the merged resident.ark by that path. Byte-identical to the DLC's entries."""
    out = {}
    for t in pkps.TRACKS:
        p = pkps.path(*t)
        d = xb.get(key(p))
        need(d is not None, 'DLC %s: %s missing' % (DLC_ARK, p))
        try:
            pk = pkps.parse(d)
            pkps.check(pk, p)
        except pkps.PkpsError as e:
            raise ModeError(str(e))
        out[p] = (d, pk)
    return out


ICON_PATHS = (modeicon.P_GFX, modeicon.P_TEX, modeicon.P_FN)

# ---- the DLC's HUD movie and sounds (Deadline's HUD in the DLL, src/dlcpickups.cpp; TU4_RULES.md section 3) ----
# UI/HUD/DialsInfo.gfx: the DLC package's movie (loose UI/HUD/DIALSINFO.GFX, the same file in Deadline and
# Onslaught) in place of the PC's. The PC movie is byte-identical to the Xbox base game's; the DLC version (Title
# Update 4's HUD) re-exports it with the timer functions Deadline's HUD calls (SetTimerToStoppedColor(Long),
# SetTimerToRunningColor, PulseFrozenTimer, around a new mc_largeTimerHolder) and King of the Air's waves. It
# replaces the PC entry for every mode (the exe loads one DialsInfo.gfx); the checks below make sure it refers to
# exactly the PC movie's external images and imports and defines every function name the PC movie has.
# Audio/Release/Menu/DLCMenu.fev/.fsb: the DLC's sound project DLCMenu (TrialPlus/Pickup, PickupWithDrift, Pulse,
# PulseWithDrift, FreezeExpired and the last seconds 5..1), converted by ../audio/dlcaudio.py (XMA -> MPEG Layer
# II; build_all.py runs it on the package's AUDIO/RELEASE/MENU and gives the folder as menu_audio). New keys, the
# path TU4 loads it from ("Menu/DLCMenu", as the PC's Menu/Menu in resident.ark); read only by the DLL.
P_DIALS = 'UI/HUD/DialsInfo.gfx'
DLC_DIALS = 'UI/HUD/DIALSINFO.GFX'
DIALS_FUNCS = ('SetTimerToStoppedColor', 'SetTimerToStoppedColorLong', 'SetTimerToRunningColor', 'PulseFrozenTimer')
P_MENU = ('Audio/Release/Menu/DLCMenu.fev', 'Audio/Release/Menu/DLCMenu.fsb')
MENU_CONV = ('audio/release/menu/dlcmenu.fev', 'audio/release/menu/dlcmenu.fsb')  # dlcaudio.py's output names
MENU_EVENTS = ('DLCMenu', 'TrialPlus', 'Pickup', 'PickupWithDrift', 'Pulse', 'PulseWithDrift', 'FreezeExpired')


def gfx_names(d):
    """(external image names, imported names, identifiers of the DoAction / DoInitAction code) of a GFX"""
    need(d[:3] in (b'GFX', b'FWS') and len(d) >= 12 and struct.unpack_from('<I', d, 4)[0] == len(d),
         'not an uncompressed GFX with its length in the header')
    tags = modeicon.read_tags(d, modeicon.G.rect_end(d, 8) + 4, len(d))
    need(tags and tags[-1][0] == 0, 'GFX does not end with an End tag')
    images = sorted(modeicon.image_info(t[1])[3] for t in tags if t[0] == 1001)
    imports = sorted(modeicon.imported_ids(tags).values())
    ids = set()

    def code(ts):
        for t in ts:
            if t[0] in (12, 59):
                ids.update(m.group().decode('latin-1') for m in re.finditer(rb'[A-Za-z_][A-Za-z0-9_]{2,}', t[1]))
            elif t[0] == 39:
                code(modeicon.read_tags(t[1], 4, len(t[1])))
    code(tags)
    return images, imports, ids


def build_dials(src, dlc_dir):
    """the DLC's DialsInfo.gfx, checked against the PC's -> (bytes, info)"""
    pc = src.get(P_DIALS)
    need(pc is not None, '%s not in the PC data' % P_DIALS)
    d = open(find_ci(dlc_dir, DLC_DIALS), 'rb').read()
    pi, pm, pf = gfx_names(pc)
    di, dm, df = gfx_names(d)
    need(di == pi and dm == pm, 'the DLC DialsInfo.gfx refers to other images or imports than the PC\'s')
    need(pf <= df, 'the DLC DialsInfo.gfx lacks names of the PC\'s code: %s' % ', '.join(sorted(pf - df)[:8]))
    need(all(f in df and f not in pf for f in DIALS_FUNCS), 'the DLC DialsInfo.gfx lacks the timer functions')
    return d, {'images': len(di), 'imports': len(dm), 'names': len(df), 'pc_names': len(pf), 'new': sorted(df - pf)}


def build_menu(menu_audio, pc):
    """the converted DLCMenu bank from dlcaudio.py's output folder -> {path: bytes}"""
    out = {}
    for p, c in zip(P_MENU, MENU_CONV):
        fn = os.path.join(menu_audio, *c.split('/'))
        need(os.path.isfile(fn), 'menu audio: %s missing (run ../audio/dlcaudio.py on the package\'s AUDIO/RELEASE/MENU)' % fn)
        need(pc.get(key(p)) is None, '%s exists in the PC resident.ark' % p)
        out[p] = open(fn, 'rb').read()
    fev, fsb = out[P_MENU[0]], out[P_MENU[1]]
    need(fev[:4] == b'FEV1' and all(e.encode() in fev for e in MENU_EVENTS), 'menu audio: %s is not the DLCMenu project' % P_MENU[0])
    need(fsb[:4] == b'FSB4' and struct.unpack_from('<I', fsb, 4)[0] == 7, 'menu audio: %s is not a FSB4 bank of 7 samples' % P_MENU[1])
    return out


def build_icon(src, dlc_dir):
    """Deadline's mode icon in the pre-race instructions (modeicon.py): the PC IG_GameModeInstructions.gfx
    with the DLC frame TimeTrialPlus appended to its icon sprite, and the DLC image FE_DLC_event_ICONS added to
    the in-game Flash set InGame.textures/.filenames (pack versions if present). -> ({path: bytes}, gfx info,
    texture info)"""
    try:
        dg = open(find_ci(dlc_dir, modeicon.DLC_GFX), 'rb').read()
        dt = open(find_ci(dlc_dir, modeicon.DLC_TEX), 'rb').read()
        g, gi = modeicon.build_gfx(src.get(modeicon.P_GFX), dg)
        t, f, ti = modeicon.build_tex(src.get(modeicon.P_TEX), src.get(modeicon.P_FN), dt)
    except modeicon.ERRORS as e:
        raise ModeError('mode icon: %s: %s' % (type(e).__name__, e))
    return {modeicon.P_GFX: g, modeicon.P_TEX: t, modeicon.P_FN: f}, gi, ti


def check_icon(src, after, dlc_dir):
    """modeicon's checks of the three icon files; returns the messages"""
    try:
        dg = open(find_ci(dlc_dir, modeicon.DLC_GFX), 'rb').read()
        dt = open(find_ci(dlc_dir, modeicon.DLC_TEX), 'rb').read()
        _, gi = modeicon.build_gfx(src.get(modeicon.P_GFX), dg)
        msgs = modeicon.check_gfx(src.get(modeicon.P_GFX), after[key(modeicon.P_GFX)], dg)
        msgs += modeicon.check_tex(src.get(modeicon.P_TEX), src.get(modeicon.P_FN), after[key(modeicon.P_TEX)],
                                   after[key(modeicon.P_FN)], dt, gi['images'])
        msgs += modeicon.present(lambda p: after.get(key(p)))
    except modeicon.ERRORS as e:
        raise ModeError('FAILED: mode icon: %s: %s' % (type(e).__name__, e))
    return msgs


def icon_present(files, pc_ark=None):
    """for any step that runs after apply() and rewrites shared files (finish_car rebuilds InGame.textures and
    .filenames from the PC copy plus the cars, which would drop FE_DLC_event_ICONS): raises ModeError unless
    the final entries still carry the icon frame and its image. Returns the messages."""
    pc = open_pc(pc_ark) if pc_ark is not None else None

    def get(p):
        d = files.get(key(p))
        return d if d is not None or pc is None else pc.get(key(p))
    try:
        return modeicon.present(get)
    except modeicon.ERRORS as e:
        raise ModeError('mode icon missing in the final pack: %s: %s' % (type(e).__name__, e))


def movie_check(dlc_dir, pc_movie_dir):
    """FE_DEADLINE.BIK against the PC's six mode movies: same Bink revision, size field, frame size, rate,
    flags and audio tracks. Returns (path, header dict, [PC headers]); the file is not packed (see top)."""
    def hdr(d):
        need(d[:3] == b'BIK', 'not a Bink file')
        size, frames, maxf, _, w, h, num, den, flags, audio = struct.unpack_from('<10I', d, 4)
        return {'rev': chr(d[3]), 'size_ok': size + 8 == len(d), 'frames': frames, 'w': w, 'h': h, 'fps': (num, den),
                'flags': flags, 'audio': audio}
    p = find_ci(dlc_dir, DLC_MOVIE)
    x = hdr(open(p, 'rb').read())
    pcs = []
    for n in PC_MOVIES:
        q = os.path.join(pc_movie_dir, n.lower() + '.bik')
        if os.path.exists(q):
            with open(q, 'rb') as f:
                pcs.append((n, hdr(f.read(64))))
    return p, x, pcs


# ---- apply ----

def apply(files, pc_ark, dlc_dir, extra_seconds=0.0, per_track=True, log=print, pickups=True, pickup_model=True,
          mode_icon=True, dlc_hud=True, menu_audio=None):
    """files {key: bytes} -> new dict with Deadline added (input dict unchanged). pickups: also the 11 .pkps
    files (new keys, read only by the DLL's [DLC] Pickups=1; inert without it). pickup_model (with pickups): also
    the box model converted from the DLC's GreenPickupBox (pickupmodel.PATH, our own format, read only by the DLL).
    dlc_hud: the DLC's DialsInfo.gfx in place of the PC's (the timer colours of Deadline's HUD); menu_audio: the folder
    dlcaudio.py converted the package's AUDIO/RELEASE/MENU into (the DLCMenu bank, Deadline's sounds), None: left out"""
    pickup_model = pickups and pickup_model
    pc = open_pc(pc_ark)
    out = dict(files)
    src = Src(files, pc)
    xb = Ark(find_ci(dlc_dir, DLC_ARK))
    try:
        dirtxt = open(find_ci(dlc_dir, DLC_DIRTXT), 'rb').read().decode('latin-1')
        need(NAME + '.params' in dirtxt and ':GAMEMODES' in dirtxt, 'DLC DIRECTORY.TXT does not list %s' % NAME)
        for p in (P_MODE, P_HUD) + (tuple(pkps.path(*t) for t in pkps.TRACKS) + P_PPD if pickups else ()) + \
                ((pickupmodel.PATH,) if pickup_model else ()) + (P_MENU if menu_audio else ()) + \
                ((P_RIVAL_NAMES,) if per_track and rival_names() else ()):
            need(key(p) not in files and pc.get(key(p)) is None, '%s exists already' % p)
        mode, dropped, _ = build_mode(src, pc, xb, extra_seconds)
        out[key(P_MODE)] = mode
        log('%s: Xbox file, %d keys no PC mode file has left out, Type %s -> %s' % (P_MODE, len(dropped), TYPE_XB, TYPE_PC))
        out[key(P_HUD)] = build_hud(src, xb)
        log('%s: PC TimeTrial HUD with \'Is Timer Using Countdown\' = true (= the DLC file)' % P_HUD)
        out[key(P_DIR)] = build_directory(src)[0]
        log('%s (%s): + %s.params in GAMEMODES' % (P_DIR, src.origin(P_DIR), NAME))
        tf = open(find_ci(dlc_dir, DLC_TF), 'rb').read()
        out[key(P_TF)] = build_trackfiltration(src, tf)[0]
        log('%s (%s): + PlayableGameMode %s (SinglePlayer, 7th) and its GameMode' % (P_TF, src.origin(P_TF), NAME))
        if per_track:
            for p, (d, n, sec) in sorted(build_overlays(src, xb_overlay_map(xb), dlc_tracks(tf), extra_seconds, xb).items()):
                out[key(p)] = d
                log('%s (%s): Initial Countdown %s s, + the Xbox rival times and TimeTrialPlusCountdown' % (
                    p, src.origin(p), fmt(sec)))
            names = rival_names()
            if names:
                out[key(P_RIVAL_NAMES)] = build_rival_names(names)
                log('%s (TU4 table): %s' % (P_RIVAL_NAMES, ', '.join(names[k] for k in sorted(names))))
            else:
                log('%s: not written (no TU4 table with the rival names; the DLL makes the names from the keys)'
                    % P_RIVAL_NAMES)
        for p, d in build_dicts(src).items():
            out[key(p)] = d
            log('%s (%s): + %d texts, %s' % (p, src.origin(p), len(text_pairs('StringIDs')), text_source()))
        if pickups:
            for p, (d, pk) in build_pickups(xb).items():
                out[key(p)] = d
                log('%s (DLC, as is): %s' % (p, pkps.summary(pk)))
            for p, d in build_presets(xb).items():
                out[key(p)] = d
                log('%s (DLC, as is): %d bytes, the Frozen Time post preset' % (p, len(d)))
        if pickup_model:
            try:
                blob, info = pickupmodel.build(xb)
            except pickupmodel.PickupModelError as e:
                raise ModeError('pickup model: %s' % e)
            out[key(pickupmodel.PATH)] = blob
            log('%s (DLC %s, converted): %d vertices, %d triangles, %d textures, %d bytes' % (
                pickupmodel.PATH, pickupmodel.SRC, info['vertices'], info['triangles'], len(info['textures']), len(blob)))
        if mode_icon:
            icon, gi, ti = build_icon(src, dlc_dir)
            for p, d in icon.items():
                out[key(p)] = d
            log('%s (%s): icon sprite %d + frame %s (the DLC frame: %s), %d frames' % (
                modeicon.P_GFX, src.origin(modeicon.P_GFX), gi['sprite'], modeicon.LABEL,
                ', '.join('%s %dx%d' % x for x in gi['images']), gi['frames'][1]))
            log('%s, %s (%s): + %s (key %08x, format %#x, %dx%d, %d bytes)' % (
                modeicon.P_TEX, modeicon.P_FN, src.origin(modeicon.P_TEX), ti['name'], ti['key'], ti['format'],
                ti['size'][0], ti['size'][1], ti['bytes']))
        if dlc_hud:
            d, di = build_dials(src, dlc_dir)
            out[key(P_DIALS)] = d
            log('%s (DLC %s, as is): %d bytes, the PC movie\'s %d images and %d imports, its %d code names + %d (%s)' % (
                P_DIALS, DLC_DIALS, len(d), di['images'], di['imports'], di['pc_names'], len(di['new']),
                ', '.join(f for f in DIALS_FUNCS)))
        if menu_audio:
            for p, d in build_menu(menu_audio, pc).items():
                out[key(p)] = d
                log('%s (DLC AUDIO/RELEASE/MENU, converted by dlcaudio.py): %d bytes' % (p, len(d)))
    finally:
        xb.close()
    return out


def mode_paths(dlc_dir, per_track=True, pickups=True, pickup_model=True, mode_icon=True, dlc_hud=True, menu_audio=False):
    """{path: 'new' | 'replaced'}: every archive path apply() writes (for manifests)"""
    out = {P_MODE: 'new', P_HUD: 'new', P_DIR: 'replaced', P_TF: 'replaced'}
    if dlc_hud:
        out[P_DIALS] = 'replaced'
    if menu_audio:
        for p in P_MENU:
            out[p] = 'new'
    if mode_icon:
        for p in ICON_PATHS:
            out[p] = 'replaced'
    if pickups:
        for t in pkps.TRACKS:
            out[pkps.path(*t)] = 'new'
        for p in P_PPD:
            out[p] = 'new'
        if pickup_model:
            out[pickupmodel.PATH] = 'new'
    if per_track:
        tf = open(find_ci(dlc_dir, DLC_TF), 'rb').read()
        for t, s in dlc_tracks(tf):
            out[P_OVL % (t.lower(), s.upper())] = 'replaced'
        if rival_names():
            out[P_RIVAL_NAMES] = 'new'
    for lang in LANGS + ('StringIDs',):
        out[dict_path(lang)] = 'replaced'
    return out


# ---- checks ----

def check(before, after, pc_ark, dlc_dir, extra_seconds=0.0, per_track=True, say=print, pickups=True, pickup_model=True,
          mode_icon=True, dlc_hud=True, menu_audio=None):
    """re-reads everything apply() did; raises ModeError on the first failure. Returns the number of checks."""
    pickup_model = pickups and pickup_model
    pc = open_pc(pc_ark)
    src = Src(before, pc)
    xb = Ark(find_ci(dlc_dir, DLC_ARK))
    n = [0]

    def ok(c, msg):
        need(c, 'FAILED: ' + msg)
        n[0] += 1
        say('ok  ' + msg)
    try:
        tf_dlc = open(find_ci(dlc_dir, DLC_TF), 'rb').read()
        tracks = dlc_tracks(tf_dlc)
        xbmap = xb_overlay_map(xb)
        rnames = rival_names() if per_track else None
        new = {P_MODE, P_HUD} | ({pkps.path(*t) for t in pkps.TRACKS} | set(P_PPD) if pickups else set()) | \
            ({pickupmodel.PATH} if pickup_model else set()) | (set(P_MENU) if menu_audio else set()) | \
            ({P_RIVAL_NAMES} if rnames else set())
        repl = {P_DIR, P_TF} | {dict_path(l) for l in LANGS + ('StringIDs',)}
        if per_track:
            repl |= {P_OVL % (t.lower(), s.upper()) for t, s in tracks}
        if mode_icon:
            repl |= set(ICON_PATHS)
        if dlc_hud:
            repl.add(P_DIALS)
        changed = {k for k in after if before.get(k) != after[k]}
        ok(set(before) <= set(after) and changed == {key(p) for p in new | repl}
           and set(after) - set(before) <= {key(p) for p in new | repl},
           'entries: %d before, %d after; changed or added exactly the %d mode files (%d new, %d replaced), '
           'every other entry byte-identical' % (len(before), len(after), len(new | repl), len(new), len(repl)))
        for p in sorted(new | repl, key=str.lower):
            ok(after.get(key(p)) is not None, 'resolves: %s (key %08x)' % (p, key(p)))

        # mode params
        m = after[key(P_MODE)].decode('latin-1')
        md = pdict(m)
        xd = pdict(xb.get(key(P_MODE)).decode('latin-1'))
        union = set()
        for nm in pc_mode_names(pc):
            union |= set(pdict(pc.get('GameLogic/GameModes/' + nm).decode('latin-1')))
        tt = pdict(pc.get(P_MODE_PC).decode('latin-1'))
        unk = set(xd) - union
        ok(set(md) == set(xd) - unk and all(k in union for k in md),
           'mode params: %d keys = the DLC file\'s %d minus the %d no PC mode file has; all known to PC mode files'
           % (len(md), len(xd), len(unk)))
        exp = dict(xd)
        exp[('/RaceSetup/Game Mode', 'Type')] = TYPE_PC
        if extra_seconds:
            x, rng = number(xd[COUNTDOWN])
            exp[COUNTDOWN] = fmt(x + extra_seconds) + rng
        ok(all(md[k] == exp[k] for k in md), 'mode params: every value as in the DLC file (Type mapped%s)'
           % (', Initial Countdown + %s s' % fmt(extra_seconds) if extra_seconds else ''))
        ok(md[('/RaceSetup/Game Mode', 'Type')].strip("'") in PC_TYPES, "mode params: Type %s is a PC enum value"
           % md[('/RaceSetup/Game Mode', 'Type')])
        diff = {k for k in set(md) & set(tt) if md[k] != tt[k]}
        ok(diff == EXPECTED_DIFF - {('/RaceSetup/Game Mode', 'Type')},
           'mode params vs PC TimeTrial.params: differs in exactly %s' % ', '.join(sorted(k[1].strip("'") for k in diff)))
        miss = set(tt) - set(md)
        ok(all(k[1].strip("'") in ('AdviceSlowMo', 'Calculate Overall Rank') for k in miss),
           'mode params: the %d PC TimeTrial keys it lacks are AdviceSlowMo / Calculate Overall Rank (no exe string)'
           % len(miss))
        ok(md[('/RaceSetup/Cameras', "'Game Mode Name'")] == 'TimeTrial', "mode params: 'Game Mode Name' = TimeTrial")
        ok(m.encode('latin-1') == after[key(P_MODE)] and '\n' not in m.replace('\r\n', ''), 'mode params: CRLF only')

        # HUD
        h = after[key(P_HUD)]
        hp = pdict(src.get(P_HUD_PC).decode('latin-1'))
        hd = pdict(h.decode('latin-1'))
        dh = [k for k in hp if hp[k] != hd.get(k)]
        ok(h == xb.get(key(P_HUD)) and set(hp) == set(hd) and len(hd) == 255 and len(dh) == 5
           and all(k[1] == "'Is Timer Using Countdown'" and hd[k] == 'true' for k in dh),
           'HUD: byte-identical to the DLC file, %d keys as the PC TimeTrial HUD, only the 5 \'Is Timer Using '
           'Countdown\' values true' % len(hd))

        # directory.txt
        db = src.get(P_DIR).decode('latin-1')
        da = after[key(P_DIR)].decode('latin-1')
        _, pos, ln = build_directory(src)
        ok(da[:pos] + da[pos + ln:] == db and da[pos:pos + ln] == '\t%s.params\r\n' % NAME,
           'directory.txt: the %s one with one line inserted, every other byte unchanged' % src.origin(P_DIR).replace('PC', 'PC resident'))

        def sections(t):
            return [s.split('\r\n')[0:3] + [len(s.split('\r\n'))] for s in t.split('\r\n-')]
        sb, sa = sections(db), sections(da)
        ok(len(sb) == len(sa) and all(a == b or (a[1] == '\t:GAMEMODES' and a[3] == b[3] + 1) for a, b in zip(sa, sb)),
           'directory.txt: %d sections intact, only GAMEMODES one entry longer' % len(sa))
        names = gamemodes_section(da)[2]
        ok(names == sorted(names, key=str.lower) == sorted(gamemodes_section(db)[2] + [NAME + '.params'], key=str.lower),
           'directory.txt: GAMEMODES = PC list + %s.params, %d entries, in order' % (NAME, len(names)))

        # TrackFiltration.xml
        tb = src.get(P_TF)
        ta = after[key(P_TF)]
        _, ins = build_trackfiltration(src, tf_dlc)
        la = ta.decode('latin-1').split('\r\n')
        cut = list(la)
        for at, lines in sorted(ins, reverse=True):
            cut[at:at + len(lines)] = []
        ok('\r\n'.join(cut).encode('latin-1') == tb, 'TrackFiltration.xml: the PC bytes plus %d inserted lines, nothing else'
           % sum(len(x[1]) for x in ins))
        root = ET.fromstring(ta)
        rb = ET.fromstring(tb)

        def pm(r, nm):
            return [g.get('Name') for p in r.iter('PlayMode') if p.get('Name') == nm for g in p.iter('PlayableGameMode')]
        ok(pm(root, 'SinglePlayer') == pm(rb, 'SinglePlayer') + [NAME]
           and pm(root, 'MultiPlayer') == pm(rb, 'MultiPlayer') and pm(root, 'SplitScreen') == pm(rb, 'SplitScreen'),
           'TrackFiltration.xml: well-formed; SinglePlayer = %s (index %d = movie id %d); MultiPlayer, SplitScreen unchanged'
           % (', '.join(pm(root, 'SinglePlayer')), len(pm(rb, 'SinglePlayer')), len(pm(rb, 'SinglePlayer'))))
        gms = [g.get('Name') for g in root.iter('GameMode')]
        ok(gms == [g.get('Name') for g in rb.iter('GameMode')] + [NAME] and len(gms) <= 16
           and max(len(pm(root, x)) for x in ('SinglePlayer', 'MultiPlayer', 'SplitScreen')) <= 16,
           'TrackFiltration.xml: GameModes = PC + %s (%d); lists within the exe\'s 16-entry arrays' % (NAME, len(gms)))
        trk = [(t.get('Name'), t.get('SubTrack')) for g in root.iter('GameMode') if g.get('Name') == NAME for t in g.iter('Track')]
        dirt = [x.lower() for x in re.search(r':TRACKS\r\n\t:1\r\n((?:\t[^\r\n]+\r\n)+)', da).group(1).split()]
        ok(trk == tracks and len(trk) <= 16 and all(t.lower() in dirt for t, s in trk)
           and all(pc.get(key(P_OVL % (t.lower(), s.upper()))) is not None for t, s in trk),
           'TrackFiltration.xml: %s has the DLC\'s %d tracks, each in TRACKS and with a PC RaceSetupOverlay_<sub>.params'
           % (NAME, len(trk)))

        # overlays
        if per_track:
            for t, s in tracks:
                p = P_OVL % (t.lower(), s.upper())
                b = src.get(p)
                a = after[key(p)]
                sec = xbmap[(t.lower(), s.upper())][1] + extra_seconds
                ad = pdict(a.decode('latin-1'))
                xbt = xb.get(key(xbmap[(t.lower(), s.upper())][0])).decode('latin-1')
                xd = pdict(xbt)
                rb, nr = rivals_block(xbt)
                riv = {k: v for k, v in ad.items() if k[0] == XB_RIVALS}
                ok(a[:len(b)] == b and a[len(b):] == (countdown_block(sec) + rb).encode('latin-1')
                   and ad[COUNTDOWN] == fmt(sec) + ' (0.0, 3600.0)' and len(ad) == len(pdict(b.decode('latin-1'))) + 2 + nr
                   and len(riv) == 7 and all(xd[k] == v for k, v in riv.items()) and ad[XB_COUNTDOWN] == xd[XB_COUNTDOWN],
                   'overlay %s %s: PC bytes + Initial Countdown %s s (Xbox TimeTrialPlusCountdown %s) + the Xbox\'s 7 '
                   'rival times (%s) and TimeTrialPlusCountdown, values as in the Xbox overlay'
                   % (t, s, fmt(sec), xbmap[(t.lower(), s.upper())][2],
                      ', '.join('%s %s' % (k[1][:-4], number(v)[0]) for k, v in sorted(riv.items()))))
            if rnames:
                d = after[key(P_RIVAL_NAMES)]
                ok(d == build_rival_names(rnames) and pc.get(key(P_RIVAL_NAMES)) is None and xb.get(key(P_RIVAL_NAMES)) is None
                   and set(rnames) == {k[1] for k in riv},
                   '%s: new key %08x, the 7 TU4 names for the overlays\' keys (%s)'
                   % (P_RIVAL_NAMES, key(P_RIVAL_NAMES), ', '.join(rnames[k] for k in sorted(rnames))))

        # pickups
        if pickups:
            total = 0
            for p, (d, pk) in build_pickups(xb).items():
                ok(after[key(p)] == d and pc.get(key(p)) is None,
                   'pickups %s: new key %08x, byte-identical to the DLC entry, %s' % (p, key(p), pkps.summary(pk)))
                total += len(pk)
            ok(total == 496, 'pickups: %d in the 11 files (the Xbox Deadline set)' % total)
            for p, d in build_presets(xb).items():
                pd = pdict(d.decode('latin-1'))
                nm = preset_section(p)
                ok(after[key(p)] == d and pc.get(key(p)) is None,
                   '%s: new key %08x, byte-identical to the DLC entry; fade in %s s, out %s s, overwrites %s' % (
                       p, key(p), number(pd[(nm, "'b; Fade In Time'")])[0], number(pd[(nm, "'c; Fade Out Time'")])[0],
                       ', '.join(k[1].strip("'")[3:].replace('Overwrite ', '').replace(' Configuration', '')
                                 for k, v in sorted(pd.items()) if k[0] == nm and 'Overwrite' in k[1] and v == 'true')))
        if pickup_model:
            blob = after[key(pickupmodel.PATH)]
            ok(pc.get(key(pickupmodel.PATH)) is None and xb.get(key(pickupmodel.PATH)) is None,
               'pickup model %s: new key %08x, in neither the PC resident.ark nor the DLC archive'
               % (pickupmodel.PATH, key(pickupmodel.PATH)))
            try:
                for msg in pickupmodel.check(blob, xb):
                    ok(True, msg)
            except pickupmodel.PickupModelError as e:
                ok(False, 'pickup model: %s' % e)

        # the DLC's HUD movie and sounds
        if dlc_hud:
            d, di = build_dials(src, dlc_dir)
            ok(after[key(P_DIALS)] == d and src.get(P_DIALS) != d,
               '%s: the DLC movie (%d bytes) in place of the PC\'s; the same %d external images and %d imports, every '
               'one of the PC code\'s %d names, + %s' % (P_DIALS, len(d), di['images'], di['imports'], di['pc_names'],
                                                       ', '.join(DIALS_FUNCS)))
        if menu_audio:
            for p, d in build_menu(menu_audio, pc).items():
                ok(after[key(p)] == d and pc.get(key(p)) is None and xb.get(key(p)) is None,
                   '%s: new key %08x, dlcaudio.py\'s conversion (%d bytes)%s' % (
                       p, key(p), len(d), ', the project DLCMenu with %s' % ', '.join(MENU_EVENTS[1:]) if p.endswith('.fev')
                       else ', FSB4, 7 samples'))

        # mode icon (pre-race game mode instructions)
        if mode_icon:
            for msg in check_icon(src, after, dlc_dir):
                ok(True, msg)

        # dictionaries
        for lang in LANGS + ('StringIDs',):
            p = dict_path(lang)
            b, a = dictadd.Dict(src.get(p)), dictadd.Dict(after[key(p)])
            ba, aa = b.all(), a.all()
            pairs = text_pairs(lang)
            good = all(a.get(k) == t.encode('utf-8') for k, t in pairs)
            ok(good and all(aa.get(hh) == v for hh, v in ba.items()) and len(aa) == len(ba) + len(pairs),
               'dictionary %s: %d entries unchanged, %d new keys resolve' % (lang, len(ba), len(pairs)))
            if lang != 'StringIDs':
                chars = set(''.join(v[0].decode('utf-8', 'replace') for v in ba.values()))
                odd = {c for k, t in pairs for c in t if ord(c) > 126 and c not in chars}
                ok(not odd, 'dictionary %s: every non-ASCII letter of the new texts is used by the PC texts' % lang)
    finally:
        xb.close()
    return n[0]


# ---- CLI ----

def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description='Deadline stage 1: add the mode to an overlay .ark (test build)')
    ap.add_argument('--base', default=BASE_ARK, help='overlay .ark to start from (read only)')
    ap.add_argument('--out', default=OUT_ARK)
    ap.add_argument('--pc', default=PC_RESIDENT, required=not PC_RESIDENT,
                    help='PC resident.ark (read only; default: below GAME_DIR, the game folder)')
    ap.add_argument('--dlc', default=DLC_DIR, help='Deadline package directory')
    ap.add_argument('--extra-seconds', type=float, default=0.0, help='added to every start time (default 0: Xbox values)')
    ap.add_argument('--no-per-track', action='store_true', help='leave the track overlays alone (mode value only)')
    ap.add_argument('--no-pickups', action='store_true', help='leave the 11 .pkps files out (they are only read with '
                    '[DLC] Pickups=1)')
    ap.add_argument('--no-pickup-model', action='store_true', help='leave the converted box model out (the DLL then '
                    'draws its own cubes)')
    ap.add_argument('--no-mode-icon', action='store_true', help='leave the mode icon out (IG_GameModeInstructions.gfx '
                    'frame TimeTrialPlus and the image FE_DLC_event_ICONS in InGame.textures)')
    ap.add_argument('--no-dlc-hud', action='store_true', help='keep the PC\'s DialsInfo.gfx (no timer colours)')
    ap.add_argument('--menu-audio', help='dlcaudio.py\'s output folder of the package\'s AUDIO/RELEASE/MENU (Deadline\'s '
                    'sounds, the DLCMenu bank)')
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    game = os.path.dirname(os.path.dirname(os.path.abspath(a.pc)))
    need(os.path.basename(out).lower() not in PROTECTED, 'refusing to overwrite %s' % out)
    need(not out.lower().startswith('g:') and not out.lower().startswith(game.lower()), 'refusing to write to %s' % out)
    need(out.lower() != os.path.abspath(a.base).lower(), 'out must not be the base archive')

    pc = Ark(a.pc)
    base = Ark(a.base)
    before = {k: base.get(k) for k in base.order}
    base.close()
    print('base %s: %d entries' % (a.base, len(before)))
    after = apply(before, pc, a.dlc, a.extra_seconds, not a.no_per_track, pickups=not a.no_pickups,
                  pickup_model=not a.no_pickup_model, mode_icon=not a.no_mode_icon, dlc_hud=not a.no_dlc_hud,
                  menu_audio=a.menu_audio)
    n = write_ark(out, sorted(after.items()))
    print('written %s: %d entries, %d bytes' % (out, n, os.path.getsize(out)))

    print('-- checks')
    rb = Ark(out)
    raw = open(out, 'rb').read()
    hsize = struct.unpack_from('<I', raw, 4)[0]
    need(not rb.be and rb.order == sorted(rb.order) and len(set(rb.order)) == len(rb.order)
         and hsize == 0x10 + 20 * len(rb.order) and all(rb.ents[k][0] == 0 for k in rb.order),
         'archive layout')
    back = {k: rb.get(k) for k in rb.order}
    rb.close()
    need(back == after, 'archive read back differs from what was built')
    print('ok  archive read back: little-endian, %d entries, keys sorted and unique, all stored, data as built' % len(back))
    cnt = 1 + check(before, back, pc, a.dlc, a.extra_seconds, not a.no_per_track, pickups=not a.no_pickups,
                    pickup_model=not a.no_pickup_model, mode_icon=not a.no_mode_icon, dlc_hud=not a.no_dlc_hud,
                    menu_audio=a.menu_audio)
    sys.path.insert(0, os.path.join(HERE, '..', 'inject'))
    import vark
    va = vark.VirtualArk(a.pc, [out])
    try:
        g = vark.GameArkReader(va.read, va.size)
        bad = [k for k in back if g.read_member(k) != back[k]]
        sample = [k for k in pc.order[::500] if k not in back]
        bad += [k for k in sample if g.read_member(k) != pc.get(k)]
        need(not bad, 'virtual resident.ark read: %d keys differ' % len(bad))
        print('ok  virtual resident.ark (inject/vark.py): every entry of the test overlay and %d base samples read '
              'through GameArkReader equal' % len(sample))
        cnt += 1
    finally:
        va.close()
    mv, x, pcs = movie_check(a.dlc, os.path.join(os.path.dirname(os.path.abspath(a.pc)), 'movies', 'screens', 'quickplay'))
    same = [nm for nm, h in pcs if all(h[f] == x[f] for f in ('rev', 'w', 'h', 'fps', 'flags', 'audio'))]
    need(x['size_ok'] and pcs and len(same) == len(pcs), 'FE_DEADLINE.BIK header differs from the PC mode movies')
    print('ok  FE_DEADLINE.BIK: Bink %s, %dx%d, %d/%d fps, %d frames, flags %d, %d audio tracks, size field = file; '
          'same header fields as %d PC mode movies (%s). Not packed: the exe names the movies (0xD0F998), id 6 shows '
          'FE_QP_Track10.bik' % (x['rev'], x['w'], x['h'], x['fps'][0], x['fps'][1], x['frames'], x['flags'], x['audio'],
                                 len(same), ', '.join(same)))
    cnt += 1
    pc.close()
    print('all %d checks passed' % cnt)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except ModeError as e:
        sys.exit('ERROR: %s' % e)
