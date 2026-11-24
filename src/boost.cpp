// Split/Second PC fix - boost (X or BoostKey held), full power bar (LS+RS or PowerBarFillKey) and handbrake for
// player 1, in split screen for both players (each with their own pad), boost for the AI cars
//
// All of it works on game data reached from the in-game resources global, no code is touched:
//   [0xD66B28] cInGameResources  +0xE4 cGameLogic (vtable 0xBA9A74): +0x18 player count, +0x1C players,
//                                +0x58 (not the local players: 8 in an 8-car split screen race too); the
//                                local players come first (split screen: Seats)
//   player i cPlayerLogic (0xBA63D4): +0xE8 cPowerBar (0xBA8644) +0x2C output (0xBA8680), +8 power 0..100
//                                     +0xAC cVehicleState (0xBA7378) +0x40 vehicle list, +4 + i*4 vehicle i
//   vehicle +0x584 chassis hkpRigidBody (0xBE9C90): +0x100 forward axis, +0x110 position,
//                  +0x1A0 linear velocity, +0x1B0 angular velocity
// The power bar update (0x43D0F0) adds its gain to the output value and clamps it to 100, so setting the
// value is enough. A boost adds speed along the chassis' forward axis while the car is moving forward
// and the game is not paused (the chassis moved since the last frame).
#include "common.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

static void* const* const kInGameResources = (void* const*)G(0x00D66B28);

static bool IsA(void* p, DWORD vtable)
{
    return p && !IsBadReadPtr(p, 4) && *(DWORD*)p == vtable;
}

static BYTE* Field(void* p, DWORD offset)
{
    BYTE* q = p ? *(BYTE**)((BYTE*)p + offset) : NULL;
    return q && !IsBadReadPtr(q, 0x200) ? q : NULL;
}

static BYTE* GameLogic()
{
    BYTE* res = (BYTE*)*kInGameResources;
    if (!res || IsBadReadPtr(res, 0x100)) return NULL;
    BYTE* logic = Field(res, 0xE4);
    return IsA(logic, G(0x00BA9A74)) && *(int*)(logic + 0x18) >= 1 && *(int*)(logic + 0x18) <= 16 ? logic : NULL;
}

static BYTE* Player(BYTE* logic, int i)
{
    BYTE* players = Field(logic, 0x1C);
    BYTE* p = players && !IsBadReadPtr(players, (i + 1) * 4) ? Field(players, i * 4) : NULL;
    return IsA(p, G(0x00BA63D4)) ? p : NULL;
}

static float* PowerValue(BYTE* player)
{
    BYTE* bar = Field(player, 0xE8);
    if (!IsA(bar, G(0x00BA8644))) return NULL;
    BYTE* out = Field(bar, 0x2C);
    return IsA(out, G(0x00BA8680)) ? (float*)(out + 8) : NULL;
}

// car i (the vehicle list holds the cars in player order)
static BYTE* Vehicle(BYTE* player, int i)
{
    BYTE* state = Field(player, 0xAC);
    if (!IsA(state, G(0x00BA7378))) return NULL;
    BYTE* vehicles = Field(state, 0x40);
    BYTE* vehicle = vehicles ? Field(vehicles, 4 + i * 4) : NULL;
    return vehicle && !IsBadReadPtr(vehicle, 0x700) ? vehicle : NULL;
}

// its chassis
static BYTE* Chassis(BYTE* player, int i)
{
    BYTE* vehicle = Vehicle(player, i);
    BYTE* body = vehicle ? Field(vehicle, 0x584) : NULL;
    return IsA(body, G(0x00BE9C90)) && !IsBadWritePtr(body + 0x1A0, 0x20) ? body : NULL;
}

static float Dot(const float* a, const float* b)
{
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

// adds up to accel * dt along the chassis heading, capped at the boost top speed; false if the car is
// not driving forward
static bool Push(BYTE* body, float accel, float dt)
{
    float* fwd = (float*)(body + 0x100);
    float* vel = (float*)(body + 0x1A0);
    float speed = sqrtf(Dot(vel, vel));
    if (Dot(vel, fwd) <= 5.0f || speed >= g_cfg.boostMaxSpeed) return false;
    float dv = accel * dt;
    if (speed + dv > g_cfg.boostMaxSpeed) dv = g_cfg.boostMaxSpeed - speed;
    vel[0] += fwd[0] * dv;
    vel[1] += fwd[1] * dv;
    vel[2] += fwd[2] * dv;
    return true;
}

// The buttons of one XInput slot, or of every pad (kAllPads: single screen, any pad drives player 1).
// Pads not wrapped (VirtualPads=0): the game reads the pad itself and the fix cannot take a button away from
// it (RacePad), so the pad's X and Y would boost and also do the game's own action: keys only then
static const int kAllPads = -2;

static WORD PadButtons(int pad = kAllPads)
{
    WORD buttons = 0;
    XINPUT_STATE st;
    // polling empty slots is slow, so only the ones the pad wrapper has seen connected
    for (DWORD i = 0; i < XUSER_MAX_COUNT && PadsWrapped(); i++)
        if ((pad == kAllPads || pad == (int)i) && PadSlotConnected(i) && pXInputGetState(i, &st) == ERROR_SUCCESS)
            buttons |= st.Gamepad.wButtons;
    return buttons;
}

// two bits XInput leaves unused: the boost and handbrake keys
static const WORD KEY_BOOST = 0x0400, KEY_HANDBRAKE = 0x0800;

// The keyboard keys count as the pad buttons they stand for, while the game window has the focus.
// Their defaults, left Shift and left Ctrl, are bound in none of the game's input presets (0xCFABF8).
static WORD KeyButtons()
{
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    if (pid != GetCurrentProcessId()) return 0;
    WORD buttons = 0;
    if (g_cfg.boostKey && (GetAsyncKeyState(g_cfg.boostKey) & 0x8000)) buttons |= KEY_BOOST;
    if (g_cfg.handbrakeKey && (GetAsyncKeyState(g_cfg.handbrakeKey) & 0x8000)) buttons |= KEY_HANDBRAKE;
    if (g_cfg.powerFillKey && (GetAsyncKeyState(g_cfg.powerFillKey) & 0x8000))
        buttons |= XINPUT_GAMEPAD_LEFT_THUMB | XINPUT_GAMEPAD_RIGHT_THUMB;
    return buttons;
}

// ---------------------------------------------------------------- AI boost (experimental)

// Each AI car has a boost tank and boosts in bursts while it is behind player 1 or not far ahead, so
// it closes gaps rather than running away. AIBoostLevel sets how much and how often; with
// AIBoostStraightOnly it only boosts on a straight at speed (little yaw rate, fast forward motion),
// AIBoostUnlimited never empties the tank. AIBoostChaos is level 3, unlimited, anywhere, and also
// while far ahead of player 1.
struct AiBoostLevel {
    float strength;            // multiple of BoostAcceleration
    float tank;                // seconds of boost per full tank
    float refill;              // seconds to refill an empty tank
    float rate;                // bursts started per second of eligible driving (on average)
    float burstMin, burstMax;  // burst length, s
    float maxAhead;            // no boost when further ahead of player 1 than this, m
};
static const AiBoostLevel kAiLevels[3] = {
    { 0.8f, 3.0f, 20.0f, 0.5f, 0.5f, 1.5f, 30.0f },   // 1 easy
    { 1.0f, 5.0f, 15.0f, 1.0f, 1.0f, 2.5f, 60.0f },   // 2 normal
    { 1.3f, 8.0f, 10.0f, 2.0f, 1.5f, 3.5f, 150.0f },  // 3 hard
};

struct AiBoostState {
    BYTE* body;
    float tank;   // seconds of boost left
    float burst;  // seconds left in the current burst
    float lastPos[3];
};
static AiBoostState s_ai[16];

static unsigned Random()
{
    static unsigned seed = GetTickCount();
    seed = seed * 1664525u + 1013904223u;
    return seed >> 8;
}

// Chaos mode also gives the AI an endless power bar: the game's own cheat switch
// "SSAI/Debug Cheats/AI Full Power Bar" (0xD72534), read by every AI player update (0x51AC16).
static BYTE* const kAiFullPowerBar = (BYTE*)G(0x00D72534);

// locals: the local players (Seats), whose cars come first and are never boosted as AI cars
static void AiBoost(BYTE* logic, BYTE* player0, BYTE* body0, int locals, float dt)
{
    if (g_cfg.aiBoostChaos) *kAiFullPowerBar = 1;
    int count = *(int*)(logic + 0x18);
    int humans = *(int*)(logic + 0x58);
    if (humans < 1 || humans >= count) humans = 1;
    if (humans < locals) humans = locals;  // +0x58 holds 8 in an 8-car split screen race as well
    const AiBoostLevel& lv = kAiLevels[g_cfg.aiBoostLevel - 1];
    for (int i = humans; i < count && i < 16; i++) {
        AiBoostState& a = s_ai[i];
        BYTE* body = Chassis(player0, i);
        if (!body) continue;
        if (body != a.body) {  // new race or new car
            memset(&a, 0, sizeof(a));
            a.body = body;
            a.tank = lv.tank;
        }
        float* pos = (float*)(body + 0x110);
        bool moved = pos[0] != a.lastPos[0] || pos[1] != a.lastPos[1] || pos[2] != a.lastPos[2];
        memcpy(a.lastPos, pos, sizeof(a.lastPos));
        if (!moved) continue;  // paused
        a.tank += lv.tank / lv.refill * dt;
        if (a.tank > lv.tank || g_cfg.aiBoostUnlimited) a.tank = lv.tank;

        float* fwd = (float*)(body + 0x100);
        float* vel = (float*)(body + 0x1A0);
        float* spin = (float*)(body + 0x1B0);
        float forward = Dot(vel, fwd);
        bool drivable = g_cfg.aiBoostStraightOnly ? fabsf(spin[1]) < 0.15f && forward > 30.0f : forward > 15.0f;
        float ahead = 0.0f;  // how far the AI car is ahead of player 1 along its own heading
        if (body0) {
            float* p0 = (float*)(body0 + 0x110);
            float d[3] = { pos[0] - p0[0], pos[1] - p0[1], pos[2] - p0[2] };
            ahead = Dot(d, fwd);
        }
        bool wanted = drivable && (ahead < lv.maxAhead || g_cfg.aiBoostChaos);
        if (a.burst > 0.0f) {
            if (!wanted || a.tank <= 0.0f) {
                a.burst = 0.0f;
                continue;
            }
        } else {
            if (!wanted || a.tank < 0.5f || (Random() % 1000) >= (unsigned)(dt * lv.rate * 1000.0f)) continue;
            a.burst = lv.burstMin + (lv.burstMax - lv.burstMin) * (Random() % 1000) / 1000.0f;
            if (g_cfg.log) Log("ai boost: car %d, %.1f s, tank %.1f s, %.0f m ahead", i, a.burst, a.tank, ahead);
        }
        if (Push(body, g_cfg.boostAcceleration * lv.strength, dt)) a.tank -= dt;
        a.burst -= dt;
    }
}

// ---------------------------------------------------------------- per frame

static float s_boostBlend;  // 0..1, eases the motion blur in and out

float BoostBlurScale()
{
    return 1.0f + (g_cfg.boostMotionBlur - 1.0f) * s_boostBlend;
}

// This player's place in the player and car lists. In a single player race it is the first. In a network
// race (SteamLAN) the lists are in the session's order on every computer, so on a guest the first car is
// the host's, a copy that the host's updates overwrite. The race camera follows this player's car: the
// car straight ahead of it, nearest to its line of sight, is the one; it has to stay the same for half
// a second and is then kept for the race (until the cars change).
static int LocalIndex(BYTE* logic)
{
    static int locked = -1, candidate = -1, seen;
    static BYTE* lockedBody;
    if (!g_cfg.steamLan || !OnlineSession()) {
        locked = -1;
        return 0;
    }
    int count = *(int*)(logic + 0x18);
    BYTE* p0 = Player(logic, 0);
    if (count <= 1 || !p0) return 0;
    if (locked >= 0 && locked < count && Chassis(p0, locked) == lockedBody) return locked;
    float cam[3], fwd[3];
    if (!RaceCameraPose(cam, fwd)) return 0;
    int best = -1;
    float bestScore = 1e30f;
    for (int i = 0; i < count && i < 16; i++) {
        BYTE* body = Chassis(p0, i);
        if (!body) continue;
        float* pos = (float*)(body + 0x110);
        float d[3] = { pos[0] - cam[0], pos[1] - cam[1], pos[2] - cam[2] };
        float along = Dot(d, fwd);
        if (along < 1.0f || along > 40.0f) continue;  // in front of the camera, at chase or bumper distance
        float off[3] = { d[0] - fwd[0] * along, d[1] - fwd[1] * along, d[2] - fwd[2] * along };
        float score = sqrtf(Dot(off, off)) + 0.05f * along;
        if (score < bestScore) {
            bestScore = score;
            best = i;
        }
    }
    if (best < 0 || bestScore > 6.0f) return 0;
    if (best != candidate) {
        candidate = best;
        seen = 0;
    }
    if (++seen >= 30) {
        locked = best;
        lockedBody = Chassis(p0, best);
        Log("boost: in this network race this player's car is number %d of %d", best, count);
    }
    return best;
}

// The local players and their pads. Single screen: one, this player's car (LocalIndex), every pad and the keys.
// Split screen: the front end's race setup [0xD6EE78] (0x453300 returns it, +0x9C the screen count) holds each
// of the two local players' car (+0xD0, two ints) and controller (+0xD8, two ints). The split screen start
// (0x47EDD0) sets them through 0x78E3E0 / 0x78E240. Player input i (cPlayerInput, +0x14 = i) drives car i with
// the controller 0x78E4C0 finds for it. That controller is the game's own number, not the XInput slot (in a test
// the second pad was controller 0), so which pad drives which car is learnt from the game's reaction (LearnPads).
// The keys go to the local car no learnt pad drives, else to the first. Steam build only: the address is not in
// build_ru.inc, the other builds keep the extras on player 1 in split screen.
struct Seat {
    int car;  // place in the player and car lists
    int pad;  // XInput slot, kAllPads, or -1 (not learnt yet: no pad extras)
};

static const BYTE* const kRaceSetup = (const BYTE*)0x00D6EE78;

// The two local cars' steering (vehicle +0x500, which 0xAB8BD0 writes from the car's controller every frame,
// with the stick's sign) against the XInput sticks. Only frames in which exactly one pad's stick is pushed
// (the others centred) count: a car that steers the same way as that stick scores for the pad, a car that does
// not steer or steers the other way loses. A car the game drives itself (after the finish, while it is put back
// on the track) steers on its own and does not follow the stick, so it does not build up a score. A pad drives
// the car it has a clear lead for; an assignment changes only when another pad leads it by far. With two pads
// and one of them assigned, the other pad drives the other car. Learnt anew for every race (new cars).
static struct {
    BYTE* veh[2];
    int padOf[2];
    int score[XUSER_MAX_COUNT][2];
} s_learn;

static int Clamp100(int v) { return v > 100 ? 100 : v < -100 ? -100 : v; }

static void LearnPads(BYTE* logic, Seat* seats)
{
    BYTE* v[2];
    for (int k = 0; k < 2; k++) v[k] = Vehicle(Player(logic, seats[k].car), seats[k].car);
    if (!v[0] || !v[1] || IsBadReadPtr(v[0] + 0x500, 4) || IsBadReadPtr(v[1] + 0x500, 4)) return;
    if (v[0] != s_learn.veh[0] || v[1] != s_learn.veh[1]) {
        memset(&s_learn, 0, sizeof(s_learn));
        s_learn.veh[0] = v[0];
        s_learn.veh[1] = v[1];
        s_learn.padOf[0] = s_learn.padOf[1] = -1;
    }
    int pushed = -1, connected = 0;
    float lx = 0;
    bool conn[XUSER_MAX_COUNT] = {};
    XINPUT_STATE st;
    for (int i = 0; i < XUSER_MAX_COUNT && PadsWrapped(); i++) {
        if (!PadSlotConnected(i) || pXInputGetState(i, &st) != ERROR_SUCCESS) continue;
        conn[i] = true;
        connected++;
        int x = st.Gamepad.sThumbLX;
        if (x > 9830 || x < -9830) {  // 0.3
            pushed = pushed == -1 ? i : -2;  // -2: more than one pad steers
            lx = x / 32767.0f;
        } else if (x > 3277 || x < -3277) {
            pushed = -2;  // not centred: not a clean frame
        }
    }
    if (pushed >= 0) {
        for (int k = 0; k < 2; k++) {
            float steer = *(const float*)(v[k] + 0x500);
            bool follows = fabsf(steer) > 0.2f && (steer > 0) == (lx > 0);
            s_learn.score[pushed][k] = Clamp100(s_learn.score[pushed][k] + (follows ? 1 : -1));
        }
    }
    for (int k = 0; k < 2; k++) {
        int best = -1, bestScore = 0, second = 0;
        for (int i = 0; i < XUSER_MAX_COUNT; i++) {
            int sc = s_learn.score[i][k];
            if (best < 0 || sc > bestScore) {
                second = best < 0 ? -100 : bestScore;
                best = i;
                bestScore = sc;
            } else if (sc > second) {
                second = sc;
            }
        }
        int cur = s_learn.padOf[k];
        if (bestScore < 20 || bestScore - second < 15 || best == cur) continue;
        if (cur >= 0 && bestScore < s_learn.score[cur][k] + 30) continue;  // hysteresis: a clear lead only
        // the pad drives only one car: the car it leads more for keeps it
        if (s_learn.padOf[1 - k] == best && s_learn.score[best][1 - k] >= bestScore) continue;
        if (s_learn.padOf[1 - k] == best) s_learn.padOf[1 - k] = -1;
        s_learn.padOf[k] = best;
        Log("split screen: pad %d steers player %d's car (car %d, score %d)", best, k + 1, seats[k].car, bestScore);
    }
    // two pads, one assigned: the other one drives the other car (unless its steering says otherwise)
    for (int k = 0; k < 2 && connected == 2; k++) {
        if (s_learn.padOf[k] < 0 || s_learn.padOf[1 - k] >= 0) continue;
        for (int i = 0; i < XUSER_MAX_COUNT; i++)
            if (conn[i] && i != s_learn.padOf[k] && s_learn.score[i][1 - k] > -20) {
                s_learn.padOf[1 - k] = i;
                Log("split screen: so pad %d is player %d's (car %d)", i, 2 - k, seats[1 - k].car);
            }
    }
    seats[0].pad = s_learn.padOf[0];
    seats[1].pad = s_learn.padOf[1];
}

static int Seats(BYTE* logic, int me, Seat* seats)
{
    seats[0].car = me;
    seats[0].pad = kAllPads;
    if (CurrentBuild() != BUILD_STEAM || IsBadReadPtr(kRaceSetup, 0xE0)) return 1;
    // the screen count, not cGameLogic +0x58: that holds the player count in split screen too (8 of 8)
    int count = *(int*)(logic + 0x18);
    if (*(const int*)(kRaceSetup + 0x9C) != 2 || count < 2) return 1;
    for (int k = 0; k < 2; k++) {
        int car = *(const int*)(kRaceSetup + 0xD0 + 4 * k);
        seats[k].car = car >= 0 && car < count ? car : k;
        seats[k].pad = -1;
    }
    static int logged[4] = { -9, -9, -9, -9 };
    int now[4] = { seats[0].car, seats[1].car, *(const int*)(kRaceSetup + 0xD8), *(const int*)(kRaceSetup + 0xDC) };
    if (memcmp(logged, now, sizeof(now))) {
        memcpy(logged, now, sizeof(now));
        Log("split screen: player 1 car %d, player 2 car %d (game controllers %d / %d); each pad's car is learnt "
            "from the steering", now[0], now[1], now[2], now[3]);
    }
    LearnPads(logic, seats);
    return 2;
}

// Which local player the keys belong to: the one no learnt pad drives, else the first
static int KeysSeat(const Seat* seats, int n)
{
    return n == 2 && seats[0].pad >= 0 && seats[1].pad < 0 ? 1 : 0;
}

// ---------------------------------------------------------------- handbrake
//
// The game has no handbrake: its input stream reads a handbrake action, but the copy into the car
// (0xAB8BD0) writes 0 in its place and nothing reads it. Two stand-ins, both on game data or input only:
//  1 (HandbrakeMode=1): the car's forced drift timer (vehicle+0x64C), which a power play blast sets when it
//    throws a car sideways. While it is above 0 the game's own drift physics hold the car in a slide; it
//    runs down by the tick's time, so it is kept at 0.1 s while the button is held and set back to the
//    game's idle value -1 when it is let go (unless a blast set more meanwhile).
//  2 (HandbrakeMode=2): the pad the button is on reports the brake trigger fully pressed. The game then
//    brakes the rear more the more the car is steered and adds its brake turning torque. Only above
//    8 m/s forward, so the car never starts to reverse.
// Player 1's car only; the AI cars are not affected.
static BYTE s_brakeSlots;   // mode 2: pads that report the brake (bit per slot)
static int s_yawAxis = -1;  // mode 1: which rotation column of the chassis is up (0: +0xE0, 1: +0xF0), -1 = not known
static int s_yawSign = 1;   // the yaw rate's sign for positive steering

// While the car is driven normally it turns about its up axis, the way the steering points: the column
// of the chassis' rotation that the angular velocity follows is up, and the sign of steering x yaw rate
// is the steering's direction. Learnt over the first curves of the session.
static void YawLearn(BYTE* vehicle, BYTE* body)
{
    static float along[2], sign[2];
    float steer = *(float*)(vehicle + 0x500);
    const float* w = (const float*)(body + 0x1B0);
    if (fabsf(steer) < 0.3f || Dot(w, w) < 0.04f) return;
    for (int c = 0; c < 2; c++) {
        float d = Dot(w, (const float*)(body + (c ? 0xF0 : 0xE0)));
        along[c] += fabsf(d);
        sign[c] += steer * d;
    }
    int best = along[1] > along[0] ? 1 : 0;
    if (along[best] > 20.0f && along[best] > 3.0f * along[!best]) {  // clearly one axis
        int sgn = sign[best] >= 0 ? 1 : -1;
        if (best != s_yawAxis || sgn != s_yawSign) Log("handbrake: up axis %s, steering turns %s", best ? "+0xF0" : "+0xE0", sgn > 0 ? "with" : "against");
        s_yawAxis = best;
        s_yawSign = sgn;
    }
}
// One local player's car, with the pad it is driven with (Seat::pad) and its own drift flag (mode 1: the timer
// is ours). Mode 2: returns the XInput slots whose brake is to be pressed (bit per slot).
static BYTE Handbrake(BYTE* vehicle, BYTE* body, WORD buttons, int pad, bool& driftSet, float dt)
{
    float forward = 0;
    if (body) forward = Dot((float*)(body + 0x1A0), (float*)(body + 0x100));
    if (g_cfg.handbrakeMode == 2) {
        BYTE slots = 0;
        XINPUT_STATE st;
        if (forward > 8.0f && g_cfg.handbrakeButton)
            for (DWORD i = 0; i < XUSER_MAX_COUNT && PadsWrapped(); i++)
                if ((pad == kAllPads || pad == (int)i) && PadSlotConnected(i) &&
                    pXInputGetState(i, &st) == ERROR_SUCCESS && (st.Gamepad.wButtons & g_cfg.handbrakeButton))
                    slots |= 1 << i;
        // the keyboard: the first pad, in split screen player 1's (none when player 1 drives without a pad)
        if (forward > 8.0f && (buttons & KEY_HANDBRAKE)) slots |= pad == kAllPads ? 1 : pad >= 0 ? 1 << pad : 0;
        return slots;
    }
    if (!vehicle || IsBadWritePtr(vehicle + 0x64C, 4)) return 0;
    float* timer = (float*)(vehicle + 0x64C);
    bool held = (buttons & (g_cfg.handbrakeButton | KEY_HANDBRAKE)) != 0;
    if (!held && body && forward > 10.0f) YawLearn(vehicle, body);
    if (held && forward > 5.0f) {
        if (*timer < 0.1f) *timer = 0.1f;
        // the forced drift keeps the car's speed: the locked rear wheels' pull is added on the chassis,
        // HandbrakeStrength m/s^2 against the direction of travel, down to 5 m/s
        float* vel = (float*)(body + 0x1A0);
        float speed = sqrtf(Dot(vel, vel));
        float slower = speed - g_cfg.handbrakeStrength * dt;
        if (slower < 5.0f) slower = 5.0f;
        if (speed > slower && !IsBadWritePtr(vel, 12)) {
            float k = slower / speed;
            vel[0] *= k;
            vel[1] *= k;
            vel[2] *= k;
        }
        // The game's drift only sets a target angle (vehicle+0x138, at its limit at once); the tyres keep the
        // car in line. So the chassis is turned itself, as the boost pushes it: its yaw rate (angular
        // velocity about the car's up axis) is raised towards steering x HandbrakeDrift rad/s, while its
        // momentum carries it on: the car slides. Which axis is up and which way the steering turns are
        // learnt from normal driving (YawLearn), so nothing about the axes is assumed.
        float steer = *(float*)(vehicle + 0x500);
        int up = s_yawAxis;
        if (up >= 0 && g_cfg.handbrakeDrift > 0 && !IsBadWritePtr(body + 0x1B0, 12)) {
            const float* axis = (const float*)(body + (up ? 0xF0 : 0xE0));
            float* w = (float*)(body + 0x1B0);
            float cur = Dot(w, axis);
            float target = s_yawSign * steer * g_cfg.handbrakeDrift;
            // The tyres and the game's drift guidance take much of a turn back within the physics step, so
            // the turn rate is set straight to the target each frame rather than eased towards it.
            static float lastSet;  // what we set the frame before, to see what the physics kept of it
            static unsigned n;
            if (g_cfg.log && n++ % 10 == 0)
                Log("handbrake: yaw rate %.2f (we set %.2f the frame before), target %.2f, steer %.2f, %.0f km/h", cur,
                    lastSet, target, steer, forward * 3.6f);
            lastSet = cur;
            if (fabsf(target) > fabsf(cur) || target * cur < 0) {
                float add = target - cur;
                w[0] += axis[0] * add;
                w[1] += axis[1] * add;
                w[2] += axis[2] * add;
                lastSet = target;
            }
        }
        static bool logged;
        if (!logged) {
            logged = true;
            Log("handbrake: yaw axis %s, steering sign %+d, steer %.2f", up < 0 ? "not learnt yet (drive a few curves)" : up ? "+0xF0" : "+0xE0",
                s_yawSign, steer);
        }
        if (!driftSet) Log("handbrake: forced drift on (%.0f km/h)", forward * 3.6f);
        driftSet = true;
    } else if (driftSet) {
        if (*timer <= 0.1f) *timer = -1.0f;
        driftSet = false;
    }
    return 0;
}

// The pad as the game sees it in a race (dinput_wrap.cpp, every poll): the buttons the boost and the
// handbrake use are taken away from the game, and when one of them is Y, the game's Y (the camera
// change) moves to D-pad down, which the game does not use while driving. Mode 2 presses the brake.
void RacePad(int slot, XINPUT_GAMEPAD* pad)
{
    if (!RaceRunning()) {
        s_brakeSlots = 0;
        return;
    }
    WORD used = (g_cfg.boost ? g_cfg.boostButton : 0) | (g_cfg.handbrake ? g_cfg.handbrakeButton : 0);
    WORD keep = pad->wButtons;
    pad->wButtons &= ~used;
    if (used & XINPUT_GAMEPAD_Y) {
        pad->wButtons &= ~XINPUT_GAMEPAD_DPAD_DOWN;
        if (keep & XINPUT_GAMEPAD_DPAD_DOWN) pad->wButtons |= XINPUT_GAMEPAD_Y;
    }
    if (g_cfg.handbrake && g_cfg.handbrakeMode == 2 && slot >= 0 && slot < 8 && (s_brakeSlots & (1 << slot)))
        pad->bLeftTrigger = 255;
}

// what each local player's extras remember from frame to frame
struct SeatState {
    WORD lastButtons;
    float lastPos[3];
    bool driftSet;  // handbrake mode 1: the drift timer is ours
    bool wasBoost, wasBrake;
    int boostLogs, brakeLogs;
};
static SeatState s_seat[2];

void BoostTick()
{
    static DWORD lastTick;
    DWORD now = GetTickCount();
    float dt = lastTick ? (now - lastTick) / 1000.0f : 0.0f;
    lastTick = now;
    if (dt > 0.1f) dt = 0.1f;

    // the photo mode drives the camera with the pad, Shift and Ctrl: no boost or power bar meanwhile
    bool blocked = CameraBlocksPad();
    WORD keys = blocked ? 0 : KeyButtons();
    bool blurBoost = false;  // the boost's motion blur: single screen only (one blur for the whole screen)
    BYTE brakeSlots = 0;

    __try {
        BYTE* logic = GameLogic();
        Seat seats[2] = { { 0, kAllPads }, { 1, -1 } };
        int n = logic ? Seats(logic, LocalIndex(logic), seats) : 1;
        int keysSeat = KeysSeat(seats, n);
        BYTE *player0 = NULL, *body0 = NULL;
        for (int k = 0; k < n; k++) {
            SeatState& st = s_seat[k];
            WORD buttons = blocked ? 0 : PadButtons(seats[k].pad) | (k == keysSeat ? keys : 0);
            WORD pressed = buttons & ~st.lastButtons;
            st.lastButtons = buttons;
            int car = seats[k].car;
            BYTE* player = logic ? Player(logic, car) : NULL;
            if (!player) continue;
            const WORD fill = XINPUT_GAMEPAD_LEFT_THUMB | XINPUT_GAMEPAD_RIGHT_THUMB;
            if (g_cfg.powerFill && (buttons & fill) == fill && (pressed & fill)) {
                if (float* power = PowerValue(player)) {
                    *power = 100.0f;
                    if (n > 1) Log("power bar filled (player %d)", k + 1);
                    else Log("power bar filled");
                }
            }
            BYTE* body = Chassis(player, car);
            bool boosting = false;
            if (body && g_cfg.boost) {
                float* pos = (float*)(body + 0x110);
                bool moved = pos[0] != st.lastPos[0] || pos[1] != st.lastPos[1] || pos[2] != st.lastPos[2];
                memcpy(st.lastPos, pos, sizeof(st.lastPos));
                if ((buttons & (g_cfg.boostButton | KEY_BOOST)) && moved) boosting = Push(body, g_cfg.boostAcceleration, dt);
            }
            if (g_cfg.handbrake) brakeSlots |= Handbrake(Vehicle(player, car), body, buttons, seats[k].pad, st.driftSet, dt);
            if (k == 0) {
                player0 = player;
                body0 = body;
                blurBoost = n == 1 && boosting;
            }

            // Log=1: every start and end of a boost or handbrake pull with what held it (pad buttons, keys), so a
            // stuck key or pad shows (a remote desktop may leave Shift held down); the first 40 of each per player
            if (g_cfg.log) {
                bool brake = g_cfg.handbrake && (buttons & (g_cfg.handbrakeButton | KEY_HANDBRAKE));
                char who[16] = "";
                if (n > 1) sprintf_s(who, "player %d, ", k + 1);
                if (boosting != st.wasBoost && st.boostLogs < 40) {
                    st.boostLogs++;
                    Log("boost %s (%spad buttons %04X, boost key %s)", boosting ? "starts" : "ends", who,
                        PadButtons(seats[k].pad), (buttons & KEY_BOOST) ? "held" : "up");
                }
                if (brake != st.wasBrake && st.brakeLogs < 40) {
                    st.brakeLogs++;
                    Log("handbrake %s (%spad buttons %04X, handbrake key %s)", brake ? "pulled" : "released", who,
                        PadButtons(seats[k].pad), (buttons & KEY_HANDBRAKE) ? "held" : "up");
                }
                st.wasBoost = boosting;
                st.wasBrake = brake;
            }
        }
        if (player0 && g_cfg.aiBoost && (g_cfg.patchMask & PATCH_AIBOOST)) AiBoost(logic, player0, body0, n, dt);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        blurBoost = false;
    }
    s_brakeSlots = brakeSlots;

    // blur reaches full strength in 0.25 s and fades out over 0.5 s
    bool boosting = blurBoost;
    if (boosting) s_boostBlend = s_boostBlend + dt * 4.0f > 1.0f ? 1.0f : s_boostBlend + dt * 4.0f;
    else s_boostBlend = s_boostBlend - dt * 2.0f < 0.0f ? 0.0f : s_boostBlend - dt * 2.0f;
}
