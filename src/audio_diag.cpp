// Split/Second controller fix - FMOD events through the import of Event::start (0xB9B43C):
//  - Log=1: name and result of every Event::start, to see which sounds and music the game asks for
//    and what FMOD answers,
//  - photo mode: the top level category of every event that starts is kept, so all of them can be
//    paused while the world is held still (EventCategory::setPaused, FMOD's own API).
//  - Log=1, sound output: FMOD's output type, speaker mode and mixer format, the level of every output
//    channel (the subwoofer's LFE included) measured in FMOD's own mix every 10 s, every change of a
//    category's pause, mute or volume (what the pause menu does), and the built-in properties of the
//    first events by index (their speaker levels, to see whether any event sends to the LFE).
//    Only FMOD's exported functions are called (fmod_event.dll, fmodex.dll); nothing is changed.
#include "common.h"
#include <math.h>
#include <stdio.h>
#include <string.h>

typedef int(__stdcall* EventStart_t)(void* evt);
typedef int(__stdcall* EventGetInfo_t)(void* evt, int* index, char** name, void* info);

static void** const kIatEventStart = (void**)G(0x00B9B43C);    // ?start@Event@FMOD@@QAG?AW4FMOD_RESULT@@XZ

typedef int(__stdcall* GetU32_t)(void* obj, unsigned* out);
typedef int(__stdcall* GetFloat_t)(void* obj, float* out);
typedef int(__stdcall* GetPtr_t)(void* obj, void** out);
typedef int(__stdcall* CatGetInfo_t)(void* cat, int* index, char** name);

static EventStart_t s_origStart;
static EventGetInfo_t s_getInfo;
static GetU32_t s_getState;
static GetFloat_t s_getVolume, s_catGetVolume;
static GetPtr_t s_getCategory, s_catGetParent;
static CatGetInfo_t s_catGetInfo;
typedef int(__stdcall* SetBool_t)(void* obj, bool on);
typedef int(__stdcall* GetBool_t)(void* obj, bool* out);
static SetBool_t s_catSetPaused;
static GetBool_t s_catGetPaused;

// Log=1: sound output diagnostics (fmodex.dll C API, fmod_event.dll categories and event properties)
typedef int(__stdcall* CatGetChannelGroup_t)(void* cat, void** group);
typedef int(__stdcall* CatGetNum_t)(void* cat, int* n);
typedef int(__stdcall* CatGetByIndex_t)(void* cat, int index, void** sub);
typedef int(__stdcall* EvGetPropByIndex_t)(void* evt, int index, void* value, bool thisInstance);
typedef int(__stdcall* CgGetSystem_t)(void* group, void** system);
typedef int(__stdcall* SysGetInt_t)(void* system, int* value);
typedef int(__stdcall* SysGetPtr_t)(void* system, void** value);
typedef int(__stdcall* SysGetFormat_t)(void* system, int* rate, int* format, int* outChannels, int* maxIn, int* resampler,
                                       int* bits);
typedef int(__stdcall* CgGetWave_t)(void* group, float* data, int count, int channelOffset);
static CatGetChannelGroup_t s_catGetChannelGroup;
static CatGetNum_t s_catGetNum;
static CatGetByIndex_t s_catGetByIndex;
static GetBool_t s_catGetMute;
static EvGetPropByIndex_t s_getPropByIndex;
static CgGetSystem_t s_cgGetSystem;
static SysGetInt_t s_sysGetOutput, s_sysGetSpeakerMode;
static SysGetPtr_t s_sysGetMaster;
static SysGetFormat_t s_sysGetFormat;
static CgGetWave_t s_cgGetWave;
static void* s_fmodSystem;
static void* s_master;
static int s_outChannels;
static void* s_rootCategory;

// top level categories of the events seen so far, and whether they were paused before the photo mode
static void* s_categories[32];
static bool s_wasPaused[32];
static int s_ncategories;

static void NoteCategory(void* evt)
{
    void* cat = NULL;
    if (!s_getCategory || s_getCategory(evt, &cat) != 0 || !cat) return;
    for (int depth = 0; depth < 8 && s_catGetParent; depth++) {
        void* parent = NULL;
        if (s_catGetParent(cat, &parent) != 0 || !parent) break;
        cat = parent;
    }
    for (int i = 0; i < s_ncategories; i++)
        if (s_categories[i] == cat) return;
    if (s_ncategories < 32) s_categories[s_ncategories++] = cat;
}

void PauseGameSound(bool pause)
{
    if (!s_catSetPaused) return;
    for (int i = 0; i < s_ncategories; i++) {
        if (pause) {
            s_wasPaused[i] = false;
            if (s_catGetPaused) s_catGetPaused(s_categories[i], &s_wasPaused[i]);
            s_catSetPaused(s_categories[i], true);
        } else if (!s_wasPaused[i]) {
            s_catSetPaused(s_categories[i], false);
        }
    }
}

// music events get a state report a moment after they started
struct Tracked {
    void* evt;
    DWORD started;
    char name[48];
};
static Tracked s_tracked[8];

static bool IsMusic(const char* n)
{
    static const char* const prefixes[] = { "Music", "RaceSection", "PreRace", "BuildUp", "RACE", "Load_loop" };
    for (const char* p : prefixes)
        if (n && !strncmp(n, p, strlen(p))) return true;
    return false;
}

// the speaker mode's channel names in FMOD's output order (FMOD_SPEAKERMODE: 1 mono, 2 stereo, 3 quad,
// 4 surround, 5 5.1, 6 7.1)
static const char* SpeakerName(int mode, int ch)
{
    static const char* const n51[] = { "FL", "FR", "C", "LFE", "SL", "SR", "SBL", "SBR" };
    static const char* const n2[] = { "L", "R" };
    if (mode == 2 && ch < 2) return n2[ch];
    return ch < 8 ? n51[ch] : "?";
}

static void FindFmodSystem(void* evt)
{
    if (s_fmodSystem || !s_getCategory || !s_catGetChannelGroup || !s_cgGetSystem) return;
    void* cat = NULL;
    if (s_getCategory(evt, &cat) != 0 || !cat) return;
    for (int depth = 0; depth < 8 && s_catGetParent; depth++) {
        void* parent = NULL;
        if (s_catGetParent(cat, &parent) != 0 || !parent) break;
        cat = parent;
    }
    s_rootCategory = cat;
    void* group = NULL;
    void* system = NULL;
    if (s_catGetChannelGroup(cat, &group) != 0 || !group || s_cgGetSystem(group, &system) != 0 || !system) return;
    s_fmodSystem = system;
    int output = -1, mode = -1, rate = 0, format = 0, maxIn = 0, resampler = 0, bits = 0;
    if (s_sysGetOutput) s_sysGetOutput(system, &output);
    if (s_sysGetSpeakerMode) s_sysGetSpeakerMode(system, &mode);
    if (s_sysGetFormat) s_sysGetFormat(system, &rate, &format, &s_outChannels, &maxIn, &resampler, &bits);
    if (s_sysGetMaster) s_sysGetMaster(system, &s_master);
    Log("audio: FMOD output type %d (2 = DirectSound), speaker mode %d (5 = 5.1), mixer %d Hz, %d output channels", output,
        mode, rate, s_outChannels);
}

static void WatchCategories(void* cat, int depth, int* count)
{
    static struct { void* cat; bool paused, mute; float vol; } known[64];
    static int nknown;
    static int logged;
    if (!cat || depth > 3 || *count >= 64) return;
    (*count)++;
    bool paused = false, mute = false;
    float vol = -1;
    if (s_catGetPaused) s_catGetPaused(cat, &paused);
    if (s_catGetMute) s_catGetMute(cat, &mute);
    if (s_catGetVolume) s_catGetVolume(cat, &vol);
    int i = 0;
    for (; i < nknown && known[i].cat != cat; i++) {}
    if (i == nknown && nknown < 64) {
        known[nknown].cat = cat;
        known[nknown].paused = paused;
        known[nknown].mute = mute;
        known[nknown].vol = vol;
        nknown++;
    } else if (i < nknown && (known[i].paused != paused || known[i].mute != mute || known[i].vol != vol)) {
        char* name = NULL;
        int idx;
        if (s_catGetInfo) s_catGetInfo(cat, &idx, &name);
        if (logged++ < 300)
            Log("audio: category '%s' paused %d -> %d, mute %d -> %d, volume %.2f -> %.2f", name ? name : "?", known[i].paused,
                paused, known[i].mute, mute, known[i].vol, vol);
        known[i].paused = paused;
        known[i].mute = mute;
        known[i].vol = vol;
    }
    int n = 0;
    if (s_catGetNum && s_catGetByIndex && s_catGetNum(cat, &n) == 0)
        for (int k = 0; k < n && k < 32; k++) {
            void* sub = NULL;
            if (s_catGetByIndex(cat, k, &sub) == 0) WatchCategories(sub, depth + 1, count);
        }
}

// the level of every output channel in FMOD's master mix: the largest and the mean RMS of 10 s
static void MeasureOutput()
{
    static float peak[8], sum[8];
    static unsigned frames;
    static DWORD since;
    if (!s_master || !s_cgGetWave || s_outChannels <= 0) return;
    int channels = s_outChannels < 8 ? s_outChannels : 8;
    for (int c = 0; c < channels; c++) {
        float wave[256];
        if (s_cgGetWave(s_master, wave, 256, c) != 0) return;
        double e = 0;
        for (float v : wave) e += (double)v * v;
        float rms = (float)sqrt(e / 256);
        if (rms > peak[c]) peak[c] = rms;
        sum[c] += rms;
    }
    frames++;
    DWORD now = GetTickCount();
    if (!since) since = now;
    if (now - since < 10000) return;
    int mode = 0;
    if (s_sysGetSpeakerMode) s_sysGetSpeakerMode(s_fmodSystem, &mode);
    char text[400] = "";
    for (int c = 0; c < channels; c++) {
        char one[48];
        _snprintf_s(one, _TRUNCATE, "%s%s %.3f/%.3f", c ? "  " : "", SpeakerName(mode, c), peak[c], sum[c] / frames);
        strcat_s(text, one);
        peak[c] = sum[c] = 0;
    }
    Log("audio out (10 s, RMS max/mean): %s", text);
    frames = 0;
    since = now;
}

void AudioDiagTick()
{
    DWORD now = GetTickCount();
    MeasureOutput();
    static DWORD lastWatch;
    if (s_rootCategory && now - lastWatch >= 250) {
        lastWatch = now;
        int count = 0;
        WatchCategories(s_rootCategory, 0, &count);
    }
    for (Tracked& t : s_tracked) {
        if (!t.evt || now - t.started < 1500) continue;
        unsigned state = 0;
        float vol = -1, catVol = -1, parentVol = -1;
        void *cat = NULL, *parent = NULL;
        char *catName = NULL, *parentName = NULL;
        int idx;
        int rs = s_getState ? s_getState(t.evt, &state) : -1;
        if (s_getVolume) s_getVolume(t.evt, &vol);
        if (s_getCategory && s_getCategory(t.evt, &cat) == 0 && cat) {
            if (s_catGetVolume) s_catGetVolume(cat, &catVol);
            if (s_catGetInfo) s_catGetInfo(cat, &idx, &catName);
            if (s_catGetParent && s_catGetParent(cat, &parent) == 0 && parent) {
                if (s_catGetVolume) s_catGetVolume(parent, &parentVol);
                if (s_catGetInfo) s_catGetInfo(parent, &idx, &parentName);
            }
        }
        Log("fmod music '%s': getState=%d state=%#x volume=%.2f category '%s'=%.2f parent '%s'=%.2f", t.name, rs, state, vol,
            catName ? catName : "?", catVol, parentName ? parentName : "?", parentVol);
        t.evt = NULL;
    }
}

static int __stdcall Hook_EventStart(void* evt)
{
    int r = s_origStart(evt);
    if (g_cfg.photoMode) NoteCategory(evt);
    if (!g_cfg.log) return r;
    FindFmodSystem(evt);
    char* name = NULL;
    int index = -1;
    if (s_getInfo) s_getInfo(evt, &index, &name, NULL);
    // the built-in properties of the first 80 different events, by index (name at 0 is a pointer), as
    // hex and float: their speaker levels and modes show which events send to which speaker
    static char propSeen[80][48];
    static int nprop;
    bool known = false;
    for (int k = 0; k < nprop && name; k++)
        if (!strncmp(propSeen[k], name, 47)) known = true;
    if (name && !known && nprop < 80 && s_getPropByIndex) {
        lstrcpynA(propSeen[nprop++], name, 48);
        char line[1400] = "";
        for (int p = 1; p < 64; p++) {
            DWORD v = 0;
            if (s_getPropByIndex(evt, p, &v, false) != 0) break;
            float f;
            memcpy(&f, &v, 4);
            char one[40];
            if (v == 0) _snprintf_s(one, _TRUNCATE, " %d:0", p);
            else if (f > -1e6f && f < 1e6f && (v & 0x7F800000)) _snprintf_s(one, _TRUNCATE, " %d:%g", p, f);
            else _snprintf_s(one, _TRUNCATE, " %d:#%lX", p, v);
            strcat_s(line, one);
        }
        Log("audio: event '%s' properties%s", name, line);
    }
    if (IsMusic(name)) {
        for (Tracked& t : s_tracked) {
            if (t.evt) continue;
            t.evt = evt;
            t.started = GetTickCount();
            lstrcpynA(t.name, name, sizeof(t.name));
            break;
        }
    }
    // engine and tyre loops restart constantly; log each name only until it has started 3 times
    static struct { char name[48]; int count; } seen[256];
    static int nseen;
    int i = 0;
    for (; i < nseen; i++)
        if (name && !strncmp(seen[i].name, name, sizeof(seen[i].name) - 1)) break;
    if (i == nseen && nseen < 256) {
        lstrcpynA(seen[nseen].name, name ? name : "?", sizeof(seen[0].name));
        seen[nseen++].count = 0;
    }
    if ((i < 256 && seen[i].count++ < 3) || r != 0)
        Log("fmod event start '%s' (#%d) -> %d", name ? name : "?", index, r);
    return r;
}

void InstallAudioDiagnostics()
{
    HMODULE ev = GetModuleHandleA("fmod_event.dll");
    if (!ev) return;
    void* start = (void*)GetProcAddress(ev, "?start@Event@FMOD@@QAG?AW4FMOD_RESULT@@XZ");
    void* info = (void*)GetProcAddress(ev, "?getInfo@Event@FMOD@@QAG?AW4FMOD_RESULT@@PAHPAPADPAUFMOD_EVENT_INFO@@@Z");
    if (!start || *kIatEventStart != start) {
        Log("fmod Event::start import not found");
        return;
    }
    s_origStart = (EventStart_t)start;
    s_getInfo = (EventGetInfo_t)info;
    s_getState = (GetU32_t)GetProcAddress(ev, "?getState@Event@FMOD@@QAG?AW4FMOD_RESULT@@PAI@Z");
    s_getVolume = (GetFloat_t)GetProcAddress(ev, "?getVolume@Event@FMOD@@QAG?AW4FMOD_RESULT@@PAM@Z");
    s_getCategory = (GetPtr_t)GetProcAddress(ev, "?getCategory@Event@FMOD@@QAG?AW4FMOD_RESULT@@PAPAVEventCategory@2@@Z");
    s_catGetVolume = (GetFloat_t)GetProcAddress(ev, "?getVolume@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAM@Z");
    s_catGetInfo = (CatGetInfo_t)GetProcAddress(ev, "?getInfo@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAHPAPAD@Z");
    s_catGetParent = (GetPtr_t)GetProcAddress(ev, "?getParentCategory@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAPAV12@@Z");
    s_catSetPaused = (SetBool_t)GetProcAddress(ev, "?setPaused@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@_N@Z");
    s_catGetPaused = (GetBool_t)GetProcAddress(ev, "?getPaused@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PA_N@Z");
    if (g_cfg.log) {
        s_catGetChannelGroup = (CatGetChannelGroup_t)GetProcAddress(
            ev, "?getChannelGroup@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAPAVChannelGroup@2@@Z");
        s_catGetNum = (CatGetNum_t)GetProcAddress(ev, "?getNumCategories@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAH@Z");
        s_catGetByIndex = (CatGetByIndex_t)GetProcAddress(
            ev, "?getCategoryByIndex@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@HPAPAVEventCategory@2@@Z");
        s_catGetMute = (GetBool_t)GetProcAddress(ev, "?getMute@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PA_N@Z");
        s_getPropByIndex = (EvGetPropByIndex_t)GetProcAddress(ev, "?getPropertyByIndex@Event@FMOD@@QAG?AW4FMOD_RESULT@@HPAX_N@Z");
        HMODULE ex = GetModuleHandleA("fmodex.dll");
        if (ex) {
            s_cgGetSystem = (CgGetSystem_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetSystemObject@8");
            s_sysGetOutput = (SysGetInt_t)GetProcAddress(ex, "_FMOD_System_GetOutput@8");
            s_sysGetSpeakerMode = (SysGetInt_t)GetProcAddress(ex, "_FMOD_System_GetSpeakerMode@8");
            s_sysGetMaster = (SysGetPtr_t)GetProcAddress(ex, "_FMOD_System_GetMasterChannelGroup@8");
            s_sysGetFormat = (SysGetFormat_t)GetProcAddress(ex, "_FMOD_System_GetSoftwareFormat@28");
            s_cgGetWave = (CgGetWave_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetWaveData@16");
        }
    }
    Log("fmod exports: state=%p vol=%p cat=%p catvol=%p catinfo=%p parent=%p", s_getState, s_getVolume, s_getCategory,
        s_catGetVolume, s_catGetInfo, s_catGetParent);
    void* hook = (void*)Hook_EventStart;
    DWORD old;
    VirtualProtect(kIatEventStart, 4, PAGE_READWRITE, &old);
    *kIatEventStart = hook;
    VirtualProtect(kIatEventStart, 4, old, &old);
    Log(g_cfg.log ? "fmod event logging on" : "fmod categories noted for the photo mode");
}
