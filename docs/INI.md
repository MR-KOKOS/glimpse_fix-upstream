# SplitSecondPad.ini reference

Every key of `SplitSecondPad.ini`. `glimpse_config.exe` sets all of them except the development keys in `[Debug]`; this is the reference for editing the file by hand.

The file is split into sections. INIs from older versions with everything under `[Game]` are still read.

**[Controller]**

| Key | Default | Meaning |
|---|---|---|
| `VirtualPads` | 4 | XInput slots exposed as pads (0–4). The game accepts at most 4 joysticks; lower this to make room for a wheel. At 0 the per-frame extras (photo mode, HUD and zoom keys, unlocks, decals, boost, LOD scaling, `ShadowDistance`, `PopInFix`, `CarDetail`, `LensFlare=0`) do not run, as they run with the pads' poll. |
| `HideXInputFromDirectInput` | 1 | Don't offer the real XInput devices through DirectInput as well. |
| `SeparateTriggers` | 1 | 0 = stock shared Z axis. |
| `StickDeadzone` | 0.15 | Radial stick deadzone (0–1). |
| `TriggerDeadzone` | 0.05 | Trigger deadzone (0–1). |
| `RumbleStrength` | 1.0 | 0 = off. |
| `ButtonGlyphs` | xbox | `xbox`, `playstation` or `keyboard` (never switch). |

**[Graphics]**

| Key | Default | Meaning |
|---|---|---|
| `FrameRate` | 60 | 30 = stock. |
| `Anisotropy` | 16 | Anisotropic filtering 2–16, 0 = stock (linear). |
| `MotionBlurStrength` | 1.0 | Motion blur multiplier on top of the 60 fps compensation. 0 = off. |
| `Vignette` | 0 | 1 = keep the cameras' darkened screen edges. |
| `Gradient` | 1 | 0 = no translucent blue band at the top of the screen. |
| `Bloom` | 1 | 0 = no bloom (glow around bright areas); the frame is still tone mapped. |
| `BloomStrength`, `BloomTone`, `BloomCurve`, `Exposure` | 1.0 | Multipliers on the cameras' bloom values (0–10): glow on bright areas, a softer second bright pass value, the add curve, the brightness of the whole picture. Read again every 2 s, so they can be tuned while the game runs. |
| `LensFlare` | 1 | 0 = no sun, light or screen space lens flares. |
| `ParticleFlare` | 1 | 0 = no flares from sparks, explosions and lights. |
| `FixPowerplayIconSize` | 1 | Powerplay icons and route change markers at console size at every resolution. 0 = stock. |
| `HDR` | 0 | 1 = HDR10 output for an HDR display (HDR switched on in the Windows display settings); the game runs in a borderless window. |
| `HDRPeakBrightness` | 1000 | The display's peak brightness in nits (400–10000), as the Windows HDR Calibration app finds it. The highlights of the game's HDR image go up to this. |
| `HDRPaperWhite` | 200 | Brightness of the game's white in nits (80–500). |

**[World]**

| Key | Default | Meaning |
|---|---|---|
| `StreamPoolMB` | 512 | Streaming pool in MB, 0 = stock (57). Only used when the exe has the LAA flag. |
| `LodDistanceScale` | 2.0 | Multiplier for LOD, streaming, particle, debris and powerplay distances (0.5–8). |
| `ShadowDistance` | 2.0 | Multiplier for the sun shadow range at the High and Very High quality levels in single player (1–3). The game: shadows reach 300 m and fade out over the last 100 m (`Lighting/CSM_Max`). Shadows close to the car get a little softer the higher it is. |
| `PopInFix` | 1 | Ignore the sectors' precomputed visibility lists. 0 = stock. |
| `CarDetail` | 1 | Opponent car detail. The PC release loads the PS3's car LOD settings (`PlayerLOD_ps3.params`: at most 3 opponents in medium detail, coarse from 100 m on). 1 = the Xbox 360's (`PlayerLOD_xenon.params`: medium detail for all, coarse from 200 m on), 2 = high (also full detail for all opponents up to 40 m), 0 = as the PC release. |

**[Camera]**

| Key | Default | Meaning |
|---|---|---|
| `FOV` | 1.0 | Field of view of the race cameras as a factor of the game's own (0.3–2.5). |
| `UltrawideFix` | 1 | Screens wider than 16:9: 1 = the race cameras keep the picture height of 16:9, 2 = experimental full ultrawide (all cameras, menus and HUD at 16:9 in the centre), 0 = stock. |
| `ZoomKeys` | 0 | 1 = zoom the race camera while `ZoomInKey` / `ZoomOutKey` is held, `ZoomResetKey` back to `FOV`. |
| `ZoomInKey`, `ZoomOutKey`, `ZoomResetKey` | 0x31, 0x32, 0x33 | Virtual key codes (1, 2, 3 on the top row). |
| `PhotoMode` | 0 | 1 = photo mode with `PhotoModeKey` or the pad's Back button during a race. |
| `PhotoModeKey` | 0x46 | Virtual key code (F). Keys the game uses itself (Esc, P, Enter, arrows) do not work, nor do the photo mode's own controls (W A S D, I J K L, Q E, U O, Shift, Ctrl, the zoom keys). |
| `CameraShake` | 1.0 | How much the race cameras (chase and bumper) shake on bumps and hits, against motion sickness: below 1.0 their rotation is smoothed (a low pass up to 0.12 s at 0.0), so quick shaking is filtered and turns follow a little later. |
| `ExplosionShakeOff` | 0 | 1 = no camera shake from explosions, collapsing buildings and power plays nearby (the game's own switch for it). |
| `HudKey` | 0 | Virtual key code that switches the HUD off and on during a race (0x48 = H). 0 = no key. |

**[Gameplay]**

| Key | Default | Meaning |
|---|---|---|
| `UnlockAll` | 0 | 1 = all tracks, events, episodes, game modes and vehicles selectable (the game's own switches; the profile is not changed). |
| `SkipIntro` | 0 | Boot movies at every start: 1 = skip the publisher logo, 2 = also the game intro. They end at once without decoding (the movie files stay untouched). 0 = stock. |
| `BonusContent` | 0 | 1 = the PC version's cheat code extras: Expressway track, Cobretti SlipStream, retailer branded cars, Euro livery pack. |
| `WreckTime` | 0 | **Highly experimental: can softlock the game, especially together with the photo mode.** Seconds from a wreck of your car to the respot (about 4 s in the game; 0 = as the game): the game's respot logic is left out until then, the crash camera keeps showing the wreck. The photo mode holds the respot of all cars while it is on. |
| `UnlockDecals` | 0 | Achievements and their decals: 1 = all, 2 = only the unobtainable ones (achievements 41–46 from ranked online races and the Pure decal; the "all achievements" decal follows once the rest is earned). Unlocked in the next single player race and saved with the profile, so they stay when set back to 0; the Pure decal shows while this is set. The `.sav` files are copied to `glimpse_backup` before the first unlock. |
| `Boost` | 0 | 1 = X held gives player 1 extra speed along the car's heading. |
| `BoostKey` | 0xA0 | Keyboard key for the boost, a virtual key code (0xA0 = left Shift). 0 = pad only. |
| `BoostButton` | X | Pad button for the boost: `X`, `Y`, `LS`, `RS` (stick clicks) or `None`. |
| `BoostAcceleration` | 15 | Boost in m/s² (1–100). |
| `BoostMaxSpeed` | 90 | No boost above this speed, m/s (10–200; 90 = 324 km/h). |
| `BoostMotionBlur` | 3.0 | Motion blur multiplier at full boost (1–5). |
| `PowerBarFill` | 0 | 1 = LS+RS (both sticks pressed) fills player 1's power play bar. |
| `PowerBarFillKey` | 0xA2 | Keyboard key that works like LS+RS (0xA2 = left Ctrl). 0 = pad only. |
| `Handbrake` | 0 | 1 = handbrake for player 1 while `HandbrakeButton` or `HandbrakeKey` is held (in a network race the host's setting applies to everybody). |
| `HandbrakeButton` | Y | Pad button: `X`, `Y`, `LS`, `RS` or `None`. When the boost or the handbrake uses Y, the game's camera change moves to D-pad down during a race. |
| `HandbrakeKey` | 0x58 | Keyboard key, a virtual key code (0x58 = X). 0 = none. |
| `HandbrakeMode` | 1 | 1 = drift: the game's own slide physics (as when a power play blast throws a car sideways) with the pull and turn below. 2 = brake: the brake pedal fully pressed above 30 km/h. |
| `HandbrakeStrength` | 15 | Drift type: how hard the handbrake slows the car, m/s² (0–40). |
| `HandbrakeDrift` | 2.5 | Drift type: how fast the car turns into the slide at full steering, rad/s (0–5; 2.5 ≈ 140° a second). |
| `AIBoost` | 0 | Experimental: 1 = AI cars boost in bursts while behind player 1 or not far ahead. |
| `AIBoostLevel` | 2 | 1 easy (0.8× your boost, 3 s tank, up to 30 m ahead), 2 normal (1×, 5 s, about once a second, 60 m), 3 hard (1.3×, 8 s, almost constantly, 150 m). |
| `AIBoostUnlimited` | 0 | 1 = the AI's boost tank never empties. |
| `AIBoostStraightOnly` | 1 | 1 = AI boosts only on straights at speed, 0 = anywhere while driving forward. |
| `AIBoostChaos` | 0 | 1 = chaos mode: level 3, endless tank, anywhere, also far ahead of you, the AI's power play bar is always full and the AI drives at its highest skill (no slowing down when ahead). Overrides the three keys above while set. |

**[Audio]**

| Key | Default | Meaning |
|---|---|---|
| `OpponentEngines` | 1 | 1 = sound bank slots big enough for the PC's opponent engine banks. 0 = stock (silent opponents). |
| `ConsoleReverb` | 1 | 1 = the Xbox 360 release's reverb presets. 0 = the PC's stock ones. |

**[Online]** (experimental, see the [README](../README.md#online-over-steam-experimental))

| Key | Default | Meaning |
|---|---|---|
| `SteamLAN` | 0 | The game's LAN mode with the players of a Steam lobby instead of the local network, through Steam (no ports to open). Needs `steam_api.dll` from the Steamworks SDK next to the game and the game started through Steam. Steam friends join through "Join game". In a LAN game the settings of its host (the player who created it) for boost, handbrake, power bar fill, AI boost and respot time (`WreckTime`) apply to everybody, with each player's own buttons and keys. |
| `FriendsOnly` | 0 | 1 = the lobby is open to your Steam friends only and not listed for anybody else; when a friend is already in one, you join theirs. `LobbyName` is not used then. |
| `LobbyName` | (empty) | Players with the same name meet in one lobby (letters, digits, `-` and `_`; Steam friends of a member can still join through "Join game"); empty = the public lobby. The name shows in your Steam status. |

**[Debug]**

| Key | Default | Meaning |
|---|---|---|
| `Log` | 0 | 1 = write `SplitSecondPad.log` (Direct3D device, FMOD events, music state, SecuROM VM contexts, every change of the profile `.sav` files). For bug reports it starts with the fix version, Windows version, CPU, RAM, graphics card with video memory and driver, the game executable (size, CRC32, build check) and every DLL in the game folder or loaded from outside the Windows folder, so other mods and different executables show at once. With `Log=1`, F9 in the game saves the next frame pass by pass to `glimpse_dump` (each render target as JPG plus its bottom rows as PNG, the passes and their inputs in the log), for image errors; the game stands still for a few seconds meanwhile. |
| `DumpTextures` | 0 | 1 = at the next start the game's textures are written as DDS files to `glimpse_textures` (a folder per archive, each with an `index.txt` listing the files with size, format and mip levels); a window shows the progress, then the game closes and the key is set back to 0. Files are named by the archive key of the texture (the hash of the game's own file name, which the archives do not keep). |
| `TestFeed`, `Profile` | 0 | Development only: pad state from shared memory (`tools/padfeed.py`), call counters. |
| `PatchMask` | 0xFFFFFFFF | Development: bit mask of the patches that may be applied (`PATCH_*` in `src/common.h`). |
