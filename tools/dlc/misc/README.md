# misc: Xbox 360 -> PC converters for the small formats, and an .ark writer

Covers the car `.deform` / `.parts` / text files and every level-ark entry kind except textures, Havok, shaders
(.fxc/.shaders/.shaderinst) and models. Those are left to their own converters (`detect()` reports them as
`foreign`). Offline only: Python, `python -I`.

Files:
- `convmisc.py` holds the converters and kind detection. Detection uses the name, then the key (level-relative
  names are the same in every level), then the content.
- `arkio.py` reads Xbox and PC .ark files (LZSS included) and writes PC .ark files.
- `testmisc.py` is the test oracle. It converts base-game Xbox files and compares them with the PC copies.
  It has this machine's paths hard-coded.

## Usage

    python -I convmisc.py auto <xbox file> <out>            # detect + convert one file
    python -I convmisc.py <kind> <xbox file> <out>          # kinds: python -I convmisc.py kinds
    python -I convmisc.py ark <xbox.ark> <outdir> [--stream]
        # writes <outdir>/<key>.<kind> for every entry handled here; other kinds go to <outdir>.foreign.txt
        # (key, kind, size) so the other converters can drop their <key>.<ext> files into <outdir>
    python -I arkio.py pack <outdir> <out.ark> [--nopad]     # PC ark from <key>.<ext> files or relative paths
    python -I arkio.py list|extract ...
    python -I testmisc.py [cars] [level] [pp] [dlc] [ark]

## .ark layout (same on both platforms, Xbox big-endian)

`u32 0x00010000, u32 hsize (=0x10+20n), u32 n, u32 0x10, u32 0, u32 hsize`, then n index entries
`{type, offset, stored size, size, key}` sorted by key, then the data, packed back to back from `8+hsize`.
Type 0 is stored. Type 1 is LZSS: a `u32 size` prefix, then the stream (4 KiB window, start 0xFEE). In some
PC stream-ark textures the index size equals the stored size, so read the prefix instead. Key =
`h*31+c` over the lower-case relative path. Main and resident arks are zero-padded to 0x800. Sector arks
(.static/.stream) are not padded and keep their data in index order.

The writer stores every entry (type 0), as PC .static arks do. Rebuilding `nem_storm.static.ark` from its
own entries gives a byte-identical file.

## Rules and results

Test basis:
- 40 base cars from the Xbox and PC resident archives.
- nem_storm: `NEM_STORM.ARK` and its `.static`/`.stream` sectors from the Xbox disc against the PC copies.
- Xbox `NEM_STORMPOWERPLAYS.ARK` against the PC `*powerplays.ark` files.
- DLC check: a converted Onslaught entry that is byte-identical to some PC base entry counts as verified.

| kind | rule | result |
|---|---|---|
| `.parts` | 4-byte swap | 40/40 identical |
| `.deform` (`DFM\2`) | stream from the PC loader 0xAD6380: u32 header[8]; verts f32x4; tris u16x3; edges {u16,u16,f32,u8,u8}; points {name[32] raw, f32[32], u32, u32}; panels {name[32], 26 u32}; boxes {name[32], 17 u32}; names[32] | 40/40 equal except exporter garbage (see open issues) |
| car text (`chassisname.txt`, `*.params`) | unchanged | 159/159 identical |
| XML (`<?xml`, `<Root`), `!directory:` tweakable dumps | LF -> CRLF when the Xbox copy has no CR (only the line ends differ) | 47 (incl. plain text that is already CRLF) + 44 powerplays identical; DLC: 66 in PC |
| path lists (`Environments/Levels/...` dependency lists) | lower-case, keep LF | 2/2 |
| NUL-separated names (`LightRigNames.names`, `ColorCubes/dirlist`) | unchanged | 2/2 |
| `.entities` (`ENTS`) | 7 u32 header; {cstring, u32 n, n bytes of f32} | 1/1 |
| `LightRigs/<rig>/<rig>.gbx` | u32 n; {u32, u32 len+chars, u32, u32 len+chars, f32[12]} | 1/1 |
| track sideways links | u32 n; {u8 c, c x u16} | 1/1, DLC 6 in PC |
| `i` lists (route/powerplay names) | u8 'i', u8 n; {u16, cstring, u8} | DLC 4 in PC (no base pair has entries) |
| AI racing line (u32 sizes 0x50,0x100,0x34,0x30,8,0x10) | sections A..F by header sizes and counts, swap4 except u8 fields at A+0x44, B+0xF0, D+0x28, D+0x2C, F+0x0C (kept); {u8,u8,u16} at C+0x0C | 1/1; Graveyard 2/2 |
| particle pools (v1 0x240 / v3 0x2E0 records: Standard.data ...) | 9-u32 header; records swap4 except id[32]@0x1FC, name[32]@0x21C, bool bytes @0x18 (v3 also 0x240/0x258/0x260); other `01 00 00 00`-like words kept as bools, except at +0x154 | 4/4 identical, plus Standard.data with the same records in another order |
| id lists (v2 `.emitterControllers`, v5) | u32 ver, u32 n, n x {id[40], name[40]} raw, rest swap4 except embedded strings | 5/5; Graveyard 13/13 |
| node list (`q000`...; level-specific name) | 3 u32; n x {0x4C raw (name + LE pointers), 456 B swap4}; tail swap4 | 1/1 equal except pointer words; Graveyard the same |
| powerplay placement lists (`.cat` names) | u32 n; n x {u32 hash, u32 len + chars, 12 floats kept big-endian, u32, u32}; the PC keeps the 3x4 matrix floats big-endian (Graveyard 84c8868f); files that do not have this layout fall back to the tokenised rule (u32 3..128 + name chars is a string, all else u32) | Graveyard 1/1 |
| everything else (`.lights`, `.emittersurfaces`, link distances, emitter records, u32 hash lists ...) | 4-byte swap except embedded strings and `01 00 00 00` bool words (both kept, see below); 19 nem_storm keys are verified | 19/19; Graveyard 43/43 main + 425/425 powerplays; DLC 46 in PC (+11 Xbox-only nem_storm powerplay hash lists) |

Embedded strings (`string_words`): emitter records in swap4 data and in the data part of id lists have char buffers
(`1268686511.1578698973`, `DEB_Rockfall_fence_debris_S`, `game8`) that the PC keeps byte for byte. A string is a run of
`[A-Za-z0-9_./ -]` that starts on a dword boundary, ends with a NUL and is >= 8 characters long, or >= 5 with a
lower-case letter. Big-endian floats almost never give that (their first byte is the exponent, 0x3F/0x40-0x4F for usual
values). In the same records the word `01 00 00 00` (a bool byte plus padding; as a u32 it would be 16777216, as a float
2.4e-38) is kept as well; every such word in the Graveyard swap4 files is kept on the PC.

Text detection: a file of which more than 1/8 of the bytes are >= 0x80 is not text. This moves short u32 hash lists
without a zero byte (powerplay entries like `f4c2e6ba daa46034`) from 'text' to the 4-byte swap; Graveyard: 35 of them,
all now identical.

nem_storm total: 85 of 85 non-foreign entries in the main ark convert (83 identical, 2 with the
differences above). The other 123 main-ark entries, the 90 `.static` models and the 213 `.stream` chunks
(streamed textures and model data) are foreign. The 6 Xbox-only main-ark entries are Havok XML packfiles.

Graveyard (`GRAVEYARD.ARK` and `GRAVEYARDPOWERPLAYS.ARK` from the disc against the PC files; `track_work\lvltest.py`):
all misc kinds identical except
- the node list (exporter pointer words only, equivalent),
- 8 particle pools: the same records in another order (equivalent, as Standard.data),
- text: 35 powerplay sequences differ only in `Effect_EventIDString`/`Effect_EventID` (export-time ids, regenerated
  for the PC build; the Xbox ids match the other Xbox files, equivalent); 2 files differ in content: the PC sequence
  list 20c51a8b has one more line (`PP09_Exploding_Silo_2`), and in b10e71a7 the PC has an empty `CameraName` besides
  new event ids. These are PC build changes, not conversion errors.
- one PC-only entry (a81ac3d8, a `!directory: /Cam...` tweakable dump); 10 Xbox-only entries (9 Havok XML packfiles,
  1 Havok binary).

## Open issues / not reproducible

- `.deform`: the bytes after the NUL in the 32-byte name buffers, and point word b when a == -1
  (0xFFE16C28 on Xbox, 0xFFDA447A on PC), are uninitialised exporter memory and differ per build. The
  loader ignores them. The Xbox bytes are kept.
- The node list: the pointer words in each record's raw 0x4C header (+0x08, +0x10, +0x38 ...) are heap
  addresses that differ per build. They are kept.
- `Standard.data`: the PC pool holds the same 9 records, but 4 are in another order. The PC order cannot be
  derived; the records are looked up by id/name, so the order should not matter. This is not verified in-game.
- Not verifiable without a PC pair: the `i` lists with entries (the u16 is LE on PC, checked on PC base
  files), the length-prefixed powerplay streams, and DLC variants of AI lines and pools (same layout,
  other values).
- Two blobs in both DLC `RESIDENT_DLC.ARK` files (keys 7c68a456 and 9cce8f36; `u32 3, hash, count, count`
  plus byte/bit data, one of them not 4-aligned) have no base counterpart. They are converted with the
  default 4-byte swap, which is probably wrong. Their format is unknown.
- `.stream.ark` entries are converted by `../tex/convtex.py` (streamed textures, key `<level>.streamtex.%03d`) and
  `../convmodel.py` (`convert_level`, model data, key = model key continued with `.stream`).
- The pool and AI-line bool offsets were learned from data, not from the loader. A bool that is 0 in every
  base sample is only covered by the `01 00 00 00` guess in pools; in AI lines it is not covered at all.
