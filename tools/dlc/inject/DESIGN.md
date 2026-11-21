# Converted Xbox DLC visible to the PC game, without code changes

Static analysis of `re/SplitSecond_dump.exe` (Steam build, image base 0x400000) and an offline prototype.
Nothing here has run inside the game.

## Recommendation

1. **Virtual resident.ark.** Hooks on file reads present `data\resident.ark` as the original plus one or more
   overlay archives: a merged index in memory, original data read from the original file, new data read
   from the overlay files. The overlays are ordinary PC `.ark` files that the converter writes. They hold
   the converted DLC files and the merged text files (`directory.txt`, `Vehicles/VehicleDatabase.xml`, later
   the dictionaries and `Audio/StreamedAudioFileList.txt`). An overlay entry replaces a base entry with the
   same key. No file in the game folder changes.
2. **Hooks:** `ReadFile`, `GetFileSize`, `SetFilePointer` and `CloseHandle` are hooked by swapping
   import-table slots; `CreateFileA` is not hooked. A handle counts as the archive when `GetFileSize` is
   called on it and it is the registered file (same volume serial and file index); the game always calls
   `GetFileSize` right after the open. The slots swapped are **kernel32's own** import slots behind its
   `jmp [slot]` export thunks. That is a data write in a system DLL, it can be done in `DllMain`, and it
   catches the game's first archive read. The game's own import slots are filled by SecuROM, and only after
   the first resident.ark open (see "Timing").
3. **New tracks are plain files on disk.** The level loader builds the archive names from the track name
   (`Environments/Levels/<track>.ark`, `<track>Powerplays.ark`, sectors `<track>.Static.ark` and
   `<track>.Stream.ark`). It needs no hook, only converted files at those paths.
4. **The unlock switches are tweakables**, set with data writes like the existing `UnlockAll` and
   `BonusContent` options.

The prototype covers step 1 and the hook layer: it runs against the real resident.ark, with the hooks
installed in a test process (results below).

## 1. How the game opens and reads its archives

### Devices and paths
A path is `device(options)|path`, for example `Resident(noassert)|Vehicles/%s`. A path without `|` goes to
the device with the empty name.
- Device table: `0xD751F0` (pointers), count `0xD75290`. `0x571EE0` finds a device by its name prefix.
  `0x571F80` registers a device and replaces one that has the same name. `0x571FE0` removes one.
- Device object: vtable, name at +4, base path string at +8 (init `0x4012A0`), separator byte at +0x110.
  The separator is `/` for archive devices and `\` for directory devices. `0x571E70` turns `/` and `\` into
  the separator and resolves `.` and `..`.
- Vtables: `0xBA0264` base class, `0xBA028C` directory device, `0xBA0338` archive device.
  The directory device opens files with `0x402410` (class `0xBA07C4`, Win32 stream) and lists them with
  `0x401680`. The archive device opens members with `0x401910` (class `0xBA03A4`).

Startup (`0x401040`):
1. `0x402860` registers `root`, the game folder.
2. `0x410BD0` runs.
3. `0x402ED0` runs these in order:
   - `0x402C30` registers `""` and `data` as `<root>Data\`.
   - `0x402B00` decides the mode (section 2).
   - `0x402DA0` registers `Resident` → `Resident.ark` and `Seclusion` → `Seclusion.ark`.
   - `0x401CB0` registers `CachedLoader`, `CachedSaver`, `Memory`, `endian` and `lzss`, then `Audio`.
     `Audio` reads `Resident(noassert)|Audio/StreamedAudioFileList.txt`: **this is the first resident.ark
     read**.
4. `0x558760` runs the main loop.

Per track:
- `environment` (`0x7A9580`) points at `Environments/Levels/%s.ark` (`0x7A96F0`).
- `powerplays` (`0x7A4F90`) points at `%sPowerplays.ark` or `FrontendPowerplays.ark` (`0x7A5100`).
  Both are called from `0x4A8C80`; the track name comes from `[x+0xE0]`.
- `CityStatic`, `CityStaticOverlay`, `CityStream` and `CityStreamOverlay` (`0x7C6140`, `%s.Static.ark`,
  `%s.Stream.ark`) are always archive devices.

The archive files themselves open through the `""` device, as `<root>Data\...`.

### ArkLoader
There is one current archive for the whole game:
- index `0xD750E0`, stream `0xD750E4`, name `0xD750E8`;
- open member count `0xD751E8`, keep-open flag `0xD1F7BF` (set by `0x571360`).

`0x571940` opens an archive. If the name differs from the current one, it closes the current one, opens
the new stream through the devices, reads the u32 `version<<16 | compression` and wraps the stream:
`0x571200` adds `lzss|`, `lzhuf|` or `zlib|`, but the PC uses 0 (none). Then `0x5718E0` → `0x571660`
reads the u32 index size and the block, and `0x5712F0` checks `hsize == 16 + 20*(cA+cB)` and relocates
`offA`, `offB` and the name pointers.

**The index is read again every time the game switches archives**, so the virtual index must be cheap; it
stays in memory. Opening a member of another archive while members are open fails (string `0xBD2D10`).

Lookup (`0x5710C0`):
- table A: binary search on the u32 key at +0x10, unsigned compare;
- table B: by name (`strcmp`). `cB` is 0 in all 42 PC archives.

Key (`0x571080`): `h = h*31 + tolower(c)` over the path below the device.

Member open (`0x5713A0`, outside Ghidra's function bounds): look up the entry, seek the stream to
`entry.offset` ("Skipping backwards/forwards" warnings), store offset and packed size, and wrap the
stream by `entry.type`.

PC archive layout:

| field | meaning |
|---|---|
| `u32 0x00010000` | version 1, no stream compression |
| `u32 hsize` | size of the index block |
| `u32 cA, offA=0x10, cB=0, offB=hsize` | offsets relative to file offset 8 |
| `cA × {type, offset, csize, size, key}` | sorted by key; offset absolute; type 0 stored, 1 LZSS (u32 size + stream) |
| data | starts at `8 + hsize`, not aligned |

### Win32 layer: the I/O a hook sees
- **Open** (`0x573490` → wrapper `0x5733A0`): `CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL,
  OPEN_EXISTING, 0x48000000 = OVERLAPPED|SEQUENTIAL_SCAN)`, retried up to 32 times. On NT it allocates a
  zeroed `OVERLAPPED` (`[+0x1DC]`). Then `GetFileSize(h, NULL)` → `[+0x1AC]`, 32 bits only.
- **Seek** (`0x5730B0`): with an `OVERLAPPED` it only writes `ov->Offset`. `SetFilePointer` is called only
  without one.
- **Read** (`0x5730F0`): `ReadFile(h, buf, n, &got, ov)`.
  - On TRUE: `ov->Offset += n`, the requested size, not `got`.
  - On `ERROR_IO_PENDING`: remembers buffer and size, still adds n, and later calls
    `GetOverlappedResult(h, ov, &got, TRUE)` (`0x573180`). On failure it subtracts n and retries.
  - The stream above it (`0x5728E0`, `0x572850`) fills its buffer at buffer-aligned positions.
- **Close** (`0x573060`): `CloseHandle`.
- No `FILE_FLAG_NO_BUFFERING`, no completion port, no file mapping, no `ReadFileEx`.

Import slots:

| slot | import | value in the dump |
|---|---|---|
| `0xB9B1A8` | CreateFileA | `0x012B13D0`: **SecuROM's wrapper**, not kernel32 |
| `0xB9B190` | ReadFile | kernel32 (`0x7659EE10`, base `0x76580000`) |
| `0xB9B18C` | SetFilePointer | kernel32 |
| `0xB9B1B0` | GetFileSize | kernel32 |
| `0xB9B194` | GetOverlappedResult | kernel32 |
| `0xB9B114` | CloseHandle | kernel32 |
| `0xB9B100` / `0xB9B1B4` / `0xB9B0FC` | FindFirst/FindNext/FindClose | directory device listing (`0x573550`, `0x573660`), resident.ark existence check (`0x4011E0`) |

Other direct file users, not affected:
- `ss.cfg` (`0xA12DD0`, `0xA12EB0`);
- the `\Deferred\Dictionary` fallback (`0xA15940`);
- the CRT (`0x76E1C7`);
- Havok's file system (`0x671530`);
- the Pure saves search (`0x590720`).

Bink and FMOD open their files through their own imports.

On disk, SplitSecond.exe's import directory belongs to SecuROM (IAT at `0x201BDB4`). The game's table at
`0xB9B000` is filled by SecuROM at run time.

## 2. Loose files and extra archives

- **There is no per-file loose override in archive mode.** `0x402B00` sets `0xD646C6` once: archive mode
  when `data|Resident.ark` exists and `-noark` is not on the command line. Without it, every device is a
  directory device (`Resident` → `<root>Data\`, levels → `%sEnvironments/Levels/%s/`), and
  `0x788430` builds the lists with FindFirstFile instead of directory.txt. That mode is all or nothing,
  and it cannot be used: the archives store only key hashes (`cB = 0`), so the tree cannot be extracted
  with its names.
- **Always loose:**
  - `data|...` (Bink movies, through the existing `BinkOpen` hook);
  - `root|...` (`SPLITSECOND.PARAMS`, `mapmaker.params`);
  - audio names listed in `Audio/StreamedAudioFileList.txt`: the Audio device (`0x403200`) sends them to
    `data(noassert)|`, everything else to `Resident`. A new streamed FSB therefore needs only a loose file
    and a line in the merged list.
- **Mounting by calling the game:** a device can be built with `0x530290` (alloc 0x114), the vtable
  `0xBA0338`, `0x4012A0`(path) and separator `0x2F`, then registered with `0x571F80`. That is useless for
  DLC content: the game builds its paths as `Resident|...` in code, so a new `DLC|` device is never
  asked. Re-registering `Resident` with another archive name (replace by name) would work, but only with a
  complete merged copy of resident.ark (option B).

## 3. Lists, vehicle database and unlock switches

| what | where | DLC change |
|---|---|---|
| `Resident\|directory.txt` | ctor `0x788610` → parser `0x787F60`: blocks `-pattern` / `:SECTION` / `:0\|1` / entries, tokens split on space, tab, CR, LF, `,:;`; `0x787C90` adds a section or appends to an existing one; lookup `0x7878A0` (CRC of the section name) | union of sections: TRACKS += Quarry; `Quarry_SUBTRACK` = A; GAMEMODES += KingOfTheAir.params, TimeTrialPlus.params; NEMESIS += TargetLeaderHelicopter.params, EasyTargetLeaderHelicopter.params. The Onslaught list also has a stray `A` under TRACKS: `dlcmerge.py` drops TRACKS entries without a `_SUBTRACK` section |
| list users | TRACKS + `<t>_SUBTRACK`: quick play `0x772FF0`; VEHICLES `0x422B50`; GAMEMODES `0x78EF00`, `0x7732C0`, `0x773B10`; NEMESIS `0x793810`; RIGIDBODIES `0x7DAAE0` | |
| `Vehicles/VehicleDatabase.xml` | `0x78A290` (`Resident(noassert)\|%s`, name pointer `0xD36814`); record parser `0x7FACA0` (ID, ModelFile, HumanPhysicsFile, AIPhysicsFile, Chase/Bumper/BonnetCamFile, Lives, AudioFile, Region, DevonName, Speed/Drift/Strength/Accel/OverallStat, ActionScriptImage, DCBModifier, PlayerSkillLevel, Locked, Player, CarSelectOrder); 0x1BC bytes per car; by ID `0x78A010`, by index `0x78A0D0` | the DLC file only replaces `Placeholder_08..11` (ModelFile Musclecar_19, _17, _18, Truck_09; `Player="true"`). IDs and table indices stay the same, so the profile's per-car data stays aligned. **`DLCRestricted` does not occur in the PC exe**, so those cars are ordinary cars |
| `UI/Logic/Season.xml` | `0xA05820`: InitialCars, UnlockableBasicCars, UnlockableEliteCars, UnlockableDLCCars (on PC: the branded retail cars) | whether a Placeholder car needs an entry here to be selectable is open; merge the XML the same way if it does |
| DLC unlock switches | singleton `0xD64E70` (`0x4116C0` → `0xA062D0`): 16 tweakables `DLCUnlocks%02d/isUnlocked`, value pointer at `+4+8i` (get `0xA06430`, set `0xA06440`), found flags `+0x80+i` (`0xA06450`; `0x41AD00` checks 13); readers `0x471160`, `0x4853B0`, `0x486D90` (indexes 11 and 12) | write 1 through the pointer once the singleton exists (data write). The Master Unlock pack's `Unlock.params` sets `DLCUnlocks12/isUnlocked = true` |
| track enum tables | descriptors `{ptr, count=15}`: `0xD7E874` (names `0xD368F8`), `0xD7E880` (subtrack letters `0xD36970`), `0xD7E88C` (subtrack paths `0xD369E8`); lookup `0x531570`, `0x78E600`/`630`/`660` | data, so they could point to longer copies, but their callers loop with an immediate 15 (`0x4128D0`, `0x4757D0`, `0x51B960`, `0x7D6770`, `0x4838B0`, `0x474CA2`). A 16th track gets no index there (see risks) |
| text | `Resident\|Dictionary/<LANG>_WIN32.dct` (`0xA15750`; ENGLISH_NTSC, FRENCH_NTSC, ITALIAN, GERMAN, SPANISH_NTSC, StringIDs exist); parser `0xA15220` | new `TEXT_...` names need merged dictionaries; the format is not analysed, and the DLC packages' dictionary was not found under the PC naming |

**A new car** needs:
- its files below `Vehicles/Bodies/<Model>/` (model, textures, deform, physics, `.rdata`, handling under
  `Vehicles/Handling/Configs/`, audio tonic files, frontend `ChassisName.txt`, `CarUnlock_%s` GFX if
  shown), in the overlay;
- the Placeholder record replaced in the merged VehicleDatabase.xml;
- if needed, an entry in Season.xml;
- the DLC switch set.

The Deadline DIRECTORY.TXT adds no VEHICLES entry, because the Placeholder cars use `ModelFile`.

**A new track** needs:
- the TRACKS and `<track>_SUBTRACK` entries in the merged directory.txt;
- converted level archives on disk:
  - `data\Environments\Levels\Quarry.ark`
  - `data\QuarryPowerplays.ark`
  - `data\Environments\Levels\Quarry\Sectors\Quarry.Static.ark` and `Quarry.Stream.ark`
- its `Resident|Environments/Levels/Quarry/...` files in the overlay (cameras, PPD params, tyre tracks,
  `_po.textures`, `HelicopterMissileTargets.xml`, `GameLogic/Tracks/Quarry/...`);
- the game mode and nemesis params;
- the quick play movie `data\Movies\Screens\QuickPlay\FE_QP_Track17.bik`, as a loose file;
- the dictionary text.

## 4. Design: virtual archive

### Layout (`vark.py`, `vark.cpp`, the same algorithm)
```
[0, H)          header {0x00010000, 16+20n} + merged index (n = |keys of base ∪ overlays|), in memory
[H, ...)        base data [8+hsize, size) shifted by delta = H - (8 + base hsize) = 20 × (new keys)
[o_i, ...)      overlay i data region, 16-aligned, one after another
```
- Base entry offsets are shifted by delta; overlay offsets are moved to their region.
- An overridden base entry's data stays in place, unreferenced.
- The index is sorted by unsigned key.
- The total must stay below 2 GB (signed 32-bit stream positions; 32-bit `GetFileSize`); resident.ark is
  833 MB.

### Read routing (`iohook.cpp`)
- Position = `ov->Offset` (overlapped) or the tracked pointer (synchronous handle).
- **Inside the base data region:** the real `ReadFile` on the game's own handle, with `ov->Offset` set to
  `pos - delta` for the call and restored afterwards. The read stays asynchronous, because the kernel takes
  the offset when the request is issued; the game edits `Offset` while a read is pending too. 98 % of the
  test reads went this way.
- **Header, index, overlay data, or a read across a border:** served synchronously from the virtual
  archive, using positional reads on its own handles. With an `OVERLAPPED` it sets `Internal = 0` and
  `InternalHigh = got`, signals `hEvent` if there is one, and returns TRUE. The game then skips
  `GetOverlappedResult`, and if it calls it anyway, the call returns at once because
  `Internal != STATUS_PENDING`, so `GetOverlappedResult` needs no hook.
- At or past the end: `ERROR_HANDLE_EOF` (overlapped) or 0 bytes.
- `GetFileSize` returns the virtual size; `SetFilePointer` works in virtual coordinates; `CloseHandle`
  drops the handle from the map.

### Timing and installation
The first resident.ark read happens inside `0x402ED0`, before the game's main loop. `ApplyGamePatches`
runs at the first `DirectInput8Create` (`0x410550`), which is too late, and the game's import table may
not be filled when `DllMain` runs. So:
1. In `DllMain`: `IoInstallKernel32Slots()`. kernel32's exports `ReadFile`, `SetFilePointer`,
   `GetFileSize` and `CloseHandle` are `FF 25 [slot]` thunks on this system (checked on SysWOW64
   kernel32). The slot is a pointer in kernel32's import table, so swapping it changes no code. Every
   kernel32 caller passes through; only registered handles are changed. If a thunk does not look like
   that, nothing is installed.
2. Build the virtual archive in `DllMain`, before the game runs. It reads two indexes, under 1 ms in the
   test. Register `<game>\data\resident.ark`.
3. (Considered, not done: moving to the game's own slots at `ApplyGamePatches` to narrow the hook to the
   game's calls. The DLL keeps kernel32's slots.)

### Converter side (outside this prototype)
- `dlcmerge.py directory|vehicles` merges the lists.
- `dlcmerge.py overlay <folder>` packs converted files under their game paths into a stored-entry PC
  archive. LZSS packing is optional; the game reads type 0 entries.
- Track archives are rewritten to PC format: same container, little-endian index, converted members.
- glimpse would keep its overlays outside `data`, for example in `glimpse_dlc\resident_*.ark`, so nothing
  in the game's files changes.

### Alternatives considered
- **B, no hooks at all:** patch resident.ark in place. Move the first few entries' data to the end of the
  file (the larger index overwrites them), write the merged index at offset 8, and append the overlay data.
  Keep the overwritten head bytes and the original size for undo. It is pure data, but it changes an 833 MB
  game file: Steam verify and updates undo it, and an interrupted write corrupts it. A full merged copy
  plus re-registering the `Resident` device costs 0.8 GB and has the same timing problem.
- **C, `-noark` loose mode:** not feasible, because the file names are unknown (section 2).
- **Hooking `CreateFileA`:** the game's slot holds SecuROM's wrapper, and replacing or chaining it is an
  unnecessary risk. Recognising the handle at `GetFileSize` avoids it.

## 5. Prototype and offline test

| file | content |
|---|---|
| `arkfmt.py` | PC archive reader/writer, key hash, LZSS decoder |
| `vark.py` | virtual archive model, the game's buffered stream (`GameStream`) and ArkLoader (`GameArkReader`) |
| `dlcmerge.py` | directory.txt / VehicleDatabase.xml merges, overlay builder (CLI) |
| `src\vark.h/.cpp` | C++ virtual archive (same layout), the DLL's own (the harness builds it from `src`) |
| `src\iohook.h/.cpp` | `ReadFile`/`GetFileSize`/`SetFilePointer`/`CloseHandle` hooks, handle registry by file ID, the installer for kernel32's slots (the DLL's own, built from `src` as well) |
| `test_vark.py` | builds 2 overlays and merges them with the real resident.ark. Checks the index invariants, reads overlay, overridden and 302 base entries through `GameArkReader` (raw and decoded), and reads across segment borders and at the end. Writes `test_out\expect.txt` |
| `test_vark.cpp`, `build_test.bat` | x86 harness: opens resident.ark the way the game does (overlapped, `GetFileSize` first), reads the header, the index and 308 entries the way ArkLoader does (Offset seeks, `ReadFile`, `GetOverlappedResult` on pending), checks CRCs. Passes: (1) the hooks called directly, buffer 0x10000 and 0x3000; (2) a synchronous handle with `SetFilePointer`; (3) **this process's kernel32 slots swapped**, plain API calls. Also checks EOF behaviour, pass-through, and unregistering on close |

Run:
```
python -I test_vark.py --dlc <...>\scratchpad\dlc\x_deadline --dlc <...>\scratchpad\dlc\x_onslaught
build_test.bat
```

Test content:
- Overlay 1: merged directory.txt (PC + Deadline + Onslaught lists), merged VehicleDatabase.xml (4 DLC
  cars), and three synthetic files of 100 B, 300 KB and 0 B.
- Overlay 2: overrides one overlay 1 file and the base `UI/Logic/Season.xml`.

Result (2026-11-20): both PASS.
- Virtual size 833,470,802 bytes (base 833,114,112), delta 60, 4 segments.
- Python and C++ give the same layout and the same index CRC.
- C++: 4 handles registered; 95 reads served from the virtual archive, 5845 passed to the real file at the
  shifted offset, 20 unrelated reads passed through unchanged.

## 6. Risks and open points

- **Not run in the game.** What still needs checking in a run:
  - that kernel32's slots can be swapped from `DllMain` under SecuROM, and that SecuROM does not check
    kernel32's import table (it checks the game's code);
  - that the handle is registered at its first `GetFileSize`;
  - that the merged directory.txt reaches quick play;
  - the order of `0x402ED0` against `DllMain`. dinput8 is loaded by SecuROM's import directory; if
    kernel32 thunks differ on other Windows versions, the fallback is game slots at
    `ApplyGamePatches`, which misses the startup reads.
- **Handle reuse:** a registered handle closed without `CloseHandle` (for example `NtClose`) would leave a
  stale entry. The game closes archives with `CloseHandle` (`0x573060`).
- **Synthetic completion:** fine without a completion port. The game creates none, and has no
  `CreateIoCompletionPort` import.
- **Track count:** quick play is list-driven, but six callers loop over a fixed 15 entries of the track
  enum tables (season, events, leaderboards and the race-setup index in `0x4128D0`). Quarry would have no
  index there. The Xenon.dll pack changed exactly these limits in code, and that is not an option here.
  Next step: decompile those six callers and find what a missing index breaks. If needed, give Quarry an
  existing slot's index by data (table entries are `{id, name}` pairs).
- **Content conversion** is the real work and is not covered here: models (converter done), textures,
  Havok, `.deform`/`.shaderinst`, audio (XMA → PCM/ADPCM FSB), Xbox GFX UI files (keep the PC ones). Also
  dictionaries: find the DLC's text, analyse the `.dct` format (`0xA15220`), and merge.
- **Season.xml / profile:** whether Placeholder cars become selectable with `Player="true"` alone, and how
  the profile stores per-car unlocks for indexes that were Placeholders.
- **Sector overlay devices** (`CityStaticOverlay`, `CityStreamOverlay`, flags `[x+2]` and `[x+3]` in
  `0x7C6140`): unused here; possibly how the Xbox DLC patched existing tracks.
