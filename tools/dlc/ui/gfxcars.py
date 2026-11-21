# Adds DLC cars to the PC's car select Flash files: UI/Frontend/Flash/FE_vehicleSelect.gfx (front end) and
# UI/InGame/VehicleSelect/VehicleSelect.gfx (in-game car select of the career's in-game event select).
#
# The car select shows a car's name and badge by going to the frame labelled with the car's record ID in two
# sprites (the badge sprite and the name sprite; the in-game file has one picture sprite, the name comes from the
# exe). The PC's file only has the base cars; the Xbox DLC ships its own copy of the file with frames for its cars
# (labelled Placeholder_NN), but that copy is an older layout of the screen (no mc_VSpip stat pips the PC exe
# drives), so it is not used as a whole. Instead, for each car, one frame is appended to every PC car sprite under
# the PC record ID, showing what the DLC file shows on its frame:
#   - the display list of the DLC frame is worked out (frames only store changes) and placed in full on a
#     new frame after every depth of the PC sprite's last frame is removed;
#   - characters the PC file already has (badges, logo shapes, clip masks) are mapped by comparing the frames
#     both files share; a bitmap-filled shape is only mapped to one that shows the same images (name and size),
#     so a DLC atlas that is a different image under a PC atlas's name (in-game FE_carSelect_set3: 1024x1024 on
#     the Xbox, 1024x256 on the PC) is copied. Anything else is copied over with a new character ID: the name's
#     text fields (imported fonts mapped by name) and the car's picture, a shape filled from an external image
#     (an atlas of the DLC's Flash textures). That image is renamed (--image), to the name flashtex.py added it
#     under. A copied picture keeps the DLC frame's matrix (it places the DLC's image, so a position the PC
#     file uses for its own atlas does not apply).
#   python -I gfxcars.py <pc.gfx> <dlc.gfx> <out.gfx> [--image FE_VSelect05=FE_VSelectDLC1] [--image-prefix FE_VSelectDLC]
#                        Placeholder_09=Musclecar_17 [...]
# --image-prefix names every copied image that --image does not name <prefix>1, <prefix>2, ... in the order they
# are first copied; an image several cars show (the Deadline cars all show FE_VSelect05) is copied once.
# main() returns {DLC image name: new name} of the images copied, for flashtex.py.
# frame_info(dlc.gfx, label) lists what a DLC frame shows in the car sprites: the name plate's texts and the
# pictures (image, size and the rectangle of it the frame shows).
# Other movies with labelled picture frames (assemble_track.py: the quick play detail screen's track picture,
# FE_quick_play_detailscreen.gfx, frames TEXT_<track>_<subtrack>):
#   --sprite-label <label>  the sprites to extend are those with a frame of that label (instead of the car
#                           sprites, whose first frame is Musclecar_01)
#   --segment               also copy the DLC frames after the label frame up to the next labelled frame (they may
#                           hold only frame scripts: a label frame that plays on to a stop a few frames later
#                           keeps its stop, instead of running off the sprite's end back to frame 0)
# Without these options the output is the same as before.
import re
import struct
import sys

sys.dont_write_bytecode = True


class Bits:
    def __init__(self, b, p):
        self.b, self.bit = b, p * 8

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.b[self.bit >> 3] >> (7 - (self.bit & 7))) & 1)
            self.bit += 1
        return v

    def end(self):
        return (self.bit + 7) >> 3


def rect_end(b, p):
    nb = b[p] >> 3
    return p + (5 + 4 * nb + 7) // 8


def matrix_end(b, p):
    r = Bits(b, p)
    if r.u(1):
        n = r.u(5); r.u(2 * n)
    if r.u(1):
        n = r.u(5); r.u(2 * n)
    n = r.u(5); r.u(2 * n)
    return r.end()


def cxform_alpha_end(b, p):
    r = Bits(b, p)
    add, mult, n = r.u(1), r.u(1), r.u(4)
    if mult: r.u(4 * n)
    if add: r.u(4 * n)
    return r.end()


def cstr(b, p):
    e = b.index(b'\x00', p)
    return b[p:e].decode('utf-8'), e + 1


def read_tags(b, p, end):
    out = []
    while p < end:
        cl = struct.unpack_from('<H', b, p)[0]
        p += 2
        code, ln = cl >> 6, cl & 0x3F
        long_form = ln == 0x3F
        if long_form:
            ln = struct.unpack_from('<I', b, p)[0]
            p += 4
        out.append([code, bytes(b[p:p + ln]), long_form])
        p += ln
    return out


def write_tag(code, data, long_form=False):
    # tags keep the header form they had, so the file only changes where something is added
    if len(data) < 0x3F and not long_form:
        return struct.pack('<H', (code << 6) | len(data)) + data
    return struct.pack('<HI', (code << 6) | 0x3F, len(data)) + data


class Place:
    """PlaceObject2, split into its fields (raw bytes kept for matrix and colour transform)."""

    def __init__(self, b):
        self.flags = b[0]
        self.depth = struct.unpack_from('<H', b, 1)[0]
        p = 3
        self.char = self.matrix = self.cx = self.ratio = self.name = self.clip = None
        if self.flags & 0x02:
            self.char = struct.unpack_from('<H', b, p)[0]; p += 2
        if self.flags & 0x04:
            e = matrix_end(b, p); self.matrix = b[p:e]; p = e
        if self.flags & 0x08:
            e = cxform_alpha_end(b, p); self.cx = b[p:e]; p = e
        if self.flags & 0x10:
            self.ratio = struct.unpack_from('<H', b, p)[0]; p += 2
        if self.flags & 0x20:
            self.name, p = cstr(b, p)
        if self.flags & 0x40:
            self.clip = struct.unpack_from('<H', b, p)[0]; p += 2
        if self.flags & 0x80:
            raise ValueError('clip actions in a car frame: not handled')
        if p != len(b):
            raise ValueError('PlaceObject2 has %d bytes left over' % (len(b) - p))


def frames_of(sprite_tags):
    """[(label, [tags of the frame])], the ShowFrame not included."""
    frames, cur, lab = [], [], None
    for t in sprite_tags:
        code, b = t[0], t[1]
        if code == 1:
            frames.append((lab, cur, t)); cur, lab = [], None
        elif code == 0:
            break
        else:
            if code == 43:
                lab = cstr(b, 0)[0]
            cur.append(t)
    return frames


def display_lists(frames):
    """The display list after each frame: {depth: dict(char, matrix, cx, ratio, name, clip)}."""
    state, out = {}, []
    for frame in frames:
        for t in frame[1]:
            code, b = t[0], t[1]
            if code == 26:
                pl = Place(b)
                if pl.flags & 0x02 and not pl.flags & 0x01 and pl.depth in state:
                    raise ValueError('place on an occupied depth without move')
                if pl.flags & 0x02:  # new character (with or without move): fresh object
                    o = dict(char=pl.char, matrix=None, cx=None, ratio=None, name=None, clip=None)
                    if pl.flags & 0x01 and pl.depth in state:  # replace keeps the old properties
                        old = state[pl.depth]
                        o.update({k: old[k] for k in ('matrix', 'cx', 'ratio', 'name', 'clip')})
                        o['char'] = pl.char
                    state[pl.depth] = o
                o = state[pl.depth]
                for k in ('matrix', 'cx', 'ratio', 'name', 'clip'):
                    v = getattr(pl, k)
                    if v is not None:
                        o[k] = v
            elif code == 28:
                state.pop(struct.unpack_from('<H', b, 0)[0], None)
            elif code in (5, 70, 4):
                raise ValueError('tag %d in a car sprite: not handled' % code)
        out.append({d: dict(o) for d, o in state.items()})
    return out


def place_bytes(depth, o, char):
    flags = 0x02
    body = struct.pack('<H', char)
    if o['matrix'] is not None:
        flags |= 0x04; body += o['matrix']
    if o['cx'] is not None:
        flags |= 0x08; body += o['cx']
    if o['ratio'] is not None:
        flags |= 0x10; body += struct.pack('<H', o['ratio'])
    if o['name'] is not None:
        flags |= 0x20; body += o['name'].encode('utf-8') + b'\x00'
    if o['clip'] is not None:
        flags |= 0x40; body += struct.pack('<H', o['clip'])
    return bytes([flags]) + struct.pack('<H', depth) + body


def char_id(code, b):
    if code in (2, 22, 32, 83, 37, 39, 6, 21, 35, 36, 20, 46, 84, 10, 48, 75, 11, 33, 7, 34, 1001):
        return struct.unpack_from('<H', b, 0)[0]
    return None


def imports(tags):
    m = {}
    for t in tags:
        code, b = t[0], t[1]
        if code == 71:
            url, q = cstr(b, 0)
            q += 2
            n = struct.unpack_from('<H', b, q)[0]
            q += 2
            for _ in range(n):
                cid = struct.unpack_from('<H', b, q)[0]
                name, q = cstr(b, q + 2)
                m[cid] = name
    return m


def load(path):
    d = open(path, 'rb').read()
    if d[:3] not in (b'GFX', b'FWS'):
        sys.exit('%s: not an uncompressed GFX/SWF' % path)
    p = rect_end(d, 8) + 4
    return d, p, read_tags(d, p, len(d))


def car_sprites(tags):
    """The sprites whose first frame is labelled Musclecar_01, in file order."""
    out = []
    for i, t in enumerate(tags):
        code, b = t[0], t[1]
        if code == 39:
            fr = frames_of(read_tags(b, 4, len(b)))
            if fr and fr[0][0] == 'Musclecar_01':
                out.append(i)
    return out


def labelled_sprites(tags, label):
    """The sprites that have a frame labelled <label>, in file order."""
    out = []
    for i, t in enumerate(tags):
        code, b = t[0], t[1]
        if code == 39 and label in [f[0] for f in frames_of(read_tags(b, 4, len(b)))]:
            out.append(i)
    return out


def _sb(r, n):
    v = r.u(n)
    return v - (1 << n) if n and v >> (n - 1) else v


def _matrix(b, p):
    """(scale x, scale y, translate x, translate y) of a MATRIX; scales 16.16, translation in twips. A matrix
    that rotates or skews is refused (the car frames have none)."""
    if b is None:
        return 1.0, 1.0, 0, 0
    r = Bits(b, p)
    sx = sy = 1.0
    if r.u(1):
        n = r.u(5); sx = _sb(r, n) / 65536; sy = _sb(r, n) / 65536
    if r.u(1):
        n = r.u(5)
        if _sb(r, n) or _sb(r, n):
            raise ValueError('rotating matrix: not handled')
    n = r.u(5)
    return sx, sy, _sb(r, n), _sb(r, n)


def _rect(b, p):
    r = Bits(b, p)
    n = r.u(5)
    return [_sb(r, n) for _ in range(4)]  # xmin, xmax, ymin, ymax (twips)


def _edit_text(b):
    """initial text of a DefineEditText, tags removed"""
    p = rect_end(b, 2)
    f1, f2 = b[p], b[p + 1]
    p += 2
    if f1 & 0x01: p += 4
    if f2 & 0x80: p = cstr(b, p)[1]
    if f1 & 0x04: p += 4
    if f1 & 0x02: p += 2
    if f2 & 0x20: p += 9
    p = cstr(b, p)[1]
    if not f1 & 0x80:
        return ''
    t = cstr(b, p)[0]
    return re.sub(r'<[^>]*>', '', t).replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"')


def _shape_bitmaps(code, b):
    """[(bitmap id, matrix)] of the bitmap fills of a DefineShape 1-3"""
    p = rect_end(b, 2)
    rgb = 4 if code == 32 else 3
    n = b[p]; p += 1
    if n == 0xFF and code != 2:
        n = struct.unpack_from('<H', b, p)[0]; p += 2
    out = []
    for _ in range(n):
        t = b[p]; p += 1
        if t == 0x00:
            p += rgb
        elif t in (0x10, 0x12, 0x13):
            p = matrix_end(b, p); ng = b[p] & 0x0F; p += 1 + ng * (1 + rgb) + (2 if t == 0x13 else 0)
        elif t in (0x40, 0x41, 0x42, 0x43):
            bid = struct.unpack_from('<H', b, p)[0]
            if bid != 0xFFFF:
                out.append((bid, _matrix(b, p + 2)))
            p = matrix_end(b, p + 2)
        else:
            raise ValueError('shape fill style %#x not handled' % t)
    return out


def bitmap_sig(defs, cid):
    """For a bitmap-filled shape: the sorted (export name, width, height) of the images it fills with; None for
    anything else. defs: {id: (code, data)}."""
    code, b = defs.get(cid, (None, b''))
    if code not in (2, 22, 32):
        return None
    sig = []
    for bid, _ in _shape_bitmaps(code, b):
        icode, ib = defs.get(bid, (None, b''))
        if icode == 1001:
            w, h = struct.unpack_from('<HH', ib, 4)
            sig.append((ib[9:9 + ib[8]].decode('ascii'), w, h))
        else:
            sig.append(('tag %s' % icode, 0, 0))
    return tuple(sorted(sig)) or None


def frame_info(path, label):
    """[(sprite id, [texts], [(image name, image w, h, (x0, x1, y0, y1) shown in image pixels)])] for the
    frame <label> of each car sprite. The picture sprite shows its image through a clip mask at depth 1, so the
    part shown (the atlas cell) is the mask's rectangle taken back through the picture's place matrix and the
    shape's fill matrix."""
    d, _, tags = load(path)
    defs = {}
    for t in tags:
        c = char_id(t[0], t[1])
        if c is not None:
            defs[c] = (t[0], t[1])
    out = []
    for si in car_sprites(tags):
        b = tags[si][1]
        fr = frames_of(read_tags(b, 4, len(b)))
        dl = display_lists(fr)
        labs = [f[0] for f in fr]
        if label not in labs:
            raise SystemExit('%s: no frame %s' % (path, label))
        texts, pics = [], []
        disp = dl[labs.index(label)]
        masks = []  # (from depth, to depth, rect in sprite twips)
        for dpt, o in sorted(disp.items()):
            if o['clip'] is not None:
                mcode, mb = defs.get(o['char'], (None, b''))
                if mcode not in (2, 22, 32, 83):
                    raise ValueError('mask %d is a tag %s' % (o['char'], mcode))
                mx0, mx1, my0, my1 = _rect(mb, 2)
                sx, sy, tx, ty = _matrix(o['matrix'], 0)
                masks.append((dpt, o['clip'], (mx0 * sx + tx, mx1 * sx + tx, my0 * sy + ty, my1 * sy + ty)))
        for dpt, o in sorted(disp.items()):
            code, cb = defs.get(o['char'], (None, b''))
            if code == 37:
                texts.append(_edit_text(cb))
            elif code in (2, 22, 32) and o['clip'] is None:
                x0, x1, y0, y1 = _rect(cb, 2)
                psx, psy, ptx, pty = _matrix(o['matrix'], 0)
                # the shape's bounds in sprite twips, cut to every mask that covers this depth
                r = [x0 * psx + ptx, x1 * psx + ptx, y0 * psy + pty, y1 * psy + pty]
                for lo, hi, (mx0, mx1, my0, my1) in masks:
                    if lo < dpt <= hi:
                        r = [max(r[0], mx0), min(r[1], mx1), max(r[2], my0), min(r[3], my1)]
                for bid, (sx, sy, tx, ty) in _shape_bitmaps(code, cb):
                    if defs.get(bid, (None,))[0] == 1001:
                        ib = defs[bid][1]
                        w, h = struct.unpack_from('<HH', ib, 4)
                        name = ib[9:9 + ib[8]].decode('ascii')
                        # back to shape twips through the place matrix, then the fill matrix maps image pixels to
                        # shape twips: pixel = (twips - translate) / scale
                        px = lambda v: round(((v - ptx) / psx - tx) / sx)
                        py = lambda v: round(((v - pty) / psy - ty) / sy)
                        pics.append((name, w, h, (px(r[0]), px(r[1]), py(r[2]), py(r[3]))))
        out.append((struct.unpack_from('<H', b, 0)[0], texts, pics))
    return out


def main(argv):
    if len(argv) < 4:
        sys.exit(__doc__ or 'usage: gfxcars.py pc.gfx dlc.gfx out.gfx Placeholder_09=Musclecar_17 ...')
    images = {}
    image_prefix = None
    sprite_label = None
    segment = False
    rest = []
    it = iter(argv)
    for a in it:
        if a == '--image':
            k, v = next(it).split('=', 1)
            images[k] = v
        elif a == '--image-prefix':
            image_prefix = next(it)
        elif a == '--sprite-label':
            sprite_label = next(it)
        elif a == '--segment':
            segment = True
        else:
            rest.append(a)
    used_images = {}
    pc_path, dlc_path, out_path = rest[0], rest[1], rest[2]
    cars = [a.split('=', 1) for a in rest[3:]]
    pd, pstart, ptags = load(pc_path)
    xd, _, xtags = load(dlc_path)
    if sprite_label is None:
        psprites, xsprites = car_sprites(ptags), car_sprites(xtags)
    else:
        psprites, xsprites = labelled_sprites(ptags, sprite_label), labelled_sprites(xtags, sprite_label)
    if len(psprites) != len(xsprites) or not psprites:
        sys.exit('car sprites: PC %d, DLC %d' % (len(psprites), len(xsprites)))

    # character definitions of the DLC file, and the PC's ids in use
    xdefs, pdefs = {}, {}
    for t in xtags:
        c = char_id(t[0], t[1])
        if c is not None:
            xdefs[c] = (t[0], t[1])
    for t in ptags:
        c = char_id(t[0], t[1])
        if c is not None:
            pdefs[c] = (t[0], t[1])
    used ={char_id(t[0], t[1]) for t in ptags} | set(imports(ptags))
    used.discard(None)
    next_id = max(used) + 1
    # imported fonts: DLC id -> PC id by name
    pimp = {v: k for k, v in imports(ptags).items()}
    font_map = {k: pimp[v] for k, v in imports(xtags).items() if v in pimp}

    new_defs = []          # tags to insert before the first car sprite
    copied = {}            # DLC char -> new PC char

    def new_id():
        nonlocal next_id
        next_id += 1
        return next_id - 1

    def copy_image(xc):
        # an external image (GFx tag 1001: id, format, width, height, export name, file name as u8-length
        # strings), renamed with --image so it names the texture added to the PC's Flash set
        code, b = xdefs[xc]
        n1 = b[8]
        export = b[9:9 + n1].decode('ascii')
        n2 = b[9 + n1]
        fname = b[10 + n1:10 + n1 + n2].decode('ascii')
        if export not in images:
            if not image_prefix:
                raise SystemExit('car image %s: give --image %s=<new name>' % (export, export))
            n = 1
            while '%s%d' % (image_prefix, n) in images.values():
                n += 1
            images[export] = '%s%d' % (image_prefix, n)
        new = images[export]
        used_images[export] = new
        nid = new_id()
        nb = struct.pack('<H', nid) + b[2:8] + bytes([len(new)]) + new.encode('ascii')
        nf = fname.replace(export, new)
        nb += bytes([len(nf)]) + nf.encode('ascii')
        new_defs.append([1001, nb, True])
        new_defs.append([56, struct.pack('<HH', 1, nid) + new.encode('ascii') + b'\x00', False])
        return nid

    def copy_shape(code, b):
        # a shape filled with a bitmap (DefineShape 1-3): the bitmap ids in its fill style array are
        # remapped. The shape records are walked to the end: a shape that brings new styles there
        # (StateNewStyles) is refused, as its bitmap ids would be missed.
        if code not in (2, 22, 32):
            raise SystemExit('shape tag %d not handled' % code)
        rgb = 4 if code == 32 else 3
        b = bytearray(b)
        p = rect_end(b, 2)

        def count(p):
            n = b[p]; p += 1
            if n == 0xFF and code != 2:  # the extended count exists from DefineShape2 on
                n = struct.unpack_from('<H', b, p)[0]; p += 2
            return n, p

        n, p = count(p)
        bitmaps = 0
        for _ in range(n):
            t = b[p]; p += 1
            if t == 0x00:
                p += rgb
            elif t in (0x10, 0x12, 0x13):
                p = matrix_end(b, p)
                ng = b[p] & 0x0F; p += 1
                p += ng * (1 + rgb)
                if t == 0x13:
                    p += 2
            elif t in (0x40, 0x41, 0x42, 0x43):
                bid = struct.unpack_from('<H', b, p)[0]
                if bid != 0xFFFF:
                    if xdefs.get(bid, (None,))[0] != 1001:
                        raise SystemExit('shape fill uses bitmap %d that is not an external image' % bid)
                    if bid not in copied:
                        copied[bid] = copy_image(bid)
                    struct.pack_into('<H', b, p, copied[bid])
                    bitmaps += 1
                p = matrix_end(b, p + 2)
            else:
                raise SystemExit('shape fill style %#x not handled' % t)
        n, p = count(p)
        p += n * (2 + rgb)  # line styles: width and colour
        r = Bits(b, p)
        nfill, nline = r.u(4), r.u(4)
        while True:
            if r.u(1) == 0:  # style change or end
                flags = r.u(5)
                if flags == 0:
                    break
                if flags & 0x10:
                    raise SystemExit('shape brings new styles in its records: not handled')
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
        if r.end() != len(b):
            raise SystemExit('shape records end at %d of %d bytes' % (r.end(), len(b)))
        if not bitmaps:
            raise SystemExit('shape without a bitmap fill: not copied')
        return bytes(b)

    def copy_char(xc):
        if xc in copied:
            return copied[xc]
        if xc not in xdefs:
            raise SystemExit('DLC character %d not defined in the file (imported?)' % xc)
        code, b = xdefs[xc]
        if code == 37:
            b = bytearray(b)
            p = rect_end(b, 2)
            f1 = b[p]
            if f1 & 0x01:  # HasFont: map the imported font
                fid = struct.unpack_from('<H', b, p + 2)[0]
                if fid not in font_map:
                    raise SystemExit('text field %d uses font %d that the PC file does not import' % (xc, fid))
                struct.pack_into('<H', b, p + 2, font_map[fid])
        elif code in (2, 22, 32):
            b = bytearray(copy_shape(code, b))
        else:
            raise SystemExit('DLC character %d is a tag %d: not copied' % (xc, code))
        nid = new_id()
        struct.pack_into('<H', b, 0, nid)
        new_defs.append([code, bytes(b), True])
        copied[xc] = nid
        return nid

    for pi, xi in zip(psprites, xsprites):
        pb, xb = ptags[pi][1], xtags[xi][1]
        psid, pnf = struct.unpack_from('<HH', pb, 0)
        ptt = read_tags(pb, 4, len(pb))
        pfr, xfr = frames_of(ptt), frames_of(read_tags(xb, 4, len(xb)))
        if len(pfr) != pnf:
            sys.exit('sprite %d: %d frames read, header says %d' % (psid, len(pfr), pnf))
        pdl, xdl = display_lists(pfr), display_lists(xfr)
        plab = {f[0]: i for i, f in enumerate(pfr) if f[0]}
        xlab = {f[0]: i for i, f in enumerate(xfr) if f[0]}
        # DLC char -> PC char from the frames both have: same label, same depth
        cmap, mmap = {}, {}
        for l in plab:
            if l in xlab:
                a, b = pdl[plab[l]], xdl[xlab[l]]
                for dpt in a:
                    if dpt in b:
                        xc, pcc = b[dpt]['char'], a[dpt]['char']
                        if cmap.setdefault(xc, pcc) != pcc or bitmap_sig(xdefs, xc) != bitmap_sig(pdefs, pcc):
                            cmap[xc] = None  # not a consistent pair, or a picture of another image
                        # the same for positions: the DLC file's layout sits a little off the PC's (the
                        # name fields 9.5 px lower); a matrix both files use consistently at a depth is mapped
                        xm, pm = b[dpt]['matrix'], a[dpt]['matrix']
                        if mmap.setdefault((dpt, xm), pm) != pm:
                            mmap[(dpt, xm)] = None
        added = []
        last = pdl[-1]
        for src, dst in cars:
            if src not in xlab:
                sys.exit('sprite: no DLC frame %s' % src)
            if dst in plab:
                sys.exit('sprite: the PC file already has a frame %s' % dst)
            want = xdl[xlab[src]]
            ft = [[28, struct.pack('<H', dpt), False] for dpt in sorted(last)]
            ft.append([43, dst.encode('utf-8') + b'\x00', False])
            ft += [list(t) for t in xfr[xlab[src]][1] if t[0] == 12]  # the frame's own script (stop)
            for dpt in sorted(want):
                o = dict(want[dpt])
                xc = o['char']
                mapped = cmap.get(xc) is not None
                if mapped:
                    pcc = cmap[xc]
                else:
                    pcc = copy_char(xc)
                # a copied picture places the DLC's own image: its matrix stays the DLC frame's
                if mmap.get((dpt, o['matrix'])) is not None and (mapped or bitmap_sig(xdefs, xc) is None):
                    o['matrix'] = mmap[(dpt, o['matrix'])]
                if o['ratio'] is not None:
                    o['ratio'] = len(pfr)  # the exporter's ratio is the frame index
                ft.append([26, place_bytes(dpt, o, pcc), False])
            pfr.append((dst, ft, [1, b'', False]))
            if segment:
                # the unlabelled frames that follow the DLC's label frame, with their frame scripts only
                j = xlab[src] + 1
                while j < len(xfr) and xfr[j][0] is None:
                    if any(t[0] != 12 for t in xfr[j][1]):
                        sys.exit('sprite: DLC frame %d after %s holds tags %s, not only frame scripts' % (
                            j, src, sorted({t[0] for t in xfr[j][1]})))
                    pfr.append((None, [list(t) for t in xfr[j][1]], [1, b'', False]))
                    j += 1
            last = want
            added.append(dst)
        # rebuild the sprite: header, frames, End
        body = struct.pack('<HH', psid, len(pfr))
        for lab, ft, show in pfr:
            for t in ft:
                body += write_tag(*t)
            body += write_tag(*show)
        end = [t for t in ptt if t[0] == 0]
        body += write_tag(*(end[0] if end else [0, b'', False]))
        ptags[pi] = [39, body, ptags[pi][2]]
        print('sprite %d: %d -> %d frames, added %s' % (psid, pnf, len(pfr), ', '.join(added)))

    first = min(psprites)
    ptags[first:first] = new_defs
    out = b''.join(write_tag(*t) for t in ptags)
    head = bytearray(pd[:pstart])
    struct.pack_into('<I', head, 4, len(head) + len(out))
    open(out_path, 'wb').write(bytes(head) + out)
    print('characters copied (DLC id -> new id): %s' % ', '.join('%d->%d' % kv for kv in copied.items()))
    print('images copied: %s' % (', '.join('%s as %s' % kv for kv in used_images.items()) or 'none'))
    print('written %s, %d bytes' % (out_path, len(head) + len(out)))
    return used_images


if __name__ == '__main__':
    main(sys.argv[1:])
