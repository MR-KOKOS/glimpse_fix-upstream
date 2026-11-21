# Assemble one Xbox 360 DLC track (default: Quarry from the Onslaught package) into the files the PC game reads,
# using the existing converters (none of them is changed here). Offline only: nothing here starts the game or
# writes to the game folder.
#
#   python -I -X utf8 assemble_track.py [--track Quarry] [--dlc <Onslaught package dir>] [--game <PC data dir>]
#       [--audio <dlcaudio.py output dir>] [--base <car pack overlay .ark>] [--combined <name.ark>]
#       [--names ENGLISH_NTSC=QUARRY,GERMAN=...] [--out <out dir>] [--xlate-cache <file.pkl>] [--no-qp-picture]
#       [--no-dlc-music]
#
# Writes below <out> (default %GLIMPSE_DLC_SCRATCH%\dlc\track_out\quarry), with the game's own folder layout:
#   data\environments\levels\<track>.ark                       level archive (Xbox <TRACK>.ARK)
#   data\<track>powerplays.ark                                 powerplays (Xbox <TRACK>POWERPLAYS.ARK)
#   data\environments\levels\<track>\sectors\<track>.static.ark, <track>.stream.ark   sector archives
#   data\movies\screens\quickplay\<movie>.bik                  quick play movie (Bink, copied as is)
#   data\audio\release\locales\<loc>\<loc>_streamed.fsb        streamed level bank (loose, as on the PC)
#   data\audio\release\<proj>\<proj>.fev, .fsb                 the package's music project (DLC3_Music; loose and
#       listed in StreamedAudioFileList like the PC's Music/Music; the stock exe never loads it, see README_track.md)
#   glimpse_dlc\track_<track>.ark                              resident.ark overlay: the RESIDENT_DLC.ARK keys
#       the PC resident.ark lacks, the level audio banks and XMLs (CRLF), and the merged shared files (directory.txt,
#       UI/Logic/TrackFiltration.xml, Environments/SubTrackLightRigs.txt, Audio/StreamedAudioFileList.txt, the six
#       dictionaries) and, unless
#       --no-qp-picture, the quick play detail screen's track picture: FE_quick_play_detailscreen.gfx with the
#       DLC's frame TEXT_<track>_<sub> (../ui/gfxcars.py) and its image in the Flash set (../ui/flashtex.py)
#   glimpse_dlc\<combined>                                     with --base and --combined: the base pack's entries
#       plus the track overlay in one archive (the recommended way to install both)
#   track_<track>.manifest.tsv, track_<track>.report.txt       one line per entry; checks, merges, DLL data
# --base: the shared files are taken from that overlay (e.g. the Deadline car pack, which replaces directory.txt
# and the dictionaries) instead of the PC resident.ark, so the merged files hold both. When the base carries the
# game mode Deadline (GameLogic/GameModes/TimeTrialPlus.params) and the package lists the track under
# TimeTrialPlus, the track is added to that mode too, with Deadline's per-track data (../mode/deadline_track.py:
# the start time in the track's overlay, the .pkps pickup file as the DLC has it). Exit code 1 on any failed
# check. See README_track.md.
import sys
sys.dont_write_bytecode = True

import argparse
import collections
import os
import pickle
import re
import contextlib
import io
import shutil
import struct
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DLC_TOOLS = os.path.dirname(HERE)
for _p in ('misc', 'tex', 'havok', 'shaders', 'ui', 'inject', 'audio', ''):
    _d = os.path.join(DLC_TOOLS, _p) if _p else DLC_TOOLS
    if _d not in sys.path:
        sys.path.insert(0, _d)

import arkio          # misc/arkio.py: .ark reader / writer, key
import convmisc       # misc/convmisc.py: level data kinds, text, detect()
import convtex        # tex/convtex.py: textures, streamed textures
import convhkx        # havok/convhkx.py
import convshaders    # shaders/convshaders.py (+ ../xenos/xenos2pc.py for IDs the PC lacks)
import convmodel      # convmodel.py: convert_level
import dictadd        # ui/dictadd.py
import flashtex       # ui/flashtex.py: image into a PC Flash texture set
import gfxcars        # ui/gfxcars.py: labelled frames into a PC GFX movie
import vark          # inject/vark.py: virtual resident.ark, GameArkReader
import fsb4          # audio/fsb4.py: FSB4 bank reader
from mdl import Model
sys.path.append(os.path.join(DLC_TOOLS, 'mode'))   # (appended: nothing there shadows the modules above)
import tu4text       # mode/tu4text.py: the official TU4 texts, when a developer's table is set
import deadline_track  # mode/deadline_track.py: Deadline's per-track rules (deadline_mode.py's, for a DLC track)
import pkps          # mode/pkps.py: Deadline pickup files

# work folder for the defaults below (extracted packages in <scratch>\dlc\x_*, outputs in <scratch>\dlc\track_out):
# the environment variable GLIMPSE_DLC_SCRATCH; without it --dlc, --audio and --out must be given
SCRATCH = os.environ.get('GLIMPSE_DLC_SCRATCH') or None
_scr = (lambda *p: os.path.join(SCRATCH, *p) if SCRATCH else None)
DEF_DLC = _scr('dlc', 'x_onslaught')
DEF_GAME = os.path.join(os.environ['GAME_DIR'], 'data') if os.environ.get('GAME_DIR') else None  # else --game
DEF_AUDIO = _scr('dlc', 'audio_work', 'pc_out')
DEF_OUT = _scr('dlc', 'track_out', 'quarry')
LANGS = ('ENGLISH_NTSC', 'GERMAN', 'FRENCH_NTSC', 'ITALIAN', 'SPANISH_NTSC')
DEF_NAMES = 'ENGLISH_NTSC=QUARRY,GERMAN=STEINBRUCH,FRENCH_NTSC=CARRIÈRE,ITALIAN=CAVA,SPANISH_NTSC=CANTERA'
# default names per track (lower case); the packages have no dictionary, the Xbox dictionaries no text for them
DEF_NAMES_BY_TRACK = {
    'quarry': DEF_NAMES,
    'nem_quarry': 'ENGLISH_NTSC=THE ROCK,GERMAN=DER FELS,FRENCH_NTSC=LE ROCHER,ITALIAN=LA ROCCIA,SPANISH_NTSC=LA ROCA',
}
# the package folder per track when --dlc is not given
DEF_DLC_BY_TRACK = {'quarry': DEF_DLC, 'nem_quarry': _scr('dlc', 'x_rock')}
# a one-word PC route whose second title line (TEXT_<t>_<s>_SUBTRACK) is copied per language
ONE_WORD_ROUTE = 'TEXT_GRAVEYARD_A_SUBTRACK'
M32 = 0xFFFFFFFF
SHADER_KINDS = ('fxc', 'shaders', 'shaderinst')
MISC_SAME_SIZE = ('swap4', 'links', 'ailine', 'ilist', 'pool', 'idlist', 'nodes', 'ents', 'gbx', 'copy', 'lenstr')
# quick play detail screen: SetTrack(name, locked, selected) in the movie's ActionScript shows the text and calls
# backgroundImgs.gotoAndStop(name) (or 'locked' / 'movie'); the exe (0x485C30) passes TEXT_<track>_<subtrack>
# (0x77B210 "TEXT_%s_%s" from the track record). backgroundImgs is the sprite with the frames TEXT_Downtown_A, ...;
# the picture is an atlas cell behind a mask (depth 1 clips the picture at depth 3).
QP_GFX = 'ui/frontend/flash/fe_quick_play_detailscreen.gfx'
QP_SET = 'ui/frontend/flash/textures/flash'      # the set FE_QuickPlay_Imgs_SET1, the movie's other track atlas, is in
QP_SPRITE_LABEL = 'TEXT_Downtown_A'               # the first track label of the PC's picture sprite
QP_INSTANCE = 'backgroundImgs'
QP_IMAGE_PREFIX = 'FE_QuickPlay_Imgs_DLC'         # DLC image FE_QuickPlay_Imgs_SET2 -> FE_QuickPlay_Imgs_DLC1
# the PC's UI texture sets (path without extension, streamed); image names are registered and looked up across all
UI_SETS = (('ui/frontend/flash/textures/flash', False), ('ui/ingame/textures/ingame', False),
           ('ui/ingame/textures/ingame1', True), ('ui/ingame/textures/ingame2', True), ('ui/ingame/textures/ingame3', True),
           ('ui/hud/textures/hud', True))


class Checks:
    def __init__(self):
        self.lines, self.errors, self.warnings = [], 0, 0

    def say(self, s=''):
        self.lines.append(s)
        print(s, flush=True)

    def ok(self, cond, what, detail=''):
        self.say('  [%s] %s%s' % ('ok' if cond else 'FAIL', what, (': ' + detail) if detail else ''))
        if not cond:
            self.errors += 1
        return cond

    def warn(self, what):
        self.say('  [note] ' + what)
        self.warnings += 1


# ------------------------------------------------------------------------------------------------- helpers
def read(fn):
    with open(fn, 'rb') as f:
        return f.read()


def find_ci(root, rel):
    """file below root for a relative path, matched case-insensitively (the package's names are upper case)"""
    cur = root
    for part in rel.replace('\\', '/').split('/'):
        if not os.path.isdir(cur):
            return None
        names = {n.lower(): n for n in os.listdir(cur)}
        if part.lower() not in names:
            return None
        cur = os.path.join(cur, names[part.lower()])
    return cur


def suffix_hash(s):
    h = 0
    for c in s.lower().encode('latin-1'):
        h = (h * 31 + c) & M32
    return h


def swap_ext(k, old, new):
    """key of '<stem><new>' from the key of '<stem><old>' (31 is odd, so h*31^n can be undone mod 2^32)"""
    stem = ((k - suffix_hash(old)) * pow(pow(31, len(old), 1 << 32), -1, 1 << 32)) & M32
    return (stem * pow(31, len(new), 1 << 32) + suffix_hash(new)) & M32


def data_order(ark):
    """keys in the order of their data in the file"""
    return [k for _, k in sorted((ark.ents[k][1], k) for k in ark.order)]


def check_model(out, so):
    """structural check of a converted level model: parses as PC, buffers and index lists inside the data, no DEC3N
    left in a declaration"""
    m = Model(out, False)
    data = so[12:] if so is not None else m.chunks[0][2]
    W = lambda o: struct.unpack_from('<I', m.block, o)[0]
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    for i in range(nvb):
        r = vbp + 0x1C * i
        off, stride, count = W(r + 4), W(r + 12), W(r + 16)
        if off + stride * count > len(data):
            return 'vertex buffer beyond data'
    for i in range(nib):
        r = ibp + 0x10 * i
        cnt, word = W(r + 4), W(r + 12)
        if (word >> 2) + cnt * (4 if word & 2 else 2) > len(data):
            return 'index list beyond data'
    for h, els in m.decls:
        for e in els:
            if e[2] == 14:
                return 'DEC3N left in a declaration'
    return None


def sdri_ids_textures(data):
    """(shader ids, texture hashes) of a PC .shaderinst"""
    E = convshaders.endian(data, convshaders.SDRI_MAGIC)
    r = convshaders.Reader(data, E)
    r.raw(12)
    ids, tex = [], []
    for _ in range(r.u32()):
        r.u32()
        r.str()
        ids.append(r.str())
        for _ in range(2):
            n = r.u32()
            r.u32()
            for _ in range(n):
                r.str()
                r.u32()
                r.u32()
                r.raw(16 * r.u32())
        for _ in range(2):
            for _ in range(r.u32()):
                r.str()
                r.u32()
                r.u32()
                tex.append(r.u32())
                r.raw(8 * r.u32())
    return ids, tex


def texture_keys_in(ark):
    """texture hashes of every PC 'SXET' container of an archive (headers only; assemble_car.pc_texture_keys)"""
    sys.path.insert(0, HERE)
    from assemble_car import pc_texture_keys
    return set(pc_texture_keys(ark))


def game_reader(fn):
    f = open(fn, 'rb')

    def rd(pos, n):
        f.seek(pos)
        return f.read(n)
    return vark.GameArkReader(rd, os.path.getsize(fn)), f


# ------------------------------------------------------------------------------------------------- DLC metadata
def parse_directory(text):
    """[(pattern, section, flag, [entries])] (dlcmerge's block rule)"""
    import dlcmerge
    return dlcmerge._split_blocks(text)


def dlc_meta(dlc, track):
    d = read(find_ci(dlc, 'DIRECTORY.TXT')).decode('latin-1')
    blocks = parse_directory(d)
    subs = next((b for b in blocks if b[1].lower() == (track + '_SUBTRACK').lower()), None)
    if subs is None:
        raise SystemExit('DIRECTORY.TXT has no %s_SUBTRACK section' % track)
    td = read(find_ci(dlc, 'TRACKDESCRIPTION.TXT')).decode('latin-1').split()
    # "<id> <track> <subtrack> <subtrack path> <movie path>"
    tf = read(find_ci(dlc, 'UI/LOGIC/TRACKFILTRATION.XML')).decode('latin-1')
    modes, dlc_modes = [], []
    for m in re.finditer(r'<GameMode Name="([^"]+)"([^>]*)>(.*?)</GameMode>', tf, re.S):
        if re.search(r'<Track Name="%s" SubTrack="%s"' % (re.escape(track), re.escape(subs[3][0])), m.group(3)):
            (dlc_modes if 'DLCGameModeIndex' in m.group(2) else modes).append(m.group(1))
    # modes: PC modes listing the track; dlc_modes: DLC-only modes (DLCGameModeIndex) listing it, of which only
    # those a converted mode in the base pack provides are merged (BASE_MODES)
    return {'subtrack_section': subs, 'subtracks': subs[3], 'desc': td, 'modes': modes, 'dlc_modes': dlc_modes,
            'directory': d, 'blocks': blocks}


# DLC-only game modes a track is added to when the --base pack carries the converted mode (its mode file):
# TimeTrialPlus = Deadline (../mode/deadline_mode.py, in the car pack). KingOfTheAir has no PC conversion.
BASE_MODES = {deadline_track.NAME: deadline_track.P_MODE}


def deadline_track_data(xres, track, sub, converted):
    """Deadline's per-track entries for a track the base pack's Deadline lists (deadline_track.py rules):
    {key: (bytes, conv, info)} for the overlay the PC reads (+ Initial Countdown = the Xbox start time) and the
    pickup file at the Xbox path the DLL reads (the RESIDENT_DLC.ARK entry as is, as deadline_mode.py keeps the
    11). converted: {key: converted bytes} of the RESIDENT_DLC.ARK entries the overlay carries."""
    out = {}
    p_ovl = deadline_track.P_OVL % (track, sub)
    p_xb = deadline_track.P_XB_OVL % (track, sub)
    k_ovl = arkio.key(p_ovl)
    base = converted.get(k_ovl)
    xb = xres.get(arkio.key(p_xb))
    if base is None:
        raise SystemExit('Deadline: %s not among the converted RESIDENT_DLC.ARK keys' % p_ovl)
    if xb is None:
        raise SystemExit('Deadline: the package has no %s (the track\'s Xbox start time)' % p_xb)
    sec, txt = deadline_track.xbox_countdown(xb)
    sec_pc, _ = deadline_track.xbox_countdown(base)
    if sec_pc != sec:
        raise SystemExit('Deadline: %s TimeTrialPlusCountdown %s, %s %s' % (p_xb, sec, p_ovl, sec_pc))
    out[k_ovl] = (deadline_track.overlay_with_countdown(base, sec),
                  'convmisc:text + Deadline Initial Countdown %s s (deadline_track.py)' % deadline_track.fmt(sec),
                  {'path': p_ovl, 'xb_path': p_xb, 'seconds': sec, 'xb_value': txt, 'base': base,
                   'rivals': deadline_track.rivals(xb)})
    p_pk = pkps.path(track, sub)
    k_pk = arkio.key(p_pk)
    d = xres.get(k_pk)
    if d is None:
        raise SystemExit('Deadline: the package\'s RESIDENT_DLC.ARK has no %s' % p_pk)
    pk = pkps.parse(d)
    pkps.check(pk, p_pk)
    out[k_pk] = (bytes(d), 'Deadline pickups: the DLC entry as is (deadline_mode.py rule)',
                 {'path': p_pk, 'pickups': pk})
    return out


# ------------------------------------------------------------------------------------------------- conversion
class Converter:
    def __init__(self, game, track, say, cache_fn=None):
        self.game, self.track, self.say = game, track.lower(), say
        self.hkref = convhkx.load_ref()
        self.cache_fn = cache_fn
        if cache_fn and os.path.exists(cache_fn):
            convshaders._XLATE.update(pickle.load(open(cache_fn, 'rb')))
        self._pool = None
        import glob
        self.pc_level_keys = set()
        for fn in glob.glob(os.path.join(game, 'environments', 'levels', '*.ark')):
            self.pc_level_keys |= set(arkio.Ark(fn).order)
        self.vins = {}           # Xbox shader id -> vin, from every .shaders of the track's archives (fallback)
        self.xlate_files = []     # (ark name, key, notes, problems)

    @property
    def pool(self):
        if self._pool is None:
            t0 = time.time()
            res = arkio.Ark(os.path.join(self.game, 'resident.ark'))
            pool = convshaders.Pool(ark=res)
            n = pool.add_ark_scan(res)
            import glob
            others = sorted(glob.glob(os.path.join(self.game, '*powerplays.ark')) +
                            glob.glob(os.path.join(self.game, 'environments', 'levels', '*.ark')))
            for fn in others:
                n += pool.add_ark_scan(arkio.Ark(fn))
            self.say('  shader pool: PC base car bodies + every .fxc/.shaders of resident.ark and %d level/powerplays '
                     'archives: %d fxc / %d shaders entry names, %d shader IDs, %d conflicts (%.0f s)'
                     % (len(others), len(pool.fxc), len(pool.shdr), len(pool.variants), len(pool.conflicts), time.time() - t0))
            self._pool = pool
        return self._pool

    def save_cache(self):
        if self.cache_fn:
            pickle.dump(dict(convshaders._XLATE), open(self.cache_fn, 'wb'))

    def collect_vins(self, xarks):
        for xa in xarks:
            for k in xa.order:
                if xa.ents[k][3] < 8:
                    continue
                xa.f.seek(xa.ents[k][1])
                raw = xa.f.read(min(xa.ents[k][2], 64))
                head = raw[:4] if xa.ents[k][0] == 0 else arkio.lzss_n(raw + bytes(16), 4, 4)
                if head == b'SHDR':
                    for e in convshaders.parse_shdr(xa.get(k))['ents']:
                        self.vins.setdefault(e['id'], e['vin'])

    def convert(self, name, xa, k, stream=None):
        """one Xbox entry -> dict(kind, out (bytes or None if skipped), streams [(key, bytes)], note, conv, problems)"""
        xd = xa.get(k)
        kind, how = convmisc.detect(xd, key=k)
        r = {'key': k, 'kind': kind, 'how': how, 'in': len(xd), 'out': None, 'streams': [], 'note': '', 'probs': [],
             'xdata': xd}
        if kind == 'havok-xml':
            # the PC levels keep two XML packfiles (5d967614, f87a8b07, in all 11 PC level archives): Graveyard's PC
            # copies are the Xbox files with LF -> CRLF. The other XML packfiles exist on the Xbox only.
            if k in self.pc_level_keys:
                out = convmisc.convert(xd, 'text')
                r.update(out=out, conv='convmisc:text (Havok XML packfile kept by the PC levels)')
                if out.replace(b'\r\n', b'\n') != xd.replace(b'\r\n', b'\n'):
                    r['probs'].append('text changed beyond line ends')
                return r
            r['note'] = 'skipped: Xbox-only Havok XML packfile (no PC level has this key)'
            return r
        if kind == 'fxc':
            sk = swap_ext(k, '.fxc', '.shaders')
            shd = xa.get(sk) if sk in xa.ents else None
            if shd is not None and shd[:4] != b'SHDR':
                shd = None
            if shd is None:     # vin lists from any .shaders of the track (the same ID has the same metadata)
                ids = [e['id'] for e in convshaders.parse_fxc(xd)['ents']]
                shd = {'ents': [{'id': i, 'vin': self.vins[i]} for i in ids if i in self.vins]}
            notes = []
            out, probs = convshaders.convert_fxc(self.pool, xd, shd, True, notes)
            r.update(out=out, conv='convshaders.convert_fxc(translate)', probs=probs, xnotes=notes,
                     note='%d entries%s' % (len(convshaders.parse_fxc(out)['ents']),
                                            ', %d IDs translated' % len(notes) if notes else ''))
            self.xlate_files.append((name, k, notes, probs))
            return r
        if kind == 'shaders':
            out, probs = convshaders.convert_shdr(self.pool, xd)
            # an ID the PC lacks has no known variant list; the .fxc side translates it with the same guessed list
            r.update(out=out, conv='convshaders.convert_shdr', probs=[p for p in probs if 'guessed' not in p],
                     guessed=[p.split()[-1] for p in probs if 'guessed' in p],
                     note='%d entries' % len(convshaders.parse_shdr(out)['ents']))
            return r
        if kind == 'shaderinst':
            r.update(out=convshaders.convert_sdri(xd), conv='convshaders.convert_sdri')
            return r
        if kind == 'textures':
            out = convtex.convert(xd)
            convtex.textures(out)
            r.update(out=out, conv='convtex.convert')
            for slot, i in convtex.streamed_slots(xd):
                sk = convtex.stream_key(self.track, slot)
                xb = stream.get(sk) if stream is not None and sk in stream.ents else None
                if xb is None:
                    r['probs'].append('streamed texture slot %d: no .stream.ark entry %08x' % (slot, sk))
                    continue
                r['streams'].append((sk, convtex.convert_streamed(xd, slot, xb), 'convtex.convert_streamed'))
            if r['streams']:
                r['note'] = '%d streamed texture(s) to .stream.ark' % len(r['streams'])
            return r
        if kind == 'model':
            x = Model(xd, True)
            xst = None
            if not x.chunks:
                sk = convmodel.stream_key(k)
                xst = stream.get(sk) if stream is not None and sk in stream.ents else None
                if xst is None:
                    r['probs'].append('chunkless model without .stream.ark entry')
                    return r
            out, so, info = convmodel.convert_level(xd, xst)
            bad = check_model(out, so)
            if bad:
                r['probs'].append('model check: ' + bad)
            r.update(out=out, conv='convmodel.convert_level', note='%d unreferenced vertices zeroed%s' % (
                info['unref'], ', 32-bit index lists' if info['wide'] else ''))
            if so is not None:
                r['streams'].append((convmodel.stream_key(k), so, 'convmodel.convert_level (stream)'))
                r['note'] += ', data in .stream.ark'
            return r
        if kind == 'havok':
            rep = {}
            out = convhkx.convert(xd, self.hkref, rep)
            r.update(out=out, conv='convhkx.convert')
            if rep['holes'] or rep['conflicts']:
                r['probs'].append('%d unswapped bytes, %d conflicts' % (rep['holes'], len(rep['conflicts'])))
            if rep['unknown']:
                r['note'] = 'classes only from the exe: %s' % (rep['unknown'],)
            return r
        out = convmisc.convert(xd, kind)
        r.update(out=out, conv='convmisc:%s (%s)' % (kind, how))
        # byte swaps keep the size; text only changes line ends (LF -> CRLF) and, for path lists, the case
        if kind in MISC_SAME_SIZE and len(out) != len(xd):
            r['probs'].append('size changed %d -> %d' % (len(xd), len(out)))
        if kind == 'text' and out.replace(b'\r\n', b'\n').lower() != xd.replace(b'\r\n', b'\n').lower():
            r['probs'].append('text changed beyond line ends / case')
        if how == 'default':
            r['note'] = 'no rule for this entry: plain 4-byte swap (+strings), unverified'
        return r


# ------------------------------------------------------------------------------------------------- merges
def insert_after(b, anchor_end, text):
    return b[:anchor_end] + text + b[anchor_end:], (anchor_end, len(text))


def merge_directory(base, track, subs, sub_pattern):
    """base directory.txt bytes + TRACKS entry + the <track>_SUBTRACK block after the last _SUBTRACK block (the PC
    lists them in TRACKS order; the PC TRACKS list is sorted case-insensitively). Returns (bytes, [inserts])."""
    t = base.decode('latin-1')
    ins = []
    m = re.search(r'(?m)^-Environments/Levels/\*\r\n\t:TRACKS\r\n\t:1\r\n((?:\t[^\r\n-][^\r\n]*\r\n)*)', t)
    if not m:
        raise SystemExit('directory.txt: TRACKS block not found')
    names = [x.strip() for x in m.group(1).split('\r\n') if x.strip()]
    if track.lower() in (n.lower() for n in names):
        raise SystemExit('directory.txt already lists %s' % track)
    pos = m.start(1)
    for line in m.group(1).split('\r\n')[:-1]:
        if line.strip().lower() > track.lower():
            break
        pos += len(line) + 2
    add = '\t%s\r\n' % track
    t = t[:pos] + add + t[pos:]
    ins.append((pos, add))
    blocks = list(re.finditer(r'(?m)^-Environments/Levels/[^/\r\n]+/Subtracks/\*\r\n\t:[^\r\n]+_SUBTRACK\r\n\t:1\r\n'
                              r'(?:\t[^\r\n]*\r\n)*', t))
    blk = '-%s\r\n\t:%s_SUBTRACK\r\n\t:1\r\n%s' % (sub_pattern, track, ''.join('\t%s\r\n' % s for s in subs))
    pos = blocks[-1].end() if blocks else len(t)
    t = t[:pos] + blk + t[pos:]
    ins.append((pos, blk))
    return t.encode('latin-1'), ins


def merge_trackfiltration(base, track, sub, modes):
    """a <Track Name=.. SubTrack=../> line at the end of the track list of each game mode, indented like the line
    before it. Returns (bytes, [inserts], [modes not found])."""
    t = base.decode('latin-1')
    ins, missing = [], []
    for mode in modes:
        m = re.search(r'<GameMode Name="%s"[^>]*>\s*<Tracks>(.*?)(\r\n)([ \t]*)</Tracks>' % re.escape(mode), t, re.S)
        if not m:
            missing.append(mode)
            continue
        last = m.group(1).rstrip().split('\r\n')[-1]
        indent = re.match(r'[ \t]*', last).group(0)
        add = '%s<Track Name="%s" SubTrack="%s"/>\r\n' % (indent, track, sub)
        pos = m.end(2)
        t = t[:pos] + add + t[pos:]
        ins.append((pos, add))
    return t.encode('latin-1'), ins, missing


def merge_streamed_list(base, entries, anchors=None):
    """NUL-separated list: each entry after the last entry of its folder group (Audio/<Release|Submission>/Locales/),
    or after the last entry starting with anchors[entry] when given"""
    items = base.split(b'\0')
    ins = []
    for e in entries:
        pre = (anchors or {}).get(e) or e.rsplit(b'/', 2)[0] + b'/'
        idx = max((i for i, x in enumerate(items) if x.lower().startswith(pre.lower())), default=len(items) - 2)
        items.insert(idx + 1, e)
        ins.append(e)
    return b'\0'.join(items), ins


def locale_of(track):
    """the audio locale of a level (0x7F1360: cut at the first '_', 'nem_x_y' keeps 'nem_x')"""
    t = track.lower()
    return t.split('_')[0] if not t.startswith('nem_') else '_'.join(t.split('_')[:2])


def to_crlf(d):
    """the PC's level audio XMLs are the Xbox files with CRLF line ends (24 of 24 base-game pairs)"""
    return d.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')


def fev_banks(d):
    """FEV1 header: (project name, [(wave bank name, load mode, max streams, 8-byte bank hash)]); the hash is the
    one at 0x18 of the bank's FSB4 header (load mode 0x80 = stream from disk, 0x200 = load into memory)"""
    if d[:4] != b'FEV1':
        raise ValueError('not an FEV1 file')
    n = struct.unpack_from('<I', d, 16)[0]
    name, p = d[20:20 + n].rstrip(b'\0').decode('latin-1'), 20 + n
    banks = []
    for _ in range(struct.unpack_from('<I', d, p)[0]):
        mode, maxs = struct.unpack_from('<2I', d, p + 4)
        h = d[p + 12:p + 20]
        n = struct.unpack_from('<I', d, p + 20)[0]
        banks.append((d[p + 24:p + 24 + n].rstrip(b'\0').decode('latin-1'), mode, maxs, h))
        p += 24 + n
    return name, banks


def dlc_music_projects(dlc):
    """the package's own music projects, from AUDIOBLACKLIST.TXT: Audio/Release/<X>_Music/<X>_Music.fev (+ .fsb)"""
    fn = find_ci(dlc, 'AUDIOBLACKLIST.TXT')
    if fn is None:
        return []
    out = []
    for p in re.split(r'[\s\0]+', read(fn).decode('latin-1')):   # NUL-separated, like StreamedAudioFileList
        m = re.match(r'Audio/Release/([^/]+_Music)/([^/]+)\.fev$', p, re.I)
        if m and m.group(1).lower() == m.group(2).lower() and m.group(1) not in out:
            out.append(m.group(1))
    return out


LIGHTRIGS_TXT = 'environments/subtracklightrigs.txt'
LIGHTRIG_NAMES = 'LightRigs/LightRigNames.names'


def parse_lightrig_names(data):
    """the level archive's LightRigs/LightRigNames.names: NUL-separated, the count first ('1\\0midday_v02\\0')"""
    items = [x.decode('latin-1') for x in data.split(b'\0') if x]
    if not items or not items[0].isdigit() or int(items[0]) != len(items) - 1:
        raise SystemExit('%s: unexpected content %r' % (LIGHTRIG_NAMES, data[:80]))
    return items[1:]


def merge_lightrigs(base, track, subs, rigs):
    """Environments/SubTrackLightRigs.txt (lines <track>:<subtrack>:<light rig>, blocks separated by blank lines):
    a block with every rig of the level for every subtrack, after the last block in front of the first nem_ line
    (the PC lists the race tracks first, then the nemesis tracks). Without a line for the track the game
    (0x790810) adds the rig 'default', which no level has. Returns (bytes, [inserts])."""
    t = base.decode('latin-1')
    if re.search(r'(?im)^%s:' % re.escape(track), t):
        raise SystemExit('%s already lists %s' % (LIGHTRIGS_TXT, track))
    if track.lower().startswith('nem_'):
        # a nemesis track: its lines go into the nem_ block (one line per track, sorted), before the first nem_
        # line that sorts after it, else after the last nem_ line
        lines = list(re.finditer(r'(?m)^nem_[^\r\n:]*:[^\r\n]*\r\n', t))
        if not lines:
            raise SystemExit('%s: no nem_ block' % LIGHTRIGS_TXT)
        after = [x for x in lines if x.group().split(':')[0].lower() > track.lower()]
        pos = after[0].start() if after else lines[-1].end()
        add = ''.join('%s:%s:%s\r\n' % (track, s, r) for s in subs for r in rigs)
        return (t[:pos] + add + t[pos:]).encode('latin-1'), [(pos, add)]
    m = re.search(r'(?m)^nem_', t)
    head = t[:m.start()] if m else t
    last = None
    for x in re.finditer(r'(?m)^[^\s:][^\r\n]*:[^\r\n]*\r\n', head):
        last = x
    if last is None:
        raise SystemExit('%s: no track line before the nemesis tracks' % LIGHTRIGS_TXT)
    add = '\r\n' + ''.join('%s:%s:%s\r\n' % (track, s, r) for s in subs for r in rigs)
    pos = last.end()
    return (t[:pos] + add + t[pos:]).encode('latin-1'), [(pos, add)]


def removed_inserts(merged, inserts):
    """merged text with the inserted parts taken out again (inserts as (position in the final text, text), applied
    in order)"""
    t = merged.decode('latin-1')
    for pos, add in reversed(inserts):
        # positions refer to the text after all earlier inserts; undo the later ones first
        if t[pos:pos + len(add)] != add:
            return None
        t = t[:pos] + t[pos + len(add):]
    return t.encode('latin-1')


# ------------------------------------------------------------------------------------------------- quick play picture
def qp_build(dlc, label, gfx0, tex0, fn0):
    """the DLC's frame <label> of the quick play picture sprite added to the PC movie (gfxcars.py --sprite-label
    --segment) and its image to the PC Flash set (flashtex.py) under a new name. Returns (gfx, textures,
    filenames, {DLC image: new name}, tool output)."""
    dlc_gfx = find_ci(dlc, 'UI/FRONTEND/FLASH/FE_QUICK_PLAY_DETAILSCREEN.GFX')
    dlc_tex = find_ci(dlc, 'UI/FRONTEND/FLASH/TEXTURES/DLC.TEXTURES')
    if not dlc_gfx or not dlc_tex:
        raise SystemExit('the DLC has no FE_quick_play_detailscreen.gfx / DLC.textures')
    # image names the base has already (an earlier track's picture, e.g. Quarry's FE_QuickPlay_Imgs_DLC1): the
    # registered names of the base Flash set and the image exports of the base movie
    taken = {flashtex.registered(s) for _, s in flashtex.names(convtex.parse(tex0))}
    _, _, gtags = gfx_tags(gfx0)
    taken |= {t[1][9:9 + t[1][8]].decode('ascii') for t in gtags if t[0] == 1001}
    with tempfile.TemporaryDirectory(prefix='assemble_track_') as td:
        pg, og = os.path.join(td, 'pc.gfx'), os.path.join(td, 'fe_quick_play_detailscreen.gfx')
        open(pg, 'wb').write(gfx0)
        tin, tout = os.path.join(td, 'in'), os.path.join(td, 'out')
        os.makedirs(tin)
        open(os.path.join(tin, 'Flash.textures'), 'wb').write(tex0)
        open(os.path.join(tin, 'Flash.filenames'), 'wb').write(fn0)
        explicit = []
        for _ in range(2):
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                images = gfxcars.main([pg, dlc_gfx, og, '--sprite-label', QP_SPRITE_LABEL, '--segment',
                                       '--image-prefix', QP_IMAGE_PREFIX] + explicit + ['%s=%s' % (label, label)])
            if not images or not set(images.values()) & taken:
                break
            # numbered from 1 by gfxcars: give every image the next name neither the base nor this run has
            explicit, n, used = [], 1, set(taken)
            for old in images:
                while '%s%d' % (QP_IMAGE_PREFIX, n) in used:
                    n += 1
                used.add('%s%d' % (QP_IMAGE_PREFIX, n))
                explicit += ['--image', '%s=%s%d' % (old, QP_IMAGE_PREFIX, n)]
        else:
            raise SystemExit('quick play picture: image names %s taken' % sorted(set(images.values()) & taken))
        with contextlib.redirect_stdout(log):
            if images:
                flashtex.main([os.path.join(tin, 'Flash.textures'), os.path.join(tin, 'Flash.filenames'), dlc_tex, tout]
                              + ['%s=%s' % kv for kv in images.items()])
        if images:
            tex1, fn1 = read(os.path.join(tout, 'Flash.textures')), read(os.path.join(tout, 'Flash.filenames'))
        else:
            tex1, fn1 = tex0, fn0
        return read(og), tex1, fn1, images, log.getvalue()


def gfx_tags(data):
    with tempfile.TemporaryDirectory(prefix='assemble_track_') as td:
        fn = os.path.join(td, 'x.gfx')
        open(fn, 'wb').write(data)
        return gfxcars.load(fn)


def shape_sig(defs, code, b, rename):
    """a shape's bytes without its id, every bitmap id replaced by the image's (export name renamed, w, h)"""
    b = bytearray(b[2:])
    p = gfxcars.rect_end(b, 0)
    rgb = 4 if code == 32 else 3
    n = b[p]; p += 1
    if n == 0xFF and code != 2:
        n = struct.unpack_from('<H', b, p)[0]; p += 2
    imgs = []
    for _ in range(n):
        t = b[p]; p += 1
        if t == 0x00:
            p += rgb
        elif t in (0x10, 0x12, 0x13):
            p = gfxcars.matrix_end(b, p); ng = b[p] & 0x0F; p += 1 + ng * (1 + rgb) + (2 if t == 0x13 else 0)
        elif t in (0x40, 0x41, 0x42, 0x43):
            bid = struct.unpack_from('<H', b, p)[0]
            if bid != 0xFFFF:
                ib = defs[bid][1]
                nm = ib[9:9 + ib[8]].decode('ascii')
                imgs.append((rename.get(nm, nm),) + struct.unpack_from('<HH', ib, 4) + (ib[2],))
                struct.pack_into('<H', b, p, 0)
            p = gfxcars.matrix_end(b, p + 2)
        else:
            raise ValueError('shape fill style %#x' % t)
    return code, bytes(b), tuple(imgs)


def qp_leaves(data, label, rename=None):
    """what frame <label> of the picture sprite shows, resolved through nested one-frame sprites: [(path of (depth,
    matrix, colour transform, clip depth), leaf signature)]; images named as in <rename>"""
    rename = rename or {}
    _, _, tags = gfx_tags(data)
    defs = {gfxcars.char_id(t[0], t[1]): (t[0], t[1]) for t in tags if gfxcars.char_id(t[0], t[1]) is not None}
    sp = gfxcars.labelled_sprites(tags, QP_SPRITE_LABEL)
    if len(sp) != 1:
        raise SystemExit('%d picture sprites' % len(sp))
    b = tags[sp[0]][1]
    fr = gfxcars.frames_of(gfxcars.read_tags(b, 4, len(b)))
    disp = gfxcars.display_lists(fr)[[f[0] for f in fr].index(label)]
    out = []

    def walk(disp, path):
        for dpt, o in sorted(disp.items()):
            code, cb = defs[o['char']]
            here = path + ((dpt, o['matrix'], o['cx'], o['clip']),)
            if code == 39:
                sfr = gfxcars.frames_of(gfxcars.read_tags(cb, 4, len(cb)))
                if len(sfr) != 1:
                    raise SystemExit('nested sprite %d has %d frames' % (o['char'], len(sfr)))
                walk(gfxcars.display_lists(sfr)[0], here)
            elif code in (2, 22, 32):
                out.append((here, shape_sig(defs, code, cb, rename)))
            else:
                out.append((here, (code, cb[2:])))
    walk(disp, ())
    return out


def qp_checks(C, qp, merges, shared_src, a, pc_res, base, res, tex_pc):
    say, ok = C.say, C.ok
    label, images = qp['label'], qp['images']
    say()
    say('Quick play detail screen %s (SetTrack -> %s.gotoAndStop("%s"))' % (QP_GFX, QP_INSTANCE, label))
    for line in qp['log'].strip().split('\n'):
        if not line.startswith('written '):   # (temporary files)
            say('  | ' + line)
    g0, g1, _ = merges[QP_GFX]
    xg = read(find_ci(a.dlc, 'UI/FRONTEND/FLASH/FE_QUICK_PLAY_DETAILSCREEN.GFX'))
    _, start1, tags1 = gfx_tags(g1)
    _, _, tags0 = gfx_tags(g0)
    _, _, xtags = gfx_tags(xg)
    rt = g1[:start1] + b''.join(gfxcars.write_tag(*t) for t in tags1)
    ok(rt == g1 and struct.unpack_from('<I', g1, 4)[0] == len(g1),
       'round-trips: re-read and re-written tag by tag gives the same %d bytes; header length right (%s: %d bytes)'
       % (len(g1), shared_src[QP_GFX], len(g0)))
    sp0, sp1, spx = (gfxcars.labelled_sprites(t, QP_SPRITE_LABEL) for t in (tags0, tags1, xtags))
    nnew = len(tags1) - len(tags0)
    ok(len(sp0) == len(sp1) == len(spx) == 1 and tags1[:sp0[0]] == tags0[:sp0[0]] and sp1[0] == sp0[0] + nnew and
       all(tags1[i + nnew] == tags0[i] for i in range(sp0[0], len(tags0)) if i != sp0[0]),
       'every PC tag kept in order (incl. all ActionScript); %d new definitions in front of the picture sprite' % nnew)
    sid = lambda tags, i: struct.unpack_from('<H', tags[i][1], 0)[0]
    ok(instance_char(tags0, QP_INSTANCE) == {sid(tags0, sp0[0])} and instance_char(tags1, QP_INSTANCE) == {sid(tags1, sp1[0])}
       and instance_char(xtags, QP_INSTANCE) == {sid(xtags, spx[0])},
       'the sprite with the frames %s, ... is the one placed as %s (PC %d, DLC %d)' % (
           QP_SPRITE_LABEL, QP_INSTANCE, sid(tags0, sp0[0]), sid(xtags, spx[0])))
    fr = lambda tags, i: gfxcars.frames_of(gfxcars.read_tags(tags[i][1], 4, len(tags[i][1])))
    f0, f1, fx = fr(tags0, sp0[0]), fr(tags1, sp1[0]), fr(xtags, spx[0])
    xl = [f[0] for f in fx]
    if label not in xl:
        ok(False, 'the DLC picture sprite has a frame %s' % label)
        return
    j = xl.index(label) + 1
    while j < len(fx) and fx[j][0] is None:
        j += 1
    seg = fx[xl.index(label):j]
    nf = struct.unpack_from('<H', tags1[sp1[0]][1], 2)[0]
    ok(nf == len(f1) == len(f0) + len(seg) and [f[1] for f in f1[:len(f0)]] == [f[1] for f in f0] and
       [f[0] for f in f1[len(f0):]] == [label] + [None] * (len(seg) - 1) and
       [f[1] for f in f1[len(f0) + 1:]] == [f[1] for f in seg[1:]] and
       [t for t in f1[len(f0)][1] if t[0] == 12] == [t for t in seg[0][1] if t[0] == 12],
       'picture sprite %d: %d -> %d frames: the PC frames unchanged, + %s and the %d frames after it as in the DLC '
       '(frame scripts: %s)' % (sid(tags1, sp1[0]), len(f0), nf, label, len(seg) - 1, ' / '.join(
           '%d:%s' % (k, ','.join(t[1].hex() for t in f[1] if t[0] == 12)) for k, f in enumerate(seg) if
           any(t[0] == 12 for t in f[1]))))
    defined = {gfxcars.char_id(t[0], t[1]) for t in tags1} | set(gfxcars.imports(tags1))
    used = {gfxcars.Place(t[1]).char for f in f1[len(f0):] for t in f[1] if t[0] == 26} - {None}
    gfxcars.display_lists(f1)
    ok(used <= defined, 'every character the new frames place is defined (%d); the display lists parse' % len(used))
    want, got = qp_leaves(xg, label, images), qp_leaves(g1, label)
    desc = '; '.join('depth %s: %s' % (' > '.join(str(p[0]) + ('' if p[3] is None else ' clip %d' % p[3]) for p in path),
                                         ', '.join('%s %dx%d' % i[:3] for i in sig[2]) if len(sig) == 3 and sig[2] else
                                         'tag %d (%d bytes)' % (sig[0], len(sig[1]))) for path, sig in got)
    ok(want == got and any(len(s) == 3 and s[2] for _, s in got),
       '%s shows what the DLC frame shows (nested sprites resolved: depths, matrices, colour transforms, clip depths, '
       'shapes; images renamed %s): %s' % (label, ', '.join('%s -> %s' % kv for kv in images.items()), desc))
    nem = [l for l in xl if l and l.startswith('TEXT_') and l not in [f[0] for f in f0] and l != label]
    if nem:
        say('  not added: DLC frame(s) %s: frames of other DLC tracks, added by the build of that track (assemble_track.py '
            '--track <name>, on top of this one with --base) from its own package' % ', '.join(nem))
    if not images:
        return
    say()
    say('Flash set %s.textures / .filenames (%s)' % (QP_SET, shared_src[QP_SET + '.textures']))
    t0, t1, _ = merges[QP_SET + '.textures']
    c0, c1 = convtex.parse(t0), convtex.parse(t1)
    n0, n1 = flashtex.names(c0), flashtex.names(c1)
    rule = flashtex.order_rule(n0)
    sk = flashtex.name_key(rule or 'upper')
    ok(rule is not None and [sk(s) for _, s in n1] == sorted(sk(s) for _, s in n1),
       '%d textures, in the base file\'s name order (%s-case compare)' % (len(n1), rule))
    added = [s for _, s in n1 if s not in {x for _, x in n0}]
    pidx = {s: i for i, (_, s) in enumerate(n0)}
    keep = all(c1['entries'][i] == c0['entries'][pidx[s]] and c1['blobs'][i] == c0['blobs'][pidx[s]] and n1[i][0] == n0[pidx[s]][0]
               for i, (_, s) in enumerate(n1) if s in pidx)
    ok(keep and len(n1) == len(n0) + len(added) and sorted(flashtex.registered(s) for s in added) == sorted(images.values()),
       'the %d textures of the base set unchanged (entry, data, key); added: %s' % (len(n0), ', '.join(added)))
    nkeys = {s: k for k, s in n1}
    ok(len(set(nkeys.values())) == len(n1), 'texture keys unique in the set')
    # every texture container the game has resident with this overlay: PC resident.ark, base pack, the track's keys
    others = set(tex_pc)
    if base is not None:
        others |= texture_keys_in(base)
    for r in res['res']:
        if r['kind'] == 'textures' and r['out'] is not None:
            others |= {k for k, _, _ in convtex.textures(r['out'])}
    ok(not [s for s in added if nkeys[s] in others],
       'added texture keys (%s) in no other texture container of the PC resident.ark%s or the track\'s resident keys (%d keys)'
       % (', '.join('%08x' % nkeys[s] for s in added), ', the base pack' if base is not None else '', len(others)))
    regs = set()
    for sp, streamed in UI_SETS:
        for src in ([base] if base is not None else []) + [pc_res]:
            if arkio.key(sp + '.textures') in src.ents:
                d = src.get(sp + '.textures')
                nm = flashtex.streamed_groups(d, '<')[2] if streamed else convtex.parse(d)['names']
                regs |= {flashtex.registered(s) for _, s in flashtex.names({'names': nm})}
    ok(not [s for s in added if flashtex.registered(s) in regs or flashtex.registered(s).lower() in {x.lower() for x in regs}],
       'added names (as registered, without _DXT5/_RAW; 0x5334B0 looks them up across all sets) new to every UI texture set '
       '(%d names in %d sets%s); no PC image replaced' % (len(regs), len(UI_SETS), ', PC + base pack' if base is not None else ''))
    fl0 = [s for s in merges[QP_SET + '.filenames'][0].split(b'\x00') if s]
    fl1 = [s for s in merges[QP_SET + '.filenames'][1].split(b'\x00') if s]
    ok(fl1[:len(fl0)] == fl0 and sorted(s.decode() for s in fl1[len(fl0):]) == sorted(flashtex.registered(s) for s in added),
       'Flash.filenames: the base\'s %d names + %s' % (len(fl0), ', '.join(s.decode() for s in fl1[len(fl0):])))
    xs = {s.rsplit('_', 1)[0]: (e, b) for _, s, e, b in flashtex.xbox_set(read(find_ci(a.dlc, 'UI/FRONTEND/FLASH/TEXTURES/DLC.TEXTURES')))}
    pfmts = collections.Counter(struct.unpack_from('<I', e, 12)[0] for e in c0['entries'])
    for old, new in images.items():
        s = next(x for x in added if flashtex.registered(x) == new)
        i = [x for _, x in n1].index(s)
        e, b = c1['entries'][i], c1['blobs'][i]
        xe, xb = xs[old]
        fmt, _, mips, w, h = struct.unpack_from('<5I', e, 12)
        same = b[4 + 128:] == xb[4 + 128:] and (w, h) == struct.unpack_from('<2I', xe, 24)
        ok(same and pfmts[fmt] and b[4 + 84:4 + 88] == (b'DXT5' if fmt == 0x25 else b[4 + 84:4 + 88]) and mips == 1,
           '%s: %dx%d, format %#x (%d other images of the set have it), %d levels; image data equal to the DLC\'s %s '
           '(converted, %d bytes)' % (s, w, h, fmt, pfmts[fmt], mips, old, len(b) - 4 - 128))


def instance_char(tags, name):
    """characters placed under an instance name, anywhere in the movie"""
    out = set()
    for t in tags:
        for u in ([t] if t[0] == 26 else gfxcars.read_tags(t[1], 4, len(t[1])) if t[0] == 39 else []):
            if u[0] == 26:
                pl = gfxcars.Place(u[1])
                if pl.name == name:
                    out.add(pl.char)
    return out


# ------------------------------------------------------------------------------------------------- main
def audio_checks(C, a, g, pc_res, loc, ov, aud, aud_src, sfn, music, music_rel, loose, out, lvl=(True, True)):
    say = C.say
    say()
    say('Audio (level banks and locale XMLs in the overlay; streamed level bank and music project loose)')
    ovd = {k: d for k, d, _, _ in ov}
    xmls = [r for r in aud if r.endswith('.xml')]
    bad = []
    for r in xmls:
        src = read(aud_src[r]) if r in aud_src else None
        pk = find_ci(a.dlc, r)
        d = ovd.get(arkio.key(r))
        if src is None or d is None or pk is None or read(pk) != src or d != to_crlf(src) or b'\n' in d.replace(b'\r\n', b''):
            bad.append(r)
    C.ok(xmls and not bad, '%d locale XMLs (Audio/Locales/%s/*.xml): the package files with CRLF line ends, CRLF only' % (
        len(xmls), loc), ', '.join(bad))
    pcd = parse_directory(pc_res.get('directory.txt').decode('latin-1'))
    locs = sorted({locale_of(x) for x in next(b for b in pcd if b[1] == 'TRACKS')[3]})
    n = lone = 0
    for lc in locs:
        for s in ('', '_ambient', '_reverb', '_swooshes'):
            d = pc_res.get('audio/locales/%s/%s_audio%s.xml' % (lc, lc, s))
            if d is not None:
                n += 1
                lone += b'\n' in d.replace(b'\r\n', b'')
    C.ok(n > 0 and not lone, 'the PC\'s own level audio XMLs use CRLF only (%d files of the locales %s)' % (n, ', '.join(locs)))

    def pc_modes(suffix):
        ms = set()
        for lc in locs:
            d = pc_res.get('audio/release/locales/%s/%s%s.fev' % (lc, lc, suffix))
            if d is not None:
                ms |= {(m, s) for _, m, s, _ in fev_banks(d)[1]}
        return ms
    lv = 'audio/release/locales/%s/%s' % (loc, loc)
    pairs = []
    if lvl[0]:
        pairs.append(('level bank', lv, ovd.get(arkio.key(lv + '.fev')), ovd.get(arkio.key(lv + '.fsb')), loc,
                      pc_modes(''), 'the PC level banks'))
    if lvl[1]:
        pairs.append(('streamed level bank', lv + '_streamed', ovd.get(arkio.key(lv + '_streamed.fev')),
                      read(sfn) if sfn else None, loc + '_Streamed', pc_modes('_streamed'), 'the PC streamed level banks'))
    if not lvl[0] and not lvl[1]:
        C.say('  no level banks in the package (as the PC\'s nem_storm, nem_downtown, nem_warehouse, nem_graveyard: '
              '0x7F1360 loads Locales/%s/%s only when its .fev exists)' % (loc, loc))
    pc_music = os.path.join(g, 'audio', 'release', 'music', 'music.fev')
    mmodes = {(m, s) for _, m, s, _ in fev_banks(read(pc_music))[1]} if os.path.exists(pc_music) else set()
    mfile = {os.path.relpath(dst, os.path.join(out, 'data')).replace('\\', '/'): dst for dst, _, _ in loose}
    for mp in music:
        rel = 'audio/release/%s/%s' % (mp.lower(), mp.lower())
        fe, fs = mfile.get(rel + '.fev'), mfile.get(rel + '.fsb')
        pairs.append(('music project', rel, read(fe) if fe else None, read(fs) if fs else None, mp, mmodes,
                      'the PC\'s Music/Music'))
        pk = find_ci(a.dlc, rel + '.fev')
        C.ok(fe is not None and pk is not None and read(fe) == read(pk),
             '%s.fev: the package file unchanged (FEV1 0x3D, the same format on both platforms)' % mp)
    for what, rel, fev, fsb, proj, modes, mwhat in pairs:
        if fev is None or fsb is None:
            C.ok(False, '%s %s: .fev and .fsb present' % (what, rel))
            continue
        name, banks = fev_banks(fev)
        bk = fsb4.FSB4(fsb)
        xf = find_ci(a.dlc, rel + '.fsb')
        xb = fsb4.FSB4(read(xf)) if xf else None
        okb = len(banks) == 1 and name.lower() == proj.lower() and banks[0][0].lower() == proj.lower()
        C.ok(okb and banks[0][3] == fsb[0x18:0x20] and (banks[0][1], banks[0][2]) in modes,
             '%s %s: project %s, one wave bank %s, load mode 0x%X / %d streams as %s, bank hash %s = the FSB\'s' % (
                 what, rel.split('/')[-1], name, banks[0][0] if banks else '-', banks[0][1] if banks else 0,
                 banks[0][2] if banks else 0, mwhat, banks[0][3].hex() if banks else '-'))
        mp2 = all(fsb4.codec(s.mode) == 'MPEG' and s.mode & fsb4.M['MPEG_L2'] for s in bk.samples)
        same = xb is not None and len(xb.samples) == len(bk.samples) and all(
            (s.channels, s.freq) == (x.channels, x.freq) for s, x in zip(bk.samples, xb.samples))
        C.ok(bk.version == 0x40000 and mp2 and same,
             '%s %s.fsb: FSB4, %d samples, all MPEG Layer II, order, channels and rates as the Xbox bank (the FEV '
             'names its waves by index)' % (what, rel.split('/')[-1], len(bk.samples)))
        if same:
            nd = ['%d %s (Xbox %s)' % (i, s.sname, x.sname) for i, (s, x) in enumerate(zip(bk.samples, xb.samples))
                  if s.sname != x.sname]
            if nd:
                C.warn('%s.fsb: %d sample name(s) differ from the Xbox bank, from samples dlcaudio.py takes from a PC '
                       'base bank because their XMA data is the same: %s' % (rel.split('/')[-1], len(nd), ', '.join(nd)))
    taken = [dst for dst, _, _ in loose if os.path.exists(os.path.join(g, os.path.relpath(dst, os.path.join(out, 'data'))))]
    C.ok(not taken, 'no loose file is taken by a PC file below data (the DLL redirect only serves missing files)',
         ', '.join(taken))
    if music:
        C.warn('%s is loaded by no stock code: the exe plays Music/<InGame_Track_xx>/RaceSection0N from the PC\'s '
               'Music/Music on every track (README_track.md, "Music")' % ', '.join(music))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--track', default='Quarry')
    ap.add_argument('--dlc', default=None, help='the package folder (default per track: x_onslaught, x_rock)')
    ap.add_argument('--game', default=DEF_GAME)
    ap.add_argument('--audio', default=DEF_AUDIO)
    ap.add_argument('--base', default=None)
    ap.add_argument('--combined', default=None)
    ap.add_argument('--names', default=None, help='per language names (default per track, see DEF_NAMES_BY_TRACK)')
    ap.add_argument('--out', default=DEF_OUT)
    ap.add_argument('--xlate-cache', default=None)
    ap.add_argument('--no-qp-picture', action='store_true',
                    help='leave out the quick play detail screen picture (frame + image)')
    ap.add_argument('--no-dlc-music', action='store_true',
                    help='leave out the package\'s music project (DLC3_Music: loose bank + StreamedAudioFileList lines)')
    a = ap.parse_args(argv)
    if a.dlc is None:
        a.dlc = DEF_DLC_BY_TRACK.get(a.track.lower(), DEF_DLC)
    names_given = a.names is not None
    if a.names is None:
        if a.track.lower() not in DEF_NAMES_BY_TRACK and tu4text.load() is None:
            raise SystemExit('no default names for %s: give --names' % a.track)
        a.names = DEF_NAMES_BY_TRACK.get(a.track.lower(), '')
    for opt in ('dlc', 'audio', 'out'):
        if not getattr(a, opt):
            raise SystemExit('--%s not given and no default (set GLIMPSE_DLC_SCRATCH or give --%s)' % (opt, opt))
    if not a.game:
        raise SystemExit('--game not given: the game\'s data folder (or set GAME_DIR to the Split/Second game folder)')
    t0 = time.time()
    C = Checks()
    say = C.say
    T, t = a.track, a.track.lower()
    out = os.path.abspath(a.out)
    g = os.path.abspath(a.game)
    gd = os.path.normcase(os.path.dirname(g)).rstrip('\\/')   # --game is <game>\data: the whole game folder is refused
    if os.path.normcase(out) == gd or os.path.normcase(out).startswith(gd + os.sep):
        raise SystemExit('refusing to write into the game folder: %s' % out)
    meta = dlc_meta(a.dlc, T)
    sub = meta['subtracks'][0]
    say('Track %s, subtracks %s; TRACKDESCRIPTION: %s' % (T, meta['subtracks'], ' '.join(meta['desc'])))
    say('Game modes listing it in the DLC TrackFiltration.xml: %s' % ', '.join(meta['modes']))

    U = T.upper()
    xfn = {'main': find_ci(a.dlc, U + '.ARK'), 'pp': find_ci(a.dlc, U + 'POWERPLAYS.ARK'),
           'static': find_ci(a.dlc, 'ENVIRONMENTS/LEVELS/%s/SECTORS/%s.STATIC.ARK' % (U, U)),
           'stream': find_ci(a.dlc, 'ENVIRONMENTS/LEVELS/%s/SECTORS/%s.STREAM.ARK' % (U, U)),
           'res': find_ci(a.dlc, 'RESIDENT_DLC.ARK')}
    for n, f in xfn.items():
        if not f:
            raise SystemExit('missing Xbox archive: %s' % n)
    xa = {n: arkio.Ark(f) for n, f in xfn.items()}
    pc_res_fn = os.path.join(g, 'resident.ark')
    pc_res = arkio.Ark(pc_res_fn)
    base = arkio.Ark(a.base) if a.base else None
    # DLC-only modes listing the track: merged when the base pack carries the converted mode, else left out
    base_modes = [m for m in meta['dlc_modes'] if m in BASE_MODES and base is not None
                  and arkio.key(BASE_MODES[m]) in base.ents]
    say('DLC-only game modes listing it: %s; added (the base pack carries the converted mode): %s; left out: %s' % (
        ', '.join(meta['dlc_modes']) or 'none', ', '.join(base_modes) or 'none',
        ', '.join(m for m in meta['dlc_modes'] if m not in base_modes) or 'none'))
    deadline = deadline_track.NAME in base_modes
    names = dict(x.split('=', 1) for x in a.names.split(',')) if a.names else {}
    paths = {'main': os.path.join(out, 'data', 'environments', 'levels', t + '.ark'),
             'pp': os.path.join(out, 'data', t + 'powerplays.ark'),
             'static': os.path.join(out, 'data', 'environments', 'levels', t, 'sectors', t + '.static.ark'),
             'stream': os.path.join(out, 'data', 'environments', 'levels', t, 'sectors', t + '.stream.ark'),
             'overlay': os.path.join(out, 'glimpse_dlc', 'track_%s.ark' % t)}
    for p in paths.values():
        os.makedirs(os.path.dirname(p), exist_ok=True)

    # ---- 1. convert the level archives
    say()
    say('Conversion')
    cv = Converter(g, T, say, a.xlate_cache)
    cv.collect_vins([xa['main'], xa['pp'], xa['res']])
    res = {}
    for n in ('main', 'pp', 'static'):
        res[n] = [cv.convert(n, xa[n], k, xa['stream']) for k in data_order(xa[n])]
    # RESIDENT_DLC.ARK: only the keys the PC resident.ark lacks; the others are copies of base content, kept as on
    # the PC. Their shader files are converted anyway as a check (they must come out as the PC files).
    res_new = [k for k in xa['res'].order if k not in pc_res.ents]
    res['res'] = [cv.convert('res', xa['res'], k, None) for k in data_order(xa['res']) if k in set(res_new)]
    copies = collections.Counter()
    for k in xa['res'].order:
        if k in pc_res.ents:
            xd = xa['res'].get(k)
            kind = convmisc.detect(xd, key=k)[0]
            if kind in SHADER_KINDS:
                o = cv.convert('res-copy', xa['res'], k)
                p = pc_res.get(k)
                if o['out'] == p:
                    st = 'identical to the PC file'
                elif kind == 'shaderinst' and len(o['out']) == len(p) and \
                        convshaders.sdri_shader_ids(o['out']) == convshaders.sdri_shader_ids(p):
                    st = 'same size and shader IDs as the PC file, other words differ'
                else:
                    st = 'DIFFERENT'
                copies[(kind, st)] += 1
            else:
                copies[(kind, 'not converted')] += 1
    cv.save_cache()

    # ---- 2. write the level archives
    files = {}
    for n in ('main', 'pp', 'static'):
        files[n] = [(r['key'], r['out']) for r in res[n] if r['out'] is not None]
    files['static'].sort()
    # keys every PC level / powerplays archive has: one the Xbox file lacks is copied from the PC when it has the
    # same bytes in all of them (level independent), at the end of the data; others are reported
    import glob
    pc_common, pc_added = {}, {'main': [], 'pp': []}
    for n, pat in (('main', os.path.join(g, 'environments', 'levels', '*.ark')), ('pp', os.path.join(g, '*powerplays.ark'))):
        arks = [x for x in (arkio.Ark(f) for f in sorted(glob.glob(pat))) if len(x.order) > 50]
        common = set.intersection(*[set(x.order) for x in arks])
        pc_common[n] = (len(arks), common)
        have = {k for k, _ in files[n]}
        for k in sorted(common - have):
            ds = [x.get(k) for x in arks]
            if all(d == ds[0] for d in ds):
                files[n].append((k, ds[0]))
                pc_added[n].append(k)
    stream =sorted((sk, d) for n in ('main', 'pp', 'static') for r in res[n] for sk, d, _ in r['streams'])
    files['stream'] = stream
    for n, pad in (('main', 0x800), ('pp', 0x800), ('static', 0), ('stream', 0)):
        arkio.write_ark(paths[n], files[n], pad=pad)

    # ---- 3. resident overlay: new keys, audio, merged shared files
    # A RESIDENT_DLC.ARK key the base pack has already (another track's package brings the same path, e.g. the
    # Quarry camera / spline files nem_quarry shares): the base's bytes are kept, so the track built first is not
    # changed; keys whose converted bytes differ from the base's are reported (on the Xbox each level reads its own
    # package's RESIDENT_DLC.ARK, on the PC one merged resident.ark serves both).
    # Deadline (base_modes): the track's overlay with its start time, its pickup file as the DLC has it
    dl_data = {}
    if deadline:
        try:
            dl_data = deadline_track_data(xa['res'], T, sub, {r['key']: r['out'] for r in res['res']
                                                              if r['out'] is not None})
        except (deadline_track.DeadlineTrackError, pkps.PkpsError) as e:
            raise SystemExit('Deadline: %s' % e)
        for r in res['res']:
            if r['key'] in dl_data:
                r['out'], r['conv'] = dl_data[r['key']][0], dl_data[r['key']][1]
        if set(dl_data) - {r['key'] for r in res['res'] if r['out'] is not None}:
            raise SystemExit('Deadline: an entry of the track is not among the converted RESIDENT_DLC.ARK keys')
    in_base_same, in_base_diff = [], []
    ov = []
    for r in res['res']:
        if r['out'] is None:
            continue
        d = r['out']
        if base is not None and r['key'] in base.ents:
            bd = base.get(r['key'])
            (in_base_same if bd == d else in_base_diff).append(r['key'])
            d = bd
        ov.append((r['key'], d, 'RESIDENT_DLC.ARK %08x' % r['key'], r['conv'] if d is r['out'] else
                   'base %s kept (key in the base pack)' % os.path.basename(a.base)))
    shared_src = {}

    def shared(path):
        if base is not None and arkio.key(path) in base.ents:
            shared_src[path] = 'base ' + os.path.basename(a.base)
            return base.get(path)
        shared_src[path] = 'PC resident.ark'
        return pc_res.get(path)
    merges = {}
    sub_pat = 'Environments/Levels/%s/Subtracks/*' % T
    d0 = shared('directory.txt')
    d1, ins = merge_directory(d0, T, meta['subtracks'], sub_pat)
    merges['directory.txt'] = (d0, d1, ins)
    f0 = shared('ui/logic/trackfiltration.xml')
    f1, fins, fmiss = merge_trackfiltration(f0, T, sub, meta['modes'] + base_modes)
    merges['ui/logic/trackfiltration.xml'] = (f0, f1, fins)
    loc = locale_of(t)
    # the level's own banks, when the package has them (Quarry: quarry + quarry_Streamed; nem_quarry has none,
    # like the PC's nem_* tracks: 0x7F1360 loads a locale bank only when its .fev exists)
    lvl_bank = find_ci(a.dlc, 'audio/release/locales/%s/%s.fev' % (loc, loc)) is not None
    lvl_streamed = find_ci(a.dlc, 'audio/release/locales/%s/%s_streamed.fev' % (loc, loc)) is not None
    aud_entries = [('Audio/%s/Locales/%s/%s_Streamed.fsb' % (rs, loc, loc)).encode('latin-1')
                   for rs in ('Release', 'Submission')] if lvl_streamed else []
    s0 = shared('audio/streamedaudiofilelist.txt')
    # the package's music project: streamed and loose like the PC's Music/Music, listed after the Music lines;
    # a project the base already lists (another track's package brings the same one) is not shipped again
    music, music_in_base = [], []
    for mp in ([] if a.no_dlc_music else dlc_music_projects(a.dlc)):
        line = ('Audio/Release/%s/%s.fev' % (mp, mp)).lower().encode('latin-1')
        (music_in_base if line in [x.lower() for x in s0.split(b'\0')] else music).append(mp)
    anchors = {}
    for mp in music:
        for rs in ('Release', 'Submission'):
            for ext in ('fev', 'fsb'):
                e = ('Audio/%s/%s/%s.%s' % (rs, mp, mp, ext)).encode('latin-1')
                aud_entries.append(e)
                anchors[e] = ('Audio/%s/%s/' % (rs, 'Music' if ext == 'fev' else mp)).encode('latin-1')
    if aud_entries:   # (nothing to list: the file stays the base's)
        s1, sins = merge_streamed_list(s0, aud_entries, anchors)
        merges['audio/streamedaudiofilelist.txt'] = (s0, s1, sins)
    # the subtrack's light rigs: the names the level archive lists (its LightRigs/LightRigNames.names)
    rig_data = dict(files['main']).get(arkio.key(LIGHTRIG_NAMES))
    if rig_data is None:
        raise SystemExit('%s not in the level archive' % LIGHTRIG_NAMES)
    rigs = parse_lightrig_names(rig_data)
    l0 = shared(LIGHTRIGS_TXT)
    l1, lins = merge_lightrigs(l0, T, meta['subtracks'], rigs)
    merges[LIGHTRIGS_TXT] = (l0, l1, lins)
    # The texts: --names if given; else the official ones of Title Update 4's text table (tu4text.py, only with a
    # developer's table, GLIMPSE_TU4_TEXTS: TEXT_<track>_<sub>, _TRACK, _SUBTRACK as the exe builds them); else the
    # generated defaults. An empty official line (Quarry's _SUBTRACK, nem_quarry's German _SUBTRACK) becomes the
    # PC's one-line route text, as for the generated names.
    tu4 = None if names_given else tu4text.load(log=say)
    xkeys = ['TEXT_%s_%s' % (T, sub), 'TEXT_%s_%s_TRACK' % (T, sub), 'TEXT_%s_%s_SUBTRACK' % (T, sub)]
    official = tu4 is not None and all(tu4.get(k, l) is not None and (i == 2 or tu4.get(k, l))
                                       for i, k in enumerate(xkeys) for l in LANGS)
    names_src = ('--names' if names_given else 'TU4 text table %s' % tu4.describe() if official else
                 'generated names (%s)' % ('no TU4 text table, %s not set' % tu4text.ENV if tu4 is None else
                                           'the TU4 table %s lacks %s' % (tu4.path, '/'.join(xkeys))))
    if not names_given and not official and not names:
        raise SystemExit('no names for %s: give --names' % T)
    say('Track texts: %s' % names_src)
    dpairs = {}
    font_bad = []
    for lang in LANGS + ('StringIDs',):
        p = 'dictionary/%s_win32.dct' % lang.lower()
        d = shared(p)
        keys = ['TEXT_%s_%s' % (T.upper(), sub), 'TEXT_%s_%s_TRACK' % (T.upper(), sub), 'TEXT_%s_%s_SUBTRACK' % (T.upper(), sub)]
        if lang == 'StringIDs':
            texts = [k.lower() for k in keys]
        else:
            one = dictadd.Dict(pc_res.get(p)).get(ONE_WORD_ROUTE).decode('utf-8')
            if official:
                texts = [tu4.get(xkeys[0], lang), tu4.get(xkeys[1], lang), tu4.get(xkeys[2], lang) or one]
            else:
                texts = [names[lang], names[lang], one]
            # the PC fonts have the letters the PC texts use (tu4text.missing_letters, the deadline_mode rule)
            have = [v[0].decode('utf-8', 'replace') for v in dictadd.Dict(pc_res.get(p)).all().values()]
            miss = tu4text.missing_letters(have, texts)
            if miss:
                font_bad.append('%s: %s' % (lang, ''.join(miss)))
        pairs = list(zip(keys, texts))
        dpairs[p] = pairs
        merges[p] = (d, dictadd.add_checked(d, pairs), None)
    merge_how = {}
    qp = None
    if not a.no_qp_picture:
        # the track picture of the quick play detail screen: the DLC's frame TEXT_<track>_<sub> and its image
        qlabel = 'TEXT_%s_%s' % (T, sub)
        g0, t0_, f0_ = shared(QP_GFX), shared(QP_SET + '.textures'), shared(QP_SET + '.filenames')
        g1, t1_, f1_, qimages, qlog = qp_build(a.dlc, qlabel, g0, t0_, f0_)
        qp = {'label': qlabel, 'images': qimages, 'log': qlog}
        merges[QP_GFX] = (g0, g1, None)
        merge_how[QP_GFX] = 'gfxcars.py --sprite-label %s --segment' % QP_SPRITE_LABEL
        if qimages:
            merges[QP_SET + '.textures'] = (t0_, t1_, None)
            merges[QP_SET + '.filenames'] = (f0_, f1_, None)
            merge_how[QP_SET + '.textures'] = merge_how[QP_SET + '.filenames'] = 'flashtex.py %s' % ', '.join(
                '%s=%s' % kv for kv in qimages.items())
    for p, (b0, b1, _) in merges.items():
        ov.append((arkio.key(p), b1, 'merge: %s + %s' % (shared_src[p], T), merge_how.get(p, 'merge')))
    # audio: the level banks (in-archive on the PC) and the locale XMLs; the streamed .fsb is a loose file
    aud = []
    loose = []
    for rel, have in (('audio/release/locales/%s/%s.fev' % (loc, loc), lvl_bank),
                      ('audio/release/locales/%s/%s.fsb' % (loc, loc), lvl_bank),
                      ('audio/release/locales/%s/%s_streamed.fev' % (loc, loc), lvl_streamed)):
        if have:
            aud.append(rel)
    adir = find_ci(a.audio, 'audio/locales/%s' % loc)
    for fn in sorted(os.listdir(adir)) if adir else []:
        aud.append('audio/locales/%s/%s' % (loc, fn.lower()))
    aud_src = {}
    for rel in aud:
        fn = find_ci(a.audio, rel)
        if fn is None:
            C.ok(False, 'audio file %s in %s' % (rel, a.audio))
            continue
        aud_src[rel] = fn
        if rel.endswith('.xml'):
            ov.append((arkio.key(rel), to_crlf(read(fn)), 'audio: ' + os.path.relpath(fn, a.audio),
                       'dlcaudio.py output, LF -> CRLF'))
        else:
            ov.append((arkio.key(rel), read(fn), 'audio: ' + os.path.relpath(fn, a.audio), 'dlcaudio.py output'))
    sfsb = 'audio/release/locales/%s/%s_streamed.fsb' % (loc, loc)
    sfn = find_ci(a.audio, sfsb) if lvl_streamed else None
    if sfn:
        loose.append((os.path.join(out, 'data', *sfsb.split('/')), sfn, 'streamed bank (loose, listed in StreamedAudioFileList)'))
    music_rel = []
    for mp in music:
        for ext in ('fev', 'fsb'):
            rel = 'audio/release/%s/%s.%s' % (mp.lower(), mp.lower(), ext)
            fn = find_ci(a.audio, rel)
            if fn is None:
                C.ok(False, 'music file %s in %s' % (rel, a.audio))
                continue
            music_rel.append(rel)
            loose.append((os.path.join(out, 'data', *rel.split('/')), fn,
                          'music project %s (loose, listed in StreamedAudioFileList)' % mp))
    mov = meta['desc'][4].split('|', 1)[-1]
    mfn = find_ci(a.dlc, mov)
    if mfn:
        loose.append((os.path.join(out, 'data', *mov.lower().split('/')), mfn, 'quick play movie (Bink, as is)'))
    for dst, src, _ in loose:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    ov_keys = [k for k, _, _, _ in ov]
    dup = [k for k, c in collections.Counter(ov_keys).items() if c > 1]
    if dup:
        raise SystemExit('duplicate overlay keys %s' % ['%08x' % k for k in dup])
    arkio.write_ark(paths['overlay'], sorted((k, d) for k, d, _, _ in ov), pad=0x800)
    combined = None
    if base is not None and a.combined:
        combined = os.path.join(out, 'glimpse_dlc', a.combined)
        mine = {k: d for k, d, _, _ in ov}
        allk = sorted(set(base.order) | set(mine))
        arkio.write_ark(combined, [(k, mine[k] if k in mine else base.get(k)) for k in allk], pad=0x800)

    # ---- 4. checks
    say()
    say('Per-kind results')
    for n in ('main', 'pp', 'static', 'res'):
        cnt = collections.Counter()
        for r in res[n]:
            cnt[(r['kind'], 'skipped' if r['out'] is None else ('PROBLEM' if r['probs'] else 'converted'))] += 1
        say('  %-7s %s' % (n, ', '.join('%s %s %d' % (k[0], k[1], v) for k, v in sorted(cnt.items()))))
    say('  resident copies (kept as on the PC): %s' % ', '.join('%s %s %d' % (k[0], k[1], v) for k, v in sorted(copies.items())))
    C.ok(all(v == 0 for (kind, st), v in copies.items() if st == 'DIFFERENT'),
         'the Xbox copies of base shader files in RESIDENT_DLC.ARK convert to the PC files (the PC versions are kept)')
    probs = [(n, r) for n in res for r in res[n] if r['probs']]
    for n, r in probs[:40]:
        say('  PROBLEM %s %08x %s: %s' % (n, r['key'], r['kind'], '; '.join(r['probs'])))
    C.ok(not probs, 'every converted entry passes its converter\'s checks', '%d with problems' % len(probs) if probs else '')
    dflt = [(n, r['key'], r['in']) for n in res for r in res[n] if r['how'] == 'default' and r['out'] is not None]
    if dflt:
        C.warn('%d entries are converted by convmisc\'s default rule (4-byte swap, embedded strings and bool words '
               'kept), which gives the PC bytes for all 43 + 425 such Graveyard entries (misc/README.md): %s' % (
            len(dflt), ', '.join('%s %08x (%d B)' % x for x in dflt[:12]) + (' ...' if len(dflt) > 12 else '')))

    say()
    say('Shaders (convshaders.convert_fxc, translate=True: IDs no PC file has come from ../xenos/xenos2pc.py)')
    for n in ('main', 'pp', 'static', 'res'):
        fx = [x for x in cv.xlate_files if x[0] == n]
        ids = sorted({line.split()[1] for x in fx for line in x[2]})
        ents = sum(len(convshaders.parse_fxc(r['out'])['ents']) for r in res[n] if r['kind'] == 'fxc')
        nsh = sum(1 for r in res[n] if r['kind'] == 'shaders')
        nsi = sum(1 for r in res[n] if r['kind'] == 'shaderinst')
        say('  %-7s %d .fxc (%d entries), %d .shaders, %d .shaderinst; %d .fxc with translated IDs, %d translated IDs'
            % (n, len(fx), ents, nsh, nsi, sum(1 for x in fx if x[2]), len(ids)))
    xl = [line for x in cv.xlate_files for line in x[2]]
    uniq = sorted(set(xl))
    say('  translated IDs (unique): %d; status vs/ps/pipeline all ok: %d' % (
        len({l.split()[1] for l in uniq}), sum(1 for l in uniq if 'vs ok, ps ok, pipeline ok' in l)))
    for l in uniq:
        if 'vs ok, ps ok, pipeline ok' not in l:
            say('    ' + l)
    # .fxc and .shaders of the same stem name the same entries; guessed variant lists are exactly the translated IDs
    pair_bad, guess_bad = [], []
    translated_ids = {l.split()[1] for l in uniq}
    for n in ('main', 'pp'):
        byk = {r['key']: r for r in res[n]}
        for r in res[n]:
            if r['kind'] != 'fxc':
                continue
            s = byk.get(swap_ext(r['key'], '.fxc', '.shaders'))
            if s is None:
                pair_bad.append('%s %08x: no .shaders' % (n, r['key']))
                continue
            a1 = [e['id'] for e in convshaders.parse_fxc(r['out'])['ents']]
            a2 = [e['id'] for e in convshaders.parse_shdr(s['out'])['ents']]
            if a1 != a2:
                pair_bad.append('%s %08x' % (n, r['key']))
            guess_bad += [i for i in s.get('guessed', []) if i not in translated_ids]
    C.ok(not pair_bad, 'every .fxc has its .shaders (key of the same stem) with the same PC entry names in the same order',
         ', '.join(pair_bad[:5]))
    C.ok(not guess_bad, '.shaders variant lists guessed only for translated IDs (same rule on both sides)', ', '.join(guess_bad[:5]))
    rt = [r for n in res for r in res[n] if r['kind'] in ('fxc', 'shaders') and r['out'] is not None and
          (convshaders.build_fxc if r['kind'] == 'fxc' else convshaders.build_shdr)(
              dict((convshaders.parse_fxc if r['kind'] == 'fxc' else convshaders.parse_shdr)(r['out']), E=None)) != r['out']]
    C.ok(not rt, 'every .fxc/.shaders re-parses and rebuilds to the same bytes')
    si = [r for n in res for r in res[n] if r['kind'] == 'shaderinst' and convshaders.convert_sdri(r['out'], '>') != r['xdata']]
    C.ok(not si, 'every .shaderinst converts back to the Xbox bytes')
    # every shader ID a .shaderinst names is in a .fxc of the same archive or of the PC resident.ark / the pool
    unres = collections.Counter()
    for n in ('main', 'pp', 'static', 'res'):
        have = set()
        for r in res[n]:
            if r['kind'] == 'fxc':
                have |= {e['id'][2:] for e in convshaders.parse_fxc(r['out'])['ents']}
        for r in res[n]:
            if r['kind'] == 'shaderinst':
                ids, _ = sdri_ids_textures(r['out'])
                for i in ids:
                    if i[2:] not in have and i[2:] not in cv.pool.variants:
                        unres[(n, i)] += 1
    C.ok(not unres, 'every .shaderinst shader ID is in a .fxc of its archive or in the PC shader files',
         ', '.join('%s %s' % k for k in list(unres)[:6]))

    say()
    say('Textures referenced by .shaderinst')
    tex_have = set()
    for n in ('main', 'pp', 'static', 'res'):
        for r in res[n]:
            if r['kind'] == 'textures':
                tex_have |= {k for k, _, _ in convtex.textures(r['out'])}
    tex_pc = texture_keys_in(pc_res)
    tref = collections.Counter()
    missing_tex = collections.Counter()
    for n in ('main', 'pp', 'static', 'res'):
        for r in res[n]:
            if r['kind'] == 'shaderinst':
                for h in sdri_ids_textures(r['out'])[1]:
                    where = 'track' if h in tex_have else ('PC resident.ark' if h in tex_pc else None)
                    tref[where or 'unresolved'] += 1
                    if where is None:
                        missing_tex[(n, h)] += 1
    say('  texture slots: %s' % dict(tref))
    if missing_tex:
        say('  unresolved texture hashes (%d distinct; the Xbox has them neither in the track archives nor does the '
            'PC resident.ark): %s' % (len({h for _, h in missing_tex}), ' '.join('%08x' % h for h in sorted({h for _, h in missing_tex})[:20])))
        C.warn('unresolved texture slots: %d (see the list; a slot without its texture renders with the engine default)'
               % sum(missing_tex.values()))

    say()
    say('Archives (read back with arkio and through vark.GameArkReader as ArkLoader reads them)')
    want = {'main': dict(files['main']), 'pp': dict(files['pp']), 'static': dict(files['static']),
            'stream': dict(files['stream']), 'overlay': {k: d for k, d, _, _ in ov}}
    for n in ('main', 'pp', 'static', 'stream', 'overlay'):
        fn = paths[n]
        ar = arkio.Ark(fn)
        size = os.path.getsize(fn)
        with open(fn, 'rb') as hf:
            hs = struct.unpack_from('<I', hf.read(8), 4)[0]
        sorted_ok = ar.order == sorted(set(ar.order))
        types0 = all(ar.ents[k][0] == 0 for k in ar.order)
        same = set(ar.order) == set(want[n]) and all(ar.get(k) == want[n][k] for k in ar.order)
        gr, gf = game_reader(fn)
        gsame = all(gr.read_member(k) == want[n][k] for k in ar.order)
        gf.close()
        end = max(ar.ents[k][1] + ar.ents[k][2] for k in ar.order)
        pad = 0x800 if n in ('main', 'pp', 'overlay') else 0
        pad_ok = (size % 0x800 == 0 and size - end < 0x800) if pad else size == end
        # (empty entries share their offset with the next one and are left out of the order comparison)
        nz = lambda keys, A: [k for k in keys if A.ents[k][3]]
        dorder = nz(data_order(ar), ar)
        if n in ('main', 'pp'):
            xo = [k for k in nz(data_order(xa[n]), xa[n]) if k in want[n] and want[n][k]]
            extra = [k for k in pc_added[n] if want[n][k]]
            order_ok, order_rule = dorder == xo + extra, 'data in the Xbox file\'s order%s' % (
                ' (+ %d PC entries at the end)' % len(extra) if extra else '')
        else:
            order_ok, order_rule = dorder == nz(ar.order, ar), 'data in key order'
        C.ok(not ar.be and hs == 0x10 + 20 * len(ar.order) and sorted_ok and types0 and same and gsame and pad_ok and order_ok,
             '%s: %d entries, %d bytes; little-endian, hsize, keys sorted and unique, all stored, data equal (arkio and '
             'GameArkReader), %s, %s' % (os.path.relpath(fn, out), len(ar.order), size,
                                         'padded to 0x800' if pad else 'not padded', order_rule),
             '' if same and gsame else 'DATA DIFFERS')
        ar.close()
    # every Xbox entry accounted for
    acc = collections.Counter()
    for n in ('main', 'pp', 'static'):
        for r in res[n]:
            acc[(n, 'converted' if r['out'] is not None else r['note'] or 'FAILED')] += 1
    acc[('res', 'new, converted into the overlay')] = sum(1 for r in res['res'] if r['out'] is not None)
    acc[('res', 'FAILED')] = sum(1 for r in res['res'] if r['out'] is None)
    acc[('res', 'copy of a PC resident.ark key, PC version kept')] = len(xa['res'].order) - len(res_new)
    used = {sk for n in res for r in res[n] for sk, _, _ in r['streams']}
    for n in ('main', 'pp', 'static', 'res'):
        tot = len(xa[n].order)
        got = sum(v for (nn, _), v in acc.items() if nn == n)
        C.ok(tot == got and not acc.get((n, 'FAILED')), 'Xbox %s: %d entries, all accounted for' % (os.path.basename(xfn[n]), tot),
             '; '.join('%s %d' % (s, v) for (nn, s), v in sorted(acc.items()) if nn == n and v))
    C.ok(set(xa['stream'].order) == used,
         'Xbox %s: %d entries, each used by a streamed texture slot or a chunkless model' % (
             os.path.basename(xfn['stream']), len(xa['stream'].order)),
         '%d unused, %d missing' % (len(set(xa['stream'].order) - used), len(used - set(xa['stream'].order))))
    # keys every PC level / powerplays archive has
    for n in ('main', 'pp'):
        narks, common = pc_common[n]
        lack = sorted(common - set(want[n]))
        what = 'level' if n == 'main' else 'powerplays'
        C.ok(not lack, '%s has every key that all %d PC %s archives have (%d)%s' % (
            os.path.basename(paths[n]), narks, what, len(common),
            '; copied from the PC because the Xbox file lacks them and they are the same in every PC %s archive: %s'
            % (what, ' '.join('%08x' % k for k in pc_added[n])) if pc_added[n] else ''),
            'missing %s' % ' '.join('%08x' % k for k in lack) if lack else '')

    say()
    say('Resident overlay %s' % os.path.relpath(paths['overlay'], out))
    repl = [k for k in ov_keys if k in pc_res.ents]
    shared_keys = {arkio.key(p) for p in merges}
    C.ok(set(repl) == shared_keys, 'keys replacing resident.ark keys are exactly the %d merged shared files' % len(shared_keys),
         ', '.join(sorted(p for p in merges)))
    newk = [k for k in ov_keys if k not in pc_res.ents]
    C.ok(set(newk) == set(res_new) | {arkio.key(p) for p in aud},
         '%d new keys: the %d RESIDENT_DLC.ARK keys the PC lacks + %d audio files' % (len(newk), len(res_new), len(aud)))
    if base is not None:
        ovd_ = {k: d for k, d, _, _ in ov}
        clash = [k for k in ov_keys if k in base.ents and k not in shared_keys and ovd_[k] != base.get(k)]
        C.ok(not clash, 'no key of the base %s is replaced except the shared files (%d of them in the base)%s' % (
            os.path.basename(a.base), len([k for k in shared_keys if k in base.ents]),
            '; %d RESIDENT_DLC.ARK keys the base has already keep the base\'s bytes' % (
                len(in_base_same) + len(in_base_diff)) if in_base_same or in_base_diff else ''))
        if in_base_diff:
            C.warn('%d RESIDENT_DLC.ARK key(s) the base has already convert to other bytes than the base\'s (the '
                   'base\'s are kept; on the Xbox each level reads its own package): %s; %d more are identical' % (
                       len(in_base_diff), ' '.join('%08x' % k for k in in_base_diff), len(in_base_same)))
    # merged files: base bytes + insertions
    for p in ('directory.txt', 'ui/logic/trackfiltration.xml', LIGHTRIGS_TXT):
        b0, b1, ins = merges[p]
        C.ok(removed_inserts(b1, ins) == b0 and b1.count(b'\r\n') == b0.count(b'\r\n') + sum(x[1].count('\r\n') for x in ins)
             and b1.count(b'\n') == b1.count(b'\r\n'),
             '%s (%s): base bytes + %d inserted part(s), CRLF only' % (p, shared_src[p], len(ins)),
             ' | '.join(x[1].strip().replace('\r\n', ' / ') for x in ins))
    lr = [ln.split(':') for ln in merges[LIGHTRIGS_TXT][1].decode('latin-1').split('\r\n') if ln.count(':') == 2]
    mine_lr = [x for x in lr if x[0].lower() == T.lower()]
    C.ok(sorted(tuple(x) for x in mine_lr) == sorted((T, s, r) for s in meta['subtracks'] for r in rigs) and
         len(lr) == len(mine_lr) + sum(1 for ln in merges[LIGHTRIGS_TXT][0].decode('latin-1').split('\r\n')
                                       if ln.count(':') == 2),
         'Environments/SubTrackLightRigs.txt: %s with the level\'s light rigs (%s: %s)' % (
             ', '.join('%s:%s' % (T, s) for s in meta['subtracks']), LIGHTRIG_NAMES, ', '.join(rigs)))
    if fmiss:
        C.ok(False, 'TrackFiltration.xml game modes found', 'missing %s' % fmiss)
    try:
        mt0 = deadline_track.mode_tracks(merges['ui/logic/trackfiltration.xml'][0])
        mt1 = deadline_track.mode_tracks(merges['ui/logic/trackfiltration.xml'][1])
    except Exception as e:
        mt0 = mt1 = None
        C.ok(False, 'TrackFiltration.xml (merged) is well-formed XML', str(e))
    if mt1 is not None:
        want_modes = meta['modes'] + base_modes
        C.ok(set(mt0) == set(mt1) and all(mt1[m] == mt0[m] + ([(T, sub)] if m in want_modes else []) for m in mt1)
             and all(len(v) <= deadline_track.MAX_TRACKS for v in mt1.values()),
             'TrackFiltration.xml as XML: %s %s added as the last track of %s, every other mode unchanged; at most %d '
             'tracks per mode (%s)' % (T, sub, ', '.join(want_modes), deadline_track.MAX_TRACKS,
                                       ', '.join('%s %d' % (m, len(v)) for m, v in mt1.items())))
    if deadline:
        say()
        say('Game mode Deadline (TimeTrialPlus) on %s %s (base %s; deadline_track.py, deadline_mode.py\'s rules)' % (
            T, sub, os.path.basename(a.base)))
        ovd_ = {k: d for k, d, _, _ in ov}
        k_ovl = arkio.key(deadline_track.P_OVL % (T, sub))
        d_ovl, _, oi = dl_data[k_ovl]
        after = ovd_.get(k_ovl)
        pa, pb = deadline_track.pdict(after or b''), deadline_track.pdict(oi['base'])
        C.ok(after == d_ovl and after[:len(oi['base'])] == oi['base']
             and after[len(oi['base']):] == deadline_track.countdown_block(oi['seconds']).encode('latin-1')
             and pa.get(deadline_track.COUNTDOWN) == deadline_track.fmt(oi['seconds']) + ' (0.0, 3600.0)'
             and len(pa) == len(pb) + 1 and pc_res.get(k_ovl) is None,
             'overlay %s (new key %08x): the converted RESIDENT_DLC.ARK entry + Initial Countdown %s s (Xbox %s '
             'TimeTrialPlusCountdown %s; extra seconds 0, the DLL adds [DLC] DeadlineBonus when Pickups=0)' % (
                 oi['path'], k_ovl, deadline_track.fmt(oi['seconds']), oi['xb_path'], oi['xb_value']))
        C.ok(pa.get(deadline_track.XB_COUNTDOWN) == oi['xb_value'] and len(oi['rivals']) == 7
             and all(pa.get((deadline_track.XB_RIVALS, k)) == v for k, v in oi['rivals'].items()),
             'overlay %s: also carries the Xbox keys TimeTrialPlusCountdown and the %d rival times (%s; no PC '
             'tweakables, the parser drops them)' % (oi['path'], len(oi['rivals']), ', '.join(
                 '%s %s' % (k, v.split()[0]) for k, v in sorted(oi['rivals'].items()))))
        others = []
        for m in meta['modes']:
            mp = 'GameLogic/GameModes/%s.params' % m
            md = base.get(mp) if arkio.key(mp) in base.ents else pc_res.get(mp)
            others.append((m, None if md is None else deadline_track.countdown_enabled(md)))
        C.ok(all(v is False for _, v in others),
             'the start time is inert for %s\'s other modes: Countdown Enabled = false in %s' % (
                 T, ', '.join('%s %s' % (m, {False: 'false', True: 'TRUE', None: 'MISSING'}[v]) for m, v in others)))
        bm = deadline_track.pdict(base.get(deadline_track.P_MODE))
        bmc = deadline_track.number(bm.get(deadline_track.COUNTDOWN, '-1'))[0]
        if bmc == deadline_track.XB_MODE_COUNTDOWN and bm.get(deadline_track.ENABLED) == 'true':
            C.ok(True, 'base %s: Countdown Enabled, Initial Countdown %s s = the Xbox mode file\'s (pack built with '
                 'extra seconds 0, as here)' % (deadline_track.P_MODE, deadline_track.fmt(bmc)))
        else:
            C.warn('base %s: Initial Countdown %s s, Enabled %s (not the Xbox 30 s: the pack was built with extra '
                   'seconds; %s keeps the Xbox start time)' % (deadline_track.P_MODE, deadline_track.fmt(bmc),
                                                               bm.get(deadline_track.ENABLED), T))
        k_pk = arkio.key(pkps.path(T, sub))
        d_pk, _, pi = dl_data[k_pk]
        dll_path = 'Environments/Levels/%s/Subtracks/%s/Pickups/%s.pkps' % (T, sub, T)  # dlcpickups.cpp LoadTrack
        try:
            back = pkps.parse(ovd_.get(k_pk) or b'')
            pkps.check(back, pi['path'])
            pk_ok = len(back) == len(pi['pickups']) and len(back) > 0
        except pkps.PkpsError as e:
            pk_ok, back = False, []
            say('  pkps: %s' % e)
        C.ok(pk_ok and ovd_.get(k_pk) == d_pk == xa['res'].get(k_pk) and arkio.key(dll_path) == k_pk
             and pc_res.get(k_pk) is None and (base is None or k_pk not in base.ents),
             'pickups %s (new key %08x = the DLL\'s path for track "%s" subtrack "%s"): byte-identical to the '
             'RESIDENT_DLC.ARK entry, parses (pkps.py): %s' % (pi['path'], k_pk, T, sub, pkps.summary(back)))
    dblocks = parse_directory(merges['directory.txt'][1].decode('latin-1'))
    tr = next(b for b in dblocks if b[1] == 'TRACKS')[3]
    sb = next((b for b in dblocks if b[1] == T + '_SUBTRACK'), None)
    C.ok(T in tr and sb is not None and sb[3] == meta['subtracks'] and sb[0] == sub_pat,
         'directory.txt as the game parses it (dlcmerge block rule): TRACKS has %s, %s_SUBTRACK = %s' % (T, T, sb[3] if sb else None))
    if 'audio/streamedaudiofilelist.txt' in merges:
        b0, b1, sins2 = merges['audio/streamedaudiofilelist.txt']
        C.ok(sorted(x for x in b1.split(b'\0') if x) == sorted([x for x in b0.split(b'\0') if x] + aud_entries) and b1.endswith(b'\0')
             and [x for x in b1.split(b'\0') if x not in aud_entries] == b0.split(b'\0'),
             'Audio/StreamedAudioFileList.txt (%s): base list in its order + %s' % (
                 shared_src['audio/streamedaudiofilelist.txt'], ', '.join(x.decode() for x in aud_entries)))
    else:
        say('  Audio/StreamedAudioFileList.txt: nothing to add (no streamed level bank%s), the base\'s file stays' % (
            '; %s listed by the base already' % ', '.join(music_in_base) if music_in_base else ''))
    if music_in_base:
        say('  music project(s) %s: the base lists them already (the same project from another package), not shipped '
            'again' % ', '.join(music_in_base))
    audio_checks(C, a, g, pc_res, loc, ov, aud, aud_src, sfn, music, music_rel, loose, out, (lvl_bank, lvl_streamed))
    C.ok(not font_bad, 'track texts (%s): every non-ASCII letter is one the PC dictionary of that language uses '
         '(the PC fonts have those)' % names_src, '; '.join(font_bad))
    for p, pairs in dpairs.items():
        b0, b1, _ = merges[p]
        d0, d1 = dictadd.Dict(b0), dictadd.Dict(b1)
        old = d0.all()
        new = d1.all()
        C.ok(all(new.get(h) == v for h, v in old.items()) and len(new) == len(old) + len(pairs) and
             all(d1.get(k) == v.encode('utf-8') for k, v in pairs) and b0[:32] == b1[:32] and len(b1) > len(b0),
             '%s (%s): %d entries unchanged, + %s' % (p, shared_src[p], len(old), ', '.join('%s=%r' % kv for kv in pairs)))
    # the virtual resident.ark with this overlay (and the base pack under it)
    stack = ([a.base] if a.base else []) + [paths['overlay']]
    va = vark.VirtualArk(pc_res_fn, stack)
    try:
        gr = vark.GameArkReader(va.read, va.size)
        bad = [k for k, d, _, _ in ov if gr.read_member(k) != d]
        if base is not None:
            bad += ['base %08x' % k for k in base.order if k not in shared_keys and gr.read_member(k) != base.get(k)]
        sample = [k for k in pc_res.order[::400] if k not in shared_keys and (base is None or k not in base.ents)]
        bad += ['pc %08x' % k for k in sample if gr.read_member(k) != pc_res.get(k)]
        C.ok(not bad, 'virtual resident.ark (vark.py: PC resident.ark + %s): every overlay entry%s and %d base samples read '
             'back through GameArkReader' % (' + '.join(os.path.basename(x) for x in stack),
                                             ', every base pack entry' if base is not None else '', len(sample)),
             ', '.join(bad[:5]))
    finally:
        va.close()
    if combined:
        ca = arkio.Ark(combined)
        mine = {k: d for k, d, _, _ in ov}
        okc = set(ca.order) == set(base.order) | set(mine) and all(
            ca.get(k) == (mine[k] if k in mine else base.get(k)) for k in ca.order)
        C.ok(okc, '%s: %d entries = base pack %d + track %d (%d shared files from the track build), %d bytes' % (
            os.path.relpath(combined, out), len(ca.order), len(base.order), len(mine), len(set(mine) & set(base.ents)),
            os.path.getsize(combined)))
    if qp is not None:
        qp_checks(C, qp, merges, shared_src, a, pc_res, base, res, tex_pc)
    else:
        say()
        say('Quick play detail screen: left out (--no-qp-picture); the picture sprite stays on the previous track\'s picture')
    say()
    say('Loose files (paths below the game\'s data folder)')
    for dst, src, what in loose:
        C.ok(read(dst) == read(src), '%s: %s, %d bytes, copy of %s' % (os.path.relpath(dst, out), what,
                                                                        os.path.getsize(dst), os.path.relpath(src, a.dlc) if src.startswith(a.dlc) else src))
    mv_pc = os.path.join(g, *mov.lower().split('/'))
    C.ok(not os.path.exists(mv_pc), 'the movie name is not taken by a PC file (%s)' % mov)

    # ---- 5. DLL data
    say()
    say('Data the DLL needs (track enum tables, five {ptr, count=15} descriptors; entries are {u32 index, char *})')
    pc_tracks = {x.lower() for x in next(b for b in parse_directory(pc_res.get('directory.txt').decode('latin-1'))
                                         if b[1] == 'TRACKS')[3]}
    earlier = [x for x in next(b for b in parse_directory(merges['directory.txt'][0].decode('latin-1'))
                               if b[1] == 'TRACKS')[3] if x.lower() not in pc_tracks]
    idx = 15 + len(earlier)
    say('  index %d (PC indices 0..14%s; src/dlcfs.cpp numbers the DLC tracks it registers from 15 in its own '
        'order: Quarry, nem_quarry)' % (idx, ', DLC tracks of the base: %s' % ', '.join(earlier) if earlier else ''))
    say('  0xD7E874 track names        -> {%d, "%s"}' % (idx, T))
    say('  0xD7E880 subtrack letters   -> {%d, "%s"}' % (idx, sub))
    say('  0xD7E88C subtrack paths     -> {%d, "%s"}' % (idx, meta['desc'][3]))
    say('  0xD6FB84 track_subtrack     -> {%d, "%s_%s"}' % (idx, t, sub))
    say('  0xD6FC40 quick play movies  -> {%d, "data|%s"}' % (idx, mov))
    say('  dictionary keys (in the overlay): %s' % ', '.join(k for k, _ in dpairs['dictionary/english_ntsc_win32.dct']))
    say('  files: the four level archives as plain files at their data\\ paths (see README_track.md for the options)')
    for mp in music:
        say('  music (optional, not needed for the track to run): the stock exe never loads %s. Load it like Music/Music '
            '(0x7F5900 calls 0x7F3030("Music/Music", 2)) with 0x7F3030("%s/%s", 2), then let the picker use its group: '
            'the race music path is "Music/%%s/RaceSection0%%d" (0x83D1E0, %%s = picker +0xAC, set per race by 0x83D9F0 '
            'from the shuffled InGame_Track table 0xD8CA00), so it needs +0xAC = "%s_Track" on this track and an '
            'event alias "Music/%s_Track/" -> "%s/%s_Track/" (the alias table 0x8A5170 / 0x8A45B0 that '
            '0x7F1360 fills with "locale/" -> "<level>/")' % (mp, mp, mp, T, T, mp, T))

    # ---- manifest
    man = os.path.join(out, 'track_%s.manifest.tsv' % t)
    with open(man, 'w', newline='\n', encoding='ascii', errors='backslashreplace') as f:
        f.write('# track %s: level archives, resident overlay, loose files\n' % T)
        f.write('archive\tkey\tsize\tresident\tkind\tconverter\tsource\tnote\n')
        for n in ('main', 'pp', 'static'):
            for r in res[n]:
                f.write('%s\t%08x\t%s\t-\t%s\t%s\t%s %08x\t%s\n' % (
                    os.path.basename(paths[n]), r['key'], len(r['out']) if r['out'] is not None else '-', r['kind'],
                    r.get('conv', '-'), os.path.basename(xfn[n]), r['key'], (r['note'] + ('; ' + '; '.join(r['probs']) if r['probs'] else ''))))
                for sk, d, how in r['streams']:
                    f.write('%s\t%08x\t%d\t-\tstream\t%s\t%s %08x\tfor %08x\n' % (
                        os.path.basename(paths['stream']), sk, len(d), how, os.path.basename(xfn['stream']), sk, r['key']))
            for k in pc_added.get(n, []):
                f.write('%s\t%08x\t%d\t-\tpc-common\tcopy\tPC (same bytes in every PC archive of this kind)\t'
                        'not in the Xbox file\n' % (os.path.basename(paths[n]), k, len(dict(files[n])[k])))
        rr = {r['key']: r for r in res['res']}
        for k, d, src, how in sorted(ov):
            r = rr.get(k)
            f.write('%s\t%08x\t%d\t%s\t%s\t%s\t%s\t%s\n' % (
                os.path.basename(paths['overlay']), k, len(d), 'replaces' if k in pc_res.ents else 'new',
                r['kind'] if r else 'shared/audio', r['conv'] if r else how, src, r['note'] if r else ''))
        for dst, src, what in loose:
            f.write('loose\t-\t%d\t-\t-\tcopy\t%s\t%s\n' % (os.path.getsize(dst), os.path.relpath(dst, out), what))
    say()
    say('Outputs')
    for n in ('main', 'pp', 'static', 'stream', 'overlay'):
        say('  %-55s %11d bytes' % (os.path.relpath(paths[n], out), os.path.getsize(paths[n])))
    if combined:
        say('  %-55s %11d bytes' % (os.path.relpath(combined, out), os.path.getsize(combined)))
    for dst, _, _ in loose:
        say('  %-55s %11d bytes' % (os.path.relpath(dst, out), os.path.getsize(dst)))
    say('time %.0f s' % (time.time() - t0))
    say('RESULT: %s' % ('OK' if not C.errors else 'ERRORS: %d' % C.errors) + (', %d notes' % C.warnings if C.warnings else ''))
    rep = os.path.join(out, 'track_%s.report.txt' % t)
    with open(rep, 'w', newline='\r\n', encoding='utf-8') as f:
        f.write('\n'.join(C.lines) + '\n')
    print('wrote', man, rep)
    return 0 if not C.errors else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
