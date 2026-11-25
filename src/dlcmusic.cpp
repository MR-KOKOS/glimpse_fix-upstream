// Split/Second PC fix - [DLC] Music=1 (default): the converted DLC track Quarry plays its own race music, the
// DLC's FMOD project DLC3_Music (group Quarry_Track), as the Xbox 360 title update does.
//
// PC (Steam exe, static analysis):
// - Sound runs on its own thread ("Audio::Interface MULTITHREADED", 0x7ECAC0; loop 0x7EC9B0 -> 0x7EC410).
//   The game posts messages; the audio thread hands each to the handler table 0xD8B500 (filled by 0x835630,
//   called as handler(msg, size), cdecl, 0x834820). Message 9 (0x834A70) loads a level's sound: 0x7F1360
//   copies the level name, cut at the first '_' ("quarry_A" -> "quarry"), to the audio manager [0xD84294]
//   +0x4C4, sets the event aliases locale/ etc. and loads the level banks. Message 2 (0x8348E0) is the game
//   state 0x7F5A00: 3/4 (race end) picks the next race music group (0x7F2C20 -> 0x83D9F0), 8 (level unload)
//   clears +0x4C4.
// - Race music: Music/Music (stream project, 2 streams) is loaded once at start-up (0x7F5900 -> 0x7F3030).
//   The music picker [0xD843A8] plays "Music/%s/RaceSection0%d" (0x83D1E0), "Music/%s/ResultScreen",
//   "Music/PreRace/PreRaceShuffled" (0x83D020) and "Music/FinishStingers/..." (0x83D0A0) with %s = its group
//   name +0xAC (a char*: InGame_Track_01..15 from 0xD41898, or Survival_Track, Airstrike_Track (0xD418D8),
//   Tutorial_Track, Elite_Track). Every name goes through the alias table (0x8A45B0; 12 entries of 0x98
//   bytes, type 1 = prefix replaced), set by 0x8A5170 with an entry built by 0x7ED280 (type, from, to).
// - Streamed projects count against a budget: [0xD84394] +4 limit, +8 in use; 0x863450 refuses a load above
//   the limit. The limit is 6 (0x7F0D10 puts 6 into the init block at +0x5C, 0x863920 copies it) = Music 2 +
//   Global_Streamed 2 + the level's _Streamed bank 2: there is no room for DLC3_Music beside Music/Music.
// Xbox 360 title update 4 (SPLITSECOND1 TU4, Ghidra): the level load 0x88586378 compares the level name with
// "Quarry"; then 0x8857BBF8 unloads Music/Music and loads DLC3_Music/DLC3_Music (stream, 2) in its place
// (and the other way round on other levels), the picker gets a DLC flag (+0x80) and a project prefix
// (+0x1D0, "DLC3_Music" or "Music") that every music name starts with ("%s/%s/RaceSection0%d",
// "%s/PreRace/PreRaceShuffled", "%s/%s/ResultScreen", "%s/FinishStingers/..."), and the picker (0x885802C0)
// takes the group Quarry_Track (Survival_Track in the DLC's NemesisRace, Airstrike_Track_1..4 in
// KingOfTheAir). The Elite race names (ChampionshipRace...) stay "Music/...". (The Xbox flag 0x88ED0250 that allows the swap
// starts at 1 and is cleared after the first Quarry load, nothing found sets it again: there the DLC music
// may come only with the first Quarry race of a session. Here it comes with every Quarry race.)
//
// Here, without a code byte changed: handlers 9 and 2 of the audio thread's table are swapped for wrappers
// that call the game's handler and then, on the audio thread as the Xbox does it in its level load:
// - on the level quarry (not tutorial, not an Elite race): unload Music/Music (0x7F1310) and load
//   DLC3_Music/DLC3_Music (0x7F3030, 2 streams: the budget stays at its 6), when both of its files exist for
//   the game's file system (0x402300; loose files listed in StreamedAudioFileList.txt), and check with
//   FMOD that DLC3_Music has its events (EventSystem::getEvent, info only); add the alias "Music/" ->
//   "DLC3_Music/" (one entry, the same as the Xbox's prefix: every music name then comes from DLC3_Music);
//   set the picker's group to "Quarry_Track" whenever the game has picked an InGame_Track_xx (and
//   "Airstrike_Track" to Airstrike_Track_1..4 in turn; Survival_Track is in DLC3_Music as it is);
// - on any other level, or when the level is unloaded: remove the alias, unload DLC3_Music, load Music/Music
//   again, put back a group name of ours.
// FMOD's Event handles are checked handles: an event of an unloaded project answers FMOD_ERR_INVALID_HANDLE
// (tested with the game's fmod_event.dll); the categories (and the music volume) stay as they are.
// Only the level "quarry": nem_quarry (a level name of its own, not cut at its "_") keeps the base music.
// Music is local: nothing here differs between players online. Steam build only.
#include "common.h"
#include <string.h>

// ---------------------------------------------------------------- game (Steam 1.0.0.1)
typedef void(__cdecl* AudioMsg_t)(void* msg, int size);
static void** const kMsgTable = (void**)0x00D8B500;
static const int kMsgState = 2, kMsgLevel = 9;
static const DWORD kMsgStateFn = 0x008348E0, kMsgLevelFn = 0x00834A70;
static BYTE* const* const kAudioMgr = (BYTE* const*)0x00D84294;  // +0x4C4 level, +0xF tutorial, +0x12 Elite race
static BYTE* const* const kPicker = (BYTE* const*)0x00D843A8;    // music picker, +0xAC group name
static BYTE* const* const kStreams = (BYTE* const*)0x00D84394;   // stream budget: +4 limit, +8 in use
static BYTE* const* const kAudioCore = (BYTE* const*)0x00D91C48;  // +4 FMOD::EventSystem*, +8 FMOD::System*
static const char* const* const kInGameTracks = (const char* const*)0x00D41898;  // 14 group names
static const char* const* const kAirstrikeTrack = (const char* const*)0x00D418D8;

typedef bool(__cdecl* FileExists_t)(const char* path, int flags);                 // 0x402300
typedef bool(__stdcall* LoadStreamed_t)(const char* name, int streams);           // 0x7F3030 (ECX unused)
typedef void(__stdcall* UnloadStreamed_t)(const char* name);                      // 0x7F1310 (ECX unused)
typedef void*(__fastcall* AliasMake_t)(void* self, void*, int type, const char* from, const char* to);  // 0x7ED280
typedef void(__stdcall* AliasSet_t)(void* alias);                                  // 0x8A5170 (ECX unused)
typedef void(__fastcall* AliasFree_t)(void* self);                                 // 0x7ED2F0
typedef bool(__stdcall* LoadProject_t)(const char* name);                          // 0x7F2F20 (ECX unused)
static const FileExists_t FileExists = (FileExists_t)0x00402300;
static const LoadStreamed_t LoadStreamed = (LoadStreamed_t)0x007F3030;
static const UnloadStreamed_t UnloadStreamed = (UnloadStreamed_t)0x007F1310;
static const AliasMake_t AliasMake = (AliasMake_t)0x007ED280;
static const AliasSet_t AliasSet = (AliasSet_t)0x008A5170;
static const AliasFree_t AliasFree = (AliasFree_t)0x007ED2F0;
static const LoadProject_t LoadProject = (LoadProject_t)0x007F2F20;

struct CodeCheck { DWORD addr; const char* bytes; size_t n; };
// the two message handlers and the resident bank load: for the music swap and Deadline's sounds
static const CodeCheck kCode[] = {
    { 0x00834A70, "\x81\xEC\x0C\x01\x00\x00\x8B\x84\x24\x14\x01", 11 },
    { 0x008348E0, "\x8B\x44\x24\x08\x8B\x4C\x24\x04\x83\xEC\x14", 11 },
    { 0x007F2F20, "\x81\xEC\xC8\x00\x00\x00\x56\x8B\xB4\x24\xD0\x00", 12 },
};
// the file check, the streamed loads and the alias table: for the music swap only
static const CodeCheck kMusicCode[] = {
    { 0x00402300, "\x6A\xFF\x68\x98\xED\xB2\x00", 7 },
    { 0x007F3030, "\x81\xEC\xC8\x00\x00\x00\x56\x8B\xB4\x24\xD0\x00", 12 },
    { 0x007F1310, "\x8B\x44\x24\x04\x81\xEC\xC8\x00\x00\x00", 10 },
    { 0x007ED280, "\x6A\xFF\x68\xB3\x29\xB4\x00", 7 },
    { 0x007ED2F0, "\x6A\xFF\x68\xB3\x29\xB4\x00", 7 },
    { 0x008A5170, "\x53\x55\x8B\x6C\x24\x0C\x56\x57\x8D\x5D\x08", 11 },
};

// fmod_event.dll (C API, the game's EventSystem pointer)
typedef int(__stdcall* EsGetProject_t)(void* es, const char* name, void** project);
typedef int(__stdcall* EsGetEvent_t)(void* es, const char* name, unsigned mode, void** event);
typedef int(__stdcall* EvGetParentGroup_t)(void* event, void** group);
typedef int(__stdcall* GrGetParentProject_t)(void* group, void** project);
static EsGetProject_t s_esGetProject;
static EsGetEvent_t s_esGetEvent;
static EvGetParentGroup_t s_evGetParentGroup;
static GrGetParentProject_t s_grGetParentProject;
static const unsigned FMOD_EVENT_INFOONLY = 4;

static const char kDlcFev[] = "Audio(noassert)|Audio/Release/DLC3_Music/DLC3_Music.fev";
static const char kDlcFsb[] = "Audio(noassert)|Audio/Release/DLC3_Music/DLC3_Music.fsb";
static const char kQuarryTrack[] = "Quarry_Track";
static const char* const kAirstrikeN[] = { "Airstrike_Track_1", "Airstrike_Track_2", "Airstrike_Track_3",
                                           "Airstrike_Track_4" };

// ---------------------------------------------------------------- state (audio thread only, after install)
static AudioMsg_t s_origState, s_origLevel;
static bool s_installed, s_gaveUp;
static bool s_swapped;        // DLC3_Music in place of Music/Music, alias Music/ -> DLC3_Music/ set
static bool s_unavailable;    // DLC3_Music's files are not there for the game: nothing is done
static const char* s_ourGroup;     // the group name we put into the picker
static const char* s_theirGroup;   // what the game had picked
static int s_airstrike;
static void* s_dlcProject;
static DWORD s_audioThread;
static bool s_musicOn;        // the Quarry music swap is wanted
static bool s_menuOn;         // Deadline's sounds are wanted (DLCMenu)
static bool s_menuTried;      // DLCMenu was looked at (once per sound system)

static void* EventSystem()
{
    BYTE* core = *kAudioCore;
    return core ? *(void**)(core + 4) : NULL;
}

static void StreamBudget(int* limit, int* used)
{
    BYTE* b = *kStreams;
    *limit = b ? *(int*)(b + 4) : -1;
    *used = b ? *(int*)(b + 8) : -1;
}

static bool ProjectLoaded(const char* name, void** project)
{
    void* es = EventSystem();
    void* p = NULL;
    if (!es || !s_esGetProject || s_esGetProject(es, name, &p) != 0 || !p) return false;
    if (project) *project = p;
    return true;
}

// the game's alias table: type 1 = names starting with from get to in its place, type 0 = entry off
static void SetAlias(int type, const char* from, const char* to)
{
    alignas(8) BYTE entry[0xA0];
    AliasMake(entry, NULL, type, from, to);
    AliasSet(entry);
    AliasFree(entry);
}

// an entry of type 1 for this prefix in the alias table [0xD96ACC] (12 x 0x98: +4 type, +8 char* from)
static bool AliasPresent(const char* from)
{
    const BYTE* table = *(const BYTE* const*)0x00D96ACC;
    if (!table) return false;
    for (int i = 0; i < 12; i++) {
        const BYTE* e = table + i * 0x98;
        const char* f = *(const char* const*)(e + 8);
        if (*(const int*)(e + 4) == 1 && f && !strcmp(f, from)) return true;
    }
    return false;
}

static bool IsInGameTrack(const char* g)
{
    for (int i = 0; i < 14; i++)
        if (g == kInGameTracks[i]) return true;
    return false;
}

// the group the picker plays next: Quarry_Track for an ordinary race (as the TU picks it with the DLC flag)
static void ChooseGroup()
{
    BYTE* picker = *kPicker;
    if (!picker) return;
    const char** group = (const char**)(picker + 0xAC);
    const char* g = *group;
    if (!g || g == s_ourGroup) return;
    const char* mine = NULL;
    if (IsInGameTrack(g)) mine = kQuarryTrack;
    else if (g == *kAirstrikeTrack) mine = kAirstrikeN[s_airstrike++ & 3];
    if (!mine) return;
    s_theirGroup = g;
    s_ourGroup = mine;
    *group = mine;
    Log("DLC music: race music group %s -> %s (DLC3_Music)", g, mine);
}

static void RestoreGroup()
{
    BYTE* picker = *kPicker;
    if (picker && s_ourGroup && *(const char**)(picker + 0xAC) == s_ourGroup)
        *(const char**)(picker + 0xAC) = s_theirGroup;
    s_ourGroup = s_theirGroup = NULL;
}

// Music/Music unloaded by us and not yet back (for the exception path), and the streams in use right after
static bool s_musicOut;
static int s_usedWithoutMusic = -1;

// 0x863450 adds a project's streams to the budget before the FMOD load and keeps them when the load fails;
// the failed project's own count (+0x138) is only set after a successful load, so its unload takes nothing
// off. The budget goes back to the value before the load (a data write), then Music/Music comes back.
static bool MusicBack(int usedBefore)
{
    int limit, used;
    StreamBudget(&limit, &used);
    if (usedBefore >= 0 && used > usedBefore && *kStreams) {
        Log("DLC music: stream budget %d -> %d (streams of a failed load)", used, usedBefore);
        *(int*)(*kStreams + 8) = usedBefore;
    }
    StreamBudget(&limit, &used);
    int before = used;
    LoadStreamed("Music/Music", 2);
    s_musicOut = false;
    StreamBudget(&limit, &used);
    bool back = ProjectLoaded("Music", NULL);
    if (!back && used > before && *kStreams) *(int*)(*kStreams + 8) = used = before;  // the same for Music/Music
    Log("DLC music: Music/Music %s (streams in use %d of %d)", back ? "loaded again" : "DID NOT LOAD", used, limit);
    return back;
}

static void SwapIn(const char* level)
{
    int limit, used;
    StreamBudget(&limit, &used);
    if (!FileExists(kDlcFev, 0) || !FileExists(kDlcFsb, 0)) {
        s_unavailable = true;
        Log("DLC music: %s: DLC3_Music not found for the game (data\\audio\\release\\dlc3_music\\dlc3_music.fev/.fsb, "
            "or below glimpse_dlc\\quarry\\data, listed in Audio/StreamedAudioFileList.txt of the overlay) - the base "
            "race music plays", level);
        return;
    }
    if (!ProjectLoaded("Music", NULL)) {
        s_unavailable = true;
        Log("DLC music: %s: Music/Music is not loaded (streams in use %d of %d), nothing swapped", level, used, limit);
        return;
    }
    AudioDiagProjectsChanged();  // before every unload: the diagnostic must not keep a group across it
    UnloadStreamed("Music/Music");
    s_musicOut = true;
    int limit2, used2;
    StreamBudget(&limit2, &used2);
    s_usedWithoutMusic = used2;
    LoadStreamed("DLC3_Music/DLC3_Music", 2);
    int limit3, used3;
    StreamBudget(&limit3, &used3);
    void* es = EventSystem();
    void* evt = NULL;
    void* project = NULL;
    int r = es && s_esGetEvent ? s_esGetEvent(es, "DLC3_Music/Quarry_Track/RaceSection01", FMOD_EVENT_INFOONLY, &evt) : -1;
    if (r != 0 || !ProjectLoaded("DLC3_Music", &project)) {
        Log("DLC music: %s: DLC3_Music did not load (FMOD %d; streams %d -> %d -> %d of %d)", level, r, used, used2, used3,
            limit3);
        s_unavailable = true;
        AudioDiagProjectsChanged();
        UnloadStreamed("DLC3_Music/DLC3_Music");
        MusicBack(used2);
        AudioDiagProjectsChanged();
        return;
    }
    SetAlias(1, "Music/", "DLC3_Music/");
    if (!AliasPresent("Music/")) {  // the table's 12 entries taken: the game's Music/ names would find nothing
        Log("DLC music: %s: the alias Music/ -> DLC3_Music/ did not go in (alias table full?)", level);
        s_unavailable = true;
        AudioDiagProjectsChanged();
        UnloadStreamed("DLC3_Music/DLC3_Music");
        MusicBack(used2);
        AudioDiagProjectsChanged();
        return;
    }
    s_dlcProject = project;
    s_swapped = true;
    AudioDiagProjectsChanged();
    Log("DLC music: %s: Music/Music unloaded, DLC3_Music loaded in its place (streams in use %d -> %d -> %d, limit %d), "
        "alias Music/ -> DLC3_Music/ set",
        level, used, used2, used3, limit3);
}

static void SwapOut(const char* why)
{
    AudioDiagProjectsChanged();
    SetAlias(0, "Music/", "");
    RestoreGroup();
    UnloadStreamed("DLC3_Music/DLC3_Music");
    s_dlcProject = NULL;
    s_swapped = false;
    s_musicOut = true;
    int limit, used;
    StreamBudget(&limit, &used);
    s_usedWithoutMusic = used;
    Log("DLC music: %s: DLC3_Music unloaded (streams in use %d of %d)", why, used, limit);
    MusicBack(used);
    AudioDiagProjectsChanged();
}

// Deadline's sounds (dlcpickups.cpp plays DLCMenu/TrialPlus/... through the game's 2D sound call): Xbox TU4's level
// audio load (0x8857F188) loads Menu/DLCMenu when the mode is TimeTrialPlus. The PC's level message carries only the
// level name, so the bank is loaded at the first state message of a Deadline race (the race setup is the new one
// then), on this thread, with the call the game uses for its own resident banks at start-up (0x7F2F20, from
// 0x7F5900: "Audio(noassert)|Audio/Release/Menu/DLCMenu.fev", found in the merged resident.ark); it stays loaded
// for the session, as those do (about 100 KB). Once per sound system (ResetForNewSoundSystem clears it when the sound
// system is made anew and the messages are wrapped again); nothing is retried after a failure.
static void DeadlineMenuBank(const char* what)
{
    if (s_menuTried || !DeadlineRaceNow()) return;
    s_menuTried = true;
    if (ProjectLoaded("DLCMenu", NULL)) return;
    const bool ok = LoadProject("Menu/DLCMenu");
    void* es = EventSystem();
    void* evt = NULL;
    int r = es && s_esGetEvent ? s_esGetEvent(es, "DLCMenu/TrialPlus/Pickup", FMOD_EVENT_INFOONLY, &evt) : -1;
    Log("DLC modes: Deadline sounds (%s): Menu/DLCMenu %s, DLCMenu/TrialPlus/Pickup FMOD %d%s", what,
        !ok ? "not found for the game (Audio/Release/Menu/DLCMenu.fev/.fsb in the mode pack)"
            : ProjectLoaded("DLCMenu", NULL) ? "loaded" : "DID NOT LOAD", r, r ? " (no Deadline sounds)" : "");
}

// set by DlcMusicTick (game thread) when the handler table was filled again: the sound system was made anew, so
// nothing of the old one is loaded or swapped any more; the audio thread drops its state at its next message
static volatile LONG s_resetPending;

static void ResetForNewSoundSystem()
{
    const bool wasSwapped = s_swapped, wasOut = s_musicOut, wasOff = s_unavailable;
    s_swapped = false;
    s_musicOut = false;
    s_dlcProject = NULL;
    s_ourGroup = s_theirGroup = NULL;  // the new picker has none of ours
    s_unavailable = false;             // the new sound system is checked again at the next Quarry race
    s_usedWithoutMusic = -1;
    s_audioThread = 0;
    s_menuTried = false;               // the new sound system has no DLCMenu bank: loaded again at the next Deadline race
    Log("DLC music: the sound system was made anew: state dropped (was %s%s%s)", wasSwapped ? "swapped" : "not swapped",
        wasOut ? ", Music/Music out" : "", wasOff ? ", off" : "");
}

// audio thread, after the game's handler of a level load or a state change
static void Sync(const char* what)
{
    if (InterlockedExchange(&s_resetPending, 0)) ResetForNewSoundSystem();  // before anything reads the old state
    if (!s_audioThread) {
        s_audioThread = GetCurrentThreadId();
        Log("DLC music: audio thread %lu (%s)", s_audioThread, what);
    }
    if (s_menuOn) DeadlineMenuBank(what);
    if (!s_musicOn) return;
    BYTE* mgr = *kAudioMgr;
    if (!mgr) return;
    const char* level = (const char*)(mgr + 0x4C4);
    bool quarry = !_stricmp(level, "quarry");
    bool want = quarry && !s_unavailable && !mgr[0xF] && !mgr[0x12];
    if (want && !s_swapped) SwapIn(level);
    else if (!want && s_swapped) SwapOut(level[0] ? level : "level unloaded");
    else if (quarry && !s_swapped && !s_unavailable && g_cfg.log) {
        static bool logged;
        if (!logged) Log("DLC music: quarry in a %s: base race music", mgr[0xF] ? "tutorial" : "Elite race");
        logged = true;
    }
    if (s_swapped) ChooseGroup();
}

static void SafeSync(const char* what)
{
    DWORD code = 0;
    __try {
        Sync(what);
    } __except (code = GetExceptionCode(), EXCEPTION_EXECUTE_HANDLER) {
        s_unavailable = true;
    }
    if (!code) return;
    Log("DLC music: exception %08lX in %s, off for this session", code, what);
    // Music/Music unloaded by us and not back: the budget as it was without it, Music/Music again
    __try {
        if (s_musicOut && !s_swapped) {  // (swapped: the next message swaps back as usual)
            AudioDiagProjectsChanged();
            SetAlias(0, "Music/", "");
            UnloadStreamed("DLC3_Music/DLC3_Music");  // nothing happens when it is not loaded
            s_dlcProject = NULL;
            MusicBack(s_usedWithoutMusic);
            AudioDiagProjectsChanged();
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("DLC music: exception while loading Music/Music again");
    }
}

static void __cdecl Hook_LevelMsg(void* msg, int size)
{
    s_origLevel(msg, size);
    SafeSync("level load");
}

static void __cdecl Hook_StateMsg(void* msg, int size)
{
    s_origState(msg, size);
    SafeSync("state change");
}

// ---------------------------------------------------------------- install (game thread)
bool DlcMusicIsDlcEvent(void* evt)
{
    void* project = s_dlcProject;
    if (!project || !evt || !s_evGetParentGroup || !s_grGetParentProject) return false;
    void* group = NULL;
    void* p = NULL;
    return s_evGetParentGroup(evt, &group) == 0 && group && s_grGetParentProject(group, &p) == 0 && p == project;
}

static bool CodeAsExpected(const CodeCheck* code, size_t n, const char* off)
{
    __try {
        for (size_t i = 0; i < n; i++)
            if (memcmp((const void*)code[i].addr, code[i].bytes, code[i].n)) {
                Log("DLC music: code at 0x%08lX not as expected, %s", code[i].addr, off);
                return false;
            }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
    return true;
}

// the music swap's own code and the picker's group names; a mismatch turns off only the swap
static bool MusicCodeAsExpected()
{
    if (!CodeAsExpected(kMusicCode, sizeof(kMusicCode) / sizeof(kMusicCode[0]), "no Quarry music")) return false;
    __try {
        const char* t = kInGameTracks[0];
        const char* a = *kAirstrikeTrack;
        if (IsBadStringPtrA(t, 32) || strcmp(t, "InGame_Track_01") || IsBadStringPtrA(a, 32) || strcmp(a, "Airstrike_Track")) {
            Log("DLC music: picker group names not as expected, no Quarry music");
            return false;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
    return true;
}

// what a failure leaves out, for the log
static const char* OffText(bool music, bool menu)
{
    return music && menu ? "no Quarry music and no Deadline sounds" : music ? "no Quarry music" : "no Deadline sounds";
}

// per input frame until the audio thread's handler table is filled (0x835630 runs when the sound starts)
void DlcMusicTick(bool music, bool menu)
{
    if (s_installed) {
        // the table filled again (the sound system made anew): wrap again; the new sound system has no DLCMenu bank,
        // so Deadline's sounds are loaded again at the next Deadline race
        // the reset is asked for before the slots point at the wrappers again: the first wrapped message drops the old state
        if ((DWORD)kMsgTable[kMsgLevel] == kMsgLevelFn || (DWORD)kMsgTable[kMsgState] == kMsgStateFn)
            InterlockedExchange(&s_resetPending, 1);
        bool again = false;
        if ((DWORD)kMsgTable[kMsgLevel] == kMsgLevelFn &&
            InterlockedCompareExchangePointer(&kMsgTable[kMsgLevel], (void*)Hook_LevelMsg, (void*)kMsgLevelFn) ==
                (void*)kMsgLevelFn) {
            Log("DLC music: audio message 9 wrapped again");
            again = true;
        }
        if ((DWORD)kMsgTable[kMsgState] == kMsgStateFn &&
            InterlockedCompareExchangePointer(&kMsgTable[kMsgState], (void*)Hook_StateMsg, (void*)kMsgStateFn) ==
                (void*)kMsgStateFn) {
            Log("DLC music: audio message 2 wrapped again");
            again = true;
        }
        if (again)
            Log("DLC modes: Deadline sounds: the sound system was made anew, DLCMenu is loaded again at the next Deadline race");
        return;
    }
    if (s_gaveUp || !(music || menu)) return;
    s_musicOn = music;
    s_menuOn = menu;
    const char* off = OffText(music, menu);
    if (CurrentBuild() != BUILD_STEAM) {
        s_gaveUp = true;
        Log("DLC music: not on this build (%s), %s", BuildName(), off);
        return;
    }
    if ((DWORD)kMsgTable[kMsgLevel] != kMsgLevelFn || (DWORD)kMsgTable[kMsgState] != kMsgStateFn) return;
    s_gaveUp = true;  // one try
    if (!CodeAsExpected(kCode, sizeof(kCode) / sizeof(kCode[0]), off)) return;
    if (s_musicOn && !MusicCodeAsExpected()) s_musicOn = false;  // Deadline's sounds may still load
    if (!s_musicOn && !s_menuOn) return;
    off = OffText(s_musicOn, s_menuOn);
    HMODULE ev = GetModuleHandleA("fmod_event.dll");
    if (ev) {
        s_esGetProject = (EsGetProject_t)GetProcAddress(ev, "_FMOD_EventSystem_GetProject@12");
        s_esGetEvent = (EsGetEvent_t)GetProcAddress(ev, "_FMOD_EventSystem_GetEvent@16");
        s_evGetParentGroup = (EvGetParentGroup_t)GetProcAddress(ev, "_FMOD_Event_GetParentGroup@8");
        s_grGetParentProject = (GrGetParentProject_t)GetProcAddress(ev, "_FMOD_EventGroup_GetParentProject@8");
    }
    if (!s_esGetProject || !s_esGetEvent) {
        Log("DLC music: fmod_event.dll exports not found, %s", off);
        return;
    }
    // the originals are stored before the slots point at the wrappers; the audio thread reads a slot per message
    s_origLevel = (AudioMsg_t)kMsgLevelFn;
    s_origState = (AudioMsg_t)kMsgStateFn;
    if (InterlockedCompareExchangePointer(&kMsgTable[kMsgLevel], (void*)Hook_LevelMsg, (void*)kMsgLevelFn) != (void*)kMsgLevelFn)
        return;
    if (InterlockedCompareExchangePointer(&kMsgTable[kMsgState], (void*)Hook_StateMsg, (void*)kMsgStateFn) != (void*)kMsgStateFn) {
        InterlockedExchangePointer(&kMsgTable[kMsgLevel], (void*)kMsgLevelFn);
        return;
    }
    s_installed = true;
    if (!s_musicOn) {
        Log("DLC music: audio messages 9 (level) and 2 (state) wrapped (table 0xD8B500) for Deadline's sounds only "
            "(DLCMenu at the first Deadline race); no music swap");
        return;
    }
    int limit, used;
    StreamBudget(&limit, &used);
    Log("DLC music: audio messages 9 (level) and 2 (state) wrapped (table 0xD8B500); on Quarry DLC3_Music takes "
        "Music/Music's place (streams in use %d of %d)%s",
        used, limit, s_menuOn ? "; DLCMenu at the first Deadline race" : "");
}
