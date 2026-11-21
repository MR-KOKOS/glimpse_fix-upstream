# Havok packfiles: Xbox 360 -> PC (convhkx.py)

Split/Second's Havok 5.5.0-r1 binary packfiles (`.phys`, `.maincoll`, level collision; magic `57e0e057 10c0c010`)
are the same structure on both platforms; only the endianness differs (layout rules `04 00 00 01` vs `04 01 00 01`),
and the PC files carry a `__types__` section (hkClass reflection) that the Xbox files leave empty.

## Method
1. `learn` reads class layouts (hkClass, hkClassMember, hkClassEnum) from the `__types__` sections of PC
   packfiles (`.hkx`, or the Havok entries of `.ark` archives directly) and the hkClass registry of the PC exe.
   The exe holds 375 classes; 111 of them also appear in PC packfiles and match exactly (the exe tables just lack the
   member enum pointers, which are set at run time). Everything goes into `hkref.json`: 381 classes, 16 enums.
2. `convert` walks every `__data__` object from the virtual fixups with its class layout (inherited members,
   embedded structs, C arrays, hkArray/simple arrays via the local fixups, pointers to non-virtual structs, strings)
   and swaps each member by type: u16, u32/float/pointer, u64. u8/bool/char data such as MOPP code is left as is.
   Gaps between blocks are 0x00 on the Xbox and 0x7F on the PC.
3. What the PC tool chain did (the PC files went through Havok's XML format): every real goes through `%f`
   (6 decimals), bools become 0/1, and hkTransform has w = 0 in the three rotation columns and translation.w = 1
   (XML stores 3 numbers per column). `--exact` skips this step. The column w's matter for level files: the Xbox
   `hkMotionState.transform` columns hold leftover SIMD lanes there (e.g. col1.w = col2.x); every PC copy has 0.
4. Fixup tables: swapped. The global table gets the hkVariant class pointers into `__types__` (sorted by source) and
   is 16-aligned. The Xbox writer sometimes reserves more space here than it uses.
5. `__types__` is generated, not copied:
   - Classes are written depth-first: the class, then its parent chain, then per member its standalone enum and
     its class. The roots are the object classes in post-order of the object graph, starting from the contents
     object.
   - Each class block holds the hkClass, its name, its declared enums (an unpadded array; each enum's name, item
     array and item names padded to 16) and its members (an unpadded array, then the names padded to 16).
   - Local fixups are in target order; global and virtual fixups are sorted.

## Usage
With `GAME_DIR` set to the Split/Second game folder (`set GAME_DIR=<...>\steamapps\common\SplitSecond`; the test
script reads it too):
```
python -I convhkx.py learn "%GAME_DIR%\data\*.ark" "%GAME_DIR%\data\environments\levels\*.ark" ^
    "%GAME_DIR%\SplitSecond.exe"      (about 10 s)
python -I convhkx.py convert MUSCLECAR_17.PHYS musclecar_17.phys
python -I convhkx.py convert <xbox dir> <out dir>        (converts every file that starts with the packfile magic)
python -I convhkx.py classes <file>                     (class names and __data__ objects)
python -I test_convhkx.py ark [--types] [--learn] [-v]   (base pairs; --types also rebuilds all PC __types__)
```
`hkref.json` is read from the game (SplitSecond.exe, the PC archives), so it is not shipped and not in git:
`build_all.py` runs `learn` on the user's game folder into `<work>\hkref.json` before the car pack and sets
`GLIMPSE_HKREF` to it, which `convhkx.REF` (and so `assemble_car.py` / `assemble_track.py`) reads; without the
variable `convhkx.py` uses `hkref.json` next to itself (development, the tests; make it with the `learn` line
above). On the Steam build the generated file is byte-identical to the one the converters were tested with
(2026-11-21; `build_all.HKREF_TESTED` holds the hash of its classes and enums).

The test reads the Xbox/PC `resident.ark` through `../pair_lib.py` (which imports `ark.py` from an old scratchpad)
and the NEM_STORM level archives. These paths are hard-coded at the top of the test.

## Results (test_convhkx.py ark --types)
- `__types__` rebuilt byte-identical for **195 of 195** PC packfiles in all PC archives.
- 88 Xbox/PC pairs: 40 cars x (`.phys`, `.maincoll`) plus the 8 Havok entries of NEM_STORM.
  - **44 byte-identical**: all 40 `.maincoll` and 4 level files.
  - **44 differ only in members holding uninitialised tool memory** (`convhkx.VOLATILE`): 40 `.phys` and 4 level
    files, 1 to 214 bytes each. Every other byte matches.
  - The volatile members:
    - `hkpCollidableBoundingVolumeData` min/max/expansion. The Xbox has 0xFFFFFFFF or leftover string bytes
      such as texture paths; the PC has different garbage.
    - `hkpMotion.savedQualityTypeIndex` plus 2 padding bytes.
    - one `hkpExtendedMeshShapeTrianglesSubpart.triangleOffset` (Xbox 0x41aaa310, PC 0x41aaa300).

  These cannot be derived from the Xbox file. Havok recomputes the bounding volume data at run time. The converter
  keeps the swapped Xbox values.
- Graveyard (`GRAVEYARD.ARK`, 10 pairs; `track_work\lvltest.py`): 3 identical, 7 equivalent. Besides the members above,
  `VOLATILE` now also lists
  - `hkpWorldObject.userData` and `hkpPhysicsSystem.userData`: exporter heap addresses (16-byte aligned, 0x17xxxxxx to
    0xbfxxxxxx, a few hundred bytes apart between the two builds; files that were not re-exported for the PC have the
    same values on both). The game sets its own user data after loading.
  - `HavokAnimatedSystemExportUserData.id`: export-time object number (0x773 Xbox, 0x772 PC in 25b0976d and d506b96c).
    The Xbox value is kept; it belongs to the other Xbox files of the level, which are converted together.
  The hkMotionState.transform differences (666 to 2347 bytes per file before) are gone with the column w rule.
  The car results are unchanged (44 identical, 44 volatile).
- In every converted Xbox file (base and DLC), the bytes not described by any member are zero, so nothing is left
  unswapped.

## DLC
- Deadline cars MUSCLECAR_17/18/19 and TRUCK_09 (`.PHYS`, `.MAINCOLL`) convert cleanly. They use only classes that
  the PC base cars use, with the same class list as the base cars.
  - Check: walking the little-endian output again gives the same member map as the input, and all floats are finite.
  - Output: `scratchpad\dlc\conv\havok\deadline\<CAR>\`.
- Onslaught `QUARRY.ARK`: all 8 Havok entries convert (also with the column w rule; the 8 Havok XML packfiles have no
  PC counterpart in any base level and are left out, as the Graveyard PC archive lacks the Xbox-only ones).
  - 7 of them use only classes known from PC packfiles.
  - One uses `HandlingModifierBox*ExportUserData*`. No PC packfile has these classes, but the PC exe registers
    them, so their layouts come from the exe. The values look plausible (speedMax 70, speedMin 55,
    acceleration 0.25).
  - Test output: `scratchpad\dlc\conv\havok\quarry_test\`.

## Open issues
- The DLC outputs have no PC reference, so they are only checked structurally. Loading in the game: tested with
  the Deadline cars and Quarry on 2026-11-20; the High Octane and Elite cars not yet.
- Classes taken only from the exe have no member enum pointers in `__types__`. The three HandlingModifierBox
  classes have no enum members, so this does not matter there.
- Not implemented (unused in all files seen): hkHomogeneousArray, in-place arrays, class defaults/attributes,
  packfile exports/imports.
