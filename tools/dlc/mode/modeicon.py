# Deadline's mode icon in the pre-race game mode instructions (UI/InGame/IG_GameModeInstructions.gfx).
# Data only: one frame in the movie's icon sprite and one image in the in-game Flash texture set.
#
# How the PC picks the icon (SplitSecond.exe, static RE, see README.md "Mode icon"):
#   0x804A70 (cInGameModeInstructions) calls the movie's ActionScript
#   SetGameMode(zGameMode, zHelpTextLine1..5, zEventIconName); zEventIconName is the race's mode name
#   (setup +0x988 without ".params", "Multi", "SplitScreen") in lower case ("finalchamprace" -> "champrace").
#   The script does mc_main.mc_eventicons.gotoAndStop(zEventIconName): the icon is the frame of that label
#   (labels match without case). The PC sprite has ChampRace, Elimination, Nemesis, QuickRace,
#   HelicopterSurvival, HelicopterRevenge, TimeTrial; for "timetrialplus" there is no frame, so the sprite
#   stays where it is (frame 1, ChampRace, or the previous race's icon).
# The Xbox DLC ships its own copy of the movie with three frames more (KingOfTheAir, NemesisRace,
# TimeTrialPlus) that show cells of a new 256x256 atlas FE_DLC_event_ICONS (DLC UI/INGAME/TEXTURES/
# INGAME4.TEXTURES). Here the PC movie gets the DLC frame TimeTrialPlus appended to its icon sprite (the
# DLC copy is not used as a whole), and the image is added to the PC's resident in-game set
# UI/InGame/Textures/InGame.textures (+ .filenames), the set FE_event_ICONS is in, under its own name.
#
#   build_gfx(pc_gfx, dlc_gfx) -> (bytes, info)
#   build_tex(pc_tex, pc_fn, dlc_tex4) -> (tex bytes, filenames bytes, info)
#   check_gfx(before, after, dlc_gfx) / check_tex(before_tex, before_fn, tex, fn, dlc_tex4) -> [messages]
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'ui'))
sys.path.insert(0, os.path.join(HERE, '..', 'tex'))
import gfxcars as G  # noqa: E402
import flashtex  # noqa: E402
import convtex  # noqa: E402

P_GFX = 'UI/InGame/IG_GameModeInstructions.gfx'
P_TEX = 'UI/InGame/Textures/InGame.textures'
P_FN = 'UI/InGame/Textures/InGame.filenames'
DLC_GFX = 'UI/INGAME/IG_GAMEMODEINSTRUCTIONS.GFX'      # loose file of the package
DLC_TEX = 'UI/INGAME/TEXTURES/INGAME4.TEXTURES'         # loose file of the package (no .filenames)
LABEL = 'TimeTrialPlus'
IMAGE = 'FE_DLC_event_ICONS'
PC_LABELS = ['ChampRace', 'Elimination', 'Nemesis', 'QuickRace', 'HelicopterSurvival', 'HelicopterRevenge',
             'TimeTrial']
# tags a copied wrapper sprite may hold: End, ShowFrame, PlaceObject2, RemoveObject2, SoundStreamHead2 (the
# exporter writes an empty one into every sprite), DoAction, FrameLabel
SPRITE_TAGS = {0, 1, 26, 28, 45, 12, 43}


class IconError(Exception):
    pass


# what a malformed input can raise in here and in the helpers (gfxcars: ValueError, struct.error, IndexError;
# convtex: FormatError; flashtex: SystemExit). Callers catch these and report them as a failed mode build.
ERRORS = (IconError, ValueError, struct.error, IndexError, KeyError, convtex.FormatError, SystemExit)


def need(c, msg):
    if not c:
        raise IconError(msg)


# root tags the movie may hold (the codes both IG_GameModeInstructions files have, plus ImportAssets 57):
# End, ShowFrame, SetBackgroundColor 9, DoAction 12, ExportAssets 56, ImportAssets 57 / ImportAssets2 71,
# FileAttributes 69, CSMTextSettings 74, PlaceObject2 26, GFx 1000 / 1001 (external image), and the
# definitions below. Anything else is refused: a definition this file does not know could hide a character id.
DEFINE_TAGS = {2, 22, 83, 37, 39, 1001}       # DefineShape 1/2/4, DefineEditText, DefineSprite, GFx image
ROOT_TAGS = {0, 1, 9, 12, 26, 56, 57, 69, 71, 74, 1000} | DEFINE_TAGS
# tags inside sprites: + PlaceObject3 70, RemoveObject2 28, FrameLabel 43, SoundStreamHead2 45, StartSound 15
INNER_TAGS = {0, 1, 12, 15, 26, 28, 43, 45, 70}


def read_tags(b, p, end):
    """gfxcars.read_tags with a bounds check: a tag running past <end> is refused"""
    out = []
    while p < end:
        need(p + 2 <= end, 'tag header at %d runs past the end %d' % (p, end))
        cl = struct.unpack_from('<H', b, p)[0]
        p += 2
        code, ln = cl >> 6, cl & 0x3F
        long_form = ln == 0x3F
        if long_form:
            need(p + 4 <= end, 'tag length at %d runs past the end %d' % (p, end))
            ln = struct.unpack_from('<I', b, p)[0]
            p += 4
        need(p + ln <= end, 'tag %d at %d (%d bytes) runs past the end %d' % (code, p, ln, end))
        out.append([code, bytes(b[p:p + ln]), long_form])
        p += ln
    return out


def parse(d):
    """(start of the tags, [tags]) of an uncompressed GFX; every tag and every sprite's inner tags are bounds
    checked, the tag codes are limited to ROOT_TAGS / INNER_TAGS, each sprite ends with exactly one End"""
    need(d[:3] in (b'GFX', b'FWS'), 'not an uncompressed GFX')
    need(len(d) >= 12 and struct.unpack_from('<I', d, 4)[0] == len(d), 'GFX length field is not the file size')
    p = G.rect_end(d, 8) + 4
    tags = read_tags(d, p, len(d))
    bad = sorted({t[0] for t in tags} - ROOT_TAGS)
    need(not bad, 'GFX root tags not handled: %s' % bad)
    need(tags and tags[-1][0] == 0 and [t[0] for t in tags].count(0) == 1, 'GFX does not end with one End tag')
    for t in tags:
        if t[0] == 39:
            need(len(t[1]) >= 4, 'sprite tag too short')
            inner = read_tags(t[1], 4, len(t[1]))
            bad = sorted({x[0] for x in inner} - INNER_TAGS)
            need(not bad, 'sprite %d: tags not handled: %s' % (struct.unpack_from('<H', t[1], 0)[0], bad))
            need(inner and inner[-1][0] == 0 and [x[0] for x in inner].count(0) == 1,
                 'sprite %d does not end with one End tag' % struct.unpack_from('<H', t[1], 0)[0])
    return p, tags


def imported_ids(tags):
    """{id: name} of ImportAssets (57: url, count, {id, name}) and ImportAssets2 (71: url, 2 reserved bytes,
    count, {id, name})"""
    m = {}
    for t in tags:
        if t[0] in (57, 71):
            b = t[1]
            q = b.index(b'\x00') + 1
            if t[0] == 71:
                q += 2
            n = struct.unpack_from('<H', b, q)[0]
            q += 2
            for _ in range(n):
                cid = struct.unpack_from('<H', b, q)[0]
                e = b.index(b'\x00', q + 2)
                m[cid] = b[q + 2:e].decode('latin-1')
                q = e + 1
            need(q == len(b), 'import tag %d has %d bytes left over' % (t[0], len(b) - q))
    return m


def defs_of(tags):
    """{id: (code, data)} of the definition tags (DEFINE_TAGS; the id is the first u16)"""
    out = {}
    for t in tags:
        if t[0] in DEFINE_TAGS:
            c = struct.unpack_from('<H', t[1], 0)[0]
            need(c not in out, 'character id %d defined twice' % c)
            out[c] = (t[0], t[1])
    return out


def used_ids(tags):
    ids = set(defs_of(tags))
    imp = set(imported_ids(tags))
    need(not ids & imp, 'ids both defined and imported: %s' % sorted(ids & imp))
    return ids | imp


def icon_sprite(tags, label):
    """index of the one sprite with a frame <label> and the PC's mode labels first"""
    hits = []
    for i in G.labelled_sprites(tags, label):
        b = tags[i][1]
        labs = [f[0] for f in G.frames_of(read_tags(b, 4, len(b)))]
        if labs[:len(PC_LABELS)] == PC_LABELS:
            hits.append(i)
    need(len(hits) == 1, '%d icon sprites with a frame %s' % (len(hits), label))
    return hits[0]


def image_info(b):
    """(id, width, height, export name) of a GFx external image tag 1001"""
    cid, fmt, w, h = struct.unpack_from('<4H', b, 0)
    n1 = b[8]
    return cid, w, h, b[9:9 + n1].decode('ascii')


def shape_bitmap_offsets(code, b):
    """[(offset of the bitmap id, id)] of the fill styles of a DefineShape 1-3; refuses shapes that bring
    new styles in their records (their bitmap ids would be missed)"""
    need(code in (2, 22, 32), 'shape tag %d not handled' % code)
    rgb = 4 if code == 32 else 3
    p = G.rect_end(b, 2)

    def count(p):
        n = b[p]; p += 1
        if n == 0xFF and code != 2:
            n = struct.unpack_from('<H', b, p)[0]; p += 2
        return n, p
    n, p = count(p)
    out = []
    for _ in range(n):
        t = b[p]; p += 1
        if t == 0x00:
            p += rgb
        elif t in (0x10, 0x12, 0x13):
            p = G.matrix_end(b, p)
            ng = b[p] & 0x0F
            p += 1 + ng * (1 + rgb) + (2 if t == 0x13 else 0)
        elif t in (0x40, 0x41, 0x42, 0x43):
            out.append((p, struct.unpack_from('<H', b, p)[0]))
            p = G.matrix_end(b, p + 2)
        else:
            raise IconError('shape fill style %#x not handled' % t)
    n, p = count(p)
    p += n * (2 + rgb)
    r = G.Bits(b, p)
    nfill, nline = r.u(4), r.u(4)
    while True:
        if r.u(1) == 0:
            flags = r.u(5)
            if flags == 0:
                break
            need(not flags & 0x10, 'shape brings new styles in its records: not handled')
            if flags & 0x01:
                nb = r.u(5); r.u(2 * nb)
            if flags & 0x02:
                r.u(nfill)
            if flags & 0x04:
                r.u(nfill)
            if flags & 0x08:
                r.u(nline)
        else:
            straight, nb = r.u(1), r.u(4) + 2
            if straight:
                if r.u(1):
                    r.u(2 * nb)
                else:
                    r.u(1); r.u(nb)
            else:
                r.u(4 * nb)
    need(r.end() == len(b), 'shape records end at %d of %d bytes' % (r.end(), len(b)))
    return [x for x in out if x[1] != 0xFFFF]


def build_gfx(pc, dlc):
    """PC IG_GameModeInstructions.gfx + the DLC frame TimeTrialPlus of the icon sprite -> new bytes"""
    pstart, ptags = parse(pc)
    _, xtags = parse(dlc)
    pi, xi = icon_sprite(ptags, 'TimeTrial'), icon_sprite(xtags, LABEL)
    pdefs, xdefs = defs_of(ptags), defs_of(xtags)
    pb, xb = ptags[pi][1], xtags[xi][1]
    psid, pnf = struct.unpack_from('<HH', pb, 0)
    ptt = read_tags(pb, 4, len(pb))
    pfr, xfr = G.frames_of(ptt), G.frames_of(read_tags(xb, 4, len(xb)))
    need(len(pfr) == pnf, 'PC icon sprite: %d frames read, header says %d' % (len(pfr), pnf))
    need([f[0] for f in pfr] == PC_LABELS, 'PC icon sprite labels %s, expected %s' % ([f[0] for f in pfr], PC_LABELS))
    need(LABEL.lower() not in [str(f[0]).lower() for f in pfr], 'the PC icon sprite already has %s' % LABEL)
    pdl, xdl = G.display_lists(pfr), G.display_lists(xfr)
    xlab = {f[0]: i for i, f in enumerate(xfr) if f[0]}
    # DLC char -> PC char over the seven frames both have (same label, same depth, consistent)
    cmap = {}
    for k, l in enumerate(PC_LABELS):
        a, b = pdl[k], xdl[xlab[l]]
        need(sorted(a) == sorted(b), 'frame %s: depths PC %s, DLC %s' % (l, sorted(a), sorted(b)))
        for dpt in a:
            xc, pcc = b[dpt]['char'], a[dpt]['char']
            need(cmap.setdefault(xc, pcc) == pcc, 'frame %s depth %d: DLC char %d maps to two PC chars' % (l, dpt, xc))
            need({k2: v for k2, v in a[dpt].items() if k2 != 'char'} == {k2: v for k2, v in b[dpt].items() if k2 != 'char'},
                 'frame %s depth %d: placement differs between PC and DLC' % (l, dpt))
    used = used_ids(ptags)
    need(max(used) + 8 < 0xFFFF, 'no free character ids')
    nid = [max(used) + 1]
    new_defs, copied, images = [], {}, []

    def new_id():
        nid[0] += 1
        return nid[0] - 1

    def copy(xc):
        if xc in cmap:
            return cmap[xc]
        if xc in copied:
            return copied[xc]
        need(xc in xdefs, 'DLC character %d not defined (imported?)' % xc)
        code, b = xdefs[xc]
        b = bytearray(b)
        if code == 1001:
            cid, w, h, name = image_info(b)
            need(name == IMAGE, 'DLC image %s, expected %s' % (name, IMAGE))
            need(not any(t[0] == 1001 and image_info(t[1])[3].lower() == name.lower() for t in ptags),
                 'the PC movie already has an image %s' % name)
            n = new_id()
            struct.pack_into('<H', b, 0, n)
            new_defs.append([1001, bytes(b), True])
            new_defs.append([56, struct.pack('<HH', 1, n) + name.encode('ascii') + b'\x00', False])
            images.append((name, w, h))
        elif code in (2, 22, 32):
            offs = shape_bitmap_offsets(code, bytes(b))
            need(offs, 'DLC shape %d without a bitmap fill' % xc)
            for off, bid in offs:
                need(xdefs.get(bid, (None,))[0] == 1001, 'DLC shape %d fills with %d, not an external image' % (xc, bid))
                struct.pack_into('<H', b, off, copy(bid))
            n = new_id()
            struct.pack_into('<H', b, 0, n)
            new_defs.append([code, bytes(b), True])
        elif code == 39:
            st = read_tags(bytes(b), 4, len(b))
            need({t[0] for t in st} <= SPRITE_TAGS, 'DLC sprite %d holds tags %s' % (xc, sorted({t[0] for t in st})))
            body = bytearray(b[:4])
            for t in st:
                tb = t[1]
                if t[0] == 26:
                    pl = G.Place(tb)
                    if pl.char is not None:
                        tb = bytearray(tb)
                        struct.pack_into('<H', tb, 3, copy(pl.char))
                        tb = bytes(tb)
                body += G.write_tag(t[0], tb, t[2])
            n = new_id()
            struct.pack_into('<H', body, 0, n)
            b = body
            new_defs.append([39, bytes(b), True])
        else:
            raise IconError('DLC character %d is a tag %d: not copied' % (xc, code))
        copied[xc] = n
        return n

    want = xdl[xlab[LABEL]]
    last = pdl[-1]
    ft = [[28, struct.pack('<H', d), False] for d in sorted(last)]
    ft.append([43, LABEL.encode('utf-8') + b'\x00', False])
    ft += [list(t) for t in xfr[xlab[LABEL]][1] if t[0] == 12]       # the frame's own script (stop)
    need(any(t[0] == 12 for t in ft), 'DLC frame %s has no frame script' % LABEL)
    placed = {}
    for dpt in sorted(want):
        o = dict(want[dpt])
        c = copy(o['char'])
        placed[dpt] = (o['char'], c)
        if o['ratio'] is not None:
            o['ratio'] = len(pfr)   # the exporter's ratio is the frame index
        ft.append([26, G.place_bytes(dpt, o, c), False])
    pfr.append((LABEL, ft, [1, b'', False]))
    body = struct.pack('<HH', psid, len(pfr))
    for lab, tt, show in pfr:
        for t in tt:
            body += G.write_tag(*t)
        body += G.write_tag(*show)
    end = [t for t in ptt if t[0] == 0]
    body += G.write_tag(*(end[0] if end else [0, b'', False]))
    out_tags = list(ptags)
    out_tags[pi] = [39, body, ptags[pi][2]]
    out_tags[pi:pi] = new_defs
    out = b''.join(G.write_tag(*t) for t in out_tags)
    head = bytearray(pc[:pstart])
    struct.pack_into('<I', head, 4, len(head) + len(out))
    info = {'sprite': psid, 'frames': (pnf, len(pfr)), 'copied': copied, 'mapped': {d: v for d, v in placed.items()},
            'images': images, 'defs': len(new_defs), 'inserted_at': pi}
    return bytes(head) + out, info


def check_gfx(before, after, dlc):
    """re-reads the result: every tag of the PC movie unchanged except the icon sprite, which is the PC
    sprite plus one frame; the new frame shows what the DLC frame shows (same image cell and placement)"""
    msgs = []
    _, bt = parse(before)
    _, at = parse(after)
    _, xt = parse(dlc)
    pi = icon_sprite(bt, 'TimeTrial')
    ai = icon_sprite(at, LABEL)
    ndef = ai - pi
    need(ndef >= 1 and at[:pi] == bt[:pi] and at[ai + 1:] == bt[pi + 1:],
         'GFX: tags outside the icon sprite changed')
    msgs.append('IG_GameModeInstructions: %d tags, the PC ones unchanged except the icon sprite, %d definitions '
                'inserted before it' % (len(at), ndef))
    used_ids(at)                       # ids unique (defs_of refuses a second definition) and not imported
    old = used_ids(bt)
    need(imported_ids(at) == imported_ids(bt), 'GFX: imports changed')
    need(all(t[0] in DEFINE_TAGS or t[0] == 56 for t in at[pi:ai]), 'GFX: inserted tags are not definitions/exports')
    new = [struct.unpack_from('<H', t[1], 0)[0] for t in at[pi:ai] if t[0] in DEFINE_TAGS]
    exports = [t for t in at[pi:ai] if t[0] == 56]
    need(all(c not in old for c in new), 'GFX: new definitions reuse PC ids')
    need(len(exports) == len([t for t in at[pi:ai] if t[0] == 1001])
         and all(struct.unpack_from('<HH', t[1], 0)[0] == 1 and struct.unpack_from('<H', t[1], 2)[0] in new
                 for t in exports), 'GFX: image exports')
    msgs.append('IG_GameModeInstructions: character ids unique, the %d new ones (%s) unused by the PC movie'
                % (len(new), ', '.join(map(str, new))))
    ab, bb = at[ai][1], bt[pi][1]
    afr, bfr = G.frames_of(read_tags(ab, 4, len(ab))), G.frames_of(read_tags(bb, 4, len(bb)))
    need(struct.unpack_from('<HH', ab, 0) == (struct.unpack_from('<H', bb, 0)[0], len(bfr) + 1)
         and afr[:-1] == bfr and afr[-1][0] == LABEL,
         'GFX: icon sprite is not the PC sprite plus one frame %s' % LABEL)
    msgs.append('IG_GameModeInstructions: icon sprite %d = the PC sprite\'s %d frames unchanged + frame %d %s'
                % (struct.unpack_from('<H', ab, 0)[0], len(bfr), len(afr), LABEL))
    # what the new frame shows, resolved down to the image, against the DLC frame
    adefs, xdefs = defs_of(at), defs_of(xt)

    def resolve(defs, c, m=None):
        """[(image export name, w, h, shape bitmap matrix)] a character shows (sprite -> shapes -> images)"""
        code, b = defs[c]
        if code == 39:
            out = []
            for f in G.display_lists(G.frames_of(read_tags(b, 4, len(b))))[:1]:
                for d in sorted(f):
                    out += [(d, f[d]['matrix'])] + resolve(defs, f[d]['char'])
            return out
        if code in (2, 22, 32):
            out = []
            for bid, mm in G._shape_bitmaps(code, b):
                _, w, h, name = image_info(defs[bid][1])
                out.append(((name, w, h), mm))
            return out
        return [('tag %d' % code,)]
    adl = G.display_lists(afr)[-1]
    xi = icon_sprite(xt, LABEL)
    xb = xt[xi][1]
    xfr = G.frames_of(read_tags(xb, 4, len(xb)))
    xdl = G.display_lists(xfr)[[f[0] for f in xfr].index(LABEL)]
    need(sorted(adl) == sorted(xdl), 'GFX: depths of the new frame %s, DLC %s' % (sorted(adl), sorted(xdl)))
    for d in sorted(adl):
        a, x = adl[d], xdl[d]
        same = {k: a[k] for k in a if k not in ('char', 'ratio')} == {k: x[k] for k in x if k not in ('char', 'ratio')}
        ra, rx = resolve(adefs, a['char']), resolve(xdefs, x['char'])
        need(same and ra == rx, 'GFX: depth %d of the new frame does not show what the DLC frame shows' % d)
        msgs.append('IG_GameModeInstructions frame %s depth %d: same placement as the DLC frame (matrix, clip%s), '
                    'shows %s' % (LABEL, d, ', name' if a['name'] else '',
                                  ', '.join('%s %dx%d' % (r[0][0], r[0][1], r[0][2]) for r in ra if len(r) == 2 and isinstance(r[0], tuple)) or 'the PC mask shape'))
    # the frame script is a stop, as in every other frame
    acts = [t for t in afr[-1][1] if t[0] == 12]
    need(len(acts) == 1 and acts[0][1] == b'\x07\x00', 'GFX: new frame script is not "stop"')
    msgs.append('IG_GameModeInstructions frame %s: script stop (as the other frames); the exe asks for the label '
                '"timetrialplus" (labels match without case)' % LABEL)
    return msgs


# ---- texture: FE_DLC_event_ICONS into the PC in-game Flash set, as flashtex.py adds images ----

def dds_fields_of_format(ents, blobs, fmt):
    """(DDS flags, mip count, caps) the set's images of format <fmt> share; refused if none or not uniform"""
    vals = {tuple(struct.unpack_from('<I', bl, 4 + off)[0] for off in (8, 28, 108))
            for en, bl in zip(ents, blobs) if struct.unpack_from('<I', en, 12)[0] == fmt}
    need(len(vals) == 1, 'InGame.textures: images of format %#x have DDS header fields %s, not one value'
         % (fmt, sorted(vals)))
    return vals.pop()


def build_tex(pc_tex, pc_fn, dlc_tex):
    """flashtex.main's insertion done in memory: the DLC image under its own name in the PC set"""
    pc = convtex.parse(pc_tex)
    need(pc['E'] == '<', 'InGame.textures: not a PC container')
    ents, blobs, nm = list(pc['entries']), list(pc['blobs']), flashtex.names(pc)
    need(flashtex.build(pc, ents, blobs, nm) == pc_tex, 'InGame.textures does not rebuild byte for byte')
    rule = flashtex.order_rule(nm)
    need(rule is not None, 'InGame.textures: names not in order')
    sk = flashtex.name_key(rule)
    fmts = {struct.unpack_from('<I', e, 12)[0] for e in ents}
    dxt5 = 0x25 in fmts and 0x24 not in fmts
    xs = flashtex.xbox_set(dlc_tex)
    hit = [x for x in xs if x[1].rsplit('_', 1)[0] == IMAGE]
    need(len(hit) == 1, '%s: %d textures of that name in the DLC set' % (IMAGE, len(hit)))
    k, s, xe, xb = hit[0]
    e, blob = bytearray(xe), bytearray(xb)
    if dxt5 and struct.unpack_from('<I', e, 12)[0] == 0x24 and blob[4 + 84:4 + 88] == b'DXT4':
        struct.pack_into('<I', e, 12, 0x25)
        blob[4 + 84:4 + 88] = b'DXT5'
    fmt = struct.unpack_from('<I', e, 12)[0]
    need(fmt in flashtex.SUFFIX, '%s: format %#x not handled' % (s, fmt))
    ns = IMAGE + flashtex.SUFFIX[fmt]
    fl = [x for x in pc_fn.split(b'\x00') if x]
    names = {x for _, x in nm}
    need(ns not in names and IMAGE.encode() not in fl
         and not any(flashtex.registered(x).lower() == IMAGE.lower() for x in names), '%s is already in the PC set' % IMAGE)
    keys = {kk for kk, _ in nm}
    while k in keys:
        k = (k * 31 + 1) & 0xFFFFFFFF
    struct.pack_into('<I', e, 4, k)
    # one level; the DDS header's flags, mip count and caps as the set's own images of that format have them
    # (the PC InGame set: 0x1007 / 1 / 0x1000 for every image; flashtex.py writes the Flash set's mip count 0)
    need(struct.unpack_from('<I', e, 20)[0] == 1, '%s: %d levels, expected 1' % (s, struct.unpack_from('<I', e, 20)[0]))
    hdr = dds_fields_of_format(ents, blobs, fmt)
    for off, v in zip((8, 28, 108), hdr):
        struct.pack_into('<I', blob, 4 + off, v)
    at = len(nm)
    for j, (_, s2) in enumerate(nm):
        if sk(s2) > sk(ns):
            at = j
            break
    ents.insert(at, bytes(e))
    blobs.insert(at, bytes(blob))
    nm.insert(at, (k, ns))
    fl.append(IMAGE.encode())
    tex = flashtex.build(pc, ents, blobs, nm)
    fn = b'\x00'.join(fl) + b'\x00' if pc_fn.endswith(b'\x00') else b'\x00'.join(fl)
    w, h = struct.unpack_from('<2I', e, 24)
    return tex, fn, {'name': ns, 'key': k, 'format': fmt, 'size': (w, h), 'bytes': len(blob), 'index': at}


def check_tex(btex, bfn, tex, fn, dlc_tex, gfx_images):
    msgs = []
    b, a = convtex.parse(btex), convtex.parse(tex)
    bn, an = flashtex.names(b), flashtex.names(a)
    new = [x for x in an if x not in bn]
    need(len(an) == len(bn) + 1 and len(new) == 1 and [x for x in an if x != new[0]] == bn,
         'InGame.textures: not the input set plus one image')
    i = an.index(new[0])
    others = [j for j in range(len(an)) if j != i]
    need([a['entries'][j] for j in others] == list(b['entries']) and [a['blobs'][j] for j in others] == list(b['blobs']),
         'InGame.textures: an existing image changed')
    need(flashtex.build(a, list(a['entries']), list(a['blobs']), an) == tex, 'InGame.textures: does not rebuild')
    rule = flashtex.order_rule(an)
    need(rule == flashtex.order_rule(bn) and rule is not None, 'InGame.textures: name order broken')
    k, ns = new[0]
    need(flashtex.registered(ns) == IMAGE and len({x[0] for x in an}) == len(an), 'InGame.textures: name or key')
    e = a['entries'][i]
    fmt = struct.unpack_from('<I', e, 12)[0]
    w, h = struct.unpack_from('<2I', e, 24)
    need((IMAGE, w, h) in gfx_images, 'InGame.textures: %s %dx%d is not the size the GFX names %s' % (ns, w, h, gfx_images))
    dds = a['blobs'][i][4:]
    need(dds[:4] == b'DDS ' and struct.unpack_from('<I', a['blobs'][i], 0)[0] == len(dds)
         and struct.unpack_from('<2I', dds, 12) == (h, w), 'InGame.textures: DDS header')
    # pixels against the Xbox texture converted on its own
    x = [t for t in flashtex.xbox_set(dlc_tex) if t[1].rsplit('_', 1)[0] == IMAGE][0]
    need(dds[128:] == x[3][4 + 128:] and len(dds) == len(x[3]) - 4, 'InGame.textures: pixels differ from the DLC image')
    same_fmt = [s for j, (kk, s) in enumerate(bn) if struct.unpack_from('<I', b['entries'][j], 12)[0] == fmt]
    hdr = tuple(struct.unpack_from('<I', dds, off)[0] for off in (8, 28, 108))
    need(struct.unpack_from('<I', e, 20)[0] == 1 and hdr == dds_fields_of_format(b['entries'], b['blobs'], fmt),
         'InGame.textures: levels / DDS flags, mip count, caps differ from the set\'s images of format %#x' % fmt)
    msgs.append('InGame.textures: the input set (%d images, as the pack or PC resident.ark has it) + %s at %d, key %08x, '
                'format %#x (%d other images of the set have it), %dx%d, 1 level, DDS flags %#x / mip count %d / caps '
                '%#x as those images, pixels = the DLC image converted, every other image byte-identical, name order kept'
                % (len(bn), ns, i, k, fmt, len(same_fmt), w, h, hdr[0], hdr[1], hdr[2]))
    fb, fa = [s for s in bfn.split(b'\0') if s], [s for s in fn.split(b'\0') if s]
    need(fa == fb + [IMAGE.encode()] and fn.endswith(b'\0') == bfn.endswith(b'\0'), 'InGame.filenames')
    msgs.append('InGame.filenames: the input list + %s (%d names)' % (IMAGE, len(fa)))
    return msgs


def present(get):
    """the icon is still in a final set of files (get(path) -> bytes or None): the movie's icon sprite has the
    frame, its image tag names IMAGE, the in-game set and its list have the image. Guards against a later step
    that rebuilds InGame.textures/.filenames from the PC copy (finish_car)."""
    g, t, f = get(P_GFX), get(P_TEX), get(P_FN)
    need(g is not None and t is not None and f is not None, 'icon files missing')
    _, tags = parse(g)
    icon_sprite(tags, LABEL)
    imgs = [image_info(d[1]) for d in defs_of(tags).values() if d[0] == 1001]
    gi = [(n, w, h) for _, w, h, n in imgs if n == IMAGE]
    need(len(gi) == 1, 'the movie has %d image tags %s' % (len(gi), IMAGE))
    c = convtex.parse(t)
    hit = [i for i, (_, s) in enumerate(flashtex.names(c)) if flashtex.registered(s) == IMAGE]
    need(len(hit) == 1, 'InGame.textures has %d images %s' % (len(hit), IMAGE))
    w, h = struct.unpack_from('<2I', c['entries'][hit[0]], 24)
    need((IMAGE, w, h) == gi[0], 'InGame.textures %s is %dx%d, the movie names %s' % (IMAGE, w, h, gi[0]))
    need(IMAGE.encode() in [x for x in f.split(b'\0') if x], 'InGame.filenames does not list %s' % IMAGE)
    return ['final files: IG_GameModeInstructions.gfx has frame %s showing %s %dx%d; InGame.textures and '
            'InGame.filenames have it (nothing later dropped it)' % (LABEL, IMAGE, w, h)]
