// Split/Second PC fix - GlassFix=1: the car windows' G-buffer alpha as on the Xbox 360.
//
// The game's deferred G-buffer: target 0 = sqrt(albedo), its alpha the specular intensity * 0.5; target 1 =
// normal * 0.5 + 0.5, its alpha the motion byte (object id, +128 on some pixels); target 2 = pre-lit colour,
// its alpha the specular power / 64. The sun pass ('ss_global_light_pass') lights every pixel with a
// specular of 2 * RT0.a * pow(.., 64 * RT2.a) * .. * shadow.
//
// The car glass (pixel shaders of VehicleGlassAndDamage 0xfd2297c8 and VehicleGlass01 0x48cca0a1) is drawn
// into all three targets in the pass 'Render Vehicles Alpha' (posh_alpha, 0x4b71b0). The shader writes
// oC0.a = oC2.a = the glass opacity (c110.x, about 0.87) and oC1.a = the motion byte. The Xbox blends each
// target on its own (0x8823d240): target 0 colour SRCALPHA/INVSRCALPHA, alpha ZERO/blend factor (0), so a
// glass pixel ends up with specular intensity 0; target 1 ONE/ZERO (the glass's normal and motion byte);
// target 2 colour SRCALPHA/INVSRCALPHA, its alpha not written. Direct3D 9 has one blend state for all
// targets, and the PC port sets colour SRCALPHA/INVSRCALPHA with alpha ONE/ZERO (separate alpha on) and
// COLORWRITEENABLE2 = 7: every glass pixel writes the opacity into RT0.a as specular intensity, while RT2.a
// keeps the specular power of the surface behind. Behind the glass of the DLC cars' window frames that power
// is 0, pow(x, 0) = 1, and the sun pass paints the frames flat white; on the base cars the glass gets a sun
// highlight with the power of whatever lies behind it. The PC's car select glass (0x4553be) does it right
// already: alpha ZERO/blend factor with a blend factor of 0.
//
// The fix, for the draws of those pixel shaders with exactly the race pass's blend state (alpha blending on,
// SRCALPHA/INVSRCALPHA ADD, separate alpha ONE/ZERO ADD, COLORWRITEENABLE 0xF/0xF/7, targets 1 and 2 set):
//   SRCBLENDALPHA ONE -> ZERO: with DESTBLENDALPHA ZERO the alpha written is src*0 + dst*0 = 0, RT0.a = 0 as
//     on the Xbox. (The car select reaches the same 0 with DESTBLENDALPHA = BLENDFACTOR and a factor of 0;
//     ZERO/ZERO gives it whatever blend factor the game left set, and DESTBLENDALPHA needs no change.)
//   COLORWRITEENABLE1 0xF -> 7: the same alpha would put 0 into RT1.a, motion byte 0 (not the car), and the
//     motion blur would smear the windows like the world behind the car. Masked, RT1.a keeps the motion
//     byte of the surface behind the glass, which is the same car's interior or body in nearly every pixel.
// Both states are put back to the values read after the draw, so the game's own state cache stays right.
//
// What remains different from the Xbox, and why that is acceptable:
//   * RT1.rgb, the normal, is blended SRCALPHA/INVSRCALPHA like the other targets instead of replaced (the
//     weight is the shader's alpha for that target as the hardware takes it): a mix of the glass's and the
//     interior's normal. It only moves the diffuse sun term of the glass pixel (RT0.a = 0 leaves it no sun
//     specular); replacing it would take a second draw of every window into target 1 alone.
//   * RT1.a is the motion byte of the surface behind instead of the glass's own: the same car, except where
//     a window shows the world through the car (no interior behind it), and its +128 bit is that surface's.
// Everything else (RT0 and RT2 colour, RT2.a) is written as before, so the glass's reflections stay.
//
// Detection is cheap: the pixel shaders are tagged when they are made (CreatePixelShader, by the hash the
// frame dump uses) and the shader set last is noted in SetPixelShader (one table lookup). A draw costs one
// test of g_glassBound; only the draws of a glass shader read the render states. No game code is changed:
// only device vtable slots (diag.cpp) and our own SetRenderState calls.
#include "common.h"
#include <d3d9.h>
#include <string.h>

// the code hashes (diag.cpp's Fnv64 over the token stream, tools/dlc/shaders/pshash.py) of the glass pixel
// shaders in every car body .fxc of resident.ark and of the DLC archives
static const ULONGLONG kGlassShaders[] = {
    0x9a5e32c27757850full,  // VehicleGlassAndDamage 0xfd2297c8, variants 0, 1, 3, 4
    0x8ad096b6d9e32dafull,  // VehicleGlassAndDamage 0xfd2297c8, variant 2
    0x4e4c069ef9e9005full,  // VehicleGlass01 0x48cca0a1, variants 0, 1, 3, 4
    0x24b4c192e1224a1full,  // VehicleGlass01 0x48cca0a1, variant 2
};

bool g_glassBound;  // the pixel shader set last is a glass shader (render thread)

// Every pixel shader address seen, with its tag. Open addressing, written under the lock, read without it
// (an address is stored after its tag). A shader released and another made at its address gets the new
// one's tag when it is made through the hook. When the table is 3/4 full it is emptied; what is set from
// then on is learned again from the runtime's copy of its code.
enum { TAG_NONE, TAG_OTHER, TAG_GLASS };
// track loads make new shaders at new addresses all session long (4096 slots filled up within minutes in a race):
// 32768 x 5 bytes, 160 KB
static const int kBits = 15;
static const int kSlots = 1 << kBits;
static void* volatile s_key[kSlots];
static volatile BYTE s_tag[kSlots];
static int s_used;
static volatile LONG s_glassCount;  // addresses tagged as glass now
static LONG s_learned;              // shaders tagged at SetPixelShader (made past the hook)
static CRITICAL_SECTION s_lock;
static bool s_ready, s_off;         // s_off: a pure device, the states cannot be read
static volatile LONG s_changed;     // draws changed (Log=1: every 10 s)
static bool s_firstLogged;

static DWORD Slot(const void* p) { return ((DWORD)(UINT_PTR)p >> 3) * 2654435761u >> (32 - kBits); }

// odd while Store empties the table: a lookup that overlaps it (its key read before, the slot's tag after, a new
// shader's) counts as not found, and the caller learns the shader's tag again from its code
static volatile LONG s_gen;

static BYTE Lookup(const void* p)
{
    const LONG gen = s_gen;
    if (gen & 1) return TAG_NONE;
    DWORD i = Slot(p);
    for (int n = 0; n < kSlots; n++, i = (i + 1) & (kSlots - 1)) {
        void* k = s_key[i];
        if (k == p) {
            const BYTE tag = s_tag[i];
            return s_gen == gen ? tag : TAG_NONE;
        }
        if (!k) return TAG_NONE;
    }
    return TAG_NONE;
}

static BYTE TagOf(ULONGLONG hash)
{
    for (ULONGLONG g : kGlassShaders)
        if (hash == g) return TAG_GLASS;
    return TAG_OTHER;
}

// under the lock
static void Store(void* p, BYTE tag)
{
    if (s_used >= kSlots / 4 * 3) {
        InterlockedIncrement(&s_gen);  // odd: Lookup finds nothing meanwhile
        for (int i = 0; i < kSlots; i++) s_key[i] = NULL;
        s_used = 0;
        s_glassCount = 0;
        InterlockedIncrement(&s_gen);
        if (g_cfg.log) Log("glass fix: the shader table is full, it starts again");
    }
    DWORD i = Slot(p);
    for (int n = 0; n < kSlots; n++, i = (i + 1) & (kSlots - 1)) {
        if (s_key[i] == p) {
            if (s_tag[i] != tag) s_glassCount += tag == TAG_GLASS ? 1 : -1;
            s_tag[i] = tag;
            return;
        }
        if (!s_key[i]) {
            s_tag[i] = tag;
            s_key[i] = p;
            s_used++;
            if (tag == TAG_GLASS) s_glassCount++;
            return;
        }
    }
}

void GlassDeviceCreated(DWORD flags)
{
    if (!s_ready) {
        InitializeCriticalSection(&s_lock);
        s_ready = true;
    }
    s_off = (flags & D3DCREATE_PUREDEVICE) != 0;
    if (s_off)
        Log("glass fix: the device is a pure device, its render states cannot be read; the fix stays off");
    else if (g_cfg.log)
        Log("glass fix: on (car glass: specular intensity 0, motion byte of the surface behind)");
}

void GlassShaderCreated(void* ps, ULONGLONG hash)
{
    if (!s_ready || !ps) return;
    EnterCriticalSection(&s_lock);
    Store(ps, TagOf(hash));
    LeaveCriticalSection(&s_lock);
}

void GlassPixelShaderSet(IDirect3DPixelShader9* ps)
{
    if (!ps || s_off || !s_ready) {
        g_glassBound = false;
        return;
    }
    BYTE tag = Lookup(ps);
    if (tag == TAG_NONE) {  // made past the hook (or the table started again): its code as the runtime holds it
        tag = TagOf(PixelShaderHash(ps));
        EnterCriticalSection(&s_lock);
        Store(ps, tag);
        s_learned++;
        LeaveCriticalSection(&s_lock);
    }
    g_glassBound = tag == TAG_GLASS;
}

// a Reset leaves the device without a pixel shader; the shaders themselves and their tags stay valid
void GlassDeviceReset() { g_glassBound = false; }

// around a pass of our own whose state block puts the game's shader back past the hook
static bool s_savedBound;
void GlassSaveTracking() { s_savedBound = g_glassBound; }
void GlassRestoreTracking() { g_glassBound = s_savedBound; }

LONG GlassTakeChangedDraws() { return InterlockedExchange(&s_changed, 0); }

// Log=1: the first few different states a glass shader was drawn with and left alone (the car select is one)
static void LogMiss(const DWORD* v, int n, bool mrt)
{
    static DWORD seen[6][12];
    static int seenCount;
    DWORD key[12];
    for (int i = 0; i < n; i++) key[i] = v[i];
    key[n] = mrt;
    for (int i = 0; i < seenCount; i++)
        if (!memcmp(seen[i], key, sizeof(key))) return;
    if (seenCount >= 6) return;
    memcpy(seen[seenCount++], key, sizeof(key));
    Log("glass fix: a glass shader draw left as it is: blend %lu, %lu/%lu op %lu, separate alpha %lu, %lu/%lu op %lu, "
        "write %lx %lx %lx, targets 1 and 2 %s",
        v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8], v[9], v[10], mrt ? "set" : "not set");
}

bool GlassBegin(IDirect3DDevice9* dev, GlassSaved* saved, const char* call, UINT prims)
{
    static const D3DRENDERSTATETYPE states[11] = {
        D3DRS_ALPHABLENDENABLE, D3DRS_SRCBLEND, D3DRS_DESTBLEND, D3DRS_BLENDOP, D3DRS_SEPARATEALPHABLENDENABLE,
        D3DRS_SRCBLENDALPHA, D3DRS_DESTBLENDALPHA, D3DRS_BLENDOPALPHA, D3DRS_COLORWRITEENABLE, D3DRS_COLORWRITEENABLE1,
        D3DRS_COLORWRITEENABLE2,
    };
    static const DWORD want[11] = {
        TRUE, D3DBLEND_SRCALPHA, D3DBLEND_INVSRCALPHA, D3DBLENDOP_ADD, TRUE,
        D3DBLEND_ONE, D3DBLEND_ZERO, D3DBLENDOP_ADD, 0xF, 0xF,
        0x7,
    };
    // the shader actually bound: g_glassBound can be stale when a state block or the runtime set the shader past
    // the SetPixelShader hook; one AddRef/Release, only on draws already thought to be glass
    IDirect3DPixelShader9* cur = NULL;
    if (FAILED(dev->GetPixelShader(&cur))) cur = NULL;
    if (cur) cur->Release();
    if (!cur || Lookup(cur) != TAG_GLASS) {
        g_glassBound = false;
        return false;
    }
    DWORD v[11];
    bool match = true;
    for (int i = 0; i < 11; i++) {
        if (FAILED(dev->GetRenderState(states[i], &v[i]))) v[i] = 0xFFFFFFFF;
        if (i == 0 || i == 4) v[i] = v[i] != 0 && v[i] != 0xFFFFFFFF;  // any nonzero is on
        if (v[i] != want[i]) match = false;
    }
    bool mrt = false;
    if (match || g_cfg.log) {  // the G-buffer: targets 1 and 2 set
        IDirect3DSurface9 *rt1 = NULL, *rt2 = NULL;
        mrt = SUCCEEDED(dev->GetRenderTarget(1, &rt1)) && rt1 && SUCCEEDED(dev->GetRenderTarget(2, &rt2)) && rt2;
        if (rt1) rt1->Release();
        if (rt2) rt2->Release();
        match = match && mrt;
    }
    if (!match) {
        if (g_cfg.log) LogMiss(v, 11, mrt);
        return false;
    }
    saved->srcBlendAlpha = v[5];
    saved->colorWrite1 = v[9];
    // the device's own SetRenderState (the slot is not hooked), past the game's state cache
    dev->SetRenderState(D3DRS_SRCBLENDALPHA, D3DBLEND_ZERO);
    dev->SetRenderState(D3DRS_COLORWRITEENABLE1, v[9] & 0x7);
    InterlockedIncrement(&s_changed);
    if (!s_firstLogged && g_cfg.log) {
        s_firstLogged = true;
        Log("glass fix: first changed draw (%s, %u primitives); %ld glass pixel shaders known, %ld shaders tagged at "
            "SetPixelShader",
            call, prims, s_glassCount, s_learned);
    }
    return true;
}

void GlassEnd(IDirect3DDevice9* dev, const GlassSaved* saved)
{
    dev->SetRenderState(D3DRS_COLORWRITEENABLE1, saved->colorWrite1);
    dev->SetRenderState(D3DRS_SRCBLENDALPHA, saved->srcBlendAlpha);
}
