# assemble: one Xbox DLC car -> one PC overlay archive

(DLC tracks: `assemble_track.py`, see `README_track.md`.)

`assemble_car.py` collects everything the PC game reads for one Deadline car, converts it with the existing
converters (none of them is changed), merges the shared text files, and writes one PC `.ark` overlay. The overlay
is meant for the virtual resident.ark in `../inject` (DESIGN.md): an overlay key replaces the base key, every
other key is added. Offline only. Nothing here starts the game or writes to the game folder.

```
python -I assemble_car.py [--model Musclecar_17[,Musclecar_18,...]] [--dlc <Deadline package dir>] [--pc <PC resident.ark>] [--out <overlay.ark>]
```
Several models (comma list) go into one overlay; see "Several cars in one overlay" below.
Defaults: model `Musclecar_17`, the session scratchpad's `dlc\x_deadline`, `G:\...\SplitSecond\data\resident.ark`, and
`scratchpad\dlc\overlay\musclecar_17.ark`. Next to the `.ark` the script writes `<name>.manifest.tsv` (key, size,
`replaces`/`new`, converter, game path, source, check result per entry) and `<name>.report.txt` (merges, checks,
skipped and missing files). It takes about 15 s. It sets `sys.dont_write_bytecode`.

Dependencies: `../misc` (arkio, convmisc), `../tex/convtex.py`, `../havok/convhkx.py` + `hkref.json`,
`../shaders/convshaders.py` (pool from the PC resident.ark through `../pair_lib.py`, which imports `ark.py` from an
older session scratchpad), `../convmodel.py` + `../mdl.py`, `../audio/tonic.py`, `../inject/dlcmerge.py`,
`../inject/vark.py`.

## What goes in

The car comes from the DLC `VehicleDatabase.xml` record whose `ModelFile` is the model (`Placeholder_09` for
`Musclecar_17`). Game paths are the package paths in lower case.

| part | source | converter |
|---|---|---|
| every file of `Vehicles/Bodies/<model>/` | DLC | `.model` convmodel, `.textures` convtex, `.phys`/`.maincoll` convhkx, `.fxc`/`.shaders`/`.shaderinst` convshaders (PC pool), the rest `convmisc.detect` (deform, parts, text); a `.params` block header without its `:` (only the Elite pack's `Unique_21/Coronas.params`) gets the colon (`fix_params_headers`, logged): the PC reader 0xABC460 takes a path as a block header only with `:` (then 0x41D1A0 sets the block), otherwise it drops it and the key after it, and the car keeps default coronas |
| `Vehicles/Handling/Configs/<HumanPhysicsFile>.params` and `.rdata`, `<ModelFile>.rdata` | DLC | convmisc text; `.rdata` is the default `swap4` (40 of 40 base cars give the PC file). The exe loads the `.params` by HumanPhysicsFile/AIPhysicsFile but the `.rdata` by ModelFile (0x8C3590: `%s%s.rdata` with record+0x28 at 0x8C37B2; +0x28 = ModelFile, serializer 0x7FACD9), so the Elite cars (ModelFile Unique_21..23, HumanPhysicsFile Unique_01/02) also get their `UNIQUE_2x.RDATA` |
| `Vehicles/Lights/<model>_Lights.params`, `Vehicles/Frontend/Bodies/<model>/ChassisName.txt` | DLC | convmisc text |
| `Audio/Tonic/_StateEngine/<AudioFile>.xml`, the bundle files it names that the PC lacks, `_OutputFiles/<AudioFile>.tonic/.tonic2` | DLC | convmisc text, tonic.convert |
| `Vehicles/VehicleDatabase.xml` | PC + the one DLC record | `dlcmerge.merge_vehicles` with only that record (the other Placeholder records stay as on the PC) |
| `Vehicles/VehicleColors.params`, `Vehicles/Compositing/GameCompositingInfo.params` | PC + the DLC blocks `<root>/<model>[/...]` | `merge_dump` here: the blocks are inserted byte for byte as one group before the first PC car that sorts after the model, and the PC blocks are kept unchanged |

Tonic bundle files that are byte-identical in the PC resident.ark (`_CarTypeSpecific/*.xml`) are left out. All PC
files and DLC blocks are ASCII with CRLF and no BOM, and the merges keep that.

## Checks (all in the script, any failure means exit code 1)

- The overlay is read back with arkio: little-endian, `hsize = 0x10 + 20n`, keys sorted ascending and unique,
  all entries type 0, and the data equal to what was built.
- It is merged with the real resident.ark through `vark.VirtualArk`, and every overlay entry plus base samples
  are read through `GameArkReader`.
- Per entry, the converters' own parsers run on the output:
  - models: `mdl.Model` + `convmodel.semantic`, index range, decls and fixups;
  - textures: `convtex.parse`/`textures`, DDS equal to the decode of the Xbox data;
  - Havok: re-walk of the output, the same member map as the input, `__types__` rebuilt identically, finite reals;
  - fxc/shaders: re-parse and rebuild to the same bytes;
  - shaderinst and tonic: round trip back to the Xbox bytes;
  - deform: layout walk;
  - parts/rdata: swap4 inverts;
  - text: CRLF only.
- Keys that replace resident.ark keys must be exactly the merged text files.
- References: every shaderinst texture key is found in the car's textures or a PC `SXET` container (index
  scan). Every shaderinst shader ID is in the same LOD's `.fxc`/`.shaders`. The cameras and the AI physics file
  of the record are in use by PC cars. The per-car paths the exe builds are listed when no source has them.

## Several cars in one overlay: build_pack.py (Deadline pack)

All cars of a pack touch the same shared files: `VehicleDatabase.xml`, `VehicleColors.params`,
`GameCompositingInfo.params`, `directory.txt`, `UI/Frontend/Flash/FE_vehicleSelect.gfx`,
`Flash.textures`/`.filenames`, the six dictionaries, `UI/InGame/VehicleSelect/VehicleSelect.gfx` and
`UI/InGame/Textures/InGame.textures`/`.filenames` (16 files). Per-car overlays would each bring their own copy and
replace each other's, so the pack is built as ONE overlay:

```
python -I build_pack.py [--models Musclecar_17,Musclecar_18,Musclecar_19,Truck_09] [--dlc ..] [--pc ..]
    [--frontend <frontend_out>] [--out <overlay\deadline.ark>] [--check-single Musclecar_17=<musclecar_17_full.ark>]
    [--verify-only]
```
1. `assemble_car.py --model A,B,C,D`: one overlay with every car's files; the three merged text files get all
   records/blocks (each car's dump blocks go in front of the first PC block that sorts after it, one car after the
   other, so the result does not depend on the order). A file two cars name (Tonic bundle) goes in once.
2. `finish_car.py ... --model A --model B ...`: every record gets ID = ModelFile in its own Placeholder slot,
   CarSelectOrder = max PC order + 1, + 2, ... in the `--model` order; each name appended to VEHICLES; the frontend
   files (`--frontend` holds `<model>.<ext>` or `<model>/<model>.<ext>`); one frame per car in both car sprites
   (`../ui/gfxcars.py`, one call for all cars, so an atlas several cars show is copied once); each DLC atlas once
   under `FE_VSelectDLC1`, `FE_VSelectDLC2`, ... (`--image-prefix`, then `../ui/flashtex.py`); the same for the
   in-game car select: one frame per car in the picture sprite of `VehicleSelect.gfx`, its atlases once under
   `FE_carSelectDLC1`, `FE_carSelectDLC2`, ... in the resident in-game set `InGame.textures` (see "In-game car
   select" below; `--no-ingame` leaves this out); names and descriptions (`cartext.py`) for all cars in one
   `dictadd.add_checked` per dictionary.
3. Checks, then `<out>.manifest.tsv` (key, size, replaces/new, car, maker, path, source) and `<out>.report.txt`:
   archive read-back and the `vark` merge with the real resident.ark; every entry accounted for, the replaced keys
   are exactly the 16 shared files; records (slot, ID, order, all other attributes as the DLC record, the other
   records and the text between them unchanged, orders 1..N without gaps); dump blocks byte-identical to the DLC's,
   PC blocks unchanged and in order, tweakable paths carry the car's own model name; VEHICLES = PC + the cars;
   dictionaries (every new key resolves, PC entries unchanged, texts = the rule); gfx (round-trips tag by tag, PC
   tags and frames unchanged, one new frame per car in both sprites, each new frame shows what the DLC frame shows:
   name texts and atlas cell); Flash set (name order, PC textures unchanged, keys unique, no PC atlas replaced; new:
   added names new to every PC UI texture set, added keys in no texture container of resident.ark, and each new
   frame's picture cell has the same DXT blocks in the pack's Flash set as in the DLC atlas); in-game
   `VehicleSelect.gfx` (round trip, every PC tag incl. all ActionScript kept, PC frames unchanged, one new frame per
   car, a frame for every record with CarSelectOrder > 0 but Musclecar_Ghost, which has none on the PC either, each
   new frame shows the DLC frame's atlas cell and its DXT blocks, found in the in-game sets, equal the DLC's);
   `InGame.textures`/`.filenames` (the PC file's lower-case name order, PC textures unchanged, added exactly the
   images the new frames show, keys unique and in no PC container, names new to every PC UI set, DXT1 as the PC's
   own DXT1 image `smalltex`); trucks against Truck_01. `--check-single` rebuilds a car alone (with
   `finish_car.py --no-ingame`, the version the references were made with) and requires it byte-identical to its
   reference, and the pack's entries of that car that are not shared files byte-identical too.

`assemble_car.py` and `finish_car.py` with one model give the same bytes as before (checked: Musclecar_17 alone ->
`musclecar_17.ark` and `musclecar_17_full.ark` byte-identical); since the in-game car select, `finish_car.py` needs
`--no-ingame` for that.

Descriptions (`cartext.py`): "FROM THE DEADLINE PACK." + the three stats the car select shows, banded 9-10
EXCELLENT, 7-8 GOOD, 5-6 AVERAGE, 0-4 LOW, highest first (ties: speed, drift, strength); Accel/OverallStat left out.
It gives Musclecar_17's line of the first build back (checked).

### Result for Deadline (2026-11-20)

| car | Xbox ID (slot) | ID / order | name (DLC plate) | picture: FE_VSelect05 -> FE_VSelectDLC1, cell (px) |
|---|---|---|---|---|
| Musclecar_17 | Placeholder_09 (70) | Musclecar_17 / 29 | COBRETTI CENTAUR | x 0..161, y 112..239 |
| Musclecar_18 | Placeholder_10 (71) | Musclecar_18 / 30 | RYBACK JAVELIN | x 160..322, y 112..239 |
| Musclecar_19 | Placeholder_08 (69) | Musclecar_19 / 31 | RYBACK COYOTE SPECIAL | x 323..485, y 113..240 |
| Truck_09 | Placeholder_11 (72) | Truck_09 / 32 | HANZO KANOBO | x 0..158, y 246..373 |

FE_VSelect05 is a 3-column atlas of the seven Xbox DLC cars (Placeholder_05..11); every DLC frame places the whole
image and the picture sprite's clip mask (depth 1) shows one cell, so one atlas serves all four cars. Order after
Musclecar_17: the DLC's own order (Truck_09 39, Musclecar_17 40, Musclecar_18 41, Musclecar_19 16) taken
cyclically from Musclecar_17. `deadline.ark`: 297 entries (71 + 70 + 71 + 72 per car, 13 shared), all checks pass.

Truck_09 needs nothing different: VEHICLES lists the PC trucks like the cars; its palettes (10 blocks) and sticker
slots (32) have Truck_01's sub-paths; the car select sprites have no class frames (the badge shape, Hanzo, maps to
the PC's own); its cameras and AI file are Truck_01's; every body file has a Truck_01 counterpart (incl.
`Coronas.params`). Musclecar_18 has no `Vehicles/Lights/Musclecar_18_Lights.params` in the DLC; it is left out as
on the Xbox. Its reader (0xACE010) fills the light block with defaults first and parses the file only if it opens
(`Resident(noassert)|`), as for the PC's own Musclecar_Ghost, which has none either; the missing `Coronas.params`
of the three musclecars is read the same way (0xACCEA0). `ActionScriptimage` is how all 84 PC records spell it too;
the attribute lookup (0xA424E0 -> 0x530970) compares lower-cased, and the car select does not use the field
(+0x194). Spanish strength words are FUERZA, the label of the Spanish car select (TEXT_STRENGTH).

### In-game car select (VehicleSelect.gfx)

Where the PC uses it: the in-game UI setup (0x79AA70) creates screen 0x54 "Car Select" from
`Resident|UI\InGame\VehicleSelect\VehicleSelect.gfx` (class `InGameUI::cVehicleSelect`, 0x809E00) only with one
local player (split screen loads `RaceEnd_SplitScreen.gfx` instead) and only when the session flag `[0xD64E40]+8`
is set (the same block creates the CarUnlock movies; the in-game event select clears it on quitting to the front
end). It is opened from the in-game event select (`FE_InGameEventSelect2.gfx`, input 0x80E790): choosing an event
that has no fixed car (event `+0x110` = 0) opens 0x54, otherwise the event's car is taken. So it is the car choice
for the next event inside the career, between races. Everything else (front-end car select; the lobby's "CHANGE
CAR" is a front-end menu, `cPCProfileSelectionDynamicMenuConfigObject`) goes through `FE_vehicleSelect.gfx`, as far
as the exe's strings show (no other movie names cars, see below).

Capacity: 0x809850 appends each listed car's ID pointer to an inline array at +0x280 with room for 30 and no bounds
check. Entries 31 and 32 land on +0x2F8 (written only by the constructor 0x809E00) and +0x2FC (read by no method of
the class); a 33rd would overwrite the flag at +0x300 and the timer at +0x304 (0x8095C0).
What fills it (corrected 2026-11-20, see "Car lists and their capacities" below) is not every record with
CarSelectOrder > 0 but the unlock manager's list 1, which holds only cars of `UI/Logic/Season.xml` (at most 3 + 20 +
4 = 27 on the PC). DLC cars added to the database never reach it, so the pack's car count is not bounded by it;
build_pack.py checks the Season.xml bound instead (`ingame_list_check`).

How it shows a car: on activation (0x80A350 -> 0x809850) the exe calls `AddVehicle(ID, TEXT_<ID> text)` per car of
the list (records, 0x1BC bytes each); the ActionScript (classes `VehicleSelectController`, `VehicleItem`,
`Vehicle`, byte-identical in the PC and DLC files) sets the name text field and sends the car's picture sprite
(sprite 19: a clip mask at depth 1, an atlas picture at depth 2) to the frame labelled with the ID. A missing label
leaves the sprite where it is, so without a frame a DLC car shows another car's picture. The stats bars and the
region badge (`euro`/`us`/`jap`/`elite` frames) are driven from the record (DLC: Euro, US, US, US); the description
line is `TEXT_CARDESCR_<ID>` (0x80A200). Both texts are in the pack's dictionaries already.

PC file against the DLC's: the PC sprite has 27 frames (the base cars with CarSelectOrder 1..27; Musclecar_Ghost
has none), the DLC's 38 (+ Placeholder_01..11). The DLC file's only other change is a `dlcIcon` with
`ShowDLCIcon`/`HideDLCIcon` in the root script, which the PC exe never calls (the names are not in it); it is not
added. The DLC shows Placeholder_09/10 on its `FE_carSelect_set3`, a 1024x1024 atlas, while the PC's
`FE_carSelect_set3` is 1024x256 with other cars, and Placeholder_08/11 on `FE_carSelect_set4` (1024x256, not on
the PC). `gfxcars.py` therefore maps a bitmap-filled shape only to a PC shape with the same images (name and size)
and keeps the DLC matrix for a copied picture; both rules leave the front-end output byte-identical.

| car | DLC frame | picture: Xbox atlas -> PC name, cell (px) |
|---|---|---|
| Musclecar_17 | Placeholder_09 | FE_carSelect_set3 -> FE_carSelectDLC1 (1024x1024), x 320..645, y 730..979 |
| Musclecar_18 | Placeholder_10 | FE_carSelect_set3 -> FE_carSelectDLC1, x 644..969, y 730..979 |
| Musclecar_19 | Placeholder_08 | FE_carSelect_set4 -> FE_carSelectDLC2 (1024x256), x 0..323, y 0..242 |
| Truck_09 | Placeholder_11 | FE_carSelect_set4 -> FE_carSelectDLC2, x 323..647, y 0..244 |

The atlases: the Xbox has them in its streamed set InGame3 (`InGame3.textures` + `.streamtex`, the two-group
layout of `../frontend/README.md`: a small copy in group 0, the full one in group 1 with its data in the
`.streamtex`). `flashtex.py --stream` takes the full-size copy and converts it with `convtex.xb_texture`. They go
into the PC's resident in-game set `UI/InGame/Textures/InGame.textures` (TextureSet 4, loaded with the in-game UI
next to the streamed sets InGame1/2/3 and Hud), not into the PC's streamed InGame3: the movie already takes its
images from InGame, InGame2 and InGame3 (the loader 0x533910 registers every image by name, without `_DXT5`/`_RAW`),
the resident set is the one-group layout `flashtex.build` rebuilds byte for byte, and a streamed set has a
streaming budget (0x539350) whose size for two more atlases is unknown. Cost: 640 KB more resident memory in game.
Both stay DXT1 as on the Xbox (the PC's UI sets have DXT1 images: `smalltex`, ingame2, the CarUnlock sets), named
without a suffix as the PC names its DXT1 images (a `_DXT1` suffix would not be stripped), one-level DDS header
(flags 0x1007, caps 0x1000). `InGame.textures` is sorted by lower-case name (`_` before letters, e.g.
`FE_episode_ICONS` before `FE_episodePoster_icoglow`), unlike `Flash.textures` (upper-case); `flashtex.py` keeps
whichever order the PC file has, and the DXT4/5 code the set uses (0x25 DXT5 in Flash, 0x24 DXT4 in InGame).

Result: `deadline_v2.ark` (2026-11-20), 300 entries, 98,848,768 bytes, all checks pass: the 297 entries of
`deadline.ark` byte-identical plus `ui/ingame/vehicleselect/vehicleselect.gfx` (63,306 -> 63,789 bytes, 6 new
definitions: 2 images, 2 exports, 2 picture shapes), `ui/ingame/textures/ingame.textures` (38 -> 40 textures,
1,755,986 -> 2,411,810 bytes) and `ui/ingame/textures/ingame.filenames` (+2 names). The four cells, decoded, show the four DLC cars. Not tried
in the game.

Other per-car UI the exe looks up by ID (strings with `%s` in the exe, every Flash movie of resident.ark scanned for
car IDs): only `FE_vehicleSelect.gfx` (44 IDs) and `VehicleSelect.gfx` (27) name cars, and both have the DLC cars
now. Texts: `TEXT_<ID>` (record +0x16C: in-game car select, HUD, results, objectives) and `TEXT_CARDESCR_<ID>` are
in the dictionaries; `UI/Frontend/ColorCubes/%s` comes from a dirlist, `TEXT_%s_NAME` are screen titles. The one
per-car file nobody has is `UI\InGame\CarUnlock\<ID>\CarUnlock_<ID>.gfx` (+ `Textures` set), the "car unlocked"
screen of the career: the exe checks that it exists and otherwise loads `CarUnlock_placeholder.gfx` (0x79B3CA..).
On the PC 32 records have one, the three starter cars (Musclecar_01, Supercar_04, Truck_03) and Musclecar_16,
Supercar_10..16, Truck_08 have none; the DLC cars are `Locked="false"` like the starters and the Xbox DLC has none
either, so nothing visible is missing.

Install the pack INSTEAD of a single-car overlay of the same cars, never next to it: the file layer
(`src/dlcfs.cpp`) lays every `glimpse_dlc\*.ark` over resident.ark in name order and a later one replaces the keys of
an earlier one, so `musclecar_17.ark` next to `deadline.ark` would bring back its own copies of the 13 shared files,
and the other three cars would be missing from the database, the compositor list and the car select. The pack holds
Musclecar_17 byte-identical (non-shared entries). The same holds for the larger packs below: install ONE of
`deadline_v2.ark`, `pack_v3.ark`, `pack_v4_elite.ark`, never two.

## Car lists and their capacities (static RE, 2026-11-20)

| list | filled by | capacity | holds |
|---|---|---|---|
| unlock manager (object at `[0xD64E48]`, ctor 0xA05410) | 0xA05820 from `UI/Logic/Season.xml`; rebuilt by 0xA05FE0 before every use | 6 vectors of 0x100 entries (0x400 bytes each) | list 0 (+0x24): InitialCars; list 2 (+0x0C, from +0x04): InitialCars + unlocked basic/elite + the profile's DLC-unlock cars; list 1 (+0x3C, from +0x34): InitialCars + unlocked basic/elite only |
| in-game car select, screen 0x54 (0x809850) | manager list 1 (0xA05360(1)) | 30 (+0x280, no bounds check) | Season.xml cars only: 3 + 20 + 4 = 27 at most |
| front-end car select `Frontend::cVehicleSelect` (ctor 0x472B90, screen 0x2D, vtable 0xBAEC5C, slot 25 = 0x473F80 -> 0x4734A0) | manager list 2 (list 1 in the career, `[0xD64E40]+8`), or with "UI/Vehicle Select/All Vehicles Unlocked" (0xD6F8F0, the DLL's `UnlockAll=1`) or `game_setup/use_dev_menus` (0xD71A34) every record with Locked (+0x1A0) != 1 and Player (+0x1A2) != 0 | 0x100 (+0x13C/+0x140/+0x144, 0x472CB5) | PC 28 + the DLC cars |
| colour palettes (0x41E810) | every record | heap, record count | - |
| vehicle compositor (0x422FD0) | directory.txt `VEHICLES` | 64 entries of 0x580 | PC 50 + DLC |
| vehicle database | VehicleDatabase.xml | the PC's 84 records; DLC cars take the Placeholder_01..20 slots, so no per-record table grows | - |

- Season.xml (0xA05820): `InitialCars` -> +0x1C/+0x20; `UnlockableBasicCars` -> 20 slots +0x6C (credits +0x14C),
  unlocked by 0xA05600; `UnlockableEliteCars` -> 4 slots +0xBC (+0x19C/+0x1AC/+0x1BC), 0xA056C0; `UnlockableDLCCars`
  -> +0xCC; no reader found in the manager's code (0xA05000..0xA07000) besides the loader. 0xA05600 and 0xA056C0 are SecuROM-virtualised (they jump to 0x2178BF3 /
  0x2178C3A, outside the dump), so whether they skip a car that is already unlocked is not visible; 0xA05760 (the
  profile's DLC-unlock cars, list 2 only) does ("Trying to unlock car %d but it is already unlocked").
- The profile's 16 DLC-unlock flags (`DLCUnlocks%02d/isUnlocked`, 0xA062D0; profile bytes `[0xD64E20]+0x2B0`) map to
  hard-wired PC bonus cars in 0xA05E30; flags 10 and 12 call 0xA057C0 + 0xA057F0 (every basic and elite car through
  0xA05600/0xA056C0 again).
- CarSelectOrder (+0x1B8) only sorts the lists (0xA053D0). The PC exe never reads `DLCRestricted`.
- Consequence: the DLC cars of a pack are listed only by the front-end car select, and there only with All Vehicles
  Unlocked (`UnlockAll=1`). Putting them into Season.xml (as the third-party port does: 14 InitialCars) would put them
  into list 1 too and overflow the in-game array (3 + 8 + 20 + 4 = 35 > 30 with Deadline + High Octane). A DLL way
  without code patches: swap vtable slot 25 of 0xBAEC5C, call the original 0x473F80, then append the DLC records'
  indices to the screen's list (+0x144, counts +0x140 and +0x138) and send `AddCarToMiniMenu` for each as 0x4734A0
  does (0x536AC0 behind the 0x472020 check). Built as `src/dlccars.cpp`, `[DLC] Cars=1` (default 1); not tried in
  the game yet.

## Several packages in one overlay: merge_dlc.py, pack_v3 (Deadline + High Octane)

The Xbox car packages are cumulative in their UI (each brings the whole DLC car select with frames for the DLC cars
released so far; Deadline, the last, has all eleven Placeholder_01..11 in both movies and the atlases FE_VSelect03/05,
FE_carSelect_set3/4). `merge_dlc.py --base x_deadline --add x_highoctane --out x_pack3` makes one source folder for
the tools: the base whole, from every add package only `Vehicles/**` and `Audio/**` (a file both have must be
byte-identical), VehicleDatabase.xml and the two tweakable dumps merged; it checks that the base's frames show every
add car as its own package does (texts, images, cells, DXT blocks). One difference was found and is listed in
`x_pack3\MERGE_REPORT.txt`: the in-game picture of RYBACK VULCAN (Supercar_19) has a retouched hood badge in the
Deadline package's FE_carSelect_set3 (32 of 5166 blocks); the base's (later) art is used, as on an Xbox with all packs.

```
python -I convfrontend.py <x_highoctane>\VEHICLES\FRONTEND\BODIES\SUPERCAR_17 ... --out frontend_out_ho   (test_frontend: 40 relations hold, ALL OK)
python -I build_pack.py --models Musclecar_17,Musclecar_18,Musclecar_19,Truck_09,Supercar_17,Supercar_18,Supercar_19,Supercar_20
    --dlc x_pack3 --frontend frontend_out_v3 --out overlay\pack_v3.ark
python -I compare_packs.py overlay\deadline_v2.ark overlay\pack_v3.ark
```
build_pack.py changes for this (the Deadline-only pack verifies as before): the FE atlas renaming is derived
(FE_VSelect05 -> FE_VSelectDLC1, FE_VSelect03 -> FE_VSelectDLC2 in first-use order), the description names the pack
(`cartext.PACK_OF`/`PACKS`), the 32-car check became `ingame_list_check` (Season.xml), and the manifest attributes
`vehicles/` paths by their own model (Supercar_17's AudioFile is "Supercar_20"). `compare_packs.py` checks that a
larger pack keeps an earlier one: all non-shared entries byte-identical, shared files equal once the added cars are
taken out, the old cars' frames show the same (gfxcars numbers copied characters anew, so ids differ).

| car | Xbox ID (slot) | ID / order | name | FE picture (FE_VSelect03 -> FE_VSelectDLC2) | in-game (set3 -> FE_carSelectDLC1) |
|---|---|---|---|---|---|
| Supercar_17 | Placeholder_01 (62) | Supercar_17 / 33 | RYBACK CYCLONE SPECIAL | x 323..485, y 243..370 | x 0..322, y 231..481 |
| Supercar_18 | Placeholder_02 (63) | Supercar_18 / 34 | COBRETTI SEVERUS | x 0..160, y 367..494 | x 322..647, y 232..482 |
| Supercar_19 | Placeholder_03 (64) | Supercar_19 / 35 | RYBACK VULCAN | x 162..324, y 367..494 | x 645..970, y 233..483 |
| Supercar_20 | Placeholder_04 (65) | Supercar_20 / 36 | HANZO KATANA (+ PC badge FE_vehSelBadgeJap) | x 324..486, y 368..495 | x 0..322, y 487..737 |

Result `pack_v3.ark`: 587 entries, 160,706,560 bytes, all build_pack checks pass, compare_packs against
`deadline_v2.ark` passes (284 non-shared entries byte-identical). Added over deadline_v2: 71-72 entries per supercar,
FE_VSelectDLC2 in Flash.textures; InGame.textures unchanged (set3 is already there). AudioFiles Supercar_20/21/24/26
and handling Supercar_17..20 are new to the PC. Supercar_18 has no Coronas.params (read with noassert, as for the
musclecars). VEHICLES 58 of 64. Not tried in the game.

Optional `pack_v4_elite.ark` (+ "The Elite" Vehicle Livery Pack, package 13B21D5F..., the newer of the two copies):
Unique_21 ELITE 440 BRN, Unique_22 ELITE S510 RP, Unique_23 ELITE S510 VX (Placeholder_05..07, orders 37..39). They
are new records (own bodies, using the base elite cars' handling Unique_01/02, whose .params the package ships again
byte-identical: assemble_car.py now leaves such a handling file out). 785 entries, 197,996,544 bytes, all checks pass,
compare_packs against pack_v3 passes. VEHICLES 61 of 64.

## Result for Musclecar_17 (2026-11-20)

67 entries: 64 new, 3 replacing (VehicleDatabase.xml, VehicleColors.params, GameCompositingInfo.params). The file
is 10,856,448 bytes and all checks pass. The overlay has not been tried in the game.

Left out, see the report for details:
- The frontend (car select) model `Vehicles/Frontend/Bodies/Musclecar_17/*.model`, `.model.stream`, `.streamtex`
  and `.textures`: the existing converters reject them (convmodel IndexError, convtex "unexpected header", and
  there is no converter for streamed data). Its `.fxc`/`.shaders` lack 3 shader IDs in the PC pool.
- Car select image and name: they are hard-wired per car ID in the Xbox `FE_vehicleSelect.gfx` and
  `VehicleSelect.gfx` ActionScript (Placeholder_01..11, `FE_VSelect05`, `FE_carSelect_set4`, `dlcIcon`). The PC
  GFX files lack all of this, and there is no GFX editor. `DLC.textures` converts, but nothing on the PC would
  load it. (Since done by `finish_car.py` with `../ui/gfxcars.py` and `../ui/flashtex.py`, see above.)
- `UI\InGame\CarUnlock\<car>\CarUnlock_<car>.gfx` exists for neither name. The base car Musclecar_16 has none
  either, and the exe has `CarUnlock_placeholder.gfx`.
