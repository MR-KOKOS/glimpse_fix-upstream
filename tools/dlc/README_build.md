# build_all and install_dlc: your own Xbox DLC, converted and installed in one go

`build_all.py` runs the whole conversion of the user's own Xbox 360 DLC dump with the existing converters and leaves
exactly the files for `<game>\glimpse_dlc\` in a staging folder. `install_dlc.py` copies them into the game folder and
can take them out again. The settings program (`glimpse_config.exe`, page *DLC*) runs both. No DLC data is part of
glimpse_fix, nothing is downloaded, uploaded or sent anywhere: the converted files are made from the user's own
packages and stay on the user's PC. Converted files and test output are kept out of the repository (`.gitignore`:
`*.ark`, `*.pkl`, `*.fsb`, `*.fev`, `*.bik`, `tools/dlc/**/test_out/`).

## Requirements

| what | why |
|---|---|
| Python 3.8 or newer with the py launcher (python.org) | every converter is a Python script; the settings program starts `py -3 -I -X utf8` |
| numpy (`py -3 -m pip install numpy`) | only for the track (the Xenos shader translation, `../xenos`). If numpy is only in the user site-packages, which `-I` hides, the steps run with `-E` instead. Without numpy the track is left out (state `WARN`) |
| `vgmstream-cli.exe` and `ffmpeg.exe` | Quarry's sound banks are XMA on the Xbox (`audio/fsbconv.py`). Looked for in `PATH`, then `glimpse_tools\bin` (next to this folder in a release) and `%LOCALAPPDATA%\glimpse_fix\tools` (also their subfolders `vgmstream`, `ffmpeg`, `bin`), or given with `--vgmstream` / `--ffmpeg`. Without them the track is left out (`WARN`) |
| `D3DCompiler_41.dll` in System32 (DirectX End-User Runtime, June 2010) | the compiler the PC shaders were made with (`../xenos/d3dcomp.py`); it must load in this Python. `fxc.exe` (d3dcompiler_47) makes wrong code for some ps_3_0 shaders, so it is never used instead: without the DLL the track is left out (`WARN`) |
| the PC game folder | read only: `data\resident.ark`, the loose audio banks, and `SplitSecond.exe` with the PC archives for the Havok class layouts (`work\hkref.json`, made in step 1 with `havok/convhkx.py learn`, about 15 s, redone only when the exe or an archive changes; `--hkref <file>` uses a given one instead). It is game data and never shipped |
| about 4 GB free for the work folder | extracted packages (up to 870 MB), the merged car source, intermediate archives, the staging copy (about 600 MB) |

## build_all.py

```
py -3 -I build_all.py --dump <DLC folder> --game <Split/Second folder> [--staging <dir>] [--work <dir>]
    [--no-mode] [--no-track] [--force] [--vgmstream <exe>] [--ffmpeg <exe>] [--status <file>] [--hkref <file>]
py -3 -I build_all.py --check [--staging <dir>] [--game <Split/Second folder>]
py -3 -I build_all.py --preflight [--game <Split/Second folder>] [--dump <DLC folder>] [--work <dir>] [--status <file>]
```

`--preflight` converts nothing: it checks Python, that this folder is complete (in a release: every file of
`glimpse_tools_files.tsv`, written by `tools/make_release.py`, with its size and SHA-256; a build checks that too),
that every converter build_all.py runs loads (`ENTRY_SCRIPTS`; the track's only when the track can be built), what the
track needs, and with `--game` the game folder and its Havok class layouts (made in the work folder as a build would),
with `--dump` the packages found. `RESULT: OK | WARN | FAILED`, exit code 0 / 0 / 1, the same status file as a build.

`--tu4-texts <json>` (or `GLIMPSE_TU4_TEXTS`), optional: a TU4 text table (JSON) made outside glimpse_fix. The
official texts need it, and the release cannot produce it (it is made with a local development tool that is not part
of glimpse_fix; game data, never shipped). Without it the converters write their own texts. It is passed to every step; its hash is part of every step's inputs, so a changed table rebuilds.

`--dump` is the title folder `425607E7`, its `00000002` subfolder or a folder above (searched 4 levels deep). Every
STFS package (`LIVE`, `PIRS`, `CON `) of title 425607E7 with content type 2 is listed; title updates and other games
are named and skipped. The packages are told apart by their `DLCINFO.TXT`; of two packages with the same pack name the
higher version (DLCINFO's first word) is used, which picks "The Elite" Vehicle Livery Pack (13B21D5F...) over the older
"Elite Vehicle Livery Pack" (EF1A90C1..., no version).

| DLCINFO | package | used for |
|---|---|---|
| `Pack4` | Deadline | Musclecar_17, Musclecar_18, Musclecar_19, Truck_09; the game mode Deadline; the base of the merged car source (its car select UI has the frames of all eleven DLC cars) |
| `AllNewSupercars` | High Octane | Supercar_17 .. Supercar_20 |
| `NewEliteLiveries` | The Elite | Unique_21, Unique_22, Unique_23 |
| `Pack5` | Onslaught | the track Quarry |
| `Nem_Quarry` | Survival At The Rock | the nemesis track nem_quarry and the game mode Survival Race |
| (none; `DLC/Unlock/Unlock.params`) | Master Unlock | listed, not needed |

Defaults: work `%LOCALAPPDATA%\glimpse_fix\dlc_work`, staging `%LOCALAPPDATA%\glimpse_fix\dlc_staging`. Both are
refused inside the game folder or the DLC folder, or holding one of them, also through a link or junction (checked
as given and with the real paths). The work folder must be new, empty (the settings program's logs, status files and
`build.lock` aside) or one build_all.py made: on first use it writes `glimpse_dlc_work.txt` into it, and later runs
need that file. A folder of an earlier build_all.py without it (its `stamps\` folder) is taken and gets the file; any
other folder with files in it is refused, as the steps clear and rewrite folders in it.

| step | what | tool |
|---|---|---|
| 1 | Python, the game, the packages, vgmstream/ffmpeg, D3DCompiler_41, numpy (track only), the tools folder, free space; the Havok class layouts `work\hkref.json` | `havok/convhkx.py learn <game>\data\*.ark <game>\data\environments\levels\*.ark <game>\SplitSecond.exe` (with `GLIMPSE_HKREF` = the new file) |
| 2 | extract the car packages and Onslaught to `work\packages\<role>`, every file's size checked | `stfs.py` (module) |
| 3 | with more than one car package: one source folder `work\merged_<signature>` (the first package present whole, from the others `Vehicles/**` and `Audio/**`, the database and the two tweakable dumps merged) | `assemble/merge_dlc.py --base <deadline> --add <highoctane> --add <elite>` |
| 4 | car select bodies of every car | `frontend/convfrontend.py --pc` |
| 5 | car pack `work\pack\dlc_cars.ark` with every car and the game mode Deadline (`--no-mode`: the cars alone; no mode without the Deadline package) | `assemble/build_pack.py --models <all cars> --dlc <merged> --mode` |
| 6, 7 | per DLC track (`TRACK_SPECS`: Quarry, then nem_quarry), each on the previous overlay: its audio (Quarry: `AUDIO/RELEASE/LOCALES/QUARRY`, `AUDIO/LOCALES/QUARRY`; nem_quarry: `AUDIO/LOCALES/NEM_QUARRY`, only its locale XMLs are used; the first track built also its package's music project `AUDIO/RELEASE/*_MUSIC`, one copy serves both tracks), then the track: level archives, movie, streamed banks, the overlay `work\track[_nem_quarry]\glimpse_dlc\step_<track>.ark` | `audio/dlcaudio.py --no-reuse` (through `build_all.py _dlcaudio`, so `fsbconv.py` uses the vgmstream-cli and ffmpeg found in step 1); `assemble/assemble_track.py --track <t> --base <car pack or previous overlay> --xlate-cache work\track_xlate_cache.pkl` |
| (8) | with nem_quarry: the game mode Survival Race on the last overlay, its quick play movie to `nem_quarry\data`; the result is `track_quarry_combined.ark` (car pack + tracks + modes) | `mode/nemesis_mode.py --base <overlay> --dlc <rock> --out ... --data-out work\nemesis_mode\data` |
| last two | staging (also `deadline\data\movies\screens\quickplay\fe_deadline.bik` from the Deadline package when the mode is built), check | |

The cars of a package are taken in the order of the tested packs and only if the package has their body and front-end
folders. `--no-reuse`: no sample of a DLC bank is replaced by the PC encoding of an identical base-game sample, since
that needs the Xbox base `resident.ark`, which users do not have; `pair_lib.py` takes that archive only from
`GLIMPSE_XB_RESIDENT`, which `build_all.py` removes from the environment of its steps. A build on the developer's PC
is therefore the same as on every other.

Each step writes `work\stamps\<step>.json` with its inputs (package size and time, `resident.ark` size and time, the
SHA-256 of every `.py`/`.json` below `tools\dlc`, options, the previous step's result) and is skipped while they
are unchanged and its outputs exist; `--force` redoes all. A step that runs first removes its own output folder in
the work folder (`packages\<role>`, `merged_*`, `frontend`, `audio_menu`, `pack`, `audio[_nem_quarry]`,
`track[_nem_quarry]`, `nemesis_mode`; never anything outside the work folder), so no file of an earlier run with other
packages, cars or tracks is left in it; the stamps carry a layout number, so steps done by an older `build_all.py` are
redone once. The merge step depends only on `merge_dlc.py` and the
modules it uses; its previous output folder is removed when it runs again (`merge_dlc.py` takes an empty folder). Each
converter's whole output is in `work\logs\<step>.log`; when one fails, its `[FAIL]` lines (or the Python exception)
are the reason shown. `work\build.lock` (with the process ID) keeps two builds or a build and an install apart.

### The staging folder

Exactly what goes into `<game>\glimpse_dlc\`, plus `glimpse_dlc_manifest.tsv`:

| file | from |
|---|---|
| `dlc_cars.ark` | the car pack overlay (every converted car, the game mode Deadline) |
| `track_quarry_combined.ark` | the car pack + Quarry in one overlay. It comes after `dlc_cars.ark` in name order and holds every entry of the pack, so with `[DLC] Tracks=1` it replaces the pack's keys with the same or merged bytes; with `Tracks=0` it is not merged and the pack alone is in use |
| `quarry\data\...` | the four level archives, `fe_qp_track17.bik`, `quarry_streamed.fsb`, the music project; the DLL opens a missing `data\` file from `glimpse_dlc\quarry\data\` |

The manifest (UTF-8, tab-separated): `version`, `built`, `dump`, `game`, `track`, `mode`, `cars`, `packages`; `ini
<key> <value>` for the `[DLC]` keys the files need (`Overlays=1`, `Modes=1` with the mode, `Tracks=1` with the track);
`file <path> <size> <sha256> <what>` per file. A rerun copies only what changed and deletes from the staging folder only
files its previous manifest lists. A staging folder with other files and no manifest is refused. A track's files are
staged from its manifest (`track_<t>.manifest.tsv`: the four level archives and its `loose` lines) only; a file below
`work\track[_nem_quarry]\data` that the manifest does not list stops the build.

`--check` verifies the staging folder: every file listed, its size and SHA-256, every `.ark` (PC version, header size,
keys sorted and unique, entries inside the file), track overlay and track files together. A file in the staging
folder that the manifest does not list is an error (not from the build that wrote the manifest; only Explorer's
`desktop.ini`/`Thumbs.db` are tolerated), so the build's own check and `install_dlc.py` refuse it. With
`--game` it also says for each file whether the game folder has it identical, different or not at all, and names other
`.ark` files in `glimpse_dlc` (read only).

### Status file

With `--status <file>` both scripts rewrite a small UTF-8 file at every step (written to a temporary name and renamed):
`state` RUNNING, OK, WARN or FAILED; `step` n/N; `text` one line for the user; `detail` lines (the failed checks, the
plan); `other` lines (install: archives in `glimpse_dlc` that are not from this build). The settings program reads it
twice a second. Exit codes: 0 done, 1 failed (the reason in the status file and at the end of the output), 2 internal
error (traceback).

## install_dlc.py

```
py -3 -I install_dlc.py --game <Split/Second folder> [--staging <dir>] [--saves <dir>] [--apply] [--move-others]
py -3 -I install_dlc.py --game <Split/Second folder> --uninstall [--apply]
```

Without `--apply` it checks the staging folder and prints the plan, per staged file: *copy*; *keep (identical)*;
*replace (installed before)*, only for a file it copied itself that still has the SHA-256 it was copied with; otherwise
*replace (the old file is moved to glimpse_dlc_replaced_<date>)*. Files it copied before that are no longer staged:
*remove* (only with their recorded SHA-256), else *leave*. The state is `WARN` when other `.ark` files lie in
`glimpse_dlc` (the game lays them over its data too; each is named in an `other` line) or the last install was
interrupted (a `*.glimpse_part` file or an unfinished list).

With `--apply`, in this order: refuses while `SplitSecond.exe` runs or the game drive lacks the space; copies the save
folder (`Documents` as Windows has it, also when moved to OneDrive, `\Disney Interactive Studios\Split Second`) to
`Split Second backup_<date>_dlc` next to it and compares the copy's file sizes; removes leftover `*.glimpse_part` files;
keeps `glimpse_dlc\glimpse_dlc_installed.tsv` up to date before every step (a file to be copied is listed as `copied`
with the staged content, a file to be moved aside gets its `aside` line, both before it happens), so an install that
stops anywhere can be undone with `--uninstall`; copies each file to `<name>.glimpse_part` and renames it; moves replaced files of others (and with `--move-others` the other `.ark` files)
to `<game>\glimpse_dlc_replaced_<date>` (`_2`, `_3` ... if that name is taken; only created when something is moved);
removes its own files that are no longer staged and puts a file that an earlier install moved aside from such a name
back there; checks the installed files and then writes the final list. A file moved aside keeps its own spelling
(`DLC_Cars.ark`); names are compared as Windows compares them, and nothing is ever moved onto an existing file. A file
error ends it with the message "the install is incomplete; run Install again", and the next run completes it.

`glimpse_dlc_installed.tsv`: as the staging manifest, each `file` line with its origin, `copied` or `adopted` (it was
there already with exactly the staged content), and an `aside <path> <where it went> <size>` line per file moved away.
Adopted files are never removed. `--uninstall` removes the copied files that still have their recorded content, leaves
the adopted and changed ones, moves every file it moved aside back where its name is free (and removes the emptied
`glimpse_dlc_replaced_<date>` folder), after the same save backup, and removes the list. The INI is left to the
settings program, which offers the manifest's `ini` keys after an install and turning off *Converted DLC* after an
uninstall.

## Tests (2026-11-20, the user's dump, scratch work and staging folders, the game folder only read)

- Packages: Deadline, High Octane, "The Elite" (13B21D5F, version 1; the older EF1A90C1 copy reported as not used),
  Onslaught used; Survival At The Rock listed as not converted yet, Master Unlock as not needed, the two title
  updates skipped (content type 000B0000). Works with `425607E7` and with `00000002`.
- Full build: merge 3 s, frontend 18 s (11 cars), pack 42 s, audio 9 s, track 62 s (with the translation cache);
  10 files, 679 MB, check OK.
- `dlc_cars.ark` byte-identical to `build_pack.py`'s own `pack_v4_elite_mode.ark` (the same 11 cars, `--mode`), with
  the converters as of 21:30. A later `deadline_mode.py` adds the 11 Deadline pickup files (`.pkps`); the 799 other
  entries stay byte-identical.
- `track_quarry_combined.ark` against `assemble_track.py`'s own `all_mode_v1\glimpse_dlc\dlc_combined.ark` on that
  pack: 959 of 961 entries byte-identical, the four level archives, the movie and `quarry_streamed.fsb` byte-identical.
  The two differences come from the inputs: `audio/release/locales/quarry/quarry.fsb` (here `--no-reuse`, 3,586,112
  bytes, every sample encoded from the XMA; there 13 of 58 samples from the PC base, 3,382,560 bytes) and
  `Audio/StreamedAudioFileList.txt` (here with the music project DLC3_Music; that build was made without it).
- `fmodcheck.exe` with the game's fmodex.dll: the `--no-reuse` `quarry.fsb` loads (58 subsounds, as compressed sample
  and as sample), `quarry.fev` 61 events, all start; `dlc3_music.fsb` 33 subsounds, `dlc3_music.fev` 49 events.
- Cars only (`--no-mode`, Deadline alone, earlier version): byte-identical to the `deadline_v2.ark` tested in the game.
- Rerun with unchanged converters: every step "up to date", staging "same as before", a few seconds. A changed
  converter reruns the conversion steps, not the extraction or (unless `merge_dlc.py` or its modules changed) the merge.
- Without vgmstream-cli: state WARN, "Quarry left out", only `dlc_cars.ark` staged.
- `install_dlc.py` against a scratch game folder and scratch saves, with a stray `old_manual.ark`, a different
  `track_quarry_combined.ark`, an identical `fe_qp_track17.bik` and a leftover `.glimpse_part`: dry run WARN (other
  archive, interrupted) and nothing changed; `--apply --move-others` backed up the saves, removed the leftover, moved
  the two other files to `glimpse_dlc_replaced_<date>`, copied 8, adopted the identical one; a second `--apply` found
  10 identical; `--uninstall --apply` removed the 9 copied files, left the adopted one, moved the two files back and
  removed the emptied `glimpse_dlc_replaced_<date>`: the game folder as before, without the leftover.
- `install_dlc.py` dry run against the real game folder: plan only, the folder unchanged, no list written.
- `glimpse_config.exe` (LOCALAPPDATA and game folder in the scratchpad): Build on an empty folder ends with the reason
  in red; Install shows the plan, then the question about the stray archive, and changes nothing on Cancel; Uninstall
  without an install says there is nothing to remove.

`build_all.py --check --staging <dir> --game <game>` repeats the comparison with the installed files at any time.
