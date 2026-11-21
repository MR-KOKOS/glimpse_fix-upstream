# Car select ("frontend") body: Xbox 360 -> PC

`convfrontend.py` converts one or more Xbox frontend folders (`Vehicles/Frontend/Bodies/<car>/`) into the PC files the
game reads from `vehicles/frontend/bodies/<car>/`. `test_frontend.py` checks it. Offline only; nothing here starts the
game or writes to the game folder.

```
python -I convfrontend.py <xbox frontend folder> [...] --out <dir> [--pc <PC resident.ark>]
python -I test_frontend.py [--dlc <folder holding the Xbox frontend folders>] [--out <dir>] [-v]
```
Output: `<dir>\<car>\<car>.model`, `.model.stream`, `.textures`, `.streamtex`, `.fxc`, `.shaders`, `.shaderinst`,
`chassisname.txt` (lower case). Exit code 1 on any problem. Takes about 15 s, mostly to build the shader pool.
`test_frontend.py` takes about 90 s.

Dependencies: `../mdl.py`, `../convmodel.py` (strip_to_list, swap_words), `../tex/convtex.py`,
`../shaders/convshaders.py`, `../pair_lib.py` (needs `ark.py` from the older session scratchpad, like convshaders;
the test also needs the Xbox base `x360_resident.ark` named in `pair_lib.XB`), `../misc/arkio.py` (test only).

## Test oracle

The Xbox base-game `resident.ark` (`pair_lib.XB`) does contain the frontend files of all 40 base cars
(`vehicles/frontend/bodies/<car>/...`), so the converter is checked byte for byte against the PC files:

| file | result over 40 base cars |
|---|---|
| `.textures`, `.streamtex` | 40/40 byte-identical |
| `.fxc`, `.shaders`, `.shaderinst` | 40/40 byte-identical (pool without the car's own frontend files; 4 cars use an ID only their own PC file has, those use the full pool) |
| `.model` + `.model.stream` | 40/40 equal as the loader reads them: every byte equal except the buffer offset words, index lists and vertex bytes equal. musclecar_08: 2 vertices are all zero in the PC file (same exporter quirk as musclecar_05_high in-game) |
| `.model` + `.model.stream` bytes | 0/40: the PC files put the buffers in another order (see below) |
| `chassisname.txt` | 40/40 identical, copied |

Then 40 structural relations that hold in all 40 PC frontends (`check_pc`) are required of the converted DLC
frontends. Musclecar_17/18/19 and Truck_09 meet all 40.

## Formats

### .model (both platforms)
The in-game `.model` layout (`../NOTES.md`, `../convmodel.py`): u8 version 1, u8 flags, u16 8; PC only: u32 n + n bytes
platform block; vertex declarations; pointer fixups; u32 block size, pad to 16, main block; u32 chunk count pair.
Differences from the in-game models:
- The chunk count pair is (1, 1), i.e. no data chunk in the `.model`. All index and vertex data is in `.model.stream`.
- PC platform block: always 16 zero bytes (in-game PC models have buffer descriptors there).
- Index sub-records (0x20 bytes, Xbox `20000002 00000001 0 0 0 ffff0000 offset size`) and vertex buffer records
  (0x1C: fetch pointer, offset, size, stride, count, 0, 0) hold offsets into the stream data. The vertex record size
  field is stride*count rounded up to 16 on both platforms. All declarations use FLOAT16_2/4 only.
- PC index record +0x0C = offset << 2 | flags; flag 2 = 32-bit indices. The PC uses 32-bit indices exactly when the
  converted list has more than 0xFFFF indices (the values are still < 0x10000). In-game models never reach that.
- Draw records (x, index record, vertex record, primitive type): 6 (strip) -> 4 (list), as in-game.

### .model.stream
`u32 0, u32 data size, u32 0` (= file size - 12), then the data; offsets in the `.model` count from byte 12.
- Xbox: vertex buffers first (16-aligned), then the index strips (16-bit, big-endian, 0xFFFF restart).
- PC: all index lists first, then the vertex buffers, packed with no padding (vertex buffers may start at an odd
  multiple of 2), nothing after the last buffer. Index lists = the Xbox strips as lists (even triangles (i, i+1, i+2),
  odd (i, i+2, i+1), degenerate ones kept); vertices with 16-bit words swapped.
- Buffer order on the PC: index lists and vertex buffers are not in record order and follow no rule found (not count,
  first/min index, CRC/MD5 of the data, sub-record address); identical cars (truck_04/07/08) share the order, so it is
  deterministic in the exporter. The converter writes record order; the loader only uses the offsets.

### .textures + .streamtex
`.textures` is the TEXS/SXET container of `../tex/README.md` in a two-group form (in-game files are the one-group case):
```
00 magic, u32 0xC, 0, hs (header size from 0x10)
10 n entries, 0x18, nslot, slot table offset (0x18+0x40n), ngroup = 2, group table offset (slot table + 8*nslot)
28 n x 0x40-byte entries (as in-game)
   slot table: nslot x 8 zero bytes
   group table: 2 x {data size, 0, nfix, fixup table offset}
   fixups: {entry offset, slot offset (slot table + 8k), data offset}
0x10+hs: group 0 data, then the name table (C2NM / MN2C, the nslot keys)
```
n = 2 * nslot: each texture appears twice with the same key, once small in group 0 (8x16 or 16x16, resident in the
`.textures`) and once at full size in group 1 (256x512 .. 1024x2048), whose data size field is 0 and whose data offsets
point into `.streamtex`. `.streamtex` has no header: it is the group 1 data only.
- Xbox data: raw Xenos tiled textures, 4 KB-aligned (fetch constant base/mip addresses relative to each texture).
- PC data: `u32 size + DDS` per texture, packed, in fixup order, in both files; entries as in-game (format, mips,
  width, height, sRGB).
Each texture is converted with `convtex.xb_texture`; the container fields are swapped, the data offsets and the
group 0 data size recomputed.

### .fxc / .shaders / .shaderinst
Same formats and method as `../shaders/README.md`; the PC pool (all PC `.fxc`/`.shaders` of resident.ark) has every
frontend shader ID of the DLC cars except three, which are replaced (table `SUBST`):

| Xbox ID (DLC) | PC shader used | why |
|---|---|---|
| `0x14f9e572` VehiclePaintFast + ambient_occlusion_color_1, gbuffer pass | `0x8f95b125` VehiclePaintFast + VehicleEmissiveLight_1 + ambient_occlusion_color_2 | the base frontends' paint shader: same vertex inputs, sampler and pass constants; Xbox microcode similarity 0.95 (VS) / 0.75 (PS), best of all PC IDs. The PS adds `emissiveValues[lightIndex]` through a one-hot select of floor(lightIndex) in 0..3 |
| `0xf19dbb0a` same, sslighting pass | `0x4db9561b` (sslighting pass of the same shader) | microcode similarity 0.97 / 0.97; its PS does not use lightIndex at all |
| `0xc48f2d34` position only, no constants or samplers | `0x73a79516` | 9 PC IDs have identical metadata and byte-identical PC code (VS = position * TransViewProj, PS = white); this one has the highest microcode similarity (0.92) and is in 34 of 40 PC frontends |

For a replaced ID the PC entries (all variants, `0x`/`2x`) take the PC substitute's code, constant tables and `.shaders`
metadata under the DLC ID's name. The `.shaderinst` instances that use it are rewritten to the substitute's material
groups: `ambient_occlusion_color_1_*` values move to `ambient_occlusion_color_2_*` (registers 105/106 -> 106/107),
and `VehicleEmissiveLight_1_lightIndex` (register 105) is added as 4.0, outside 0..3, so the emissive term is 0 and
the result equals the substitute without emission. paintColour (104) and the diffuse sampler (s10) stay.
Musclecar_19 needs no replacement (its `.fxc`/`.shaders` come out identical to musclecar_07's).

## DLC results (Deadline)

| car | model | 32-bit lists | textures | replaced shaders |
|---|---|---|---|---|
| Musclecar_17 | 18 index lists, 3 vertex buffers (55922+1535+2024) | 1 | 4 (x2) | 3 |
| Musclecar_18 | 17, 3 | 2 | 4 (x2) | 3 |
| Musclecar_19 | 14, 3 | 1 | 4 (x2) | 0 |
| Truck_09 | 15, 4 | 3 | 4 (x2) | 3 |

All PC relations hold, vertex positions are within +-4.1, the textures decode (ffmpeg PNGs look right).

## Open points
- Tested in the game with the Deadline cars on 2026-11-20; the High Octane and Elite cars not yet.
- Buffer order in `.model.stream` differs from what the PC exporter would write (loader uses offsets).
- The paint shader replacement assumes the DLC paint shader is the base one without the emissive term (supported by
  the names and the microcode similarity, not proven); the two DLC paint IDs' Xenos code was not translated.
