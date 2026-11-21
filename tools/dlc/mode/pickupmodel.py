# Deadline's pickup box model for the DLL ([DLC] Pickups=1, src/dlcpickups.cpp): the Xbox model
# Generic/Models/GreenPickupBox (in the DLC's RESIDENT_DLC.ARK only; no PC code can load an Xbox model with Xbox
# shaders) converted into a small blob of our own that the DLL draws with its own D3D9 code. Offline only.
#
#   python -I -X utf8 pickupmodel.py <RESIDENT_DLC.ARK | Deadline package dir> [<out.bin>]   (prints the facts)
#   build(xb_ark) -> (blob bytes, info);  parse(blob) -> dict;  check(blob, xb_ark) -> [messages] (raises PickupModelError)
#
# What the Xbox files hold (Generic/Models/GreenPickupBox/GreenPickupBox.*, see README.md "Pickup model"):
# - .model (mdl.Model, big-endian): two meshes. Mesh 0x190: the pickup itself, a hexagonal token (corners at 0.9 m,
#   1.72 x 1.99 m, 0.71 m thick, standing in the model's x/y plane, y up, faces +z/-z), 151 vertices of 0x30 bytes
#   {FLOAT3 position, FLOAT4 texcoord0 = normal xyz + u0, FLOAT4 texcoord1 = v0, alpha1, u2, v2, FLOAT1 texcoord2 =
#   alpha3}, one strip of 191 indices (0xFFFF restarts). Material 0x84136097 = shader 0x58f57228 (main pass), plus a
#   depth-only pass (0xacf16db3). Mesh 0x140: a 1.65 x 1.53 m quad 1.30 m below the token, normal down (shader
#   0xb0ef2956, G-buffer, no texture): not converted.
# - .shaderinst: 0x58f57228 with uvslideshow_2 frames (4, 2), skip 1, speed 10; reflect_fresnel_cheap_3 fadeout 0,
#   fresnel 0; Emissive_0_emissive 1.0; texture_1 = HUD_PPIcon_Detonator2_DXT5 (key 13cb6a8a), cube map =
#   cubemap_Generic (2540d1ff), texture_4 = HUD_PPIcon_Detonator2_Slide (05e9fa9e).
# - .textures (tex/convtex.py): 512x512 DXT1 10 mips (the icon), 1024x512 DXT1 11 mips (the slide show, 4 x 2 cells
#   of 256 x 256), 128 cube DXT1 8 mips; all gamma (sRGB), default swizzle, practically no DXT1 punch-through.
# - Shader 0x58f57228 (Xenos -> HLSL with ../xenos/xlate.py): albedo = lerp(lerp(0.5, icon.rgb, icon.a * alpha1),
#   slide.rgb, saturate(slide.a * alpha3)) with the slide at uv2 + (column / 4, row / 2) of frame
#   floor(7 * frac(speed * t / 7)) (t = frame counter / 60, 7 = frames x * y - skip); written to the G-buffer
#   (albedo sqrt-encoded, normal) and the light buffer gets albedo * (hemisphere ambient + emissive * scene
#   emissive scale) + cube(reflect(view, normal)) * (1 - |n.v|) ^ (1 + fresnel) * (1 - fadeout).
#
# The blob (little-endian; pickupmodel.parse makes the same checks as the DLL's ParseModel), key 'glimpse/pickups/GreenPickupBox.bin' (a path no PC or DLC file has):
#   0x00 'GPKM'  u32 version 1  u32 header size 0x60  u32 vertex count  u32 index count  u32 vertex stride (48)
#   0x18 u32 vertex data offset  u32 index data offset  u32 texture count  u32 texture table offset
#   0x28 f32 slide frames x, frames y, skip, speed (frames per second)
#   0x38 f32 emissive  f32 fresnel  f32 fadeout  f32 0
#   0x48 f32 bounding box min xyz, max xyz (model space, metres)
#   vertex: f32 position xyz, normal xyz, uv0 (icon), uv2 (slide, frame 0), alpha1, alpha3   (48 bytes)
#   index: u16 triangle list (the strip's degenerate triangles left out; winding as the strip gives it, the DLL
#          culls by the normal)
#   texture table, 0x20 each: u32 kind (0 = 2D, 1 = cube), u32 FOURCC 'DXT1', width, height, levels, data offset,
#          data size, role (0 icon, 1 cube map, 2 slide show); data: the DDS body as convtex.xb_texture gives it
#          (2D: levels largest first; cube: face +X, -X, +Y, -Y, +Z, -Z, each with all its levels)
import os
import struct
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ('..', os.path.join('..', 'misc'), os.path.join('..', 'tex'), os.path.join('..', 'shaders')):
    p = os.path.normpath(os.path.join(HERE, sub))
    if p not in sys.path:
        sys.path.insert(0, p)
from arkio import Ark, key  # noqa: E402
from mdl import Model  # noqa: E402
from convmodel import strip_to_list  # noqa: E402
import convtex  # noqa: E402
from convshaders import Reader, endian, SDRI_MAGIC  # noqa: E402

PATH = 'glimpse/pickups/GreenPickupBox.bin'
SRC = 'Generic/Models/GreenPickupBox/GreenPickupBox'
MAGIC = b'GPKM'
VERSION = 1
HEADER = 0x60
STRIDE = 48
MAIN_SHADER = '0x58f57228'
ROLES = {0x13cb6a8a: 0, 0x2540d1ff: 1, 0x05e9fa9e: 2}   # icon, cube map, slide show (texture keys of the .shaderinst)
ROLE_NAMES = ('icon', 'cube map', 'slide show')
FOURCC_DXT1 = struct.unpack('<I', b'DXT1')[0]


class PickupModelError(Exception):
    pass


def need(c, msg):
    if not c:
        raise PickupModelError(msg)


def dxt1_size(w, h, levels, faces=1):
    n = 0
    for L in range(levels):
        n += max(1, (max(1, w >> L) + 3) // 4) * max(1, (max(1, h >> L) + 3) // 4) * 8
    return n * faces


def open_xb(src):
    """an Ark of the DLC's RESIDENT_DLC.ARK (src: the archive, its package dir, or an open Ark)"""
    if isinstance(src, Ark):
        return src, False
    if os.path.isdir(src):
        for nm in os.listdir(src):
            if nm.upper() == 'RESIDENT_DLC.ARK':
                return Ark(os.path.join(src, nm)), True
        raise PickupModelError('%s: no RESIDENT_DLC.ARK' % src)
    return Ark(src), True


def shaderinst(data):
    """{hash: (name, shader id, {const: [vec4]}, {texture: (reg, texture key)})} of a .shaderinst"""
    E = endian(data, SDRI_MAGIC)
    r = Reader(data, E)
    r.raw(12)
    out = {}
    for _ in range(r.u32()):
        h, nm, sid = r.u32(), r.str(), r.str()
        consts, texs = {}, {}
        for _ in range(2):
            n = r.u32()
            r.u32()
            for _ in range(n):
                cn, _cl, _reg, cnt = r.str(), r.u32(), r.u32(), r.u32()
                consts[cn] = [struct.unpack(E + '4f', r.raw(16)) for _ in range(cnt)]
        for _ in range(2):
            for _ in range(r.u32()):
                tn, _cl, reg, th, m = r.str(), r.u32(), r.u32(), r.u32(), r.u32()
                r.raw(8 * m)
                texs[tn] = (reg, th)
        out[h] = (nm, sid, consts, texs)
    return out


def decode_mesh(md):
    """the token mesh of the Xbox .model: (vertices [(pos, nrm, uv0, uv2, a1, a3)], triangles, info)"""
    m = Model(md, True)
    need(m.end == len(md) and m.chunk_counts == (1, 0) and len(m.chunks) == 1, 'model: one data chunk expected')
    blk, data = m.block, m.chunks[0][2]
    W = lambda o: struct.unpack_from('>I', blk, o)[0]
    nvb, vbp, nib, ibp = W(0), W(4), W(8), W(12)
    ib_recs = {ibp + 0x10 * i: i for i in range(nib)}
    vb_recs = {vbp + 0x1C * i: i for i in range(nvb)}
    draws = {}
    for o in range(0, len(blk) - 15, 4):
        if W(o + 4) in ib_recs and W(o + 8) in vb_recs and W(o + 12) == 6:
            draws[o] = (ib_recs[W(o + 4)], vb_recs[W(o + 8)])
    fix = set(m.fixups)
    mats = []   # (mesh, decl index, material hash, (ib, vb))
    for o in range(0, len(blk) - 15, 4):
        if W(o) in fix and W(o + 4) == 0 and W(o + 8) < len(m.decls) and W(W(o) + 0x40) in draws:
            mats.append((W(o), W(o + 8), W(o + 12), draws[W(W(o) + 0x40)]))
    return m, blk, data, mats


def build(xb_src):
    xb, close = open_xb(xb_src)
    try:
        md = xb.get(SRC + '.model')
        sd = xb.get(SRC + '.shaderinst')
        td = xb.get(SRC + '.textures')
        need(md and sd and td, 'DLC archive: %s .model/.shaderinst/.textures missing' % SRC)
    finally:
        if close:
            xb.close()
    inst = shaderinst(sd)
    m, blk, data, mats = decode_mesh(md)
    main = [x for x in mats if x[2] in inst and inst[x[2]][1] == MAIN_SHADER]
    need(len(main) == 1, 'model: one material with shader %s expected, found %d' % (MAIN_SHADER, len(main)))
    mesh, dj, mh, (ib, vb) = main[0]
    els = [tuple(e[:4]) for e in m.decls[dj][1]]
    need(els == [(0, 0, 2, 0), (0, 12, 3, 5), (0, 28, 3, 5), (0, 44, 0, 5)],
         'model: vertex declaration %s, expected FLOAT3 pos, FLOAT4 tc0, FLOAT4 tc1, FLOAT1 tc2' % (els,))
    r = struct.unpack_from('>I', blk, 4)[0] + 0x1C * vb
    off, _size, stride, count = struct.unpack_from('>4I', blk, r + 4)
    need(stride == 0x30 and off + stride * count <= len(data), 'model: vertex buffer 0x%x x %d' % (stride, count))
    verts = []
    for i in range(count):
        v = struct.unpack_from('>12f', data, off + stride * i)
        pos, nrm = v[0:3], v[3:6]
        verts.append((pos, nrm, (v[6], v[7]), (v[9], v[10]), v[8], v[11]))
    ir = struct.unpack_from('>I', blk, 12)[0] + 0x10 * ib
    sub, icount = struct.unpack_from('>2I', blk, ir)
    ioff = struct.unpack_from('>I', blk, sub + 0x18)[0]
    strip = struct.unpack_from('>%dH' % icount, data, ioff)
    lst = strip_to_list(list(strip))
    tris = [tuple(lst[k:k + 3]) for k in range(0, len(lst), 3)]
    tris = [t for t in tris if len(set(t)) == 3]
    need(all(i < count for t in tris for i in t), 'model: index beyond the vertices')
    # the material's constants and textures
    nm, sid, consts, texs = inst[mh]
    g = lambda n, d: consts.get(n, [(d, 0, 0, 0)])[0]
    frames = g('uvslideshow_2_frames', 1)
    skip, speed = g('uvslideshow_2_skip', 0)[0], g('uvslideshow_2_speed', 0)[0]
    emissive = g('Emissive_0_emissive', 1)[0]
    fresnel, fadeout = g('reflect_fresnel_cheap_3_fresnel', 0)[0], g('reflect_fresnel_cheap_3_fadeout', 0)[0]
    tex_keys = {th for _, th in texs.values()}
    need(tex_keys == set(ROLES), 'shaderinst: textures %s, expected %s' % (sorted('%08x' % k for k in tex_keys),
                                                                          sorted('%08x' % k for k in ROLES)))
    # textures
    c = convtex.parse_groups(td)
    need(len(c['groups']) == 1 and c['groups'][0]['dsize'], 'textures: one resident group expected')
    gr = c['groups'][0]
    texinfo = {}
    for k, (loc, slot, toff) in enumerate(gr['fix']):
        i = (loc - 0x18) // 0x40
        e = c['entries'][i]
        tkey = struct.unpack_from('>I', e, 4)[0]
        t, pcf, nlev, srgb, dds = convtex.xb_texture(e, gr['blobs'][k])
        need(t['fmt'] == 0x12 and dds[84:88] == b'DXT1', 'texture %08x: format 0x%x, DXT1 expected' % (tkey, t['fmt']))
        cube = t['dim'] == 3
        body = dds[128:]
        need(len(body) == dxt1_size(t['width'], t['height'], nlev, 6 if cube else 1),
             'texture %08x: %d data bytes for %dx%d x %d levels' % (tkey, len(body), t['width'], t['height'], nlev))
        texinfo[tkey] = dict(kind=1 if cube else 0, w=t['width'], h=t['height'], levels=nlev, body=body, srgb=srgb,
                             swizzle=t['swizzle'])
    need(set(texinfo) >= set(ROLES), 'textures: %s missing' % sorted('%08x' % k for k in set(ROLES) - set(texinfo)))
    # blob
    bb_min = [min(v[0][a] for v in verts) for a in range(3)]
    bb_max = [max(v[0][a] for v in verts) for a in range(3)]
    vdata = b''.join(struct.pack('<12f', *(v[0] + v[1] + v[2] + v[3] + (v[4], v[5]))) for v in verts)
    idata = struct.pack('<%dH' % (3 * len(tris)), *[i for t in tris for i in t])
    order = sorted(ROLES, key=lambda k: ROLES[k])
    vo = HEADER
    io = vo + len(vdata)
    to = (io + len(idata) + 15) & ~15
    pos = to + 0x20 * len(order)
    table, bodies = b'', b''
    for k in order:
        t = texinfo[k]
        table += struct.pack('<8I', t['kind'], FOURCC_DXT1, t['w'], t['h'], t['levels'], pos + len(bodies),
                             len(t['body']), ROLES[k])
        bodies += t['body']
    head = MAGIC + struct.pack('<9I', VERSION, HEADER, len(verts), 3 * len(tris), STRIDE, vo, io, len(order), to)
    head += struct.pack('<8f', frames[0], frames[1], skip, speed, emissive, fresnel, fadeout, 0.0)
    head += struct.pack('<6f', *(bb_min + bb_max))
    head = head.ljust(HEADER, b'\0')
    blob = head + vdata + idata
    blob = blob.ljust(to, b'\0') + table + bodies
    info = dict(vertices=len(verts), triangles=len(tris), strip=icount, frames=frames[:2], skip=skip, speed=speed,
                emissive=emissive, fresnel=fresnel, fadeout=fadeout, bbox=(bb_min, bb_max),
                a1=sorted({round(v[4], 4) for v in verts}), a3=sorted({round(v[5], 4) for v in verts}),
                textures=[(ROLE_NAMES[ROLES[k]], '%08x' % k, texinfo[k]['w'], texinfo[k]['h'], texinfo[k]['levels'],
                           'cube' if texinfo[k]['kind'] else '2D', texinfo[k]['srgb'], hex(texinfo[k]['swizzle']))
                          for k in order],
                materials=[('mesh 0x%x' % x[0], 'decl %d' % x[1], '%08x' % x[2],
                            inst[x[2]][1] if x[2] in inst else '?') for x in mats],
                verts=verts, tris=tris, texinfo={ROLES[k]: texinfo[k] for k in order})
    return blob, info


def parse(blob):
    """the blob as the DLL reads it (same bounds checks): dict with verts, idx, textures, constants"""
    need(len(blob) >= HEADER and blob[:4] == MAGIC, 'blob: no GPKM header')
    ver, hs, nv, ni, stride, vo, io, nt, to = struct.unpack_from('<9I', blob, 4)
    need(ver == VERSION and hs == HEADER and stride == STRIDE, 'blob: version %d header %x stride %d' % (ver, hs, stride))
    need(0 < nv <= 0x10000 and 0 < ni <= 0x30000 and ni % 3 == 0 and vo + nv * stride <= len(blob)
         and io + 2 * ni <= len(blob) and nt == len(ROLES) and to + 0x20 * nt <= len(blob), 'blob: sections out of range')
    consts = struct.unpack_from('<8f', blob, 0x28)
    fx, fy, skip, speed, em = consts[:5]
    need(1 <= fx <= 16 and 1 <= fy <= 16 and 0 <= skip < fx * fy and 0 <= speed <= 120 and 0 <= em <= 16,
         'blob: slide show or emissive constants out of range')
    bbox = struct.unpack_from('<6f', blob, 0x48)
    verts = [struct.unpack_from('<12f', blob, vo + stride * i) for i in range(nv)]
    idx = list(struct.unpack_from('<%dH' % ni, blob, io))
    need(max(idx) < nv, 'blob: index beyond the vertices')
    texs = []
    roles = set()
    for i in range(nt):
        kind, fcc, w, h, lv, off, size, role = struct.unpack_from('<8I', blob, to + 0x20 * i)
        need(kind in (0, 1) and fcc == FOURCC_DXT1 and 0 < w <= 4096 and 0 < h <= 4096 and 0 < lv <= 13
             and (not kind or w == h) and role < 3 and role not in roles and kind == (1 if role == 1 else 0)
             and size == dxt1_size(w, h, lv, 6 if kind else 1) and off + size <= len(blob),
             'blob: texture %d table entry' % i)
        roles.add(role)
        texs.append(dict(kind=kind, w=w, h=h, levels=lv, role=role, body=blob[off:off + size]))
    return dict(verts=verts, idx=idx, textures=texs, frames=consts[0:2], skip=consts[2], speed=consts[3],
                emissive=consts[4], fresnel=consts[5], fadeout=consts[6], bbox=bbox)


def check(blob, xb_src):
    """round trip: the blob parses, and every vertex, triangle, constant and texture byte equals what the DLC files
    give. Returns the messages (one per check)."""
    _, info = build(xb_src)
    p = parse(blob)
    msgs = []
    vs = info['verts']
    need(len(p['verts']) == len(vs) and all(
        all(abs(a - b) <= 1e-6 * max(1.0, abs(b)) for a, b in zip(pv, v[0] + v[1] + v[2] + v[3] + (v[4], v[5])))
        for pv, v in zip(p['verts'], vs)), 'round trip: vertices differ')
    msgs.append('pickup model: %d vertices equal to the Xbox stream (position, normal, uv0, uv2, alpha1, alpha3)' % len(vs))
    need([tuple(p['idx'][k:k + 3]) for k in range(0, len(p['idx']), 3)] == info['tris'], 'round trip: triangles differ')
    msgs.append('pickup model: %d triangles = the strip of %d indices as a list without its degenerate triangles'
                % (len(info['tris']), info['strip']))
    nrm_ok = all(abs(sum(c * c for c in v[1]) - 1.0) < 1e-3 for v in vs)
    need(nrm_ok, 'normals not unit length')
    msgs.append('pickup model: every normal (texcoord0.xyz) unit length')
    need(tuple(p['frames']) == tuple(info['frames']) and p['speed'] == info['speed'] and p['emissive'] == info['emissive'],
         'round trip: constants differ')
    msgs.append('pickup model: slide show %gx%g frames, skip %g, speed %g, emissive %g, fresnel %g, fadeout %g' % (
        p['frames'][0], p['frames'][1], p['skip'], p['speed'], p['emissive'], p['fresnel'], p['fadeout']))
    for t in p['textures']:
        src = info['texinfo'][t['role']]
        need(t['body'] == src['body'] and (t['w'], t['h'], t['levels'], t['kind']) ==
             (src['w'], src['h'], src['levels'], src['kind']), 'round trip: texture %d differs' % t['role'])
        msgs.append('pickup model: %s %dx%d %s DXT1, %d levels, %d bytes equal to convtex.xb_texture' % (
            ROLE_NAMES[t['role']], t['w'], t['h'], 'cube' if t['kind'] else '2D', t['levels'], len(t['body'])))
    return msgs


def main(argv):
    if not argv or len(argv) > 2:
        print('usage: python -I -X utf8 pickupmodel.py <RESIDENT_DLC.ARK | package dir> [<out.bin>]')
        return 2
    blob, info = build(argv[0])
    for k in ('vertices', 'triangles', 'strip', 'frames', 'skip', 'speed', 'emissive', 'fresnel', 'fadeout', 'bbox',
              'a1', 'a3', 'materials', 'textures'):
        print('%-10s %s' % (k, info[k]))
    for msg in check(blob, argv[0]):
        print('ok  ' + msg)
    print('blob %d bytes' % len(blob))
    if len(argv) > 1:
        out = os.path.abspath(argv[1])
        need(not out.lower().startswith('g:'), 'refusing to write to %s' % out)
        with open(out, 'wb') as f:
            f.write(blob)
        print('written', out)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except PickupModelError as e:
        sys.exit('ERROR: %s' % e)
