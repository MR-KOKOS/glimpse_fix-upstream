# Find a base-game car that exists in both the Xbox 360 and the PC resident archive, and extract its files
# from both, so the formats can be compared file by file.
import os
import struct
import sys
# The archives' LZSS (as misc/arkio.py; the same as the older session's ark.py, checked on 300 PC entries):
# returns (data, input position after).
def lzss_n(src, i, size, fill=0):
    win = bytearray([fill]) * 4096
    r = 0xFEE
    out = bytearray()
    flags = 0
    while len(out) < size:
        flags >>= 1
        if not flags & 0x100:
            flags = src[i] | 0xFF00
            i += 1
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            win[r] = c
            r = (r + 1) & 0xFFF
        else:
            a, b = src[i], src[i + 1]
            i += 2
            o = a | ((b & 0xF0) << 4)
            for k in range((b & 0x0F) + 3):
                c = win[(o + k) & 0xFFF]
                out.append(c)
                win[r] = c
                r = (r + 1) & 0xFFF
    return bytes(out[:size]), i

# The Xbox 360 base resident.ark is only for the oracle tests (test_*.py) and dlcaudio's sample reuse: set
# GLIMPSE_XB_RESIDENT to it. build_all.py never does, so a build here equals what every user gets.
XB = os.environ.get('GLIMPSE_XB_RESIDENT', '')
# tools/dlc/build_all.py sets GLIMPSE_PC_RESIDENT to the user's own game folder; a manual run takes it below the game
# folder in GAME_DIR ('' without either: Ark says what to set)
PC = os.environ.get('GLIMPSE_PC_RESIDENT') or (
    os.path.join(os.environ['GAME_DIR'], 'data', 'resident.ark') if os.environ.get('GAME_DIR') else '')
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
        if not fn:  # PC or XB above without its environment variable
            raise FileNotFoundError('no archive given: set GAME_DIR to the Split/Second game folder (the PC '
                                    'resident.ark) or GLIMPSE_XB_RESIDENT to the Xbox 360 one')
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


