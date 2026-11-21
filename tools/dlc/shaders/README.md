# Shader files, Xbox 360 -> PC

`convshaders.py` converts a car's `.fxc`, `.shaders` and `.shaderinst` files. `test_shaders.py` checks
the method on the base cars of both `resident.ark` files (`../pair_lib.py`).

```
python -I convshaders.py <out dir> <xbox car dir> [<xbox car dir> ...] [--base-only]
python -I test_shaders.py [-v]
```

## Formats

All three files are streams of u32 values and strings: Xbox big endian, PC little endian. A string is
u32 length (with the NUL), the bytes, then `length & 3` zero bytes. That padding does not 4-align.

- `.fxc` (`\0FXC`): u32 version 2, u32 size value (unknown; the same on Xbox and PC, copied), u32 count.
  Each entry: str id, then the vertex shader and the pixel shader. Each shader is u32 n, then n
  constants (str name, u32 class, u32 register, u32 count), then u32 size and the code (Xenos microcode
  on the Xbox, D3D9 `vs_3_0`/`ps_3_0` with CTAB on the PC). The file ends with u32 `\0END`.
- `.shaders` (`SHDR`): u32 version 6, u32 count. Each entry: str id, `HDRB`, then 4 constant groups
  (u32 n, u32 registers used, n x (u32 register, u32 count, str name)) in the order VS global, VS
  material, PS global, PS material. Then 4 sampler groups (u32 n, n x (u32 register, str name)) in the
  same order, then u32 n vertex inputs (str name, u32 type, u32 usage, u32 index), `HDRE`, and 2 x
  (`SDRS`, u32 size, data, `SDRE`). The data is empty in all files. The file ends with `\0END`.
- `.shaderinst` (`SDRI`): u32 version 8, `INSS`, u32 count. Each instance: u32 hash, str name, str
  shader id, 2 constant groups (the material groups: u32 n, u32 used, n x (str name, u32 class, u32 reg,
  u32 count, count x 4 floats)), 2 texture groups (u32 n, n x (str name, u32 class, u32 reg, u32
  texture hash, u32 m, m x (u32 sampler state, u32 value))). Then `INSE`, and optionally `SMAP` followed
  by u32 values only.

PC variants: for each Xbox entry `0x<id>`, the PC `.fxc`/`.shaders` file has, in the same order,
`0x<id> 2x<id>`, or `0x<id> 1x<id> 2x<id> 3x<id> 4x<id>`. The five-variant form goes with deforming
shaders (vertex input `indices`, `DeformVertexPosition_*` constants) with 13 exceptions in 8249 occurrences. All
variants of an ID have the same metadata and constant tables, and only the code differs. The variant
list of an ID is the same in every PC file. Only `0x<id>` exists on the Xbox. The `.shaders` metadata
and the `.fxc` constant tables are the same on both platforms (4156 of 4156 car entries).

## Method

- `.shaders`: each Xbox entry is re-encoded little endian once per PC variant name. No PC data is
  needed except the variant list (from the PC files; for an unknown ID it falls back to the
  `indices` rule and reports "variant list guessed").
- `.fxc`: for each variant, the Xbox entry's constant tables plus the PC code of that entry name from
  the pool. The pool holds the PC base car bodies plus every other `.fxc`/`.shaders` in the PC
  `resident.ark` (1868 files, found by magic). Each entry name has the same code in every PC file. Only
  the constant names differ by context, for example `0xa222438e`.
- `.shaderinst`: walked by structure and re-encoded. Strings are kept as bytes.

## IDs no PC file has: translation (opt-in)

`convert_fxc(pool, data, shaders=None, translate=False, notes=None)` (and `convert_file(..., shaders, translate,
notes)`, CLI `--translate`): with `translate=True`, an entry name the pool lacks is made by `translate_entry`, which
calls `../xenos/xenos2pc.pc_entries` (Xenos microcode -> vs_3_0/ps_3_0, Xbox constant tables) once per ID and caches
it. `shaders` is the matching Xbox `.shaders` file (bytes or parsed): its vertex inputs feed the translator's
vertex-normal decode rule, and for an ID unknown to the pool the variant list then follows the `.shaders` rule, as in
`convert_shdr`. A VS, PS or VS->PS pipeline status other than `ok` is reported as a problem; `notes` gets one line per
translated ID. Without `translate` nothing changes: `test_shaders.py`, `../frontend/test_frontend.py` and
`../assemble/build_pack.py --verify-only` give the same output as before, and all 120 Deadline car shader files
convert byte-identically with and without `translate` (no fallback fires).

The level-model converter keeps the PC packing of level normals (DEC3N -> UBYTE4N, (v+1)/2), so the translator's
default decode applies. Checked on Graveyard (default rule vs no decode, translated VS against the PC VS): 154 main VS
get the decode; 89 of them match the PC only with it, 65 differ either way (detail fade-in etc.), none matches only
without it; 24 powerplay VS get it and differ either way. Quarry: `../assemble/assemble_track.py`.

## Test results (test_shaders.py)

- All 1868 PC `.fxc`/`.shaders` files parse and re-encode byte-identical. The pool has 1742 entry
  names, with 0 code or variant-list conflicts.
- Leave-one-out over the 40 base cars x 10 LODs: each car's PC file is rebuilt from its Xbox file and
  the other cars' (or all other PC files') entries.
  - `.shaders`: 400 of 400 byte-identical. 6 of them have IDs no other car has, so their variant list
    was guessed and still came out right.
  - `.fxc`: 394 of 400 byte-identical. The other 6 (musclecar_06 low/mid, supercar_05 high, truck_06
    low/mid/high) use 17 IDs that exist in no other PC file. Apart from those entries they are
    byte-identical.
- `.shaderinst`: 400 of 400 car files byte-identical. All 932 `.shaderinst` files in each archive
  round-trip.

## DLC

Deadline cars MUSCLECAR_17/18/19 and TRUCK_09: 120 files, all complete. Every Xbox ID was found. 8 of
417 entries come from PC files that are not car bodies: MUSCLECAR_18 `0x00b3239c` and `0xea7d6b2e` in
the bw/fw low/mid LODs.

The `.fxc` and `.shaders` ID lists match, and every `.shaderinst` shader ID is present. For every
entry, the pool entry's constant tables equal the Xbox ones.
