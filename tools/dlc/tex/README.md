# Split/Second .textures: Xbox 360 -> PC

`convtex.py` turns an Xbox 360 texture container (`TEXS`, big-endian, Xenos tiled data) into the PC container
(`SXET`, little-endian, one DDS per texture). It is checked against all 40 base cars: 320 of 320 files come out
byte-identical to the PC files (658 textures). Level textures (Graveyard, all formats Quarry uses): 54 of 60 containers
and 736 of 739 streamed `.stream.ark` textures byte-identical, the rest equivalent (see Levels below).

## Usage (Python 3, `python -I`)
    python -I convtex.py IN [OUT]          # file or folder (every *.textures below it, any case); default OUT = IN.pc / IN_pc
    python -I convtex.py --dds IN OUTDIR   # dump the textures of an Xbox or PC container as .dds files (for viewing)
    python -I fetchpairs.py [CACHE]        # extract the base car textures of both platforms (pair_lib.XB / pair_lib.PC)
    python -I test_convtex.py [CACHE] [-v] # convert every Xbox file in CACHE and compare with the PC file

The default CACHE is the session scratchpad (`...\scratchpad\dlc\texpairs`); `fetchpairs.py` needs `../pair_lib.py`.

## Format (offsets from file start; E = file endianness)
    00 magic 'TEXS' (Xbox) / 'SXET' (PC)   04 version 0xC   08 0   0C hs = header size counted from 0x10
    10 n textures, 0x18, 0, 0, 1, 0x18 + 0x40*n
    28 n entries of 0x40 bytes:
         +00 data pointer (0, fixed up)  +04 key  +08 bytes FF FF 00 00
         Xbox: +0C 3, +10 1, +14..1C 0, +20 FFFF0000, +24 FFFF0000, +28 Xenos fetch constant (6 dwords), 
         PC:   +0C format (0x21 DXT1, 0x24 DXT4/5), +10 1, +14 mip count, +18 width, +1C height,
               +20 sRGB (1 when the Xbox fetch sign bits are 3 = gamma, 0 for the damage maps), rest 0
    then: data size, 0, n fixups, offset of the fixup table (from 0x10), then n x {0x18 + 0x40*i, 0, data offset}
    0x10 + hs: texture data.  Xbox: raw Xenos memory per texture (4 KB-aligned sizes).  PC: u32 size + DDS file,
               packed without padding.
    after the data: name table 'C2NM' + {u32 key, u32 length, name} (dwords swapped on PC, bytes 'MN2C');
               an empty container has the magic on Xbox and nothing on PC.

Xbox fetch constant: dword0 bit31 tiled, bits 22-30 pitch/32, bits 2-9 sign per channel; dword1 bits 0-5
format (0x12 DXT1, 0x14 DXT4_5), bits 6-7 endian (1 = 8-in-16), bits 12+ base address; dword2 width-1 (13 bits),
height-1 (13 bits); dword4 bits 6-9 max mip level; dword5 bits 9-10 dimension, bit 11 packed mips, bits 12+ mip
address.

PC DDS: flags 0x21007, pitch 0, depth 0, mip count, pixel format FOURCC 'DXT1' or 'DXT4' (the PC files use
'DXT4' for every Xbox DXT4_5 texture), caps 0x401008; then the mips, linear block order. (Levels: see below for
one-level, cube, volume and 8888 textures.)

## Conversion
16-bit byte swap of the whole texture; per mip XGAddress2DTiledOffset with the pitch aligned to 32 blocks;
level 0 at the base address, levels >= 1 at the mip address with pow2 sizes rounded to 32x32-block tiles; once
the smaller side is <= 16 texels the rest sits in one packed tile (Xenia GetPackedMipOffset; if level 0 is
already that small everything is in the base tile).

## Levels (main arks and `<level>.stream.ark`)
Formats and layouts beyond the cars, all checked against the PC Graveyard files:

| Xenos | PC | notes |
|---|---|---|
| `0x06` k_8_8_8_8, endian 8-in-32 | format 2, DDS RGB+alpha 32 bit, masks ff0000/ff00/ff/ff000000 | after the 32-bit swap the texel bytes are already B, G, R, A |
| dim 3 (cube), 6 faces | entry +0x10 = 6, DDS caps2 0xFE00 | each face padded to whole tiles and 4 KB; level 0 faces at the base address, levels >= 1 (all faces of a level one after the other) at the mip address; DDS order face by face |
| dim 2 (3D) | entry +0x10 = depth, DDS DEPTH flag + depth, caps2 0x200000 | fetch dword2 is 11+11+10 bits (w, h, d); tiling below; slices of a level padded to a multiple of 4; DDS order level by level, slices one after the other |
| unpacked mips (fetch bit 11 = 0) | | already handled |
| one-level textures | DDS flags 0x1007, caps 0x1000 | no MIPMAPCOUNT flag, no COMPLEX/MIPMAP caps (the count field is still 1) |

3D tiling (`tiled_block_3d`, fitted on all blocks of the 17 Graveyard volumes, DXT1 and DXT5): macro tiles of 32 x 16 x 4
blocks, `macro = ((z >> 2) * (H >> 4) + (y >> 4)) * (P >> 5) + (x >> 5)`, inside as in 2D plus `(z & 3) << (6 + lb)`,
and the bank bits take `t = (y >> 3) ^ (z >> 2)` where 2D uses y bit 3/4.

Container: the general form is `n, 0x18, nslot, slot table, ngroup, group table` (`parse_groups`). Level arks have one
streamed container (Graveyard 9a649c89: 1478 entries, 739 slots, 1478 groups of one fixup each): group 2k holds the
resident low mips (16x16 or smaller) in the container, group 2k+1 has data size 0 and its texture (full size, same key)
in `<level>.stream.ark`. Fixups are `{entry offset, slot offset, offset in the group's data}`; group data follow each other
after the header. PC entry fields as for one-group containers (also for the streamed entries).

Streamed texture key (`stream_key`): the exe formats `"%s.%03d"` (string at 0xC18E54, used in FUN_007C3D60 at 0x7C3EDB)
with `<level>.streamtex` and the slot number, so the key is `h31("<level>.streamtex.%03d" % slot)` with slot =
(fixup slot offset - slot table offset) / 8. Every streamed slot has its entry, and together with the chunkless models
(key `h31(model key continued with ".stream")`) they are every `.stream.ark` entry of Graveyard (739 + 568 of 1307),
Quarry (436 + 187 of 623), nem_storm (168 + 45), powerplant (639 + 1252) and docks (914 + 890). The Xbox entry is the
raw Xenos texture, the PC entry `u32 size + DDS` (`convert_streamed`).

Results (Graveyard, `track_work\lvltest.py`): containers 54 identical + 6 equivalent, streamed textures 736 identical +
3 equivalent. The 9 non-identical ones are all wider-than-tall textures with a packed mip tail (256x32, 512x64 8888;
64x16, 64x8 DXT): the PC has different data in the tail levels. Ours follow Xenia's GetPackedMipOffset and each level is
a 2x2 box filter of the level before it (mean error 0.1-0.4 per channel for 8888); the PC levels are not (errors 22-140):
the PC exporter read these tails from the wrong place. Equivalent, not reproduced.

Quarry: 30 + 3 (resident_dlc) containers and 436 streamed textures convert.

## Coverage / open points
- Base cars use only DXT1 and DXT4_5, 2D, tiled, packed mips, square or taller-than-wide sizes (16x32 .. 1024x2048).
  Levels add 8888, cube, 3D and unpacked mips (see above). DXT2_3 (PC code 0x23 guessed) is untested; linear
  (untiled) textures, 2D arrays and other formats raise FormatError rather than guessing.
- Wider-than-tall packed tails cannot be compared with the PC (its data there is broken, see Levels).
- 3D packed tails: the levels that fit in the tail use the 2D offsets with z offset 0; all 17 Graveyard volumes come
  out identical with that, other volume shapes are untested.
- DLC (Deadline): all 32 files of MUSCLECAR_17/18/19 and TRUCK_09 convert (36 textures, all sizes/formats
  covered by the base test; MUSCLECAR_18 damage maps are empty containers). The 6 DLC textures whose keys also
  exist in base cars come out identical to the base PC textures.
