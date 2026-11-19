# glimpse_fix: how it works

The details behind the features in the [README](../README.md): what the port gets wrong, how the fix changes it without touching the game code, and the notes for building and research.

## Supported executables

All addresses come from the Steam executable. On a different exe the byte checks fail and the
game-specific patches (60 fps, streaming pool, prompt icons, trigger preset) switch themselves off;
whether the rest works there is unknown.

The Steam version ships more than one `SplitSecond.exe`: the language you pick in Steam decides
which one you get. Supported are the build of the German and English depots (built `4BE13C66`, the
one the fix was made on) and the build of the Russian depot 297867 (built `4BE3EA74`), whose
addresses are mapped from a table (`src/build_ru.inc`). The Polish depot 297866 is a third build and
not supported; French, Italian and Spanish have not been checked. The log names the build it found
(`SplitSecond.exe build recognised (...)`) or says `unknown SplitSecond.exe build`.

## What was wrong, and what the fix does

### Controllers

The port reads joysticks only through DirectInput and picks a binding preset by USB VID/PID.
A complete layout (menus, driving, camera) exists only for the wired Xbox 360 pad
(`045E:028E` / `045E:02A1`). Every other controller — Xbox One/Series included — gets the
generic preset, which is **empty**: no bindings at all, so menus can't be used. On top of that
DirectInput puts LT and RT on one shared Z axis, so throttle and brake cancel out, and the
prompt bar is hard-wired to keyboard keys although its Flash movie still contains the Xbox and
PS3 icons.

* Every XInput slot is exposed to the game as a virtual "Controller (XBOX 360 For Windows)", so
  the original console layout applies to any XInput device. Plugging a pad in while the game
  runs works.
* LT and RT get their own axes; the 360 preset is adjusted (throttle RT, brake LT).
* The virtual pads come first in the device list, so the game's joystick index matches the
  XInput slot it rumbles.
* The prompt bar shows A, B, X, Y, Start (or Circle, Square, Triangle) while the pad is the device in use and
  switches back to key captions as soon as a key is pressed. The title screen reads
  "Press START" instead of "Press ENTER" (any language). The controls screen names pad inputs
  A/B/X/Y/LB/RB/LT/RT/LS…/RS… instead of "Button 1" or "Z rotation".

### 30 fps

The game simulates in fixed 1/60 s ticks but its main loop renders every 1/30 s and hands two
ticks to each frame; physics and race logic use `dt = ticks × 1/60`. The only per-frame code that
assumes a 1/30 s frame is the AI player update (once per car per frame; its steering controller
limits the steering change per call by 1/30 s). Speeding up just the main loop makes the AI react
twice as fast — the reason the old 60 fps patch made AI cars crash constantly.

The loop adds the measured frame time to an accumulator, takes two ticks per 1/30 s in it, sleeps
away the rest and clamps the ticks to a "max ticks" tweakable. All of that is plain data, read only
by the loop, so 60 fps work without changing its code:

* After every presented frame (Direct3D `Present`) the fix waits for the next 1/60 s on a high
  resolution timer, sets "max ticks" to the number of 1/60 s steps that have passed (1, more after
  a slow frame) and tops the accumulator up so the loop finds that many ticks and never sleeps.
* Each AI car is updated every other frame and drives on its last inputs in between, exactly as
  at 30 fps with two ticks per update (the AI player's vtable slot). Restarting a race clears each
  AI car's track section and the next update finds it again, so that update is never skipped:
  another AI method reads the section every frame.
* Above 60 fps would need render interpolation, since the ticks are fixed at 1/60 s.

### Blurry / missing textures and props

The sector streaming pool is set to 57 MB, a console-era budget. When it's full the streamer
evicts sector data: walls and signs drop to their smallest mip levels (colour smears, flat grey)
and small props such as bushes disappear. Without the large address aware flag the 32-bit
process has 2 GB of address space, and at 4K only about 500 MB of it is left, so the pool can
only grow with the flag set.

* With the flag, the pool is raised to 512 MB (configurable). Without it, the pool stays stock.
  Levels write their own value (57) into the pool tweakable before the pool is created; the
  tweakable's record is pointed at a variable of the fix, so the level's value lands there and the
  real one keeps the configured size.

### Pop-in

Every track sector carries a precomputed visibility list, and buildings or props that are not
on the current sector's list are skipped, whatever their distance. Crossing a sector border makes
large halls and terminals beside the track appear at once. The game still has the debug switches
that turn these lists off (`Sectors/Visibility/Disable Powerplay Visibility`,
`Disable Sector Visibility`, `Disable Visibility Map`); with `PopInFix=1` they are set, and the
streaming distance, LOD distances and the view frustum decide what is drawn.

`LodDistanceScale` multiplies the high/mid environment LOD distances, sector and entity streaming
distances, particle and debris distances and the per-level powerplay asset distances. The low LOD
hand-over to each level's backdrop models stays as the level sets it, since moving it makes both
draw at once.

**Opponent cars.** The PC release loads the PS3's car detail settings
(`Generic/Params/PlayerLOD_ps3.params`): at most 3 opponents get the mid detail level, all others
the far one, and every opponent switches to the far level at 100 m, so cars visibly change their
model during a race. The Xbox 360's file in the same archive (`PlayerLOD_xenon.params`) allows 31
opponents at the mid level up to 200 m. `CarDetail=1` writes those numbers into the tweakables
(`PlayerLOD/02;MaxAIMidLODs`, `PlayerLOD/04;AIMidToFar`); `CarDetail=2` also gives all opponents
the near level up to 40 m (`MaxAINearLODs`, `AINearToMid`, stock 2 and 20 m). The game reads them
every frame as a budget per level (0x7B0A2A), not as array sizes; they are written again whenever
a level loads the file. On the Russian build the addresses are mapped and checked against a dump,
not yet played (as for `ShadowDistance`).

**Shadows.** At the High and Very High quality levels in single player the game takes its sun
shadow cascades from the global `Lighting/CSM_Max` settings (chosen at 0x88A3E0, copied at
0x894B60): three cascades up to 300 m, faded out over the last 100 m. `ShadowDistance` multiplies
both. The three cascades then cover a longer distance, so close shadows get a little softer. Split
screen and the Low and Medium levels keep their per-level settings.

### Other image fixes

* **Powerplay icons:** the port multiplies their scale by width / 1280 on top of the renderer's
  own width scaling (1.5x too big at 1080p, 3x at 4K); the loaded scales are divided back.
  The route change markers (red and blue arrows) get their size as width x 2.25/1280, a constant
  only that code reads; it is set to 2.25 / width.
* **Motion blur:** it blurs along one frame of camera motion, which at 60 fps is half as much;
  the amount is doubled. `MotionBlurStrength` scales it further. During the blur pass the textures
  are clamped to the edge (through the Direct3D device's sampler states), so samples that reach past
  the picture at speed repeat its edge.
* **Line at the bottom at Very High:** all full screen passes (tone mapping, motion blur, depth of
  field, grading ...) draw one quad template that the game moves by half a pixel plus a fixed 0.0005
  up and 0.00015 left in clip space. That is a fraction of a pixel up to 2160 rows, but Very High on
  a 4K screen renders at 7680x4320, where it adds up to more than a pixel: every pass moved the picture
  up a row and left its bottom row unwritten, so whatever landed there once stayed as a line. For
  render targets above 2160 rows (or 3840 columns) the template's position values are set so the fixed
  part stays what it is at native 4K; smaller targets are left as they are.
* **Vignette and gradient:** the race cameras darken the screen edges and lay a translucent blue
  band over the top of the screen in the grading pass. `Vignette=0` sets the vignette's U/V scale
  to 0 for that pass, `Gradient=0` the gradient's alpha; the grading itself stays.
* **Bloom and flares:** bloom, the sun/light and screen space lens flares and the particle flares
  can each be switched off (`Bloom`, `LensFlare`, `ParticleFlare`), and the bloom look and exposure
  tuned live (`BloomStrength`, `BloomTone`, `BloomCurve`, `Exposure`). Bloom is cleared from the
  tone mapping pass's shader choice, the particle flare pass from its own state flag (both through
  their vtable slots), the lens flares through the game's two enable tweakables.
* **HDR output** (`HDR=1`): Direct3D 9 has no way to an HDR10 display on Windows. The game's device
  is made windowed (a borderless window over the whole screen) and its frames go out through a
  Direct3D 11 swap chain of the fix with the HDR10 colour space: at Present the back buffer is
  converted to linear light at the paper white level (`HDRPaperWhite`), BT.2020 and the PQ curve,
  read back one frame later and presented. The light above white comes from the game's own half
  float image: its filmic tone mapper (the last draw of the bloom pass, vtable slot `0xBB3678`)
  squeezes it into 8 bits with `T(L) = k L^p / (L^p + q)`. Right after that pass, with the game's
  textures and constants still bound (read from the shader's constant table), one more pass of the fix
  runs the same curve twice per pixel, as the game does and opened up above a knee (same value and
  slope there, then rising towards `HDRPeakBrightness`), and keeps the ratio as a gain per pixel. When
  the first Scaleform movie of the frame is drawn (`GFxMovieRoot::Display`, vtable `0xC41CF4` slot 31),
  the game's target is copied: the picture without the HUD. At Present every pixel that still equals
  that copy gets the gain, so the HUD and menus stay at paper white. The output costs about 1.6 ms a
  frame at 4K and adds one frame of delay.
* **Anisotropic filtering:** the game filters all textures linearly; mipmapped textures that are
  not render targets get anisotropic filtering.
* **Graphics quality:** "Very High" is exactly "High" plus 2x supersampling: of all places in the
  game that read the quality level, only the supersampling ones tell Very High from High. To render
  at screen resolution, pick "High" in the game; nothing else changes.

### Audio

* **Opponents' engines:** every opponent's engine is a sound bank of its own, loaded into one of seven
  slots that the audio system makes with the Xbox 360's size (about 100 KB). The PC's banks are larger,
  the loader refuses them and the opponents race without an engine sound. The slot size is plain data
  in the exe and is raised to 256 KB before the audio system starts (`OpponentEngines`). Pointed out by
  the [Back on Air](https://github.com/xenophobentx/back-on-air) project (an inspiration only; no code
  was taken) and checked against this build.
* **Reverb:** in the PC release six of the seven reverb presets (halls, tunnels, under bridges, the
  menu) hold FMOD's stock "Generic" values and the seventh the stock "Bathroom". The Xbox 360
  release's copy of the same event file holds the presets the sound designers set: more reverb and a
  longer decay where it belongs. FMOD's preset lookup in `fmod_event.dll` is wrapped and hands the game
  the console's values (`ConsoleReverb`), read from the Xbox 360 disc.

### Camera

* **Field of view:** `FOV` scales the race cameras' (chase and bumper) field of view; the game's
  widening with speed stays on top of it. The game's near plane is 1 m; with a much wider view the
  bottom of the picture looks down at the road right below the camera and would see through it, so
  the near plane moves closer by the same ratio (the camera state's own near plane setting).
* **Ultrawide:** the game keeps the horizontal field of view, so screens wider than 16:9 lose
  picture at the top and bottom. `UltrawideFix=1` widens the race cameras' view until the vertical
  one matches 16:9 again (single screen only).
* **Full ultrawide (experimental):** `UltrawideFix=2` corrects the game instead. Every camera takes
  its horizontal field of view from one 16/9 constant (`0x00BAA9DC`); the screen's real aspect there
  gives every camera the 16:9 picture height: menus, power plays, crash cameras, split screen. The
  Scaleform movies get a centred 16:9 viewport, so menus, HUD, cards and result screens keep their
  proportions; their full-width bands and backgrounds are stretched to the screen edges (renderer
  hooks, a table of shapes the automatic rule gets wrong), the loading screen is zoomed with black
  sides, the split screen HUD is drawn once per half. Bink cinematics keep their aspect with black
  sides, the 3D player tags their proportions, the pause and intro letterbox is off, and the last
  pixel column (wrong in the stock game above 16:9) is copied from its neighbour. Tested at 32:9
  (5120x1440, 3840x1080), 24:9 (3840x1440), 21:9 (3440x1440, 2560x1080) and 16:9 (unchanged).
  Steam build only; elsewhere it falls back to 1.
* **Zoom keys:** hold a key to zoom the race camera closer or wider, another one resets it.
* **Photo mode:** during a race, a key or the pad's Back button holds the world still (the game's
  own slow motion at 0.000001), hides the HUD and the on-screen messages (the power play icons in
  the world stay) and frees the camera: left
  stick or W A S D to move, right stick or I J K L to look, the triggers or Q E down and up, A or
  Shift faster, LB RB or the zoom keys to zoom, Y + LB RB or U O to tilt. The d-pad or the arrow
  keys set the look: up and down the focus distance (it switches the depth of field on at half
  strength when it is off), left and right the depth of field (the game's own depth of field pass,
  left end = off; not at the lowest graphics quality); with X or Ctrl held, up and down bloom (down
  to off) and left and right exposure; the left stick click or Backspace resets it. No car is put
  back on the track while it is on. Back, B, Esc or the key again returns to the race. The game's
  sound is paused meanwhile (FMOD's own category pause). Crashes take it over only in part, see the
  [known limitations](../README.md#known-limitations).
* **Camera shake:** a slider for the race cameras' shake on bumps and hits, and a separate switch for
  the shake from explosions and power plays, both against motion sickness.
* **HUD key:** switches the power bar and the info bar off and on during a race.

## Texture dump

`DumpTextures=1` exports the game's textures at the next start, before the game sets up Direct3D:
a small window says "glimpse_fix: dumping the game's textures, please wait", a worker thread reads
every `.ark` archive under `data`, writes each texture as a DDS file to
`glimpse_textures\<archive path>\`, the key is set back to 0 and the process ends. Only files are read and written; no game code runs and none is changed.

* Archive: a header (`u32 0x10000`, header size), then an index of 20-byte entries (type, offset,
  packed size, size, key), sorted by key. The key is a hash of the lower-case file name with `/`
  (`h = h * 31 + c`, 0x571080); the names themselves are not stored. Entries are stored (type 0) or
  LZSS-packed (type 1: a `u32` size, then a 4 KB window starting at 0xFEE); types 2 and 3 exist in
  the loader but do not occur.
* A texture entry is a 136-byte `SXET` header followed by a complete DDS file (DXT1/3/4/5, ATI1/2,
  uncompressed and float formats, cube maps).
* Files are named `<key>.dds`; each folder's `index.txt` lists name, size, format, mip levels and
  bytes. The sector archives (`*.static.ark`, `*.stream.ark`) hold geometry only.
* Steam build: 42 archives, 551 textures, 573 MB, about 10 seconds.

This is the groundwork for texture packs, which need the original textures and their keys. Loading
replacement textures is not part of this version. The large address aware flag stays for that
reason too: high-resolution textures need address space on top of the streaming pool.

## Online over Steam

The game's network code is Quazal's Net-Z over PRUDP. In LAN mode the host answers search broadcasts
on UDP port 9100 and the players connect to its game port. Quazal's sockets go through a replaceable
transport, its SocketDriver (`[0xD77734]`, taken when its network starts); the fix puts a driver
there that carries the datagrams through `ISteamNetworkingMessages` to the members of a Steam lobby
(every player gets a virtual address 10.x.y.z from the Steam account, a broadcast goes to every
member) and answers Quazal's interface list query with that address through the `WSAIoctl` import
slot. A data write, two import slots (`WSAIoctl`, and `OutputDebugStringA` for the game's debug text
in the log) and the Scaleform movie's `Invoke` vtable slot; no game code.

* **Lobbies:** public lobbies are found by their name (`glimpse_name`, the oldest one wins); friends
  only lobbies through the friends' current game (`GetFriendGamePlayed`). A lobby that Steam no longer
  reports is noticed after 3 s and found or made again; a switch joins the new lobby before it leaves
  the old one.
* **Session rules:** every member publishes its own settings and whether it hosts (lobby member
  data). The host is the player that creates a game (a game port open without a connection to a host)
  or answers a search; a guest's host is the first player it connects to. Until a host is known, the
  Steam lobby owner's settings stand in. The extras follow the host's
  settings; until a guest has them, they are off.
* **Game mode:** the game's lobby screen sets its title to "LOBBY - <mode>" in the game's language;
  the fix reads it from the Scaleform calls (`GFxMovieRoot` vtable slot 18, `Invoke`) and the host
  publishes it for the others in the Steam lobby.

## Copy protection in detail

The Steam executable is protected with SecuROM, which decrypts the game at runtime and runs parts
of it in its own virtual machine. **This project does not remove, bypass or weaken it**; it is
built to work alongside it.

Why this matters: SecuROM checks the integrity of the game code at fixed times after launch, each
time a different part of it (about 4½, 5½, 6½ and 17 minutes in). A changed instruction does not
fail at once. Once a check finds one, its check threads (every ~22 s) stop releasing their VM
contexts; once all ten are taken, everything that needs protected code waits forever. The music
stops and the game hangs, typically after the finish line. This is also the likely reason why
earlier 60 fps patches and mods got a reputation for "breaking" the game: they patch the main
loop, and that part is checked about 6½ minutes after launch.

How the fix avoids it: **no game code is changed at all.** Everything works through

* values in the game's data: tweakables and their records (frame pacing, streaming pool, LOD and
  streaming distances, debug, unlock and lens flare switches, the achievements' debug unlock, the
  slow motion and HUD render switches of the photo mode, the race cameras' FOV, near plane and smoothed rotation, the explosion shake switch, AI
  skill limits), loaded HUD
  parameters, the full screen passes' quad template positions, powerplay distance records, the main
  loop's accumulator, the power bar, the AI skill and the cars' chassis velocity,
* swapped vtable slots (motion blur, grading, bloom, depth of field and particle flare passes, AI
  player update, chase and bumper camera update, with the photo mode the other camera classes'
  update, the respot logic's update with the photo mode or `WreckTime`, the Scaleform movie's
  `Display` with `HDR=1` and `UltrawideFix=2`, its `SetViewport` with `UltrawideFix=2` and its `Invoke`
  with `SteamLAN=1`),
  import table entries (`XInputSetState`, Bink's `BinkOpen`/`BinkWait`/`BinkDoFrame`, FMOD's
  `Event::start`), read-only
  strings and constants (prompt bar icon paths, route marker size),
* the Direct3D device's vtable, and calls into the game's own functions (dictionary lookup,
  Scaleform `Invoke`/`SetVariable` for the prompt bar, `SetVisible` for the photo mode's overlays,
  the depth of field blur setup) from the input poll and the hooked passes.

The prompt bar glyphs, the title screen text, the icon size, the 60 fps and the streaming pool were
first done with code patches and each set off exactly this hang, so they were rebuilt this way.
An internal resolution option (`RenderScale`) needed code patches too and set off the same hang,
so it was removed in 1.7.0.

* `[Debug] PatchMask` (bits as in `src/common.h`) switches individual patches off, to find out
  which one causes a side effect. With `Log=1` the log also shows the number of SecuROM VM
  contexts in use whenever it changes; a check that found something shows up as two more
  contexts (`8B03 8AF3`) every ~22 s.

## The Xbox content modpack (Xenon.dll) in detail

There is a pack that brings the Xbox 360 DLC to the PC (the "elysium" package with `Xenon.dll`).
glimpse_fix does not support it and will not build on it. What the pack is:

* **The free PC demo instead of the game.** The pack overwrites `SplitSecond.exe` with the
  executable of the official Split/Second PC demo (version 1.3.8.0, "Split Second DEMO
  executable"). The demo exe is patched on disk to load `Xenon.dll` at startup.
* **Xenon.dll turns the demo into the full game** by patching it in memory, about 30 places,
  roughly half of them code (jumps, immediates, push operands), some in code that SecuROM only
  decrypts at runtime:
  * demo names and demo parameter files are replaced by those of the full game, a demo setting
    is switched off, the demo's end-of-race state is skipped and its graphics quality limit lifted,
  * the track tables are rebuilt with 17 instead of 15 entries (Quarry and Nem_Quarry added),
    together with the matching track count limits,
  * 14 in-game music tracks in shuffled order instead of the demo's 3,
  * the `PCcheat` switches are renamed so `SplitSecond.params` unlocks the retailer cars and the
    Euro livery pack,
  * the prompt bar uses the Xbox 360 or PC prompt movie from the pack.
* **Data:** the pack replaces `Resident.ark` and the level archives, and adds the paid Xbox 360
  DLC (the Quarry tracks, Deadline, Onslaught, Survival, the car and livery packs), which was
  never released for PC, without a license.

Why glimpse_fix stays away from it:

1. **It is not the Steam game.** glimpse_fix is written for the unmodified Steam exe and checks
   the expected bytes before every patch. On the demo exe those checks fail and most of the fix
   switches itself off (60 fps, streaming pool, pop-in fix, button prompts, icon size, ...).
2. **Copy protection.** glimpse_fix never changes game code and leaves SecuROM fully in place.
   The pack patches game code and runs the full game on the demo exe, so the full version's
   executable and its protection are not used at all.
3. **Content.** The pack redistributes Disney's DLC and game data without a license. That is
   tolerated, not allowed, and it has no place here.

What glimpse_fix does instead: everything the Steam version already ships but keeps locked can be
unlocked through the game's own switches, without code changes: all tracks, modes and cars
(`UnlockAll`), the PC cheat code extras including the Expressway track (`BonusContent`) and all
achievements and decals (`UnlockDecals`). Using the pack is your choice, but please reproduce
problems without it before reporting them.

## Known limitations in detail

* With virtual pads active, the controls screen uses Xbox names only when no other DirectInput
  joystick (e.g. a wheel) is present, since those names are shared by all devices.
* 60 fps: races, AI and physics were checked; not every power play and cutscene was. Set
  `FrameRate=30` if something looks off.
* Boost, the power bar fill, the photo mode and the HUD key work for player 1 only; the photo mode
  only on a single screen.
* With `VirtualPads=0` the per-frame extras do not run (see `VirtualPads`).
* `UltrawideFix=2` is experimental. Checked: single player menus, season screens, races, pre-race
  cards, pause and result screens at 32:9 and 21:9; menus, cards and races at 24:9. Not checked yet:
  split screen, online and LAN menus, power play close-ups, the end-of-season cutscene. A screen whose
  band stops at the 16:9 edge or stretches oddly needs an entry in the shape table in `src/ultrawide.cpp`.
* Photo mode on crashes: once your car is wrecked, the game's crash sequence decides the view.
  On a crash on the road (walls, other cars, power plays) the photo camera stays tied to the car
  and is not fully free. After leaving the track (falls) the game holds a waiting view that no
  camera drives, so the photo camera cannot take it over at all. Both would need changes to the
  game code, which this project does not make (see [Copy protection in detail](#copy-protection-in-detail)). Enter the photo mode before
  the crash happens for a free camera.
* `UnlockDecals` works in single player races only (the game's own unlock runs there), and the
  profile is saved when the race ends: finish it or leave it through the pause menu. The Pure
  decal is reset by the game whenever it looks for Pure saves, so it is shown only while the
  option is set.
* `PopInFix=1` draws somewhat more per frame than the stock visibility lists allow.
* HDR: the game runs in a borderless window instead of exclusive fullscreen, and the Steam overlay may
  not show while it is on. The HUD is told apart from the picture by comparing it with the copy taken
  before the first menu is drawn, so a white HUD element over a white sky gets the highlight gain too.
* `SteamLAN` is experimental; it needs `steam_api.dll`, which is not part of this fix. It runs on both
  supported builds; on the Russian one it is mapped and checked against a dump, not yet played.
* Audio: the game forces FMOD to DirectSound in 5.1. The LFE channel stays silent and the pause
  menu is completely silent; both are the game's own mix.

## Building

`build.bat` with the Visual Studio 2022 Build Tools (x86) builds `build\dinput8.dll`,
`build\setlaa.exe` and `build\glimpse_config.exe` (`config/glimpse_config.cpp`).

### Research tools

The scripts in `tools/` were used to reverse-engineer and test the fix. They expect a decrypted
memory dump of the running game (`tools/dump.py`) and a Ghidra project in `ghidra/`; neither is
part of this repository.

* `dump.py` — dump the decrypted `SplitSecond.exe` image from the running process (SecuROM/Steam
  DRM decrypt it at runtime).
* `buildmap.py` — map every fixed address of the source from the supported build to another
  build from two dumps and write its table (`src/build_ru.inc`); it checks the result against the
  other dump (code bytes, swapped vtable slots, every byte and prologue check).
* `dec.sh` + `ghidra_scripts/` — headless Ghidra: decompile functions or ranges, list xrefs.
* `dis.py`, `xref.py`, `strs.py`, `memgrep.py`, `mem.py`, `vmem.py` — quick disassembly, string,
  memory and address space tools; `objscan.py` finds live objects by vtable or pointers to an
  address, `rtti.py` names the class of a vtable.
* `padfeed.py` (with `TestFeed=1`) — scripted virtual pad; `test_race.sh` builds, installs and
  drives into a quick race; `ishot.sh` saves a frame straight from the Direct3D back buffer
  (works in exclusive fullscreen).
* `loopback/loopback.cpp` + `wavstat.py` — record the system mix (WASAPI loopback) and measure
  per-channel levels.

### Notes (Steam exe, image base 0x400000)

Addresses of the supported build (`4BE13C66`); in the source they go through `G()`, which gives
the Russian build's place from `src/build_ru.inc`.

| Address | What |
|---|---|
| `0x00D646C8` | PC input manager singleton (vtable `0x00BA2904`) |
| `0x0040F0F0` / `0x0040EE10` | `GetActionValue` / `GetActionState(controller, action)` |
| `0x00410490`, `0x0040FFC0` | Joystick enumeration and its callback |
| `0x0040E850` | Adds a controller record; retries `Acquire` while it returns `DIERR_INPUTLOST` |
| `0x0040DB50`, `0x00CFABF8` | Preset choice by `guidProduct`; presets, 57 × 5 DWORDs each |
| `0x0040FC70` | Rumble: `XInputSetState(joystick index)` |
| `0x004DADE0` | `cLayer_PromptBar`: sets the PC key frames and captions |
| `0x00CFA5E0` / `0x00CFA6E0` | JOY_AXIS_* / JOY_BUTTON_* name tables |
| `0x00558880` | Main loop: measure frame time, accumulate ticks, `Update(ticks)`, `Render` |
| `0x00D747F4`, `0x00D747F0`, `0x00D199D8` | Main loop accumulator, its tick counter (`Sleep(5)` at 5), "max ticks" tweakable (10) |
| `0x00BA0C14` | 1/30, shared by the main loop, the AI steering controller `0x524AC0`, AI look-ahead grids and data conversions |
| `0x00BC0BC0` | `cAIPlayer` vtable slot of the AI player update `0x51A8B0`; the AI's skill (0–1) at +0x24 |
| `0x00BC0BF8` | `cAIPlayer` vtable slot 17 `0x51A550`, every frame: reads the track section `[[[ai+0x18]]+0x28]+0x54` (NULL after a restart until the next update) |
| `0x00D17044` … `0x00D1705C` | AI catch-up skill limits (initial/final min and max, 8 bytes apart) |
| `0x00D72534` | "SSAI/Debug Cheats/AI Full Power Bar" |
| `0x00D389E0`, `0x00D389E4` | Tweakable "Sectors/Streaming Memory Pool MB" (57), read on level load (`0x7C654F`); its record (vtable `0xBB2498`, +4 variable) |
| `0x00D66B28` | `cInGameResources`: +0xE4 `cGameLogic` (+0x18 player count, +0x1C players); player +0xE8 `cPowerBar` (+0x2C output, +8 power 0–100), +0xAC `cVehicleState` (+0x40 vehicles, +4 + i×4); vehicle +0x584 chassis `hkpRigidBody` (+0x100 forward, +0x110 position, +0x1A0 velocity) |
| `0x00D6FC54`, `0x00D6FBA0`, `0x00D7D978`, `0x00D6F8F0` | UI/All Gamemodes / Episodes / Events / Vehicles Unlocked |
| `0x00D6C49C` | 10 `PCcheat;…` switches, 8 bytes apart (Cobretti SlipStream, Expressway = Downtown B, branded cars, Euro livery) |
| `0x00D8B368`, `0x00D82788`, `0x00D9AFCC` | Disable Powerplay Visibility / Sector Visibility / Visibility Map |
| `0x00D8B384` | Powerplay draw distance records (name +0x10, distance +0x90) |
| `0x00D66B24` | HUD; parameters at +0x20, powerplay icon scales +0x214/+0x218 |
| `0x00BB8494` | Route change marker size factor 2.25/1280 (read only by `0x4C7910`) |
| `0x00BB42B0`, `0x00BB4048` | vtable slots of the motion blur pass `0x49B490` and the grading/vignette pass `0x49A7A0` |
| `0x00D67758` | Full screen quad helper `0x511BA0`: template at `[+4]` (4 vertices of 7 floats, position xyz and texture coordinates), offset by `0x510CA0` (±0.5 / size) plus 0.0005 up, 0.00015 left |
| `0x00BB3678`, `0x00BB39B8` | vtable slots of the bloom/tone mapping pass `0x492F50` (parameters: +0x38 bloom add, +0x3C/+0x40 bright pass, +0x58 add curve, +0x20 exposure) and the particle flare pass `0x496C60` |
| `0x00D10A3C`, `0x00D37D08` | "PostProcess/SSLensFlare/z; Enable", "LensFlare/Config/a; Enable" |
| `0x00B9B39C`, `0x00B9B3B0`, `0x00B9B380` | Import table: `BinkOpen`, `BinkWait`, `BinkDoFrame` (boot movies: `data/movies/bootup`) |
| `0x01E0266A` | SecuROM VM context table (10 entries, +0x24 = `0x66666666` while in use) |
| `0x00A15BF0` | Dictionary lookup, key → UTF-8 text |
| `0x00C5AE64`, `0x00C5B1F0` | vtable slots of the chase `0xA89240` and bumper `0xA8D9A0` camera update (camera state: +0x14 axes, FOV) |
| `0x00D71208`, `0x00D113B8` | "Physics/Change Phys dt/Slow motion activated", "Slow Motion cheat speed" |
| `0x00D1BC94`, `0x00D1BC9C` | "UI/HUD/3D Components/Render Powerbar", "Render Infobar" |
| `0x00D64E0C` | UI layers: `[+8][id]`; player 1's `Hud.gfx` is layer `0x3B` (vtable `0xBD0E1C`, `[+0x68]+4` GFx movie view) |
| `0x00D64E00` | +0x208 `cAnchoviesPC` achievements (vtable `0xC4AAC4`, unlock `0xA17F90`; +0x2C unlocked, +0x38 count 47); ids 41–46 come from ranked online results (`0x802870`) |
| `0x00D71954`, `0x00D11B6C` | "Anchovies/UnlockAnchovy", "UnlockAnchovieID", read by the race update `0x4BF180` |
| `0x00D64E6C` | HasPureSave (decal id 47); decal table `0x00D6FABC` (id, title), 48 entries |
| `0x00B9B43C` | Import table: FMOD `Event::start` |
| `0x00BAA9DC` | 16/9 aspect constant (.rdata) of every camera: FOV conversion `0xA59A51`, aspect getter `0x794390`, frustum size `0x451680`, tripod cameras `0xAAB02D` |
| `0x00C41CF4` | `GFxMovieRoot` vtable: slot 20 `SetViewport` (`0x98CDF0`, viewport at +0x38), 31 `Display` (`0x987E60`); +0x28 movie definition (vtable `0xC3D5CC`), +0x80 scale mode, +0x88 visible frame |
| `0x00BC93DC` | Scaleform renderer vtable: slots 7 `SetMatrix`, 14 `SetVertexData`, 17 `DrawIndexedTriList`, 18 `DrawLineStrip`, 26–28 mask begin/end/disable |
| `0x00D66000` | Viewport manager: +0 screen viewports (1 = one player), +8 forced viewport (−1 = none) |
| `0x00D36F54` | Tweakable "LetterBox/LetterBox Amount" (0.118) |
| `0x00D1DB68`, `0x00D1DB78`, `0x00D1DB88` | 3D player tag name width, icon width, icon offset x (per viewport width) |
| `0x00D714D0` | Split screen HUDs: `[+0xCC+4i]` player HUD, +0x12C power bar, +0x128 info bar → +8 dial → +0x78 quad corners at +0x2C |
| `0x00B9B388` | Import table: `BinkClose` |
| `0x008674F0` | FMOD init: DirectSound, `setSpeakerMode(5POINT1)` |

Actions: `0x29` throttle, `0x2A` brake, `0x1D`–`0x2C` the 16 console buttons, `0x03`–`0x06` menu
up/down/left/right, `0x0A`–`0x0D` menu Y/A/X/B.
