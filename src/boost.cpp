// Split/Second PC fix - boost (X or BoostKey held), full power bar (LS+RS or PowerBarFillKey) for player 1,
// boost for the AI cars
//
// All of it works on game data reached from the in-game resources global, no code is touched:
//   [0xD66B28] cInGameResources  +0xE4 cGameLogic (vtable 0xBA9A74): +0x18 player count, +0x1C players,
//                                +0x58 local (human) players, they come first
//   player i cPlayerLogic (0xBA63D4): +0xE8 cPowerBar (0xBA8644) +0x2C output (0xBA8680), +8 power 0..100
//                                     +0xAC cVehicleState (0xBA7378) +0x40 vehicle list, +4 + i*4 vehicle i
//   vehicle +0x584 chassis hkpRigidBody (0xBE9C90): +0x100 forward axis, +0x110 position,
//                  +0x1A0 linear velocity, +0x1B0 angular velocity
// The power bar update (0x43D0F0) adds its gain to the output value and clamps it to 100, so setting the
// value is enough. A boost adds speed along the chassis' forward axis while the car is moving forward
// and the game is not paused (the chassis moved since the last frame).
#include "common.h"
#include <math.h>
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

static WORD PadButtons()
{
    WORD buttons = 0;
    XINPUT_STATE st;
    // polling empty slots is slow, so only the ones the pad wrapper has seen connected
    for (DWORD i = 0; i < XUSER_MAX_COUNT && pXInputGetState; i++)
        if (PadSlotConnected(i) && pXInputGetState(i, &st) == ERROR_SUCCESS) buttons |= st.Gamepad.wButtons;
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

static void AiBoost(BYTE* logic, BYTE* player0, BYTE* body0, float dt)
{
    if (g_cfg.aiBoostChaos) *kAiFullPowerBar = 1;
    int count = *(int*)(logic + 0x18);
    int humans = *(int*)(logic + 0x58);
    if (humans < 1 || humans >= count) humans = 1;
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
static bool s_driftSet;     // mode 1: the timer is ours

static void Handbrake(BYTE* vehicle, BYTE* body, WORD buttons, float dt)
{
    if (g_cfg.steamLan && OnlineSession()) {  // not part of the lobby rules yet: off in a network race
        s_brakeSlots = 0;
        return;
    }
    float forward = 0;
    if (body) forward = Dot((float*)(body + 0x1A0), (float*)(body + 0x100));
    if (g_cfg.handbrakeMode == 2) {
        BYTE slots = 0;
        XINPUT_STATE st;
        if (forward > 8.0f && g_cfg.handbrakeButton)
            for (DWORD i = 0; i < XUSER_MAX_COUNT && pXInputGetState; i++)
                if (PadSlotConnected(i) && pXInputGetState(i, &st) == ERROR_SUCCESS &&
                    (st.Gamepad.wButtons & g_cfg.handbrakeButton))
                    slots |= 1 << i;
        if (forward > 8.0f && (buttons & KEY_HANDBRAKE)) slots |= 1;  // the keyboard: the first pad
        s_brakeSlots = slots;
        return;
    }
    if (!vehicle || IsBadWritePtr(vehicle + 0x64C, 4)) return;
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
        if (!s_driftSet) Log("handbrake: forced drift on (%.0f km/h)", forward * 3.6f);
        s_driftSet = true;
    } else if (s_driftSet) {
        if (*timer <= 0.1f) *timer = -1.0f;
        s_driftSet = false;
    }
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

void BoostTick()
{
    static DWORD lastTick;
    static WORD lastButtons;
    static float lastPos[3];
    DWORD now = GetTickCount();
    float dt = lastTick ? (now - lastTick) / 1000.0f : 0.0f;
    lastTick = now;
    if (dt > 0.1f) dt = 0.1f;

    // the photo mode drives the camera with the pad, Shift and Ctrl: no boost or power bar meanwhile
    WORD buttons = CameraBlocksPad() ? 0 : PadButtons() | KeyButtons();
    WORD pressed = buttons & ~lastButtons;
    lastButtons = buttons;
    bool boosting = false;

    __try {
        BYTE* logic = GameLogic();
        int me = logic ? LocalIndex(logic) : 0;
        BYTE* player = logic ? Player(logic, me) : NULL;
        if (player) {
            const WORD fill = XINPUT_GAMEPAD_LEFT_THUMB | XINPUT_GAMEPAD_RIGHT_THUMB;
            if (g_cfg.powerFill && (buttons & fill) == fill && (pressed & fill)) {
                if (float* power = PowerValue(player)) {
                    *power = 100.0f;
                    Log("power bar filled");
                }
            }
            BYTE* body = Chassis(player, me);
            if (body && g_cfg.boost) {
                float* pos = (float*)(body + 0x110);
                bool moved = pos[0] != lastPos[0] || pos[1] != lastPos[1] || pos[2] != lastPos[2];
                memcpy(lastPos, pos, sizeof(lastPos));
                if ((buttons & (g_cfg.boostButton | KEY_BOOST)) && moved) boosting = Push(body, g_cfg.boostAcceleration, dt);
            }
            if (g_cfg.handbrake) Handbrake(Vehicle(player, me), body, buttons, dt);
            if (g_cfg.aiBoost && (g_cfg.patchMask & PATCH_AIBOOST)) AiBoost(logic, player, body, dt);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        boosting = false;
    }

    // blur reaches full strength in 0.25 s and fades out over 0.5 s
    if (boosting) s_boostBlend = s_boostBlend + dt * 4.0f > 1.0f ? 1.0f : s_boostBlend + dt * 4.0f;
    else s_boostBlend = s_boostBlend - dt * 2.0f < 0.0f ? 0.0f : s_boostBlend - dt * 2.0f;
}
