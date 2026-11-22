# The official texts of Title Update 4 (TU4) for the DLC content, for the PC dictionaries. Offline only.
#
# TU4's default.xex (SPLITSECOND1, title update 4) carries a built-in text table that the DLC content uses next to
# the game's dictionaries: 75 records of 0x20 bytes in .data (patched image 0x88EC80D8..0x88EC8A38):
#   +0x00 u32 hash of the key (filled at start-up), +0x04 language 0 (always an empty string),
#   +0x08 English, +0x0C French, +0x10 Italian, +0x14 German, +0x18 Spanish, +0x1C Russian  (big-endian pointers)
# The strings are UTF-8 with a NUL, in .rdata around 0x88047000..0x8804D000 (the linker packs them into gaps, so
# a record's strings are not next to each other and the key is not stored in the record).
# The keys are only in a static initializer (0x88E65330..0x88E66610, no .pdata entry): for each record it hashes
# one key string (lbz/lbzu over the string, CRC-32 table 0x8811DF70, init 0xFFFFFFFF, no final xor) and stores the
# hash with "stw r10/r9, <0x20 * record>(r5)", r5 = the table. The lookup 0x883CD440 hashes the asked key the same
# way, scans the 75 records (bound "addi r9, r11, 0x960") and returns the pointer at +4 * (language + 1)
# (language from 0x884B3A80).
#
# The texts are game data: they are never put into this repository or into a release. The official texts need a
# TU4 text table that the release cannot produce: the patched SPLITSECOND1 image it is read from is made with a local
# development tool that is not part of glimpse_fix. From that image a JSON cache is made once:
#   python -I -X utf8 tu4text.py extract <SPLITSECOND1_tu4.img> <out.json>
# and GLIMPSE_TU4_TEXTS points at it (or the path is passed). Without it the converters write their own texts. The consumers (assemble/cartext.py, deadline_mode.py)
# use the official text when the table is there and fall back to their own generated texts otherwise, and say
# which one they used.
# The cache also holds Deadline's rival names ("rivals": RaceSetupOverlay/TimeTrialPlus/<Rival>Time key -> the name
# the results show), found in the code (_find_rivals: the results builder 0x882E7C58 passes each name with the time
# from its tweakable; the tweakable's registration names the key). deadline_mode.py writes them into the mode pack
# for the DLL (glimpse/deadline/RivalNames.txt); a table made before has none (the DLL then makes them from the keys).
#   python -I -X utf8 tu4text.py list [<json>]         keys grouped by kind (default: GLIMPSE_TU4_TEXTS)
#   python -I -X utf8 tu4text.py show <key> [<json>]   the key's texts
import json
import os
import re
import struct
import sys
import zlib

sys.dont_write_bytecode = True

ENV = 'GLIMPSE_TU4_TEXTS'
FORMAT = 1
# record field order (+0x08 ..+0x1C) and the PC dictionary of each language. The PC exe knows a RUSSIAN dictionary
# (language names 0xC4A7E4.., game_setup/ForceRussian) but the PC resident.ark here has none, so the consumers
# only use the five dictionaries they already write; RU stays in the JSON.
XB_LANGS = ('EN', 'FR', 'IT', 'DE', 'ES', 'RU')
PC_LANG = {'ENGLISH_NTSC': 'EN', 'FRENCH_NTSC': 'FR', 'ITALIAN': 'IT', 'GERMAN': 'DE', 'SPANISH_NTSC': 'ES',
           'RUSSIAN': 'RU'}
REC = 0x20


class TU4Error(Exception):
    pass


# ------------------------------------------------------------------------------------------------ the cache
class Table:
    def __init__(self, data, path):
        if data.get('format') != FORMAT or not isinstance(data.get('texts'), dict):
            raise TU4Error('%s: not a tu4text table (format %r)' % (path, data.get('format')))
        self.path = path
        self.texts = data['texts']          # key -> {EN: .., FR: .., ...}
        self.rivals = data.get('rivals') or {}  # Deadline's rival names: <Rival>Time key -> name (tables made since)
        self.source = data.get('source', {})

    def get(self, key, pc_lang):
        """the official text of key for a PC dictionary language (ENGLISH_NTSC, GERMAN, ...), or None"""
        t = self.texts.get(key)
        return None if t is None else t.get(PC_LANG[pc_lang])

    def has(self, key, pc_langs):
        return all(self.get(key, l) for l in pc_langs)

    def describe(self):
        return '%s (%d keys, from %s)' % (self.path, len(self.texts), self.source.get('image', '?'))


_cache = {}


def load(path=None, log=None):
    """the table from path, else from %GLIMPSE_TU4_TEXTS%; None when neither is set. A set but unreadable path
    is an error (a build must not silently fall back when the developer asked for the official texts)."""
    path = path or os.environ.get(ENV)
    if not path:
        return None
    path = os.path.abspath(path)
    if path not in _cache:
        try:
            with open(path, encoding='utf-8') as f:
                _cache[path] = Table(json.load(f), path)
        except (OSError, ValueError) as e:
            raise TU4Error('%s (%s): %s' % (path, ENV, e))
        if log:
            log('TU4 texts: %s' % _cache[path].describe())
    return _cache[path]


def missing_letters(dict_texts, new_texts):
    """the non-ASCII letters of new_texts that no text of the PC dictionary uses (dict_texts: iterable of str);
    the PC fonts have the letters the PC texts use (the rule deadline_mode.check applies)"""
    have = set(''.join(dict_texts))
    return sorted({c for t in new_texts for c in t if ord(c) > 126 and c not in have})


# ------------------------------------------------------------------------------- development: the extractor
def _sections(img):
    e = struct.unpack_from('<I', img, 0x3C)[0]
    if img[e:e + 4] != b'PE\0\0':
        raise TU4Error('not a PE image (give the patched SPLITSECOND1 image of title update 4)')
    base = struct.unpack_from('<I', img, e + 24 + 28)[0]
    n, osz = struct.unpack_from('<H', img, e + 6)[0], struct.unpack_from('<H', img, e + 20)[0]
    sec = {}
    for i in range(n):
        s = e + 24 + osz + 40 * i
        vs, va = struct.unpack_from('<II', img, s + 8)
        sec[img[s:s + 8].rstrip(b'\0').decode('ascii', 'replace')] = (va, vs)
    return base, sec


def _cstr(img, base, a, limit=1024):
    o = a - base
    if not 0 <= o < len(img):
        raise TU4Error('pointer %08x outside the image' % a)
    e = img.find(b'\0', o, o + limit)
    if e < 0:
        raise TU4Error('no NUL after %08x' % a)
    return img[o:e]


def _find_init(img, base, text):
    """the static initializer: lis r11 / lis r9 / addi r11,r11 / addi r6,r9 / li r4,-1"""
    va, vs = text
    words = struct.unpack_from('>%dI' % (vs // 4), img, va)
    hits = []
    for k in range(len(words) - 5):
        w = words[k:k + 5]
        if (w[0] >> 16) == 0x3D60 and (w[1] >> 16) == 0x3D20 and (w[2] >> 16) == 0x396B and (w[3] >> 16) == 0x38C9 \
                and w[4] == 0x3880FFFF:
            hits.append(base + va + 4 * k)
    return hits


def _run_init(img, base, start):
    """emulates the initializer far enough for the key addresses (lbz rX, d(r11)), the hash stores
    (stw rS, d(r5): the i-th store is the hash of the i-th key) and the CRC table (r6); stops at blr"""
    reg = {}
    keys, stores = [], []
    a = start
    for _ in range(0x4000):
        w = struct.unpack_from('>I', img, a - base)[0]
        op, rd, ra, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if w == 0x4E800020:                       # blr
            return keys, stores, reg.get(6), a
        if op in (14, 15):                        # addi / li, addis / lis
            src = 0 if ra == 0 else reg.get(ra)
            add = simm if op == 14 else imm << 16
            reg[rd] = None if src is None else (src + add) & 0xFFFFFFFF
        elif op == 34:                            # lbz
            if ra == 11 and reg.get(11) is not None:
                keys.append((reg[11] + simm) & 0xFFFFFFFF)
            reg[rd] = None
        elif op == 35:                            # lbzu
            if ra == 11:
                reg[11] = None
            reg[rd] = None
            reg[ra] = None
        elif op == 36:                            # stw
            if ra == 5:                           # the hash of the previous key (r10 or r9)
                if reg.get(5) is None:
                    raise TU4Error('stw at %08x before r5 is known' % a)
                stores.append((reg[5] + simm) & 0xFFFFFFFF)
        elif op == 31:                            # X-form: lwzx (rD), xor/or/mr (rA), others: both
            xo = (w >> 1) & 0x3FF
            if xo == 444 and rd == ((w >> 11) & 31):   # mr rA, rS
                reg[ra] = reg.get(rd)
            elif xo == 23:
                reg[rd] = None
            else:
                reg[ra] = None
                if xo not in (316, 444):
                    reg[rd] = None
        elif op in (21, 24, 28):                  # rlwinm / ori / andi.: rA
            reg[ra] = None
        elif op in (10, 11, 16, 18):              # compares, branches inside the loops
            pass
        else:
            raise TU4Error('initializer: unexpected instruction %08x at %08x' % (w, a))
        a += 4
    raise TU4Error('initializer: no blr')


def _find_lookup_bound(img, base, text, table):
    """the lookup's scan bound: lis r11,hi / addi r11,r11,lo (the table) ... addi r9,r11,<bound>"""
    va, vs = text
    hi, lo = (table + 0x8000) >> 16, table & 0xFFFF
    words = struct.unpack_from('>%dI' % (vs // 4), img, va)
    out = set()
    for k in range(len(words) - 8):
        if words[k] == (0x3D600000 | hi) and words[k + 1] == (0x396B0000 | lo):
            for j in range(k + 2, k + 10):
                if (words[j] >> 16) == 0x392B:
                    out.add(words[j] & 0xFFFF)
                    break
    return out


def _refs(words):
    """a linear walk over the code with the values of lis/addi/ori/mr (reset at each mflr r12 and blr): for every
    addi with a known base and every lfs from a known base, {index: (op, rd, address)}"""
    out = {}
    reg = {}
    for k, w in enumerate(words):
        op, rd, ra, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        if w == 0x7D8802A6 or w == 0x4E800020:      # mflr r12 (prologue) / blr
            reg = {}
        elif op == 15:                              # lis / addis
            src = 0 if ra == 0 else reg.get(ra)
            reg[rd] = None if src is None else (src + (imm << 16)) & 0xFFFFFFFF
        elif op == 14:                              # li / addi
            src = 0 if ra == 0 else reg.get(ra)
            v = None if src is None else (src + simm) & 0xFFFFFFFF
            if ra and v is not None:
                out[k] = (14, rd, v)
            reg[rd] = v
        elif op == 24:                              # ori rA, rS
            reg[ra] = None if reg.get(rd) is None else reg[rd] | imm
        elif op == 48:                              # lfs fD, d(rA)
            if ra and reg.get(ra) is not None:
                out[k] = (48, rd, (reg[ra] + simm) & 0xFFFFFFFF)
        elif op == 31:
            xo = (w >> 1) & 0x3FF
            rb = (w >> 11) & 31
            if xo == 444 and rd == rb:              # mr rA, rS
                reg[ra] = reg.get(rd)
            elif xo in (444, 28, 60, 124, 284, 316, 412, 476, 24, 536, 792, 824, 26, 954, 922):  # logical/shift: rA
                reg[ra] = None
            else:
                reg[rd] = None
        elif 32 <= op <= 35 or 40 <= op <= 43 or op in (58, 7, 8, 12, 13):  # loads, mulli, subfic, addic: rD
            reg[rd] = None
        elif op in (20, 21, 23, 25, 26, 27, 28, 29, 30):  # rlwimi/rlwinm/rlwnm/oris/xori/xoris/andi./andis./rld*: rA
            reg[ra] = None
        elif op == 18 and (w & 1):                  # bl: the volatile registers
            for r in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                reg.pop(r, None)
    return out


def _find_rivals(img, base, text):
    """TU4's Deadline rival names, structurally: the results builder (0x882E7C58) makes 7 calls of one function
    (r4 = the name, r5 = 1, r6 = 0) each after loading the track's countdown and one rival time (lfs); the rival
    time's tweakable is registered with its key name (lis/addi of the name near lis/addi of the address).
    -> [(key, name)] in call order, or [] when not found exactly once"""
    va, vs = text
    words = struct.unpack_from('>%dI' % (vs // 4), img, va)
    refs = _refs(words)
    calls = {}
    for k, w in enumerate(words):
        if (w >> 26) != 18 or (w & 3) != 1:
            continue
        li = w & 0x03FFFFFC
        tgt = (base + va + 4 * k + (li - 0x04000000 if li & 0x02000000 else li)) & 0xFFFFFFFF
        win = words[max(0, k - 16):k]
        if 0x38A00001 not in win or 0x38C00000 not in win:
            continue
        pr = [refs[j] for j in range(max(0, k - 16), k) if j in refs]
        names = [a for op, rd, a in pr if op == 14 and rd == 4]
        floats = [a for op, rd, a in pr if op == 48]
        if not names:
            continue
        try:
            nm = _cstr(img, base, names[-1], 32)
        except TU4Error:
            continue
        if re.fullmatch(rb'[A-Z][A-Z ]{1,23}', nm) and len(floats) >= 2:
            calls.setdefault(tgt, []).append((base + va + 4 * k, nm.decode('ascii'), floats[-3:]))
    groups = []
    for tgt, cs in calls.items():
        run = [cs[0]]
        for c in cs[1:]:
            if c[0] - run[-1][0] <= 0x100:
                run.append(c)
            else:
                if len(run) == 7:
                    groups.append(run)
                run = [c]
        if len(run) == 7:
            groups.append(run)
    if len(groups) != 1:
        return []
    run = groups[0]
    common = set(run[0][2])
    for c in run[1:]:
        common &= set(c[2])
    if len(common) != 1:
        return []
    # the rival time of a call: of the other floats it loads, the one next to the countdown (the tweakables lie
    # together; the first call also loads the factor 100.0 from elsewhere)
    cd = next(iter(common))
    rival_addr = []
    for c in run:
        other = sorted((abs(a - cd), a) for a in c[2] if a not in common)
        if not other or other[0][0] > 0x100:
            return []
        rival_addr.append([other[0][1]])
    if len({a[0] for a in rival_addr}) != 7:
        return []
    # the registrations: the address formed (addi) near the tweakable's path "RaceSetupOverlay/TimeTrialPlus/<Name>Time"
    # (addi), within 12 instructions
    want = {a[0] for a in rival_addr}
    keys = {}
    idx = sorted(refs)
    pos = {k: i for i, k in enumerate(idx)}
    for k in idx:
        op, rd, a = refs[k]
        if op != 14 or a not in want:
            continue
        i = pos[k]
        for j in idx[max(0, i - 8):i + 8]:
            if abs(j - k) > 12 or refs[j][0] != 14 or refs[j][2] in want:
                continue
            try:
                st = _cstr(img, base, refs[j][2], 96)
            except TU4Error:
                continue
            m = re.fullmatch(rb'RaceSetupOverlay/TimeTrialPlus/([A-Z][A-Za-z]+Time)', st)
            if m:
                keys.setdefault(a, set()).add(m.group(1).decode('ascii'))
    out = []
    for c, a in zip(run, rival_addr):
        ks = keys.get(a[0], set())
        if len(ks) != 1:
            return []
        out.append((ks.pop(), c[1]))
    if len({k for k, n in out}) != 7:
        return []
    return out


def extract(img_path):
    img = open(img_path, 'rb').read()
    base, sec = _sections(img)
    if '.text' not in sec or '.data' not in sec:
        raise TU4Error('no .text/.data section')
    inits = _find_init(img, base, sec['.text'])
    found = []
    for start in inits:
        keys, stores, crc_tab, end = _run_init(img, base, start)
        names = []
        try:
            names = [_cstr(img, base, k, 128) for k in keys]
        except TU4Error:
            continue
        if keys and len(keys) == len(stores) and all(re.fullmatch(rb'TEXT_[A-Za-z0-9_]+', n) for n in names):
            found.append((start, end, keys, names, stores, crc_tab))
    if len(found) != 1:
        raise TU4Error('expected one key initializer, found %d (candidates %s)' % (
            len(found), ', '.join('%08x' % s for s in inits)))
    start, end, keys, names, stores, crc_tab = found[0]
    # the CRC table is the standard (zlib) one: the hash of a key is crc32 with init -1 and no final xor
    tab = struct.unpack_from('>256I', img, crc_tab - base)
    if tab[1] != 0x77073096 or tab[255] != 0x2D02EF8D:
        raise TU4Error('CRC table at %08x is not the standard one' % crc_tab)
    table = min(stores)
    if sorted(stores) != [table + REC * i for i in range(len(stores))]:
        raise TU4Error('hash stores are not one record of 0x20 each')
    bound = _find_lookup_bound(img, base, sec['.text'], table)
    if bound != {REC * len(stores)}:
        raise TU4Error('lookup bound %s, initializer has %d records' % (sorted(hex(b) for b in bound), len(stores)))
    texts, info = {}, []
    for k, name, slot in zip(keys, names, stores):
        key = name.decode('ascii')
        if key in texts:
            raise TU4Error('key %s twice' % key)
        ptrs = struct.unpack_from('>7I', img, slot + 4 - base)
        if _cstr(img, base, ptrs[0]) != b'':
            raise TU4Error('%s: language 0 is not empty' % key)
        rec = {}
        for lang, p in zip(XB_LANGS, ptrs[1:]):
            rec[lang] = _cstr(img, base, p).decode('utf-8')     # raises on a non-UTF-8 string
        texts[key] = rec
        info.append((key, slot))
    h = __import__('hashlib').sha1(img).hexdigest()
    rivals = _find_rivals(img, base, sec['.text'])
    return {'format': FORMAT,
            'rivals': {k: n for k, n in rivals},
            'source': {'image': os.path.basename(img_path), 'sha1': h, 'image_base': '%08x' % base,
                       'initializer': '%08x..%08x' % (start, end), 'table': '%08x' % table,
                       'records': len(texts), 'crc_table': '%08x' % crc_tab,
                       'hash': 'crc32 (zlib table), init 0xFFFFFFFF, no final xor',
                       'languages': 'record +8.. = ' + ', '.join(XB_LANGS)},
            'texts': texts}


def key_hash(key):
    """the TU4 hash of a key (crc32 without the final xor)"""
    return zlib.crc32(key.encode('ascii')) ^ 0xFFFFFFFF


GROUPS = (('car descriptions', r'TEXT_CARDESCR_Placeholder_\d+'), ('car names', r'TEXT_PLACEHOLDER_\d+'),
          ('mode names', r'TEXT_GAMEMODE_NAME_.*'), ('mode help lines', r'TEXT_GAMEMODE_.*_HELP_LINE\d'),
          ('Deadline (TimeTrialPlus)', r'TEXT_TIMETRIALPLUS_.*'), ('Quarry', r'TEXT_Quarry_.*'),
          ('Quarry Nemesis', r'TEXT_nem_quarry_.*'), ('loading tips', r'TEXT_LOADING_TIPS_\d+'))


def groups(keys):
    out, left = [], list(keys)
    for title, pat in GROUPS:
        g = [k for k in left if re.fullmatch(pat, k)]
        left = [k for k in left if k not in g]
        out.append((title, g))
    out.append(('other', left))
    return out


def main(argv):
    if len(argv) >= 3 and argv[0] == 'extract':
        out = os.path.abspath(argv[2])
        if os.path.abspath(argv[1]) == out:
            sys.exit('the output would replace the image')
        data = extract(argv[1])
        with open(out, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        print('%s: %d records (%s), written %s' % (argv[1], len(data['texts']), data['source']['table'], out))
        print('  %-26s %s' % ('Deadline rival names', ', '.join('%s = %s' % kv for kv in sorted(data['rivals'].items()))
                                                        or 'not found'))
        for title, g in groups(sorted(data['texts'])):
            print('  %-26s %d' % (title, len(g)))
        return 0
    if argv and argv[0] in ('list', 'show'):
        rest = argv[1:]
        k = rest.pop(0) if argv[0] == 'show' and rest else None
        t = load(rest[0] if rest else None)
        if t is None:
            sys.exit('no table: give the JSON or set %s' % ENV)
        if argv[0] == 'show':
            for l in XB_LANGS:
                print('%s: %s' % (l, t.texts.get(k, {}).get(l)))
            return 0
        print(t.describe())
        for title, g in groups(sorted(t.texts)):
            print('%s (%d)' % (title, len(g)))
            for kk in g:
                print('  ' + kk)
        return 0
    sys.exit('usage: tu4text.py extract <patched image> <out.json> | list [<json>] | show <key> [<json>]')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
