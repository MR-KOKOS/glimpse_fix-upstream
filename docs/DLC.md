# Your own Xbox DLC (experimental)

The short version is in the [README](../README.md#your-own-xbox-dlc-experimental). This page has the details: what is
converted, what the build needs, what *Set up DLC* does step by step, and the command line.

The Xbox 360 version of Split/Second had downloadable content the PC version never got: the Deadline
car pack with the Deadline game mode, the Onslaught pack with the track Quarry, and more. If you
bought it on your Xbox 360, glimpse_fix can convert **your own copy** on your PC so the PC game loads
it. **No DLC content is part of glimpse_fix or downloaded by it**: without your own dump there is
nothing to convert, and the converted files stay on your PC. Please do not share them.

**Compatibility:** the Steam version 1.0.0.1 (German and English depots), not the Russian depot; on any
other executable the fix leaves the converted DLC off. The Russian depot is left out on purpose: the DLC
part relies on game addresses that are only worked out for the Steam build. All other features of the
fix work there as before. Support for the Russian depot may follow later, depending on how the beta
tests go.

## What is converted

Converted so far, from whichever of these packages your dump has:

* *Deadline*: Cobretti Centaur, Ryback Javelin, Ryback Coyote Special, Hanzo Kanobo, and the game mode
  Deadline (a countdown time trial) in quick play, with its time pickups (the Xbox's hexagonal token
  model, from your own DLC) and its own icon in the game mode instructions before the race;
* *High Octane*: Ryback Cyclone Special, Cobretti Severus, Ryback Vulcan, Hanzo Katana;
* *The Elite* livery pack: Elite 440 BRN, Elite S510 RP, Elite S510 VX;
* *Onslaught*: the track Quarry in quick play;
* *Survival At The Rock*: the nemesis track nem_quarry (*Minepit Park*, track 16) for Survival, and the
  split-screen game mode Survival Race.

All cars go into one car pack. *Master Unlock* is not needed.

### Texts

The DLC's official texts (the help lines of Deadline and Survival Race, the pickup message and hint in
the game's language, the car descriptions, the rivals' names) are not in the DLC packages: the Xbox's Title
Update 4 carries them inside its program. A normal build therefore uses glimpse_fix's own texts, which explain the
same rules in other words. The official texts need a TU4 text table that the release cannot produce (it is made
with a local development tool that is not part of glimpse_fix); without it the converters write their own texts.

## What you need

* Your own DLC from the Xbox 360, copied off the console as it stores it: the folder `425607E7`
  (Split/Second's title ID) with the subfolder `00000002` and the package files in it (the long hex
  names, as the console keeps them in `Content\<profile>\425607E7\00000002`).
* About 4 GB free on drive C: for the conversion, 0.5 GB on the game's drive.
* The converters: the folder `glimpse_tools` of the release zip, copied into the game folder with
  `glimpse_config.exe` (from the source: `tools\dlc`; if they are somewhere else, the settings
  program asks for the folder the first time).
* The programs the converters run. The easy way: the **DLC tools** zip of the release,
  `glimpse_fix-dlc-tools-<version>.zip`, a download of its own next to the main zip (about 90 MB; not
  part of glimpse_fix itself, see below). *Set up DLC* in the settings program downloads it only when
  it is missing and only after you say yes (it shows the size and the address first), from the GitHub
  release of the same version, checks it against the SHA-256 checksum published next to it and unpacks
  it into `%LOCALAPPDATA%\glimpse_fix\tools`: nothing else to install, no admin rights.
  `glimpse_tools\dlc\setup_deps.bat -Install` does the same from a command prompt (a zip you downloaded
  yourself: `setup_deps.bat -Install -BundleZip <zip>`, or unpack it there as it is). It holds:

| Program | Used for | Without it |
|---|---|---|
| Python 3 (the official embeddable Python of python.org, 64 bit) | runs the converters | nothing can be converted; the settings program says so |
| numpy (the official wheel of PyPI, inside that Python) | translating Quarry's Xbox shaders | Quarry is left out (state WARN), the cars and the mode are built |
| vgmstream-cli (the vgmstream release for Windows) | decoding Quarry's sound banks (XMA on the Xbox) | Quarry is left out (WARN) |
| ffmpeg (an LGPL build of FFmpeg-Builds, unmodified) | encoding them for the PC (MPEG Layer II) | Quarry is left out (WARN) |

  Not in it, as Microsoft passes it on only through its own installer: `D3DCompiler_41.dll` (DirectX
  End-User Runtime, June 2010), which compiles the translated Quarry shaders; without it Quarry is left
  out (WARN). The game's installer normally puts it in place. When it is missing, *Set up DLC* asks
  whether to install it and then runs Microsoft's own DirectX installer (`setup_deps.bat -DirectXOnly`;
  Windows asks for admin rights); `setup_deps.bat -Install` does that too.

  Each program keeps its own license (Python: PSF; numpy: BSD; vgmstream: ISC style; FFmpeg: LGPL 2.1+);
  the zip has `THIRD_PARTY_dlc_tools.txt` with where each one's source is, and a manifest with the
  version, download URL and SHA-256 of everything. It is put together from the official downloads by
  `tools\make_dlc_tools.py`, each checked against the SHA-256 its source publishes.

  The other way: install them yourself (Python 3.8 or newer from python.org with the *py launcher*,
  `py -3 -m pip install numpy`, vgmstream-cli, a Windows build of ffmpeg), or `setup_deps.bat -Install
  -NoBundle`, which gets each in its current version from its official source (Python with winget,
  numpy with pip, vgmstream-cli and ffmpeg portable into `%LOCALAPPDATA%\glimpse_fix\tools`). Without
  `-Install`, `setup_deps.bat` only shows what is there and what is missing; `-Update` also replaces an
  older bundle or updates what is there.

When the bundle is there, `build_all.py` runs in its Python (it restarts itself there) and takes its
vgmstream-cli and ffmpeg; otherwise they are found in `PATH`, in `glimpse_tools\bin` or in
`%LOCALAPPDATA%\glimpse_fix\tools`. The game folder is found through Steam (the Steam library that has
Split/Second) unless you give one. `glimpse_tools\dlc\build_all.py --preflight` (run with the bundle's
`python.exe -I`, or `py -3 -I`) checks all of it without converting anything. The Havok class layouts the
converters need are read from your `SplitSecond.exe` during the build (about 15 seconds the first time).

## Set up DLC, step by step

In the settings program, page *DLC*, click **Set up DLC**. It does everything in one go and asks
before anything is downloaded or installed:

1. The game folder: the one of the `SplitSecondPad.ini` you are editing, else the one Steam has
   (it reads Steam's folder from the registry, its library list `libraryfolders.vdf` and Split/Second's
   `appmanifest_297860.acf`, read only). Started outside the game folder, the settings program opens
   the INI of the game folder Steam has; *Open INI...* picks another one.
2. Your DLC: the *Xbox DLC folder* if it is set and holds packages, else it looks in the usual places
   (Xenia's `content` folders, the Desktop, Downloads and Documents, the Xbox layout on fixed and USB
   drives; never whole drives, never network drives, a second or two at most). Each package's header is
   checked (Split/Second's title ID, DLC content). If it finds different dumps, it lets you pick; if it
   finds none, it opens the folder picker. *...* next to the folder shows what it finds, too.
3. The DLC tools, only if they are missing: it shows the size (about 90 MB) and the GitHub release
   address and downloads them after your yes, checked against their published SHA-256 checksum (see
   above). Without them it can still use a Python of your own (the tracks may then be left out).
4. The DirectX runtime, only if `D3DCompiler_41.dll` is missing: after your yes, Microsoft's own
   installer (admin rights); with no, the cars and the Deadline mode are built without the tracks.
5. The build: converts everything on your PC (`tools\dlc\build_all.py`), about 10 minutes the first
   time, a minute or two after that, since finished steps are kept. The progress is shown below the
   buttons; *Stop* (the same button) stops the download or the build, *Open log* shows the details. The
   game folder is not touched: the result goes to `%LOCALAPPDATA%\glimpse_fix\dlc_staging`, the
   intermediate files to `...\dlc_work`.
6. The install: it says in plain words what it will add and where, and installs after your yes. It
   copies your saves first (`Documents\Disney Interactive Studios\Split Second` to
   `Split Second backup_<date>_dlc`), then the converted files into `glimpse_dlc` in the game folder. It
   only ever replaces or removes files it copied itself (it keeps a list,
   `glimpse_dlc\glimpse_dlc_installed.tsv`). Other archives in `glimpse_dlc` (for example from an older
   manual install) are named and moved to `glimpse_dlc_replaced_<date>`, since the game would lay them
   over its data as well.
7. The settings: it turns on what the build needs (*Converted DLC*, *DLC tracks Quarry and Minepit
   Park*, the *Deadline game mode* on the page *Deadline mode*, ...) and shows a summary. *DLC cars in
   the car select without Unlock all* is on by default.

Advanced, the single steps (smaller buttons next to *Set up DLC*): *Build* converts only (step 5, for
the folder set and the game folder of this INI), *Install* shows what it would do and then installs
(step 6, without turning on any settings: the program offers that afterwards), and *Uninstall* takes
it out again the same way: it shows what it will do, copies your saves, removes the files Install
copied and puts back what it moved aside.

## Command line

Without the settings program, from a command prompt in `glimpse_tools\dlc` (`tools\dlc` in the source; details in
[tools/dlc/README_build.md](../tools/dlc/README_build.md)):

```
py -3 -I build_all.py --dump "<...>\425607E7" [--game "<...>\steamapps\common\SplitSecond"]
py -3 -I install_dlc.py [--game "<...>\steamapps\common\SplitSecond"]            (dry run)
py -3 -I install_dlc.py [--game "<...>\steamapps\common\SplitSecond"] --apply
```

(`py -3`, or with the DLC tools `%LOCALAPPDATA%\glimpse_fix\tools\python\python.exe -I`; without `--game` the
Steam install of Split/Second is used.)

To take it out again: `install_dlc.py [--game <...>] --uninstall` (dry run, then `--apply`), or just turn
off *Converted DLC*.
All of this is experimental: online, every player needs the same converted track (see
`TracksOnline` in [INI.md](INI.md)).
