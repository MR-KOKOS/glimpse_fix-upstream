# assemble_track: one Xbox DLC track -> PC level archives + resident overlay

`assemble_track.py` converts everything the PC game reads for one DLC track (Quarry from the user's own Onslaught
package) with the existing converters, writes the four PC level archives, a resident.ark overlay with the new keys
and the merged shared files, and the loose files (movie, streamed bank, music project). Offline only: nothing here starts the game
or writes to the game folder (the script refuses an output path inside it).

```
python -I -X utf8 assemble_track.py [--track Quarry] [--dlc <Onslaught package dir>] [--game <PC data dir>]
    [--audio <dlcaudio.py output dir>] [--base <car pack .ark>] [--combined <name.ark>]
    [--names ENGLISH_NTSC=QUARRY,GERMAN=STEINBRUCH,...] [--out <out dir>] [--xlate-cache <file.pkl>]
    [--no-qp-picture] [--no-dlc-music]
```
Defaults (only with the environment variable `GLIMPSE_DLC_SCRATCH` = a work folder; otherwise `--dlc`, `--audio`
and `--out` are required): `<work>\dlc\x_onslaught` (`x_rock` for `--track nem_quarry`), `G:\...\SplitSecond\data`, `dlc\audio_work\pc_out` (the output
of `../audio/dlcaudio.py`) and `dlc\track_out\quarry`. It takes about 1 minute (5 minutes the first time, when the 89
Quarry-only shader IDs are translated and self-checked; `--xlate-cache` keeps the translations for the next run).

Dependencies: `../misc` (arkio, convmisc, dlcmerge rule), `../tex/convtex.py`, `../havok/convhkx.py`,
`../shaders/convshaders.py` with `../xenos` (translator), `../convmodel.py` + `../mdl.py`, `../ui/dictadd.py`,
`../ui/gfxcars.py`, `../ui/flashtex.py`, `../inject/vark.py`, `assemble_car.pc_texture_keys`. None of them is
changed by this script.

## Outputs (below `--out`, in the game's folder layout)

| file | content |
|---|---|
| `data\environments\levels\quarry.ark` | Xbox `QUARRY.ARK`, converted; 0x800-padded, data in the Xbox file's order |
| `data\quarrypowerplays.ark` | Xbox `QUARRYPOWERPLAYS.ARK`, converted; the same rules |
| `data\environments\levels\quarry\sectors\quarry.static.ark` | Xbox `.STATIC.ARK` models; not padded, data in key order |
| `data\environments\levels\quarry\sectors\quarry.stream.ark` | streamed textures (`quarry.streamtex.%03d`) and chunkless model data (`<model key>.stream`); not padded, key order |
| `data\movies\screens\quickplay\fe_qp_track17.bik` | the quick play movie, Bink copied as is |
| `data\audio\release\locales\quarry\quarry_streamed.fsb` | the streamed level bank, loose as the PC keeps its `*_Streamed.fsb` |
| `data\audio\release\dlc3_music\dlc3_music.fev`, `.fsb` | the package's music project (from `AUDIOBLACKLIST.TXT`), loose and streamed like the PC's `Music/Music`; no stock code loads it (see Music below); not with `--no-dlc-music` |
| `glimpse_dlc\track_quarry.ark` | resident overlay (below) |
| `glimpse_dlc\<--combined>` | with `--base`: the base pack's entries + the track overlay in one archive |
| `track_quarry.manifest.tsv`, `track_quarry.report.txt` | one line per entry (archive, key, size, kind, converter, source); checks, merges, DLL data |

Layout rules, from the PC files: main level and powerplays archives are padded to 0x800 and keep the export order
(PC Graveyard: 390 of 421 and 1364 of 1399 entries in the Xbox data order); sector archives are not padded and keep
their data in key order. PC `.stream.ark` entries are LZSS (type 1); ours are stored (type 0), which ArkLoader reads
the same way (`vark.GameArkReader` read-back).

## What goes where

| Xbox source | converter | goes to |
|---|---|---|
| `.model` (main, powerplays, static) | `convmodel.convert_level` (+ `.stream` data for chunkless models) | level archive / `.stream.ark` |
| `.textures` (+ streamed slots) | `convtex.convert`, `convtex.convert_streamed` | level archive / `.stream.ark` |
| `.fxc` | `convshaders.convert_fxc(translate=True)` with the sibling `.shaders` (key of the same stem: `h*31` is invertible, so the `.shaders` key follows from the `.fxc` key) | level archive |
| `.shaders`, `.shaderinst` | `convshaders.convert_shdr`, `convert_sdri` | level archive |
| Havok binary | `convhkx.convert` | level archive |
| Havok XML packfiles `5d967614`, `f87a8b07` | text (LF -> CRLF); every PC level keeps these two keys, and PC Graveyard's copies are the Xbox files with CRLF | level archive |
| the other 6 Havok XML packfiles | none: no PC level has these keys (Graveyard: 9 Xbox-only) | skipped |
| everything else | `convmisc.detect` / `convert` | level archive |
| powerplays key `62d50b37` (TimeLine XML) | not in the Xbox file, the same bytes in all 6 PC powerplays archives | copied from the PC |
| `RESIDENT_DLC.ARK`: 152 keys the PC resident.ark lacks (camera / AI / route text, splines, links, AI lines, `_po.textures`, KingOfTheAir and helicopter nemesis params) | as above | overlay |
| `RESIDENT_DLC.ARK`: 1007 keys the PC has (250 models + 250 x fxc/shaders/shaderinst + 7 text) | PC versions kept; the shader files are converted anyway as a check | not in the overlay |
| `audio/release/locales/quarry/quarry.fev/.fsb`, `quarry_streamed.fev` | `dlcaudio.py` output | overlay |
| `audio/locales/quarry/*.xml` (`_Audio`, `_Ambient`, `_Reverb`, `_Swooshes`) | `dlcaudio.py` output, LF -> CRLF (every PC level audio XML is the Xbox file with CRLF, 24 of 24 base pairs) | overlay |
| `audio/release/locales/quarry/quarry_streamed.fsb`, `audio/release/dlc3_music/dlc3_music.fev/.fsb` | `dlcaudio.py` output | loose files |

Merged shared files (base = `--base` pack if it has the file, else the PC resident.ark; each is the base bytes plus
inserted parts only):

| file | insertion |
|---|---|
| `directory.txt` | `Quarry` at its sorted place in TRACKS; the block `-Environments/Levels/Quarry/Subtracks/*`, `:Quarry_SUBTRACK`, `:1`, `A` after the last `_SUBTRACK` block. Only the track sections: the Onslaught GAMEMODES/NEMESIS additions (KingOfTheAir, helicopter nemeses) are not merged |
| `UI/Logic/TrackFiltration.xml` | `<Track Name="Quarry" SubTrack="A"/>` at the end of QuickRace, TimeTrial, Elimination, HelicopterSurvival, HelicopterRevenge (the DLC file's modes) and, when the `--base` pack carries the converted Deadline (`GameLogic/GameModes/TimeTrialPlus.params`), TimeTrialPlus (see "Deadline on Quarry" below); the DLC-only KingOfTheAir (no PC conversion) is left out; `DLCPack` is not written, the PC parser ignores it. Every mode stays within the exe's 16 tracks (0x77B620; 12 at most here) |
| `Environments/SubTrackLightRigs.txt` | `Quarry:A:midday_v02` (`<track>:<subtrack>:<rig>` for every rig the level's `LightRigs/LightRigNames.names` lists) as its own block after the last race track block, before the `nem_` lines. Without a line for the track 0x790810 adds the rig `default`, which no level has (race setup 0x4AE930 -> 0x790950) |
| `Audio/StreamedAudioFileList.txt` | `Audio/Release/Locales/quarry/quarry_Streamed.fsb` and the Submission twin, after the other `Locales/*_Streamed.fsb` (NUL-separated list); `Audio/Release/DLC3_Music/DLC3_Music.fev`, `.fsb` and the Submission twins after the `Music/Music` lines |
| the six dictionaries | `TEXT_QUARRY_A`, `TEXT_QUARRY_A_TRACK`, `TEXT_QUARRY_A_SUBTRACK` (`dictadd.add_checked`; the exe builds `TEXT_%s_%s`, `TEXT_%s_%s_TRACK`, `TEXT_%s_%s_SUBTRACK`; the DLC GFX names `TEXT_Quarry_A`; lookups lower-case). Texts: `--names` (default QUARRY, STEINBRUCH, CARRIÈRE, CAVA, CANTERA: the package has no dictionary and the Xbox dictionaries have no Quarry text); the `_SUBTRACK` line is copied from the one-word PC route `TEXT_GRAVEYARD_A_SUBTRACK` per language; StringIDs = the lower-case key |
| `UI/Frontend/Flash/FE_quick_play_detailscreen.gfx` | the quick play picture frame `TEXT_Quarry_A` (below; not with `--no-qp-picture`) |
| `UI/Frontend/Flash/Textures/Flash.textures`, `Flash.filenames` | its image `FE_QuickPlay_Imgs_DLC1` (below) |

### Quick play detail screen picture

The movie's ActionScript `SetTrack(name, locked, selected)` sets the track text and calls
`main.backgroundImgs.gotoAndStop(name)` (`locked` / `movie` instead when locked / selected). The exe (`0x485C30`)
passes `TEXT_<track>_<subtrack>` built by `0x77B210` (`"TEXT_%s_%s"` from the track record, so `TEXT_Quarry_A` with
the track name `Quarry`). `backgroundImgs` is sprite 16 (PC), 311 frames: one segment per track (label frame with
`play`, empty frames, `stop`), then `locked` and `movie`. Depth 1 is a mask (clip depth 5), depth 3 the picture: a
cell of the atlas `FE_QuickPlay_Imgs_SET1` (1024x1024, PC `Flash.textures`), chosen by the place matrix. Without a
frame of the right label `gotoAndStop` does nothing and the previous track's picture stays.

The DLC file is the PC file with the character IDs moved up and two segments inserted before `locked`:
`TEXT_Quarry_A` (a new shape filled with `FE_QuickPlay_Imgs_SET2`, 512x512, from the DLC's `DLC.textures`) and
`TEXT_nem_quarry_A` (the same shape, other offset). `assemble_track.py` adds `TEXT_Quarry_A` with
`gfxcars.py --sprite-label TEXT_Downtown_A --segment --image-prefix FE_QuickPlay_Imgs_DLC`: the segment (label frame +
15 frames, `play` ... `stop`) is appended after the PC's last frame, so the PC frames and labels keep their numbers;
the mask sprite is mapped to the PC's, the picture shape and its image are copied (image renamed
`FE_QuickPlay_Imgs_DLC1`, the DLC frame's matrix kept). `flashtex.py` adds the image to the set the movie's other
quick play atlas is in, `Flash.textures` (DXT5, code 0x25 as the set's DXT5 images, key `455fef38`, the DLC's), as
`FE_QuickPlay_Imgs_DLC1_DXT5`, and `FE_QuickPlay_Imgs_DLC1` to `Flash.filenames`. With `--base` both files are taken
from the base pack (the car pack adds `FE_VSelectDLC1` to the same set). `TEXT_nem_quarry_A` is not added: nothing on
the PC can name it (no `nem_quarry` track in directory.txt or the level data; the DLC lists it only in its
KingOfTheAir mode with `DLCPack="Nem_Quarry"`, which is not merged).

`gfxcars.py` got two options for this (`--sprite-label`, `--segment`); without them it writes what it wrote before
(the Deadline car pack rebuilt with `build_pack.py` is byte-identical to `overlay\deadline_v2.ark`).

### Deadline on Quarry

The Onslaught package's `UI/LOGIC/TRACKFILTRATION.XML` lists `Quarry A` in the DLC-only modes KingOfTheAir and
TimeTrialPlus (`DLCGameModeIndex`). TimeTrialPlus is the game mode Deadline, which `../mode/deadline_mode.py` adds
to the car pack; when the `--base` pack has `GameLogic/GameModes/TimeTrialPlus.params`, `assemble_track.py` adds
Quarry to it with deadline_mode.py's per-track rules (`../mode/deadline_track.py`; without the mode, e.g.
`build_all.py --no-mode`, nothing of this is done and the two entries below stay as before):

| entry | what |
|---|---|
| `UI/Logic/TrackFiltration.xml` | `<Track Name="Quarry" SubTrack="A"/>` as the 12th track of TimeTrialPlus (the same line form as the other modes and as deadline_mode.py's GameMode element) |
| `GameLogic/Tracks/Quarry/Params/RaceSetupOverlay_A.params` (new key, from RESIDENT_DLC.ARK) | + `/RaceSetup/Logic/Countdown` `'Initial Countdown' = 60.0 (0.0, 3600.0)`: the Xbox `TimeTrialPlusCountdown` of `RaceSetupOverlay_A_TimeTrialPlus.params` (60 s, the same in the plain overlay), extra seconds 0; the DLL adds `[DLC] DeadlineBonus` when `Pickups=0`. The file is the package's overlay, so it carries the Xbox keys `TimeTrialPlusCountdown` and the 7 `/RaceSetupOverlay/TimeTrialPlus/<Rival>Time` already (no PC tweakables, dropped by the parser). Inert for Quarry's other modes: their mode files have Countdown Enabled = false (checked) |
| `Environments/Levels/Quarry/Subtracks/A/Pickups/Quarry.pkps` (new key, from RESIDENT_DLC.ARK) | the DLC entry byte for byte (LF, 15644 bytes, 42 green pickups in one group, checked with `pkps.py`), as deadline_mode.py keeps the 11 others, instead of the text rule's CRLF copy; the path is the one `src\dlcpickups.cpp` LoadTrack builds for track `Quarry` subtrack `A` (keys are case-insensitive) |

The DLL needs no change for it: the pickups, the start time bonus and the HUD go by the race setup's track and mode
names, and the quick play track list (`src\dlcfs.cpp`: the DLC track records unlocked, id 21 = Quarry's movie while
its item is selected) is the same list object for every mode.

### Level audio and music (how the PC exe loads them)

Static RE of `re\SplitSecond_dump.exe` (Ghidra, read-only):

- Level banks: `0x7F1360` (level load) takes the level name, cuts it at the first `_` (`nem_x_y` keeps `nem_x`),
  registers the event aliases `locale/` -> `quarry/` and `locale_Streamed/` -> `quarry_Streamed/` and calls
  `0x83C370`. That loads `Locales/quarry/quarry` into the memory group `Level` (`0x83BE90` -> `0x7F2FA0`) and
  `Locales/quarry/quarry_Streamed` as a streamed project (`0x7F3030(name, 2)`), each only when
  `Audio(noassert)|Audio/Release/<name>.fev` exists. The FMOD file system opens `<bank>.fsb` next to the `.fev`;
  the `Audio` device sends the names in `Audio/StreamedAudioFileList.txt` to `data\` and everything else to
  resident.ark, so `quarry.fev/.fsb` and `quarry_streamed.fev` are overlay keys and `quarry_streamed.fsb` is a loose
  file. The FEV names the wave bank, its load mode (`quarry` 0x200 = into memory, `quarry_Streamed` 0x80 = stream,
  2 streams, as every PC level) and an 8-byte bank hash that equals bytes 0x18..0x1F of the FSB.
- Level XMLs: `0x83C370` reads `Audio(noassert)|Audio/Locales/quarry/quarry_Audio%s.xml` for `""`, `_Ambient`,
  `_Reverb` (table `0xD4183C`; `AudioTrigger` and `AudioPortal` elements), and `..._Audio_Swooshes.xml` separately;
  all resident keys. Quarry's ambience trigger plays `locale_Streamed/AMBIENCE/GENERAL_AMBIENCE_AFTERNOON`, i.e.
  `quarry_Streamed/...`.
- Race music: the same on every track. `Music/Music` (loose `data\Audio\Release\Music\Music.fev/.fsb`, streamed) is
  loaded once at start-up (`0x7F5900`). The picker (`0x83DD70`) shuffles the 14 group names `InGame_Track_01` ..
  `_15` (no `_13`; table `0xD41898`) into `0xD8CA00`; each race `0x83D9F0` takes the next one (or `Survival_Track`,
  `Airstrike_Track`, `Tutorial_Track`, `Elite_Track` for those modes), and `0x83D1E0` starts
  `Music/<group>/RaceSection0<n>` (`n` = intensity 1..3), `Music/PreRace/PreRaceShuffled` before the start
  (`0x83D020`), `Music/<group>/ResultScreen` and `Music/FinishStingers/*` after it. No level data, directory.txt
  section or list chooses the music; the in-game log's `PreRaceShuffled` / `RaceSection01` on Quarry are these base
  events, restarted after each crash (`0x83D6C0` stops the music while `Global_Streamed/CamReSpotCrash/Loop` plays).
  The exe has no string `DLC3_Music` or `Quarry_Track` (neither has the Xbox disc module; the DLC code came with a
  title update), so the package's `DLC3_Music` project (groups `PreRace`, `FinishStingers`, `Survival_Track`,
  `Airstrike_Track_1..4`, `Quarry_Track` with `RaceSection01..03` and `ResultScreen`; 49 events) is never loaded.
  The build ships it loose and listed, ready for a loader in the DLL (the report's DLL section has the hook points);
  without one Quarry plays the base race music like every other track.

The third-party PC conversion (comparison only) ships no DLC music either; its `quarry_Streamed.fev` is the Xbox
file plus the 16-byte `sett` chunk some PC FEVs carry (both forms load; the PC `Music.fev` has none), and its Quarry
XMLs are the Xbox files (only `quarry_Audio.xml` with CRLF).

## Checks (exit code 1 on any failure)

- Per entry: models (`mdl.Model` parse, vertex buffers and index lists inside the data, no DEC3N left), textures
  re-parse, Havok (no unswapped bytes or conflicts), `.fxc`/`.shaders` re-parse and rebuild identically,
  `.shaderinst` converts back to the Xbox bytes, byte-swapped kinds keep their size, text changes only line ends/case.
- Shaders: every translated ID has VS, PS and VS->PS pipeline status `ok`; every `.fxc` has its `.shaders` with the
  same entry names in the same order; guessed variant lists only for translated IDs; every `.shaderinst` shader ID is
  in a `.fxc` of its archive or in the PC shader files. The Xbox copies of base shader files in RESIDENT_DLC.ARK give
  the PC files (247 + 250 + 250 byte-identical; 3 `.shaderinst` differ only in instance hash words).
- Texture slots of every `.shaderinst`: found in the track's containers or the PC resident.ark; the rest is listed
  (Quarry 6 slots / 2 hashes; PC Graveyard has 8 such slots too).
- Archives: read back with arkio and through `vark.GameArkReader`: little-endian, hsize, keys sorted and unique, all
  stored, data equal, padding and data order rules above.
- Every Xbox entry accounted for (converted, skipped with the reason, or a PC resident key kept); every `.stream.ark`
  entry used by a texture slot or a chunkless model; every key all PC level / powerplays archives have is present.
- Quick play picture: the new GFX round-trips tag by tag with the right header length; every PC tag kept in order
  (all ActionScript included), the new definitions in front of the picture sprite; the sprite is the one placed as
  `backgroundImgs` in both files; PC frames unchanged, + `TEXT_Quarry_A` and the 15 frames after it with the DLC's
  frame scripts; every placed character defined; the new frame shows what the DLC frame shows (display lists resolved
  through nested sprites: depths, matrices, colour transforms, clip depths, shape bytes, images by name and size with
  the rename). Flash set: in the base file's name order, the base's textures unchanged (entry, data, key), only the new
  image added, keys unique in the set, the new key in no other texture container of the PC resident.ark, the base pack
  or the track's resident keys, the registered name new to all six UI sets (PC and base), `Flash.filenames` = base +
  the name, the image data equal to the DLC's converted image (format 0x25, one level).
- Overlay: the keys that replace resident.ark keys are exactly the 13 merged files (10 without the picture; 12 / 9 before the SubTrackLightRigs.txt merge, outputs up to `quarry_on_deadline_v2`); the new keys are the 152 + 7 audio
  files; with `--base` no other base pack key is replaced; merged text files = base bytes + the inserted parts, CRLF
  only; directory.txt parsed with the game's block rule; dictionaries: every old entry unchanged, new keys resolve;
  the virtual resident.ark (PC + base pack + overlay) read back through `GameArkReader`; the combined archive = base
  pack + track entries. `StreamedAudioFileList.txt` = the base list in its order + the new lines.
- Audio: the locale XMLs are the package files with CRLF only, and every PC level audio XML is CRLF only (the rule);
  for the level bank, the streamed level bank and the music project: the FEV's project and wave bank names, its load
  mode and stream count as the PC's banks of that kind (level banks, streamed level banks, `Music/Music`), its bank
  hash = the FSB's; the FSB is FSB4 0x40000, all MPEG Layer II, with the Xbox bank's sample count, order, channels and
  rates (names may differ where `dlcaudio.py` took a sample from a PC base bank; the FEV refers to waves by index);
  `DLC3_Music.fev` is the package file; no loose file name is taken by a PC file below `data` (the DLL redirect only
  serves files that are missing there).

## Result for Quarry (2026-11-20)

All checks pass (2 notes: 170 entries use convmisc's default 4-byte swap rule, which gives the PC bytes for every such
Graveyard entry; 6 unresolved texture slots).

| file | entries | bytes |
|---|---|---|
| `quarry.ark` | 229 (of 235; 6 Xbox-only XML packfiles skipped) | 64,301,056 |
| `quarrypowerplays.ark` | 548 (547 + 1 PC entry) | 18,169,856 |
| `quarry.static.ark` | 376 | 20,876,952 |
| `quarry.stream.ark` | 623 (436 streamed textures, 187 model streams) | 152,547,950 |
| `track_quarry.ark` | 168 (152 + 7 audio + 9 merged) | 9,125,888 |
| `fe_qp_track17.bik`, `quarry_streamed.fsb` | loose | 5,358,580, 4,249,760 |

Shaders: `quarry.ark` 18 `.fxc` (673 PC entries, 4 files with translated IDs, 77 IDs), `quarrypowerplays.ark` 1 `.fxc`
(571 entries, 12 translated IDs); 89 IDs translated, all `ok`; their 202 entries are byte-identical to
`xenos_work\quarry\translated.fxc`. The static archive and the 152 resident keys have no shader files.

With `--base overlay\deadline_v2.ark --combined dlc_combined.ark`: the same four level archives (byte-identical),
`directory.txt` and the six dictionaries built on the pack's versions, `dlc_combined.ark` 461 entries (300 + 168, 7
shared), 107,534,336 bytes, all checks pass.

With the quick play picture (default; outputs `track_out\quarry_v2`, `track_out\quarry_on_deadline_v2`, all checks
pass, same 2 notes): the four level archives byte-identical to the ones above; `track_quarry.ark` 171 entries (the
168 unchanged + the GFX and the two Flash set files), 42,725,376 bytes (on the car pack 42,989,568): the Flash set is
a 33 MB resident file and the overlay replaces it whole. `dlc_combined.ark` 462 entries (+ the GFX; `Flash.textures` /
`.filenames` replace the pack's versions, which they contain), 107,806,720 bytes. The GFX grows from 9,383 to 9,630
bytes (sprite 16: 311 -> 327 frames, characters 45 image, 46 shape).

With the audio changes (output `track_out\quarry_on_deadline_v4`, `--base overlay\deadline_v2.ark --combined
dlc_combined.ark`; all checks pass, 4 notes: the 2 above, one `quarry.fsb` sample named `INITIAL.wav` instead of
`sawend.wav` because its data comes from a PC base bank, and DLC3_Music not loaded by stock code): the four level
archives, the movie and `quarry_streamed.fsb` byte-identical to v3; `track_quarry.ark` and `dlc_combined.ark` keep
their entry counts (172, 463) and sizes and differ from v3 in 5 entries only: the four locale XMLs (CRLF) and
`StreamedAudioFileList.txt` (+ the four `DLC3_Music` lines after `Music/Music`). New loose files
`data\audio\release\dlc3_music\dlc3_music.fev` (16,323 bytes, the package file) and `dlc3_music.fsb` (40,405,536
bytes). The game's FMOD (`../audio/fmodcheck.exe`) loads all 49 DLC3_Music, 61 quarry and 6 quarry_Streamed events
against these banks with no failure.

## Survival At The Rock: nem_quarry (2026-11-21)

`--track nem_quarry --dlc <Rock package>` builds the package's nemesis track (TRACKDESCRIPTION `16 nem_quarry A`)
with the same code; what differs comes from the data:

- Level archives `nem_quarry.ark` (186 of 192, the 6 Xbox-only XML packfiles skipped), `nem_quarrypowerplays.ark`
  (33 + 3 PC entries), sectors 70 / 215; 24 shader IDs translated (all `ok`), all 518 texture slots resolved.
  The PC ships no `nem_*powerplays.ark` for its own nemesis tracks, but the race load asks for `<level>Powerplays.ark`
  on every level (0x462100 -> 0x7A5100, a missing file is tolerated); the Xbox file is shipped and loaded as there.
- No level sound banks (the package has only the four `Audio/Locales/nem_quarry` XMLs; the PC's nem_* tracks have
  none either: 0x7F1360 loads a locale bank only when its .fev exists), so no streamed bank and no
  StreamedAudioFileList change. The ambience trigger names `Global_Streamed/Nemesis_Ambience/nem_quarry/...`, which
  the PC's Global_Streamed lacks (silent, as on the Xbox without its title update's DLC3_nem_truck_Streamed).
  `DLC3_nem_truck` (+ `_Streamed`) converts and loads in FMOD (12 / 2 events) but no PC code asks for it (the PC's
  trucks use the hard-coded `Locales/nem/nem_truck`); not shipped. `DLC3_Music` is byte-identical to Onslaught's
  (FEV and FSB): built on a base that lists it already, it is not shipped again (one copy below `quarry\data`).
- TrackFiltration: only the PC mode `Nemesis` (Survival; with its SplitScreenName / MultiplayerName also split
  screen and online). The DLC's mode `NemesisRace` is not a track-mode line: `../mode/nemesis_mode.py`.
- Light rigs: `nem_quarry:A:midday_v02` in the nem_ block at its sorted place.
- Texts (`--names`, default THE ROCK / DER FELS / LE ROCHER / LA ROCCIA / LA ROCA; replace them with official ones
  when known): `TEXT_NEM_QUARRY_A`, `_TRACK`, `_SUBTRACK`.
- Quick play picture `TEXT_nem_quarry_A` from the Rock package's own movie and `DLC.TEXTURES` (its
  `FE_QuickPlay_Imgs_SET2` differs from Onslaught's); built on the Quarry build the image is registered as the next
  free name `FE_QuickPlay_Imgs_DLC2` (key moved to 669df7c9, flashtex: unique in the set).
- RESIDENT_DLC.ARK: 135 keys the PC lacks (with the mode files NemesisRace / TruckSurvivalRace and 5 new models).
  49 of them are Onslaught keys too; on a base that has them the base's bytes are kept (43 identical; 6 differ:
  236d1d23 4cd7a028 ce227ead 7c68a456 e79644bb 2fe58f91, camera / spline text and one 340 KB binary, reported as a
  note: on the Xbox each level reads its own package, the PC has one resident.ark).

Combined: build Quarry on the car pack (`--combined step_quarry.ark`), then nem_quarry with `--base` that archive,
then `../mode/nemesis_mode.py` on the result; the files below data go to `glimpse_dlc\quarry\data` and
`glimpse_dlc\nem_quarry\data`. Quarry's outputs stay byte-identical to before (checked against `all_mode_pk1`).

## Installing with the car pack

The file layer (`src/dlcfs.cpp`) lays every `glimpse_dlc\*.ark` over resident.ark in name order, and a later archive
replaces an earlier one's keys. The car pack replaces `directory.txt` and the six dictionaries, the track replaces them
too. So build the track with `--base <pack> --combined <name>`: the combined archive holds every entry of the pack plus
the track, with the shared files merged from the pack's versions. Installing the car pack and that combined archive
(named `track_*.ark`, so it comes after the pack in name order) next to each other is fine: the combined archive
replaces all of the pack's keys with the same or merged bytes, nothing is loaded twice, and with `Tracks=0` it is
not merged at all and the pack alone is in use (this is what `../build_all.py` installs). What does not work is a
track overlay built on the PC files (or on another pack) next to a pack: it would hide the pack's cars from
directory.txt and the dictionaries, or the other way round, depending on the name order.

## For the DLL (src/dlcfs.cpp)

The DLL registers the DLC tracks the merged directory.txt lists, in its order Quarry, nem_quarry, as 15, 16, ...
(index = position: 0x78E2A0 returns the position in the path table). It also serves the quick play movies: the
track list asks the screen for movie `index + 6`, 21 is FE_Locked.bik, so Quarry (15) showed the *locked* movie
and nem_quarry (22) none; cNewQuickPlay's movie slot (vtable 0xBAFD34 slot 24) is swapped for that (see dlcfs.cpp).

Track enum tables: five `{ptr, count = 15}` descriptors whose entries are `{u32 index, char *}`. Quarry takes index 15:

| descriptor | PC table | new entry |
|---|---|---|
| `0xD7E874` track names | `0xD368F8` | `{15, "Quarry"}` |
| `0xD7E880` subtrack letters | `0xD36970` | `{15, "A"}` |
| `0xD7E88C` subtrack paths | `0xD369E8` | `{15, "Environments/Levels/Quarry/Subtracks/A"}` |
| `0xD6FB84` track_subtrack names | `0xD0F9F8` | `{15, "quarry_A"}` (the PC spells them lower-case track + `_` + letter) |
| `0xD6FC40` quick play movies | `0xD10188` | `{15, "data\|Movies/Screens/QuickPlay/FE_QP_Track17.bik"}` |

Note that the PC tables are not index-aligned for the nemesis tracks (`0xD368F8` [12..14] nem_storm, nem_warehouse,
nem_graveyard; `0xD0F9F8` [12..14] nem_graveyard_A, nem_storm_A, nem_warehouse_A), so each table is searched by
string. Extending them is a data write: copy each table with one more entry and point the descriptor at it with
count 16. The six callers that loop with an immediate 15 (`0x4128D0`, `0x4757D0`, `0x51B960`, `0x7D6770`,
`0x4838B0`, `0x474CA2`) stay at 15, and per-track arrays sized 15 (profile best times, `ESPF_UNLOCKED_QUICK_PLAY_TRACK`
flags) have to be checked before index 15 is handed out. Unlock: the quick play lock state and the DLC switches
(`DLCUnlocks%02d/isUnlocked`; the package's DLCINFO says `LEVEL Pack5`, `LICENSEBITS 2`). Leaderboards: none.

## DLL side and open points

- The level archives, the movie and the streamed bank reach the game through the DLL (`src\dlcfs.cpp`, `[DLC]
  Tracks=1`): opens of these names are sent to `glimpse_dlc\<track>\data\...` through kernel32's CreateFileA/W
  import slots, or the files lie below the game's `data` with the same layout. The track enum tables, the
  15-track loops, the quick play movies and the unlock of the track entries are done there too.
- Quick play detail screen: the DLL registers the DLC tracks (Quarry 15, nem_quarry 16), so the exe asks for
  `TEXT_Quarry_A` and `TEXT_nem_quarry_A`; both pictures are built (above). nem_quarry's is not tried in the game
  yet.
- The other Onslaught content (KingOfTheAir mode, helicopter nemeses with `DLC3_nem_helicopter`) is not merged; its
  params are in the 152 keys but nothing names them.
- DLC3_Music is played by the DLL (`src\dlcmusic.cpp`, `[DLC] Music=1`, see docs\INI.md): when Quarry's sound
  loads, `Music/Music` is unloaded and `DLC3_Music` loaded in its place (the game's 6 streams are all in use during
  a race), every `Music/...` event name is aliased to `DLC3_Music/...`, and the picker gets the group
  `Quarry_Track`. Not tried in the game yet; nem_quarry keeps the base music.
- An in-game report of silent race music on Quarry (an earlier build, before the DLL's music) is not explained by
  the data: the events are the base game's `Music/InGame_Track_xx/...` from the unchanged loose `Music.fev/.fsb`,
  identical on every track. The DLL's `audio out` meter reads 0.000 on every channel in the front end too (where
  menu music plays), so it cannot show whether anything was audible; an A/B on a base track with the same build and
  settings is the next test.
- Quarry loads and is playable in the game (tested again on 2026-11-20 with the `build_all.py` build); nem_quarry is
  not tried in the game yet.
