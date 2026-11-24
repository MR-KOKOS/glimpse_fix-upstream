# Maps every fixed address the fix uses from the supported Steam build to another build of the same
# game, from two decrypted dumps (tools/dump.py), and writes src/build_<name>.inc.
#
#   python -I tools/buildmap.py re/SplitSecond_dump.exe re/SplitSecond_ru_dump.exe src/build_ru.inc
#
# For each address the bytes around it are matched in the other dump: short runs ("anchors") before
# and after the address are searched near it, with every absolute address and rel32 operand masked,
# since those differ between builds. When the anchors on both sides agree on the distance, the
# address moved by that distance. The result is checked: the masked bytes at the address must
# match, pointers stored there must point to the mapped targets, and the expected bytes of every
# byte check in the source must be found at the mapped place. Addresses that fail are listed, an
# override table below takes hand-checked ones.
import os, re, struct, sys

BASE = 0x400000
ADDR = re.compile(r'\bG\(0x0*([0-9A-Fa-f]{6,8})\)')
CHECK = re.compile(r'(?:BytesMatch|Matches)\(\s*([^,]+?),\s*"([0-9A-F]+)"\s*\)')
OFFSET_CHECK = re.compile(r'(k\w+) \+ (0x[0-9a-fA-F]+)')
CALL_CHECK = re.compile(r'Rel32CallTarget\((k\w+) \+ (0x[0-9a-fA-F]+)\)(?: \+ (0x[0-9a-fA-F]+))?')
EXTRA = [0x00401000, 0x00CF5000]  # section bounds used in range checks
# addresses that occur only as operands inside the expected bytes of a byte check. Only these (and
# the G() addresses) are translated there: other byte groups can look like addresses by chance,
# e.g. 0x00CF6CB1 inside "8BB16CCF0000" (mov esi,[ecx+0xCF6C]).
EMBEDDED = [0x00D72320,  # framerate.cpp, movss xmm0,[0xD72320] in the main loop check
            0x00D066DC]  # camera.cpp, mov al,[0xD066DC] at the start of the respot update

# hand-checked: steam address -> other build address (with the reason)
OVERRIDES = {}


def section_table(img):
    pe = struct.unpack_from('<I', img, 0x3C)[0]
    n = struct.unpack_from('<H', img, pe + 6)[0]
    opt = struct.unpack_from('<H', img, pe + 20)[0]
    out = []
    for i in range(n):
        o = pe + 24 + opt + i * 40
        name = img[o:o + 8].rstrip(b'\0').decode()
        vs, va = struct.unpack_from('<II', img, o + 8)
        out.append((name, BASE + va, BASE + va + vs))
    return out


def in_image(v):
    return 0x401000 <= v < 0x2100000


def mask_of(img, lo, hi):
    """True = byte may differ between builds (absolute address, rel32 operand)."""
    b = img[lo - BASE:hi - BASE]
    m = bytearray(len(b))
    for i in range(len(b) - 3):
        if in_image(struct.unpack_from('<I', b, i)[0]):
            m[i:i + 4] = b'\1\1\1\1'
    for i in range(len(b) - 4):
        if b[i] in (0xE8, 0xE9):
            m[i + 1:i + 5] = b'\1\1\1\1'
        if b[i] == 0x0F and 0x80 <= b[i + 1] <= 0x8F and i + 6 <= len(b):
            m[i + 2:i + 6] = b'\1\1\1\1'
    return b, m


class Mapper:
    def __init__(self, a, b):
        self.a, self.b = a, b
        self.cache = {}

    def anchor_deltas(self, addr, span=0x300, step=8, width=16, reach=(-0x800, 0x2800)):
        """(offset from addr, delta) for anchors around addr that are found exactly once nearby."""
        lo, hi = addr - span, addr + span + width
        data, m = mask_of(self.a, lo, hi)
        out = []
        for off in range(0, len(data) - width, step):
            if any(m[off:off + width]):
                continue
            piece = data[off:off + width]
            if len(set(piece)) < 4:  # padding, zero runs
                continue
            at = lo + off
            s_lo, s_hi = at + reach[0], at + reach[1] + width
            hay = self.b[s_lo - BASE:s_hi - BASE]
            i = hay.find(piece)
            if i < 0 or hay.find(piece, i + 1) >= 0:
                continue
            out.append((at - addr, s_lo + i - at))
        return out

    def map(self, addr):
        if addr in OVERRIDES:
            return OVERRIDES[addr], 'override'
        if addr in self.cache:
            return self.cache[addr]
        res = (None, 'no anchors')
        for span in (0x100, 0x300, 0x800):
            ds = self.anchor_deltas(addr, span)
            before = [d for o, d in ds if o < 0]
            after = [d for o, d in ds if o >= 0]
            if not before or not after:
                continue
            # nearest anchors on each side
            nb = max((o, d) for o, d in ds if o < 0)[1]
            na = min((o, d) for o, d in ds if o >= 0)[1]
            if nb == na:
                res = (addr + nb, 'anchors %d/%d' % (len(before), len(after)))
                break
            res = (None, 'anchors disagree (%+d before, %+d after)' % (nb, na))
        self.cache[addr] = res
        return res


def collect(root):
    addrs, checks = set(EXTRA) | set(EMBEDDED), []
    for name in sorted(os.listdir(os.path.join(root, 'src'))):
        if not name.endswith(('.cpp', '.h')):
            continue
        text = open(os.path.join(root, 'src', name), encoding='utf-8').read()
        for m in ADDR.finditer(text):
            addrs.add(int(m.group(1), 16))
        for m in CHECK.finditer(text):
            checks.append((name, m.group(1).strip(), m.group(2)))
        # prologues given as C strings: memcmp(fn, "\x..", n), HookSlot(slot, fn, "\x..", n, ...)
        # and the camera table rows { vtable, update, "\x..", n, name }
        for m in RAWCHECK.finditer(text):
            checks.append((name, m.group(1).strip(), escaped_hex(m.group(2))))
        for m in RAWROW.finditer(text):
            checks.append((name, 'G(0x%s)' % m.group(1), escaped_hex(m.group(2))))
    return sorted(addrs), checks


RAWCHECK = re.compile(r'(?:memcmp\(|HookSlot\([^,]+,)\s*([^,]+?),\s*"((?:\\x[0-9A-Fa-f]{2})+)"')
RAWROW = re.compile(r'\{\s*G\(0x[0-9A-Fa-f]+\),\s*G\(0x0*([0-9A-Fa-f]{6,8})\),\s*"((?:\\x[0-9A-Fa-f]{2})+)"')


def escaped_hex(s):
    return ''.join(s[i + 2:i + 4] for i in range(0, len(s), 4)).upper()


def imports(img):
    """IAT slot -> (dll, function) from the dump's import directory."""
    pe = struct.unpack_from('<I', img, 0x3C)[0]
    rva = struct.unpack_from('<I', img, pe + 24 + 96 + 8)[0]
    out, d = {}, rva
    while True:
        ilt, _, _, name, iat = struct.unpack_from('<IIIII', img, d)
        if not name:
            return out
        dll = img[name:img.index(b'\0', name)].decode()
        k = 0
        while True:
            e = struct.unpack_from('<I', img, (ilt or iat) + k * 4)[0]
            if not e:
                break
            fn = '#%d' % (e & 0xFFFF) if e & 0x80000000 else img[e + 2:img.index(b'\0', e + 2)].decode()
            out[BASE + iat + k * 4] = (dll.lower(), fn)
            k += 1
        d += 20


def main():
    a = open(sys.argv[1], 'rb').read()
    b = open(sys.argv[2], 'rb').read()
    out = sys.argv[3]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mp = Mapper(a, b)
    addrs, checks = collect(root)
    secs_a, secs_b = section_table(a), section_table(b)
    sec_b = {s[0]: s for s in secs_b}
    imp_a, imp_b = imports(a), imports(b)
    slot_b = {v: k for k, v in imp_b.items()}
    table, bad = {}, []
    for x in addrs:
        sec = next((s for s in secs_a if s[1] <= x < s[2]), None)
        name = sec[0] if sec else '?'
        if x in imp_a:
            # import slot: the same function's slot in the other build
            y = slot_b.get(imp_a[x])
            how = 'import %s!%s' % imp_a[x]
            if y is None:
                bad.append((x, name, 'import %s!%s missing' % imp_a[x]))
                continue
        elif sec and x == sec[1] and name in sec_b:
            y, how = sec_b[name][1], 'section start'
        else:
            y, how = mp.map(x)
            if y is None and sec and name in ('.data', 'Sdata') and name in sec_b and \
                    sec[2] - sec[1] == sec_b[name][2] - sec_b[name][1]:
                # run time data without fixed bytes around it: the section is as large in both
                # builds and moved as a whole (every anchored address in it moved by the same)
                y, how = x + sec_b[name][1] - sec[1], 'section moved %+#x (%s)' % (sec_b[name][1] - sec[1], how)
            if y is None:
                bad.append((x, name, how))
                continue
        sec = name
        # check: masked bytes at the address, and a pointer stored there
        da, ma = mask_of(a, x - 8, x + 16)
        da, ma = da[8:16], ma[8:16]
        db = b[y - BASE:y - BASE + 8]
        same = all(ma[i] or da[i] == db[i] for i in range(8))
        word_a = struct.unpack_from('<I', a, x - BASE)[0]
        word_b = struct.unpack_from('<I', b, y - BASE)[0]
        note = ''
        if sec in ('.rdata',) and in_image(word_a) and not (0x401000 <= word_a < 0xB9B000 and False):
            ty, _ = mp.map(word_a)
            if ty is not None and ty != word_b:
                same = False
                note = ' pointer %08X -> %08X, other build holds %08X' % (word_a, ty, word_b)
        # an import slot holds the loaded DLL's address, which changes with every boot: it was matched
        # by DLL and function name above, which is the check
        if not same and sec != '.data' and sec != 'Sdata' and x not in imp_a:
            bad.append((x, sec, 'bytes differ at the mapped place (%08X)%s' % (y, note)))
            continue
        table[x] = y
        print('%08X -> %08X  %+6d  %-7s %s' % (x, y, y - x, sec, how))
    # every byte check, translated as the DLL does, must match at its mapped place
    consts = {}
    for fn in os.listdir(os.path.join(root, 'src')):
        if fn.endswith('.cpp'):
            for m in re.finditer(r'\b(k\w+)\s*=\s*\([^)]*\)\s*G\(0x0*([0-9A-Fa-f]{6,8})\)',
                                 open(os.path.join(root, 'src', fn), encoding='utf-8').read()):
                consts[m.group(1)] = int(m.group(2), 16)
    for name, at, hexs in checks:
        # Follow the same mapped anchor, offsets and direct call as the runtime hook.
        offset = OFFSET_CHECK.fullmatch(at)
        call = CALL_CHECK.fullmatch(at)
        m = re.search(r'0x0*([0-9A-Fa-f]{6,8})', at)
        field = re.match(r'(k\w+)\[0\]\.name$', at)
        named = re.search(r'\b(k\w+)$', at)
        if field and field.group(1) in consts and consts[field.group(1)] in table:
            # a string the table points to: compare it where the other build's table points
            y = table[consts[field.group(1)]]
            ptr = struct.unpack_from('<I', b, y + 4 - BASE)[0]
            got = b[ptr - BASE:ptr - BASE + len(hexs) // 2]
            ok = got == bytes.fromhex(hexs)
            print('check %s %s: %s' % (name, at, 'ok' if ok else 'FAILS ' + got.hex()))
            if not ok:
                bad.append((consts[field.group(1)], 'check', 'byte check fails'))
            continue
        if not m and named and named.group(1) in consts:
            m = re.search(r'([0-9A-Fa-f]+)$', '%08X' % consts[named.group(1)])
        if (offset or call) and (offset or call)[1] in consts:
            # x: the Steam address, reported; y: the mapped one, compared
            anchor = offset or call
            if consts[anchor[1]] not in table:
                continue
            x = consts[anchor[1]] + int(anchor[2], 16)
            y = table[consts[anchor[1]]] + int(anchor[2], 16)
            if call:
                if a[x - BASE] != 0xE8 or b[y - BASE] != 0xE8:
                    bad.append((x, 'check', 'expected rel32 CALL for ' + at))
                    continue
                x += 5 + struct.unpack_from('<i', a, x - BASE + 1)[0] + int(call[3] or '0', 16)
                y += 5 + struct.unpack_from('<i', b, y - BASE + 1)[0] + int(call[3] or '0', 16)
        elif m:
            x = int(m.group(1), 16)
            if x not in table:
                continue
            y = table[x]
        else:
            print('check %s %s: address not resolved' % (name, at))
            bad.append((0, 'check', 'unresolved ' + at))
            continue
        want = bytearray(bytes.fromhex(hexs))
        i = 0
        while i + 4 <= len(want):
            v = struct.unpack_from('<I', want, i)[0]
            if v >= 0x401000 and v in table:
                struct.pack_into('<I', want, i, table[v])
                i += 4
            else:
                i += 1
        got = b[y - BASE:y - BASE + len(want)]
        print('check %s %08X: %s' % (name, x, 'ok' if got == bytes(want) else 'FAILS ' + got.hex()))
        if got != bytes(want):
            bad.append((x, 'check', 'byte check fails'))
    for x, sec, why in bad:
        print('UNMAPPED %08X %-7s %s' % (x, sec, why))
    with open(out, 'w', newline='\n') as f:
        f.write('// generated by tools/buildmap.py - Steam build address, Russian depot build address\n')
        for x in sorted(table):
            # EMBEDDED: no G() call names them; BytesMatch maps them inside checked code bytes (MapEmbedded)
            note = '  // EMBEDDED: in checked code bytes (MapEmbedded), not a G() address' if x in EMBEDDED else ''
            f.write('{ 0x%08X, 0x%08X },%s\n' % (x, table[x], note))
    print('%d mapped, %d left' % (len(table), len(bad)))


if __name__ == '__main__':
    main()
