// Split/Second controller fix - FMOD events through the import of Event::start (0xB9B43C):
//  - Log=1: name and result of every Event::start, to see which sounds and music the game asks for
//    and what FMOD answers,
//  - photo mode: the top level category of every event that starts is kept, so all of them can be
//    paused while the world is held still (EventCategory::setPaused, FMOD's own API), dispatched to
//    the audio thread so all FMOD calls run on the thread that owns the sound system.
//  - Log=1, sound output: FMOD's output type, speaker mode and mixer format, the level of every output
//    channel (the subwoofer's LFE included) measured in FMOD's final mix (System::getWaveData) and the level of
//    the music category's own mix every 10 s, every change of a category's pause, mute or volume (what the pause
//    menu does), and the built-in properties of the first events by index (their speaker levels, to see whether
//    any event sends to the LFE). Music events are logged every time with their group (and [DLC3_Music] when
//    they come from the DLC's project, dlcmusic.cpp).
//    The master channel group's getWaveData (used before) answers FMOD_OK with silence in this FMOD Ex (4.28,
//    checked with the game's DLLs: the event system's categories mix into the output past it), so the 10 s line
//    read 0.000 on every channel; System::getWaveData and the categories' channel groups carry the signal.
//    Only FMOD's exported functions are called (fmod_event.dll, fmodex.dll); nothing is changed.
//  - OpponentEngineVolume: the volume of the opponents' engines (FMOD category CPUEngine), through the
//    exe's import of ChannelGroup::setVolume.
//  - Threading: every FMOD handle and call lives strictly on the game's audio thread (Hook_EventStart,
//    Hook_GroupSetVolume, audio message handlers); the game thread communicates only via atomic flags.
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

// FMOD helpers (fmodex.dll C API, fmod_event.dll categories and event properties)
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
typedef int(__stdcall* CgGetVolume_t)(void* group, float* volume);
typedef int(__stdcall* SysGetWave_t)(void* system, float* data, int count, int channelOffset);
typedef int(__stdcall* EvGetParentGroup_t)(void* evt, void** group);
typedef int(__stdcall* GrGetInfo_t)(void* group, int* index, char** name);
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
static CgGetVolume_t s_cgGetVolume;
static SysGetWave_t s_sysGetWave;
static EvGetParentGroup_t s_evGetParentGroup;
static GrGetInfo_t s_grGetInfo;

// Sound-system diagnostic state (all pointers audio-thread only; reset via AudioDiagResetState on system rebuild)
static void* s_fmodSystem;
static void* s_master;
static void* s_musicGroup;        // channel group of category 'music' (cleared by dlcmusic.cpp and Hook_Msg51)
static int s_outChannels;
static void* s_rootCategory;
// Log=1, audio thread only: active CPUEngine event instances (cleared on project swap / system reset)
struct CpuEvt { void* evt; char name[48]; };
static CpuEvt s_cpuEvts[16];
static int s_ncpuEvts;

// Photo mode: top-level categories of events seen on the current sound system (audio thread only);
// PauseGameSound on the game thread sets s_photoPauseWant and the audio thread applies it.
static void* s_categories[32];
static bool s_wasPaused[32];
static int s_ncategories;
static volatile LONG s_photoPauseWant;
static bool s_photoPauseApplied;

static void NoteCategory(void* evt)
{
    void* cat = NULL;
    if (!s_getCategory || s_getCategory(evt, &cat) != 0 || !cat) return;
    // Walk up to the direct child of master; never register or pause master itself
    void* parent = NULL;
    if (!s_catGetParent || s_catGetParent(cat, &parent) != 0 || !parent) return;
    for (int depth = 0; depth < 8; depth++) {
        void* grandparent = NULL;
        if (s_catGetParent(parent, &grandparent) != 0 || !grandparent) break;
        cat = parent;
        parent = grandparent;
    }
    for (int i = 0; i < s_ncategories; i++)
        if (s_categories[i] == cat) return;
    if (s_ncategories < 32) {
        int idx = s_ncategories++;
        s_categories[idx] = cat;
        s_wasPaused[idx] = false;
        if (s_photoPauseApplied && s_catSetPaused) {
            if (s_catGetPaused) s_catGetPaused(cat, &s_wasPaused[idx]);
            s_catSetPaused(cat, true);
        }
    }
}

static void ApplyPhotoPause()
{
    if (!s_catSetPaused) return;
    bool want = s_photoPauseWant != 0;
    if (want == s_photoPauseApplied) return;
    s_photoPauseApplied = want;
    for (int i = 0; i < s_ncategories; i++) {
        if (want) {
            s_wasPaused[i] = false;
            if (s_catGetPaused) s_catGetPaused(s_categories[i], &s_wasPaused[i]);
            s_catSetPaused(s_categories[i], true);
        } else if (!s_wasPaused[i]) {
            s_catSetPaused(s_categories[i], false);
        }
    }
    if (g_cfg.log)
        Log("photo mode: %s %d categories on the audio thread (thread %lu)", want ? "paused" : "resumed", s_ncategories,
            GetCurrentThreadId());
}

void PauseGameSound(bool pause)
{
    InterlockedExchange(&s_photoPauseWant, pause ? 1 : 0);
}

// Opponent engine volume (OpponentEngineVolume): every opponent's engine event is in the FMOD category
// CPUEngine, a child of master (the player's engine is in PlayerEngine). The game's mixer sets the level of
// that category's channel group itself, through the exe's import of ChannelGroup::setVolume, for its mix
// presets, fades and ducking. That import is wrapped: a level the game sets on the CPUEngine group is
// multiplied by the setting (percent, 0..200), every other group passes as it is. FMOD caps a group's level at
// 1.0 (ChannelGroupI::setVolume); in the logged races the mixer's level for CPUEngine peaked at about 0.17, so
// 200 % has room. At 100 % the game's levels pass as they are and nothing else is written.
// Threads: the setting is read from the INI on the game thread every 2 s (only when the file changed), which
// only stores it and asks for it to be applied. Every FMOD call is made where the game makes its own: in the
// wrapper above, and at the next Event::start, which finds the group from that event's category tree and
// applies a pending change. A group that is not the one known (or belongs to a new FMOD::System*) is taken
// with the level FMOD reports for it, unscaled, since the wrapper has not touched it.
typedef int(__stdcall* CatGetByName_t)(void* cat, const char* name, void** sub);
typedef int(__stdcall* GroupSetVolume_t)(void* group, float volume);
typedef int(__stdcall* GroupGetVolume_t)(void* group, float* volume);
static CatGetByName_t s_catGetByName;
static GroupSetVolume_t s_groupSetVolume;
static GroupGetVolume_t s_groupGetVolume;
static bool s_engineScaleReady;               // OpponentEngines=1 and all CPUEngine FMOD exports resolved
static void* s_engineGroup;                   // CPUEngine's channel group (audio thread only; cleared on project swap)
static void* s_engineSystem;                  // FMOD::System* owning s_engineGroup
static void* s_lastEngineGroup;               // last known group pointer (audio thread only; cleared on system reset)
static void* s_lastEngineSystem;              // FMOD::System* owning s_lastEngineGroup
static volatile LONG s_enginePercent = 100;   // the setting (written by the game thread)
static volatile LONG s_engineApply;           // a changed setting waits for the next Event::start
static float s_engineLevel = 1.0f;            // the game's last unscaled level for the group (audio thread only)
static bool s_engineUnscaledPending;          // a mixer write passed unscaled during clear-to-reacquire window
static bool s_engineLogNext;                  // Log=1: log next mixer setVolume after an INI change or swap reacquire

// Log=1 audio-thread accumulators for 10 s output and CPUEngine summary (cleared on system reset)
static float s_outPeak[8], s_outSum[8], s_musicPeak, s_musicSum, s_musicVolMin, s_musicVolSum;
static unsigned s_outFrames, s_musicFrames, s_musicVolFrames;
static DWORD s_outSince, s_lastSampleTick, s_lastWatchTick;
static float s_cpuPeak, s_cpuSum, s_cpuVolMin, s_cpuVolSum;
static unsigned s_cpuFrames, s_cpuVolFrames;

// Log=1 category-watch cache (audio thread only; cleared on system reset)
static struct { void* cat; bool paused, mute; float vol; } s_watchKnown[64];
static int s_nwatchKnown;

// Log=1 delayed music event probes (audio thread only; cleared before every project unload and system reset)
struct Tracked {
    void* evt;
    DWORD started;
    char name[48];
};
static Tracked s_tracked[8];

static float WaveRms(const float* wave, int n);
static void AudioDiagAudioThreadStep();

void AudioDiagResetState()
{
    s_fmodSystem = NULL;
    s_master = NULL;
    s_rootCategory = NULL;
    s_outChannels = 0;
    s_musicGroup = NULL;
    s_engineGroup = NULL;
    s_engineSystem = NULL;
    s_lastEngineGroup = NULL;
    s_lastEngineSystem = NULL;
    s_engineLevel = 1.0f;
    s_engineUnscaledPending = false;
    s_engineLogNext = false;
    s_ncpuEvts = 0;
    s_ncategories = 0;
    s_photoPauseApplied = false;
    s_nwatchKnown = 0;
    memset(s_tracked, 0, sizeof(s_tracked));
    memset(s_outPeak, 0, sizeof(s_outPeak));
    memset(s_outSum, 0, sizeof(s_outSum));
    s_musicPeak = s_musicSum = s_musicVolMin = s_musicVolSum = 0;
    s_outFrames = s_musicFrames = s_musicVolFrames = 0;
    s_cpuPeak = s_cpuSum = s_cpuVolMin = s_cpuVolSum = 0;
    s_cpuFrames = s_cpuVolFrames = 0;
}

static void RecordCpuAudioStats(void* group, float appliedVol)
{
    if (!g_cfg.log || !group) return;
    if (s_cgGetWave) {
        float l[256], r[256];
        if (s_cgGetWave(group, l, 256, 0) == 0 && s_cgGetWave(group, r, 256, 1) == 0) {
            float rms = (WaveRms(l, 256) + WaveRms(r, 256)) * 0.5f;
            if (rms > s_cpuPeak) s_cpuPeak = rms;
            s_cpuSum += rms;
            s_cpuFrames++;
        }
    }
    if (!s_cpuVolFrames || appliedVol < s_cpuVolMin) s_cpuVolMin = appliedVol;
    s_cpuVolSum += appliedVol;
    s_cpuVolFrames++;
}

static int __stdcall Hook_GroupSetVolume(void* group, float volume)
{
    if (!AudioSystemInitHooked()) return s_groupSetVolume(group, volume);
    void* sys = NULL;
    if (group && s_cgGetSystem && s_cgGetSystem(group, &sys) != 0) sys = NULL;
    AudioSystemEnsureCurrent(sys, "new FMOD::System in ChannelGroup::setVolume");

    float applied = volume;
    if (s_engineScaleReady && group && group == s_engineGroup &&
        (!sys || !s_engineSystem || sys == s_engineSystem)) {
        static bool threadLogged;
        if (!threadLogged && g_cfg.log) {
            threadLogged = true;
            Log("opponent engine volume: the mixer sets CPUEngine on thread %lu", GetCurrentThreadId());
        }
        s_engineLevel = volume;
        s_engineUnscaledPending = false;
        bool fromIni = InterlockedExchange(&s_engineApply, 0) != 0;
        LONG pct = s_enginePercent;
        applied = pct == 100 ? volume : volume * (pct / 100.0f);
        if (g_cfg.log && (fromIni || s_engineLogNext)) {
            s_engineLogNext = false;
            Log("opponent engine volume %ld %% (mixer): stock %.4f -> set %.4f", pct, volume, applied);
        }
        RecordCpuAudioStats(group, applied);
    } else if (s_engineScaleReady && !s_engineGroup && group && group == s_lastEngineGroup &&
               (!sys || !s_lastEngineSystem || sys == s_lastEngineSystem)) {
        // Same-system project swap window: keep s_engineLevel current so reacquire never applies a stale level.
        s_engineLevel = volume;
        s_engineUnscaledPending = true;
    }

    if (g_cfg.log && g_cfg.ducking) AudioFixNoteGroupVolume(group, volume, applied);
    if (g_cfg.photoMode) ApplyPhotoPause();
    if (g_cfg.log) AudioDiagAudioThreadStep();
    return s_groupSetVolume(group, applied);
}

// Root category and FMOD::System* of an event that starts (audio thread only)
static void* RootCategoryOf(void* evt)
{
    void* cat = NULL;
    if (!s_getCategory || s_getCategory(evt, &cat) != 0 || !cat) return NULL;
    for (int depth = 0; depth < 8 && s_catGetParent; depth++) {
        void* parent = NULL;
        if (s_catGetParent(cat, &parent) != 0 || !parent) break;
        cat = parent;
    }
    return cat;
}

static void* SystemOfRoot(void* rootCat)
{
    if (!rootCat || !s_catGetChannelGroup || !s_cgGetSystem) return NULL;
    void *group = NULL, *sys = NULL;
    if (s_catGetChannelGroup(rootCat, &group) != 0 || !group || s_cgGetSystem(group, &sys) != 0) return NULL;
    return sys;
}

// CPUEngine's channel group, from the root category of an event that starts
static void* EngineGroupOf(void* rootCat)
{
    if (!rootCat || !s_catGetByName || !s_catGetChannelGroup) return NULL;
    void *engines = NULL, *group = NULL;
    if (s_catGetByName(rootCat, "CPUEngine", &engines) != 0 || !engines ||
        s_catGetChannelGroup(engines, &group) != 0)
        return NULL;
    return group;
}

// at every Event::start until the group is known, then only while a changed setting waits
static void UpdateEngineGroup(void* rootCat, void* evtSys)
{
    if (!s_engineScaleReady || !s_groupSetVolume || (s_engineGroup && !s_engineApply)) return;
    void* group = EngineGroupOf(rootCat);
    if (!group) return;  // tried again at the next start
    void* sys = evtSys;
    if (!sys && s_cgGetSystem) s_cgGetSystem(group, &sys);
    if (group != s_engineGroup) {
        const bool sameGroup =
            (group == s_lastEngineGroup) && (!sys || !s_lastEngineSystem || sys == s_lastEngineSystem);
        // Keep the stored unscaled level when the same group returns on the same system after a project swap;
        // reading FMOD's current volume there would double-scale a non-100 % setting.
        if (!sameGroup) {
            float level;
            if (s_groupGetVolume(group, &level) != 0) return;
            s_engineLevel = level;
            s_lastEngineGroup = group;
            s_lastEngineSystem = sys;
            s_engineUnscaledPending = false;
            Log("opponent engine volume: CPUEngine group found (level %.2f), %ld %%, thread %lu", level, s_enginePercent,
                GetCurrentThreadId());
        }
        s_engineGroup = group;
        s_engineSystem = sys;
        // Skip redundant or stale writes when the same group returns with no pending INI or unscaled mixer write.
        if (!s_engineApply && (!s_engineUnscaledPending || s_enginePercent == 100) && (sameGroup || s_enginePercent == 100)) {
            s_engineUnscaledPending = false;
            return;
        }
    }
    bool fromIni = InterlockedExchange(&s_engineApply, 0) != 0;
    bool fromSwap = s_engineUnscaledPending;
    s_engineUnscaledPending = false;
    LONG pct = s_enginePercent;
    float applied = pct == 100 ? s_engineLevel : s_engineLevel * (pct / 100.0f);
    if (g_cfg.log) {
        Log("opponent engine volume %ld %% (%s): stock %.4f -> set %.4f", pct,
            fromIni ? "ini" : fromSwap ? "post-swap" : "init", s_engineLevel, applied);
        s_engineLogNext = true;
    }
    s_groupSetVolume(group, applied);
}

// the exe's import of a DLL function, by name: its slot in the import address table
void** FindImport(const char* dll, const char* name)
{
    BYTE* base = (BYTE*)GetModuleHandleA(NULL);
    HMODULE lib = GetModuleHandleA(dll);
    void* fn = lib ? (void*)GetProcAddress(lib, name) : NULL;
    if (!fn) return NULL;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + ((IMAGE_DOS_HEADER*)base)->e_lfanew);
    IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress || !dir.Size) return NULL;
    for (IMAGE_IMPORT_DESCRIPTOR* d = (IMAGE_IMPORT_DESCRIPTOR*)(base + dir.VirtualAddress); d->Name; d++) {
        if (!d->FirstThunk || lstrcmpiA((const char*)(base + d->Name), dll) != 0) continue;
        for (void** slot = (void**)(base + d->FirstThunk); *slot; slot++)
            if (*slot == fn) return slot;
    }
    return NULL;
}

static void InstallEngineVolume(HMODULE ev)
{
    if (!g_cfg.opponentEngines && !g_cfg.log && !g_cfg.photoMode) return;
    HMODULE ex = GetModuleHandleA("fmodex.dll");
    const char* const kSetVolume = "?setVolume@ChannelGroup@FMOD@@QAG?AW4FMOD_RESULT@@M@Z";
    if (g_cfg.opponentEngines) {
        s_catGetByName = (CatGetByName_t)GetProcAddress(
            ev, "?getCategory@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PBDPAPAVEventCategory@2@@Z");
        s_groupGetVolume = ex ? (GroupGetVolume_t)GetProcAddress(
                                    ex, "?getVolume@ChannelGroup@FMOD@@QAG?AW4FMOD_RESULT@@PAM@Z")
                              : NULL;
        s_engineScaleReady = s_catGetByName && s_catGetParent && s_catGetChannelGroup && s_groupGetVolume && s_getCategory;
        if (!s_engineScaleReady)
            Log("opponent engine volume: FMOD functions not found, left as the game's");
    }
    void** slot = FindImport("fmodex.dll", kSetVolume);
    if (!slot) {
        s_engineScaleReady = false;
        Log("audio: ChannelGroup::setVolume import not found, left as the game's");
        return;
    }
    s_enginePercent = g_cfg.opponentEngineVolume;
    GroupSetVolume_t original = (GroupSetVolume_t)*slot;
    s_groupSetVolume = original;  // before the slot points at the wrapper, which calls it
    if (!WriteSlot(slot, (void*)Hook_GroupSetVolume)) {
        s_groupSetVolume = NULL;
        s_engineScaleReady = false;
        Log("audio: the ChannelGroup::setVolume import could not be written, left as the game's");
        return;
    }
    if (s_engineScaleReady) Log("opponent engine volume: ChannelGroup::setVolume import wrapped, %ld %%", s_enginePercent);
    else Log("audio: ChannelGroup::setVolume import wrapped for audio-thread %s",
             g_cfg.log && g_cfg.photoMode ? "diagnostics and photo mode" : g_cfg.log ? "diagnostics" : "photo mode");
}

// on the game thread, every 2 s: a changed INI value is stored and applied at the next Event::start
static void ReadEngineVolume()
{
    static DWORD last;
    static FILETIME written;
    DWORD now = GetTickCount();
    if (last && now - last < 2000) return;
    last = now;
    char ini[MAX_PATH];
    ModulePath(ini, "SplitSecondPad.ini");
    WIN32_FILE_ATTRIBUTE_DATA fa;  // only when the file changed
    if (!GetFileAttributesExA(ini, GetFileExInfoStandard, &fa) || !CompareFileTime(&fa.ftLastWriteTime, &written)) return;
    written = fa.ftLastWriteTime;
    LONG percent = IniPercent("Audio", "OpponentEngineVolume", 100, 0, 200);
    if (percent == s_enginePercent) return;
    Log("opponent engine volume %ld %%", percent);
    InterlockedExchange(&s_enginePercent, percent);
    InterlockedExchange(&s_engineApply, 1);
}

// every frame, on the game thread
void OpponentEngineVolumeTick()
{
    if (s_engineScaleReady && s_groupSetVolume) ReadEngineVolume();
}

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
    static const char* const n51[] = { "FL", "FR", "C", "LFE", "BL", "BR", "SL", "SR" };
    static const char* const n2[] = { "L", "R" };
    static const char* const nQuad[] = { "FL", "FR", "BL", "BR" };
    static const char* const nSurround[] = { "FL", "FR", "C", "BL", "BR" };
    if (mode == 1 && ch < 1) return "M";
    if (mode == 2 && ch < 2) return n2[ch];
    if (mode == 3 && ch < 4) return nQuad[ch];
    if (mode == 4 && ch < 5) return nSurround[ch];
    return ch < 8 ? n51[ch] : "?";
}

// audio thread: dlcmusic.cpp / Hook_Msg51 swapped or unloaded FMOD projects on the same sound system;
// discard delayed music event probes and re-fetch music/CPUEngine groups while keeping s_lastEngineGroup
// so surviving CPUEngine retains its unscaled level
void AudioDiagProjectsChanged()
{
    s_musicGroup = NULL;
    s_engineGroup = NULL;
    s_engineSystem = NULL;
    s_ncpuEvts = 0;
    memset(s_tracked, 0, sizeof(s_tracked));
}

static void FindFmodSystem(void* rootCat, void* system)
{
    if (s_fmodSystem || !rootCat || !system) return;
    s_rootCategory = rootCat;
    s_fmodSystem = system;
    int output = -1, mode = -1, rate = 0, format = 0, maxIn = 0, resampler = 0, bits = 0;
    if (s_sysGetOutput) s_sysGetOutput(system, &output);
    if (s_sysGetSpeakerMode) s_sysGetSpeakerMode(system, &mode);
    if (s_sysGetFormat) s_sysGetFormat(system, &rate, &format, &s_outChannels, &maxIn, &resampler, &bits);
    if (s_sysGetMaster) s_sysGetMaster(system, &s_master);
    Log("audio: FMOD output type %d (6 = DirectSound, 8 = WASAPI), speaker mode %d (5 = 5.1), mixer %d Hz, %d output "
        "channels; first Event::start on thread %lu",
        output, mode, rate, s_outChannels, GetCurrentThreadId());
}

static void WatchCategories(void* cat, int depth, int* count)
{
    static int logged;
    if (!cat || depth > 3 || *count >= 64) return;
    (*count)++;
    bool paused = false, mute = false;
    float vol = -1;
    if (s_catGetPaused) s_catGetPaused(cat, &paused);
    if (s_catGetMute) s_catGetMute(cat, &mute);
    if (s_catGetVolume) s_catGetVolume(cat, &vol);
    int i = 0;
    for (; i < s_nwatchKnown && s_watchKnown[i].cat != cat; i++) {}
    if (i == s_nwatchKnown && s_nwatchKnown < 64) {
        s_watchKnown[s_nwatchKnown].cat = cat;
        s_watchKnown[s_nwatchKnown].paused = paused;
        s_watchKnown[s_nwatchKnown].mute = mute;
        s_watchKnown[s_nwatchKnown].vol = vol;
        s_nwatchKnown++;
    } else if (i < s_nwatchKnown &&
               (s_watchKnown[i].paused != paused || s_watchKnown[i].mute != mute || s_watchKnown[i].vol != vol)) {
        char* name = NULL;
        int idx;
        if (s_catGetInfo) s_catGetInfo(cat, &idx, &name);
        if (logged++ < 300)
            Log("audio: category '%s' paused %d -> %d, mute %d -> %d, volume %.2f -> %.2f", name ? name : "?",
                s_watchKnown[i].paused, paused, s_watchKnown[i].mute, mute, s_watchKnown[i].vol, vol);
        s_watchKnown[i].paused = paused;
        s_watchKnown[i].mute = mute;
        s_watchKnown[i].vol = vol;
    }
    int n = 0;
    if (s_catGetNum && s_catGetByIndex && s_catGetNum(cat, &n) == 0)
        for (int k = 0; k < n && k < 32; k++) {
            void* sub = NULL;
            if (s_catGetByIndex(cat, k, &sub) == 0) WatchCategories(sub, depth + 1, count);
        }
}

static float WaveRms(const float* wave, int n)
{
    double e = 0;
    for (int i = 0; i < n; i++) e += (double)wave[i] * wave[i];
    return (float)sqrt(e / n);
}

// audio thread only: the level of every output channel in FMOD's final mix and of the music category (front
// left and right): the largest and the mean RMS of 10 s. The game's dynamic mixer (Audio/XML/DynamicMixer.xml)
// sets the categories' channel group volumes (ChannelGroup::setVolume), not the category volume; the music
// group's volume is logged beside its level (lowest/mean, in dB), as the wave data may be read before that volume.
static void MeasureOutput(DWORD now)
{
    if (!s_fmodSystem || !s_sysGetWave || s_outChannels <= 0) return;
    int channels = s_outChannels < 8 ? s_outChannels : 8;
    for (int c = 0; c < channels; c++) {
        float wave[256];
        if (s_sysGetWave(s_fmodSystem, wave, 256, c) != 0) return;
        float rms = WaveRms(wave, 256);
        if (rms > s_outPeak[c]) s_outPeak[c] = rms;
        s_outSum[c] += rms;
    }
    if (s_musicGroup && s_cgGetWave) {
        float l[256], r[256];
        if (s_cgGetWave(s_musicGroup, l, 256, 0) == 0 && s_cgGetWave(s_musicGroup, r, 256, 1) == 0) {
            float rms = (WaveRms(l, 256) + WaveRms(r, 256)) * 0.5f;
            if (rms > s_musicPeak) s_musicPeak = rms;
            s_musicSum += rms;
            s_musicFrames++;
        }
    }
    float musicVol;
    if (s_musicGroup && s_cgGetVolume && s_cgGetVolume(s_musicGroup, &musicVol) == 0) {
        if (!s_musicVolFrames || musicVol < s_musicVolMin) s_musicVolMin = musicVol;
        s_musicVolSum += musicVol;
        s_musicVolFrames++;
    }
    s_outFrames++;
    if (!s_outSince) s_outSince = now;
    if (now - s_outSince < 10000) return;
    int mode = 0;
    if (s_sysGetSpeakerMode) s_sysGetSpeakerMode(s_fmodSystem, &mode);
    char text[400] = "";
    for (int c = 0; c < channels; c++) {
        char one[48];
        _snprintf_s(one, _TRUNCATE, "%s%s %.3f/%.3f", c ? "  " : "", SpeakerName(mode, c), s_outPeak[c],
                    s_outSum[c] / s_outFrames);
        strcat_s(text, one);
        s_outPeak[c] = s_outSum[c] = 0;
    }
    char music[64] = "not seen yet";
    if (s_musicFrames) _snprintf_s(music, _TRUNCATE, "%.3f/%.3f", s_musicPeak, s_musicSum / s_musicFrames);
    char mixer[64] = "";
    if (s_musicVolFrames) {
        float mean = s_musicVolSum / s_musicVolFrames;
        float minDb = s_musicVolMin > 0 ? 20.0f * log10f(s_musicVolMin) : -99.0f;
        float meanDb = mean > 0 ? 20.0f * log10f(mean) : -99.0f;
        _snprintf_s(mixer, _TRUNCATE, ", mixer volume min/mean %.1f/%.1f dB", minDb, meanDb);
    }
    char cpu[96] = "";
    if (s_cpuFrames || s_cpuVolFrames) {
        float volMean = s_cpuVolFrames ? s_cpuVolSum / s_cpuVolFrames : 0.0f;
        _snprintf_s(cpu, _TRUNCATE, "; CPUEngine L+R %.3f/%.3f (vol min/mean %.2f/%.2f)", s_cpuPeak,
                    s_cpuFrames ? s_cpuSum / s_cpuFrames : 0.0f, s_cpuVolFrames ? s_cpuVolMin : 0.0f, volMean);
    }
    Log("audio out (10 s, RMS max/mean): %s; music category L+R %s%s%s", text, music, mixer, cpu);
    s_musicPeak = s_musicSum = s_musicVolMin = s_musicVolSum = 0;
    s_outFrames = s_musicFrames = s_musicVolFrames = 0;
    s_cpuPeak = s_cpuSum = s_cpuVolMin = s_cpuVolSum = 0;
    s_cpuFrames = s_cpuVolFrames = 0;
    s_outSince = now;
}

static void FlushTrackedMusic(DWORD now)
{
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

static void AudioDiagAudioThreadStep()
{
    DWORD now = GetTickCount();
    FlushTrackedMusic(now);
    if (!s_lastSampleTick || now - s_lastSampleTick >= 16) {
        s_lastSampleTick = now;
        MeasureOutput(now);
    }
    if (s_rootCategory && (!s_lastWatchTick || now - s_lastWatchTick >= 250)) {
        s_lastWatchTick = now;
        int count = 0;
        WatchCategories(s_rootCategory, 0, &count);
    }
}

static int __stdcall Hook_EventStart(void* evt)
{
    int r = s_origStart(evt);
    const bool lifecycleOk = AudioSystemInitHooked();
    void* rootCat = NULL;
    void* sys = NULL;
    if (lifecycleOk) {
        rootCat = RootCategoryOf(evt);
        sys = SystemOfRoot(rootCat);
        AudioSystemEnsureCurrent(sys, "new FMOD::System in Event::start");
        if (g_cfg.photoMode) {
            NoteCategory(evt);
            ApplyPhotoPause();
        }
        UpdateEngineGroup(rootCat, sys);
    }
    if (!g_cfg.log) return r;
    if (lifecycleOk) {
        AudioFixNoteEventPeak();
        FindFmodSystem(rootCat, sys);
    }
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
    bool music = IsMusic(name);
    char where[96] = "";
    if (music) {
        if (lifecycleOk) {
            for (Tracked& t : s_tracked) {
                if (t.evt) continue;
                t.evt = evt;
                t.started = GetTickCount();
                lstrcpynA(t.name, name, sizeof(t.name));
                break;
            }
        }
        // its group (Quarry_Track, InGame_Track_05, PreRace ...) and whether it is DLC3_Music's
        void* group = NULL;
        char* gname = NULL;
        int gi = -1;
        if (s_evGetParentGroup && s_grGetInfo && s_evGetParentGroup(evt, &group) == 0 && group)
            s_grGetInfo(group, &gi, &gname);
        _snprintf_s(where, _TRUNCATE, " group '%s'%s", gname ? gname : "?", DlcMusicIsDlcEvent(evt) ? " [DLC3_Music]" : "");
        // the music category's channel group, for the level in the 10 s line
        if (lifecycleOk) {
            void* cat = NULL;
            char* cname = NULL;
            int ci;
            void* musicGroup = NULL;
            if (!s_musicGroup && s_getCategory && s_catGetInfo && s_catGetChannelGroup && s_getCategory(evt, &cat) == 0 && cat &&
                s_catGetInfo(cat, &ci, &cname) == 0 && cname && !strcmp(cname, "music") &&
                s_catGetChannelGroup(cat, &musicGroup) == 0 && musicGroup)
                s_musicGroup = musicGroup;
        }
    }
    bool cpuEngine = false;
    if (lifecycleOk) {
        void* cat = NULL;
        char* cname = NULL;
        int ci;
        if (s_getCategory && s_catGetInfo && s_getCategory(evt, &cat) == 0 && cat &&
            s_catGetInfo(cat, &ci, &cname) == 0 && cname && !strcmp(cname, "CPUEngine"))
            cpuEngine = true;
    }
    if (cpuEngine) {
        // Track distinct CPUEngine handles and report live simultaneous PLAYING instances (audio thread only)
        int write = 0, playingTotal = 0, playingSame = 0;
        bool knownEvt = false;
        for (int k = 0; k < s_ncpuEvts; k++) {
            unsigned st = 0;
            bool alive = s_cpuEvts[k].evt == evt ||
                         (s_getState && s_getState(s_cpuEvts[k].evt, &st) == 0 && (st & 8));
            if (!alive) continue;
            if (s_cpuEvts[k].evt == evt) {
                knownEvt = true;
                lstrcpynA(s_cpuEvts[k].name, name ? name : "?", sizeof(s_cpuEvts[0].name));
            }
            if (write != k) s_cpuEvts[write] = s_cpuEvts[k];
            write++;
        }
        s_ncpuEvts = write;
        if (!knownEvt && s_ncpuEvts < 16) {
            s_cpuEvts[s_ncpuEvts].evt = evt;
            lstrcpynA(s_cpuEvts[s_ncpuEvts].name, name ? name : "?", sizeof(s_cpuEvts[0].name));
            s_ncpuEvts++;
        }
        for (int k = 0; k < s_ncpuEvts; k++) {
            unsigned st = 0;
            if (s_getState && s_getState(s_cpuEvts[k].evt, &st) == 0 && (st & 8)) {
                playingTotal++;
                if (name && !strcmp(s_cpuEvts[k].name, name)) playingSame++;
            }
        }
        static int cpuLogged;
        if ((!knownEvt || r != 0) && cpuLogged < 200) {
            cpuLogged++;
            Log("fmod cpu engine start '%s' (#%d, evt=%p) -> %d (playing: %d same, %d total)", name ? name : "?", index,
                evt, r, playingSame, playingTotal);
        }
        AudioDiagAudioThreadStep();
        return r;
    }
    // engine and tyre loops restart constantly; log each name only until it has started 3 times (music always)
    static struct { char name[48]; int count; } seen[256];
    static int nseen;
    int i = 0;
    for (; i < nseen; i++)
        if (name && !strncmp(seen[i].name, name, sizeof(seen[i].name) - 1)) break;
    if (i == nseen && nseen < 256) {
        lstrcpynA(seen[nseen].name, name ? name : "?", sizeof(seen[0].name));
        seen[nseen++].count = 0;
    }
    if ((i < 256 && seen[i].count++ < 3) || r != 0 || music)
        Log("fmod event start '%s' (#%d) -> %d%s", name ? name : "?", index, r, where);
    if (lifecycleOk) AudioDiagAudioThreadStep();
    return r;
}

void InstallAudioDiagnostics()
{
    const bool lifecycleOk = AudioSystemInitHooked();
    if (!lifecycleOk) {
        Log("audio: System::setFileSystem not wrapped: photo mode sound pause, opponent engine volume and stateful "
            "audio diagnostics stay off");
        if (!g_cfg.log) return;
    }
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
    s_catGetParent =
        (GetPtr_t)GetProcAddress(ev, "?getParentCategory@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAPAVEventCategory@2@@Z");
    s_catSetPaused = (SetBool_t)GetProcAddress(ev, "?setPaused@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@_N@Z");
    s_catGetPaused = (GetBool_t)GetProcAddress(ev, "?getPaused@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PA_N@Z");
    s_catGetChannelGroup = (CatGetChannelGroup_t)GetProcAddress(
        ev, "?getChannelGroup@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAPAVChannelGroup@2@@Z");
    HMODULE ex = GetModuleHandleA("fmodex.dll");
    if (ex) s_cgGetSystem = (CgGetSystem_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetSystemObject@8");
    if (g_cfg.log) {
        s_catGetNum = (CatGetNum_t)GetProcAddress(ev, "?getNumCategories@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PAH@Z");
        s_catGetByIndex = (CatGetByIndex_t)GetProcAddress(
            ev, "?getCategoryByIndex@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@HPAPAVEventCategory@2@@Z");
        s_catGetMute = (GetBool_t)GetProcAddress(ev, "?getMute@EventCategoryI@FMOD@@UAG?AW4FMOD_RESULT@@PA_N@Z");
        s_getPropByIndex = (EvGetPropByIndex_t)GetProcAddress(ev, "?getPropertyByIndex@Event@FMOD@@QAG?AW4FMOD_RESULT@@HPAX_N@Z");
        if (ex) {
            s_sysGetOutput = (SysGetInt_t)GetProcAddress(ex, "_FMOD_System_GetOutput@8");
            s_sysGetSpeakerMode = (SysGetInt_t)GetProcAddress(ex, "_FMOD_System_GetSpeakerMode@8");
            s_sysGetMaster = (SysGetPtr_t)GetProcAddress(ex, "_FMOD_System_GetMasterChannelGroup@8");
            s_sysGetFormat = (SysGetFormat_t)GetProcAddress(ex, "_FMOD_System_GetSoftwareFormat@28");
            s_cgGetWave = (CgGetWave_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetWaveData@16");
            s_cgGetVolume = (CgGetVolume_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetVolume@8");
            s_sysGetWave = (SysGetWave_t)GetProcAddress(ex, "_FMOD_System_GetWaveData@16");
        }
        s_evGetParentGroup = (EvGetParentGroup_t)GetProcAddress(ev, "_FMOD_Event_GetParentGroup@8");
        s_grGetInfo = (GrGetInfo_t)GetProcAddress(ev, "_FMOD_EventGroup_GetInfo@12");
    }
    Log("fmod exports: state=%p vol=%p cat=%p catvol=%p catinfo=%p parent=%p", s_getState, s_getVolume, s_getCategory,
        s_catGetVolume, s_catGetInfo, s_catGetParent);
    if (!WriteSlot(kIatEventStart, (void*)Hook_EventStart)) {
        Log("fmod Event::start import could not be written: no event logging, photo mode sounds or opponent engine volume");
        return;
    }
    if (lifecycleOk) InstallEngineVolume(ev);
    Log(g_cfg.log ? (lifecycleOk ? "fmod event logging on" : "fmod stateless event logging on")
                  : "fmod events watched (photo mode, opponent engine volume)");
}
