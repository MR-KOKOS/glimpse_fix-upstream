# Split/Second .model reader for both platforms (see NOTES.md): splits a file into its parts.
import struct


class Model:
    def __init__(self, data, xbox):
        self.d = d = data
        self.xbox = xbox
        E = self.E = '>' if xbox else '<'
        self.version, self.flags = d[0], d[1]
        self.u16 = struct.unpack_from(E + 'H', d, 2)[0]
        o = 4
        self.platform = b''
        if not xbox:
            n = struct.unpack_from(E + 'I', d, 4)[0]
            self.platform = d[8:8 + n]
            o = 8 + n
        self.decl_start = o
        n = struct.unpack_from(E + 'I', d, o)[0]
        o += 4
        self.decls = []
        for i in range(n):
            head = d[o:o + 8]
            c = struct.unpack_from(E + 'I', d, o + 8)[0]
            els = [struct.unpack_from(E + '5I', d, o + 12 + 0x14 * k) for k in range(c)]
            self.decls.append((head, els))
            o += 12 + 0x14 * c
        nf = struct.unpack_from(E + 'I', d, o)[0]
        self.fixups = list(struct.unpack_from(E + '%dI' % nf, d, o + 4))
        o += 4 + 4 * nf
        bs = struct.unpack_from(E + 'I', d, o)[0]
        o += 4
        self.block_size_at = o - 4
        o = (o + 15) & ~15
        self.block_at = o
        self.block = d[o:o + bs]
        o += bs
        self.chunk_head_at = o
        a, b = struct.unpack_from(E + '2I', d, o)
        o += 8
        self.chunks = []
        for i in range(a - b):
            idx, size, extra = struct.unpack_from(E + '3I', d, o)
            o += 12
            self.chunks.append((idx, extra, d[o:o + size]))
            o += size
        self.chunk_counts = (a, b)
        self.end = o
