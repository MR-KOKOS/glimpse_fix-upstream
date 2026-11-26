// Split/Second PC fix - full ultrawide support, experimental (UltrawideFix=2)
// SPDX-License-Identifier: GPL-3.0-only
//
// UltrawideFix=1 (camera.cpp) widens the two race cameras. This mode corrects the game itself instead:
//
// * 3D: every camera (race, menus, power play and crash cameras, split screen) takes its horizontal field of
//   view from one 16/9 float in .rdata (0x00BAA9DC, read by the FOV conversion, the aspect getter, the frustum
//   size and the tripod cameras). Wider screens than 16:9 lose picture at the top and bottom (Vert-). The real
//   back buffer aspect written there gives Hor+ for all of them. Written after CreateDevice and every Reset.
// * 2D: the Scaleform movies get a viewport of the whole back buffer, and most of them stretch their 16:9
//   layout to it. GFxMovieRoot::SetViewport (vtable 0xC41CF4 slot 20) narrows full-width viewports to a
//   centred 16:9 rect. Some screens need more: full-width bands and backgrounds of the results, cards and car
//   select stretch to the screen edges (renderer hooks, vtable 0xBC93DC), the loading screen is zoomed with
//   black sides, the split screen HUD is drawn once per player half (Display, slot 31).
// * Bink cinematics keep their own aspect with black sides; 3D player tags keep their 16:9 proportions;
//   the engine's letterbox in pause and race intro is off; the last pixel column (wrong in the stock game
//   above 16:9) is copied from its neighbour.
//
// Only data changes: vtable and import slots, the .rdata constant, three .data tweakables. No game code is
// patched; every slot and value is checked before it is changed, and anything not recognised stays stock.
// Made on the Steam build (4BE13C66) only; the other builds keep UltrawideFix=1. The addresses below are that
// build's, written as plain literals (not through G()): CheckExe turns the mode off on any other build before
// one of them is used.
//
// The aspect constant has nine readers in the game's code. Four are checked (kSigs) and are what this mode is
// for; the others read it as well and so follow the screen's aspect too: 0x4AFBA8 (a field of view / size
// division), 0x4B45E6 and 0x4B4C25 (two camera setups), 0x794A8D (an argument of 0x50B180) and 0xA9F3AB (a
// screen space distance check). One more reason the mode is marked experimental.
//
// Threading: the device is created multithreaded, but the movie and renderer hooks run on the render thread
// only (Scaleform's SetViewport and Display, the renderer's draws); their state is kept without locks.
#include "common.h"
#include <d3d9.h>
#include <math.h>
#include <string.h>

// ---------------------------------------------------------------- version check and the aspect constant

static const DWORD kAspectVA = 0x00BAA9DC;       // .rdata, 16/9
static const DWORD kStock16x9 = 0x3FE38E39;      // 16/9 as float bits
static const DWORD kScreen = 0x00D64708;         // pointer; +0x30 width, +0x34 height
struct Sig { DWORD va; BYTE bytes[8]; const char* what; };
static const Sig kSigs[] = {  // code that reads the constant
    { 0x00A59A51, { 0xD8, 0x0D, 0xDC, 0xA9, 0xBA, 0x00, 0xD9, 0xE8 }, "camera fov conversion" },
    { 0x00794390, { 0xF3, 0x0F, 0x10, 0x05, 0xDC, 0xA9, 0xBA, 0x00 }, "aspect getter" },
    { 0x00451680, { 0xD8, 0x0D, 0xDC, 0xA9, 0xBA, 0x00, 0xD9, 0xC0 }, "frustum size" },
    { 0x00AAB02D, { 0xF3, 0x0F, 0x59, 0x05, 0xDC, 0xA9, 0xBA, 0x00 }, "tripod camera extents" },
};

static int s_state;                    // 0 not checked, 1 active, -1 off
static volatile LONG s_frame;          // presented frames
static volatile LONG s_scrW, s_scrH;   // back buffer size
static IDirect3DDevice9* s_dev;

static bool Readable(const void* p, size_t n) { return p && !IsBadReadPtr(p, n); }

static bool CheckExe()
{
    if (CurrentBuild() != BUILD_STEAM) {
        Log("ultrawide: made for the Steam build only (%s), UltrawideFix=1 used instead", BuildName());
        return false;
    }
    if (GetModuleHandleA("DXFreezerServer.dll")) {
        // the standalone ultrawide module (Split/Second Community Patcher) does the same; both would widen twice
        Log("ultrawide: DXFreezerServer.dll (standalone ultrawide module) is loaded, UltrawideFix=2 left off");
        return false;
    }
    for (const Sig& s : kSigs)
        if (!Readable((void*)s.va, sizeof s.bytes) || memcmp((void*)s.va, s.bytes, sizeof s.bytes)) {
            Log("ultrawide: unexpected code at %08lx (%s), UltrawideFix=1 used instead", s.va, s.what);
            return false;
        }
    if (!Readable((void*)kAspectVA, 4) || *(DWORD*)kAspectVA != kStock16x9) {
        Log("ultrawide: aspect constant at %08lx is not 16/9, UltrawideFix=1 used instead", kAspectVA);
        return false;
    }
    return true;
}

static void WriteAspect(float value)
{
    DWORD old;
    if (!VirtualProtect((void*)kAspectVA, 4, PAGE_READWRITE, &old)) return;
    *(float*)kAspectVA = value;
    VirtualProtect((void*)kAspectVA, 4, old, &old);
}

// ---------------------------------------------------------------- movies

// GViewport as GFx 2.x keeps it (13 dwords at GFxMovieRoot+0x38)
struct GViewport { int bufW, bufH, left, top, w, h, scL, scT, scW, scH; float scale, aspect; unsigned flags; };
enum { VP_SCISSOR = 4 };

// what each screen needs, by file name
static const char kKeepStretched[] =  // full-screen effects and 3D-attached movies: stay full width
    "FE_Transitions.gfx;ingameTransitions.gfx;FE_RouteChangeLetterbox.gfx;PowerPlayIcons.gfx;HUD_PlayerName_3D.gfx;"
    "DemoTutorial.gfx";
static const char kPillarbox[] = "FE_LoadingScreen.gfx";       // zoomed, black sides
static const char kZoomOnly[] = "FE_PauseMenu_NEW.gfx";        // zoomed, the 3D scene stays at the sides
static const char kEdgeLine[] = "FE_PauseMenu_NEW.gfx";        // its frame line repeated at the left edge
static const char kExtend[] =  // centred, full-width shapes stretched to the screen edges
    "FE_vehicleSelect.gfx;VehicleSelect.gfx;FE_InGameEpisodeSelect.gfx;FE_InGameEventSelect2.gfx;"
    "IG_GameModeInstructions.gfx;EventObjectives.gfx;RaceEnd.gfx;RaceResults.gfx;RaceStats.gfx;"
    "CarUnlock_*;DecalUnlock.gfx;Rewards.gfx";
static const char kExtendMask1[] =  // kExtend movies whose root reveal strip mask is extended (depth-1 masked shapes auto-extend)
    "CarUnlock_*;DecalUnlock.gfx;Rewards.gfx";
static const char kSplitHud[] = "Hud.gfx;DialsInfo.gfx;DialsPower.gfx;TimerOverlay.gfx";  // split screen: per half
static const float kPillarZoom = 1.35f;  // pushes the loading screen's own top and bottom bars off screen
static const float kEdgeOffset = -63;    // pause frame line: x from the stage's left edge, stage units

enum {
    M_KEEP = 1, M_PILLAR = 2, M_ZOOM = 4, M_EDGELINE = 8, M_EXTEND = 0x10, M_SPLITHUD = 0x20,
    M_DIALPOWER = 0x40, M_DIALINFO = 0x80, M_LOGGED = 0x100, M_EXTEND_MASK1 = 0x200,
};

static const char* BaseName(const char* path)
{
    const char* base = path;  // paths mix '/' and '\\' (e.g. "Resident|UI\\CommonUI\\FE_LoadingScreen.gfx")
    for (const char* c = path; *c; c++)
        if (*c == '/' || *c == '\\' || *c == '|') base = c + 1;
    return base;
}

static bool MatchPattern(const char* pat, size_t plen, const char* name)
{
    if (plen > 0 && pat[plen - 1] == '*') return !_strnicmp(pat, name, plen - 1);
    return strlen(name) == plen && !_strnicmp(pat, name, plen);
}

static bool InList(const char* list, const char* base)
{
    for (const char* t = list; *t;) {
        const char* e = strchr(t, ';');
        size_t len = e ? (size_t)(e - t) : strlen(t);
        if (MatchPattern(t, len, base)) return true;
        t += len + (e ? 1 : 0);
    }
    return false;
}

// movie file name: [movie+0x28] = GFxMovieDefImpl (vtable 0xC3D5CC); its file URL is the GString at
// [[[[def+0x1C]+0x10]+0x1C]+0x20] + 0x0C
bool ReadMovieName(void* movie, char* out, size_t size)
{
    __try {
        BYTE* p = *(BYTE**)((BYTE*)movie + 0x28);
        if (!p || *(DWORD*)p != 0x00C3D5CC) return false;
        p = *(BYTE**)(p + 0x1C);
        p = *(BYTE**)(p + 0x10);
        p = *(BYTE**)(p + 0x1C);
        p = *(BYTE**)(p + 0x20);
        strncpy_s(out, size, BaseName((const char*)p + 0x0C), _TRUNCATE);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

// Per movie, once: its name and what it needs. Keyed by the movie and its definition, so a new movie at a
// freed address is looked up again; the oldest entry is replaced when the table is full.
struct MovieInfo { void* movie; void* def; unsigned flags; char name[48]; };
static MovieInfo s_movies[64];
static unsigned s_movieNext;

static MovieInfo* Movie(void* movie)
{
    void* def = *(void**)((BYTE*)movie + 0x28);
    for (MovieInfo& m : s_movies)
        if (m.movie == movie && m.def == def) return &m;
    MovieInfo& m = s_movies[s_movieNext++ % 64];
    m.movie = movie;
    m.def = def;
    m.flags = 0;
    if (!ReadMovieName(movie, m.name, sizeof m.name)) strcpy_s(m.name, "(unnamed movie)");
    if (InList(kKeepStretched, m.name)) m.flags |= M_KEEP;
    if (InList(kPillarbox, m.name)) m.flags |= M_PILLAR;
    if (InList(kZoomOnly, m.name)) m.flags |= M_ZOOM;
    if (InList(kEdgeLine, m.name)) m.flags |= M_EDGELINE;
    if (InList(kExtend, m.name)) m.flags |= M_EXTEND;
    if (InList(kExtendMask1, m.name)) m.flags |= M_EXTEND_MASK1;
    if (InList(kSplitHud, m.name)) m.flags |= M_SPLITHUD;
    if (!_stricmp(m.name, "DialsPower.gfx")) m.flags |= M_DIALPOWER;
    if (!_stricmp(m.name, "DialsInfo.gfx")) m.flags |= M_DIALINFO;
    return &m;
}

static void Note(MovieInfo* m, const char* what)
{
    if (m->flags & M_LOGGED) return;
    m->flags |= M_LOGGED;
    Log("ultrawide: %s -> %s", m->name, what);
}

// Split screen: the viewport manager [0xD66000] holds the number of screen viewports at +0 (1 = one player)
// and a forced viewport index at +8 (-1 = none)
static int LocalPlayers()
{
    __try {
        int* mgr = *(int**)0x00D66000;
        if (!mgr) return 0;
        return mgr[2] == -1 && mgr[0] >= 2 && mgr[0] <= 4 ? mgr[0] : 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

// ---------------------------------------------------------------- Scaleform viewports: centred 16:9

typedef void(__thiscall* SetViewport_t)(void*, const GViewport*);
typedef void(__thiscall* Display_t)(void*);
static void** const kGfxSetViewportSlot = (void**)(0x00C41CF4 + 20 * 4);
static void** const kGfxDisplaySlot = (void**)(0x00C41CF4 + 31 * 4);
static SetViewport_t s_origSetViewport;
static Display_t s_origDisplay;

static volatile LONG s_pillarFrame = -10;  // a pillarboxed movie was drawn: Present clears the sides
static int s_pillarLeft, s_pillarWidth;
static int s_uiBufW;                       // width of the buffer the UI viewports refer to (2x at Very High)
static void* s_edgeMovie;                  // the pause menu, whose frame line is repeated at the left edge
static float s_edgeVpL, s_edgeVpW;         // its viewport (UI buffer px)
static struct { void* movie; LONG frame; } s_split[8];  // split screen HUD movies, drawn once per half

static void MarkSplit(void* movie)
{
    int free = -1;
    for (int i = 0; i < 8; i++) {
        if (s_split[i].movie == movie) { s_split[i].frame = s_frame; return; }
        if (free < 0 && (!s_split[i].movie || s_frame - s_split[i].frame > 30)) free = i;
    }
    if (free >= 0) { s_split[free].movie = movie; s_split[free].frame = s_frame; }
}

static bool IsSplit(void* movie)
{
    for (int i = 0; i < 8; i++)
        if (s_split[i].movie == movie) return s_frame - s_split[i].frame <= 2;
    return false;
}

static bool FullWidthWide(const GViewport* vp)
{
    return vp->left == 0 && vp->w == vp->bufW && vp->h > 0 && vp->w * 9 > vp->h * 16 + 9;
}

static void __fastcall Hook_SetViewport(void* self, void*, const GViewport* vp)
{
    // 16:9 and narrower screens keep the stock UI, and so do render-to-texture targets (their own buffer size,
    // not the screen's or its 2x supersampled copy): event select panels, 3D monitors
    if (!vp || s_scrH <= 0 || s_scrW * 9 <= s_scrH * 16 + 9 ||
        (!(vp->bufW == s_scrW && vp->bufH == s_scrH) && !(vp->bufW == 2 * s_scrW && vp->bufH == 2 * s_scrH))) {
        s_origSetViewport(self, vp);
        return;
    }
    MovieInfo* m = Movie(self);
    int players = FullWidthWide(vp) || vp->w * 2 <= vp->bufW + 2 ? LocalPlayers() : 1;
    if (FullWidthWide(vp) && players >= 2) {
        // split screen race HUD: one movie with each player's HUD in one half of its stage; it keeps the full
        // viewport and Hook_Display draws it once per screen half
        if (m->flags & M_SPLITHUD) {
            Note(m, "split screen, drawn once per player half");
            MarkSplit(self);
            s_origSetViewport(self, vp);
            return;
        }
    }
    if (vp->h > 0 && vp->w * 2 <= vp->bufW + 2 && vp->w * 9 > vp->h * 8 + 9 && players >= 2) {
        // split screen, per-player movie in its own half: authored for an 8:9 half, narrowed to it, centred
        Note(m, "split screen half, narrowed to 8:9");
        GViewport v = *vp;
        int w = (v.h * 8 + 4) / 9;
        v.left += (v.w - w) / 2;
        v.w = w;
        if (v.flags & VP_SCISSOR) { v.scL = v.left; v.scW = w; }
        s_origSetViewport(self, &v);
        return;
    }
    if (!FullWidthWide(vp)) {
        s_origSetViewport(self, vp);
        return;
    }
    if (m->flags & (M_KEEP | M_DIALPOWER | M_DIALINFO)) {  // the dials are textures for a 3D panel
        Note(m, "full width");
        s_origSetViewport(self, vp);
        return;
    }
    if (m->flags & M_EXTEND) {
        // ShowAll (scale mode 1) on the full viewport: the 16:9 stage centred, nothing clipped; the renderer
        // hooks stretch the full-width shapes to the screen edges
        Note(m, "centred 16:9, full-width shapes extended");
        unsigned* mode = (unsigned*)((BYTE*)self + 0x80);
        if (*mode == 2) *mode = 1;
        s_origSetViewport(self, vp);
        return;
    }
    if (m->flags & (M_PILLAR | M_ZOOM)) {
        // the 16:9 stage scaled by kPillarZoom around the screen centre (at most to the screen width); its own
        // letterbox bars above and below are cut by the scissor, Present clears the sides of pillarboxed ones
        Note(m, m->flags & M_PILLAR ? "centred 16:9, zoomed, black sides" : "centred 16:9, zoomed");
        GViewport v = *vp;
        float zoom = kPillarZoom, maxZoom = v.w / (v.h * 16 / 9.0f);
        if (zoom > maxZoom) zoom = maxZoom;
        int w = (int)((v.h * 16 / 9.0f) * zoom + 0.5f), h = (int)(v.h * zoom + 0.5f);
        if (w > v.w) w = v.w;
        v.left = (v.w - w) / 2;
        v.top = (vp->h - h) / 2;
        v.w = w;
        v.h = h;
        v.scL = v.left; v.scT = 0; v.scW = w; v.scH = vp->h;
        v.flags |= VP_SCISSOR;
        s_uiBufW = vp->bufW;
        if (m->flags & M_PILLAR) { s_pillarWidth = w; s_pillarLeft = v.left; s_pillarFrame = s_frame; }
        if (m->flags & M_EDGELINE) { s_edgeMovie = self; s_edgeVpL = (float)v.left; s_edgeVpW = (float)v.w; }
        s_origSetViewport(self, &v);
        return;
    }
    Note(m, "centred 16:9");
    GViewport v = *vp;
    int w = (v.h * 16 + 8) / 9;
    v.left = (v.w - w) / 2;
    v.w = w;
    if (v.scW == vp->bufW && v.scL == 0) { v.scL = v.left; v.scW = w; }
    s_origSetViewport(self, &v);
}

// Split screen's power bar uses its own HUD texture. Its quad comes out too wide on wider halves
// (2x at 32:9). The quad's corners:
// player HUD [[0xD714D0]+0xCC+4i] -> +0x12C power bar (vtable 0xBD0FD4) or +0x128 info bar (0xBD06E8) -> +8 dial
// (0xBD1C04) -> +0x78 wrapper, 4 x vec3 at +0x2C/+0x3C/+0x4C/+0x5C. Its long axis is scaled by 16:9 / aspect.
static void NarrowDials(int kind, float f)
{
    static float s_done[2][4][12];  // corners as last written, so a quad is never scaled twice
    __try {
        BYTE* mgr = *(BYTE**)0x00D714D0;
        for (int i = 0; mgr && i < 4; i++) {
            BYTE* hud = *(BYTE**)(mgr + 0xCC + 4 * i);
            BYTE* pb = hud ? *(BYTE**)(hud + (kind == 1 ? 0x12C : 0x128)) : NULL;
            if (!pb || *(DWORD*)pb != (kind == 1 ? 0x00BD0FD4u : 0x00BD06E8u)) continue;
            BYTE* dial = *(BYTE**)(pb + 8);
            if (!dial || *(DWORD*)dial != 0x00BD1C04) continue;
            BYTE* w = *(BYTE**)(dial + 0x78);
            if (!w) continue;
            float p[4][3];
            for (int k = 0; k < 4; k++) memcpy(p[k], w + 0x2C + 0x10 * k, 12);
            if (!memcmp(p, s_done[kind - 1][i], sizeof p)) continue;
            // two edges and the diagonal from corner 0; the longer edge is the bar's axis
            float c[3], e[3][3], len[3];
            for (int j = 0; j < 3; j++) c[j] = (p[0][j] + p[1][j] + p[2][j] + p[3][j]) / 4;
            for (int k = 0; k < 3; k++) {
                for (int j = 0; j < 3; j++) e[k][j] = p[k + 1][j] - p[0][j];
                len[k] = e[k][0] * e[k][0] + e[k][1] * e[k][1] + e[k][2] * e[k][2];
            }
            int diag = len[0] >= len[1] ? (len[0] >= len[2] ? 0 : 2) : (len[1] >= len[2] ? 1 : 2);
            int ia = diag == 0 ? 1 : 0, ib = diag == 2 ? 1 : 2;
            int edge = len[ia] >= len[ib] ? ia : ib;
            float* ax = e[edge];
            float l2 = len[edge];
            if (l2 <= 0) continue;
            for (int k = 0; k < 4; k++) {
                float d = ((p[k][0] - c[0]) * ax[0] + (p[k][1] - c[1]) * ax[1] + (p[k][2] - c[2]) * ax[2]) / l2;
                for (int j = 0; j < 3; j++) p[k][j] -= (1 - f) * d * ax[j];
                memcpy(w + 0x2C + 0x10 * k, p[k], 12);
            }
            memcpy(s_done[kind - 1][i], p, sizeof p);
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

// ---------------------------------------------------------------- edge extension (renderer hooks)
// The game's Scaleform renderer (vtable 0xBC93DC, GFx 2.x GRenderer): slot 7 SetMatrix (6 floats), slot 14
// SetVertexData(ptr, count, format, cache), slot 17 DrawIndexedTriList(base, min, count, start, tris),
// 18 DrawLineStrip(base, lines), 26/27/28 BeginSubmitMask / EndSubmitMask / DisableMask. For the kExtend
// movies (drawn ShowAll on the full viewport) each filled shape that is wide and touches a stage side is
// stretched to that screen edge; text and everything inside the stage keep their size.

typedef void(__thiscall* RSetMatrix_t)(void*, const float*);
typedef void(__thiscall* RSetVerts_t)(void*, const void*, int, int, void*);
typedef void(__thiscall* RDrawTri_t)(void*, int, int, int, int, int);
typedef void(__thiscall* RLine_t)(void*, int, int);
typedef void(__thiscall* RArg_t)(void*, int);
typedef void(__thiscall* RVoid_t)(void*);
static void** const kRendVt = (void**)0x00BC93DC;
static RSetMatrix_t s_rSetMatrix;
static RSetVerts_t s_rSetVerts;
static RDrawTri_t s_rDrawTri;
static RLine_t s_rDrawLine;
static RArg_t s_rBeginMask;
static RVoid_t s_rEndMask, s_rDisableMask;
static float s_rMat[6];
static const BYTE* s_rVerts;
static int s_rVertCount, s_rVertFmt;
static const char* s_extendName;  // set while a kExtend movie is in Display
static bool s_extendMask1;        // set while a kExtendMask1 movie is in Display
static bool s_rootMaskExtended;   // true only while the active depth-1 mask itself was extended by a P_MASK rule
static bool s_mirrorNow;          // set while the pause menu is in Display
static int s_maskPhase;           // 0 none, 1 submitting mask shapes, 2 drawing masked content
static int s_maskSubmitMode;      // BeginSubmitMask mode, meaningful during mask submission
static int s_maskDepth;           // nested stencil mask depth (mirrors GRenderer+0x170)
static float s_stageL, s_stageR, s_stageH, s_visL, s_visR, s_visT, s_visB;  // renderer units

// Shapes that the automatic rules get wrong, by their bounds in 16:9 stage px at 1440p (x 0..2560 = the centred
// stage, y 0..1440; the same on every screen size) within 8 px. kAny matches any value (animated edges).
// Phase: masks and masked content are left as authored unless a rule names them (or kExtendMask1 depth-1 content).
// First match wins.
enum { P_ANY = -1, P_PLAIN, P_MASK, P_MASKED };
enum { R_RIGHT, R_BOTH, R_PIN, R_PLACE };  // PIN keeps the width at the nearer screen edge; PLACE: px from the right
static const float kAny = 1e9f;
struct Rule { const char* movie; float x0, y0, x1, y1; int phase, act; float a, b; };
static const Rule kRules[] = {
    // results: the glows sit at the screen's right edge at twice their width; the title band and the table
    // backing (mask and content) reach the right edge, also while the table settles (its edge wobbles to ~2550)
    { "RaceEnd.gfx", 2256, 363, 2569, 1340, P_ANY, R_PLACE, -620, 0 },
    { "RaceEnd.gfx", 607, 866, 2575, 1196, P_MASK, R_RIGHT },  // Season: points ticker box
    { "RaceEnd.gfx", 607, 865, 2575, 1196, P_MASKED, R_RIGHT },
    { "RaceResults.gfx", 961, 405, 2564, 574, P_MASK, R_RIGHT },
    { "RaceResults.gfx", 962, 404, 2558, 575, P_MASKED, R_RIGHT },
    { "RaceResults.gfx", 961, kAny, 2565, kAny, P_MASK, R_RIGHT },
    { "RaceResults.gfx", 964, kAny, 2567, kAny, P_MASKED, R_RIGHT },
    { "RaceResults.gfx", 961, kAny, 2549, kAny, P_MASK, R_RIGHT },
    { "RaceResults.gfx", 964, kAny, 2551, kAny, P_MASKED, R_RIGHT },
    { "RaceResults.gfx", 2241, 62, 2555, 1039, P_ANY, R_PLACE, -620, 0 },
    // pre-race RACE card: the title band reaches the right edge like the text panel; full-width bands and
    // full-stage flashes stay full width (listed before the catch-all), the animated glows keep their width
    { "IG_GameModeInstructions.gfx", 265, 671, 2665, 840, P_MASK, R_RIGHT },
    { "IG_GameModeInstructions.gfx", 267, 668, 2668, 780, P_MASKED, R_RIGHT },
    { "IG_GameModeInstructions.gfx", 0, 1259, 2562, 1443, P_ANY, R_BOTH },
    { "IG_GameModeInstructions.gfx", 0, 2, 2562, 186, P_ANY, R_BOTH },
    { "IG_GameModeInstructions.gfx", 266, kAny, 2566, kAny, P_ANY, R_RIGHT },
    { "IG_GameModeInstructions.gfx", -4, kAny, 2564, kAny, P_ANY, R_BOTH },
    { "IG_GameModeInstructions.gfx", kAny, kAny, 2560, kAny, P_ANY, R_PIN },
    // pre-race track card: the same, the card sits at different heights
    { "EventObjectives.gfx", 0, 32, 2563, 433, P_ANY, R_BOTH },
    { "EventObjectives.gfx", 1, 1, 2562, 186, P_ANY, R_BOTH },
    { "EventObjectives.gfx", 1, 1258, 2562, 1443, P_ANY, R_BOTH },
    { "EventObjectives.gfx", 970, kAny, 2566, kAny, P_ANY, R_RIGHT },
    { "EventObjectives.gfx", -4, kAny, 2564, kAny, P_ANY, R_BOTH },
    { "EventObjectives.gfx", kAny, kAny, 2558, kAny, P_ANY, R_PIN },
    // car select: the header strip (mask and two full-stage masked strips) spans the screen like at 16:9
    { "FE_vehicleSelect.gfx", -1496, 36, 2628, 348, P_MASK, R_BOTH },
    { "FE_vehicleSelect.gfx", -1, 134, 2560, 239, P_MASKED, R_BOTH },
    { "FE_vehicleSelect.gfx", -1, 242, 2560, 299, P_MASKED, R_BOTH },
    // season unlocks & rewards: root reveal strip clip mask spans the screen; its depth-1 masked shapes auto-extend
    // while deeper nested masks (track/mode thumbnails and decal icons) stay as authored
    { "CarUnlock_*", -128, 412, 2641, 1022, P_MASK, R_BOTH },
    { "DecalUnlock.gfx", -6, 416, 2566, 1026, P_MASK, R_BOTH },
    { "Rewards.gfx", 0, 406, 2571, 1016, P_MASK, R_BOTH },
};

static bool Near(float v, float rule) { return rule == kAny || fabsf(v - rule) <= 8; }

static const Rule* FindRule(float x0, float y0, float x1, float y1)
{
    for (const Rule& r : kRules)
        if (MatchPattern(r.movie, strlen(r.movie), s_extendName) && (r.phase == P_ANY || r.phase == s_maskPhase) &&
            Near(x0, r.x0) && Near(y0, r.y0) && Near(x1, r.x1) && Near(y1, r.y1))
            return &r;
    return NULL;
}

static void __fastcall Hook_RSetMatrix(void* r, void*, const float* m)
{
    memcpy(s_rMat, m, sizeof s_rMat);
    s_rSetMatrix(r, m);
}

static void __fastcall Hook_RSetVerts(void* r, void*, const void* v, int n, int fmt, void* cache)
{
    s_rVerts = (const BYTE*)v;
    s_rVertCount = n;
    s_rVertFmt = fmt;
    s_rSetVerts(r, v, n, fmt, cache);
}

// x/y bounds of vertices [first, first+n) under the current matrix
// formats: 1 XY16i (4 bytes), 2 XY32f (8), 3 XY16iC32 (8), 4 XY16iCF32 (12)
static bool VertBounds(int first, int n, float& minx, float& maxx, float& miny, float& maxy)
{
    static const int kStride[] = { 0, 4, 8, 8, 12 };
    if (!s_rVerts || s_rVertFmt < 1 || s_rVertFmt > 4 || n <= 0) return false;
    minx = miny = 1e30f;
    maxx = maxy = -1e30f;
    for (int i = first, e = first + n; i < e && i < s_rVertCount; i++) {
        const BYTE* v = s_rVerts + i * kStride[s_rVertFmt];
        float x, y;
        if (s_rVertFmt == 2) { x = ((const float*)v)[0]; y = ((const float*)v)[1]; }
        else { x = ((const short*)v)[0]; y = ((const short*)v)[1]; }
        float wx = s_rMat[0] * x + s_rMat[1] * y + s_rMat[2], wy = s_rMat[3] * x + s_rMat[4] * y + s_rMat[5];
        if (wx < minx) minx = wx;
        if (wx > maxx) maxx = wx;
        if (wy < miny) miny = wy;
        if (wy > maxy) maxy = wy;
    }
    return maxx >= minx;
}

// The pause menu's 16:9 clip is the D3D viewport, so a copy left of it would be cut: for the copy, the viewport is
// widened to the left by e px and the x mapping compensated so everything lands on the same pixels.
static bool BeginEdgeCopy(float* m, D3DVIEWPORT9& saved, DWORD& sc)
{
    float sw = s_stageR - s_stageL;
    if (sw <= 0 || !s_dev || s_edgeVpW <= 0 || FAILED(s_dev->GetViewport(&saved))) return false;
    s_dev->GetRenderState(D3DRS_SCISSORTESTENABLE, &sc);
    s_dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
    float k = s_edgeVpW / sw;
    DWORD want = (DWORD)(fabsf(kEdgeOffset) * k + 32.0f);
    if (want < 128) want = 128;
    DWORD e = saved.X < want ? saved.X : want;
    if (!e) return true;
    float X = (float)saved.X, W = (float)saved.Width, Xn = X - e, Wn = W + e, r = W / Wn;
    float c = s_stageL - s_stageL * r + (s_edgeVpL * r + X - Xn * r - s_edgeVpL) / k;
    m[0] *= r;
    m[1] *= r;
    m[2] = m[2] * r + c;
    D3DVIEWPORT9 v = saved;
    v.X = (DWORD)Xn;
    v.Width = (DWORD)Wn;
    s_dev->SetViewport(&v);
    return true;
}

static void EndEdgeCopy(bool ok, const D3DVIEWPORT9& saved, DWORD sc)
{
    if (!ok) return;
    s_dev->SetViewport(&saved);
    s_dev->SetRenderState(D3DRS_SCISSORTESTENABLE, sc);
}

// pause menu: a tall thin shape right of the stage centre is its frame line; draw it again at the left edge
static void MirrorLine(float minx, float maxx, float miny, float maxy, void (*draw)(void*), void* arg, void* r)
{
    float sw = s_stageR - s_stageL;
    if (!(maxx - minx < sw * 0.01f && maxy - miny > s_stageH * 0.5f && minx > s_stageL + sw * 0.05f)) return;
    float m[6] = { s_rMat[0], s_rMat[1], s_rMat[2] + (s_stageL + kEdgeOffset - minx), s_rMat[3], s_rMat[4], s_rMat[5] };
    D3DVIEWPORT9 vp;
    DWORD sc = FALSE;
    bool ok = BeginEdgeCopy(m, vp, sc);
    s_rSetMatrix(r, m);
    draw(arg);
    s_rSetMatrix(r, s_rMat);
    EndEdgeCopy(ok, vp, sc);
}

struct TriCall { void* r; int base, minIdx, count, start, tris; };
static void DrawTri(void* a)
{
    TriCall* t = (TriCall*)a;
    s_rDrawTri(t->r, t->base, t->minIdx, t->count, t->start, t->tris);
}

static void __fastcall Hook_RDrawTri(void* r, void*, int base, int minIdx, int count, int start, int tris)
{
    float minx, maxx, miny, maxy;
    if ((!s_extendName && !s_mirrorNow) || (s_mirrorNow && s_maskPhase) ||
        !VertBounds(base + minIdx, count, minx, maxx, miny, maxy)) {
        s_rDrawTri(r, base, minIdx, count, start, tris);
        return;
    }
    if (s_mirrorNow) {
        TriCall t = { r, base, minIdx, count, start, tris };
        DrawTri(&t);
        MirrorLine(minx, maxx, miny, maxy, DrawTri, &t, r);
        return;
    }
    // 16:9 stage px at 1440p
    float kx = s_scrW / (s_visR - s_visL), ky = s_scrH / (s_visB - s_visT > 1 ? s_visB - s_visT : 1);
    float s = 1440.0f / (s_scrH > 0 ? s_scrH : 1440);
    const Rule* rule = FindRule((minx - s_stageL) * kx * s, (miny - s_visT) * ky * s, (maxx - s_stageL) * kx * s,
                                (maxy - s_visT) * ky * s);
    if (!rule && s_maskPhase && !(s_maskPhase == 2 && s_maskDepth == 1 && s_extendMask1 && s_rootMaskExtended)) {
        // masks and masked content stay as authored (except depth-1 content under an extended root strip mask)
        s_rDrawTri(r, base, minIdx, count, start, tris);
        return;
    }
    // Mode 2 can already report depth 1 while submitting the geometry that removes a child mask.
    if (s_maskPhase == P_MASK && s_maskSubmitMode == 0 && rule && rule->phase == P_MASK && rule->act == R_BOTH)
        s_rootMaskExtended = true;
    // stage edges map to the screen edges: shapes reaching past a stage edge keep their overshoot beyond the
    // screen edge, shapes wholly outside the stage move with it, thin lines and square sprites (glows) stay
    float sw = s_stageR - s_stageL, tol = sw * 0.01f, mL = s_stageL - s_visL, mR = s_visR - s_stageR;
    bool left = minx <= s_stageL + tol, right = maxx >= s_stageR - tol;
    bool thin = maxx - minx < sw * 0.04f;
    float aspect = (maxx - minx) / (maxy - miny > 1 ? maxy - miny : 1);
    bool sprite = aspect > 0.8f && aspect < 1.25f;
    float nl = minx, nr = maxx;
    if (rule) {
        float pxu = (s_visR - s_visL) / (float)(s_scrW > 0 ? s_scrW : 1) * (s_scrH > 0 ? s_scrH / 1440.0f : 1);
        switch (rule->act) {
        case R_RIGHT: nr = s_visR; break;
        case R_BOTH: nl -= mL; nr += mR; break;
        case R_PIN:
            if (minx + maxx > s_stageL + s_stageR) { nr = maxx + mR; nl = nr - (maxx - minx); }
            else { nl = minx - mL; nr = nl + (maxx - minx); }
            break;
        case R_PLACE: nl = s_visR + rule->a * pxu; nr = s_visR + rule->b * pxu; break;
        }
    } else if (thin || (!left && !right) || maxx <= minx) {
        // kept
    } else if (minx >= s_stageR - tol) {
        nl += mR; nr += mR;
    } else if (maxx <= s_stageL + tol) {
        nl -= mL; nr -= mL;
    } else if (sprite) {
        // kept
    } else {
        if (left) nl -= mL;
        if (right) nr += mR;
    }
    if (nl == minx && nr == maxx) {
        s_rDrawTri(r, base, minIdx, count, start, tris);
        return;
    }
    // A shape's anti-aliased fringe can leave a 1 px line, so snap nearby edges 2 px past the screen.
    // The top/bottom tolerance is deliberately 5 px at 1440p for all kExtend movies' shapes that reach
    // a screen side. It scales with screen height and catches bars and fills that sit 4 px inside the stage.
    float ny0 = 0, sy = 1;
    if (s_scrW > 0 && s_scrH > 0) {
        float ux = (s_visR - s_visL) / s_scrW, uy = (s_visB - s_visT) / s_scrH;
        float u1440 = (s_visB - s_visT) / 1440.0f;
        float xTol = u1440 > ux ? u1440 : ux;
        float yTol = 5.0f * (u1440 > uy ? u1440 : uy);
        if (nl != minx && nl > s_visL - 2 * ux && nl <= s_visL + xTol) nl = s_visL - 2 * ux;
        if (nr != maxx && nr < s_visR + 2 * ux && nr >= s_visR - xTol) nr = s_visR + 2 * ux;
        float nt = miny, nb = maxy;
        if (miny > s_visT - 2 * uy && miny <= s_visT + yTol) nt = s_visT - 2 * uy;
        if (maxy < s_visB + 2 * uy && maxy >= s_visB - yTol) nb = s_visB + 2 * uy;
        // opt-in full-height solid black backdrop on CarUnlock_*, DecalUnlock.gfx and Rewards.gfx:
        // extend their own top/bottom black letterbox bars vertically to meet the centre reveal strip
        if (g_cfg.ultrawideBlackBackdrop && s_extendMask1 && s_maskPhase == 0 && nl <= s_visL && nr >= s_visR) {
            float y0p = (miny - s_visT) * ky * s, y1p = (maxy - s_visT) * ky * s;
            float meetT = 414.0f, meetB = 1020.0f;  // CarUnlock_* (strip 412..1022)
            if (!_stricmp(s_extendName, "DecalUnlock.gfx")) { meetT = 418.0f; meetB = 1024.0f; }
            else if (!_stricmp(s_extendName, "Rewards.gfx")) { meetT = 408.0f; meetB = 1014.0f; }
            if (y0p <= 8.0f && y1p >= 70.0f && y1p <= 220.0f) nb = s_visT + (s_visB - s_visT) * (meetT / 1440.0f);
            if (y0p >= 1220.0f && y0p <= 1370.0f && y1p >= 1432.0f) nt = s_visT + (s_visB - s_visT) * (meetB / 1440.0f);
        }
        if ((nl <= s_visL || nr >= s_visR) && (nt != miny || nb != maxy) && maxy > miny) {
            sy = (nb - nt) / (maxy - miny);
            ny0 = nt - sy * miny;
        }
    }
    float b = maxx > minx ? (nr - nl) / (maxx - minx) : 1, a = nl - b * minx;
    float m[6] = { s_rMat[0] * b, s_rMat[1] * b, s_rMat[2] * b + a, s_rMat[3] * sy, s_rMat[4] * sy, s_rMat[5] * sy + ny0 };
    s_rSetMatrix(r, m);
    s_rDrawTri(r, base, minIdx, count, start, tris);
    s_rSetMatrix(r, s_rMat);
}

struct LineCall { void* r; int base, lines; };
static void DrawLines(void* a)
{
    LineCall* l = (LineCall*)a;
    s_rDrawLine(l->r, l->base, l->lines);
}

static void __fastcall Hook_RDrawLine(void* r, void*, int base, int lines)
{
    s_rDrawLine(r, base, lines);
    float minx, maxx, miny, maxy;
    if (!s_mirrorNow || !VertBounds(base, lines + 1, minx, maxx, miny, maxy)) return;
    if (minx <= s_stageL + (s_stageR - s_stageL) * 0.5f) return;
    LineCall l = { r, base, lines };
    MirrorLine(minx, maxx, miny, maxy, DrawLines, &l, r);
}

static void __fastcall Hook_RBeginMask(void* r, void*, int a)
{
    if (a == 0) { s_maskDepth = 1; s_rootMaskExtended = false; }
    else if (a == 1) s_maskDepth++;
    else if (a == 2 && s_maskDepth > 0) s_maskDepth--;
    s_maskSubmitMode = a;
    s_maskPhase = 1;
    s_rBeginMask(r, a);
}
static void __fastcall Hook_REndMask(void* r, void*) { s_maskPhase = s_maskDepth > 0 ? 2 : 0; s_rEndMask(r); }
static void __fastcall Hook_RDisableMask(void* r, void*) { s_maskDepth = 0; s_maskPhase = 0; s_rootMaskExtended = false; s_rDisableMask(r); }

static void __fastcall Hook_Display(void* self, void*)
{
    if (s_scrH <= 0 || s_scrW * 9 <= s_scrH * 16 + 9) { s_origDisplay(self); return; }
    MovieInfo* m = Movie(self);
    // visible frame rect (L, T, R, B) at +0x88; the stage is 16:9 and fits the visible height
    const float* vis = (const float*)((BYTE*)self + 0x88);
    float cx = (vis[0] + vis[2]) / 2, sw = (vis[3] - vis[1]) * 16.0f / 9.0f;
    if (self == s_edgeMovie && s_rDrawTri) {
        // pause menu: its frame line at the panel's right edge is drawn again at the stage's left edge, in the
        // movie's own pass, so it fades, appears and hides exactly like the original
        s_stageL = cx - sw / 2; s_stageR = cx + sw / 2; s_stageH = vis[3] - vis[1];
        s_maskDepth = 0; s_maskPhase = 0;
        s_mirrorNow = true;
        s_origDisplay(self);
        s_mirrorNow = false;
        return;
    }
    if (s_rDrawTri && (m->flags & M_EXTEND) && vis[2] - vis[0] > sw * 1.01f) {
        s_visL = vis[0]; s_visR = vis[2]; s_visT = vis[1]; s_visB = vis[3];
        s_stageL = cx - sw / 2; s_stageR = cx + sw / 2;
        s_maskDepth = 0; s_maskPhase = 0; s_rootMaskExtended = false;
        s_extendName = m->name;
        s_extendMask1 = (m->flags & M_EXTEND_MASK1) != 0;
        s_origDisplay(self);
        s_extendName = NULL;
        s_extendMask1 = false;
        s_rootMaskExtended = false;
        return;
    }
    if ((m->flags & M_DIALPOWER) && LocalPlayers() >= 2) {
        // split screen boost bar: drawn stock (full texture width, as the quad's UVs expect), the quad narrowed
        float aspect = *(float*)kAspectVA;
        if (aspect > 16.0f / 9.0f + 0.001f) NarrowDials(1, (16.0f / 9.0f) / aspect);
        s_origDisplay(self);
        return;
    }
    if (!IsSplit(self)) { s_origDisplay(self); return; }
    // split screen HUD: drawn twice with a 16:9 viewport, the stage's left half centred in the left screen half,
    // then its right half in the right one, each clipped to that player's half of the stage
    GViewport full = *(GViewport*)((BYTE*)self + 0x38);
    int vw = (full.h * 16 + 8) / 9, half = full.w / 2;
    for (int p = 0; p < 2; p++) {
        GViewport v = full;
        v.w = vw;
        v.left = full.left + (p ? half + half / 2 - vw * 3 / 4 : half / 2 - vw / 4);
        v.scL = v.left + p * (vw / 2); v.scT = full.top; v.scW = vw / 2; v.scH = full.h;
        v.flags |= VP_SCISSOR;
        s_origSetViewport(self, &v);
        s_origDisplay(self);
    }
    s_origSetViewport(self, &full);
}

bool SwapSlot(void** slot, void* hook, void** orig)
{
    DWORD old;
    if (!VirtualProtect(slot, 4, PAGE_READWRITE, &old)) return false;
    *orig = *slot;
    *slot = hook;
    VirtualProtect(slot, 4, old, &old);
    return true;
}

bool SwapSlotChecked(void** slot, void* expected, void* hook, void** orig)
{
    DWORD old;
    if (!VirtualProtect(slot, 4, PAGE_READWRITE, &old)) return false;
    *orig = expected;
    bool done = InterlockedCompareExchangePointer(slot, hook, expected) == expected;
    if (!done) *orig = NULL;
    VirtualProtect(slot, 4, old, &old);
    return done;
}

const void* UltrawideSetViewportHook()
{
    return s_origSetViewport ? (const void*)Hook_SetViewport : NULL;
}

static void InstallUiHooks()
{
    if (s_origSetViewport) return;
    if (!Readable(kGfxSetViewportSlot, 4 * 12) || (DWORD)kGfxSetViewportSlot[0] != 0x0098CDF0 ||
        (DWORD)kGfxSetViewportSlot[1] != 0x009823F0 || (DWORD)*kGfxDisplaySlot != 0x00987E60 ||
        memcmp((void*)0x0098CDF0, "\x83\xEC\x24\x53\x8B\xD9\x8B\x4B\x48", 9) ||
        memcmp((void*)0x009823F0, "\x56\x57\x8B\x7C\x24\x0C\x8D\x71\x38\xB9\x0D\x00\x00\x00", 14)) {
        Log("ultrawide: Scaleform movie class not recognised, UI left as is");
        return;
    }
    SwapSlot(kGfxSetViewportSlot, (void*)Hook_SetViewport, (void**)&s_origSetViewport);
    SwapSlot(kGfxDisplaySlot, (void*)Hook_Display, (void**)&s_origDisplay);
    if (!Readable(kRendVt, 29 * 4) || (DWORD)kRendVt[7] != 0x00539620 || (DWORD)kRendVt[14] != 0x00534D00 ||
        (DWORD)kRendVt[17] != 0x00536570 || (DWORD)kRendVt[18] != 0x00536800 || (DWORD)kRendVt[26] != 0x00534630 ||
        (DWORD)kRendVt[27] != 0x00534890 || (DWORD)kRendVt[28] != 0x00534920 ||
        memcmp((void*)0x00539620, "\x8B\x44\x24\x04", 4)) {
        Log("ultrawide: Scaleform renderer not recognised, UI centred without edge extension");
        return;
    }
    SwapSlot(&kRendVt[7], (void*)Hook_RSetMatrix, (void**)&s_rSetMatrix);
    SwapSlot(&kRendVt[14], (void*)Hook_RSetVerts, (void**)&s_rSetVerts);
    SwapSlot(&kRendVt[18], (void*)Hook_RDrawLine, (void**)&s_rDrawLine);
    SwapSlot(&kRendVt[26], (void*)Hook_RBeginMask, (void**)&s_rBeginMask);
    SwapSlot(&kRendVt[27], (void*)Hook_REndMask, (void**)&s_rEndMask);
    SwapSlot(&kRendVt[28], (void*)Hook_RDisableMask, (void**)&s_rDisableMask);
    SwapSlot(&kRendVt[17], (void*)Hook_RDrawTri, (void**)&s_rDrawTri);  // last: it uses the others
    Log("ultrawide: Scaleform viewports centred at 16:9, edge extension on");
}

// ---------------------------------------------------------------- engine values

// tweakable "LetterBox/LetterBox Amount" (0.118): the share of the screen height blacked out at top and bottom
// in pause, race intro and cinematics. Off only above 16:9 so 16:9 and narrower screens keep the stock bars.
static void SetLetterbox(bool off)
{
    static const DWORD kStockLb = 0x3DF1A9FC;  // 0.118f
    static int s_ok = -1;
    float* amount = (float*)0x00D36F54;
    if (s_ok < 0) {
        s_ok = Readable(amount, 4) && *(DWORD*)amount == kStockLb &&
               !memcmp((void*)0x00B7D7D2, "\x68\x54\x6F\xD3\x00\x68\x78\x21\xC1\x00", 10) ? 1 : 0;
        if (!s_ok) Log("ultrawide: letterbox tweakable not recognised, left as is");
    }
    if (s_ok) *amount = off ? 0.0f : *(const float*)&kStockLb;
}

// 3D player tags (split screen "PLAYER 2" above the other car): sized as tweakable / viewport width (Name
// Width, Icon Width, Icon Offset X at 0xD1DB68/78/88), tuned for 16:9. Scaled by 16:9 / aspect.
static void ScaleTags(float factor)
{
    static const struct { DWORD va, stock; } kTag[] = {
        { 0x00D1DB68, 0x3F0A3D71 }, { 0x00D1DB78, 0x3DCCCCCD }, { 0x00D1DB88, 0xBC814793 },
    };
    static int s_ok = -1;
    if (s_ok < 0) {
        s_ok = 1;
        for (auto& t : kTag) if (!Readable((void*)t.va, 4) || *(DWORD*)t.va != t.stock) s_ok = 0;
        if (!s_ok) Log("ultrawide: player tag widths not recognised, left as is");
    }
    if (s_ok)
        for (auto& t : kTag) *(float*)t.va = *(const float*)&t.stock * factor;
}

// ---------------------------------------------------------------- Bink cinematics at their own aspect
// The game draws a movie as one quad, x +-1 and y +-k in clip space with k = screen aspect * 9/16: fit to the
// width, so on wider screens its top and bottom are cut. While a cinematic plays (BinkOpen..BinkClose imports,
// any hook already there stays in the chain), that 80-byte quad is rewritten at Unlock to fit the height at
// the movie's own aspect; Present keeps the sides black. Movies under Screens\ are menu textures, left alone.

typedef void*(__stdcall* BinkOpen_t)(const char*, DWORD);
typedef void(__stdcall* BinkClose_t)(void*);
typedef int(__stdcall* BinkDoFrame_t)(void*);
typedef HRESULT(STDMETHODCALLTYPE* VbLock_t)(IDirect3DVertexBuffer9*, UINT, UINT, void**, DWORD);
typedef HRESULT(STDMETHODCALLTYPE* VbUnlock_t)(IDirect3DVertexBuffer9*);
static BinkOpen_t s_binkOpen;
static BinkClose_t s_binkClose;
static BinkDoFrame_t s_binkDoFrame;
static volatile LONG s_binkFrame = -100;      // frame of the last decoded movie frame
static volatile LONG s_binkQuadFrame = -100;  // frame in which the movie quad was refitted
static void* volatile s_binkMovie;
static volatile float s_binkAspect;
static struct { void** vt; VbLock_t lock; VbUnlock_t unlock; } s_vbVt[8];  // per vertex buffer class
static int s_vbVtCount;
static IDirect3DVertexBuffer9* s_lockVb;
static float* s_lockPtr;

static void* __stdcall Hook_BinkOpen(const char* name, DWORD flags)
{
    void* h = s_binkOpen(name, flags);
    if (h && name && Readable(h, 8)) {
        char low[MAX_PATH];
        lstrcpynA(low, name, MAX_PATH);
        CharLowerA(low);
        UINT w = ((UINT*)h)[0], hh = ((UINT*)h)[1];
        if (!strstr(low, "\\screens\\") && w && hh) {
            s_binkAspect = (float)w / hh;
            s_binkMovie = h;
        }
    }
    return h;
}

static int __stdcall Hook_BinkDoFrame(void* h)
{
    if (h && h == s_binkMovie) s_binkFrame = s_frame;
    return s_binkDoFrame(h);
}

static void __stdcall Hook_BinkClose(void* h)
{
    if (h == s_binkMovie) s_binkMovie = NULL;
    s_binkClose(h);
}

// the intro skip (game.cpp) hooks the same imports, after this module or before it
bool UltrawideBinkHook(const void* fn)
{
    return fn == (void*)Hook_BinkOpen || fn == (void*)Hook_BinkDoFrame || fn == (void*)Hook_BinkClose;
}

static int VbVtIndex(IDirect3DVertexBuffer9* vb)
{
    void** vt = *(void***)vb;
    for (int i = 0; i < s_vbVtCount; i++)
        if (s_vbVt[i].vt == vt) return i;
    return -1;
}

static HRESULT STDMETHODCALLTYPE Hook_VbLock(IDirect3DVertexBuffer9* vb, UINT off, UINT size, void** data, DWORD flags)
{
    int i = VbVtIndex(vb);
    if (i < 0) return D3DERR_INVALIDCALL;  // only patched classes point here
    HRESULT hr = s_vbVt[i].lock(vb, off, size, data, flags);
    s_lockVb = NULL;
    if (SUCCEEDED(hr) && data && *data && size == 80 && s_binkMovie && s_frame - s_binkFrame <= 6) {
        s_lockVb = vb;
        s_lockPtr = (float*)*data;
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_VbUnlock(IDirect3DVertexBuffer9* vb)
{
    if (vb == s_lockVb && s_lockPtr) {
        s_lockVb = NULL;
        float sa = s_scrH > 0 ? (float)s_scrW / s_scrH : 0, k = sa * 9.0f / 16.0f, ma = s_binkAspect;
        if (k > 1.01f && ma > 0.1f && ma < sa) {
            float* v = s_lockPtr;  // 4 vertices, stride 5 floats
            bool quad = true;
            int xs = 0, ys = 0;
            for (int n = 0; n < 4 && quad; n++) {
                float x = v[n * 5], y = v[n * 5 + 1];
                quad = fabsf(fabsf(x) - 1.0f) < 1e-3f && fabsf(fabsf(y) - k) < 1e-3f * k;
                xs |= x > 0 ? 1 : 2;
                ys |= y > 0 ? 1 : 2;
            }
            if (quad && xs == 3 && ys == 3) {
                for (int n = 0; n < 4; n++) { v[n * 5] *= ma / sa; v[n * 5 + 1] /= k; }
                s_binkQuadFrame = s_frame;
            }
        }
    }
    int i = VbVtIndex(vb);
    return i < 0 ? D3DERR_INVALIDCALL : s_vbVt[i].unlock(vb);
}

static void* HookSlot(void* obj, int slot, void* fn)
{
    void** vt = *(void***)obj;
    if (vt[slot] == fn) return NULL;
    void* prev = NULL;
    return SwapSlot(&vt[slot], fn, &prev) ? prev : NULL;
}

static void PatchVb(IDirect3DVertexBuffer9* vb)
{
    if (!vb || VbVtIndex(vb) >= 0 || s_vbVtCount >= 8) return;
    void** vt = *(void***)vb;
    s_vbVt[s_vbVtCount].vt = vt;
    s_vbVt[s_vbVtCount].lock = (VbLock_t)vt[11];
    s_vbVt[s_vbVtCount].unlock = (VbUnlock_t)vt[12];
    s_vbVtCount++;  // registered before patching, so a concurrent call finds its original
    HookSlot(vb, 11, (void*)Hook_VbLock);
    HookSlot(vb, 12, (void*)Hook_VbUnlock);
}

typedef HRESULT(STDMETHODCALLTYPE* CreateVb_t)(IDirect3DDevice9*, UINT, DWORD, DWORD, D3DPOOL, IDirect3DVertexBuffer9**, HANDLE*);
static CreateVb_t s_origCreateVb;
static HRESULT STDMETHODCALLTYPE Hook_CreateVb(IDirect3DDevice9* dev, UINT len, DWORD usage, DWORD fvf, D3DPOOL pool,
                                               IDirect3DVertexBuffer9** out, HANDLE* sh)
{
    HRESULT hr = s_origCreateVb(dev, len, usage, fvf, pool, out, sh);
    if (SUCCEEDED(hr) && out && *out) PatchVb(*out);
    return hr;
}

static void InstallBinkHooks(IDirect3DDevice9* dev)
{
    void** open = (void**)0x00B9B39C;
    void** close = (void**)0x00B9B388;
    void** doFrame = (void**)0x00B9B380;
    HMODULE bink = GetModuleHandleA("binkw32.dll");
    // each import holds Bink's function, or the intro skip's hook (game.cpp), which calls on to it: the
    // hook here then goes in front of that one
    if (!bink || !Readable(doFrame, 0x20) || GetProcAddress(bink, "_BinkClose@4") != *close ||
        (GetProcAddress(bink, "_BinkOpen@8") != *open && !IntroSkipBinkHook(*open)) ||
        (GetProcAddress(bink, "_BinkDoFrame@4") != *doFrame && !IntroSkipBinkHook(*doFrame))) {
        Log("ultrawide: Bink imports not recognised, cinematics left as is");
        return;
    }
    SwapSlot(open, (void*)Hook_BinkOpen, (void**)&s_binkOpen);
    SwapSlot(close, (void*)Hook_BinkClose, (void**)&s_binkClose);
    SwapSlot(doFrame, (void*)Hook_BinkDoFrame, (void**)&s_binkDoFrame);
    s_origCreateVb = (CreateVb_t)HookSlot(dev, 26, (void*)Hook_CreateVb);
}

// ---------------------------------------------------------------- device hooks

// Present and Reset come from diag.cpp's hooks (one hook per slot for the whole fix)
static IDirect3DSurface9 *s_edgeCol, *s_binkTmp;  // default pool: released before Reset

static void ClearSides(IDirect3DDevice9* dev, IDirect3DSurface9* bb, LONG l, LONG r, LONG w, LONG h)
{
    if (l > 0) { RECT a = { 0, 0, l, h }; dev->ColorFill(bb, &a, D3DCOLOR_XRGB(0, 0, 0)); }
    if (r < w) { RECT b = { r, 0, w, h }; dev->ColorFill(bb, &b, D3DCOLOR_XRGB(0, 0, 0)); }
}

// before the device's Present
void UltrawidePresent(IDirect3DDevice9* dev)
{
    if (s_state <= 0 || dev != s_dev) return;
    LONG f = InterlockedIncrement(&s_frame);
    IDirect3DSurface9* bb = NULL;
    if (s_state > 0 && SUCCEEDED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb))) {
        D3DSURFACE_DESC sd;
        bb->GetDesc(&sd);
        LONG W = (LONG)sd.Width, H = (LONG)sd.Height;
        if (W * 9 > H * 16 + 9 && f - s_pillarFrame <= 2 && s_pillarWidth > 0) {
            // UI viewports may refer to the 2x supersampling target (Very High); mapped to the back buffer
            float sx = s_uiBufW > 0 ? (float)W / s_uiBufW : 1.0f;
            ClearSides(dev, bb, (LONG)(s_pillarLeft * sx + 0.5f), (LONG)((s_pillarLeft + s_pillarWidth) * sx + 0.5f), W, H);
        }
        if (s_binkMovie && f - s_binkFrame <= 6) {
            LONG bw = (LONG)(H * s_binkAspect + 0.5f), l = (W - bw) / 2;
            if (f - s_binkQuadFrame <= 2) {
                ClearSides(dev, bb, l, l + bw, W, H);  // quad already refitted
            } else if (bw + 8 < W) {
                // not refitted (another path drew it): squeeze the frame to the movie's aspect
                static LONG tw, th;
                static D3DFORMAT tf;
                if (!s_binkTmp || tw != bw || th != H || tf != sd.Format) {
                    if (s_binkTmp) s_binkTmp->Release();
                    s_binkTmp = NULL;
                    tw = bw; th = H; tf = sd.Format;
                    dev->CreateRenderTarget(bw, H, sd.Format, D3DMULTISAMPLE_NONE, 0, FALSE, &s_binkTmp, NULL);
                }
                RECT to = { l, 0, l + bw, H };
                if (s_binkTmp && SUCCEEDED(dev->StretchRect(bb, NULL, s_binkTmp, NULL, D3DTEXF_LINEAR))) {
                    dev->ColorFill(bb, NULL, D3DCOLOR_XRGB(0, 0, 0));
                    dev->StretchRect(s_binkTmp, NULL, bb, &to, D3DTEXF_POINT);
                }
            }
        }
        if (W * 9 > H * 16 + 9) {
            // above 16:9 the game's final pass leaves the last pixel column wrong (also without this fix):
            // replaced with its neighbour
            static LONG colH;
            static D3DFORMAT colF;
            if (!s_edgeCol || colH != H || colF != sd.Format) {
                if (s_edgeCol) s_edgeCol->Release();
                s_edgeCol = NULL;
                colH = H; colF = sd.Format;
                dev->CreateRenderTarget(1, H, sd.Format, D3DMULTISAMPLE_NONE, 0, FALSE, &s_edgeCol, NULL);
            }
            RECT from = { W - 2, 0, W - 1, H }, to = { W - 1, 0, W, H };
            if (s_edgeCol && SUCCEEDED(dev->StretchRect(bb, &from, s_edgeCol, NULL, D3DTEXF_POINT)))
                dev->StretchRect(s_edgeCol, NULL, bb, &to, D3DTEXF_POINT);
        }
        bb->Release();
    }
}

static void Apply(UINT w, UINT h, HWND wnd)
{
    if ((!w || !h) && wnd) {  // windowed device with a 0x0 back buffer: the client area
        RECT r;
        if (GetClientRect(wnd, &r)) { w = r.right - r.left; h = r.bottom - r.top; }
    }
    if (!w || !h) return;
    s_scrW = (LONG)w;
    s_scrH = (LONG)h;
    const float stock = 16.0f / 9.0f;
    float aspect = (float)w / h, target = aspect > stock + 0.001f ? aspect : stock;
    WriteAspect(target);
    ScaleTags(stock / target);
    SetLetterbox(target > stock);
    Log("ultrawide: %ux%u, aspect %.4f%s", w, h, aspect, target > stock ? ", Hor+ with the UI at 16:9" : " (stock)");
}

static void DropSurfaces()
{
    if (s_binkTmp) { s_binkTmp->Release(); s_binkTmp = NULL; }
    if (s_edgeCol) { s_edgeCol->Release(); s_edgeCol = NULL; }
}

// before Reset: D3D9 Reset fails while default pool surfaces exist
void UltrawideDeviceLost()
{
    DropSurfaces();
}

// after a successful Reset
void UltrawideDeviceReset(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp)
{
    if (s_state <= 0 || dev != s_dev || !pp) return;
    D3DDEVICE_CREATION_PARAMETERS cp = {};
    dev->GetCreationParameters(&cp);
    Apply(pp->BackBufferWidth, pp->BackBufferHeight, pp->hDeviceWindow ? pp->hDeviceWindow : cp.hFocusWindow);
}

void UltrawideDeviceCreated(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp, HWND focus)
{
    if (s_state == 0) {
        s_state = CheckExe() ? 1 : -1;
        if (s_state < 0) {
            // the race cameras' fix instead (camera.cpp), unless the standalone module does it all
            g_cfg.ultrawideFix = GetModuleHandleA("DXFreezerServer.dll") ? 0 : 1;
            return;
        }
        Log("ultrawide: experimental full ultrawide (UltrawideFix=2)");
        if (g_cfg.ultrawideBlackBackdrop) Log("ultrawide: full-height black unlock backdrop enabled");
        InstallUiHooks();
        InstallBinkHooks(dev);
    }
    if (s_state < 0) return;
    if (s_dev && dev != s_dev) DropSurfaces();  // a new device: the old one's surfaces go with it
    s_dev = dev;
    if (pp) Apply(pp->BackBufferWidth, pp->BackBufferHeight, pp->hDeviceWindow ? pp->hDeviceWindow : focus);
}
