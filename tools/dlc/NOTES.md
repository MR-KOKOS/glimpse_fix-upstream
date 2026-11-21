# Xbox 360 DLC on PC: probe notes (2026-11-20)

Goal: point the settings program at the user's own Xbox DLC folder; convert and load without any game code
change (data writes, import/vtable slot swaps, calls into game functions, D3D hooks only).

## Packages (user's dump, 425607E7/00000002, STFS LIVE, contents unencrypted)
Deadline (vehicles MUSCLECAR_17-19, TRUCK_09, mode TimeTrialPlus), Onslaught (track Quarry, Nemesis
helicopter, KingOfTheAir), Survival At The Rock, High Octane, two livery packs, Master Unlock
(DLC/Unlock/Unlock.params: DLCUnlocks12/isUnlocked). Reader: stfs.py (all files consecutive).

## PC exe
No DLC loader (no DLCINFO/TRACKDESCRIPTION parsing). Has DLCUnlocks%02d/isUnlocked, UnlockableDLCCars,
"Resident|directory.txt". DLC cars/tracks are not in the PC archives (key search, keyfind.py).

## Formats, Xbox vs PC (pair.py, cmp.py, arkcmp.py; musclecar_16 and NEM_STORM pairs)
- .ark: same format, big-endian on Xbox; key = h*31+c over the lower-case path.
- params/txt/xml: identical or nearly.
- .parts: plain 4-byte swap. .deform, .shaderinst: same size, mixed swaps (need structure).
- level binaries: 84-100 % equal after a 4-byte swap.
- Havok (.phys, .maincoll, level collision): Havok-5.5.0-r1, same layout rules except endianness; __data__
  identical in size and fixups; PC files add a __types__ section (class reflection) that tells how to swap.
- Shaders: Xbox .fxc/.shaders are Xenos microcode, but every DLC car shader ID (27 of 27 for MUSCLECAR_17)
  exists in PC base cars: substitute the PC versions.
- Textures: TEXS container; Xbox has a Xenos fetch constant instead of the DDS header, tiled, DXT data
  16-bit swapped. Convertible with the known untiling.
- .model: BLOCKER. PC loader 0xA36B20 (stream reader vtable: +0x20 read, +0x24 u8, +0x28 u16, +0x2C u32,
  +0x1C skip, +0x34 tell): version u8, flags u8, u16, u32 block size, vertex declarations (8 bytes + count +
  count*0x14-byte elements, FUN_005082a0), buffer records of 7 dwords (offset, count, stride, chunk, done),
  index records of 0x10 bytes, then data chunks (FUN_00a354b0 / FUN_00a36460 create the buffers). PC xlow:
  0x15A vertices x 0x10 stride = 0x15A0. The Xbox file has none of these numbers: a different
  serialization (likely compressed vertex formats, own buffer split). Needs the Xbox loader (default.xex,
  PowerPC) or a statistical comparison over many pairs.
- .model, decoded (modelparse.py; Xbox code module SPLITSECOND1.DLL in Ghidra via XEXLoaderWV 12.1.3):
  u8 version 1, u8 flags, u16 8; PC only: u32 n + n bytes "platform block" (buffer descriptors, read
  only when [renderer+0xB50] == 5, i.e. skipped otherwise: vertex count, stream sizes and offsets into the
  data chunk, e.g. 15a 1 15a0 2e74 ad0 4414 ad0 4ee4); u32 vertex declaration count, each 8 bytes + u32 n +
  n x (stream, offset, D3DDECLTYPE, usage, usage index) - identical on Xbox and PC (FLOAT16_4/FLOAT16_2);
  u32 n + n u32 pointer fixup offsets (identical); u32 block size, pad to 16, main block (same size, 92.6 %
  equal after a 4-byte swap, the rest u16/u8 fields); then chunks (u32 count pair, index, size, data).
  Data chunk differs: PC = 16-bit index list first (0x2E74 bytes) + 3 vertex streams (0x15A0, 0xAD0, 0xAD0);
  Xbox = vertices first, much smaller index part (likely strips) and one stream fewer.
  Test oracle: convert the Xbox copy of a BASE car and compare with the PC file byte for byte.
  CONVERTER (convmodel.py) DONE: default 4-byte swap of the main block; vertex buffer records (0x1C:
  Xbox fetch-header pointer -> 0, data offset), index records (0x10: Xbox sub-record pointer -> 0, list
  count, offset<<2|flags), Xbox index sub-records (0x20) kept as raw bytes except the offset at +0x18,
  draw records (0, index rec, vertex rec, prim type 6 -> 4); data chunk: index lists first (strips with
  0xFFFF restart -> lists, even (i,i+1,i+2), odd (i,i+2,i+1), degenerate triangles kept), then the vertex
  buffers with 16-bit words swapped (all car decls are FLOAT16_2/4); PC-only platform block left empty
  (only read when [renderer+0xB50] == 5: no INTZ/RAWZ and no R16F vertex textures).
  testall.py: 399 of 400 base car models equal as the PC loader reads them; the one left (musclecar_05_high)
  has one vertex zeroed in the PC file. All 10 MUSCLECAR_17 models convert and parse.
  Tools here have hard-coded scratchpad paths (old session's x360tools/ark.py for LZSS, the Xbox resident).
- Audio: XMA in FSB, needs conversion (or PC engine sounds as a stand-in).

## Loading without code changes (plan)
Hook the game's kernel32 file imports (CreateFile/ReadFile/SetFilePointer/GetFileSize) to present a
resident.ark with extra index entries for converted files; new tracks as their own .ark in
data/environments/levels; merge VehicleDatabase.xml, directory.txt lists; set DLCUnlocks tweakables.

## Level models (convmodel.convert_level, 2026-11-20; convert() above is unchanged for cars)
Checked on every Graveyard model pair (disc GRAVEYARD.ARK, GRAVEYARDPOWERPLAYS.ARK, .static/.stream sector arks against
the PC files; scratchpad track_work\lvltest.py): main 44/44, static 568/568 with a chunk + 568/568 chunkless (with
their .stream.ark entry) byte-identical, powerplays 322/425 identical; the other 103 powerplay models are identical up
to a PC-only part (below). Rules (comments in convmodel.py):
- Main block as for cars. Mesh records sit at the pointer fixups (word 0 = 0x88888888, +0x40 = draw record); a table
  of 0x20-byte material records {mesh, 0, declaration index, hash} names the declaration per mesh (powerplay meshes can
  have a second, shorter pass declaration).
- Vertices: each material record in table order converts the words its declaration covers for the vertices its index
  list references, later records overwrite earlier ones; a declaration of another stride than the buffer only takes
  part if all its elements have the offset and PC type of an element of the same mesh's own-stride declarations.
  Unreferenced vertices are zero on the PC. FLOAT1-4/UBYTE4N u32 swap, FLOAT16 u16 swap, DEC3N (type 14) -> UBYTE4N
  (8), PC = trunc((n + 1) * 127.5) with n = normalize((2v + 1) / 1023) per component (exact for all normals; a zero
  word gives 201,201,201).
- Data: index lists, then vertex buffers, each in the order of first use in the material record table; 32-bit lists
  when longer than 0xFFFF. PC platform block 16 zero bytes. Chunkless models: data in .stream.ark under the model key
  continued with ".stream", entry u32 0, size, 0 + data (as the frontend .model.stream).
- PC-only, not reproduced: 103 Graveyard powerplay models have a real platform block (100..1852 bytes) and 0x8000 to
  0x28000 extra data bytes after the vertex buffers: half-float vertex-texture streams (per vertex position half4 +
  a second half4, then streams of (1,0,0,0)), laid out by the platform block. The PC loader reads the platform block
  only when [renderer+0xB50] == 5; our output has the 16 zero bytes the other models have.
  Mode 5 (renderer init FUN_0050d340, the only writer of [0xD64708]+0xB50): CheckDeviceFormat picks the depth-texture
  path, 1 INTZ, else 2 RAWZ, else 3 DF24/DF16, else 0; with 3 the game asks for D3DUSAGE_QUERY_VERTEXTEXTURE on
  A16B16G16R16F and sets 5 when that fails (a DF-only GPU without float vertex textures, Radeon X1000 era; 800x600
  default). In mode 5 the loader keeps the platform block (FUN_00a352d0) and copies the half-float streams into
  A16B16G16R16F textures (FUN_00a36240); the powerplay deformation then runs on the CPU (FUN_00ac4e10 etc.). Any GPU
  with INTZ (all current NVIDIA/AMD/Intel; the user's RTX 5090) runs mode 1, where the block is skipped and the extra
  bytes are never read, so the 103 models are equivalent as loaded. Not generated.

## Converters (2026-11-20, all offline, verified against base-game Xbox/PC pairs; README in each folder)
- convmodel.py (.model): 399/400 base car models equal as the PC loader reads them; level models see above.
- tex/convtex.py (.textures): 320/320 files, 658/658 textures byte-identical (Xenos untiling, packed mips); levels:
  8888, cube, 3D, streamed container and .stream.ark textures (key h31('<level>.streamtex.%03d' % slot), exe
  FUN_007C3D60 "%s.%03d"), Graveyard 54/60 containers and 736/739 streamed identical, the rest equivalent.
- havok/convhkx.py (.phys, .maincoll, level collision): 44/88 byte-identical, the rest differ only in
  fields holding leftover exporter memory in both originals; __types__ rebuilt from hkref.json.
- shaders/convshaders.py (.fxc, .shaders, .shaderinst): 400/400 .shaders and .shaderinst, 394/400 .fxc
  (the 6 use shader IDs found in no other PC file); PC code taken from the PC files by shader ID.
- misc/convmisc.py (.deform, .parts, level data kinds, text with CRLF) and misc/arkio.py (.ark reader and
  writer, rebuilds nem_storm.static.ark byte-identically).
- audio/ (Tonic 60/60 byte-identical; FSB XMA -> MPEG Layer II via vgmstream + ffmpeg, all banks load in
  the game's FMOD; FEV unchanged). DLC3_Music, DLC3_nem_helicopter, DLCMenu are never requested by the PC exe.
- inject/DESIGN.md: virtual resident.ark via kernel32 import slots (ReadFile, GetFileSize, SetFilePointer,
  CloseHandle), installed in DllMain; new track archives as plain files; cars replace Placeholder_08..11.
  Open: six code paths loop over a fixed 15 tracks (Xenon.dll patched them in code; we cannot).
