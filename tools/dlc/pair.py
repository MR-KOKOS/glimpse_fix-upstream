# Find a base-game car that exists in both the Xbox 360 and the PC resident archive, and extract its files
# from both, so the formats can be compared file by file.
import os
import struct
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# the same LZSS as the old scratchpad ark.py; XB = GLIMPSE_XB_RESIDENT, PC = GLIMPSE_PC_RESIDENT (pair_lib.py)
from pair_lib import lzss_n, XB, PC  # noqa: E402
if __name__ == '__main__' and not XB:
    sys.exit('Set GLIMPSE_XB_RESIDENT to the Xbox 360 resident.ark (and GLIMPSE_PC_RESIDENT to the PC one).')
SUFFIXES = ['.deform', '.low.textures', '.maincoll', '.parts', '.phys', '.textures', '_bodypaint.textures', '_bodypaint.low.textures',
            '_bw_high.fxc', '_bw_high.model', '_bw_high.shaderinst', '_bw_high.shaders', '_high.fxc', '_high.model',
            '_high.shaderinst', '_high.shaders', '_low.model', '_xlow.model', '_damagemap.textures', '_whls.textures',
            '_dynamicparts.params']
EXTRA = ['chassisname.txt', 'deformationcontrol.params', 'surfacedamage.params']


def key(path):
    h = 0
    for c in path.lower().encode('latin-1'):
        h = (h * 31 + c) & 0xFFFFFFFF
    return h


class Ark:
    def __init__(self, fn):
        self.f = open(fn, 'rb')
        head = self.f.read(24)
        self.E = '>' if head[:4] == b'\x00\x01\x00\x00' else '<'
        ver, hsize, cA, offA, cB, offB = struct.unpack_from(self.E + '6I', head, 0)
        self.f.seek(8 + offA)
        idx = self.f.read(20 * cA)
        self.ents = {}
        for k in range(cA):
            t, off, cs, us, kk = struct.unpack_from(self.E + '5I', idx, 20 * k)
            self.ents[kk] = (t, off, cs, us)

    def get(self, path):
        e = self.ents.get(key(path))
        if not e:
            return None
        t, off, cs, us = e
        self.f.seek(off)
        raw = self.f.read(cs + 16)
        if t == 0:
            return raw[:us]
        if t == 1:
            return lzss_n(raw + b'\0' * 16, 4, us)[0]
        raise Exception('type %d' % t)


xb, pc = Ark(XB), Ark(PC)
print('xbox entries', len(xb.ents), 'pc entries', len(pc.ents))
for kind in ['musclecar', 'supercar', 'truck', 'hypercar', 'suv', 'pickup', 'compact', 'rally', 'sports', 'gt', 'coupe']:
    for n in range(1, 21):
        name = '%s_%02d' % (kind, n)
        p = 'vehicles/bodies/%s/%s_high.model' % (name, name)
        if key(p) in xb.ents or key(p) in pc.ents:
            print(name, 'xbox' if key(p) in xb.ents else '-', 'pc' if key(p) in pc.ents else '-')

if len(sys.argv) > 1:
    name = sys.argv[1]
    for side, ark in (('xb', xb), ('pc', pc)):
        os.makedirs('pair_%s/%s' % (side, name), exist_ok=True)
    for sfx in SUFFIXES + EXTRA:
        rel = 'vehicles/bodies/%s/%s' % (name, sfx if sfx in EXTRA else name + sfx)
        sizes = []
        for side, ark in (('xb', xb), ('pc', pc)):
            data = ark.get(rel)
            sizes.append(len(data) if data is not None else -1)
            if data is not None:
                open('pair_%s/%s/%s' % (side, name, os.path.basename(rel)), 'wb').write(data)
        print('%-45s xbox %9d  pc %9d' % (os.path.basename(rel), sizes[0], sizes[1]))
