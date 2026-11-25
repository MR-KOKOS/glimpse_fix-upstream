# High Octane opponent audio

The installed High Octane pack supplies a dedicated player engine for each of its four cars.
High Octane cars are absent from the normal AI roster, and a remote SteamLAN player can drive one.
With `OpponentEngines=1`, a remote car plays its matching opponent bank if installed. Each car
has an individual stock fallback, and the log identifies the bank used.

## Vehicle and sound identifiers

The converted pack's `Vehicles/VehicleDatabase.xml` uses separate identifiers for the model and
its `AudioFile`. Opponent audio resolves the latter.

| Player-facing name | Model ID | AudioFile | Stock fallback |
| --- | --- | --- | --- |
| Ryback Cyclone Special | `Supercar_17` | `Supercar_20` | `Supercar_04` (Cyclone RS) |
| Cobretti Severus | `Supercar_18` | `Supercar_21` | `Supercar_07` (Cobretti GT RS) |
| Ryback Vulcan | `Supercar_19` | `Supercar_24` | `Muscle_24` (Ryback Firestorm) |
| Hanzo Katana | `Supercar_20` | `Supercar_26` | `Muscle_05` (shares part of the Katana's Tonic recording) |

If the listed fallback is missing, the resolver uses the first available bank of the same
audio family from the stock table, then the first available stock bank overall.

All four records are selectable player cars with an AI physics profile (`AISupercar_03d`).
The physics profile supplies handling if a car is used by AI. Grid membership comes from
a separate driver database. Each car uses its authored Tonic recordings when driven by a
player. The inspected PC and Xbox 360 assets supply no matching `Vehicles/CPU/<AudioFile>`
FMOD bank for these four. Comparing the High Octane grains with the base sets found no
byte-identical donor. The respective matching grain counts were 0 of 2720, 0 of 2644,
0 of 2732, and 255 of 1990.

## Where an AI grid gets its cars

On Steam build 385319, the ordinary grid setup at `0x517E90` builds its pool at `0x517280`
from `GameLogic/AIDriversDatabase/AIDriversDatabase.params`. It looks up the configured
driver vehicles in the vehicle database and filters by skill relative to the player's car.
The base archive lists 39 distinct vehicle names across `GenericDrivers` and
`ChampionshipDrivers`. None of the four High Octane model IDs appears in those definitions.
The installed `dlc_cars.ark` and `track_quarry_combined.ark` overlays supply no replacement
driver database. Lab captures (not in the repository) also contained no High Octane
opponent. Those runs cover the observed grids. The roster lookup explains the selection rule.

Career car selection is separate. The front-end car list can offer DLC cars when
`UnlockAll=1` or when the DLC car option with `CarsCareer=1` appends them to eligible events.
Fixed-car events use their configured car. The in-race career car list is populated from
`Season.xml` and does not gain these DLC cars merely because the pack is installed.

## The Demolition boss path

The executable has a separate boss racer type, value 3. The setup at `0x412C6A` compares the
active mode to `Demolition.params` before assigning that type to the special slot. The label
in the AI assignment logic at `0x51816A` calls it the Boss vehicle. For that type,
`AIManager::Load` at `0x51908E` calls `0x78A0F0` with the lead player's vehicle name and
a skill offset of 1. The helper clamps the target to 1.0, scans vehicle records for a skill
within the single-precision 0.05 tolerance, and falls back to the input vehicle when no
record qualifies. Evaluating the helper against the installed table produces that fallback
for each High Octane car. This is a static result for the selector, not a live observation
of a forced Demolition session.

The shipped `Demolition.params` enables the Demolition boss and specifies
`Truck_Demolition`. Normal Season events, including Elite and championship races, do not
use that mode. The shipped `ChampRace` and `FinalChampRace` settings explicitly disable the
Demolition boss. The installed DLC mode settings inspected also leave it disabled. Offering
a High Octane car in the Season front end therefore does not introduce a High Octane boss.
A custom or forced mode needs its own runtime check of the actual racer model and the
audio message. The ordinary-grid finding does not cover replacement AI rosters either.

## Matching banks from the DLC converter

A matching opponent bank could use the individual car's Tonic recordings from the user's
DLC package as its source. Generate the bank offline during DLC conversion. The
`OpponentEngines` lookup checks for `Audio/Release/Vehicles/CPU/<AudioFile>.fev` and can pick
up a compatible bank from an installed overlay provided it fits in the 256 KB `CPUCar` bank slot
(`0xD3DBC0`) and exposes the stock `<AudioFile>/Tonic/<AudioFile>` event path (verified across all
30 base CPU `.fev` banks). A generator or bank design still needs validation for RPM behavior, loop
transitions, positional mix, concurrent instances and reload on subsequent races. Keep extracted
audio and generated game assets in the local test environment.
