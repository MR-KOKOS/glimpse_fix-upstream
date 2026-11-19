# glimpse_fix — Split/Second PC fix

A drop-in `dinput8.dll` for the **Steam** PC version of **Split/Second** (Disney / Black Rock, 2010)
that adds what the port never had, with a settings program instead of hand-editing an INI.

> **Compatibility: Steam version only.** Developed and tested exclusively with the Steam release
> (`SplitSecond.exe` 1.0.0.1, app 297860) on Windows 11. The retail DVD version
> (Split/Second: Velocity) and any other release have **not** been tested. Packs that replace the
> exe are not supported, see [Copy protection](#copy-protection). Which Steam language builds are
> supported is listed in [the technical notes](docs/TECHNICAL.md#supported-executables).

## Features

**Fixes**

* **Controllers:** every XInput pad (Xbox 360/One/Series, 8BitDo, Steam Input, DS4Windows, ...) works
  with the console layout, full menu navigation, Xbox or PlayStation button prompts, separate analog
  triggers and rumble. Out of the box the port only knows the wired Xbox 360 pad.
* **60 fps** with the simulation stepped exactly as at 30 fps, so physics and AI behave as intended.
* **Sharp, complete worlds:** a larger streaming pool against blurry, grey or missing textures and
  props, no pop-in of buildings beside the track, longer draw distances, sun shadows that reach twice
  as far, and the opponents' cars in the Xbox 360's detail instead of the PS3's, so they no longer pop
  between detail levels.
* **Image:** power play icons at console size at high resolutions, motion blur matched to 60 fps,
  anisotropic filtering, no stray line at the bottom at Very High, vignette, gradient, bloom and
  flares switchable.
* **Sound as on the console:** the opponents' engines are audible again, and the Xbox 360 release's
  reverb presets replace the PC's stock ones (see [Credits](#credits)).
* **Ultrawide:** the race cameras keep the 16:9 picture height; an experimental full mode widens
  every camera and keeps menus and HUD at 16:9 in the centre.
* **HDR output** for HDR displays: the game's picture at an adjustable paper white, and the highlights
  of the game's own HDR image (sun, sky, lights, explosions) up to the display's peak brightness.

**Extras** (all off by default)

| | |
|---|---|
| **Boost** | Hold X (keyboard: left Shift) and your car surges forward, up to 324 km/h, with the motion blur swelling with it. |
| **Handbrake** | The game has none. Hold Y (keyboard: X) and the car brakes and swings into a slide with the steering, donuts included. |
| **AI boost** *(experimental)* | The AI boosts too, from easy to hard, or in **chaos mode** all the time with a full power play bar. |
| **Full power play bar** | LS + RS (keyboard: left Ctrl) fills it: drop a building on the leader whenever you like. |
| **Everything unlocked** | All tracks, events, episodes, modes and cars, through the game's own switches; your profile is not changed. |
| **Bonus content** | The PC version's cheat code extras: the Expressway track, the Cobretti SlipStream, the branded cars, the Euro livery. |
| **Achievements and decals** | All of them, or only the ones nobody can earn any more (ranked online, Pure). |
| **Camera** | Field of view, zoom keys, less camera shake, a HUD key and a **photo mode** with a free camera, depth of field, bloom and exposure. |
| **Skip the intro** | No boot movies at start. |
| **Online over Steam** *(experimental)* | The game's LAN mode over the internet with the players of a Steam lobby: no server, no ports to open. See [Online over Steam](#online-over-steam-experimental). |
| **Texture dump** | All the game's textures as DDS files, the groundwork for texture packs. See [Texture dump](#texture-dump). |

All buttons and keys can be changed. Details on every feature are in the
[technical notes](docs/TECHNICAL.md).

## Install

1. Copy `dinput8.dll`, `SplitSecondPad.ini` and `glimpse_config.exe` into the game folder
   (`...\steamapps\common\SplitSecond`).
2. Drag `SplitSecond.exe` onto `setlaa.exe`. It sets the large address aware flag, which the bigger
   streaming pool (and later texture packs) needs. The original stays as `SplitSecond.exe.orig`.
   Steam's "Verify integrity of game files" restores the original exe; just run it again.
3. Run `glimpse_config.exe` to change the settings.

Uninstall: delete the three files and rename `SplitSecond.exe.orig` back.

## Online over Steam (experimental)

The game's own LAN mode, played over the internet with the players of a Steam lobby: no server, no
sign-up, no ports to open. Steam carries the data, directly or through its relays. **This is new and
needs testing: please try it and report what you find in the
[issues](https://github.com/frankyfife/glimpse_fix/issues), with `SplitSecondPad.log` (`Log=1`) from
every PC involved.**

> **Important: online play needs one file that is NOT in the download.** Copying the fix is not
> enough: put `steam_api.dll` (32 bit) next to `SplitSecond.exe` yourself. It comes with Valve's
> Steamworks SDK (free download after signing in at partner.steamgames.com), file
> `sdk/redistributable_bin/steam_api.dll` (not the 64 bit `steam_api64.dll`). Without it the game runs
> as before, but online play stays off and the log says `steam_api.dll not found`.

**What you need**

* **`steam_api.dll` next to `SplitSecond.exe`** (see above).
* `SteamLAN=1` (settings program, page *Online*) on every PC, all with the same version of the fix.
* The game started through Steam.

**Where you land** when the game starts

| Setting | You meet |
|---|---|
| Lobby name empty | everybody else with an empty name, in the public lobby "glimpse" |
| A lobby name | everybody with the same name (letters, digits, `-` and `_`); Steam friends of a member can still join through "Join game" |
| *Friends only* | your Steam friends: you join a friend's lobby if one is playing, otherwise you open one that only your friends can find |
| Steam's "Join game" on a friend | that friend's lobby, also when your game is not running yet |

Two players who open a lobby of the same name at the same moment end up together in the older one
within 15 seconds. A full lobby (8 players) makes the next player open a second one.

**Playing**

* In the game choose *LAN*, then create a game or search for one. Several games can run in one lobby.
* The player who creates a game is its host: the host's mode and track apply, and so do the host's
  settings for boost, handbrake, power bar fill, AI boost and respot time (`WreckTime`), for everyone
  in that game. A guest uses its own buttons and keys for them, also when it has them off itself; when
  the boost or handbrake button is Y, the game's camera change moves to D-pad down.
* A banner at the top left shows the lobby, the players (2/8), the game mode and whose rules apply.
  The photo mode is a free camera there; the race goes on. The lobby name and the mode are also in
  your Steam status, which your friends can see in your game info.

Online play runs on the German/English and on the Russian Steam version. **Not yet:** split screen
in an online game. Tested so far with two players, in Race and Eliminator.

![The banner in a game's lobby: lobby, players, mode and the host's rules](docs/images/online-lobby.jpg)

## Settings

`glimpse_config.exe` edits `SplitSecondPad.ini` next to it: one page per topic, a help line for the
option under the mouse, and three presets (*Recommended*, *Original game*, *Chaos*). Every change is
saved at once; bloom and exposure apply while the game runs, everything else at the next start.
Comments and keys it does not know stay as they are. Every key is described in the
[INI reference](docs/INI.md).

## Texture dump

Settings program, page *Debug*: *Dump textures on the next start*. At the next start a small window
says "glimpse_fix: dumping the game's textures, please wait" while the fix reads the game's
archives, then the switch turns itself off and the game closes. Start it again to play.

* Result: the folder `glimpse_textures` in the game folder, one subfolder per archive, every texture
  as a DDS file with an `index.txt` (size, format, mip levels). On the Steam version: 551 textures,
  573 MB, in about 10 seconds.
* The archives keep only a hash of each file name, so the files are named by that key
  (`0A1B2C3D.dds`); the key is also what a texture pack will use to say which texture it replaces.
* Nothing in the game folder is changed apart from the new folder and the switch in the INI, and
  no game code is involved.

This is the first step towards texture packs: a pack needs the original textures to start from.
Loading replaced textures in the game is the next step and not part of this version. That is also
why the large address aware flag (`setlaa.exe`) stays: high-resolution textures need address space
on top of the 512 MB streaming pool.

## Copy protection

The Steam executable is protected with SecuROM. **This project does not remove, bypass or weaken
it, and it does not change any game code.** SecuROM checks the game code at fixed times after launch;
a changed instruction makes the game hang minutes later, typically after the finish line. That is
also the likely reason why earlier 60 fps patches got a reputation for breaking the game. glimpse_fix
works only through the game's data, swapped function pointers and calls into the game's own
functions, after checking the expected bytes. The copy protection stays intact. How, in detail:
[Copy protection in detail](docs/TECHNICAL.md#copy-protection-in-detail).

**The "elysium" Xbox content pack (`Xenon.dll`) is not supported.** It replaces `SplitSecond.exe`
with the patched executable of the free PC demo and patches game code in memory, so the full game's
executable and its SecuROM protection are not used at all. It also redistributes the paid Xbox 360
DLC, which is still sold on the Xbox Store, without a license. The SS Multiplayer mod depends on it.
glimpse_fix checks for the unmodified Steam exe and switches most of itself off on the demo exe.
Please reproduce problems without such packs before reporting them.
[More on the pack](docs/TECHNICAL.md#the-xbox-content-modpack-xenondll-in-detail).

## Known limitations

* 60 fps: races, AI and physics were checked; not every power play and cutscene was. Set
  `FrameRate=30` if something looks off.
* Boost, handbrake, power bar fill, photo mode and HUD key work for player 1 only; the photo mode
  only on a single screen.
* Photo mode on crashes: once your car is wrecked, the game's crash sequence decides the view. Enter
  the photo mode before the crash for a free camera.
* The full ultrawide mode is experimental; split screen and the online menus are not checked yet.
* HDR runs the game in a borderless window instead of exclusive fullscreen; the Steam overlay may not
  show while it is on. A white HUD element over an already white sky is brightened with it.
* With a wheel or another DirectInput device present, the controls screen names inputs generically.
* `UnlockDecals` works in single player races; finish the race or leave it through the pause menu
  so the profile is saved.
* Online over Steam is experimental, see [above](#online-over-steam-experimental).
* Audio: the game forces FMOD to DirectSound 5.1; the LFE channel and the pause menu are silent in
  the game's own mix.

Bug reports: set `Log=1` (settings program, *Debug*) and attach `SplitSecondPad.log`.

## Credits

* [Back on Air](https://github.com/xenophobentx/back-on-air) by xenophobentx gave the ideas for three
  features: the opponent engine fix (its notes pointed out that the opponents race without an engine
  sound on PC, which I had never noticed), the opponent car detail (the PC loads the PS3's car detail
  settings) and the longer shadow distance. All three were worked out and written independently
  here; no code was taken.
* JOHN30011887: the requests for the car detail and the shadow distance
  ([#13](https://github.com/frankyfife/glimpse_fix/issues/13),
  [#12](https://github.com/frankyfife/glimpse_fix/issues/12)).
* MR-KOKOS: the full ultrawide mode (`UltrawideFix=2`).

## Building

`build.bat` with the Visual Studio 2022 Build Tools (x86). The research tools and the address notes
are in the [technical notes](docs/TECHNICAL.md#building).

## AI disclosure

glimpse_fix is developed with AI assistance: the code, the reverse engineering and the
documentation are written with Claude (Anthropic). Changes are tested in the game on the
German/English Steam version before a release; what could not be tested is marked as such.

## License

GPL-3.0, see [LICENSE](LICENSE). Split/Second is © Disney; this project contains no game code or
data.
