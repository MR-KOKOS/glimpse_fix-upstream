# Xenos microcode -> D3D9 SM3 (Split/Second shaders, Xbox 360 -> PC)

Translates the Xbox 360 shader code in Split/Second `.fxc` entries into PC `vs_3_0` / `ps_3_0` bytecode. This
is for the shader IDs that no PC file has (89 of the 522 IDs of the DLC track Quarry). Offline tooling only.

```
python -I xenos2pc.py <out dir> <xbox .ark|.fxc> [...] [--missing-only] [--ids a,b] [--game <PC data dir>]
python -I validate.py collect <work dir>     # read the Xbox / PC archives (read only) into <work>/all.pkl
python -I validate.py run <work dir> [id]    # translate every shared ID, compare, write validate.txt
python -I test_xenos.py <work dir> [--full]  # self tests + a sample (or, with --full, validate run)
```

Requirements: Python 3 with numpy, and `D3DCompiler_41.dll` in System32, which comes with the DirectX
runtime. It is loaded with ctypes and is the compiler the PC shaders were built with (9.26.952.2844). If it
is missing or fails, `fxc.exe` from the Windows SDK is used instead.

## Files
| file | content |
|---|---|
| `ucode.py` | Xenos microcode decoder and disassembler: control flow, ALU vector+scalar, vertex and texture fetch. Bit layouts are ported from rexglue/Xenia `ucode.h` |
| `xshader.py` | Xbox shader container: header, big-endian CTAB, literal constant table, vertex elements, interpolators, SQ_PROGRAM_CNTL / CONTEXT_MISC, microcode |
| `xlate.py` | the translator: Xenos -> HLSL -> SM3 bytecode, and a PC CTAB rebuilt from the Xbox CTAB |
| `d3dcomp.py` | `D3DCompile` from a legacy D3DCompiler DLL through ctypes |
| `sm3.py` | SM3 bytecode parser, disassembler, CTAB reader/writer |
| `xinterp.py` | vectorised Xenos interpreter (numpy). Semantics from rexglue/Xenia `interpreter.cpp`, with per-lane control flow |
| `sm3interp.py` | vectorised SM3 interpreter (numpy): 2x2 quads for dsx/dsy; if/loop/rep/break/call, predication |
| `xcompare.py` | runs both with random inputs keyed by constant name, semantic and sampler name, then compares outputs |
| `validate.py` | leave-one-out validation on all IDs that exist on both platforms |
| `xenos2pc.py` | converter API and CLI: all PC variants of an ID, self check, VS->PS pipeline check |
| `test_xenos.py` | tests |

## Formats (found here)
- Container (`0x102A1101` VS / `0x102A1100` PS): virtual part (headers) + physical part (literals, then
  microcode). Layout: `xshader.py`. The CTAB is a normal D3DX constant table in big endian. Its registers equal the
  `.fxc` entry tables in all 13494 checked shaders.
- Literal constants: records `(u16 register (+256 for PS), u16 float count, u32 offset)`, normally
  c252..c255. They become HLSL literals.
- VS vertex elements: bits 0-11 = slot of the vfetch, 12-15 = D3DDECLUSAGE, 16-19 = usage index. The vfetch
  format, stride and offset in the microcode are placeholders patched by the runtime. Only the semantic is
  used; D3D9 does the format conversion.
- Interpolators: usage index, usage, register (VS export o#, PS input r#), mask. The export patch list after
  them (instructions per interpolator) is for runtime VS/PS linking. It is not needed, because both sides
  are matched by semantic.
- PS param gen: SQ_PROGRAM_CNTL bit 18 enables it, SQ_CONTEXT_MISC bits 8-15 give the register. It holds
  (VPOS.x with the sign bit set for back faces, VPOS.y, 0, 0) and is rebuilt from VPOS/VFACE.
- Samplers: PS tf# = s#; VS tf# = 16 + s# (vertex textures s0..s3).
- One container version is unknown: `0x102A1110/1111`, a single ID (0x905f38e8 / 0xd9c2e2d4).

## Method
- Each Xenos register becomes a float4 HLSL local. The other state becomes HLSL variables too: the
  previous-scalar register `pv`, the predicate `p0` and `a0`.
- Each ALU instruction evaluates its co-issued vector and scalar operation from the old values and then
  writes them, with exports including the constant 0/1 components. A predicated instruction is wrapped in
  `if (p0 == cond)`.
- Supported control flow: exec, predicated exec, and forward predicated/unconditional jumps (as if-blocks).
  Loops are not supported. They only occur in 48 resident system PS such as tiled lighting.
- Constants stay at their Xbox registers. These are the `.fxc` table registers the game binds. VS
  a0-relative access uses a `float4 C[256]` array.
- Texture fetches:
  - plain `tfetch` -> `tex2D`
  - `tfetch` with an LOD bias, or a register LOD on top of the computed one -> `tex2Dbias`
  - `tfetch` without the computed LOD, and every VS fetch -> `tex2Dlod`
  - register gradients (`setGradientH/V`) -> `tex2Dgrad`
  - `getGradients` -> `ddx/ddy`
  - cube maps -> `texCUBE`. The direction is rebuilt from the Xenos face coordinates
    `(sc/|2ma| + 1.5, tc/|2ma| + 1.5, face)`.
  - 3D textures -> `tex3D`.
- Compiled with D3DCompiler_41. The PC CTAB is rebuilt from the Xbox CTAB (names, sets, registers, counts,
  types) and replaces the compiler's own table.
- Self check after every translation: the Xbox microcode (xinterp) and the bytecode (sm3interp) run on the
  same inputs (2 seeds x 256 lanes).
- **D3DCompiler optimiser bug**: all of D3DCompiler_41, _43 and _47 (fxc 10.1) compile some HLSL to code with
  rotated vector components. `test_xenos.py` has a 10-line repro:
  `normalize(v.yzw)` comes out as `.yzx`. The translator writes permuted same-swizzle dot products unpermuted,
  which avoids most cases. When the self check still fails, it recompiles without optimisation; that was
  needed for 1 Graveyard PS.

### PC conventions applied
- **Reversed depth.** The Xbox renders with a reversed depth buffer. Scene-depth reads (sampler names with
  `SceneDepth` or `ztex`) get `d = 1 - d`. Without this, 39 of 39 soft-particle PS and the
  blend/motion-blur/DOF depth reads disagree with PC; with it they match.
- **RAWZ** (`2x` variants, GeForce 6/7 depth textures). Depth samplers (`SceneDepth`/`DepthTex`/`ztex`) are
  decoded with `dot(t.wxy, (255/256, 255/65536, 255/2^24))`, exactly as in the 73 PC 2x shaders that do this.
- **Vertex normals on level geometry.** The PC data stores them as D3DCOLOR (v+1)/2 and the PC shaders decode
  `*2-1`. The Xbox shaders read signed values directly. Default rule, `xenos2pc.vertex_decode`: decode the
  elements the VS uses as normals (their value reaches a product with `NormalTrans`), if the `.shaders` input
  list has a D3DCOLOR TEXCOORD input.
  - It matches the PC shaders for 905 of 927 checked VS: no false positive, 22 decodes missed.
  - Cars (FLOAT16 data) never need it.
  - This depends on how the level `.model` converter writes the vertex data. `pc_entries(...,
    vin_decode=[(usage, index), ...])` overrides it.
- **Too many interpolators.** vs_3_0 allows 10 interpolators plus the position. Larger pairs are packed: only
  the components the PS reads, first-fit, the same map on both sides. This happens for 1 Quarry ID
  (11 -> 7 registers).

### PC variants (from the PC exe, FUN_0050d340 / FUN_005105f0)
At start-up the game picks a depth-texture path and loads `<n>x<id>`. A missing variant falls back to `0x`.

| n | path |
|---|---|
| 1 | INTZ |
| 2 | RAWZ |
| 3 | DF24/DF16 |
| 0 | none |
| 5 | DF without R32F; there is no `5x`, so it falls back to `0x` |

So current GPUs use `1x` for deforming/skinned shaders (the five-variant form) and `0x` for the others. Corpus
comparison:

| PC variant | relation to the others |
|---|---|
| `1x`, `3x`, `4x` | identical |
| `2x` | VS = 1x; PS has `_pp` removed and RAWZ decoding |
| `0x` of a 5-variant ID | VS without vertex-texture deformation or skinning; PS = 1x |

`xenos2pc` produces:
- `0x`/`1x`/`3x`/`4x`: the translation
- `2x`: the RAWZ PS
- `0x` of a deforming shader: the same deforming VS as 1x. The PC version compiles deformation out, and it is
  only used on GPUs without INTZ/RAWZ/DF.

## Validation (`validate.py run`, xenos_work\validate.txt)
Every ID that exists on both platforms was translated from its Xbox code. 1282 IDs: 72 car, 267 Graveyard,
266 Graveyard power plays, 31 Nem Storm, 646 resident/system.
- *fidelity*: translated bytecode vs the Xbox microcode, which tests the translator.
- *truth*: translated bytecode vs the PC bytecode of the variant the game uses (1x, else 0x).

| set | stage | translated | fidelity ok | truth ok |
|---|---|---|---|---|
| cars | VS | 72/72 | 72 | 71 |
| cars | PS | 71/72 | 71 | 64 (7: emissive-light term differs in the PC source) |
| Graveyard | VS | 267/267 | 267 | 64 + 107 with the vertex-normal decode = 171 |
| Graveyard | PS | 267/267 | 267 | **267** |
| Graveyard power plays | VS | 266/266 | 266 | 120 |
| Graveyard power plays | PS | 266/266 | 266 | **266** |
| Nem Storm | VS | 31/31 | 31 | 16 |
| Nem Storm | PS | 31/31 | 31 | **31** |
| resident | VS | 645/646 | 645 | 549 |
| resident | PS | 532/646 | 530 | 427 |

Causes of the remaining truth differences (both platforms' sources differ):
- **Detail fade-in** (239 VS). The PC VS writes `2*(fadeRange >= 0) + clamp(fade, 0, 2)`, which switches the
  dithered fade-in off. The Xbox VS writes the raw fade. Both PS dither-kill the same way.
- **Emissive lights** (19 PS).
- **Different constant sets**: 76 shaders (47 VS + 29 PS) whose PC version reads different constants, e.g.
  vertex-texture skinning or `TiledLighting` only on Xbox.
- **Other**: about 130, mostly resident system shaders (deferred lighting with `*360` textures, MSAA, post
  effects).

Not translated, all resident system PS: getCompTexLOD 56, loops 48, PS relative constants 7, unnormalised
coordinates 2, texel offsets 1, unknown container 1.

## Quarry (`xenos2pc.py ... --missing-only`; xenos_work\quarry)
- All 89 Quarry-only IDs translate. The VS and PS self checks pass, and so does the VS->PS pipeline check
  (Xbox VS->PS vs translated VS->PS by semantic).
- `translated.fxc` is one PC little-endian `.fxc` with 202 entries: 81 x (0x, 2x) + 8 deforming x (0x..4x).
- `status.txt` has the per-ID status. `hlsl\` has the generated sources.
- The vertex-normal decode was applied in 76 IDs. 38 IDs use Detail_FadeIn, so they fade in like on the Xbox
  rather than being switched off as on PC.

## Hooking into convshaders.py (done: `convshaders.convert_fxc(..., translate=True)`, see ../shaders/README.md)
The plan it follows:
In `convert_fxc`, when `pool.fxc.get(n)` is None:
```python
import xenos2pc                      # tools/dlc/xenos
ents, st = xenos2pc.pc_entries(e, vin=<vin list of the same ID from the car/level .shaders>)   # once per e
pe = {x['id']: x for x in ents}.get(n)   # entries carry the Xbox constant tables + translated code
```
- Pass the `.shaders` entry's `vin`, for example by handing the parsed `.shaders` file to `convert_fxc`.
- `st['vs']`/`st['ps']`/`st['pipeline']` say whether the result can be trusted. Report `!= 'ok'` as a problem.
- The variant names agree with `Pool.pc_names`' guessed rule.
- For level meshes, check with the level `.model` converter whether it keeps the D3DCOLOR (v+1)/2 normals
  that PC levels use. If not, pass `vin_decode=[]`.

## Known limits / open
- Not supported: loops (aL/integer constants), getCompTexLOD/getWeights, texel offsets, unnormalised
  coordinates, PS relative constants, vfetch_mini, bool `cexec`/calls. None of these occur in the 89 Quarry IDs.
- All checks run on interpreters. The SM3 interpreter agrees with the PC bytecode on thousands of shaders, but
  it is not a GPU run. Implicit-LOD texturing is compared without LOD; explicit LOD and bias are included.
- Random inputs: lanes where either side is NaN/inf are excluded (Xenos `0*inf = 0` vs IEEE) and counted.
- Detail fade-in follows the Xbox, not the PC switch-off.
- The deforming `0x` VS is not stripped of deformation.
- Centroid sampling flags are not reproduced. PC PS declare some `_centroid` inputs.
- The vertex-normal decode is a rule (22 misses on the base set) and must agree with the level-mesh conversion.

## Attribution
`ucode.py` and `xinterp.py` port the instruction formats, opcode tables and semantics of the rexglue SDK
(Tom Clay, 2026), derived from Xenia (Ben Vanik and the Xenia contributors) and freedreno (Rob Clark). They
are BSD licensed; the licence is in `LICENSE-rexglue.txt`. No rexglue code is called at run time.
