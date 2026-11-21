# Deadline (Xbox DLC game mode "TimeTrialPlus") on a converted DLC track: the per-track rules of
# deadline_mode.py (which adds the mode and its 11 base-game tracks to the car pack), as small functions a track
# build uses to list its track in the mode too (../assemble/assemble_track.py: Quarry, which the Onslaught
# package's TrackFiltration.xml lists under TimeTrialPlus). Offline only; nothing here reads the game folder.
#
# The rules (deadline_mode.py, README.md "Per-track start time"):
# - TrackFiltration.xml: one <Track Name=".." SubTrack=".."/> line in the mode's track list (at most 16 tracks
#   per mode: 0x77B620; the quick play track list holds 16 records, 0x486570).
# - Start time: the PC never reads the Xbox RaceSetupOverlay_<sub>_TimeTrialPlus.params; the Xbox
#   /RaceSetupOverlay TimeTrialPlusCountdown of the track goes as /RaceSetup/Logic/Countdown 'Initial Countdown'
#   into the overlay the PC reads, RaceSetupOverlay_<sub>.params, one block appended (extra seconds 0: the Xbox
#   value; the DLL adds [DLC] DeadlineBonus at run time when Pickups=0). Inert for the track's other modes as long
#   as their mode file has Countdown Enabled = false (checked by the caller).
# - Pickups: the .pkps file as the DLC has it, at the Xbox path the DLL reads (pkps.PATH).
# The Xbox <Rival>Time keys (/RaceSetupOverlay/TimeTrialPlus) are no PC tweakables; deadline_mode.py does not
# carry them, and the PC parser drops them where an overlay has them.
import re

NAME = 'TimeTrialPlus'
P_MODE = 'GameLogic/GameModes/TimeTrialPlus.params'
P_OVL = 'GameLogic/Tracks/%s/Params/RaceSetupOverlay_%s.params'
P_XB_OVL = 'GameLogic/Tracks/%s/Params/RaceSetupOverlay_%s_TimeTrialPlus.params'
COUNTDOWN = ('/RaceSetup/Logic/Countdown', "'Initial Countdown'")
ENABLED = ('/RaceSetup/Logic/Countdown', 'Enabled')
XB_COUNTDOWN = ('/RaceSetupOverlay', 'TimeTrialPlusCountdown')
XB_RIVALS = '/RaceSetupOverlay/TimeTrialPlus'
MAX_TRACKS = 16
# the Initial Countdown of the Xbox mode file (deadline_mode.build_mode keeps it, + extra seconds)
XB_MODE_COUNTDOWN = 30.0


class DeadlineTrackError(Exception):
    pass


def need(cond, msg):
    if not cond:
        raise DeadlineTrackError(msg)


def params(text):
    """[(section, key, value)] of a .params text (sections absolute, one key per line; deadline_mode.params)"""
    out, sec = [], None
    for ln in text.split('\r\n'):
        st = ln.strip()
        if st.startswith('/') and st.endswith(':') and ' = ' not in st:
            sec = st.rstrip(':')
        elif st and ln.startswith('\t') and sec:
            m = re.match(r'^\t(.+?) = (.*);[ \t]*$', ln)
            need(m, 'unexpected params line %r' % ln)
            out.append((sec, m.group(1), m.group(2)))
        else:
            need(st == '' or st.startswith('!'), 'unexpected params line %r' % ln)
    return out


def pdict(data):
    """{(section, key): value} of .params bytes or text"""
    text = data.decode('latin-1') if isinstance(data, bytes) else data
    d = {}
    for s, k, v in params(text):
        need((s, k) not in d, 'duplicate key %s %s' % (s, k))
        d[(s, k)] = v
    return d


def number(v):
    m = re.match(r'^(-?[0-9.]+)( \(.*\))?$', v)
    need(m, 'not a number value %r' % v)
    return float(m.group(1)), m.group(2) or ''


def fmt(x):
    s = repr(float(x))
    return s if '.' in s else s + '.0'


def countdown_block(seconds):
    """the block deadline_mode.py appends to a track overlay (byte for byte its countdown_block)"""
    return "\r\n/RaceSetup/Logic/Countdown:\r\n\t'Initial Countdown' = %s (0.0, 3600.0);\r\n" % fmt(seconds)


def xbox_countdown(overlay):
    """(seconds, value text) of an Xbox overlay's TimeTrialPlusCountdown"""
    v = pdict(overlay).get(XB_COUNTDOWN)
    need(v is not None, 'no %s %s' % XB_COUNTDOWN)
    return number(v)[0], v


def rivals(overlay):
    """{rival key: value} of an overlay's /RaceSetupOverlay/TimeTrialPlus section (the Xbox <Rival>Time keys)"""
    return {k: v for (s, k), v in pdict(overlay).items() if s == XB_RIVALS}


def overlay_with_countdown(base, seconds):
    """the overlay bytes the PC reads + the Initial Countdown block -> bytes"""
    need(base.endswith(b'\r\n') and b'/RaceSetup/Logic/Countdown' not in base,
         'overlay does not end with CRLF or has a countdown already')
    return base + countdown_block(seconds).encode('latin-1')


def mode_tracks(tf):
    """{GameMode name: [(track, subtrack)]} of a TrackFiltration.xml (bytes or text), in file order"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(tf if isinstance(tf, bytes) else tf.encode('latin-1'))
    out = {}
    for g in root.iter('GameMode'):
        need(g.get('Name') not in out, 'TrackFiltration.xml: GameMode %s twice' % g.get('Name'))
        out[g.get('Name')] = [(t.get('Name'), t.get('SubTrack')) for t in g.iter('Track')]
    return out


def countdown_enabled(mode_params):
    """the mode file's /RaceSetup/Logic/Countdown Enabled (None: not set)"""
    v = pdict(mode_params).get(ENABLED)
    return None if v is None else v.strip().lower() == 'true'
