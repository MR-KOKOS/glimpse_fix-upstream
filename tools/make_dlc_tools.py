# glimpse_fix: build the DLC tools bundle, a zip of its own next to the release zip.
#
#   py -3 -I tools\make_dlc_tools.py [--version <x.y.z>] [--out <dir>] [--cache <dir>] [--force]
#       [--python <3.x.y>] [--numpy <x.y.z>] [--vgmstream <tag>] [--ffmpeg-branch <x.y>] [--ffmpeg-tag <tag>] [--no-test]
#
# The bundle holds every program the DLC build (tools\dlc\build_all.py) needs except D3DCompiler_41.dll, so a user
# installs nothing by hand. It is unpacked as it is into %LOCALAPPDATA%\glimpse_fix\tools\ (setup_deps.bat -Bundle,
# or the settings program), where build_all.py looks first:
#   python\      the Windows embeddable Python of python.org (64 bit, the newest stable 3.x that numpy has a wheel for)
#                with numpy (the official win_amd64 wheel of PyPI) unpacked into python\Lib\site-packages, which the
#                python3XX._pth lists (the only change to the embeddable files; "import site" stays off, so no user or
#                system site-packages is ever seen). numpy is precompiled with that Python (unchecked-hash .pyc).
#   vgmstream\   vgmstream-win64.zip of the newest vgmstream release on GitHub, as it is (COPYING is its license)
#   ffmpeg\      ffmpeg.exe and LICENSE.txt of the newest LGPL build (win64-lgpl, static, of the newest FFmpeg
#                release branch) of FFmpeg-Builds (github.com/BtbN/FFmpeg-Builds, now FFmpeg-Builds/FFmpeg-Builds),
#                unmodified; SOURCE.txt names the FFmpeg commit and the build scripts' commit (LGPL: where the source is)
#   glimpse_dlc_tools.txt            the version file: bundle (= glimpse_fix version), format, the component versions
#   glimpse_dlc_tools_manifest.tsv   each component (version, download URL, SHA-256, license) and each file (size,
#                                    SHA-256)
#   THIRD_PARTY_dlc_tools.txt        the licenses (PSF for Python, BSD for numpy, ISC style for vgmstream, LGPL for
#                                    ffmpeg) and where each one's text is
# D3DCompiler_41.dll is never part of it (Microsoft's DirectX runtime may only be passed on through its own
# installer): setup_deps.bat -Install gets it from Microsoft.
#
# The versions are resolved when it runs (the newest of each, or the one given) and recorded. Every download is checked
# against the SHA-256 its source publishes: python.org (release file API), PyPI (JSON API), GitHub (the asset's
# "digest" in the releases API); a mismatch stops it. Downloads are kept in <cache> (default <out>\dlc_tools_cache)
# and reused while their SHA-256 matches. Then the bundle is tested: every module the shipped converters import loads
# in the bundled Python with -I, numpy works, vgmstream-cli runs, ffmpeg is an LGPL build with the native MPEG Layer II
# encoder (mp2) and the filters fsbconv.py uses (apad, aresample) and encodes a test tone.
# Result: <out>\dlc_tools_<version>\ (the unpacked bundle), <out>\glimpse_fix-dlc-tools-<version>.zip and
# <out>\glimpse_fix-dlc-tools-<version>.zip.sha256. Nothing is uploaded: the zip is attached to the GitHub release of
# tag v<version> by hand, next to glimpse_fix-v<version>.zip (never inside it).
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
import wave
import zipfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

def _load(name, fn):
    # by path: tools\ holds scripts named like standard modules (dis.py), so it never goes on sys.path
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, fn)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


make_release = _load('glimpse_make_release', os.path.join(HERE, 'make_release.py'))   # the version, the converter trace

FORMAT = 1                                  # the bundle layout; the settings program accepts what it knows
ZIP_NAME = 'glimpse_fix-dlc-tools-%s.zip'   # a release asset of tag v<version>
VERSION_FILE = 'glimpse_dlc_tools.txt'
MANIFEST = 'glimpse_dlc_tools_manifest.tsv'
NOTICE = 'THIRD_PARTY_dlc_tools.txt'
UA = {'User-Agent': 'glimpse_fix-make_dlc_tools', 'Accept': 'application/json'}

PY_RELEASES = 'https://www.python.org/api/v2/downloads/release/?is_published=true&pre_release=false'
PY_FILES = 'https://www.python.org/api/v2/downloads/release_file/?release=%s'
PYPI = 'https://pypi.org/pypi/numpy/json'
PYPI_V = 'https://pypi.org/pypi/numpy/%s/json'
GH_API = 'https://api.github.com/repos/%s'
VGM_REPO = 'vgmstream/vgmstream'
FF_REPO = 'BtbN/FFmpeg-Builds'              # moved to FFmpeg-Builds/FFmpeg-Builds; the API follows the move
# downloads only from these (the URLs the APIs give must start with one of them)
ALLOWED = ('https://www.python.org/ftp/python/', 'https://files.pythonhosted.org/packages/',
           'https://github.com/vgmstream/vgmstream/releases/download/',
           'https://github.com/BtbN/FFmpeg-Builds/releases/download/',
           'https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/')
# a static LGPL build of a release branch, with the exact FFmpeg commit in its name (autobuild-* releases; the rolling
# "latest" release names it only "nX.Y-latest")
FF_ASSET = re.compile(r'^ffmpeg-(n(\d+)\.(\d+)(?:\.\d+)*(?:-\d+-g([0-9a-f]{7,40}))?)-win64-lgpl-(\d+\.\d+)\.zip$')
NUMPY_WHEEL = re.compile(r'^numpy-([^-]+)-cp3(\d+)-cp3(\d+)-win_amd64\.whl$')   # not the free-threaded cp3XXt


class Fail(Exception):
    pass


def say(*a):
    print(*a)
    sys.stdout.flush()


def sha256(fn):
    return make_release.sha256(fn)


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


def vtuple(s):
    return tuple(int(x) for x in re.findall(r'\d+', s))


# ------------------------------------------------------------------------------------------------- resolving
def resolve_numpy(want):
    d = get_json(PYPI_V % want if want else PYPI)
    ver = d['info']['version']
    wheels = {}
    for u in d['urls']:
        m = NUMPY_WHEEL.match(u['filename'])
        if m and m.group(2) == m.group(3) and not u.get('yanked') and u['packagetype'] == 'bdist_wheel':
            wheels[int(m.group(2))] = dict(name=u['filename'], url=u['url'], sha256=u['digests']['sha256'],
                                           size=u['size'])
    if not wheels:
        raise Fail('numpy %s has no win_amd64 wheel on PyPI' % ver)
    return ver, wheels


def resolve_python(want, minors):
    """The newest stable Python 3.x release whose minor version numpy has a wheel for (or the one given)."""
    rels = []
    for r in get_json(PY_RELEASES):
        m = re.match(r'^Python (3\.(\d+)\.(\d+))$', r['name'])
        if m and r.get('is_published') and not r.get('pre_release'):
            rels.append((vtuple(m.group(1)), m.group(1), r['resource_uri'].rstrip('/').split('/')[-1]))
    rels.sort(reverse=True)
    for vt, ver, rid in rels:
        if (want and ver != want) or (not want and vt[1] not in minors):
            continue
        for f in get_json(PY_FILES % rid):
            if f['url'].endswith('-embed-amd64.zip'):
                if not f.get('sha256_sum'):
                    raise Fail('python.org gives no SHA-256 for %s' % f['url'])
                return ver, dict(name=f['url'].rsplit('/', 1)[1], url=f['url'], sha256=f['sha256_sum'].lower(),
                                 size=f.get('filesize'))
        if want:
            break
    raise Fail('no Python release %s with a 64-bit embeddable zip (numpy wheels for 3.%s)'
               % (want or '3.x', ', 3.'.join(str(m) for m in sorted(minors))))


def gh_digest(asset):
    d = asset.get('digest') or ''
    if not d.startswith('sha256:'):
        raise Fail('GitHub gives no SHA-256 digest for %s' % asset['browser_download_url'])
    return d[7:].lower()


def resolve_vgmstream(tag):
    r = get_json(GH_API % VGM_REPO + ('/releases/tags/%s' % tag if tag else '/releases/latest'))
    a = next((x for x in r['assets'] if x['name'] == 'vgmstream-win64.zip'), None)
    if not a:
        raise Fail('vgmstream release %s has no vgmstream-win64.zip' % r['tag_name'])
    return r['tag_name'], dict(name=a['name'], url=a['browser_download_url'], sha256=gh_digest(a), size=a['size'],
                               release=r['html_url'], published=r.get('published_at', ''))


def resolve_ffmpeg(branch, tag=None):
    """The newest autobuild release (or the one of the given tag, to rebuild a bundle with the same build) with a static
    win64 LGPL build of the newest (or the given) release branch."""
    releases = [get_json(GH_API % FF_REPO + '/releases/tags/' + tag)] if tag else         get_json(GH_API % FF_REPO + '/releases?per_page=15')
    for r in releases:
        if not r['tag_name'].startswith('autobuild-') or r.get('draft') or r.get('prerelease'):
            continue
        best = None
        for a in r['assets']:
            m = FF_ASSET.match(a['name'])
            if not m or not m.group(4):
                continue
            if branch and m.group(5) != branch:
                continue
            if best is None or vtuple(m.group(5)) > vtuple(best[0].group(5)):
                best = (m, a)
        if not best:
            continue
        m, a = best
        repo = r['html_url'].split('/releases/')[0].replace('https://github.com/', '')
        ref = get_json(GH_API % repo + '/git/ref/tags/' + r['tag_name'])
        obj = ref['object']
        if obj['type'] == 'tag':   # an annotated tag: its commit
            obj = get_json(obj['url'])['object']
        return m.group(1), dict(name=a['name'], url=a['browser_download_url'], sha256=gh_digest(a), size=a['size'],
                                release=r['html_url'], tag=r['tag_name'], scripts_commit=obj['sha'], repo=repo,
                                ffmpeg_commit=m.group(4), branch=m.group(5), published=r.get('published_at', ''))
    raise Fail('no FFmpeg-Builds autobuild release%s with a win64-lgpl build of a release branch%s'
               % (' ' + tag if tag else '', ' ' + branch if branch else ''))


# ------------------------------------------------------------------------------------------------- downloads
def fetch(item, cache):
    """The download in the cache, checked against its published SHA-256 (reused while it matches)."""
    url = item['url']
    if not url.startswith(ALLOWED):
        raise Fail('refusing a download from %s (not one of the official sources)' % url)
    fn = os.path.join(cache, item['name'])
    if os.path.isfile(fn) and sha256(fn) == item['sha256']:
        say('  cached   %s' % item['name'])
        return fn
    say('  download %s' % url)
    part = fn + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': UA['User-Agent']})
    with urllib.request.urlopen(req, timeout=120) as r, open(part, 'wb') as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(part)
    if got != item['sha256']:
        os.remove(part)
        raise Fail('%s: SHA-256 %s, the source publishes %s' % (item['name'], got, item['sha256']))
    os.replace(part, fn)
    return fn


def safe_member(name):
    n = name.replace('\\', '/')
    parts = n.split('/')
    return not (n.startswith('/') or ':' in n or '..' in parts)


def unzip(zfn, dest, pick=None):
    """Unpacks zfn (every member, or pick(name) -> relative target or None) into dest; returns the names written."""
    out = []
    with zipfile.ZipFile(zfn) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if not safe_member(info.filename):
                raise Fail('%s: unsafe member name %r' % (os.path.basename(zfn), info.filename))
            rel = pick(info.filename) if pick else info.filename
            if not rel:
                continue
            p = os.path.join(dest, *rel.split('/'))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with z.open(info) as src, open(p, 'wb') as f:
                shutil.copyfileobj(src, f, 1 << 20)
            out.append(rel)
    return out


def check_wheel_record(site, names):
    """Every file of the numpy wheel against its RECORD (sha256, size)."""
    rec = next((n for n in names if n.endswith('.dist-info/RECORD')), None)
    if not rec:
        raise Fail('the numpy wheel has no RECORD')
    n = 0
    for line in open(os.path.join(site, *rec.split('/')), encoding='utf-8'):
        f = line.rstrip('\r\n').rsplit(',', 2)
        if len(f) != 3 or not f[1]:
            continue
        algo, _, want = f[1].partition('=')
        if algo != 'sha256':
            raise Fail('numpy RECORD: %s uses %s' % (f[0], algo))
        p = os.path.join(site, *f[0].split('/'))
        got = base64.urlsafe_b64encode(bytes.fromhex(sha256(p))).rstrip(b'=').decode()
        if got != want or (f[2] and os.path.getsize(p) != int(f[2])):
            raise Fail('numpy wheel: %s does not match its RECORD' % f[0])
        n += 1
    return n


def run(cmd, cwd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=cwd)
    return r.returncode, r.stdout.decode('utf-8', 'replace')


# ------------------------------------------------------------------------------------------------- assembling
def make_python(bundle, py_zip, np_whl, py_ver, scratch):
    pd = os.path.join(bundle, 'python')
    unzip(py_zip, pd)
    pth = [n for n in os.listdir(pd) if re.match(r'^python3\d+\._pth$', n)]
    if len(pth) != 1:
        raise Fail('the embeddable Python has no (or more than one) python3XX._pth: %s' % pth)
    fn = os.path.join(pd, pth[0])
    lines = open(fn, encoding='utf-8').read().splitlines()
    if 'Lib\\site-packages' not in lines:
        at = lines.index('.') + 1 if '.' in lines else len(lines)
        lines.insert(at, 'Lib\\site-packages')
    with open(fn, 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')
    if any(l.strip() == 'import site' for l in lines):
        raise Fail('%s enables "import site"; the bundle keeps it off' % pth[0])
    site = os.path.join(pd, 'Lib', 'site-packages')
    names = unzip(np_whl, site)
    say('  numpy: %d files, RECORD checked (%d)' % (len(names), check_wheel_record(site, names)))
    exe = os.path.join(pd, 'python.exe')
    # precompiled with this very Python; unchecked-hash: valid whatever the files' times are after unpacking
    code, out = run([exe, '-I', '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode', 'unchecked-hash', site],
                    cwd=scratch)
    if code:
        raise Fail('compileall of numpy failed:\n' + out[-2000:])
    return exe, pth[0]


def make_vgmstream(bundle, zfn):
    vd = os.path.join(bundle, 'vgmstream')
    names = unzip(zfn, vd)
    low = {n.lower(): n for n in names}
    if 'vgmstream-cli.exe' not in low:
        raise Fail('vgmstream-win64.zip holds no vgmstream-cli.exe at its top (%s)' % ', '.join(sorted(names)[:8]))
    if 'copying' not in low:
        raise Fail('vgmstream-win64.zip holds no COPYING (its license)')
    return os.path.join(vd, 'vgmstream-cli.exe'), names


def make_ffmpeg(bundle, zfn, ff, ver, scratch):
    fd = os.path.join(bundle, 'ffmpeg')

    def pick(name):
        parts = name.split('/')
        if parts[-2:] == ['bin', 'ffmpeg.exe']:
            return 'ffmpeg.exe'
        if len(parts) == 2 and parts[1] == 'LICENSE.txt':
            return 'LICENSE.txt'
        return None
    got = unzip(zfn, fd, pick)
    if sorted(got) != ['LICENSE.txt', 'ffmpeg.exe']:
        raise Fail('%s: expected bin\\ffmpeg.exe and LICENSE.txt, found %s' % (ff['name'], got))
    exe = os.path.join(fd, 'ffmpeg.exe')
    lic = open(os.path.join(fd, 'LICENSE.txt'), encoding='utf-8', errors='replace').read()
    if 'LESSER GENERAL PUBLIC LICENSE' not in lic.upper():
        raise Fail('%s: LICENSE.txt is not the LGPL' % ff['name'])
    code, vout = run([exe, '-hide_banner', '-version'], cwd=scratch)
    conf = next((l for l in vout.splitlines() if l.startswith('configuration:')), '')
    if code or not conf:
        raise Fail('ffmpeg -version failed: %s' % vout[-500:])
    if '--enable-gpl' in conf or '--enable-nonfree' in conf:
        raise Fail('this ffmpeg is not an LGPL build: %s' % conf)
    code, lout = run([exe, '-hide_banner', '-L'], cwd=scratch)
    if 'Lesser General Public License' not in lout:
        raise Fail('ffmpeg -L does not say LGPL: %s' % lout[:300])
    code, enc = run([exe, '-hide_banner', '-encoders'], cwd=scratch)
    if not re.search(r'(?m)^\s*A\S*\s+mp2\s', enc):
        raise Fail('this ffmpeg has no native mp2 encoder')
    code, flt = run([exe, '-hide_banner', '-filters'], cwd=scratch)
    for name in ('apad', 'aresample'):
        if not re.search(r'(?m)^\s*\S+\s+%s\s' % name, flt):
            raise Fail('this ffmpeg has no %s filter' % name)
    first = vout.splitlines()[0] if vout else ''
    with open(os.path.join(fd, 'SOURCE.txt'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('FFmpeg %s, %s\n' % (ver, first))
        f.write('An unmodified build of FFmpeg-Builds (formerly BtbN/FFmpeg-Builds), static, win64, LGPL variant.\n'
                'FFmpeg is licensed under the GNU Lesser General Public License 2.1 or later (LICENSE.txt); this\n'
                'build links only libraries whose licenses allow that (the build scripts select them for the "lgpl"\n'
                'variant), no GPL or nonfree part.\n\n')
        f.write('Binary:          %s (SHA-256 %s)\n' % (ff['url'], ff['sha256']))
        f.write('Release:         %s\n' % ff['release'])
        f.write('FFmpeg source:   https://github.com/FFmpeg/FFmpeg/tree/%s (git describe %s; the same commit is in\n'
                '                 git://source.ffmpeg.org/ffmpeg.git)\n' % (ff['ffmpeg_commit'], ver))
        f.write('Build scripts:   https://github.com/%s/tree/%s (tag %s); they name the exact versions of\n'
                '                 every library linked into ffmpeg.exe\n' % (ff['repo'], ff['scripts_commit'], ff['tag']))
        f.write('Only bin\\ffmpeg.exe and LICENSE.txt of the archive are part of glimpse_fix\'s DLC tools.\n\n')
        f.write('%s\n' % conf)
    return exe, conf


def tone_wav(fn, rate=44100, secs=1.0):
    import math
    n = int(rate * secs)
    w = wave.open(fn, 'wb')
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(b''.join(struct.pack('<hh', int(8000 * math.sin(i * 0.0627)), int(8000 * math.sin(i * 0.031)))
                           for i in range(n)))
    w.close()


def test_bundle(bundle, py, vgm, ffm, scratch):
    # every module the shipped converters import (make_release's trace) loads in this Python with -I
    mods = set()
    local = set(make_release.tool_modules())
    for rel in make_release.converter_set():
        imp, _ = make_release.references(rel)
        mods |= {m for m in imp if m not in local}
    mods |= {'numpy', 'ctypes', 'winreg', 'zlib', 'lzma', 'bz2', 'json', 'sqlite3', 'hashlib', 'wave', 'zipfile'}
    code = ('import importlib, sys\nbad = []\nfor m in sys.argv[1:]:\n    try:\n        importlib.import_module(m)\n'
            '    except Exception as ex:\n        bad.append("%s: %s" % (m, ex))\n'
            'import numpy\na = numpy.arange(12, dtype=numpy.float32).reshape(3, 4)\nassert float((a @ a.T).sum()) == 1134.0\n'
            'import json\nprint("numpy", numpy.__version__, "python", sys.version.split()[0], "modules", len(sys.argv) - 1)\n'
            'print("PATH " + json.dumps(sys.path))\n'
            'print("BAD" if bad else "OK", bad)\n')
    c, out = run([py, '-I', '-X', 'utf8', '-B', '-c', code] + sorted(mods), cwd=scratch)
    say('  python: %s' % out.strip().replace('\n', '\n          '))
    if c or 'OK []' not in out:
        raise Fail('the bundled Python cannot load what the converters import: %s' % out[-1500:])
    path = json.loads(next(l for l in out.splitlines() if l.startswith('PATH '))[5:])
    own = os.path.normcase(os.path.join(bundle, 'python'))
    outside = [p for p in path if not os.path.normcase(os.path.abspath(p)).startswith(own)]
    if outside:   # no user or system site-packages, no current folder: only the bundle's own files
        raise Fail('the bundled Python sees folders outside the bundle: %s' % outside)
    c, out = run([vgm, '-V'], cwd=scratch)
    m = re.search(r'"version"\s*:\s*"([^"]+)"', out)
    if not m:
        raise Fail('vgmstream-cli -V failed: %s' % out[:300])
    say('  vgmstream-cli: version %s' % m.group(1))
    wav = os.path.join(scratch, 'tone.wav')
    mp2 = os.path.join(scratch, 'tone.mp2')
    tone_wav(wav)
    c, out = run([ffm, '-v', 'error', '-y', '-i', wav, '-af', 'apad=pad_len=481', '-ar', '48000', '-c:a', 'mp2',
                  '-b:a', '160k', '-f', 'mp2', mp2], cwd=scratch)
    if c or not os.path.isfile(mp2):
        raise Fail('ffmpeg could not encode MPEG Layer II: %s' % out[-500:])
    fsbconv = _load('glimpse_fsbconv', os.path.join(REPO, 'tools', 'dlc', 'audio', 'fsbconv.py'))
    frames, info = fsbconv.mpeg_frames(open(mp2, 'rb').read())
    if not frames or info['rate'] != 48000 or info['kbps'] != 160:
        raise Fail('ffmpeg\'s Layer II output is not what fsbconv.py expects: %s' % info)
    say('  ffmpeg: mp2 test tone, %d frames, %s' % (len(frames), info))
    # vgmstream-cli decodes a WAV it is given (a smoke test of the decoder chain)
    out_wav = os.path.join(scratch, 'tone_vgm.wav')
    c, out = run([vgm, '-o', out_wav, wav], cwd=scratch)
    say('  vgmstream-cli: decoded the test WAV: %s' % ('yes' if c == 0 and os.path.isfile(out_wav) else 'no (%s)' % out.strip()[-200:]))


# ------------------------------------------------------------------------------------------------- texts
def write_notice(bundle, comps, pth):
    py, np_, vg, ff = comps['python'], comps['numpy'], comps['vgmstream'], comps['ffmpeg']
    t = []
    t.append('glimpse_fix DLC tools - third-party notices\n')
    t.append('This bundle holds unmodified third-party programs that glimpse_fix\'s DLC converters (glimpse_tools\\dlc)\n'
             'run. None of them is part of glimpse_fix itself (GPL-3.0); each keeps its own license, named below with\n'
             'the file of its full text. Versions, download URLs and SHA-256 of everything: %s.\n' % MANIFEST)
    t.append('1. Python %s (python\\)\n'
             '   The Windows embeddable package (64-bit) of python.org: %s\n'
             '   Python Software Foundation License Version 2 (PSF-2.0); the full text, with the licenses of the parts\n'
             '   Python includes (OpenSSL, libffi, bzip2, xz, SQLite, zlib, mpdecimal, ...), is python\\LICENSE.txt.\n'
             '   Changed: python\\%s lists Lib\\site-packages (where numpy is); nothing else.\n'
             '   Source: https://www.python.org/downloads/source/ and https://github.com/python/cpython (tag v%s).\n'
             % (py['version'], py['url'], pth, py['version']))
    t.append('2. numpy %s (python\\Lib\\site-packages\\numpy, numpy.libs, numpy-%s.dist-info)\n'
             '   The official wheel of PyPI: %s\n'
             '   BSD 3-Clause license (Copyright (c) 2005-2025, NumPy Developers); the wheel ships the libraries it\n'
             '   bundles (OpenBLAS: BSD-3-Clause; the GCC runtime libraries: GPL-3.0 with the GCC Runtime Library\n'
             '   Exception; and others) with their texts in numpy-%s.dist-info\\licenses.\n'
             '   Source: https://github.com/numpy/numpy (tag v%s), https://pypi.org/project/numpy/%s/#files.\n'
             % (np_['version'], np_['version'], np_['url'], np_['version'], np_['version'], np_['version']))
    t.append('3. vgmstream %s (vgmstream\\)\n'
             '   vgmstream-win64.zip of the vgmstream release on GitHub, as it is: %s\n'
             '   vgmstream\'s own license is ISC style (vgmstream\\COPYING). The DLLs next to vgmstream-cli.exe are the\n'
             '   decoder libraries of that release (FFmpeg as avcodec/avformat/avutil/swresample-vgmstream:\n'
             '   LGPL-2.1+; mpg123: LGPL-2.1; libvorbis, CELT, Speex: BSD style), as vgmstream distributes them;\n'
             '   the vgmstream repository (its build documentation and ext_libs) names their versions and sources.\n'
             '   Source: https://github.com/vgmstream/vgmstream (tag %s).\n'
             % (vg['version'], vg['url'], vg['version']))
    t.append('4. FFmpeg %s (ffmpeg\\ffmpeg.exe)\n'
             '   An unmodified static LGPL build of FFmpeg-Builds (formerly BtbN/FFmpeg-Builds): %s\n'
             '   GNU Lesser General Public License 2.1 or later; the full text is ffmpeg\\LICENSE.txt. It is an LGPL\n'
             '   build (no --enable-gpl, no --enable-nonfree; its configuration is in ffmpeg\\SOURCE.txt).\n'
             '   Source: FFmpeg commit %s (https://github.com/FFmpeg/FFmpeg/tree/%s), built with the scripts of\n'
             '   https://github.com/%s/tree/%s, which pin every library linked in. ffmpeg\\SOURCE.txt has the details.\n'
             '   glimpse_fix only runs ffmpeg.exe as a separate program (fsbconv.py: WAV to MPEG Layer II).\n'
             % (ff['version'], ff['url'], ff['ffmpeg_commit'], ff['ffmpeg_commit'], ff['repo'], ff['scripts_commit']))
    t.append('Not in this bundle: D3DCompiler_41.dll. It is part of Microsoft\'s DirectX End-User Runtime (June 2010),\n'
             'which may only be installed with Microsoft\'s own installer: setup_deps.bat -Install runs it (winget\n'
             'Microsoft.DirectX, or Microsoft\'s signed dxwebsetup.exe). The game\'s installer usually put it in place.\n')
    with open(os.path.join(bundle, NOTICE), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(t))


def write_version(bundle, version, comps, built):
    with open(os.path.join(bundle, VERSION_FILE), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('# glimpse_fix DLC tools (tools\\make_dlc_tools.py); unpacked into %LOCALAPPDATA%\\glimpse_fix\\tools\n')
        f.write('bundle\t%s\nformat\t%d\nbuilt\t%s\n' % (version, FORMAT, built))
        for k in ('python', 'numpy', 'vgmstream', 'ffmpeg'):
            f.write('%s\t%s\n' % (k, comps[k]['version']))
        f.write('exe\tpython\tpython/python.exe\nexe\tvgmstream-cli\tvgmstream/vgmstream-cli.exe\nexe\tffmpeg\tffmpeg/ffmpeg.exe\n')


def write_manifest(bundle, version, comps):
    files = []
    for dp, dn, fns in os.walk(bundle):
        dn.sort()
        for n in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, n), bundle).replace(os.sep, '/')
            if rel != MANIFEST:
                files.append(rel)
    files.sort(key=lambda r: r.lower())
    with open(os.path.join(bundle, MANIFEST), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('# glimpse_fix DLC tools %s: component <name> <version> <download URL> <SHA-256 of the download> '
                '<license>; file <path> <size> <SHA-256>\n' % version)
        for k, lic in (('python', 'PSF-2.0'), ('numpy', 'BSD-3-Clause'), ('vgmstream', 'ISC-style'),
                       ('ffmpeg', 'LGPL-2.1-or-later')):
            c = comps[k]
            f.write('component\t%s\t%s\t%s\t%s\t%s\n' % (k, c['version'], c['url'], c['sha256'], lic))
        for rel in files:
            p = os.path.join(bundle, *rel.split('/'))
            f.write('file\t%s\t%d\t%s\n' % (rel, os.path.getsize(p), sha256(p)))
    return len(files)


# ------------------------------------------------------------------------------------------------- main
def main(argv):
    ap = argparse.ArgumentParser(description='Build the glimpse_fix DLC tools bundle (nothing is published).')
    ap.add_argument('--version', help='the bundle\'s version = the glimpse_fix release (default: src\\common.h)')
    ap.add_argument('--out', default=os.path.join(REPO, 'dist'), help='where the folder and zip go (default %(default)s)')
    ap.add_argument('--cache', help='downloads (default <out>\\dlc_tools_cache)')
    ap.add_argument('--force', action='store_true', help='replace an existing bundle folder and zip of this version')
    ap.add_argument('--python', help='this Python 3.x.y instead of the newest one numpy has a wheel for')
    ap.add_argument('--numpy', help='this numpy version instead of the newest')
    ap.add_argument('--vgmstream', help='this vgmstream release tag (e.g. r2168) instead of the newest')
    ap.add_argument('--ffmpeg-branch', help='this FFmpeg release branch (e.g. 9.0) instead of the newest')
    ap.add_argument('--ffmpeg-tag', help='this FFmpeg-Builds autobuild release (e.g. autobuild-2026-11-22-12-36) instead '
                    'of the newest, to rebuild a bundle with the same ffmpeg')
    ap.add_argument('--no-test', action='store_true', help='skip the tests of the assembled bundle')
    a = ap.parse_args(argv)

    version = a.version or make_release.version_of_source()[0]
    if not version:
        raise Fail('No GLIMPSE_VERSION in src\\common.h; give --version.')
    out = os.path.abspath(a.out)
    cache = os.path.abspath(a.cache) if a.cache else os.path.join(out, 'dlc_tools_cache')
    bundle = os.path.join(out, 'dlc_tools_' + version)
    zip_fn = os.path.join(out, ZIP_NAME % version)
    for p in (bundle, zip_fn):
        if os.path.exists(p) and not a.force:
            raise Fail('%s exists already (--force replaces it).' % p)
    os.makedirs(cache, exist_ok=True)

    say('== resolving the versions')
    np_ver, wheels = resolve_numpy(a.numpy)
    py_ver, py = resolve_python(a.python, set(wheels))
    minor = vtuple(py_ver)[1]
    if minor not in wheels:
        raise Fail('numpy %s has no wheel for Python 3.%d' % (np_ver, minor))
    np_ = dict(wheels[minor], version=np_ver)
    py['version'] = py_ver
    vg_ver, vg = resolve_vgmstream(a.vgmstream)
    vg['version'] = vg_ver
    ff_ver, ff = resolve_ffmpeg(a.ffmpeg_branch, a.ffmpeg_tag)
    ff['version'] = ff_ver
    comps = dict(python=py, numpy=np_, vgmstream=vg, ffmpeg=ff)
    for k, c in comps.items():
        say('  %-10s %-24s %s' % (k, c['version'], c['url']))

    say('== downloads (%s)' % cache)
    files = {k: fetch(c, cache) for k, c in comps.items()}

    say('== assembling %s' % bundle)
    if os.path.isdir(bundle):
        if os.path.normcase(os.path.dirname(bundle)) != os.path.normcase(out):
            raise Fail('refusing to remove %s' % bundle)
        shutil.rmtree(bundle)
    os.makedirs(bundle)
    scratch = tempfile.mkdtemp(prefix='glimpse_dlc_tools_')
    try:
        pyexe, pth = make_python(bundle, files['python'], files['numpy'], py_ver, scratch)
        vgm, _ = make_vgmstream(bundle, files['vgmstream'])
        ffm, conf = make_ffmpeg(bundle, files['ffmpeg'], ff, ff_ver, scratch)
        if not a.no_test:
            say('== tests')
            test_bundle(bundle, pyexe, vgm, ffm, scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    built = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    write_notice(bundle, comps, pth)
    write_version(bundle, version, comps, built)
    n = write_manifest(bundle, version, comps)

    say('== zip')
    names = []
    for dp, dn, fns in os.walk(bundle):
        dn.sort()
        for fn in sorted(fns):
            names.append(os.path.relpath(os.path.join(dp, fn), bundle).replace(os.sep, '/'))
    names.sort(key=lambda r: (r.count('/'), r.lower()))
    if os.path.isfile(zip_fn):
        os.remove(zip_fn)
    with zipfile.ZipFile(zip_fn + '.part', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in names:
            z.write(os.path.join(bundle, *rel.split('/')), rel)
    os.replace(zip_fn + '.part', zip_fn)
    digest = sha256(zip_fn)
    with open(zip_fn + '.sha256', 'w', encoding='ascii', newline='\n') as f:
        f.write('%s  %s\n' % (digest, os.path.basename(zip_fn)))
    total = sum(os.path.getsize(os.path.join(bundle, *r.split('/'))) for r in names)
    say('== %s: %d files (%d in the manifest), %d bytes unpacked, %d bytes zipped, SHA-256 %s'
        % (zip_fn, len(names), n, total, os.path.getsize(zip_fn), digest))
    for k in ('python', 'numpy', 'vgmstream', 'ffmpeg'):
        say('   %-10s %s' % (k, comps[k]['version']))
    say('Attach %s (and its .sha256) to the GitHub release v%s, next to glimpse_fix-v%s.zip.'
        % (os.path.basename(zip_fn), version, version))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as ex:
        print('FAILED: %s' % ex)
        sys.exit(1)
