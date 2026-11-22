# glimpse_fix: build the release zip.
#
#   py -3 -I tools\make_release.py [--readme <README.txt>] [--version <x.y.z>] [--out <dir>] [--no-build] [--force]
#       [--allow-local-paths]
#
# 1. runs build.bat (--no-build: takes build\ as it is)
# 2. puts together <out>\v<version digits>\ (default <repo>\dist) and zips it to <out>\glimpse_fix-v<version>.zip:
#      dinput8.dll, glimpse_config.exe, setlaa.exe   build\
#      SplitSecondPad.ini                            the template in the repository
#      README.txt                                    --readme (the release notes, written by hand as before); without
#                                                    it a short stub (test builds)
#      LICENSE, LICENSE-SMAA.txt, THIRD_PARTY.txt    GPL-3.0, third_party\smaa\LICENSE.txt, THIRD_PARTY.txt
#      glimpse_tools\docs\INI.md                     docs\INI.md
#      glimpse_tools\dlc\...                         the DLC converters: build_all.py, install_dlc.py and every
#                                                    script of tools\dlc they import or run (traced, see below),
#                                                    README_build.md, xenos\LICENSE-rexglue.txt and
#                                                    glimpse_tools_files.tsv (size and SHA-256 of each, which
#                                                    build_all.py --preflight and every build check)
#    The user copies all of it into the game folder; glimpse_config.exe finds the converters in glimpse_tools\dlc
#    next to it.
# 3. checks: nothing but scripts and text below glimpse_tools (no game or DLC data: no .ark/.pkl/.fsb/.fev/.bik/.json,
#    no file over 1 MB), no tests, no __pycache__, nothing of tools\dlc\xex (local only); no shipped file of any type
#    holds the retail XEX2 key (hex or raw bytes); no paths of the developer's PC in a shipped script (an error unless
#    --allow-local-paths, which a test build may use; the paths are listed either way). Every other file below tools\
#    that git tracks or would pick up is scanned too, as a warning (shipped or not, they would land in the repository).
# The converter set: starting from build_all.py and install_dlc.py, every "import x" / "from x import" anywhere in a
# file (also inside functions) that names a module of tools\dlc, and every string 'name.py' that names one (scripts
# started with subprocess), recursively. Module names are unique below tools\dlc (checked).
# Nothing is uploaded or published; the zip is only written.
import argparse
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile

sys.dont_write_bytecode = True
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DLC = os.path.join(REPO, 'tools', 'dlc')
TOOLS_DIR = 'glimpse_tools'             # in the zip and the game folder; glimpse_config.exe looks for <it>\dlc
DLC_ENTRIES = ('build_all.py', 'install_dlc.py')
# shipped besides the traced scripts (paths below tools\dlc)
DLC_EXTRA = ('README_build.md', 'xenos/LICENSE-rexglue.txt')
# shipped when present, also before a converter imports them (their data, e.g. the TU4 text JSON, never is)
DLC_OPTIONAL = ('mode/tu4text.py',)
FILES_LIST = 'glimpse_tools_files.tsv'  # build_all.FILES_LIST
# xex: the local-only XEX2 / title update tools (tools\dlc\xex is not in the repository, it holds a decryption tool);
# ppc: reverse engineering helpers (need capstone): never traced, never shipped
SKIP_DIRS = {'__pycache__', 'ghidra', 'xex', 'ppc'}
# SHA-256 of the retail XEX2 key's 16 bytes: no shipped file may hold the key (a second lock besides SKIP_DIRS),
# neither as 32 hex digits in a text file nor as the 16 raw bytes in any file (scripts, texts, the DLL and exes)
XEX_KEY_SHA256 = 'a0bd3f5f56adf2b0238a2702c0c0cd8548fb34b3dc19345943dc011010723fe4'
# (zip path, repo path); None = optional
TOP = (('dinput8.dll', 'build/dinput8.dll'), ('glimpse_config.exe', 'build/glimpse_config.exe'),
       ('setlaa.exe', 'build/setlaa.exe'), ('SplitSecondPad.ini', 'SplitSecondPad.ini'), ('LICENSE', 'LICENSE'),
       ('LICENSE-SMAA.txt', 'third_party/smaa/LICENSE.txt'), ('THIRD_PARTY.txt', 'THIRD_PARTY.txt'),
       (TOOLS_DIR + '/docs/INI.md', 'docs/INI.md'))
DATA_EXT = ('.ark', '.pkl', '.fsb', '.fev', '.bik', '.json', '.exe', '.dll', '.xex', '.xexp', '.bin', '.dds', '.wav')
TEXT_EXT = ('.py', '.md', '.txt', '.tsv')
# a developer's PC in a shipped file. PERSONAL (user folders with the account name, the scratchpad, the source
# folder) is an error: it would publish the account name and the build would depend on folders only that PC has.
# MACHINE (a fixed game library or tool folder as a manual run's default) is listed: build_all.py passes every such
# path itself, so these defaults are never used by a build, but they are worth making neutral.
PERSONAL_RE = re.compile(r'(?i)(?:[a-z]:\\users\\(?!public\\)[^\\\'"%]+\\|\\scratchpad\b|scratchpad\\|[a-z]:\\projekte\\|'
                         r'\\appdata\\local\\temp\\claude)')
# the repository scan (all of tools\): what names this PC's account or session, not the word "scratchpad" in notes
REPO_RE = re.compile(r'(?i)(?:[a-z]:\\users\\(?!public\\)[^\\\'"%<>.]+\\|\\appdata\\local\\temp\\claude|[a-z]:\\projekte\\)')
MACHINE_RE = re.compile(r'(?i)(?:[d-z]:\\steamlibrary\\|[d-z]:\\vgmstream\\|[d-z]:\\program files)')


class Fail(Exception):
    pass


def sha256(fn):
    h = hashlib.sha256()
    with open(fn, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------- version
def version_of_source():
    h = open(os.path.join(REPO, 'src', 'common.h'), encoding='utf-8', errors='replace').read()
    m = re.search(r'#define\s+GLIMPSE_VERSION\s+"([^"]+)"', h)
    rc = open(os.path.join(REPO, 'config', 'glimpse_config.rc'), encoding='utf-8', errors='replace').read()
    r = re.search(r'VALUE\s+"FileVersion",\s*"([^"]+)"', rc)
    return (m.group(1) if m else None), (r.group(1) if r else None)


# ------------------------------------------------------------------------------------------------- converters
def tool_modules():
    """{module name: path relative to tools\\dlc} of every .py below tools\\dlc (no tests' output, no caches)."""
    by_name = {}
    for dp, dn, fns in os.walk(DLC):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith('test_out'))
        for n in sorted(fns):
            if n.endswith('.py'):
                rel = os.path.relpath(os.path.join(dp, n), DLC).replace(os.sep, '/')
                by_name.setdefault(n[:-3], []).append(rel)
    dup = {k: v for k, v in by_name.items() if len(v) > 1}
    if dup:
        raise Fail('module names used twice below tools\\dlc (the trace cannot tell them apart): %s' % dup)
    return {k: v[0] for k, v in by_name.items()}


def references(rel):
    """(module names imported anywhere in the file, names of .py files given as strings)."""
    src = open(os.path.join(DLC, *rel.split('/')), encoding='utf-8-sig').read()
    mods, scripts = set(), set()
    for node in ast.walk(ast.parse(src, rel)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            mods.add(node.module.split('.')[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith('.py'):
            scripts.add(os.path.basename(node.value.replace('\\', '/'))[:-3])
    return mods, scripts


def converter_set():
    mods = tool_modules()
    seen, todo = set(), list(DLC_ENTRIES)
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        imp, scripts = references(rel)
        for name in imp | scripts:
            if name in mods and mods[name] not in seen:
                todo.append(mods[name])
    local_only = sorted(r for r in seen if set(r.split('/')[:-1]) & SKIP_DIRS)
    if local_only:   # cannot happen while tool_modules skips them: a second lock on the release
        raise Fail('local-only tools must never be shipped: %s' % ', '.join(local_only))
    return sorted(seen)


# ------------------------------------------------------------------------------------------------- checks
def check_tools_file(rel, fn):
    """Problems of a file below glimpse_tools: (errors, personal paths, machine paths)."""
    errors, local, machine = [], [], []
    low = rel.lower()
    parts = low.split('/')
    if low.endswith(DATA_EXT) or not low.endswith(TEXT_EXT):
        errors.append('%s: not a script or text file' % rel)
    if os.path.getsize(fn) > 1 << 20:
        errors.append('%s: over 1 MB' % rel)
    if '__pycache__' in parts or any(p.startswith('test_out') for p in parts) or \
            os.path.basename(low).startswith(('test_', 'testall')):
        errors.append('%s: a test or cache file' % rel)
    if low.endswith(('.py', '.md')):
        for i, line in enumerate(open(fn, encoding='utf-8-sig', errors='replace'), 1):
            if PERSONAL_RE.search(line):
                local.append('%s:%d: %s' % (rel, i, line.strip()[:140]))
            elif MACHINE_RE.search(line):
                machine.append('%s:%d: %s' % (rel, i, line.strip()[:140]))
    return errors, local, machine


def xex_key_hits(rel, fn):
    """Where a shipped file (any type) holds the retail XEX2 key: as 32 hex digits or as its 16 raw bytes."""
    with open(fn, 'rb') as f:
        data = f.read()
    key = bytes.fromhex(XEX_KEY_SHA256)
    hits = []
    for m in re.finditer(rb'(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])', data):
        if hashlib.sha256(bytes.fromhex(m.group().decode('ascii'))).digest() == key:
            hits.append('%s:%d: holds the retail XEX2 key as hex digits' % (rel, data.count(b'\n', 0, m.start()) + 1))
    sha = hashlib.sha256
    for off in range(len(data) - 15):
        if sha(data[off:off + 16]).digest() == key:
            hits.append('%s: holds the retail XEX2 key as 16 bytes at offset 0x%X' % (rel, off))
    return hits


def repo_personal_paths():
    """Personal paths (REPO_RE) in every text file below tools\\ that git tracks or would pick up (git ls-files
    --cached --others --exclude-standard; read only), shipped or not: so they cannot creep back into the repository."""
    r = subprocess.run(['git', '-C', REPO, 'ls-files', '-co', '--exclude-standard', '--', 'tools'],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if r.returncode:
        return ['(git ls-files failed: the repository scan was skipped)']
    hits = []
    for rel in sorted(set(r.stdout.decode('utf-8', 'replace').splitlines())):
        if not rel.lower().endswith(('.py', '.md', '.txt', '.tsv', '.bat', '.sh', '.java', '.c', '.cpp', '.h')):
            continue
        fn = os.path.join(REPO, *rel.split('/'))
        if not os.path.isfile(fn) or os.path.getsize(fn) > 4 << 20 or os.path.normcase(fn) == os.path.normcase(__file__):
            continue
        for i, line in enumerate(open(fn, encoding='utf-8-sig', errors='replace'), 1):
            if REPO_RE.search(line):
                hits.append('%s:%d: %s' % (rel, i, line.strip()[:140]))
    return hits


# ------------------------------------------------------------------------------------------------- main
def main(argv):
    ap = argparse.ArgumentParser(description='Build the glimpse_fix release zip (nothing is published).')
    ap.add_argument('--version', help='default: GLIMPSE_VERSION of src\\common.h')
    ap.add_argument('--readme', help='README.txt of the release (the release notes); default: a short stub')
    ap.add_argument('--out', default=os.path.join(REPO, 'dist'), help='where the folder and the zip go (default %(default)s)')
    ap.add_argument('--no-build', action='store_true', help='do not run build.bat, take build\\ as it is')
    ap.add_argument('--force', action='store_true', help='replace an existing release folder and zip of this version')
    ap.add_argument('--allow-local-paths', action='store_true',
                    help='test builds: paths of this PC in shipped scripts are only listed, not an error')
    a = ap.parse_args(argv)

    src_ver, rc_ver = version_of_source()
    version = a.version or src_ver
    if not version:
        raise Fail('No GLIMPSE_VERSION in src\\common.h; give --version.')
    if rc_ver and src_ver and not src_ver.startswith(rc_ver):
        print('WARNING: src\\common.h says %s, config\\glimpse_config.rc says %s' % (src_ver, rc_ver))
    if a.version and src_ver and a.version != src_ver:
        print('WARNING: --version %s, but the DLL calls itself %s (src\\common.h)' % (a.version, src_ver))
    out = os.path.abspath(a.out)
    folder = os.path.join(out, 'v' + version.replace('.', ''))
    zip_fn = os.path.join(out, 'glimpse_fix-v%s.zip' % version)
    for p in (folder, zip_fn):
        if os.path.exists(p) and not a.force:
            raise Fail('%s exists already (--force replaces it).' % p)

    if not a.no_build:
        print('== build.bat')
        r = subprocess.run(['cmd', '/c', os.path.join(REPO, 'build.bat')], cwd=REPO)
        if r.returncode:
            raise Fail('build.bat failed (exit code %d)' % r.returncode)

    # ---- what goes where: [(zip path, source)]
    items = []
    for dst, src in TOP:
        fn = os.path.join(REPO, *src.split('/'))
        if not os.path.isfile(fn):
            raise Fail('missing %s' % fn)
        items.append((dst, fn))
    if a.readme:
        if not os.path.isfile(a.readme):
            raise Fail('--readme %s does not exist' % a.readme)
        items.append(('README.txt', os.path.abspath(a.readme)))
    else:
        print('WARNING: no --readme: README.txt is a stub (fine for a test build, not for a release)')
        items.append(('README.txt', None))
    conv = converter_set()
    print('== converters: %d scripts traced from %s' % (len(conv), ', '.join(DLC_ENTRIES)))
    dlc_items = []
    conv += [r for r in DLC_OPTIONAL if r not in conv and os.path.isfile(os.path.join(DLC, *r.split('/')))]
    for rel in conv + list(DLC_EXTRA):
        fn = os.path.join(DLC, *rel.split('/'))
        if not os.path.isfile(fn):
            raise Fail('missing %s' % fn)
        dlc_items.append((rel, fn))
    left_out = sorted(r for r in tool_modules().values() if r not in conv)
    print('   left out (not used by build_all.py / install_dlc.py): %s' % ', '.join(left_out))
    local_only = sorted(r for r, _ in dlc_items if set(r.split('/')[:-1]) & SKIP_DIRS)
    if local_only:   # DLC_EXTRA / DLC_OPTIONAL must not name them either
        raise Fail('local-only tools must never be shipped: %s' % ', '.join(local_only))

    errors, local, machine = [], [], []
    for rel, fn in dlc_items:
        e, l_, m_ = check_tools_file(TOOLS_DIR + '/dlc/' + rel, fn)
        errors += e
        local += l_
        machine += m_
    for rel, fn in items + [(TOOLS_DIR + '/dlc/' + r, f) for r, f in dlc_items]:
        if fn is not None:
            errors += xex_key_hits(rel, fn)
    if errors:
        raise Fail('not shippable:\n  ' + '\n  '.join(errors))
    repo_hits = repo_personal_paths()
    if repo_hits:
        print('WARNING: paths of this PC in files below tools\\ that git would take (shipped or not; %d lines):'
              % len(repo_hits))
        for l_ in repo_hits:
            print('   ' + l_)
    if machine:
        print('NOTE: defaults of manual runs with folders of this PC (build_all.py always passes these paths; %d lines):'
              % len(machine))
        for l_ in machine:
            print('   ' + l_)
    if local:
        print('%s: paths of this PC in shipped files (%d lines):' % ('WARNING' if a.allow_local_paths else 'ERROR', len(local)))
        for l_ in local:
            print('   ' + l_)
        if not a.allow_local_paths:
            raise Fail('paths of this PC in shipped scripts (see above): make them neutral, or --allow-local-paths for '
                       'a test build')

    # ---- the folder
    if os.path.isdir(folder):
        if os.path.normcase(os.path.dirname(folder)) != os.path.normcase(out):
            raise Fail('refusing to remove %s' % folder)
        shutil.rmtree(folder)
    os.makedirs(folder)
    for dst, fn in items:
        p = os.path.join(folder, *dst.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if fn is None:
            with open(p, 'w', encoding='utf-8', newline='\r\n') as f:
                f.write('glimpse_fix %s - Split/Second PC fix (test build)\nhttps://github.com/frankyfife/glimpse_fix\n\n'
                        'Copy everything into the game folder (...\\steamapps\\common\\SplitSecond), glimpse_tools '
                        'included, and run glimpse_config.exe.\nThe README on GitHub has the details.\n' % version)
        else:
            shutil.copy2(fn, p)
    dlc_dir = os.path.join(folder, TOOLS_DIR, 'dlc')
    listed = []
    for rel, fn in dlc_items:
        p = os.path.join(dlc_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy2(fn, p)
        listed.append((rel, os.path.getsize(p), sha256(p)))
    with open(os.path.join(dlc_dir, FILES_LIST), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write('# glimpse_fix %s: the files of this folder (path, size, SHA-256); build_all.py checks them\n' % version)
        for rel, size, sha in listed:
            f.write('%s\t%d\t%s\n' % (rel, size, sha))

    # ---- the zip (paths with /, sorted, top-level files first)
    if os.path.isfile(zip_fn):
        os.remove(zip_fn)
    names = []
    for dp, dn, fns in os.walk(folder):
        dn.sort()
        for n in sorted(fns):
            names.append(os.path.relpath(os.path.join(dp, n), folder).replace(os.sep, '/'))
    names.sort(key=lambda n: (n.count('/'), n.lower()))
    with zipfile.ZipFile(zip_fn + '.part', 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in names:
            z.write(os.path.join(folder, *n.split('/')), n)
    os.replace(zip_fn + '.part', zip_fn)
    print('== %s: %d files, %d bytes' % (zip_fn, len(names), os.path.getsize(zip_fn)))
    for n in names:
        print('   %s' % n)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except Fail as ex:
        print('FAILED: %s' % ex)
        sys.exit(1)
