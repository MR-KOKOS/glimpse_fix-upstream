# glimpse_fix: build the PC files of your own Xbox 360 Split/Second DLC, in one go.
#
#   py -3 -I build_all.py --dump <DLC folder> [--game <Split/Second folder>] [--staging <dir>] [--work <dir>]
#       [--no-mode] [--no-track] [--force] [--vgmstream <vgmstream-cli.exe>] [--ffmpeg <ffmpeg.exe>]
#       [--status <file>] [--hkref <hkref.json>] [--tu4-texts <json>]
#   py -3 -I build_all.py --check [--staging <dir>] [--game <Split/Second folder>]
#   py -3 -I build_all.py --preflight [--game <Split/Second folder>] [--dump <DLC folder>] [--work <dir>]
#   py -3 -I build_all.py --find-game
#
# Needs: Python 3.8+ (the cars); the DLC tracks also numpy, vgmstream-cli, ffmpeg and D3DCompiler_41.dll. The DLC
# tools bundle (tools/make_dlc_tools.py, unpacked into %LOCALAPPDATA%\glimpse_fix\tools) has all of them but the
# DLL: when it is there, this script restarts itself in the bundle's python\python.exe -I -X utf8 (unless
# --no-bundle-python) and uses its vgmstream-cli and ffmpeg (BUNDLE_DIR).
# --game: default the Steam install of Split/Second (find_steam_game: Steam's folder from the registry, its
# steamapps\libraryfolders.vdf, appmanifest_297860.acf, steamapps\common\<installdir>\SplitSecond.exe; read only).
# --tu4-texts (or GLIMPSE_TU4_TEXTS): optional: a TU4 text table (JSON) made outside glimpse_fix (mode/tu4text.py
# format, game data, never shipped). The official texts need it and the release cannot produce it (it is made with a
# local development tool that is not part of glimpse_fix); passed to every step, whose converters then use the
# official texts instead of their own. Without it the converters write their own texts.
# --preflight checks all of that, that this tools folder is complete (glimpse_tools_files.tsv of a release) and that
# every converter loads, and with --game reads the Havok class layouts; it converts nothing.
# <DLC folder>: the title folder 425607E7 of the Xbox 360 content dump (or its 00000002 subfolder, or a folder
# above it) holding the DLC packages as the console stores them (STFS LIVE files). Nothing in it is changed.
# The steps, each the existing converter (README.md next to each), run in the work folder
# (default %LOCALAPPDATA%\glimpse_fix\dlc_work); a step whose inputs are unchanged since its last successful run is
# skipped, so a rerun only redoes what is needed (--force: everything); a step that runs first clears its own output
# folder in the work folder (clear_output), and only files this build produced are staged:
#   1. extract  the car packages found (Deadline, High Octane, The Elite liveries), Onslaught (track Quarry) and
#               Survival At The Rock (track nem_quarry) with stfs.py; Master Unlock is not needed
#   2. merge    with more than one car package: one source folder (assemble/merge_dlc.py, Deadline as the base)
#   3. frontend the car select bodies of every car (frontend/convfrontend.py)
#   4. pack     the car pack overlay dlc_cars.ark with all cars and the game mode Deadline (assemble/build_pack.py
#               --models ... --mode; --no-mode: the cars alone); Deadline's quick play movie to deadline\data
#   5. per DLC track (TRACK_SPECS: Quarry, then nem_quarry), each laid on the previous one's overlay:
#      audio    its sound files (audio/dlcaudio.py --no-reuse; needs vgmstream-cli and ffmpeg); the first track built
#               also takes its package's music project DLC3_Music (one copy serves both tracks)
#      track    assemble/assemble_track.py --track <t> --base <car pack or the previous track's overlay>
#   6. mode     with nem_quarry: the game mode Survival Race (mode/nemesis_mode.py) on the last overlay; the result
#               is the combined overlay track_quarry_combined.ark (car pack + tracks + modes)
#   7. stage    the staging folder (default %LOCALAPPDATA%\glimpse_fix\dlc_staging) = exactly the files that go into
#               <game>\glimpse_dlc\, plus glimpse_dlc_manifest.tsv (size and SHA-256 of each)
#   8. check    the staging folder against its manifest, every archive's index (also alone: --check)
# Without vgmstream-cli, ffmpeg, D3DCompiler_41.dll or numpy, or with --no-track, the tracks are left out (state
# WARN) and only the car pack is staged.
# Before step 1 the Havok class layouts are read from the game's SplitSecond.exe and PC archives into <work>\hkref.json
# (havok/convhkx.py learn, about 15 s, redone only when the exe or an archive changes; never shipped, as it is game
# data). The game folder is only read (data\resident.ark, the loose audio banks); the work and staging folders must lie
# outside it. Installing is a separate step: install_dlc.py. Nothing is uploaded or sent anywhere: the converted
# files stay on this PC.
#
# --status <file>: the state for the settings program (glimpse_config.exe), rewritten at every step:
#   state<TAB>RUNNING|OK|WARN|FAILED, step<TAB>n/N, text<TAB>one line, detail<TAB>... (any number).
# Exit code 0 = built (state OK or WARN), 1 = failed (the reason is printed and in the status file), 2 = internal
# error (traceback printed).
import argparse
import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import traceback

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, 'misc')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TITLE_ID = 0x425607E7
STEAM_APP = 297860                     # Split/Second on Steam: the game folder when --game is not given
MANIFEST = 'glimpse_dlc_manifest.tsv'
MANIFEST_VERSION = 1
# overlays load in name order: the car pack first, track_* last (it holds the pack's entries too)
PACK_NAME = 'dlc_cars.ark'             # every converted car (+ the game mode Deadline unless --no-mode)
TRACK_NAME = 'track_quarry_combined.ark'   # the car pack + every DLC track + their modes (the last overlay)
TRACK = 'Quarry'
# the DLC tracks in build order. role: the package; track: assemble_track.py --track; dir: the folder of its loose
# files in glimpse_dlc (<dir>\data\..., served as data\ by the DLL); audio: the package folders converted for it
# (Quarry: its banks and locale XMLs, the PC exe never asks for DLC3_nem_helicopter or DLCMenu, see audio/README.md;
# nem_quarry: only its locale XMLs are used); mode: a script laid on the overlay after the track (mode/<mode>.py).
# A new track: one more entry here (and its folder in DATA_DIRS).
TRACK_SPECS = (
    dict(role='onslaught', track='Quarry', dir='quarry', name='Quarry', step='',
         audio=('AUDIO/RELEASE/LOCALES/QUARRY', 'AUDIO/LOCALES/QUARRY'), mode=None),
    dict(role='rock', track='nem_quarry', dir='nem_quarry', name='nem_quarry (Survival At The Rock)', step='_nem_quarry',
         audio=('AUDIO/LOCALES/NEM_QUARRY',), mode='nemesis_mode'),
)
TRACK_ROLES = tuple(t['role'] for t in TRACK_SPECS)
# the folders below glimpse_dlc that hold loose files (install_dlc.py removes them when they are empty)
DATA_DIRS = tuple(t['dir'] for t in TRACK_SPECS) + ('deadline',)
DEADLINE_MOVIE = ('MOVIES/SCREENS/QUICKPLAY/FE_DEADLINE.BIK', 'deadline/data/movies/screens/quickplay/fe_deadline.bik')
TU4_ENV = 'GLIMPSE_TU4_TEXTS'   # mode/tu4text.py
# the packages, by the pack name in their DLCINFO.TXT; of two packages with the same name the one with the higher
# version (DLCINFO's first word) is used ("The Elite" livery pack 13B21D5F... over the older copy EF1A90C1...)
ROLES = {'Pack4': 'deadline', 'Pack5': 'onslaught', 'AllNewSupercars': 'highoctane', 'NewEliteLiveries': 'elite',
         'Nem_Quarry': 'rock'}
ROLE_NAMES = {'deadline': 'Deadline', 'onslaught': 'Onslaught', 'highoctane': 'High Octane', 'elite': 'The Elite',
              'rock': 'Survival At The Rock', 'unlock': 'Master Unlock'}
NOT_CONVERTED = {'unlock': 'not needed: it only sets the Xbox unlock switch (the fix opens the converted content itself)'}
# the car packages in merge order (the first one present is merge_dlc.py's base: Deadline's car select UI has the
# frames of all eleven DLC cars) and their cars, in the order build_pack.py numbers them (the tested packs)
CAR_ROLES = ('deadline', 'highoctane', 'elite')
CAR_MODELS = {'deadline': ('Musclecar_17', 'Musclecar_18', 'Musclecar_19', 'Truck_09'),
              'highoctane': ('Supercar_17', 'Supercar_18', 'Supercar_19', 'Supercar_20'),
              'elite': ('Unique_21', 'Unique_22', 'Unique_23')}
# vgmstream-cli and ffmpeg: --vgmstream/--ffmpeg, else PATH, else these folders (find_exe); nothing machine-specific
EXE_DIRS = (os.path.join(os.path.dirname(HERE), 'bin'),                          # glimpse_tools\bin next to dlc
            os.path.join(os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local'),
                         'glimpse_fix', 'tools'))
# the DLC tools bundle (tools/make_dlc_tools.py, glimpse_fix-dlc-tools-<version>.zip of the release), unpacked into
# %LOCALAPPDATA%\glimpse_fix\tools: python\python.exe (embeddable Python with numpy), vgmstream\vgmstream-cli.exe,
# ffmpeg\ffmpeg.exe, the version file and the manifest (every file's size and SHA-256). When it is there, build_all.py
# runs in its Python (it restarts itself there; --no-bundle-python: not) and takes its vgmstream-cli and ffmpeg
# before any in PATH.
BUNDLE_DIR = EXE_DIRS[1]
BUNDLE_VERSION = 'glimpse_dlc_tools.txt'
BUNDLE_MANIFEST = 'glimpse_dlc_tools_manifest.tsv'
BUNDLE_FORMATS = (1,)
BUNDLE_EXES = {'python': ('python', 'python.exe'), 'vgmstream-cli': ('vgmstream', 'vgmstream-cli.exe'),
               'ffmpeg': ('ffmpeg', 'ffmpeg.exe')}
NO_BUNDLE_ENV = 'GLIMPSE_NO_BUNDLE_PYTHON'   # set for the restarted build_all.py (no second restart); set by hand = opt-out
                                             # like --no-bundle-python
# the helper that installs the programs above in their current versions (setup_deps.ps1 next to this script)
SETUP_HINT = ' Run setup_deps.bat -Install (next to build_all.py) to get it.'
NEED_FREE_WORK = 4 << 30     # extracted packages, intermediate archives and the staging copy
# Havok class layouts (havok/convhkx.py): read on this PC from SplitSecond.exe and the PC archives into
# <work>\hkref.json (convhkx.py learn, about 15 s), never shipped; GLIMPSE_HKREF points the converters at it.
HKREF = 'hkref.json'
# its classes and enums (hkref_digest) as the Steam build 1.0.0.1 (German/English depot) gives them; another result
# is only a NOTE (another build of the game), the converters check each Havok file they write anyway
HKREF_TESTED = '52854ac28d3765e853df53fdf53574d04826f250f9a5b9a3ffd90860e2a2d88b'
# the converters build_all.py runs itself (each imports the rest); --preflight loads each of them once
ENTRY_SCRIPTS = ('assemble/merge_dlc.py', 'frontend/convfrontend.py', 'assemble/build_pack.py', 'assemble/assemble_car.py',
                 'assemble/finish_car.py', 'audio/dlcaudio.py', 'havok/convhkx.py', 'assemble/assemble_track.py',
                 'mode/nemesis_mode.py')
# only with the tracks (assemble_track.py needs numpy for the shader translator; the cars do not)
TRACK_SCRIPTS = ('assemble/assemble_track.py', 'mode/nemesis_mode.py')
# written by tools/make_release.py into the released tools folder: every shipped file with its size and SHA-256
FILES_LIST = 'glimpse_tools_files.tsv'


class BuildError(Exception):
    """A problem the user can act on; the message says what and where."""

    def __init__(self, msg, details=()):
        Exception.__init__(self, msg)
        self.details = list(details)


def local_appdata():
    d = os.environ.get('LOCALAPPDATA')
    if not d:
        d = os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
    return d


def default_work():
    return os.path.join(local_appdata(), 'glimpse_fix', 'dlc_work')


def default_staging():
    return os.path.join(local_appdata(), 'glimpse_fix', 'dlc_staging')


def norm(p):
    return os.path.normcase(os.path.abspath(p)).rstrip('\\/')


def inside(path, folder):
    a, b = norm(path), norm(folder)
    return a == b or a.startswith(b + os.sep)


def overlap(p, q):
    """p and q are the same folder or one holds the other, as given and with links and junctions resolved."""
    rp, rq = os.path.realpath(p), os.path.realpath(q)
    return inside(p, q) or inside(q, p) or inside(rp, rq) or inside(rq, rp)


# The work folder: build_all.py clears and rewrites folders in it (clear_output), so it only takes a new or empty
# folder, or one it made itself. On first use it writes WORK_MARKER; later runs need it. A folder of a build_all.py
# that wrote no marker yet (its own layout: the stamps\ folder of the steps) is taken and gets the marker.
WORK_MARKER = 'glimpse_dlc_work.txt'
# what may lie in a work folder without the marker: the settings program's logs and status files (it makes the
# folder and starts the build there), the lock of build_all.py / install_dlc.py, Explorer's folder files
WORK_FOREIGN_OK = ('build.log', 'install.log', 'build_status.txt', 'install_status.txt', 'build_status.txt.tmp',
                   'install_status.txt.tmp', 'download.log', 'deps.log', 'build.lock', 'desktop.ini', 'thumbs.db')


def claim_work(work, write):
    """Refuses a work folder that is not empty and was not made by build_all.py; write: make it (if needed) and put
    WORK_MARKER into it. Returns a line for the log."""
    work = os.path.abspath(work)
    if os.path.lexists(work) and not os.path.isdir(work):
        raise BuildError('The work folder %s is a file. Give a new or empty folder with --work.' % work)
    marker = os.path.join(work, WORK_MARKER)
    if os.path.isfile(marker):
        return 'work folder %s (made by build_all.py)' % work
    names = os.listdir(work) if os.path.isdir(work) else []
    other = sorted(n for n in names if n.lower() not in WORK_FOREIGN_OK)
    stamps = os.path.join(work, 'stamps')
    own = os.path.isdir(stamps) and not os.path.islink(stamps) and \
        all(n.lower().endswith('.json') and os.path.isfile(os.path.join(stamps, n)) for n in os.listdir(stamps))
    if other and not own:
        hint = ('give a new or empty folder with --work' if norm(work) != norm(default_work()) else
                'move or remove what is in it yourself, or give a new or empty folder with --work')
        raise BuildError('The work folder %s is not empty and was not made by build_all.py (no %s in it; it holds %s). '
                         'build_all.py clears and rewrites folders in its work folder, so it only uses a new or empty '
                         'folder or one of its own: %s.'
                         % (work, WORK_MARKER, ', '.join(other[:5]) + (', ...' if len(other) > 5 else ''), hint),
                         other[:20])
    how = 'an earlier build_all.py\'s folder' if other else 'empty' if os.path.isdir(work) else 'new'
    if write:
        os.makedirs(work, exist_ok=True)
        with open(marker, 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('glimpse_fix: the work folder of tools\\dlc\\build_all.py (intermediate files of the DLC build).\n'
                    'build_all.py clears and rewrites the folders in it. Made %s.\n'
                    % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    return 'work folder %s (%s%s)' % (work, how, ', marked with ' + WORK_MARKER if write else '')


def human(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'bytes' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def sha256(fn):
    h = hashlib.sha256()
    with open(fn, 'rb') as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- status file
class Status:
    def __init__(self, fn, total):
        self.fn = fn
        self.total = total
        self.n = 0

    def write(self, state, text, details=(), extra=()):
        """extra: (key, value) lines for the settings program, e.g. ('other', file name)"""
        if not self.fn:
            return
        lines = ['state\t' + state, 'step\t%d/%d' % (self.n, self.total), 'text\t' + text.replace('\n', ' ')]
        lines += ['detail\t' + d.replace('\n', ' ') for d in details]
        lines += ['%s\t%s' % (k, v.replace('\n', ' ')) for k, v in extra]
        tmp = self.fn + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\r\n') as f:
                f.write('\n'.join(lines) + '\n')
            for _ in range(20):       # the settings program may be reading it this very moment
                try:
                    os.replace(tmp, self.fn)
                    return
                except PermissionError:
                    time.sleep(0.05)
        except OSError:
            pass

    def step(self, text):
        self.n += 1
        say('')
        say('== %d/%d %s' % (self.n, self.total, text))
        self.write('RUNNING', text)


def say(*a):
    print(*a)
    sys.stdout.flush()


# ------------------------------------------------------------------------------------------------- Steam
def vdf_parse(text):
    """Valve's text KeyValues (libraryfolders.vdf, appmanifest_*.acf) as nested dicts; keys lower-case. Quoted
    strings with backslash escapes (\\\\ \\" \\n \\t), { }, // comments."""
    toks, i, n = [], 0, len(text)
    esc = {'n': '\n', 't': '\t', '\\': '\\', '"': '"'}
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
        elif c == '/' and text.startswith('//', i):
            j = text.find('\n', i)
            i = n if j < 0 else j
        elif c in '{}':
            toks.append(c)
            i += 1
        elif c == '"':
            i += 1
            s = []
            while i < n and text[i] != '"':
                if text[i] == '\\' and i + 1 < n:
                    s.append(esc.get(text[i + 1], '\\' + text[i + 1]))
                    i += 2
                else:
                    s.append(text[i])
                    i += 1
            toks.append(('s', ''.join(s)))
            i += 1
        else:   # an unquoted token
            j = i
            while j < n and text[j] not in ' \t\r\n{}"':
                j += 1
            toks.append(('s', text[i:j]))
            i = j
    root, stack, key = {}, [], None
    cur = root
    for t in toks:
        if t == '{':
            d = {}
            if key is not None:
                cur[key.lower()] = d
            stack.append(cur)
            cur, key = d, None
        elif t == '}':
            cur = stack.pop() if stack else root
            key = None
        elif key is None:
            key = t[1]
        else:
            cur[key.lower()] = t[1]
            key = None
    return root


def steam_roots():
    """Steam's folder from the registry (HKCU SteamPath, HKLM InstallPath), read only."""
    out = []
    try:
        import winreg
    except ImportError:
        return out
    for hive, key, name in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath')):
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ) as k:
                v, _ = winreg.QueryValueEx(k, name)
        except OSError:
            continue
        if isinstance(v, str) and v.strip():
            p = os.path.normpath(v.strip())
            if os.path.isdir(p) and norm(p) not in (norm(x) for x in out):
                out.append(p)
    return out


def steam_libraries(root):
    """The library folders of steamapps\\libraryfolders.vdf (old and new layout), Steam's own folder first."""
    libs = [root]
    fn = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
    try:
        with open(fn, encoding='utf-8', errors='replace') as f:
            d = vdf_parse(f.read())
    except OSError:
        return libs
    lf = d.get('libraryfolders')
    for k, v in sorted(lf.items() if isinstance(lf, dict) else (), key=lambda kv: (len(kv[0]), kv[0])):
        p = v.get('path') if isinstance(v, dict) else (v if k.isdigit() else None)
        if isinstance(p, str) and p.strip() and os.path.isabs(p.strip()):   # never a folder relative to the current one
            p = os.path.normpath(p.strip())
            if norm(p) not in (norm(x) for x in libs):
                libs.append(p)
    return libs


def find_steam_game():
    """(game folder, how it was found) of Split/Second (app STEAM_APP) in the Steam libraries, or (None, why not).
    Only reads: the registry, libraryfolders.vdf, appmanifest_<app>.acf; the folder must hold SplitSecond.exe."""
    roots = steam_roots()
    if not roots:
        return None, 'Steam was not found in the registry'
    tried = []
    for root in roots:
        for lib in steam_libraries(root):
            acf = os.path.join(lib, 'steamapps', 'appmanifest_%d.acf' % STEAM_APP)
            if not os.path.isfile(acf):
                tried.append(lib)
                continue
            try:
                with open(acf, encoding='utf-8', errors='replace') as f:
                    st = vdf_parse(f.read()).get('appstate', {})
            except OSError:
                continue
            inst = st.get('installdir') if isinstance(st, dict) else None
            if not isinstance(inst, str) or not inst or os.path.basename(inst) != inst or inst in ('.', '..'):
                continue
            game = os.path.join(lib, 'steamapps', 'common', inst)
            if os.path.isfile(os.path.join(game, 'SplitSecond.exe')):
                return game, 'Steam library %s (appmanifest_%d.acf)' % (lib, STEAM_APP)
            tried.append('%s (listed, but no SplitSecond.exe in %s)' % (lib, game))
    return None, 'not in the Steam libraries %s' % ', '.join(tried)


# ------------------------------------------------------------------------------------------------- inputs
def find_game(game, read_data=True):
    """(game folder, resident.ark); read_data=False (install): only that it is the game folder. game None: the Steam
    install of Split/Second (find_steam_game), else an error asking for --game."""
    if not game:
        game, how = find_steam_game()
        if not game:
            raise BuildError('No --game given and Split/Second was not found through Steam (%s). Give the game folder '
                             '(the one with SplitSecond.exe) with --game.' % how)
        say('game folder found through Steam: %s (%s)' % (game, how))
    game = os.path.abspath(game)
    res = os.path.join(game, 'data', 'resident.ark')
    if not read_data:
        if not os.path.isfile(os.path.join(game, 'SplitSecond.exe')) and not os.path.isfile(res):
            raise BuildError('%s is not the Split/Second folder (no SplitSecond.exe, no data\\resident.ark).' % game)
        return game, res
    if not os.path.isfile(res):
        raise BuildError('No data\\resident.ark in %s. Give the Split/Second folder (the one with SplitSecond.exe).' % game)
    if os.path.getsize(res) < 1 << 20:
        raise BuildError('%s is far too small for the game\'s resident.ark; check the game folder (Steam: verify the '
                         'game files).' % res)
    return game, res


def find_packages(root):
    """Every STFS package of the game below root (depth 4): list of dict(path, name, info, role, files)."""
    import stfs
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise BuildError('The DLC folder %s does not exist.' % root)
    found, others = [], []
    base_depth = root.rstrip('\\/').count(os.sep)
    for dp, dn, fns in os.walk(root):
        if dp.rstrip('\\/').count(os.sep) - base_depth >= 4:
            dn[:] = []
        for n in sorted(fns):
            p = os.path.join(dp, n)
            try:
                if os.path.getsize(p) < 0xB000:
                    continue
                with open(p, 'rb') as f:
                    magic = f.read(4)
            except OSError:
                continue
            if magic not in (b'LIVE', b'PIRS', b'CON '):
                continue
            try:
                s = stfs.Stfs(p)
                if s.title_id != TITLE_ID:
                    others.append('%s: another game (title %08X)' % (n, s.title_id))
                    continue
                if s.content_type != 2:
                    others.append('%s: not DLC (content type %08X, %s)' % (n, s.content_type, s.display_name))
                    continue
                fs = s.files()
                paths = {s.path_of(fs, i).upper(): e for i, e in enumerate(fs)}
                info = b''
                if 'DLCINFO.TXT' in paths:
                    info = s.read_file(paths['DLCINFO.TXT'])
                s.f.close()
            except Exception as ex:      # a damaged package: name it, go on with the others
                others.append('%s: cannot be read as a package (%s)' % (n, ex))
                continue
            words = info.decode('latin-1', 'replace').split()
            role = next((ROLES[w] for w in words if w in ROLES), None)
            if role is None and 'DLC/UNLOCK/UNLOCK.PARAMS' in paths:
                role = 'unlock'
            version = int(words[0]) if words and words[0].isdigit() else 0
            found.append(dict(path=p, name=s.display_name or n, info=' '.join(words[:3]), role=role, version=version,
                              count=len(fs), size=sum(e['size'] for e in fs if not e['dir'])))
    # one package per role: the highest version, then the first by file name
    by_role = {}
    for p in sorted(found, key=lambda p: (-p['version'], os.path.basename(p['path']))):
        if p['role'] and p['role'] not in by_role:
            by_role[p['role']] = p
    for p in found:
        p['used'] = p['role'] in by_role and by_role[p['role']] is p
    return found, others, by_role


# ------------------------------------------------------------------------------------------------- steps
def tools_signature(names=None):
    """SHA-256 of the converter scripts (.py/.json below tools\\dlc): a changed tool rebuilds the steps that use it."""
    h = hashlib.sha256()
    # hkref.json: a developer's copy next to convhkx.py is not an input (the build uses <work>\hkref.json, whose hash
    # the steps that need it take as their own input)
    skip = {'build_all.py', 'install_dlc.py', HKREF}
    for dp, dn, fns in os.walk(HERE):
        dn[:] = sorted(d for d in dn if d != '__pycache__' and not d.startswith('test_out'))
        for n in sorted(fns):
            if n in skip or not n.endswith(('.py', '.json')):
                continue
            if names is not None and n not in names:
                continue
            p = os.path.join(dp, n)
            # the content itself (a few MB in all): a changed converter or option default always rebuilds
            h.update(('%s|%s\n' % (os.path.relpath(p, HERE), sha256(p))).encode())
    return h.hexdigest()


def file_sig(fn):
    st = os.stat(fn)
    return '%s|%d|%d' % (os.path.normcase(os.path.abspath(fn)), st.st_size, int(st.st_mtime))


# part of every step's signature: 2 = each step clears its own output folder before it runs (clear_output), so a step
# done by an older build_all.py (which left the files of an earlier run in place) is redone once
WORK_LAYOUT = 2


def clear_output(path, work):
    """Removes one step's own output folder below the work folder before the step rebuilds it, so nothing of an
    earlier run (other packages, cars or tracks) is left in it. Refuses anything not strictly below work."""
    path, work = os.path.abspath(path), os.path.abspath(work)
    if (not inside(path, work) or norm(path) == norm(work) or
            not inside(os.path.realpath(path), os.path.realpath(work)) or
            norm(os.path.realpath(path)) == norm(os.path.realpath(work))):
        raise BuildError('Internal: refusing to clear %s (not a step folder below the work folder %s).' % (path, work))
    if os.path.isdir(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        raise BuildError('%s is a file, not a step folder; remove it or use another work folder (--work).' % path)


class Step:
    """Skipped when its stamp holds the same inputs and every output exists."""

    def __init__(self, work, name, inputs, outputs, force):
        self.dir = os.path.join(work, name)
        self.stamp = os.path.join(work, 'stamps', name + '.json')
        self.inputs = dict(inputs, layout=WORK_LAYOUT)
        self.outputs = outputs
        self.force = force
        self.sig = hashlib.sha256(json.dumps(self.inputs, sort_keys=True).encode()).hexdigest()

    def up_to_date(self):
        if self.force or not os.path.isfile(self.stamp):
            return False
        try:
            st = json.load(open(self.stamp, encoding='utf-8'))
        except (OSError, ValueError):
            return False
        return st.get('sig') == self.sig and all(os.path.isfile(o) for o in self.outputs)

    def invalidate(self):
        if os.path.isfile(self.stamp):
            os.remove(self.stamp)

    def done(self):
        os.makedirs(os.path.dirname(self.stamp), exist_ok=True)
        with open(self.stamp, 'w', encoding='utf-8') as f:
            json.dump(dict(sig=self.sig, inputs=self.inputs, time=time.strftime('%Y-%m-%d %H:%M:%S')), f, indent=1)


def run_tool(cmd, log, env, cwd):
    """Runs one converter; its whole output goes to log. Returns (exit code, last lines)."""
    os.makedirs(os.path.dirname(log), exist_ok=True)
    say('  > ' + ' '.join(('"%s"' % c) if ' ' in c else c for c in cmd[1:]))
    t0 = time.time()
    with open(log, 'wb') as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env, cwd=cwd)
    tail = open(log, 'rb').read().decode('utf-8', 'replace').replace('\r\n', '\n').split('\n')
    tail = [l for l in tail if l.strip()][-25:]
    say('  exit code %d after %.0f s, log %s' % (r.returncode, time.time() - t0, log))
    return r.returncode, tail


def require_ok(code, tail, what, log):
    if code:
        say('  last lines of the log:')
        for l in tail:
            say('    ' + l)
        # the converters mark a failed check with [FAIL] (or FAIL at the start of the line): those are the reason
        text = open(log, 'rb').read().decode('utf-8', 'replace').replace('\r\n', '\n').split('\n')
        fails = [l.strip() for l in text if '[FAIL]' in l or l.startswith('FAIL')][:8]
        if any(l.startswith('Traceback') for l in text) and tail:
            fails.insert(0, 'Python error: ' + tail[-1].strip())   # the exception line ends the traceback
        if fails:
            say('  failed checks:')
            for l in fails:
                say('    ' + l)
        reason = (': ' + fails[0][:200]) if fails else ''
        raise BuildError('%s failed (exit code %d)%s. Details: %s' % (what, code, reason, log), fails + tail[-6:])


def extract(pkg, dst):
    """The whole package into dst (as stfs.py extract), every file's size checked."""
    import stfs
    s = stfs.Stfs(pkg['path'])
    fs = s.files()
    n = total = 0
    for i, e in enumerate(fs):
        rel = s.path_of(fs, i)
        if '..' in rel.replace('\\', '/').split('/') or rel.startswith(('/', '\\')) or ':' in rel:
            raise BuildError('%s: unexpected file name in the package: %r' % (pkg['name'], rel))
        p = os.path.join(dst, *rel.split('/'))
        if e['dir']:
            os.makedirs(p, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = s.read_file(e)
        if len(data) != e['size']:
            raise BuildError('%s: %s is cut short in the package (%d of %d bytes); copy the package from the '
                             'console again.' % (pkg['name'], rel, len(data), e['size']))
        with open(p, 'wb') as w:
            w.write(data)
        n += 1
        total += len(data)
    s.f.close()
    return n, total


def find_ci(root, rel):
    """root/rel with any case of each part, or None."""
    p = root
    for part in rel.replace('\\', '/').split('/'):
        if not os.path.isdir(p):
            return None
        hit = next((n for n in os.listdir(p) if n.lower() == part.lower()), None)
        if hit is None:
            return None
        p = os.path.join(p, hit)
    return p


def read_bundle(folder=None):
    """The DLC tools bundle's version file ({key: value}, 'exe' lines as {name: path}), or None when there is none or
    its format is unknown."""
    folder = folder or BUNDLE_DIR
    fn = os.path.join(folder, BUNDLE_VERSION)
    try:
        lines = open(fn, encoding='utf-8-sig').read().splitlines()
    except OSError:
        return None
    b = {'exe': {}, 'folder': folder}
    for line in lines:
        f = line.split('\t')
        if line.startswith('#') or len(f) < 2:
            continue
        if f[0] == 'exe' and len(f) >= 3:
            b['exe'][f[1]] = os.path.join(folder, *f[2].split('/'))
        else:
            b[f[0]] = f[1]
    try:
        if int(b.get('format', '0')) not in BUNDLE_FORMATS:
            return None
    except ValueError:
        return None
    return b


def bundle_exe(name, b=None):
    """<bundle>\\python\\python.exe, ...\\vgmstream\\vgmstream-cli.exe, ...\\ffmpeg\\ffmpeg.exe when the bundle has it."""
    b = b or read_bundle()
    if not b:
        return None
    p = os.path.join(b['folder'], *BUNDLE_EXES[name])
    return p if os.path.isfile(p) else None


def same_file(p, q):
    try:
        return os.path.samefile(p, q)
    except OSError:
        return norm(p) == norm(q)


def check_bundle(b):
    """Problems of the bundle against its manifest (each file's size and SHA-256): [] when complete."""
    fn = os.path.join(b['folder'], BUNDLE_MANIFEST)
    if not os.path.isfile(fn):
        return ['the bundle has no %s' % BUNDLE_MANIFEST]
    probs, n = [], 0
    for line in open(fn, encoding='utf-8-sig'):
        f = line.rstrip('\r\n').split('\t')
        if f[0] != 'file' or len(f) < 4:
            continue
        p = os.path.join(b['folder'], *f[1].split('/'))
        n += 1
        if not os.path.isfile(p):
            probs.append('missing: %s' % f[1])
        elif os.path.getsize(p) != int(f[2]) or sha256(p) != f[3]:
            probs.append('changed or damaged: %s' % f[1])
    return probs if n else ['%s lists no files' % BUNDLE_MANIFEST]


def find_exe(given, name):
    """--vgmstream/--ffmpeg, else the DLC tools bundle's, else PATH, else <dir>\\<name>.exe or
    <dir>\\{vgmstream,ffmpeg,bin}\\<name>.exe of EXE_DIRS."""
    if given:
        return given if os.path.isfile(given) else None
    hit = bundle_exe(name) or shutil.which(name)
    if hit:
        return hit
    for own in EXE_DIRS:
        for d in [os.path.join(own, name + '.exe')] + [os.path.join(own, sub, name + '.exe') for sub in ('vgmstream', 'ffmpeg', 'bin')]:
            if os.path.isfile(d):
                return d
    return None


def python_flags():
    """-I if numpy is found that way, else -E (numpy in the user site-packages, which -I hides)."""
    for flags in (['-I'], ['-E']):
        r = subprocess.run([sys.executable] + flags + ['-c', 'import numpy'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode == 0:
            return flags
    return None


def d3dcompiler_ok():
    """D3DCompiler_41.dll loads from System32 (as xenos/d3dcomp.py loads it), with this Python's bitness."""
    import ctypes
    fn = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'D3DCompiler_41.dll')
    if not os.path.isfile(fn):
        return False
    try:
        return bool(ctypes.WinDLL(fn).D3DCompile)
    except (OSError, AttributeError):
        return False


def requirements(a, want_track):
    """What the DLC tracks need besides Python: (want_track, vgmstream, ffmpeg, python flags, warnings). A missing
    piece leaves the track out with a warning; the cars need nothing of it."""
    warnings = []
    vgm = ffm = None
    if want_track:
        vgm = find_exe(a.vgmstream, 'vgmstream-cli')
        ffm = find_exe(a.ffmpeg, 'ffmpeg')
        if not vgm or not ffm:
            warnings.append('The DLC tracks left out: their sound banks need %s (see tools\\dlc\\README_build.md). Only the cars '
                            'are built.%s' % (' and '.join(n for n, x in (('vgmstream-cli', vgm), ('ffmpeg', ffm)) if not x), SETUP_HINT))
            want_track = False
        else:
            say('vgmstream-cli: %s' % vgm)
            say('ffmpeg: %s' % ffm)
    if want_track and not d3dcompiler_ok():
        # the track's shaders are translated with the compiler the PC shaders were made with; fxc.exe (d3dcompiler_47)
        # makes wrong code for some of them (xenos/d3dcomp.py), so it is never used instead
        warnings.append('The DLC tracks left out: D3DCompiler_41.dll (DirectX End-User Runtime, June 2010) is not installed. '
                        'Only the cars are built.' + SETUP_HINT)
        want_track = False
    flags = ['-I']
    if want_track:
        flags = python_flags()
        if flags is None:
            warnings.append('The DLC tracks left out: numpy is missing in this Python (%s). Only the cars are built.%s'
                            % (sys.executable, SETUP_HINT))
            want_track, flags = False, ['-I']
        else:
            say('numpy found (steps run with %s)' % ' '.join(flags))
    return want_track, vgm, ffm, flags, warnings


# ------------------------------------------------------------------------------------------------- the tools folder
def check_tools_files():
    """(problems, number of files checked) against FILES_LIST (written by tools/make_release.py into a release; a
    development checkout has none and is not checked)."""
    fn = os.path.join(HERE, FILES_LIST)
    if not os.path.isfile(fn):
        return [], 0
    probs, n = [], 0
    for line in open(fn, encoding='utf-8'):
        f = line.rstrip('\r\n').split('\t')
        if len(f) < 3 or line.startswith('#'):
            continue
        rel, size, sha = f[0], int(f[1]), f[2]
        p = os.path.join(HERE, *rel.split('/'))
        n += 1
        if not os.path.isfile(p):
            probs.append('missing: %s' % rel)
        elif os.path.getsize(p) != size or sha256(p) != sha:
            probs.append('changed or damaged: %s' % rel)
    return probs, n


def load_scripts(py, env, scripts):
    """Loads each converter once (its module code and imports, not its main): {script: error text or ''}."""
    code = ('import importlib.util, sys\n'
            'for i, p in enumerate(sys.argv[1:]):\n'
            '    sys.argv = [p, "--help"]   # a script that parses its arguments at load time stops there (exit 0)\n'
            '    try:\n'
            '        s = importlib.util.spec_from_file_location("glimpse_probe_%d" % i, p)\n'
            '        s.loader.exec_module(importlib.util.module_from_spec(s))\n'
            '        print("ok\\t" + p)\n'
            '    except SystemExit as ex:\n'
            '        print(("ok\\t%s" % p) if not ex.code else ("error\\t%s\\tSystemExit: %s" % (p, ex.code)))\n'
            '    except BaseException as ex:\n'
            '        print("error\\t%s\\t%s: %s" % (p, type(ex).__name__, str(ex).replace("\\n", " ")))\n')
    paths = [os.path.join(HERE, *s.split('/')) for s in scripts]
    out = {s: 'missing' for s, p in zip(scripts, paths) if not os.path.isfile(p)}
    todo = [(s, p) for s, p in zip(scripts, paths) if s not in out]
    if todo:
        r = subprocess.run(py + ['-c', code] + [p for _, p in todo], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env=env, cwd=HERE)
        got = {}
        for line in r.stdout.decode('utf-8', 'replace').splitlines():
            f = line.split('\t')
            if len(f) >= 2 and f[0] in ('ok', 'error'):
                got[norm(f[1])] = '' if f[0] == 'ok' else (f[2] if len(f) > 2 else 'error')
        for s, p in todo:
            out[s] = got.get(norm(p), 'did not load (exit code %d)' % r.returncode)
    return out


def converter_env(resident):
    """The environment of every converter run: this game folder's archive, nothing of a developer's setup."""
    env = dict(os.environ)
    env['GLIMPSE_PC_RESIDENT'] = resident   # tools/dlc/pair_lib.py: the PC archive of this game folder
    env.pop('GLIMPSE_XB_RESIDENT', None)    # no Xbox base archive: a build here is what every user gets
    env.pop('GLIMPSE_HKREF', None)          # set to the hkref.json of this build (make_hkref)
    env.pop(TU4_ENV, None)                  # set by build() only when given (--tu4-texts or the variable)
    env.pop('PYTHONPATH', None)
    return env


# ------------------------------------------------------------------------------------------------- hkref.json
def hkref_inputs(game):
    """convhkx.py learn's inputs (havok/README.md): the __types__ of the PC archives, then the exe's hkClass registry."""
    return [os.path.join(game, 'data', '*.ark'), os.path.join(game, 'data', 'environments', 'levels', '*.ark'),
            os.path.join(game, 'SplitSecond.exe')]


def hkref_digest(fn):
    """SHA-256 of the classes and enums of an hkref.json (not of its 'sources', which only name where each came from)."""
    r = json.load(open(fn, encoding='utf-8'))
    return hashlib.sha256(json.dumps({'classes': r.get('classes', {}), 'enums': r.get('enums', {})}, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def make_hkref(a, work, game, logs, env, py):
    """<work>\\hkref.json from this game folder (a step: redone when the exe or an archive changes), or --hkref.
    Returns (path, notes)."""
    if a.hkref:
        fn = os.path.abspath(a.hkref)
        if not os.path.isfile(fn):
            raise BuildError('--hkref %s does not exist.' % fn)
        say('hkref.json: %s (given)' % fn)
        return fn, []
    import glob
    exe = os.path.join(game, 'SplitSecond.exe')
    if not os.path.isfile(exe):
        raise BuildError('No SplitSecond.exe in %s: the Havok class layouts are read from it.' % game)
    pats = hkref_inputs(game)
    arks = sorted(f for p in pats[:2] for f in glob.glob(p))
    out = os.path.join(work, HKREF)
    st = Step(work, 'hkref', dict(inputs=[file_sig(f) for f in arks + [exe]], tool=tools_signature({'convhkx.py'})),
              [out], a.force)
    if st.up_to_date():
        say('hkref.json: up to date (%s)' % out)
    else:
        st.invalidate()
        tmp = out + '.new'
        if os.path.isfile(tmp):
            os.remove(tmp)
        log = os.path.join(logs, 'hkref.log')
        code, tail = run_tool(py + [os.path.join(HERE, 'havok', 'convhkx.py'), 'learn'] + pats, log,
                              dict(env, GLIMPSE_HKREF=tmp), work)
        require_ok(code, tail, 'Reading the Havok class layouts from SplitSecond.exe', log)
        try:
            n = len(json.load(open(tmp, encoding='utf-8'))['classes'])
        except (OSError, ValueError, KeyError):
            n = 0
        if not n:
            raise BuildError('No Havok class layouts found in %s and its archives. Details: %s' % (game, log))
        os.replace(tmp, out)
        st.done()
        say('hkref.json: %d classes from %d archives and SplitSecond.exe -> %s' % (n, len(arks), out))
    notes = []
    if hkref_digest(out) != HKREF_TESTED:
        notes.append('The Havok class layouts of this SplitSecond.exe differ from the tested Steam build; the cars and '
                     'the track may not load (see %s).' % out)
    return out, notes


# ------------------------------------------------------------------------------------------------- staging
def staged_files(spec, track_out):
    """(staging rel path, source, what) of one track build, below <dir>/data: its level archives (required) and the
    loose files its manifest lists, nothing else. A file below <out>\\data that the manifest does not list is not from
    this run (the step clears its folder first): refused."""
    t, d = spec['track'].lower(), spec['dir']
    rels = ['data/environments/levels/%s.ark' % t, 'data/%spowerplays.ark' % t,
            'data/environments/levels/%s/sectors/%s.static.ark' % (t, t),
            'data/environments/levels/%s/sectors/%s.stream.ark' % (t, t)]
    man = os.path.join(track_out, 'track_%s.manifest.tsv' % t)
    for line in open(man, encoding='ascii', errors='replace'):
        f = line.rstrip('\n').split('\t')
        if len(f) >= 7 and f[0] == 'loose':
            rels.append(f[6].replace('\\', '/'))
    have = {r.lower() for r in rels}
    extra = []
    for dp, dn, fns in os.walk(os.path.join(track_out, 'data')):
        for n in sorted(fns):
            r = os.path.relpath(os.path.join(dp, n), track_out).replace(os.sep, '/')
            if r.lower() not in have:
                extra.append(r)
    if extra:
        raise BuildError('%s holds %d file(s) that its manifest %s does not list, e.g. %s: not from this build. Run the '
                         'build again with --force.' % (track_out, len(extra), os.path.basename(man), extra[0]), extra[:20])
    return [('%s/%s' % (d, r.lower()), os.path.join(track_out, *r.split('/')), 'track %s: %s' % (spec['track'], r))
            for r in rels]


def data_dirs(rels):
    """The folders below glimpse_dlc that hold loose files: DATA_DIRS and the first part of every rel with one."""
    return sorted(set(DATA_DIRS) | {r.split('/')[0] for r in rels if '/' in r})


def read_manifest(folder, name=MANIFEST):
    """{'files': [(rel, size, sha, what)], 'ini': [(key, value)], 'meta': {k: v}} or None if there is none."""
    fn = os.path.join(folder, name)
    if not os.path.isfile(fn):
        return None
    m = dict(files=[], ini=[], meta={}, origin={}, aside=[])
    for line in open(fn, encoding='utf-8'):
        line = line.rstrip('\r\n')
        if not line or line.startswith('#'):
            continue
        f = line.split('\t')
        if f[0] == 'file' and len(f) >= 4:
            m['files'].append((f[1], int(f[2]), f[3], f[4] if len(f) > 4 else ''))
            m['origin'][f[1]] = f[5] if len(f) > 5 else 'copied'
        elif f[0] == 'aside' and len(f) >= 4:
            m['aside'].append((f[1], f[2], int(f[3])))   # glimpse_dlc path, where it went (below the game), size
        elif f[0] == 'ini' and len(f) >= 3:
            m['ini'].append((f[1], f[2]))
        elif len(f) >= 2:
            m['meta'][f[0]] = f[1]
    return m


def safe_rel(rel):
    parts = rel.split('/')
    return (rel and not rel.startswith('/') and ':' not in rel and '\\' not in rel and
            all(p and p not in ('.', '..') for p in parts) and
            all(c.isalnum() or c in '._-/' for c in rel))


def write_manifest(folder, files, ini, meta, name=MANIFEST, origin=None, aside=()):
    """origin {rel: 'copied'|'adopted'} and aside [(rel, moved to, size)]: install_dlc.py's list of what it did."""
    fn = os.path.join(folder, name)
    with open(fn + '.tmp', 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('# glimpse_fix: the converted DLC files for <game>\\glimpse_dlc\\, made by tools\\dlc\\build_all.py on this PC\n')
        f.write('# from your own Xbox 360 DLC packages. They hold the DLC\'s content: keep them for your own use.\n')
        f.write('version\t%d\n' % MANIFEST_VERSION)
        for k in sorted(meta):
            f.write('%s\t%s\n' % (k, meta[k]))
        for k, v in ini:
            f.write('ini\t%s\t%s\n' % (k, v))
        for rel, size, sha, what in files:
            f.write('file\t%s\t%d\t%s\t%s%s\n' % (rel, size, sha, what, ('\t' + origin[rel]) if origin else ''))
        for rel, to, size in aside:
            f.write('aside\t%s\t%s\t%d\n' % (rel, to, size))
    os.replace(fn + '.tmp', fn)


def stage(staging, items, ini, meta):
    """Copies items [(rel, src, what)] into staging; files of the previous manifest that are no longer staged go."""
    bad = [rel for rel, _, _ in items if not safe_rel(rel)]
    if bad:   # every path the manifest will name: plain names below the staging folder (check_staging requires it too)
        raise BuildError('Internal: unsafe file name(s) to stage: %s' % ', '.join(repr(r) for r in bad[:5]), bad[:20])
    os.makedirs(staging, exist_ok=True)
    old = read_manifest(staging)
    present = [n for n in os.listdir(staging) if n != MANIFEST]
    if old is None and present:
        raise BuildError('The staging folder %s holds other files and no %s. Use an empty or new folder '
                         '(--staging).' % (staging, MANIFEST))
    new_rels = {rel for rel, _, _ in items}
    files = []
    for rel, src, what in items:
        if not os.path.isfile(src):
            raise BuildError('Missing build output %s' % src)
        dst = os.path.join(staging, *rel.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        size = os.path.getsize(src)
        # a file of the previous staging with the same size and hash as the new output stays as it is
        prev = next((x for x in (old['files'] if old else []) if x[0] == rel), None)
        sha_src = sha256(src)
        if prev and os.path.isfile(dst) and os.path.getsize(dst) == size and prev[2] == sha_src and sha256(dst) == sha_src:
            say('  same as before  %-62s %12d' % (rel, size))
        else:
            shutil.copyfile(src, dst + '.part')
            os.replace(dst + '.part', dst)
            say('  copied          %-62s %12d' % (rel, size))
        files.append((rel, size, sha_src, what))
    for rel, size, sha, what in (old['files'] if old else []):
        if rel in new_rels or not safe_rel(rel):
            continue
        dst = os.path.join(staging, *rel.split('/'))
        if os.path.isfile(dst):
            os.remove(dst)
            say('  removed (no longer built) ' + rel)
    # a copy cut short by an earlier, stopped run (<file>.part next to a file this or the previous manifest names)
    for rel in new_rels | {r for r, _, _, _ in (old['files'] if old else []) if safe_rel(r)}:
        part = os.path.join(staging, *rel.split('/')) + '.part'
        if os.path.isfile(part):
            os.remove(part)
    remove_empty_dirs(staging)
    write_manifest(staging, files, ini, meta)
    return files


def remove_empty_dirs(top):
    for dp, dn, fns in os.walk(top, topdown=False):
        if dp != top and not os.listdir(dp):
            os.rmdir(dp)


# ------------------------------------------------------------------------------------------------- check
# Keys no staged archive may hold. UI/Network/leaderboard_def.xml: the Deadline package brings its own
# (UI/NETWORK/LEADERBOARD_DEF.XML, with TimeTrialPlus boards); laid over the PC's, the game would keep and upload
# records for the converted mode. key = h*31 + c over the lower-case path (misc/arkio.py key()).
FORBIDDEN_KEYS = {0x6CE14990: 'UI/Network/leaderboard_def.xml'}


def check_ark(fn):
    """Problems of a PC .ark: version, header size, key order, entries inside the file, keys that must not be there."""
    probs = []
    size = os.path.getsize(fn)
    with open(fn, 'rb') as f:
        head = f.read(24)
        if len(head) < 24:
            return ['shorter than an archive header']
        ver, hsize, count, offa, cb, offb = struct.unpack_from('<6I', head, 0)
        if ver != 0x00010000:
            return ['not a PC archive (version %08X)' % ver]
        if hsize != 0x10 + 20 * count or offa != 0x10 or cb != 0:
            probs.append('header: hsize %X for %d entries' % (hsize, count))
        idx = f.read(20 * count)
    if len(idx) != 20 * count:
        return probs + ['index cut short']
    last = -1
    for i in range(count):
        t, off, cs, us, k = struct.unpack_from('<5I', idx, 20 * i)
        if k <= last:
            probs.append('keys not sorted or not unique at entry %d' % i)
            break
        last = k
        if k in FORBIDDEN_KEYS:
            probs.append('holds %s (key %08x): the DLC\'s leaderboard definitions must never be installed (records '
                         'and uploads for the converted mode); rebuild without it' % (FORBIDDEN_KEYS[k], k))
        if t not in (0, 1) or off < 8 + hsize or off + cs > size or (t == 0 and cs != us):
            probs.append('entry %d (key %08x) outside the file or of unknown type %d' % (i, k, t))
            break
    return probs


def check_staging(staging, game=None, quick=False):
    """(errors, notes, manifest); with game also how it compares to <game>\\glimpse_dlc (read only)."""
    errors, notes = [], []
    m = read_manifest(staging)
    if m is None:
        return ['No converted DLC in %s yet (no %s): build it first.' % (staging, MANIFEST)], notes, None
    if int(m['meta'].get('version', '0')) != MANIFEST_VERSION:
        errors.append('%s: unknown manifest version %s' % (MANIFEST, m['meta'].get('version')))
    if not m['files']:
        errors.append('%s lists no files' % MANIFEST)
    listed = set()
    for rel, size, sha, what in m['files']:
        listed.add(os.path.normcase(rel.replace('/', os.sep)))
        if not safe_rel(rel):
            errors.append('unsafe file name in the manifest: %r' % rel)
            continue
        if rel.lower().endswith('leaderboard_def.xml'):
            errors.append('%s: the DLC\'s leaderboard definitions must never be installed (records and uploads for '
                          'the converted mode)' % rel)
        fn = os.path.join(staging, *rel.split('/'))
        if not os.path.isfile(fn):
            errors.append('missing: %s' % rel)
            continue
        if os.path.getsize(fn) != size:
            errors.append('%s: %d bytes, the manifest says %d' % (rel, os.path.getsize(fn), size))
            continue
        if not quick and sha256(fn) != sha:
            errors.append('%s: the content differs from the build (SHA-256)' % rel)
            continue
        if rel.endswith('.ark'):
            for p in check_ark(fn):
                errors.append('%s: %s' % (rel, p))
    tops = [r for r, _, _, _ in m['files'] if '/' not in r and r.endswith('.ark')]
    tracks = [r for r in tops if r.startswith('track_')]
    track_dirs = {t['dir'] for t in TRACK_SPECS}
    if tracks and not any(r.split('/')[0] in track_dirs and '/data/' in r for r, _, _, _ in m['files']):
        errors.append('a track overlay without the track\'s level files')
    if any(r.split('/')[0] in track_dirs for r, _, _, _ in m['files']) and not tracks:
        errors.append('the track\'s level files without a track overlay')
    for t in TRACK_SPECS:   # every track folder with all four level archives
        if any(r.startswith(t['dir'] + '/') for r, _, _, _ in m['files']):
            lv = '%s/data/environments/levels/%s' % (t['dir'], t['track'].lower())
            for need in (lv + '.ark', '%s/sectors/%s.static.ark' % (lv, t['track'].lower())):
                if need not in {r for r, _, _, _ in m['files']}:
                    errors.append('track %s without %s' % (t['track'], need))
    # the staging folder holds exactly the files of the build that wrote the manifest: anything else is refused (only
    # Explorer's own folder files are tolerated; install_dlc.py copies only what the manifest lists in any case)
    for dp, dn, fns in os.walk(staging):
        for n in fns:
            rel = os.path.relpath(os.path.join(dp, n), staging)
            if (dp == staging and n == MANIFEST) or os.path.normcase(rel) in listed:
                continue
            if n.lower() in ('desktop.ini', 'thumbs.db'):
                notes.append('not in the manifest (Explorer\'s folder file, not installed): %s' % rel.replace(os.sep, '/'))
            else:
                errors.append('not in the manifest, not from the build that wrote it: %s (remove it, or build into '
                              'a new staging folder)' % rel.replace(os.sep, '/'))
    if game:
        gd = os.path.join(game, 'glimpse_dlc')
        for rel, size, sha, what in m['files']:
            fn = os.path.join(gd, *rel.split('/'))
            if not os.path.isfile(fn):
                state = 'not installed'
            elif os.path.getsize(fn) != size:
                state = 'installed, DIFFERENT (%d bytes)' % os.path.getsize(fn)
            elif quick:
                state = 'installed, same size'
            else:
                state = 'installed, identical' if sha256(fn) == sha else 'installed, DIFFERENT content'
            notes.append('game folder: %-58s %s' % (rel, state))
        if os.path.isdir(gd):
            for n in sorted(os.listdir(gd)):
                if n.lower().endswith('.ark') and n not in tops:
                    notes.append('game folder: glimpse_dlc\\%s is not from this build; it is laid over the game too' % n)
    return errors, notes, m


# ------------------------------------------------------------------------------------------------- build
def build(a, status):
    t_start = time.time()
    warnings = []
    game, resident = find_game(a.game)
    work = os.path.abspath(a.work)
    staging = os.path.abspath(a.staging)
    # as given and with links / junctions resolved (overlap)
    for what, p in (('work', work), ('staging', staging)):
        if overlap(p, game):
            raise BuildError('The %s folder %s lies in the game folder or holds it; choose one outside %s.' % (what, p, game))
        if overlap(p, a.dump):
            raise BuildError('The %s folder %s and the DLC folder %s overlap; choose another %s folder.' % (what, p, a.dump, what))
    if overlap(work, staging):
        raise BuildError('The work and staging folders must not lie inside each other.')
    say(claim_work(work, write=True))
    logs = os.path.join(work, 'logs')
    os.makedirs(logs, exist_ok=True)

    # ---- preflight
    status.step('Checking Python, the game and the DLC folder')
    say('Python %s (%s)' % (sys.version.split()[0], sys.executable))
    if sys.version_info < (3, 8):
        raise BuildError('Python 3.8 or newer is needed (this is %s).' % sys.version.split()[0])
    b = read_bundle()
    if b:
        say('DLC tools bundle %s (%s)' % (b.get('bundle', '?'), b['folder']))
    say('game: %s (resident.ark %d bytes)' % (game, os.path.getsize(resident)))
    pkgs, others, by_role = find_packages(a.dump)
    for p in pkgs:
        if p['used'] and p['role'] in NOT_CONVERTED:
            what = '%s: %s' % (ROLE_NAMES[p['role']], NOT_CONVERTED[p['role']])
        elif p['used']:
            what = 'used: ' + ROLE_NAMES[p['role']]
        elif p['role']:
            what = 'not used: an older copy of %s' % ROLE_NAMES[p['role']]
        else:
            what = 'unknown package, not converted'
        say('package %-40s %-28s %5d files %12d bytes  %s' % (p['name'][:40], p['info'], p['count'], p['size'], what))
    for o in others:
        say('skipped ' + o)
    car_roles = [r for r in CAR_ROLES if r in by_role]
    if not car_roles:
        raise BuildError('No car package (Deadline, High Octane, The Elite) is in %s. Give the folder 425607E7 (or '
                         '00000002) of your Xbox 360 dump.' % a.dump, ['%d Split/Second packages found' % len(pkgs)] + others[:5])
    if 'deadline' not in by_role:
        warnings.append('The Deadline pack is not in the DLC folder: no game mode Deadline, and the car select uses '
                        'the UI of %s.' % ROLE_NAMES[car_roles[0]])
    specs = [t for t in TRACK_SPECS if t['role'] in by_role] if not a.no_track else []
    if not a.no_track:
        for t in TRACK_SPECS:
            if t['role'] not in by_role:
                warnings.append('The %s pack (track %s) is not in the DLC folder: built without it.'
                                % (ROLE_NAMES[t['role']], t['track']))
    want_track, vgm, ffm, flags, more = requirements(a, bool(specs))
    warnings += more
    if not want_track:
        specs = []
    # the steps after the preflight: extract, merge, frontend, pack, per track audio + track (+ its mode), stage, check
    status.total = 7 + 2 * len(specs) + sum(1 for t in specs if t['mode'])
    tu4 = os.path.abspath(a.tu4_texts) if a.tu4_texts else os.environ.get(TU4_ENV, '')
    if tu4 and not os.path.isfile(tu4):
        raise BuildError('The TU4 text table %s does not exist (--tu4-texts / %s).' % (tu4, TU4_ENV))
    if tu4:
        say('TU4 texts: %s (optional: a TU4 text table (JSON) made outside glimpse_fix; the converters that know '
            'them use the official texts)' % tu4)
    else:
        say('TU4 texts: none (the release cannot produce a TU4 text table; the converters write their own texts)')
    probs, nfiles = check_tools_files()
    if probs:
        raise BuildError('The converter folder %s is not complete: %s. Unpack the release again (the whole folder '
                         'glimpse_tools).' % (HERE, probs[0]), probs)
    for w in warnings:
        say('NOTE: ' + w)
    free = shutil.disk_usage(work).free
    if free < NEED_FREE_WORK:
        raise BuildError('Only %s free on the drive of %s; the build needs about %s.' % (human(free), work, human(NEED_FREE_WORK)))

    env = converter_env(resident)
    # -B: no __pycache__ next to the converters (the tools folder may lie in the game folder)
    py = [sys.executable] + flags + ['-B', '-X', 'utf8']
    status.write('RUNNING', 'Reading the Havok class layouts from SplitSecond.exe (about 15 s the first time)')
    hkref, more = make_hkref(a, work, game, logs, env, py)
    warnings += more
    for w in more:
        say('NOTE: ' + w)
    env['GLIMPSE_HKREF'] = hkref            # havok/convhkx.py (assemble_car.py, assemble_track.py) reads it there
    hk_sig = sha256(hkref)
    tools = tools_signature()
    if tu4:
        env[TU4_ENV] = tu4                  # mode/tu4text.py (cartext.py, deadline_mode.py, ...) reads it there
        tools = hashlib.sha256((tools + '|tu4|' + sha256(tu4)).encode()).hexdigest()
    else:
        env.pop(TU4_ENV, None)
    res_sig = file_sig(resident)

    # ---- 1. extract
    roles = car_roles + [t['role'] for t in specs]
    status.step('Extracting the DLC packages')
    pkg_dir = {}
    for role in roles:
        p = by_role[role]
        dst = os.path.join(work, 'packages', role)
        st = Step(work, 'extract_' + role, dict(pkg=file_sig(p['path']), count=p['count'], size=p['size'],
                                                tool=tools_signature({'stfs.py'})),
                  [os.path.join(dst, 'DLCINFO.TXT')], a.force)
        pkg_dir[role] = dst
        if st.up_to_date():
            say('  %s: up to date (%s)' % (p['name'], dst))
            continue
        st.invalidate()
        clear_output(dst, work)       # only this package's folder: nothing of another package version stays
        say('  %s -> %s' % (p['name'], dst))
        status.write('RUNNING', 'Extracting %s (%s)' % (p['name'], human(p['size'])))
        n, total = extract(p, dst)
        if total != p['size']:
            raise BuildError('%s: extracted %d bytes, the package lists %d.' % (p['name'], total, p['size']))
        say('  %d files, %d bytes' % (n, total))
        st.done()

    # the cars of each package (those whose body folder it has), in the order of the tested packs
    models, car_dirs = [], []
    for role in car_roles:
        for mo in CAR_MODELS[role]:
            body = find_ci(pkg_dir[role], 'VEHICLES/BODIES/' + mo)
            fe = find_ci(pkg_dir[role], 'VEHICLES/FRONTEND/BODIES/' + mo)
            if not body or not fe:
                warnings.append('%s: %s is not in the package, left out.' % (ROLE_NAMES[role], mo))
                continue
            models.append(mo)
            car_dirs.append(fe)
    if not models:
        raise BuildError('None of the DLC cars was found in the car packages.')
    say('cars: %s' % ', '.join(models))
    pkg_sigs = {r: file_sig(by_role[r]['path']) for r in car_roles}

    # ---- 2. merge (more than one car package: one source folder, the first package's UI)
    status.step('Merging the car packages' if len(car_roles) > 1 else 'One car package: nothing to merge')
    if len(car_roles) > 1:
        st_mg = Step(work, 'merge', dict(pkgs=pkg_sigs, tools=tools_signature({'merge_dlc.py', 'flashtex.py', 'gfxcars.py',
                                                                                'convtex.py', 'arkio.py'})),
                     [], a.force)
        # merge_dlc.py takes an empty folder: one per input signature, the previous one is removed
        src = os.path.join(work, 'merged_' + st_mg.sig[:12])
        st_mg.outputs = [os.path.join(src, 'MERGE_REPORT.txt')]
        if st_mg.up_to_date():
            say('  up to date (%s)' % src)
        else:
            st_mg.invalidate()
            for n in os.listdir(work):
                old = os.path.join(work, n)
                if n.startswith('merged_') and os.path.isdir(old):
                    clear_output(old, work)
            log = os.path.join(logs, 'merge.log')
            cmd = py + [os.path.join(HERE, 'assemble', 'merge_dlc.py'), '--base', pkg_dir[car_roles[0]]]
            for r in car_roles[1:]:
                cmd += ['--add', pkg_dir[r]]
            code, tail = run_tool(cmd + ['--out', src], log, env, work)
            require_ok(code, tail, 'Merging the car packages', log)
            st_mg.done()
        mg_sig = st_mg.sig
    else:
        src, mg_sig = pkg_dir[car_roles[0]], ''

    # ---- 3. frontend (car select bodies)
    status.step('Converting the car select models (about 30 s)')
    fe_out = os.path.join(work, 'frontend')
    st_fe = Step(work, 'frontend', dict(pkgs=pkg_sigs, res=res_sig, tools=tools, cars=models),
                 [os.path.join(fe_out, m.lower(), m.lower() + '.model') for m in models], a.force)
    if st_fe.up_to_date():
        say('  up to date (%s)' % fe_out)
    else:
        st_fe.invalidate()
        clear_output(fe_out, work)    # no car select body of a car that is no longer built
        code, tail = run_tool(py + [os.path.join(HERE, 'frontend', 'convfrontend.py')] + car_dirs + ['--out', fe_out, '--pc', resident],
                              os.path.join(logs, 'frontend.log'), env, work)
        require_ok(code, tail, 'Converting the car select models', os.path.join(logs, 'frontend.log'))
        st_fe.done()

    # ---- 4. car pack
    status.step('Building the car pack (about 1 minute)')
    pack_dir = os.path.join(work, 'pack')
    os.makedirs(pack_dir, exist_ok=True)
    bp = os.path.join(HERE, 'assemble', 'build_pack.py')
    pack_help = subprocess.run(py + [bp, '--help'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env).stdout.decode('utf-8', 'replace')
    extra = []
    if not a.no_mode and 'deadline' in by_role:
        if '--mode' in pack_help:
            extra.append('--mode')
        else:
            warnings.append('This build_pack.py has no --mode: the game mode Deadline is left out.')
    # Deadline's sounds: the package's DLCMenu bank (AUDIO/RELEASE/MENU) converted by audio/dlcaudio.py, put into the
    # mode pack by build_pack.py --mode-audio (deadline_mode.py); the DLL plays them in a Deadline race
    if extra and '--mode-audio' in pack_help:
        menu_dir = find_ci(pkg_dir['deadline'], 'AUDIO/RELEASE/MENU')
        mvgm = vgm or find_exe(a.vgmstream, 'vgmstream-cli')
        mffm = ffm or find_exe(a.ffmpeg, 'ffmpeg')
        if not menu_dir:
            warnings.append('The Deadline package has no AUDIO/RELEASE/MENU: the mode has no pickup sounds.')
        elif not mvgm or not mffm:
            warnings.append('Deadline\'s pickup sounds left out: the DLCMenu bank needs vgmstream-cli and ffmpeg.')
        else:
            mn_src = os.path.join(work, 'audio_menu', 'src')
            mn_out = os.path.join(work, 'audio_menu', 'out')
            st_mn = Step(work, 'audio_menu', dict(pkg=file_sig(by_role['deadline']['path']), res=res_sig, tools=tools,
                                                  vgm=file_sig(mvgm), ffm=file_sig(mffm)),
                         [os.path.join(mn_out, 'audio', 'release', 'menu', 'dlcmenu.fsb')], a.force)
            if st_mn.up_to_date():
                say('  Deadline sounds: up to date (%s)' % mn_out)
            else:
                st_mn.invalidate()
                clear_output(os.path.dirname(mn_src), work)   # only this step's folder (its copy and its output)
                dst = os.path.join(mn_src, 'AUDIO', 'RELEASE', 'MENU')
                os.makedirs(dst, exist_ok=True)
                for n in sorted(os.listdir(menu_dir)):
                    if os.path.isfile(os.path.join(menu_dir, n)):
                        shutil.copyfile(os.path.join(menu_dir, n), os.path.join(dst, n))
                log = os.path.join(logs, 'audio_menu.log')
                code, tail = run_tool(py + [os.path.abspath(__file__), '_dlcaudio', '--vgmstream', mvgm, '--ffmpeg', mffm,
                                            '--', mn_out, mn_src, '--game', game, '--no-reuse'], log, env, work)
                require_ok(code, tail, 'Converting Deadline\'s sounds (DLCMenu)', log)
                st_mn.done()
            extra += ['--mode-audio', mn_out]
    pack = os.path.join(pack_dir, PACK_NAME)
    st_pack = Step(work, 'pack', dict(pkgs=pkg_sigs, merge=mg_sig, res=res_sig, tools=tools, extra=extra, models=models,
                                      fe=st_fe.sig, hkref=hk_sig,
                                      menu=st_mn.sig if '--mode-audio' in extra else ''), [pack], a.force)
    if st_pack.up_to_date():
        say('  up to date (%s)' % pack)
    else:
        st_pack.invalidate()
        clear_output(pack_dir, work)  # build_pack.py's intermediate packs and reports of an earlier run
        os.makedirs(pack_dir, exist_ok=True)
        log = os.path.join(logs, 'pack.log')
        code, tail = run_tool(py + [bp, '--models', ','.join(models), '--dlc', src, '--pc', resident, '--frontend', fe_out,
                                    '--out', pack] + extra, log, env, work)
        require_ok(code, tail, 'Building the car pack', log)
        st_pack.done()
    report = os.path.splitext(pack)[0] + '.report.txt'
    if os.path.isfile(report):
        res_line = [l for l in open(report, encoding='utf-8', errors='replace') if l.startswith('RESULT')]
        say('  %s: %s' % (os.path.basename(report), res_line[-1].strip() if res_line else '?'))

    contents = ' + '.join(ROLE_NAMES[r] for r in car_roles)
    items = [(PACK_NAME, pack, 'overlay: cars of %s (%s)%s' % (contents, ', '.join(models),
                                                               ' + game mode Deadline' if extra else ''))]
    # the mode is built with its pickup files (deadline_mode.py's default), so Deadline is played as on the Xbox:
    # its boxes on (Pickups=1, which adds no DeadlineBonus)
    ini = [('Overlays', '1')] + ([('Modes', '1'), ('Pickups', '1')] if extra else [])

    # Deadline's quick play movie (served from deadline\data by the DLL's redirect while [DLC] Modes=1)
    if extra:
        mv = find_ci(pkg_dir['deadline'], DEADLINE_MOVIE[0])
        if mv:
            items.append((DEADLINE_MOVIE[1], mv, 'game mode Deadline: quick play movie (Bink, as is)'))
        else:
            warnings.append('The Deadline package has no %s: the mode shows the first track movie.' % DEADLINE_MOVIE[0])

    # ---- 5. the DLC tracks, each on the previous one's overlay; 6. their modes
    base, base_sig, built, music = pack, st_pack.sig, [], False
    at = os.path.join(HERE, 'assemble', 'assemble_track.py')
    if specs:
        track_help = subprocess.run(py + [at, '--help'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=env).stdout.decode('utf-8', 'replace')
    for i, t in enumerate(specs):
        src_pkg = pkg_dir[t['role']]
        last = i == len(specs) - 1
        dirs = list(t['audio'])
        if i == 0 and '--no-dlc-music' in track_help:
            # the first track also installs its package's music project (AUDIO/RELEASE/<X>_MUSIC); a later track's
            # package brings the same project, which its base lists already (assemble_track.py does not ship it twice)
            rel_dir = find_ci(src_pkg, 'AUDIO/RELEASE')
            for n in sorted(os.listdir(rel_dir)) if rel_dir else []:
                if n.upper().endswith('_MUSIC') and os.path.isdir(os.path.join(rel_dir, n)):
                    dirs.append('AUDIO/RELEASE/' + n)
                    music = True
        status.step('Converting the sound files of %s (about 1 minute)' % t['name'])
        aud_src = os.path.join(work, 'audio' + t['step'], 'src')
        aud_out = os.path.join(work, 'audio' + t['step'], 'out')
        st_aud = Step(work, 'audio' + t['step'], dict(pkg=file_sig(by_role[t['role']]['path']), res=res_sig, tools=tools,
                                                      vgm=file_sig(vgm), ffm=file_sig(ffm), dirs=dirs, reuse=False),
                      [os.path.join(aud_out, 'manifest.txt')], a.force)
        if st_aud.up_to_date():
            say('  up to date (%s)' % aud_out)
        else:
            st_aud.invalidate()
            clear_output(os.path.dirname(aud_src), work)   # only this step's folder (its copy and its output)
            n = 0
            for rel in dirs:
                src = find_ci(src_pkg, rel)
                if not src:
                    raise BuildError('The %s package has no %s folder.' % (ROLE_NAMES[t['role']], rel))
                for dp, dn, fns in os.walk(src):
                    for fn in fns:
                        r = os.path.relpath(os.path.join(dp, fn), src_pkg)
                        d = os.path.join(aud_src, r)
                        os.makedirs(os.path.dirname(d), exist_ok=True)
                        shutil.copyfile(os.path.join(dp, fn), d)
                        n += 1
            say('  %d audio files of the package' % n)
            log = os.path.join(logs, 'audio%s.log' % t['step'])
            code, tail = run_tool(py + [os.path.abspath(__file__), '_dlcaudio', '--vgmstream', vgm, '--ffmpeg', ffm,
                                        '--', aud_out, aud_src, '--game', game, '--no-reuse'], log, env, work)
            require_ok(code, tail, 'Converting the sound files of %s' % t['name'], log)
            st_aud.done()

        status.step('Converting the track %s (about 5 minutes the first time, 1 minute after that)' % t['name'])
        trk_out = os.path.join(work, 'track' + t['step'])
        # the last overlay is named TRACK_NAME (unless a mode script follows); the others are intermediate steps
        comb = TRACK_NAME if last and not t['mode'] else 'step_%s.ark' % t['dir']
        st_trk = Step(work, 'track' + t['step'], dict(pkg=file_sig(by_role[t['role']]['path']), res=res_sig, tools=tools,
                                                      base=file_sig(base), base_inputs=base_sig, audio=st_aud.sig,
                                                      hkref=hk_sig, combined=comb),
                      [os.path.join(trk_out, 'glimpse_dlc', comb),
                       os.path.join(trk_out, 'track_%s.manifest.tsv' % t['track'].lower())], a.force)
        if st_trk.up_to_date():
            say('  up to date (%s)' % trk_out)
        else:
            st_trk.invalidate()
            clear_output(trk_out, work)   # assemble_track.py never clears its output folder itself
            log = os.path.join(logs, 'track%s.log' % t['step'])
            code, tail = run_tool(py + [at, '--track', t['track'], '--dlc', src_pkg,
                                        '--game', os.path.join(game, 'data'), '--audio', aud_out, '--base', base,
                                        '--combined', comb, '--out', trk_out,
                                        '--xlate-cache', os.path.join(work, 'track_xlate_cache.pkl')], log, env, work)
            require_ok(code, tail, 'Converting the track %s' % t['name'], log)
            st_trk.done()
        items += staged_files(t, trk_out)
        base, base_sig = os.path.join(trk_out, 'glimpse_dlc', comb), st_trk.sig
        built.append(t['name'])

        if t['mode']:
            status.step('Adding the game mode of %s' % t['name'])
            md_out = os.path.join(work, t['mode'])
            md_data = os.path.join(md_out, 'data')
            comb = TRACK_NAME if last else 'step_%s_mode.ark' % t['dir']
            script = os.path.join(HERE, 'mode', t['mode'] + '.py')
            st_md = Step(work, t['mode'], dict(pkg=file_sig(by_role[t['role']]['path']), res=res_sig, tools=tools,
                                               base=file_sig(base), base_inputs=base_sig, combined=comb),
                         [os.path.join(md_out, comb)], a.force)
            if st_md.up_to_date():
                say('  up to date (%s)' % md_out)
            else:
                st_md.invalidate()
                clear_output(md_out, work)    # its overlay and data folder of an earlier run
                log = os.path.join(logs, '%s.log' % t['mode'])
                code, tail = run_tool(py + [script, '--base', base, '--dlc', src_pkg, '--out', os.path.join(md_out, comb),
                                            '--data-out', md_data, '--game', os.path.join(game, 'data')], log, env, work)
                require_ok(code, tail, 'Adding the game mode of %s' % t['name'], log)
                st_md.done()
            for dp, dn, fns in os.walk(md_data):
                for n in sorted(fns):
                    r = os.path.relpath(os.path.join(dp, n), md_data).replace(os.sep, '/').lower()
                    items.append(('%s/data/%s' % (t['dir'], r), os.path.join(dp, n), 'game mode of %s: %s' % (t['name'], r)))
            base, base_sig = os.path.join(md_out, comb), st_md.sig
    if specs:
        items.append((TRACK_NAME, base, 'overlay: the car pack + track%s %s' % ('s' if len(built) > 1 else '',
                                                                               ' + '.join(built))))
        ini.append(('Tracks', '1'))
        if music:
            ini.append(('Music', '1'))   # the tracks' own race music ([DLC] Music, on by default)
    else:
        say('')
        say('== DLC tracks: left out')

    # ---- 6. stage
    status.step('Copying the result to the staging folder')
    seen = {}
    for rel, src, what in items:   # two steps must never stage the same name (one would silently replace the other)
        if rel.lower() in seen:
            raise BuildError('Internal: %s is staged twice (%s; %s)' % (rel, seen[rel.lower()], what))
        seen[rel.lower()] = what
    modes = (['Deadline'] if extra else []) + ['Survival Race' for t in specs if t['mode'] == 'nemesis_mode']
    meta = {'built': time.strftime('%Y-%m-%d %H:%M:%S'), 'dump': os.path.abspath(a.dump), 'game': game,
            'track': ', '.join(t['track'] for t in specs) or '-', 'mode': ', '.join(modes) or '-',
            'tu4texts': 'yes' if tu4 else 'no',
            'cars': ', '.join(models),
            'packages': '; '.join('%s (%s)' % (p['name'], ROLE_NAMES[p['role']]) for p in pkgs if p['used'] and p['role'] in roles)}
    files = stage(staging, items, ini, meta)

    # ---- 7. check
    status.step('Checking the staging folder')
    errors, notes, _ = check_staging(staging)
    for e in errors:
        say('ERROR: ' + e)
    if errors:
        raise BuildError('The staging folder does not match its manifest: %s' % errors[0], errors)
    total = sum(f[1] for f in files)
    say('')
    say('Staging folder %s: %d files, %s (manifest %s)' % (staging, len(files), human(total), MANIFEST))
    for rel, size, sha, what in files:
        say('  %-62s %12d  %s' % (rel, size, what))
    say('Needs in SplitSecondPad.ini [DLC]: %s' % ', '.join('%s=%s' % kv for kv in ini))
    say('Next: install it with install_dlc.py (or "Install" in glimpse_config.exe). Time %.0f s.' % (time.time() - t_start))
    what = '%d cars (%s)' % (len(models), contents) + \
        (', track%s %s' % ('s' if len(specs) > 1 else '', ', '.join(t['track'] for t in specs)) if specs else '') + \
        (', game mode%s %s' % ('s' if len(modes) > 1 else '', ', '.join(modes)) if modes else '')
    text = 'Built: %s, %d files, %s. Next: Install.' % (what, len(files), human(total))
    return text, warnings


def check_main(a):
    staging = os.path.abspath(a.staging)
    game = os.path.abspath(a.game) if a.game else None
    if not game:   # the comparison with the installed files, read only: the Steam install when there is one
        game, how = find_steam_game()
        if game:
            say('game folder found through Steam: %s (%s)' % (game, how))
    errors, notes, m = check_staging(staging, game)
    if m:
        say('Staging folder %s, built %s' % (staging, m['meta'].get('built', '?')))
        for rel, size, sha, what in m['files']:
            say('  %-62s %12d  %s' % (rel, size, sha[:16]))
        say('Needs in SplitSecondPad.ini [DLC]: %s' % ', '.join('%s=%s' % kv for kv in m['ini']))
    for n in notes:
        say(n)
    for e in errors:
        say('ERROR: ' + e)
    say('RESULT: %s' % ('OK' if not errors else '%d error(s)' % len(errors)))
    return errors, m


def preflight_main(a):
    """--preflight: everything a build checks before it starts, without converting anything: Python, this tools
    folder (complete, every converter loads), what the track needs, and with --game the game folder and its Havok class
    layouts (made in the work folder, as a build would), with --dump the packages found. Returns (errors, warnings)."""
    errors, warnings = [], []
    say('Python %s (%s)' % (sys.version.split()[0], sys.executable))
    say('tools folder %s' % HERE)
    if sys.version_info < (3, 8):
        errors.append('Python 3.8 or newer is needed (this is %s).' % sys.version.split()[0])
    probs, nfiles = check_tools_files()
    say('release file list: %s' % ('%d files checked' % nfiles if nfiles else 'none (a development checkout)'))
    errors += probs
    want_track, vgm, ffm, flags, more = requirements(a, not a.no_track)
    warnings += more
    b = read_bundle()
    used = [x for x in (sys.executable, vgm, ffm) if x and b and inside(x, b['folder'])]
    if b:
        say('DLC tools bundle %s in %s: Python %s, numpy %s, vgmstream %s, ffmpeg %s; %s' % (
            b.get('bundle', '?'), b['folder'], b.get('python', '?'), b.get('numpy', '?'), b.get('vgmstream', '?'),
            b.get('ffmpeg', '?'), 'in use' if used else 'NOT in use'))
        if used:
            bprobs = check_bundle(b)
            say('DLC tools bundle: %s' % ('every file checked' if not bprobs else '%d problem(s)' % len(bprobs)))
            errors += ['DLC tools bundle %s (unpack it again: setup_deps.bat -Install)' % p for p in bprobs[:10]]
    else:
        say('DLC tools bundle: none in %s' % BUNDLE_DIR)
    py = [sys.executable] + flags + ['-B', '-X', 'utf8']
    game = resident = None
    try:
        game, resident = find_game(a.game)
        say('game: %s (resident.ark %d bytes)' % (game, os.path.getsize(resident)))
    except BuildError as ex:
        (errors if a.game else warnings).append(str(ex))
    env = converter_env(resident or '')
    scripts = [s for s in ENTRY_SCRIPTS if want_track or s not in TRACK_SCRIPTS]
    for s, err in sorted(load_scripts(py, env, scripts).items()):
        say('converter %-32s %s' % (s, err or 'loads'))
        if err:
            errors.append('%s does not load: %s' % (s, err))
    try:
        claim_work(a.work, write=False)     # the build would refuse it too; only made and marked below, with --game
    except BuildError as ex:
        errors.append(str(ex))
    if game and not errors:
        work = os.path.abspath(a.work)
        if overlap(work, game):
            errors.append('The work folder %s lies in the game folder or holds it.' % work)
        elif a.dump and overlap(work, a.dump):
            errors.append('The work folder %s and the DLC folder %s overlap.' % (work, os.path.abspath(a.dump)))
        else:
            try:
                say(claim_work(work, write=True))
                logs = os.path.join(work, 'logs')
                os.makedirs(logs, exist_ok=True)
                with Lock(work):
                    fn, more = make_hkref(a, work, game, logs, env, py)
                warnings += more
                say('hkref.json: %s, %s' % (fn, 'the tested layouts' if not more else 'OTHER layouts'))
            except BuildError as ex:
                errors.append(str(ex))
    if a.dump:
        try:
            pkgs, others, by_role = find_packages(a.dump)
            for p in pkgs:
                say('package %-40s %-28s %s' % (p['name'][:40], p['info'], ROLE_NAMES.get(p['role'], 'unknown')))
            if not any(r in by_role for r in CAR_ROLES):
                errors.append('No car package (Deadline, High Octane, The Elite) is in %s.' % a.dump)
        except BuildError as ex:
            errors.append(str(ex))
    for w in warnings:
        say('NOTE: ' + w)
    for e in errors:
        say('ERROR: ' + e)
    say('RESULT: %s' % ('FAILED, %d error(s)' % len(errors) if errors else 'WARN' if warnings else 'OK'))
    return errors, warnings


# ------------------------------------------------------------------------------------------------- lock
def pid_alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259                 # STILL_ACTIVE
    except Exception:
        return False


class Lock:
    def __init__(self, work):
        self.fn = os.path.join(work, 'build.lock')

    def __enter__(self):
        os.makedirs(os.path.dirname(self.fn), exist_ok=True)
        if os.path.isfile(self.fn):
            try:
                pid = int(open(self.fn).read().strip() or '0')
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and pid_alive(pid):
                raise BuildError('Another DLC build or install is running (process %d).' % pid)
            os.remove(self.fn)
        fd = os.open(self.fn, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(self.fn)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------- main
def dlcaudio_main(argv):
    """Internal: audio/dlcaudio.py with the vgmstream-cli and ffmpeg found here (fsbconv reads its defaults then)."""
    ap = argparse.ArgumentParser(prog='build_all.py _dlcaudio')
    ap.add_argument('--vgmstream', required=True)
    ap.add_argument('--ffmpeg', required=True)
    ap.add_argument('rest', nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    rest = a.rest[1:] if a.rest[:1] == ['--'] else a.rest
    sys.path.insert(0, os.path.join(HERE, 'audio'))
    import fsbconv
    fsbconv.VGMSTREAM = a.vgmstream
    fsbconv.FFMPEG = a.ffmpeg
    import dlcaudio
    sys.argv = ['dlcaudio.py'] + rest
    dlcaudio.main()
    return 0


def in_bundle_python(argv):
    """Runs this script again in the DLC tools bundle's Python when there is one and this is another Python
    (--no-bundle-python or GLIMPSE_NO_BUNDLE_PYTHON: not). Returns its exit code, or None to go on here."""
    if '--no-bundle-python' in argv or os.environ.get(NO_BUNDLE_ENV) or '-h' in argv or '--help' in argv:
        return None
    b = read_bundle()
    exe = bundle_exe('python', b)
    if not exe or same_file(exe, sys.executable):
        return None
    say('Python: the DLC tools bundle %s has its own (%s); running there.' % (b.get('bundle', '?'), exe))
    env = dict(os.environ)
    env[NO_BUNDLE_ENV] = '1'
    env.pop('PYTHONPATH', None)
    try:
        return subprocess.run([exe, '-I', '-X', 'utf8', os.path.abspath(__file__)] + list(argv), env=env).returncode
    except KeyboardInterrupt:
        return 1


def main(argv):
    if argv[:1] == ['_dlcaudio']:
        return dlcaudio_main(argv[1:])
    code = in_bundle_python(argv)
    if code is not None:
        return code
    ap = argparse.ArgumentParser(description='Build the PC files of your own Xbox 360 Split/Second DLC (see README_build.md).')
    ap.add_argument('--dump', help='the Xbox 360 DLC folder (425607E7, or its 00000002 subfolder)')
    ap.add_argument('--game', help='the Split/Second folder (read only; default: the Steam install of Split/Second)')
    ap.add_argument('--find-game', action='store_true', help='only print the game folder found through Steam '
                                                             '(exit code 1: none)')
    ap.add_argument('--no-bundle-python', action='store_true', help='run in this Python even when the DLC tools '
                                                                    'bundle has its own')
    ap.add_argument('--staging', default=default_staging(), help='where the result goes (default %(default)s)')
    ap.add_argument('--work', default=default_work(), help='intermediate files (default %(default)s)')
    ap.add_argument('--no-mode', action='store_true', help='without the game mode Deadline (the cars only)')
    ap.add_argument('--no-track', action='store_true', help='only the car pack, without the DLC tracks')
    ap.add_argument('--force', action='store_true', help='redo every step')
    ap.add_argument('--vgmstream', help='vgmstream-cli.exe (default: PATH, glimpse_tools\\bin, '
                                        '%%LOCALAPPDATA%%\\glimpse_fix\\tools)')
    ap.add_argument('--ffmpeg', help='ffmpeg.exe (default: PATH, glimpse_tools\\bin, %%LOCALAPPDATA%%\\glimpse_fix\\tools)')
    ap.add_argument('--tu4-texts', help='optional: a TU4 text table (JSON) made outside glimpse_fix, for the official '
                                        'texts (game data; the release cannot produce it, without it the converters '
                                        'write their own texts); default: the environment variable ' + TU4_ENV)
    ap.add_argument('--hkref', help='development: this hkref.json instead of one read from the game folder\'s SplitSecond.exe')
    ap.add_argument('--check', action='store_true', help='only check the staging folder (with --game: and compare it '
                                                         'with the game folder, read only)')
    ap.add_argument('--preflight', action='store_true', help='only check Python, this tools folder, what the track needs '
                                                             'and (with --game, --dump) the game and DLC folders; '
                                                             'converts nothing')
    ap.add_argument('--status', help='status file for the settings program')
    a = ap.parse_args(argv)
    if a.find_game:
        game, how = find_steam_game()
        if game:
            say('game\t%s' % game)
            say('how\t%s' % how)
            return 0
        say('none\t%s' % how)
        return 1
    if a.preflight:
        st = Status(a.status, 1)
        try:
            errors, warnings = preflight_main(a)
        except Exception as ex:
            traceback.print_exc()
            st.write('FAILED', 'Internal error: %s' % ex)
            return 2
        st.n = 1
        st.write('FAILED' if errors else 'WARN' if warnings else 'OK',
                 errors[0] if errors else warnings[0] if warnings else 'Ready to build', errors + warnings)
        return 1 if errors else 0
    if a.check:
        st = Status(a.status, 1)
        try:
            errors, m = check_main(a)
        except Exception as ex:
            traceback.print_exc()
            st.write('FAILED', 'Internal error: %s' % ex)
            return 2
        st.n = 1
        st.write('OK' if not errors else 'FAILED', 'Staging folder OK' if not errors else errors[0], errors)
        return 0 if not errors else 1
    if not a.dump:
        ap.error('--dump is needed (or --check, --preflight); --game too unless Steam has Split/Second')
    status = Status(a.status, 12)   # build() sets the real count once it knows the packages
    say('glimpse_fix DLC build, %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    say('DLC folder %s' % os.path.abspath(a.dump))
    say('work %s, staging %s' % (os.path.abspath(a.work), os.path.abspath(a.staging)))
    try:
        claim_work(a.work, write=False)     # before the lock puts a file into it; build() marks it
        with Lock(os.path.abspath(a.work)):
            text, warnings = build(a, status)
    except BuildError as ex:
        say('')
        say('FAILED: %s' % ex)
        status.write('FAILED', str(ex), ex.details)
        return 1
    except KeyboardInterrupt:
        status.write('FAILED', 'Stopped.')
        return 1
    except Exception as ex:
        traceback.print_exc()
        status.write('FAILED', 'Internal error: %s: %s (see the log)' % (type(ex).__name__, ex))
        return 2
    say('')
    say('DONE: ' + text)
    for w in warnings:
        say('NOTE: ' + w)
    status.write('WARN' if warnings else 'OK', text + (' ' + warnings[0] if warnings else ''), warnings)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
