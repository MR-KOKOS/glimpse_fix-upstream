# Xbox 360 compiled shader container (as stored in Split/Second .fxc entries, big endian).
#
#   0x00 u32 magic 0x102A1100 (pixel) / 0x102A1101 (vertex)
#   0x04 u32 virtual size (header part), 0x08 u32 physical size (literal constants + microcode)
#   0x0C u32 0x24, 0x10 u32 constant table offset, 0x14 u32 definition table offset, 0x18 u32 shader
#   header offset, 0x1C/0x20 0;  0x24 u32 hash, str debug path (updb)
#   constant table: u32 size, then a D3DXSHADER_CONSTANTTABLE ('CTAB' without the fourcc), big endian
#   definition table: u32 x4, u32 size, then (u16 register, u16 float count, u32 offset in the physical
#       part) records up to a zero word: literal float constants the runtime loads (registers >= 256 = PS)
#   shader header: u32 microcode offset in the physical part, u32 microcode size, u32 SQ_PROGRAM_CNTL
#       part, u32 SQ_CONTEXT_MISC part (PS: param_gen_pos in bits 8-15), u32, u32 interpolator info
#       (count in bits 5-9)
#     vertex shader: u32 1, u32 vertex element count, u32 export patch count, u32, then vertex elements
#       (bits 0-11 vfetch slot, 12-15 D3DDECLUSAGE, 16-19 usage index), interpolators, export patch list
#     pixel shader: u32, u32, then interpolators
#   interpolator: bits 0-3 usage index, 4-7 usage, 8-11 register (VS export o#, PS input r#), 12-15 mask
#   the physical part (literals, then microcode) follows the virtual part.
import struct
import sm3

PS_MAGIC, VS_MAGIC = 0x102A1100, 0x102A1101


class XShader:
    def __init__(self, data):
        self.data = data
        h = struct.unpack_from('>9I', data, 0)
        self.magic = h[0]
        if self.magic not in (PS_MAGIC, VS_MAGIC):
            raise ValueError('not an Xbox shader container (%08x)' % self.magic)
        self.is_ps = self.magic == PS_MAGIC
        self.virt, self.phys = h[1], h[2]
        if self.virt + self.phys != len(data):
            raise ValueError('container size')
        ct_off, def_off, sh_off = h[4], h[5], h[6]
        n = struct.unpack_from('>I', data, ct_off)[0]
        self.ctab = sm3.parse_ctab(data[ct_off + 4: ct_off + 4 + n], '>')
        d = struct.unpack_from('>5I', data, def_off)
        self.def_hdr = d
        self.defs = []
        p = def_off + 20
        end = def_off + 20 + d[4]
        while p + 8 <= end:
            reg, cnt, off = struct.unpack_from('>HHI', data, p)
            if reg == 0 and cnt == 0:
                break
            vals = struct.unpack_from('>%df' % cnt, data, self.virt + off)
            self.defs.append((reg, cnt, vals))
            p += 8
        s = struct.unpack_from('>6I', data, sh_off)
        self.code_off, self.code_size, self.program_cntl, self.context_misc, self.sh10, self.interp_info = s
        self.code = data[self.virt + self.code_off: self.virt + self.code_off + self.code_size]
        n_int = (self.interp_info >> 5) & 0x1F
        p = sh_off + 24
        self.vertex_elements, self.exports = [], []
        if not self.is_ps:
            one, n_ve, n_exp, self.vs_extra = struct.unpack_from('>4I', data, p)
            p += 16
            for k in range(n_ve):
                v = struct.unpack_from('>I', data, p + 4 * k)[0]
                self.vertex_elements.append({'slot': v & 0xFFF, 'usage': (v >> 12) & 15, 'index': (v >> 16) & 15,
                                             'extra': v >> 20})
            p += 4 * n_ve
        else:
            self.ps_extra = struct.unpack_from('>2I', data, p)
            p += 8
            n_exp = 0
        self.interps = []
        for k in range(n_int):
            v = struct.unpack_from('>I', data, p + 4 * k)[0]
            self.interps.append({'index': v & 15, 'usage': (v >> 4) & 15, 'reg': (v >> 8) & 15,
                                 'mask': (v >> 12) & 15, 'extra': v >> 16})
        p += 4 * n_int
        for k in range(n_exp):
            self.exports.append(struct.unpack_from('>I', data, p + 4 * k)[0])
        p += 4 * n_exp
        self.header_end = p
        # SQ_PROGRAM_CNTL / SQ_CONTEXT_MISC fields
        self.param_gen = bool(self.program_cntl & (1 << 18)) if self.is_ps else False
        self.param_gen_reg = (self.context_misc >> 8) & 0xFF
        self.num_regs = ((self.program_cntl >> 8) & 0xFF) if self.is_ps else (self.program_cntl & 0xFF)

    def literal(self, reg):
        """literal float4 for constant register reg (0..255 in this shader's space), or None"""
        r = reg + (256 if self.is_ps else 0)
        for base, cnt, vals in self.defs:
            if base <= r < base + (cnt + 3) // 4:
                k = 4 * (r - base)
                v = list(vals[k:k + 4])
                return tuple(v + [0.0] * (4 - len(v)))
        return None
