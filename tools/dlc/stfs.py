# Minimal read-only STFS (Xbox 360 LIVE/PIRS/CON package) reader: metadata, file list, extraction.
# Usage: python -I stfs.py info <package>...
#        python -I stfs.py list <package>
#        python -I stfs.py extract <package> <outdir>
import os
import struct
import sys


def be16(b, o): return struct.unpack_from('>H', b, o)[0]
def be32(b, o): return struct.unpack_from('>I', b, o)[0]
def be24(b, o): return (b[o] << 16) | (b[o + 1] << 8) | b[o + 2]
def le24(b, o): return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


class Stfs:
    def __init__(self, path):
        self.f = open(path, 'rb')
        self.h = self.f.read(0xA000)
        self.magic = self.h[:4]
        self.header_size = be32(self.h, 0x340)
        self.content_type = be32(self.h, 0x344)
        self.title_id = be32(self.h, 0x360)
        self.display_name = self.h[0x411:0x411 + 0x80].decode('utf-16-be', 'replace').split('\0')[0]
        self.description = self.h[0xD11:0xD11 + 0x80].decode('utf-16-be', 'replace').split('\0')[0]
        vd = 0x379
        self.vd_size = self.h[vd]
        self.block_sep = self.h[vd + 2]
        self.ft_blocks = struct.unpack_from('<H', self.h, vd + 3)[0]
        self.ft_start = le24(self.h, vd + 5)
        self.total_blocks = be32(self.h, vd + 0x14)
        # block layout: female (read-only) packages use one hash table per level, male ones two
        self.base = ((self.header_size + 0xFFF) & ~0xFFF)
        self.shift = 0 if (self.block_sep & 1) == 1 else 1  # 1 = two tables per level (male)

    def block_offset(self, block):
        # STFS: after every 0xAA data blocks a hash table, after 0xAA*0xAA a level 1 table, ...
        sh = self.shift
        adj = block + ((block // 0xAA) + 1 << sh)
        if block >= 0xAA:
            adj += ((block // 0x70E4) + 1) << sh
            if block >= 0x70E4:
                adj += 1 << sh
        return self.base + adj * 0x1000

    def files(self):
        out = []
        for i in range(self.ft_blocks):
            self.f.seek(self.block_offset(self.ft_start + i))
            blk = self.f.read(0x1000)
            for e in range(0x40):
                ent = blk[e * 0x40:(e + 1) * 0x40]
                name_len = ent[0x28] & 0x3F
                if not name_len:
                    continue
                flags = ent[0x28] >> 6
                name = ent[:name_len].decode('latin-1')
                blocks = le24(ent, 0x29)
                start = le24(ent, 0x2F)
                parent = be16(ent, 0x32)
                size = be32(ent, 0x34)
                out.append(dict(name=name, dir=bool(flags & 2), consecutive=bool(flags & 1), blocks=blocks, start=start,
                                parent=parent, size=size))
        return out

    def path_of(self, files, i):
        parts = [files[i]['name']]
        p = files[i]['parent']
        while p != 0xFFFF and p < len(files):
            parts.append(files[p]['name'])
            p = files[p]['parent']
        return '/'.join(reversed(parts))

    def hash_entry_next(self, block):
        # the level 0 hash table that covers this block holds its "next block" link
        sh = self.shift
        group = block // 0xAA
        tbl = group * 0xAB
        if block >= 0xAA:
            tbl += ((block // 0x70E4) + 1) << sh
            if block >= 0x70E4:
                tbl += 1 << sh
        tbl = tbl << sh if False else tbl
        # table position in blocks (same formula as STFS docs, level 0)
        off = self.base + self.level0_table_block(block) * 0x1000 + (block % 0xAA) * 0x18
        self.f.seek(off)
        e = self.f.read(0x18)
        return be24(e, 0x15)

    def level0_table_block(self, block):
        sh = self.shift
        base = (block // 0xAA) * (0xAA + (1 << sh))
        if block >= 0xAA:
            base += ((block // 0x70E4) + 1) << sh
            if block >= 0x70E4:
                base += 1 << sh
        return base

    def read_file(self, ent):
        data = bytearray()
        left = ent['size']
        block = ent['start']
        for n in range(ent['blocks']):
            self.f.seek(self.block_offset(block))
            chunk = self.f.read(min(0x1000, left))
            data += chunk
            left -= len(chunk)
            if left <= 0:
                break
            block = block + 1 if ent['consecutive'] else self.hash_entry_next(block)
        return bytes(data)


def main():
    cmd = sys.argv[1]
    if cmd == 'info':
        for p in sys.argv[2:]:
            s = Stfs(p)
            print('%s %s type %08X title %08X blocks %d ft %d@%d sep %d | %s | %s' % (
                os.path.basename(p)[:12], s.magic, s.content_type, s.title_id, s.total_blocks, s.ft_blocks, s.ft_start,
                s.block_sep, s.display_name, s.description[:80]))
    elif cmd == 'list':
        s = Stfs(sys.argv[2])
        fs = s.files()
        for i, e in enumerate(fs):
            print('%s%-60s %10d %s' % ('D ' if e['dir'] else '  ', s.path_of(fs, i), e['size'], 'cons' if e['consecutive'] else ''))
    elif cmd == 'extract':
        s = Stfs(sys.argv[2])
        out = sys.argv[3]
        fs = s.files()
        for i, e in enumerate(fs):
            path = os.path.join(out, s.path_of(fs, i))
            if e['dir']:
                os.makedirs(path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as w:
                w.write(s.read_file(e))
        print('extracted', len(fs), 'entries')


if __name__ == '__main__':
    main()
