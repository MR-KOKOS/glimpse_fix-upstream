# DLC audio: Xbox 360 -> PC

Converts the audio of the user's own Xbox 360 DLC packages (Deadline, Onslaught) into the PC formats.
Offline only; nothing here touches the game, its code or its saves.

## Formats (Xbox vs PC, checked on base-game pairs)

| File | Xbox 360 | PC | Conversion |
|---|---|---|---|
| `.tonic` / `.tonic2` (Tonic engine sound, 44.1 kHz / 21 kHz sets) | big-endian headers, platform word 1 | little-endian, platform word 0 | byteswap of every header/table word; the 4-bit sample data is the same bytes on both (`tonic.py`) |
| `.fsb` (FMOD Ex FSB4, version 0x40000, flags 0x40) | XMA (mode 0x01000000), 0x50-byte header + extra (XMA seek table, `SYNC` chunk), data in whole 2048-byte blocks; Generic_LFE holds big-endian PCM at 500 Hz (flag 0x8) | MPEG-1/2 Layer II (mode 0x200 and 0x40000), plain 0x50 headers, only a `SYNC` chunk as extra, frames back to back, every sample's data and the header area padded to 32 bytes; 128-320 kbit/s mono, 96-160 kbit/s stereo; rates Layer II lacks are resampled (500 Hz -> 16 kHz) | decode (vgmstream) + encode (ffmpeg `mp2`), same sample order, names, mode flags, defaults (`fsbconv.py`) |
| `.fev` (FMOD Designer event file, FEV1 0x3D) | little-endian, same layout | same | none. Xbox and PC copies are the same size and differ in a few mix values only (e.g. 3 helicopter event volumes) |
| `.xml`, `.txt` | text, LF | text; the level audio XMLs (`Audio/Locales/<loc>/*.xml`) are the Xbox files with CRLF (24 of 24 base pairs) | none here; `../assemble/assemble_track.py` writes the locale XMLs with CRLF |

Container offsets at 0x48 of a sample header hold the header size again (u32), on both platforms.
The PC FMOD (fmodex 4.28.09) rejects an Xbox bank outright (`FMOD_ERR_FORMAT`).

## How the PC game finds the banks

- FMOD runs through the game's file system (`System::setFileSystem`); paths are
  `Audio/Release/<bank>.fev`, and FMOD opens `<bank>.fsb` next to it. Banks listed in
  `Audio/StreamedAudioFileList.txt` (inside resident.ark) are loose files under `data/audio/release/`; all
  others live in `resident.ark` (key h*31+c over the lower-case path; `manifest.txt` lists the keys).
  The Xbox uses Audio/Submission/; the DLC packages carry byte-identical Release and Submission copies.
- Level audio: `FUN_007f1360` takes the level name, cuts it at the first `_` (`nem_x_y` keeps `nem_x`),
  registers the event aliases `locale/` and `locale_Streamed/` for it and loads the `<locale>` bank (memory group
  `Level`) and the `<locale>_Streamed` bank (streamed, `0x7F3030(name, 2)`), so Quarry asks for
  `Locales/quarry/quarry(.fev/_Streamed)`; then `0x83C370` reads `Audio/Locales/quarry/quarry_Audio.xml`,
  `_Audio_Ambient.xml`, `_Audio_Reverb.xml` (and `_Audio_Swooshes.xml`), all from resident.ark.
- FEV header: project name, then per wave bank {u32 load mode, u32 max streams, 8-byte bank hash, name}; the hash
  is bytes 0x18..0x1F of the FSB4 header (kept by `fsbconv.py`). PC level banks are 0x200 (into memory), the
  streamed level banks and `Music` 0x80 (stream) with 2 streams; the DLC banks have the same values. Some PC FEVs end
  with a 16-byte `sett` chunk after `comp` (two floats 1.0), others not (`Music.fev`); both load.
- Race music: the same on every track, from `Music/Music` (loose, loaded at start-up by `0x7F5900`). The picker
  (`0x83DD70`, `0x83D9F0`) cycles a shuffled list of 14 groups `InGame_Track_01..15` (no 13) and plays
  `Music/<group>/RaceSection0<n>`, `Music/PreRace/PreRaceShuffled`, `Music/<group>/ResultScreen`; no level data
  chooses it. Details in `../assemble/README_track.md` (Level audio and music).
- Cars: the player's car loads Tonic files from `VehicleDatabase.xml` `AudioFile` (the DLC maps
  Musclecar_17 -> Hypercar_20, Musclecar_18 -> Truck_20, Musclecar_19 -> Truck_21, Truck_09 -> Muscle_20):
  `audio/tonic/_stateengine/<AudioFile>.xml` (a bundle of the `_cartypespecific` and `_carspecific` XMLs) and
  `audio/tonic/_outputfiles/<AudioFile>.tonic(2)`. CPU cars pick from a fixed table of 22 base audio names
  (`FUN_007f30c0`, `Vehicles/CPU/%s` + `%s/Tonic/%s`), so they never use DLC sounds.
- The PC exe has no string for `DLC3_Music`, `DLC3_nem_helicopter` or `DLCMenu`; nothing loads those banks
  without code (the helicopter events are hard-wired as `nem_helicopter/...`).

## Usage

```
python -I dlcaudio.py <out dir> <Deadline package dir> <Onslaught package dir> [--check] [--codec pcm]
python -I fsbconv.py <xbox.fsb> <pc.fsb> [--codec mp2|pcm] [--stereo-kbps 160] [--mono-kbps 128] [--no-sync]
python -I tonic.py <xbox.tonic> <pc.tonic>
python -I cmpfsb.py <converted.fsb> <pc.fsb>              structural comparison
python -I test_tonic.py                                  oracle: all base-game tonic pairs
python -I test_fsb.py <work dir> [bank ...]               oracle: 22 base-game bank pairs
fmodcheck.exe <game dir> fsb <bank.fsb> [dump dir]         load with the game's fmodex.dll (no sound output)
fmodcheck.exe <game dir> fev <media dir\> <name.fev>       load the event file, get and start every event
fmodcheck.exe <game dir> fevprops <media dir\> <name.fev>  dump all event properties (to diff two .fev)
```

`dlcaudio.py` writes lower-case game paths plus `manifest.txt`; XMLs that are already byte-identical in the
PC resident.ark (the four `_cartypespecific` files) are left out. `quarry_streamed.fsb` is byte-identical to
the Xbox `graveyard_streamed.fsb`, so the PC `graveyard_streamed.fsb` is copied. Samples of a DLC bank whose
XMA data equals a base-game sample are taken from the PC base bank (13 of 58 in quarry.fsb).
Needs vgmstream-cli and ffmpeg (in `PATH`, or where `build_all.py` looks, see `../README_build.md`); `fmodcheck.exe` is
32-bit (`build_fmodcheck.bat`, VS 2022 Build Tools). Base-game pairs come from `../pair_lib.py` paths.

Loop and sync points: Layer II decoding delays the audio by exactly 481 samples (measured on FMOD's own
decode of the converted banks), so partial loop points and sync points are shifted by 481 and samples with a
partial loop get 481 samples of padding. Whole-sample loops stay 0..length-1, as in the PC banks.

## Test results

- Tonic: 60 of 60 base-game Xbox files (30 cars, .tonic and .tonic2) convert byte-identical to the PC files.
  All 8 DLC files parse completely and re-parse as PC files.
- FSB parser: every PC bank tested (19, resident and loose) rebuilds byte-identically.
- Base-game banks (22 Xbox banks, 937 samples): all load in the game's FMOD (compressed sample, sample and
  stream modes), and every event of the Xbox .fev (673 events) loads its waves and starts against the
  converted bank. In the 20 banks whose content matches the PC build, 764 of 774 samples have exactly the PC
  length and 10 are within one frame. Names, order, channels, rates, mode flags, header sizes, sync points
  and layout match. The only remaining difference is the loop range of about 18 LFE samples (PC 1..n or 16..n,
  converted 0..n-1). menu and docks differ in content between the Xbox and PC builds.
- Decoded audio (FMOD decode vs vgmstream decode of the XMA, lag 481): correlation 0.91-0.999, median
  waveform SNR about 21 dB at 160 kbit/s (the lowest are noise-like helicopter loops).
- The harness tells good from bad: an Xbox bank gives `FMOD_ERR_FORMAT` (25), a missing bank gives `FMOD_ERR_FILE_NOTFOUND` (23).
- DLC: all 5 banks load; DLC3_Music 49, DLC3_nem_helicopter 14, quarry 61, quarry_Streamed 6, DLCMenu 10
  events load with no failures.

## Open issues

- Placement (done by `../assemble/assemble_track.py` and the car pack builders): the in-archive banks, Tonic files
  and locale XMLs as resident.ark overlay keys; loose `data/audio/release/locales/quarry/quarry_streamed.fsb` and
  `data/audio/release/dlc3_music/dlc3_music.fev/.fsb`, each listed in `Audio/StreamedAudioFileList.txt`. The Quarry
  level banks run in the game; the music project is not loaded by it (below).
- DLC3_Music, DLC3_nem_helicopter and DLCMenu convert and load, but the PC exe never asks for them (no string
  `DLC3_Music` or `Quarry_Track`). DLC3_Music is shipped with the track for a DLL loader (load as
  `0x7F3030("DLC3_Music/DLC3_Music", 2)`, picker group `Quarry_Track`, event alias `Music/Quarry_Track/` ->
  `DLC3_Music/Quarry_Track/`); without one Quarry plays the base race music.
- `quarry.fsb`: sample 7 is `INITIAL.wav` (Xbox `sawend.wav`): its XMA data equals a base sample, and the reused PC
  sample keeps the PC name. The FEV refers to waves by index, so this is cosmetic.
- Memory: the game calls `FMOD_Memory_Initialize`; pool size unknown. `--codec pcm` (exact, no codec delay)
  makes banks about 8x larger than mp2, so mp2 is the default.
- The PC music bank has the SYNCPOINTS flag but no sync data; converted banks keep the Xbox sync points
  (`--no-sync` drops them).
- The meaning of the few remaining FEV byte differences (e.g. one byte 2 vs 0 in tonic.fev) is unknown. They
  are not event properties, and the Xbox files load as they are.
