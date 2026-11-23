# glimpse_fix: install the converted DLC (made by build_all.py) into the game folder, or take it out again.
#
#   py -3 -I install_dlc.py [--game <Split/Second folder>] [--staging <dir>] [--apply] [--move-others] [--status <file>]
#   py -3 -I install_dlc.py [--game <Split/Second folder>] --uninstall [--apply] [--status <file>]
#
# Without --apply nothing is changed: it only prints what it would do (state WARN when other archives lie in
# glimpse_dlc or the last install was interrupted). With --apply:
#   1. the save folder (Documents\Disney Interactive Studios\Split Second, or --saves) is copied to
#      "Split Second backup_<date>_dlc" next to it (checked by file sizes), before anything in the game folder is
#      changed;
#   2. leftover <name>.glimpse_part files of an interrupted install are removed;
#   3. every staged file is copied into <game>\glimpse_dlc\ (to <name>.glimpse_part first, then renamed). A file that
#      is there already with the same content is left as it is and recorded as "adopted". A file of the same name with
#      other content is replaced only if this script copied it and it still has the content it was copied with;
#      otherwise it is moved to <game>\glimpse_dlc_replaced_<date>\ (outside glimpse_dlc, so the game ignores it);
#   4. --move-others: the other *.ark in glimpse_dlc (not from this build; the game lays them over its data too) are
#      moved there as well;
#   5. files it copied before that are no longer staged are removed (only with the content it copied); adopted files
#      are never removed. A file an earlier install moved aside from such a name is put back there;
#   6. glimpse_dlc\glimpse_dlc_installed.tsv lists every file (copied or adopted) and every file moved aside.
# --uninstall removes the files it copied (with the content it copied), leaves the adopted ones, moves the files it
# moved aside back where their name is free, and removes the list. The game must not be running. The INI is not
# changed: glimpse_config.exe offers the [DLC] keys the build needs after an install.
# Without --game the game folder is the Steam install of Split/Second (build_all.find_game: Steam's folder from the
# registry, its steamapps\libraryfolders.vdf, appmanifest_297860.acf in one of the libraries; read only), named in the
# output.
# Exit code 0 = done (or the dry run found nothing wrong), 1 = refused or failed, 2 = internal error.
import argparse
import datetime
import os
import shutil
import subprocess
import sys
import traceback

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import build_all  # noqa: E402
from build_all import BuildError, Status, say, sha256, human, safe_rel, read_manifest, write_manifest  # noqa: E402

INSTALLED = 'glimpse_dlc_installed.tsv'
PART = '.glimpse_part'
SAVE_PARTS = ('Disney Interactive Studios', 'Split Second')


def documents_folder():
    """The user's Documents folder as Windows has it (also when moved, e.g. to OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [('a', wintypes.DWORD), ('b', wintypes.WORD), ('c', wintypes.WORD), ('d', ctypes.c_ubyte * 8)]
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(p)) == 0:
            path = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            if path:
                return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser('~'), 'Documents')


def game_running():
    try:
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq SplitSecond.exe', '/NH', '/FO', 'CSV'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return b'splitsecond.exe' in r.stdout.lower()
    except OSError:
        return False


def backup_saves(saves, stamp):
    """Copies the save folder next to itself; returns the backup folder (None: there is no save folder)."""
    if not os.path.isdir(saves):
        return None
    dst = os.path.join(os.path.dirname(saves), '%s backup_%s_dlc' % (os.path.basename(saves), stamp))
    n = 1
    while os.path.exists(dst):
        n += 1
        dst = os.path.join(os.path.dirname(saves), '%s backup_%s_dlc_%d' % (os.path.basename(saves), stamp, n))
    try:
        shutil.copytree(saves, dst)
    except (OSError, shutil.Error) as ex:
        raise BuildError('The saves could not be backed up to %s (%s); nothing was installed.' % (dst, ex))
    for dp, dn, fns in os.walk(saves):
        for fn in fns:
            s = os.path.join(dp, fn)
            d = os.path.join(dst, os.path.relpath(s, saves))
            if not os.path.isfile(d) or os.path.getsize(d) != os.path.getsize(s):
                raise BuildError('The save backup %s is incomplete (%s); nothing was installed.' % (dst, fn))
    return dst


def ini_values(game):
    """[DLC] values in the game's SplitSecondPad.ini, as far as they are set."""
    out = {}
    fn = os.path.join(game, 'SplitSecondPad.ini')
    if not os.path.isfile(fn):
        return out
    sec = ''
    for line in open(fn, encoding='latin-1'):
        t = line.strip()
        if t.startswith('[') and t.endswith(']'):
            sec = t[1:-1].strip().lower()
        elif sec == 'dlc' and '=' in t and not t.startswith(';'):
            k, v = t.split('=', 1)
            out.setdefault(k.strip().lower(), v.strip())
    return out


def part_files(gd):
    out = []
    for dp, dn, fns in os.walk(gd):
        out += [os.path.join(dp, n) for n in fns if n.endswith(PART)]
    return out


def replaced_dir(game, stamp):
    """A folder of its own for this run's moved files: glimpse_dlc_replaced_<date>, _2, _3 ... when taken."""
    d = os.path.join(game, 'glimpse_dlc_replaced_' + stamp)
    n = 1
    while os.path.exists(d):
        n += 1
        d = os.path.join(game, 'glimpse_dlc_replaced_%s_%d' % (stamp, n))
    return d


def plan_install(game, staging):
    errors, notes, m = build_all.check_staging(staging)
    if errors:
        raise BuildError('The staging folder is not ready: %s' % errors[0], errors)
    gd = os.path.join(game, 'glimpse_dlc')
    old = read_manifest(gd, INSTALLED) if os.path.isdir(gd) else None
    rec = {rel: (size, sha) for rel, size, sha, _ in (old['files'] if old else []) if safe_rel(rel)}
    origin = old['origin'] if old else {}
    plan = []
    for rel, size, sha, what in m['files']:
        src = os.path.join(staging, *rel.split('/'))
        dst = os.path.join(gd, *rel.split('/'))
        cur = sha256(dst) if os.path.isfile(dst) else None
        if cur is None:
            act, org = 'new', 'copied'
        elif cur == sha and os.path.getsize(dst) == size:
            act, org = 'same', origin.get(rel, 'adopted') if rel in rec else 'adopted'
        elif rel in rec and origin.get(rel) == 'copied' and cur == rec[rel][1]:
            act, org = 'replace', 'copied'          # copied by an earlier install, unchanged since
        else:
            act, org = 'replace-foreign', 'copied'  # someone else's file (or changed since): moved aside, not lost
        plan.append(dict(act=act, rel=rel, src=src, dst=dst, size=size, sha=sha, what=what, origin=org))
    staged = {p['rel'] for p in plan}
    for rel, (size, sha) in sorted(rec.items()):
        if rel in staged:
            continue
        dst = os.path.join(gd, *rel.split('/'))
        if not os.path.isfile(dst):
            continue
        if origin.get(rel) != 'copied':
            act = 'leave-adopted'
        else:
            act = 'remove' if os.path.getsize(dst) == size and sha256(dst) == sha else 'leave-changed'
        plan.append(dict(act=act, rel=rel, src=None, dst=dst, size=size, sha=sha, what='installed before',
                         origin=origin.get(rel, 'adopted')))
    others = []
    if os.path.isdir(gd):
        # as Windows compares names: a foreign DLC_Cars.ark has the staged dlc_cars.ark's name (replace-foreign above
        # moves it aside), it is not another archive that --move-others would move a second time
        known = {k.lower() for k in staged | set(rec)}
        for n in sorted(os.listdir(gd)):
            if n.lower().endswith('.ark') and os.path.isfile(os.path.join(gd, n)) and n.lower() not in known:
                others.append(n)
    interrupted = bool(part_files(gd)) or bool(old and old['meta'].get('installed') == 'in progress')
    return m, old, plan, others, interrupted


WORDS = {'new': 'copy', 'same': 'keep (identical)', 'replace': 'replace (installed before)',
         'replace-foreign': 'replace (the old file is moved to glimpse_dlc_replaced_<date>)',
         'remove': 'remove (installed before, no longer built)',
         'leave-changed': 'leave (installed before, changed since: not removed)',
         'leave-adopted': 'leave (was there before the install: not removed)'}


def describe(plan, others, m, game):
    lines = ['%-58s %s' % ('glimpse_dlc\\' + p['rel'].replace('/', '\\'), WORDS[p['act']]) for p in plan]
    for n in others:
        lines.append('glimpse_dlc\\%s: not from this build; the game lays it over its data too (in name order)' % n)
    need = sum(p['size'] for p in plan if p['act'] in ('new', 'replace', 'replace-foreign'))
    counts = {}
    for p in plan:
        counts[p['act']] = counts.get(p['act'], 0) + 1
    names = {'new': 'new', 'replace': 'replaced', 'replace-foreign': 'replaced (old file moved aside)', 'same': 'identical',
             'remove': 'removed', 'leave-changed': 'left (changed)', 'leave-adopted': 'left (not installed by it)'}
    summary = ', '.join('%d %s' % (counts[k], names[k]) for k in names if counts.get(k))
    ini = ini_values(game)
    want = ', '.join('%s=%s' % kv for kv in m['ini'])
    # a key missing from the INI has the DLL's default (src\main.cpp): Music and Cars default to 1, the rest to 0
    dll_default = {'music': '1', 'cars': '1', 'pickupvignette': '1'}
    now = ', '.join('%s=%s' % (k, ini.get(k.lower(), dll_default.get(k.lower(), '0'))) for k, _ in m['ini'])
    return lines, summary, need, want, now


def move_aside(src, game, folder, rel, aside, record):
    """Moves src to folder\\rel; the move is in the list (record() rewrites it) before it happens, so an install that
    stops right after it can still be undone (uninstall skips an aside entry whose file is not there)."""
    to = os.path.join(folder, *rel.split('/'))
    if os.path.exists(to):   # os.replace would overwrite it: a file moved aside is never lost to a second move
        raise BuildError('Cannot move %s aside: %s is there already. Nothing was overwritten; run Install again.'
                         % (rel, to))
    aside.append((rel, os.path.relpath(to, game).replace(os.sep, '/'), os.path.getsize(src)))
    record()
    os.makedirs(os.path.dirname(to), exist_ok=True)
    os.replace(src, to)
    say('  moved aside  %s -> %s' % (rel, to))


def plain_rel(rel):
    """A relative path that stays below its folder. Unlike safe_rel it allows any file name Windows has: an archive
    moved aside with --move-others is the user's own and may be called "dlc cars (old).ark"."""
    return bool(rel) and not rel.startswith('/') and ':' not in rel and '\\' not in rel and \
        all(p and p not in ('.', '..') for p in rel.split('/'))


def name_on_disk(path):
    """The file's name as the folder lists it (DLC_Cars.ark for dlc_cars.ark), so a file moved aside keeps its spelling."""
    want = os.path.basename(path).lower()
    try:
        for n in os.listdir(os.path.dirname(path)):
            if n.lower() == want:
                return n
    except OSError:
        pass
    return os.path.basename(path)


def aside_ok(to):
    """An aside entry's place as this script writes it: below <game>\\glimpse_dlc_replaced_<date>."""
    return plain_rel(to) and '/' in to and to.split('/')[0].lower().startswith('glimpse_dlc_replaced_')


def prune_empty(d, game):
    """Removes d and its parents below the game folder while they are empty (an emptied glimpse_dlc_replaced_<date>)."""
    while (build_all.inside(d, game) and build_all.norm(d) != build_all.norm(game) and os.path.isdir(d)
           and not os.listdir(d)):
        os.rmdir(d)
        d = os.path.dirname(d)


def install(a, status):
    game, _ = build_all.find_game(a.game, read_data=False)
    staging = os.path.abspath(a.staging)
    gd = os.path.join(game, 'glimpse_dlc')
    saves = os.path.abspath(a.saves) if a.saves else os.path.join(documents_folder(), *SAVE_PARTS)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    if build_all.inside(staging, game):
        raise BuildError('The staging folder must not lie in the game folder.')
    status.step('Checking the staging folder and the game folder')
    m, old, plan, others, interrupted = plan_install(game, staging)
    lines, summary, need, want, now = describe(plan, others, m, game)
    say('Game folder %s' % game)
    say('Staging folder %s (built %s)' % (staging, m['meta'].get('built', '?')))
    for l in lines:
        say('  ' + l)
    if interrupted:
        say('The last install was interrupted; installing again completes it.')
    say('Save folder: %s%s' % (saves, '' if os.path.isdir(saves) else ' (not there: nothing to back up)'))
    say('INI [DLC]: needs %s; now %s' % (want, now))
    extra = [('other', n) for n in others]
    details = lines + ['Your saves are copied first: %s -> %s backup_<date>_dlc' % (saves, os.path.basename(saves))
                       if os.path.isdir(saves) else 'No save folder found at %s (nothing to back up).' % saves,
                       'SplitSecondPad.ini [DLC] needs %s (now %s).' % (want, now)]
    if not a.apply:
        say('')
        say('Dry run: nothing was changed. Run again with --apply to install.')
        warn = []
        if interrupted:
            warn.append('The last install was interrupted: Install again completes it.')
        if others:
            warn.append('glimpse_dlc also holds %s, not from this build; the game lays %s over its data too.'
                        % (', '.join(others), 'it' if len(others) == 1 else 'them'))
        text = 'Ready to install: %s (%s to copy).' % (summary, human(need))
        return ('WARN' if warn else 'OK'), ' '.join([text] + warn), warn + details, extra
    if game_running():
        raise BuildError('Split/Second is running. Close it, then install.')
    free = shutil.disk_usage(game).free
    if free < need + (64 << 20):
        raise BuildError('Only %s free on the game drive; the install needs %s.' % (human(free), human(need)))

    status.step('Backing up your saves')
    bak = backup_saves(saves, stamp)
    say('Saves backed up to %s' % bak if bak else 'No save folder at %s: nothing to back up.' % saves)

    status.step('Copying the files into glimpse_dlc')
    aside = list(old['aside']) if old else []
    folder = replaced_dir(game, stamp)
    moved = []   # this run's moves only
    returned = []   # files an earlier install moved aside, put back because their name is no longer installed
    try:
        os.makedirs(gd, exist_ok=True)
        for fn in part_files(gd):   # an interrupted copy: never a finished file
            os.remove(fn)
            say('  removed the leftover %s' % os.path.relpath(fn, gd))
        # The list always says what may be there at this moment, so an install that stops anywhere can be undone:
        # every file it had before, each file this run copies (with the staged content, origin "copied") from just
        # before its copy on, and each file moved aside from just before the move on.
        cur = {r: (r, size, sha, what) for r, size, sha, what in (old['files'] if old else [])}
        org = dict(old['origin']) if old else {}
        meta = {'installed': 'in progress', 'staging': staging, 'built': m['meta'].get('built', '?')}

        def record():
            write_manifest(gd, list(cur.values()), m['ini'], meta, INSTALLED, origin=org, aside=aside)
        for p in plan:   # identical files: kept, adopted if the list did not have them
            if p['act'] == 'same' and p['rel'] not in cur:
                cur[p['rel']] = (p['rel'], p['size'], p['sha'], p['what'])
                org[p['rel']] = p['origin']
        record()
        for p in plan:
            if not build_all.inside(p['dst'], gd):
                raise BuildError('Refusing a path outside glimpse_dlc: %s' % p['dst'])
            if p['act'] in ('new', 'replace', 'replace-foreign'):
                os.makedirs(os.path.dirname(p['dst']), exist_ok=True)
                if p['act'] != 'replace':
                    # the name will hold nothing or the staged file: listed as such before anything happens
                    cur[p['rel']] = (p['rel'], p['size'], p['sha'], p['what'])
                    org[p['rel']] = 'copied'
                if p['act'] == 'replace-foreign':
                    own = '/'.join(p['rel'].split('/')[:-1] + [name_on_disk(p['dst'])])   # as its owner spelled it
                    move_aside(p['dst'], game, folder, own, aside, record)
                    moved.append(own)
                else:
                    record()
                status.write('RUNNING', 'Copying %s (%s)' % (p['rel'], human(p['size'])))
                part = p['dst'] + PART
                shutil.copyfile(p['src'], part)
                if os.path.getsize(part) != p['size']:
                    os.remove(part)
                    raise BuildError('Copying %s failed (size); the install is incomplete, run Install again.' % p['rel'])
                os.replace(part, p['dst'])
                if p['act'] == 'replace':   # its own earlier copy, unchanged: listed with the new content once it is in
                    cur[p['rel']] = (p['rel'], p['size'], p['sha'], p['what'])
                    org[p['rel']] = 'copied'
                    record()
                say('  copied       %s' % p['rel'])
            elif p['act'] == 'remove':
                os.remove(p['dst'])
                cur.pop(p['rel'], None)
                org.pop(p['rel'], None)
                say('  removed      %s' % p['rel'])
                # the name is free again: the file an earlier install moved aside from it (the newest one) returns,
                # as an uninstall would put it back. Its entry goes only after the move, so a stop in between leaves
                # an entry without a file, which the uninstall skips.
                for e in reversed(aside):
                    src = os.path.join(game, *e[1].split('/'))
                    if e[0].lower() == p['rel'].lower() and aside_ok(e[1]) and os.path.isfile(src):
                        os.replace(src, os.path.join(gd, *e[0].split('/')))   # under its own spelling
                        aside.remove(e)
                        returned.append(p['rel'])
                        say('  put back     %s (moved aside by an earlier install)' % p['rel'])
                        prune_empty(os.path.dirname(src), game)
                        break
                record()
            elif p['act'].startswith('leave'):
                say('  left alone   %s (%s)' % (p['rel'], WORDS[p['act']]))
        if a.move_others:
            for n in others:
                move_aside(os.path.join(gd, n), game, folder, n, aside, record)
                moved.append(n)
    except OSError as ex:
        raise BuildError('Installing failed: %s. The install is incomplete; close what may use the files and run Install '
                         'again.' % ex)
    for d in build_all.data_dirs(p['rel'] for p in plan):   # quarry\, nem_quarry\, deadline\ ...: empty folders go
        if os.path.isdir(os.path.join(gd, d)):
            build_all.remove_empty_dirs(os.path.join(gd, d))
    # checked before the list says "installed": after a failure it still says "in progress" and Install runs again
    status.step('Checking the installed files')
    bad = [p['rel'] for p in plan if p['src'] and (not os.path.isfile(p['dst']) or os.path.getsize(p['dst']) != p['size'])]
    if bad:
        raise BuildError('Installed files are missing or wrong: %s. The install is incomplete; run Install again.'
                         % ', '.join(bad))
    keep = [p for p in plan if p['src'] or p['act'] == 'leave-changed']
    final = [(p['rel'], p['size'], p['sha'], p['what']) for p in keep]
    meta['installed'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_manifest(gd, final, m['ini'], meta, INSTALLED, origin={p['rel']: p['origin'] for p in keep}, aside=aside)
    say('')
    say('Installed into %s: %s.' % (gd, summary))
    say('Turn on SplitSecondPad.ini [DLC] %s (now %s).' % (want, now))
    details = ['Saves backed up to %s' % bak if bak else 'No save folder found (nothing backed up).',
               'SplitSecondPad.ini [DLC] needs %s (now %s).' % (want, now)]
    if returned:
        details.insert(0, 'Put back into glimpse_dlc (moved aside by an earlier install, the name is free again): %s'
                       % ', '.join(returned))
    if moved:
        details.insert(0, 'Moved to %s: %s' % (folder, ', '.join(moved)))
    left = [n for n in others if n not in moved]
    if left:
        details.insert(0, 'Still in glimpse_dlc, not from this build: %s' % ', '.join(left))
    return ('WARN' if left else 'OK'), 'Installed: %s.' % summary, details, [('other', n) for n in left]


def uninstall(a, status):
    game, _ = build_all.find_game(a.game, read_data=False)
    gd = os.path.join(game, 'glimpse_dlc')
    saves = os.path.abspath(a.saves) if a.saves else os.path.join(documents_folder(), *SAVE_PARTS)
    status.step('Reading what was installed')
    old = read_manifest(gd, INSTALLED) if os.path.isdir(gd) else None
    if not old:
        raise BuildError('Nothing to remove: there is no glimpse_dlc\\%s in %s.' % (INSTALLED, game))
    rm, keep = [], []
    for rel, size, sha, what in old['files']:
        dst = os.path.join(gd, *rel.split('/'))
        if not safe_rel(rel) or not build_all.inside(dst, gd) or not os.path.isfile(dst):
            continue
        if old['origin'].get(rel) != 'copied':
            keep.append((rel, 'was there before the install'))
        elif os.path.getsize(dst) == size and sha256(dst) == sha:
            rm.append((rel, dst))
        else:
            keep.append((rel, 'changed since it was installed'))
    back = []
    for rel, to, size in old['aside']:
        src = os.path.join(game, *to.split('/'))
        dst = os.path.join(gd, *rel.split('/'))
        # only places this script moves files to (glimpse_dlc_replaced_<date>): a damaged list never moves a game file
        if not plain_rel(rel) or not aside_ok(to) or not build_all.inside(src, game) or not build_all.inside(dst, gd):
            continue
        back.append((rel, src, dst))
    removing = {build_all.norm(d) for _, d in rm}
    for rel, _ in rm:
        say('  remove  glimpse_dlc\\%s' % rel.replace('/', '\\'))
    for rel, why in keep:
        say('  leave   glimpse_dlc\\%s (%s)' % (rel.replace('/', '\\'), why))
    for rel, src, dst in back:
        free = not os.path.exists(dst) or build_all.norm(dst) in removing
        say('  %s glimpse_dlc\\%s from %s' % ('put back' if os.path.isfile(src) and free else 'cannot put back', rel, src))
    if not a.apply:
        say('Dry run: nothing was changed. Run again with --apply.')
        details = ['remove glimpse_dlc\\' + r.replace('/', '\\') for r, _ in rm] + \
                  ['leave glimpse_dlc\\%s (%s)' % (r.replace('/', '\\'), w) for r, w in keep] + \
                  ['put back glimpse_dlc\\%s' % r.replace('/', '\\') for r, s, _ in back if os.path.isfile(s)]
        return 'OK', 'Ready to remove: %d files%s.' % (len(rm), ', %d put back' % len(back) if back else ''), details, []
    if game_running():
        raise BuildError('Split/Second is running. Close it first.')
    status.step('Backing up your saves')
    bak = backup_saves(saves, datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
    say('Saves backed up to %s' % bak if bak else 'No save folder: nothing to back up.')
    status.step('Removing the files')
    restored, missed = [], []
    try:
        for rel, dst in rm:
            os.remove(dst)
        for rel, src, dst in back:
            if os.path.isfile(src) and not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
                restored.append(rel)
                prune_empty(os.path.dirname(src), game)   # the emptied glimpse_dlc_replaced_<date> folder
            else:
                missed.append(rel)
        for d in build_all.data_dirs(rel for rel, _, _, _ in old['files']):   # quarry\, nem_quarry\, deadline\ ...
            t = os.path.join(gd, d)
            if os.path.isdir(t):
                build_all.remove_empty_dirs(t)
                if not os.listdir(t):
                    os.rmdir(t)
        os.remove(os.path.join(gd, INSTALLED))
    except OSError as ex:
        raise BuildError('Removing failed: %s. Close what may use the files and run it again.' % ex)
    say('Removed %d files, put back %d.' % (len(rm), len(restored)))
    details = ['Saves backed up to %s' % bak if bak else 'No save folder found (nothing backed up).']
    if restored:
        details.append('Put back: %s' % ', '.join(restored))
    if missed:
        details.append('Not put back (the name is taken or the file is gone): %s' % ', '.join(missed))
    if keep:
        details.append('Left in glimpse_dlc: %s' % ', '.join(r for r, _ in keep))
    return ('WARN' if missed else 'OK'), 'Removed %d files.' % len(rm), details, []


def main(argv):
    ap = argparse.ArgumentParser(description='Install the converted DLC into the game folder (dry run without --apply).')
    ap.add_argument('--game', help='the Split/Second folder (default: the Steam install of Split/Second)')
    ap.add_argument('--staging', default=build_all.default_staging(), help='made by build_all.py (default %(default)s)')
    ap.add_argument('--work', default=build_all.default_work(), help='work folder of build_all.py (for its lock)')
    ap.add_argument('--saves', help='the save folder (default Documents\\Disney Interactive Studios\\Split Second)')
    ap.add_argument('--apply', action='store_true', help='really do it')
    ap.add_argument('--move-others', action='store_true', help='with --apply: also move the other *.ark in glimpse_dlc '
                                                               'to glimpse_dlc_replaced_<date>')
    ap.add_argument('--uninstall', action='store_true', help='remove what this script installed')
    ap.add_argument('--status', help='status file for the settings program')
    a = ap.parse_args(argv)
    status = Status(a.status, 1 if not a.apply else (3 if a.uninstall else 4))
    try:
        with build_all.Lock(os.path.abspath(a.work)):
            state, text, details, extra = (uninstall if a.uninstall else install)(a, status)
    except BuildError as ex:
        say('')
        say('FAILED: %s' % ex)
        status.write('FAILED', str(ex), ex.details)
        return 1
    except Exception as ex:
        traceback.print_exc()
        status.write('FAILED', 'Internal error: %s: %s (see the log)' % (type(ex).__name__, ex))
        return 2
    for d in details:
        say(d)
    status.write(state, text, details, extra)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
