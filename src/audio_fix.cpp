// Split/Second PC fix - audio faults of the PC release
//
// Opponent engines. Every opponent's engine is a sound project of its own, loaded into one of seven
// "CPUCar" sound bank slots. Four faults keep opponents silent or mismatched on PC:
// 1. Slot size: when the audio system starts (0x7F5900) it makes those slots with [0xD3DBC0] bytes each
//    (0x19999, 104857: the Xbox 360's size, whose car banks are 57..94 KB of XMA); the PC's banks are MP2
//    and 145..203 KB, so the loader (0x8695D0) refuses them. Raised to 256 KB at DLL load (data only).
//    Found by the Back on Air project (github.com/xenophobentx/back-on-air) and checked against this build.
// 2. Event capacity: AudioInit (0x7F0D65) sizes the EventMgr table and its object pool to 511.
//    Raising only the table lets RegisterEvent (0x867CD0) reach the locked pool with no free objects.
//    Reserve 2048 objects in LLAudio2 before raising the table to match. See docs/TECHNICAL.md.
// 3. Multi-race reload: at race end message 51 (0x835620 -> 0x7F3400) unloads "CPUCar" via 0x865980 ->
//    0x86DD00, which releases each FMOD project without moving its record from the group's used list
//    (+0x128) back to free (+0x118). In later races any repeated model matches the stale record, gets
//    marked kept (+0x641) and skips reload. Unloaded records are moved back to free after message 51 and
//    before message 43.
// 4. Random car mismatch: message 43 (0x835340 -> 0x7F30C0) carries every racer's actual AudioFile from
//    VehicleDatabase.xml, yet ignores it for CPU racers and picks randomly from a 22-entry table (0xD3E428,
//    which even names missing Truck_07). All 27 base-game AudioFiles (covering 84/84 base cars) and 3/3 Elite
//    DLC cars map to the 30 shipped Vehicles/CPU/<name> banks (each with maxPlaybacks = 7), so duplicate cars
//    share one bank slot and play independently. Four Deadline DLC cars ship .tonic/.tonic2 binaries byte-identical
//    to base cars and use those CPU banks. High Octane cars have individual fallback banks. Other cars
//    without a match use an available stock bank, preferably from the same family. Steam build only for hooks 2..4.
#include "common.h"
#include <stdio.h>
#include <stdarg.h>
#include <ctype.h>
#include <string.h>

void FixOpponentEngineBanks()
{
    if (!g_cfg.opponentEngines) return;
    if (CurrentBuild() == BUILD_UNKNOWN) {
        Log("opponent engines: only on the known Steam builds");
        return;
    }
    DWORD* slot = (DWORD*)G(0x00D3DBC0);
    const DWORD kGame = 0x19999, kWanted = 256 * 1024;
    if (*slot != kGame) {
        Log("opponent engines: bank slot size %lu, not the expected %lu, left alone", *slot, kGame);
        return;
    }
    DWORD value = kWanted;
    WriteMemory(slot, &value, sizeof(value));
    Log("opponent engines: sound bank slots %lu -> %lu bytes", kGame, kWanted);
}

// ---- opponent engines: EventMgr capacity, CPUCar slot cleanup, car-to-engine matching (Steam build)

typedef void(__cdecl* AudioMsg_t)(void* msg, int size);
typedef void*(__cdecl* GameAlloc_t)(size_t bytes);
typedef void(__cdecl* GameFree_t)(void* ptr);
typedef void(__fastcall* GrowEventPool_t)(void* pool, void*, DWORD count);
typedef void*(__fastcall* HeapScopePush_t)(void* scope, void*, const char* name, int flag);
typedef void(__fastcall* HeapScopePop_t)(void* scope);
typedef bool(__cdecl* FileExists_t)(const char* path, int flags);
typedef bool(__stdcall* LoadBankToGroup_t)(const char* bankPath, const char* groupName);
typedef void(__fastcall* SetRacerEngineName_t)(void* mgr, void*, const char* eventName, int racerIdx);
typedef int(__stdcall* EsGetProject_t)(void* es, const char* name, void** project);

static void** const kAudioMsgTable = (void**)0x00D8B500;
static const int kMsgRacers = 43, kMsgRaceUnload = 51;
static const DWORD kMsgRacersFn = 0x00835340, kMsgRaceUnloadFn = 0x00835620;
static BYTE* const* const kAudioMgrPtr = (BYTE* const*)0x00D84294;
static void* const* const kRacerEngineMgr = (void* const*)0x00D84298;
static BYTE* const* const kAudioCorePtr = (BYTE* const*)0x00D91C48;
static BYTE* const* const kEventMgrPtr = (BYTE* const*)0x00D91C50;
static BYTE* const* const kSoundbankMgrPtr = (BYTE* const*)0x00D91C58;
static const DWORD kEventMgrVtable = 0x00C2B3D4;
static const DWORD kStockEventCap = 511;
static const DWORD kWantedEventCap = 2048;
static BYTE* const kEventPool = (BYTE*)0x00D4390C;

static const GameAlloc_t kGameAlloc = (GameAlloc_t)0x00530290;
static const GameFree_t kGameFree = (GameFree_t)0x005302D0;
static const GrowEventPool_t kGrowEventPool = (GrowEventPool_t)0x005081D0;
static const HeapScopePush_t kHeapScopePush = (HeapScopePush_t)0x00AF4D00;
static const HeapScopePop_t kHeapScopePop = (HeapScopePop_t)0x00AF48F0;
static const char* const kAudioHeapName = (const char*)0x00C2AD44;  // "LLAudio2"
static const FileExists_t kFileExists = (FileExists_t)0x00402300;
static const LoadBankToGroup_t kLoadBankToGroup = (LoadBankToGroup_t)0x007F2FA0;
static const SetRacerEngineName_t kSetRacerEngineName = (SetRacerEngineName_t)0x008359A0;

static AudioMsg_t s_origMsg43, s_origMsg51;
static EsGetProject_t s_esGetProject;
static bool s_msg43Installed, s_msg51Installed, s_engHooksGaveUp;
static volatile LONG s_msg51Mode;  // published before Hook_Msg51: 0 none, 1 placement-only, 2 engines+placement
static LONG s_audioSystemGen;      // monotonic FMOD::System generation (audio thread only)
static void* s_audioSystem;        // active FMOD::System* owning all cached handles (audio thread only)
static bool s_setFileSystemWrapped;  // System::setFileSystem IAT wrapped (authoritative pre-registration init signal)
static bool s_voiceAttachPatched;  // the voice units attach to their voices (FixGameDspEffects)
static bool s_raceMatched;         // Msg 43 ran since the last Msg 51 (suppresses boot-time unload peak log)
static int s_allocChecked;         // 0 unchecked, 1 ok, -1 bad
static DWORD s_eventPeak, s_eventPeakCap;

// DLC alternatives keyed by VehicleDatabase AudioFile. Deadline's Tonic recordings are byte-identical
// to its donors. High Octane uses stand-ins from related base cars or, for the Katana, a recording that
// shares part of its Tonic set. See docs/HIGH_OCTANE_AUDIO.md.
static const struct { const char* audioFile; const char* cpu; const char* reason; } kDlcCpuBanks[] = {
    { "Hypercar_20", "Hypercar_04", "identical-tonic" },  // Cobretti Centaur
    { "Muscle_20",   "Muscle_14",   "identical-tonic" },  // Hanzo Kanobo
    { "Truck_20",    "Truck_08",    "identical-tonic" },  // Ryback Javelin
    { "Truck_21",    "Truck_06",    "identical-tonic" },  // Ryback Coyote Special
    { "Supercar_20", "Supercar_04", "High Octane fallback" },  // Ryback Cyclone Special
    { "Supercar_21", "Supercar_07", "High Octane fallback" },  // Cobretti Severus
    { "Supercar_24", "Muscle_24",   "High Octane fallback" },  // Ryback Vulcan
    { "Supercar_26", "Muscle_05",   "High Octane fallback" },  // Hanzo Katana
};

// Stock message 43's bank order (0xD3E428 on Steam). Check the VFS because some entries, such as Truck_07,
// are missing in the shipped archive.
static const char* const kStockCpuBanks[] = {
    "Hypercar_03", "Hypercar_04", "Hypercar_06", "Hypercar_07",
    "Muscle_03", "Muscle_05", "Muscle_08", "Muscle_14", "Muscle_22", "Muscle_24", "Muscle_25",
    "Supercar_04", "Supercar_07", "Supercar_08", "Supercar_09", "Supercar_10", "Supercar_11",
    "Truck_03", "Truck_06", "Truck_07", "Truck_08", "Truck_09",
};

static void SoundPlacementRaceUnload();

static bool AllocCodeOk()
{
    if (s_allocChecked) return s_allocChecked > 0;
    static const BYTE kAllocSig[12] = { 0x56, 0xE8, 0xAA, 0x9F, 0x01, 0x00, 0xE8, 0x95, 0xEE, 0xFF, 0xFF, 0x8B };
    static const BYTE kFreeSig[12] = { 0x57, 0x8B, 0x7C, 0x24, 0x08, 0x85, 0xFF, 0x74, 0x2E, 0x56, 0xE8, 0x51 };
    static const BYTE kPushSig[12] = { 0x8B, 0x44, 0x24, 0x04, 0x53, 0x55, 0x56, 0x57, 0x8B, 0xE9, 0x8B, 0x0D };
    static const BYTE kPopSig[12] = { 0xE8, 0x3B, 0xA8, 0xA3, 0xFF, 0x8B, 0xC8, 0xE9, 0xE4, 0x49, 0xA5, 0xFF };
    static const BYTE kGrowSig[12] = { 0x56, 0x8B, 0xF1, 0x8B, 0x56, 0x1C, 0x8B, 0x46, 0x0C, 0x8B, 0x40, 0x04 };
    static const BYTE kPoolAllocSig[12] = { 0x56, 0x8B, 0xF1, 0xF6, 0x46, 0x04, 0x01, 0x75, 0x43, 0x83, 0x7E, 0x08 };
    bool ok = CodeMatches((void*)0x00530290, kAllocSig, sizeof(kAllocSig)) &&
              CodeMatches((void*)0x005302D0, kFreeSig, sizeof(kFreeSig)) &&
              CodeMatches((void*)0x00AF4D00, kPushSig, sizeof(kPushSig)) &&
              CodeMatches((void*)0x00AF48F0, kPopSig, sizeof(kPopSig)) &&
              CodeMatches((void*)0x005081D0, kGrowSig, sizeof(kGrowSig)) &&
              CodeMatches((void*)0x00410D00, kPoolAllocSig, sizeof(kPoolAllocSig)) &&
              !IsBadStringPtrA(kAudioHeapName, 16) && !strcmp(kAudioHeapName, "LLAudio2");
    s_allocChecked = ok ? 1 : -1;
    if (!ok) Log("opponent engines: allocator signature mismatch, event table kept at stock size");
    return ok;
}

// The stock constructor uses 0x5081D0 to reserve objects, then 0x86F2F0 sets allocator flag bit 0.
// With that bit set 0x410D00 returns NULL. Reserve a chunk with the stock layout and restore the
// flag before message 43 can queue banks. The verified active-heap allocator is used with heap ID 2.
static bool ReserveEventPool()
{
    DWORD& capacity = *(DWORD*)(kEventPool + 0x24);
    if (capacity == kWantedEventCap) return true;
    DWORD& flags = *(DWORD*)(kEventPool + 0x10);
    const DWORD allocatorVtable = *(DWORD*)(kEventPool + 0x0C);
    if (capacity != kStockEventCap || *(DWORD*)(kEventPool + 0x20) > capacity ||
        *(DWORD*)(kEventPool + 0x1C) != 0xCC || allocatorVtable != 0x00BA29B0 ||
        *(DWORD*)(allocatorVtable + 4) != 0x00410D00 || flags != 3 || *(DWORD*)(kEventPool + 0x14) != 2) {
        return false;
    }

    flags = 2;
    __try {
        kGrowEventPool(kEventPool, NULL, kWantedEventCap - capacity);
    } __finally {
        flags = 3;
    }
    return capacity == kWantedEventCap;
}

static void* ResizeEventMgrInHeap(BYTE* em, DWORD cnt, void* oldData)
{
    BYTE scope[8] = {};
    void* newData = NULL;
    kHeapScopePush(scope, NULL, kAudioHeapName, 0);
    __try {
        if (!ReserveEventPool()) {
            static bool logged;
            if (!logged) Log("opponent engines: event pool reserve failed, event table kept at stock size");
            logged = true;
            return NULL;
        }
        newData = kGameAlloc(kWantedEventCap * 8);
        if (newData) {
            if (cnt) memcpy(newData, oldData, cnt * 8);
            *(void**)(em + 0x10) = newData;
            *(DWORD*)(em + 0x08) = kWantedEventCap;
            kGameFree(oldData);
        }
    } __finally {
        kHeapScopePop(scope);
    }
    return newData;
}

// audio thread message handler only (never inside EventMgr::Update / Event::start, which holds an iterator
// into em->data): raise EventMgr's stock 511-event cap to 2048 inside the persistent "LLAudio2" heap
static void EnsureEventMgrCapacity()
{
    if (!g_cfg.opponentEngines || CurrentBuild() != BUILD_STEAM) return;
    __try {
        BYTE* em = *kEventMgrPtr;
        if (!em || *(DWORD*)em != kEventMgrVtable) return;
        DWORD cap = *(DWORD*)(em + 0x08);
        DWORD cnt = *(DWORD*)(em + 0x0C);
        void* oldData = *(void**)(em + 0x10);
        if (cap != kStockEventCap || cnt > cap || !oldData) return;
        if (!AllocCodeOk()) return;
        void* newData = ResizeEventMgrInHeap(em, cnt, oldData);
        if (!newData) {
            static bool logged;
            if (!logged) Log("opponent engines: could not grow event table (cap %lu)", cap);
            logged = true;
            return;
        }
        Log("opponent engines: event table and object pool %lu -> %lu entries (%lu table entries in use)",
            cap, kWantedEventCap, cnt);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        static bool logged;
        if (!logged) Log("opponent engines: exception resizing event table");
        logged = true;
    }
}

void AudioFixNoteEventPeak()
{
    if (!g_cfg.log || !g_cfg.opponentEngines || CurrentBuild() != BUILD_STEAM) return;
    BYTE* em = *kEventMgrPtr;
    if (!em || *(DWORD*)em != kEventMgrVtable) return;
    DWORD cnt = *(DWORD*)(em + 0x0C);
    DWORD cap = *(DWORD*)(em + 0x08);
    if (cnt > s_eventPeak) {
        s_eventPeak = cnt;
        s_eventPeakCap = cap;
    }
}

enum CpuProjState { CPU_PROJ_UNKNOWN, CPU_PROJ_LOADED, CPU_PROJ_ABSENT };

// Query FMOD EventSystemI::getProject (fmod_event.dll 4.28, 0x1002A9A0):
// returns 0 with non-NULL project when loaded, 37 (0x25) with NULL project when confirmed absent,
// and 31 (0x1F) when uninitialized. Only CPU_PROJ_ABSENT permits reclaiming a CPUCar slot.
static CpuProjState QueryCpuProject(const char* name)
{
    if (!name || !*name || !s_esGetProject) return CPU_PROJ_UNKNOWN;
    BYTE* core = *kAudioCorePtr;
    void* es = core ? *(void**)(core + 4) : NULL;
    if (!es) return CPU_PROJ_UNKNOWN;
    void* proj = NULL;
    int r = s_esGetProject(es, name, &proj);
    if (r == 0 && proj != NULL) return CPU_PROJ_LOADED;
    if (r == 37 && proj == NULL) return CPU_PROJ_ABSENT;
    return CPU_PROJ_UNKNOWN;
}

// Move any unloaded CPUCar records from group's used list (+0x128) back to free (+0x118), clearing kept (+0x641).
// Live projects (e.g. during a race restart) and unreadable/unknown queries stay in used.
static void ReclaimUnloadedCpuCarSlots(const char* why)
{
    __try {
        BYTE* mgr = *kSoundbankMgrPtr;
        if (!mgr) return;
        BYTE* g = *(BYTE**)(mgr + 0x0C);
        BYTE* end = mgr + 0x10;
        BYTE* cpuGroup = NULL;
        for (int i = 0; i < 64 && g && g != end; i++, g = *(BYTE**)g) {
            const char* name = *(const char* const*)(g + 0x08);
            if (name && !IsBadStringPtrA(name, 32) && !_stricmp(name, "CPUCar")) {
                cpuGroup = g;
                break;
            }
        }
        if (!cpuGroup) return;
        BYTE** freeHead = (BYTE**)(cpuGroup + 0x118);
        BYTE* freeSentinel = cpuGroup + 0x11C;
        BYTE** freeTail = (BYTE**)(cpuGroup + 0x120);
        DWORD* freeCount = (DWORD*)(cpuGroup + 0x124);
        BYTE** usedHead = (BYTE**)(cpuGroup + 0x128);
        BYTE* usedSentinel = cpuGroup + 0x12C;
        DWORD* usedCount = (DWORD*)(cpuGroup + 0x134);

        int reclaimed = 0;
        BYTE* r = *usedHead;
        for (int guard = 0; guard < 32 && r && r != usedSentinel; guard++) {
            BYTE* next = *(BYTE**)(r + 0x00);
            const char* path = *(const char* const*)(r + 0x08);
            if (!path || IsBadStringPtrA(path, 256) || !*path) {
                static bool loggedBad;
                if (!loggedBad) {
                    loggedBad = true;
                    Log("opponent engines: unreadable CPUCar slot path (%s), record kept in used", why);
                }
                r = next;
                continue;
            }
            const char* base = path;
            for (const char* c = path; *c; c++)
                if (*c == '/' || *c == '\\') base = c + 1;
            CpuProjState st = QueryCpuProject(base);
            if (st == CPU_PROJ_UNKNOWN) {
                static bool loggedUnknown;
                if (!loggedUnknown) {
                    loggedUnknown = true;
                    Log("opponent engines: EventSystem::getProject indeterminate for '%s' (%s), record kept in used",
                        base, why);
                }
            } else if (st == CPU_PROJ_ABSENT) {
                BYTE* prev = *(BYTE**)(r + 0x04);
                if (*usedHead == r) *usedHead = next;
                *(BYTE**)(next + 0x04) = prev;  // when next == usedSentinel, next+4 is *usedTail
                if (prev) *(BYTE**)(prev + 0x00) = next;
                if (*usedCount > 0) (*usedCount)--;
                r[0x641] = 0;

                BYTE* ft = *freeTail;
                *(BYTE**)(r + 0x00) = freeSentinel;
                *(BYTE**)(r + 0x04) = ft;
                if (ft) *(BYTE**)(ft + 0x00) = r;
                else *freeHead = r;
                *freeTail = r;
                (*freeCount)++;
                reclaimed++;
            }
            r = next;
        }
        if (reclaimed > 0 && g_cfg.log)
            Log("opponent engines: reclaimed %d unloaded CPUCar slot(s) (%s; used %lu, free %lu)", reclaimed, why,
                *usedCount, *freeCount);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        static bool logged;
        if (!logged) Log("opponent engines: exception reclaiming CPUCar slots (%s)", why);
        logged = true;
    }
}

static bool CpuBankExists(const char* model)
{
    if (!model || !*model) return false;
    char path[200];
    _snprintf_s(path, _TRUNCATE, "Audio(noassert)|Audio/Release/Vehicles/CPU/%s.fev", model);
    return kFileExists(path, 0);
}

static const char* ResolveCpuEngine(const char* carAudio, const char** how)
{
    if (carAudio && *carAudio && CpuBankExists(carAudio)) {
        *how = "exact";
        return carAudio;
    }
    if (carAudio && *carAudio) {
        for (const auto& f : kDlcCpuBanks)
            if (!_stricmp(carAudio, f.audioFile) && CpuBankExists(f.cpu)) {
                *how = f.reason;
                return f.cpu;
            }
    }
    const char* familyEnd = carAudio ? strchr(carAudio, '_') : NULL;
    const size_t familyLength = familyEnd ? static_cast<size_t>(familyEnd - carAudio) + 1 : 0;
    const char* stockFallback = NULL;
    for (const char* stock : kStockCpuBanks) {
        const bool sameFamily = familyLength && !_strnicmp(carAudio, stock, familyLength);
        if ((sameFamily || !stockFallback) && CpuBankExists(stock)) {
            if (sameFamily) {
                *how = "same-family fallback";
                return stock;
            }
            stockFallback = stock;
        }
    }
    *how = stockFallback ? "stock fallback" : "uncovered";
    return stockFallback;
}

static void __cdecl Hook_Msg51(void* msg, int size)
{
    const bool withEngines = (InterlockedCompareExchange(&s_msg51Mode, 0, 0) == 2);
    if (withEngines) {
        EnsureEventMgrCapacity();
        AudioFixNoteEventPeak();
        if (g_cfg.log && s_raceMatched && s_eventPeak)
            Log("opponent engines: event table sampled high-water %lu/%lu before race unload", s_eventPeak, s_eventPeakCap);
        s_eventPeak = 0;
        s_eventPeakCap = 0;
        s_raceMatched = false;
    }
    SoundPlacementRaceUnload();
    AudioDiagProjectsChanged();
    s_origMsg51(msg, size);
    if (withEngines) ReclaimUnloadedCpuCarSlots("race unload");
}

static void __cdecl Hook_Msg43(void* msg, int size)
{
    EnsureEventMgrCapacity();
    ReclaimUnloadedCpuCarSlots("before load");
    s_raceMatched = true;

    BYTE* audioMgr = *kAudioMgrPtr;
    // Tutorial (+0x0F, set at 0x7F17F1) and Survival/Nemesis (+0x0D, set at 0x7F1871) load their own seven
    // fixed banks in stock 0x7F3341..0x7F33BF; leave those modes on the stock handler.
    if (!msg || size < 12 || size > 1024 || (audioMgr && (audioMgr[0x0F] || audioMgr[0x0D]))) {
        s_origMsg43(msg, size);
        return;
    }

    const BYTE* raw = (const BYTE*)msg;
    int count = *(const int*)(raw + 8);
    if (count < 0) count = 0;
    if (count > 8) count = 8;
    if (12 + count * 0x70 > size) {
        s_origMsg43(msg, size);
        return;
    }

    bool hasCpu = false;
    for (int i = 0; i < count; i++) {
        const BYTE* e = raw + 12 + i * 0x70;
        if (e[0x6D] && !e[0x6C]) hasCpu = true;
    }
    if (!hasCpu) {
        s_origMsg43(msg, size);
        return;
    }

    // Run stock handler with CPU entries hidden so racer count, split-screen mix and human Tonic
    // engines run untouched without the stock random CPU bank picker.
    BYTE copy[1024];
    memcpy(copy, raw, (size_t)size);
    for (int i = 0; i < count; i++) {
        BYTE* e = copy + 12 + i * 0x70;
        if (e[0x6D] && !e[0x6C]) e[0x6D] = 0;
    }
    s_origMsg43(copy, size);

    void* racerMgr = *kRacerEngineMgr;
    char queued[8][105];
    bool queuedOk[8];
    int nQueued = 0;
    if (g_cfg.log) {
        BYTE* em = *kEventMgrPtr;
        DWORD emCnt = em && *(DWORD*)em == kEventMgrVtable ? *(DWORD*)(em + 0x0C) : 0;
        DWORD emCap = em && *(DWORD*)em == kEventMgrVtable ? *(DWORD*)(em + 0x08) : 0;
        Log("opponent engines: matching %d racer slots (event table %lu/%lu)", count, emCnt, emCap);
    }

    for (int i = 0; i < count; i++) {
        const BYTE* e = raw + 12 + i * 0x70;
        if (!e[0x6D] || e[0x6C]) continue;
        char carAudio[105];
        memcpy(carAudio, e, 104);
        carAudio[104] = 0;
        const char* how = "";
        const char* model = ResolveCpuEngine(carAudio, &how);
        if (!model) {
            if (racerMgr) kSetRacerEngineName(racerMgr, NULL, "", i);
            Log("opponent engines: slot %d car '%s' found no available CPU bank (left unassigned)", i, carAudio);
            continue;
        }
        int qIdx = -1;
        for (int q = 0; q < nQueued; q++)
            if (!_stricmp(queued[q], model)) {
                qIdx = q;
                break;
            }
        char bankPath[128], evName[128];
        bool namesOk = _snprintf_s(bankPath, _TRUNCATE, "Vehicles/CPU/%s", model) >= 0 &&
                       _snprintf_s(evName, _TRUNCATE, "%s/Tonic/%s", model, model) >= 0;
        bool loadedOk = false;
        if (!namesOk) {
            loadedOk = false;
        } else if (qIdx >= 0) {
            loadedOk = queuedOk[qIdx];
        } else if (nQueued < 8) {
            loadedOk = kLoadBankToGroup(bankPath, "CPUCar");
            lstrcpynA(queued[nQueued], model, sizeof(queued[0]));
            queuedOk[nQueued++] = loadedOk;
        }
        if (racerMgr) kSetRacerEngineName(racerMgr, NULL, loadedOk ? evName : "", i);
        if (g_cfg.log || strcmp(how, "exact") != 0 || !loadedOk)
            Log("opponent engines: slot %d car '%s' -> '%s' (%s, %s%s)", i, carAudio, model, how,
                qIdx >= 0 ? "shared" : "queued", loadedOk ? "" : ", LOAD FAILED (left unassigned)");
    }
    AudioFixNoteEventPeak();
}

// per input frame: wrap audio message 51 (race-unload cleanup for OpponentEngines and/or SoundPlacement)
// and message 43 (car matching for OpponentEngines) once the handler table is filled
void OpponentEnginesTick()
{
    const bool want43 = g_cfg.opponentEngines;
    const bool want51 = g_cfg.opponentEngines || s_voiceAttachPatched;
    if (!want51) return;
    if (s_msg51Installed) {
        if (s_msg43Installed && (DWORD)kAudioMsgTable[kMsgRacers] == kMsgRacersFn &&
            InterlockedCompareExchangePointer(&kAudioMsgTable[kMsgRacers], (void*)Hook_Msg43, (void*)kMsgRacersFn) ==
                (void*)kMsgRacersFn)
            Log("opponent engines: audio message 43 wrapped again");
        if ((DWORD)kAudioMsgTable[kMsgRaceUnload] == kMsgRaceUnloadFn &&
            InterlockedCompareExchangePointer(&kAudioMsgTable[kMsgRaceUnload], (void*)Hook_Msg51, (void*)kMsgRaceUnloadFn) ==
                (void*)kMsgRaceUnloadFn)
            Log("%s: audio message 51 wrapped again", s_msg43Installed ? "opponent engines" : "sound placement");
        return;
    }
    if (s_engHooksGaveUp) return;
    if (CurrentBuild() != BUILD_STEAM) {
        s_engHooksGaveUp = true;
        return;
    }
    if ((want43 && (DWORD)kAudioMsgTable[kMsgRacers] != kMsgRacersFn) ||
        (DWORD)kAudioMsgTable[kMsgRaceUnload] != kMsgRaceUnloadFn)
        return;
    s_engHooksGaveUp = true;

    static const BYTE kMsg51Sig[11] = { 0x8B, 0x0D, 0x94, 0x42, 0xD8, 0x00, 0xE9, 0xD5, 0xDD, 0xFB, 0xFF };
    if (!CodeMatches((void*)kMsgRaceUnloadFn, kMsg51Sig, sizeof(kMsg51Sig))) {
        Log("%s: audio message 51 code not as expected, race-unload cleanup off",
            want43 ? "opponent engines" : "sound placement");
        return;
    }
    bool ok43 = false;
    if (want43) {
        static const BYTE kMsg43Sig[16] = { 0x81, 0xEC, 0x8C, 0x03, 0x00, 0x00, 0x8D, 0x4C,
                                            0x24, 0x08, 0xC7, 0x04, 0x24, 0x2B, 0x00, 0x00 };
        static const BYTE kLoadSig[14] = { 0x81, 0xEC, 0xC8, 0x00, 0x00, 0x00, 0x56, 0x8B,
                                           0xB4, 0x24, 0xD0, 0x00, 0x00, 0x00 };
        static const BYTE kSetNameSig[14] = { 0x8B, 0x44, 0x24, 0x04, 0x50, 0x8B, 0x44, 0x24,
                                              0x0C, 0x8B, 0xD0, 0xC1, 0xE2, 0x04 };
        static const BYTE kExistsSig[7] = { 0x6A, 0xFF, 0x68, 0x98, 0xED, 0xB2, 0x00 };
        HMODULE ev = GetModuleHandleA("fmod_event.dll");
        s_esGetProject = ev ? (EsGetProject_t)GetProcAddress(ev, "_FMOD_EventSystem_GetProject@12") : NULL;
        ok43 = CodeMatches((void*)kMsgRacersFn, kMsg43Sig, sizeof(kMsg43Sig)) &&
               CodeMatches((void*)0x007F2FA0, kLoadSig, sizeof(kLoadSig)) &&
               CodeMatches((void*)0x008359A0, kSetNameSig, sizeof(kSetNameSig)) &&
               CodeMatches((void*)0x00402300, kExistsSig, sizeof(kExistsSig)) && s_esGetProject != NULL;
        if (!ok43) {
            Log("opponent engines: audio message 43 code or EventSystem_GetProject not as expected, car matching off");
            if (!s_voiceAttachPatched) return;
        }
    }
    s_origMsg51 = (AudioMsg_t)kMsgRaceUnloadFn;
    InterlockedExchange(&s_msg51Mode, ok43 ? 2 : 1);  // publish mode before Hook_Msg51 becomes reachable
    if (ok43) {
        s_origMsg43 = (AudioMsg_t)kMsgRacersFn;
        if (InterlockedCompareExchangePointer(&kAudioMsgTable[kMsgRacers], (void*)Hook_Msg43, (void*)kMsgRacersFn) !=
            (void*)kMsgRacersFn) {
            InterlockedExchange(&s_msg51Mode, 0);
            return;
        }
    }
    if (InterlockedCompareExchangePointer(&kAudioMsgTable[kMsgRaceUnload], (void*)Hook_Msg51, (void*)kMsgRaceUnloadFn) !=
        (void*)kMsgRaceUnloadFn) {
        InterlockedExchange(&s_msg51Mode, 0);
        if (ok43) InterlockedExchangePointer(&kAudioMsgTable[kMsgRacers], (void*)kMsgRacersFn);
        return;
    }
    s_msg43Installed = ok43;
    s_msg51Installed = true;
    if (ok43) Log("opponent engines: audio messages 43 (car matching) and 51 (race-unload cleanup) wrapped");
    else Log("sound placement: audio message 51 (race-unload cleanup) wrapped");
}

// ---------------------------------------------------------------- reverb presets
//
// The game reads its reverb presets from FMOD's event system (EventSystem::getReverbPreset /
// getReverbPresetByIndex) and hands them to FMOD as the car drives through the trigger zones. They come
// from the event project ReverbAndGroups (in resident.ark). In the PC release six of its seven presets
// hold FMOD's stock "Generic" values and LARGE_TANK the stock "Bathroom"; the Xbox 360 release's copy of
// the same file (same size, same layout, it differs only in these records) holds the presets the sound
// designers set: reverb up to 10 dB louder, longer decay in halls and under bridges, the menu's long
// room. The table below is that file's values. The two methods are virtual; fmod_event.dll exports
// their implementations, so the vtable slots that hold them are found in the DLL's own data and pointed
// at wrappers that call the original and then put the console's values into the result.
// fmod_event.dll is a library beside the exe: nothing in SplitSecond.exe is touched.

struct ReverbProps {  // FMOD_REVERB_PROPERTIES of FMOD Ex 4.28, 31 dwords
    int Instance, Environment;
    float EnvSize, EnvDiffusion;
    int Room, RoomHF, RoomLF;
    float DecayTime, DecayHFRatio, DecayLFRatio;
    int Reflections;
    float ReflectionsDelay, ReflectionsPan[3];
    int Reverb;
    float ReverbDelay, ReverbPan[3];
    float EchoTime, EchoDepth, ModulationTime, ModulationDepth, AirAbsorptionHF, HFReference, LFReference;
    float RoomRolloffFactor, Diffusion, Density;
    unsigned Flags;
};
static_assert(sizeof(ReverbProps) == 31 * 4, "FMOD_REVERB_PROPERTIES");

typedef int(__stdcall* GetPreset_t)(void* self, const char* name, ReverbProps* props, int* index);
typedef int(__stdcall* GetPresetByIndex_t)(void* self, int index, ReverbProps* props, char** name);
static GetPreset_t s_origGetPreset;
static GetPresetByIndex_t s_origGetPresetByIndex;

// the Xbox 360 release's ReverbAndGroups presets: the fields in which they differ from the PC's
struct ConsolePreset {
    const char* name;
    int environment, room, roomHF;
    float decay, decayHFRatio;
    int reflections;
    float reflectionsDelay;
    int reverb;
    float reverbDelay, density, lfReference;
};
static const ConsolePreset kConsolePresets[] = {
    {"LARGE_TANK", 0, 0, -100, 1.49f, 0.83f, -1000, 0.007f, 1000, 0.011f, 100.0f, 250.0f},
    {"OVERHEAD", 10, 0, -300, 4.0f, 0.23f, -602, 0.02f, 200, 0.03f, 100.0f, 250.0f},
    {"HALL", 13, 0, -300, 2.7f, 0.79f, -500, 0.013f, 395, 0.02f, 100.0f, 250.0f},
    {"DefaultAmbient", 20, -1000, 0, 1.65f, 1.5f, -1363, 0.008f, -1153, 0.012f, 100.0f, 250.0f},
    {"TakeDownCamera", 13, 0, -237, 1.0f, 0.79f, -1214, 0.013f, 395, 0.02f, 100.0f, 250.0f},
    {"LoadingAndFrontendScreenReverb", 3, -1500, 0, 8.0f, 1.0f, -370, 0.007f, 1030, 0.011f, 60.0f, 50.0f},
    {"LARGE_HALL", 13, 0, -300, 2.7f, 0.79f, -500, 0.013f, 395, 0.02f, 100.0f, 250.0f},
};

static void LogPreset(const char* how, const char* name, const ReverbProps* p, bool replaced)
{
    static char seen[32][64];
    static int count;
    if (!g_cfg.log || !name || !p) return;
    for (int i = 0; i < count; i++)
        if (!strcmp(seen[i], name)) return;
    if (count < 32) lstrcpynA(seen[count++], name, sizeof(seen[0]));
    Log("reverb preset '%s' (%s%s): environment %d, room %d, room HF %d, decay %.2f s (HF ratio %.2f), "
        "reflections %d at %.3f s, reverb %d at %.3f s, diffusion %.0f, density %.0f",
        name, how, replaced ? ", console values" : ", as the game", p->Environment, p->Room, p->RoomHF, p->DecayTime,
        p->DecayHFRatio, p->Reflections, p->ReflectionsDelay, p->Reverb, p->ReverbDelay, p->Diffusion, p->Density);
}

static bool ApplyConsolePreset(const char* name, ReverbProps* p)
{
    if (!g_cfg.consoleReverb || !name || !p) return false;
    for (const ConsolePreset& c : kConsolePresets) {
        if (strcmp(c.name, name)) continue;
        p->Environment = c.environment;
        p->Room = c.room;
        p->RoomHF = c.roomHF;
        p->DecayTime = c.decay;
        p->DecayHFRatio = c.decayHFRatio;
        p->Reflections = c.reflections;
        p->ReflectionsDelay = c.reflectionsDelay;
        p->Reverb = c.reverb;
        p->ReverbDelay = c.reverbDelay;
        p->Density = c.density;
        p->LFReference = c.lfReference;
        return true;
    }
    return false;
}

static int __stdcall Hook_GetPreset(void* self, const char* name, ReverbProps* props, int* index)
{
    int r = s_origGetPreset(self, name, props, index);
    if (r == 0) LogPreset("by name", name, props, ApplyConsolePreset(name, props));
    else if (g_cfg.log && name && !IsBadStringPtrA(name, 64)) {
        // once per name and result, at most 16 lines (the table is shared by whichever thread looks up)
        static DWORD seen[16];
        static int count;
        static SRWLOCK lock = SRWLOCK_INIT;
        DWORD h = 2166136261u ^ (DWORD)r;
        for (const char* c = name; *c; c++) h = (h ^ (BYTE)*c) * 16777619u;
        AcquireSRWLockExclusive(&lock);
        bool fresh = count < 16;
        for (int i = 0; i < count && fresh; i++)
            if (seen[i] == h) fresh = false;
        if (fresh) seen[count++] = h;
        ReleaseSRWLockExclusive(&lock);
        if (fresh) {
            Log("reverb preset '%s' (by name): lookup failed (FMOD result %d), left as the game", name, r);
        }
    }
    return r;
}

static int __stdcall Hook_GetPresetByIndex(void* self, int index, ReverbProps* props, char** name)
{
    int r = s_origGetPresetByIndex(self, index, props, name);
    if (r == 0) {
        const char* n = name ? *name : NULL;
        LogPreset("by index", n, props, ApplyConsolePreset(n, props));
    }
    return r;
}

// the slot in fmod_event.dll's read-only data that holds 'fn' (the implementation's address)
static void** FindSlot(HMODULE dll, void* fn)
{
    BYTE* base = (BYTE*)dll;
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + ((IMAGE_DOS_HEADER*)base)->e_lfanew);
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    void** found = NULL;
    int hits = 0;
    for (int s = 0; s < nt->FileHeader.NumberOfSections; s++) {
        if (sec[s].Characteristics & IMAGE_SCN_MEM_EXECUTE) continue;
        void** p = (void**)(base + sec[s].VirtualAddress);
        void** end = (void**)(base + sec[s].VirtualAddress + sec[s].Misc.VirtualSize);
        for (; p < end; p++)
            if (*p == fn) {
                found = p;
                hits++;
            }
    }
    return hits == 1 ? found : NULL;  // one vtable only, or we do not know which one
}

void FixReverbPresets()
{
    if (!g_cfg.consoleReverb && !g_cfg.log) return;
    HMODULE ev = GetModuleHandleA("fmod_event.dll");
    if (!ev) {
        Log("reverb: fmod_event.dll not loaded yet");
        return;
    }
    void* get = (void*)GetProcAddress(
        ev, "?getReverbPreset@EventSystemI@FMOD@@UAG?AW4FMOD_RESULT@@PBDPAUFMOD_REVERB_PROPERTIES@@PAH@Z");
    void* byIndex = (void*)GetProcAddress(
        ev, "?getReverbPresetByIndex@EventSystemI@FMOD@@UAG?AW4FMOD_RESULT@@HPAUFMOD_REVERB_PROPERTIES@@PAPAD@Z");
    void** slotGet = get ? FindSlot(ev, get) : NULL;
    void** slotIndex = byIndex ? FindSlot(ev, byIndex) : NULL;
    if (!slotGet || !slotIndex) {
        Log("reverb: EventSystem vtable slots not found (%p %p), presets left as the game's", (void*)slotGet, (void*)slotIndex);
        return;
    }
    s_origGetPreset = (GetPreset_t)get;
    s_origGetPresetByIndex = (GetPresetByIndex_t)byIndex;
    void* h1 = (void*)Hook_GetPreset;
    void* h2 = (void*)Hook_GetPresetByIndex;
    WriteMemory(slotGet, &h1, sizeof(h1));
    WriteMemory(slotIndex, &h2, sizeof(h2));
    Log("reverb: fmod_event.dll EventSystem getReverbPreset / getReverbPresetByIndex wrapped, %s",
        g_cfg.consoleReverb ? "the console's presets" : "presets only logged");
}

// ---------------------------------------------------------------- the game's own DSP effects
//
// The game's audio code wraps every DSP effect in an object whose virtual "attach" (vtable slot 9) is called
// with the channel group the effect belongs on. FMOD's own effects do the attaching there with
// ChannelGroup::addDSP (cFModDSPInstance, 0x864CE0). In the PC build the game's plugin effects only return
// their DSP, so none of them ever hears a sample. Restore the used mixer effects and VoiceDSP through
// their vtable entries in .rdata (the game's code is untouched). Steam build only: the vtables
// are checked against their RTTI class name and the stub's bytes. The missing connection was described by
// the Back on Air project; this is a separate implementation.
//
// Ducking: the mixer (DynamicMixer.xml, the same on PC and Xbox 360) ducks audio groups while others are
// loud: collisions duck music (-2 dB) and both engines (-5 dB); power plays duck opponents' engines and
// surfaces (-12 dB), player engine and collisions (-6 dB); HUD ducks music (-3 dB); speech mutes music,
// swooshes and particles (-60 dB) and ducks engines, collisions, power plays, surfaces and HUD (-18 dB)
// and ambience (-12 dB). Each side chain listens to a VolumeMeter in slot 7 of its audio group, attached
// when the game builds the group (0x86C4C3). Without the attach every side chain reads silence.
//
// Sound placement: the game places positional sounds itself, the opponents' engines among them: each such
// voice gets a VoiceDSP that takes the event's stereo signal and writes it to the six 5.1 speakers with
// levels it computes every update from the listener's distance and direction. Without the attach those
// sounds play at one fixed level, all seven opponent engines at once, wherever the cars are. Two more
// things the PC build gets wrong are handled: the unit's read callback (0x8A5470) always processes 256
// frames, the console mixer's block, while FMOD on PC asks for 1024, and it writes six channels whatever
// the output has. So the unit is created (System::createDSP, the exe's import) with a read callback that
// runs the game's in blocks of 256 frames through a 5.1 buffer and folds that down to the output's
// channels. And the units come from a pool and are not detached when their voice ends, while the event's
// channel group goes to the next voice: a unit attached to a group takes the group's previous unit off.
typedef int(__stdcall* FmodGroupAddDsp_t)(void* group, void* dsp, void** connection);
typedef int(__stdcall* FmodGroupGetSystem_t)(void* group, void** system);
typedef int(__stdcall* FmodDsp_t)(void* dsp);
typedef int(__stdcall* FmodDspActive_t)(void* dsp, int active);
typedef int(__stdcall* FmodDspRead_t)(void* state, float* in, float* out, unsigned length, int inChannels, int outChannels);
typedef int(__stdcall* FmodCreateDsp_t)(void* system, const void* description, void** dsp);
static FmodGroupAddDsp_t s_groupAddDsp;
static FmodGroupGetSystem_t s_groupGetSystem;
static FmodDsp_t s_dspRemove;
static FmodDspActive_t s_dspSetActive;
static FmodDspRead_t s_voiceRead;  // the game's VoiceDSP read callback
static FmodCreateDsp_t s_createDsp;

struct PluginEffect {
    const char* option;  // for the log
    void** vtable;
    const char* rtti;    // in the class name
    DWORD stub;          // slot 9 in the PC build: mov eax,[ecx+dspOffset]; ret 4
    DWORD dspOffset;
};
static const PluginEffect kVolumeMeter = {
    "ducking", (void**)0x00C1E028, "VolumeMeterState@VolumeMeter@DSP@Audio", 0x007F7E90, 0xB4
};
static const PluginEffect kPowerPlayFlanger = {
    "power play flanger", (void**)0x00C1DE54, "stFlangerState@Flanger@DSP@Audio", 0x007F6A80, 0x25CC
};
static const PluginEffect kVoiceDsp = {
    "sound placement", (void**)0x00C32824, "VoiceDSPState@VoiceDSP@Voice@Audio", 0x008A5B40, 0x138
};

static bool PatchAttach(const PluginEffect& fx, void* attach)
{
    bool ok = false;
    __try {
        const DWORD col = (DWORD)fx.vtable[-1];  // RTTI: complete object locator -> type descriptor (+0xC) -> name (+8)
        const DWORD type = col >= 0x00C90000 && col < 0x00D80000 ? ((const DWORD*)col)[3] : 0;
        BYTE stub[9] = { 0x8B, 0x81, 0, 0, 0, 0, 0xC2, 0x04, 0x00 };
        memcpy(stub + 2, &fx.dspOffset, 4);
        ok = type >= 0x00C90000 && type < 0x00D80000 && !IsBadStringPtrA((const char*)type + 8, 96) &&
             strstr((const char*)type + 8, fx.rtti) != NULL && fx.vtable[9] == (void*)fx.stub &&
             CodeMatches((void*)fx.stub, stub, sizeof(stub));
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        Log("%s: exception checking %s vtable, left alone", fx.option, fx.rtti);
        return false;
    }
    if (!ok) {
        Log("%s: %s vtable not as expected, left alone", fx.option, fx.rtti);
        return false;
    }
    if (!WriteSlot(&fx.vtable[9], attach)) {
        Log("%s: %s vtable could not be written, left alone", fx.option, fx.rtti);
        return false;
    }
    return true;
}

static void ResolveFmod()
{
    HMODULE ex = GetModuleHandleA("fmodex.dll");
    if (!ex) return;
    s_groupAddDsp = (FmodGroupAddDsp_t)GetProcAddress(ex, "_FMOD_ChannelGroup_AddDSP@12");
    s_groupGetSystem = (FmodGroupGetSystem_t)GetProcAddress(ex, "_FMOD_ChannelGroup_GetSystemObject@8");
    s_dspRemove = (FmodDsp_t)GetProcAddress(ex, "_FMOD_DSP_Remove@4");
    s_dspSetActive = (FmodDspActive_t)GetProcAddress(ex, "_FMOD_DSP_SetActive@8");
}

static void* __fastcall AttachPowerPlayFlanger(BYTE* self, void*, void* group)
{
    void* dsp = *(void**)(self + kPowerPlayFlanger.dspOffset);
    if (!dsp || !group) {
        Log("power play flanger: missing DSP or channel group, left unattached");
        return dsp;
    }
    // The group builder creates one DSP for this slot. The mixer controls its wet mix and frees it.
    int result = s_groupAddDsp(group, dsp, NULL);
    if (g_cfg.log || result != 0)
        Log("power play flanger: instance %p add to channel group %p -> %d", self, group, result);
    return dsp;
}

// ---- ducking (audio thread only)

static void* s_meterGroups[32];  // Log=1: channel groups with a successfully attached VolumeMeter
static float s_meterGroupVolume[32];
static DWORD s_meterGroupLogTick[32];
static int s_meterGroupCount;

static void* __fastcall AttachVolumeMeter(BYTE* self, void*, void* group)
{
    void* dsp = *(void**)(self + kVolumeMeter.dspOffset);
    if (!dsp || !group || !s_setFileSystemWrapped) return dsp;
    void* sys = NULL;
    if (s_groupGetSystem && s_groupGetSystem(group, &sys) != 0) sys = NULL;
    AudioSystemEnsureCurrent(sys, "new FMOD::System in VolumeMeter");
    s_dspRemove(dsp);  // a no-op unless the meter is attached already: it is never connected twice
    int r = s_groupAddDsp(group, dsp, NULL);
    int idx = -1;
    if (r == 0) {
        for (int i = 0; i < s_meterGroupCount; i++)
            if (s_meterGroups[i] == group) idx = i;
        if (idx < 0 && s_meterGroupCount < 32) {
            idx = s_meterGroupCount;
            s_meterGroups[idx] = group;
            s_meterGroupVolume[idx] = -1.0f;
            s_meterGroupLogTick[idx] = 0;
            s_meterGroupCount++;
        }
    }
    if (g_cfg.log && (r != 0 || idx >= 0))
        Log("ducking: volume meter %d attached to channel group %p -> %d", idx >= 0 ? idx + 1 : 0, group, r);
    return dsp;
}

// Log=1, audio thread (called from Hook_GroupSetVolume): log each meter group's volume move of 1 dB or more
// or silence crossing (throttled to 250 ms per group so focus-loss 0<->level pairs cannot flood the 3000-line cap)
void AudioFixNoteGroupVolume(void* group, float v, float applied)
{
    if (!g_cfg.log || !group || !s_meterGroupCount) return;
    static int logged;
    static bool capLogged;
    if (logged >= 3000) {
        if (!capLogged) {
            capLogged = true;
            Log("ducking: log limit reached (3000), further moves not logged");
        }
        return;
    }
    int i = 0;
    for (; i < s_meterGroupCount && s_meterGroups[i] != group; i++) {}
    if (i >= s_meterGroupCount) return;
    const float kSilence = 0.005f;  // below half a %.2f step (prints as 0.00)
    float& last = s_meterGroupVolume[i];
    if (last < 0.0f) {
        last = v;
        return;
    }
    const bool zeroFlip = (last < kSilence && v >= kSilence) || (v < kSilence && last >= kSilence);
    const bool moved1dB = last >= kSilence && v >= kSilence && (v / last > 1.122f || last / v > 1.122f);
    if (!zeroFlip && !moved1dB) return;
    DWORD now = GetTickCount();
    if (s_meterGroupLogTick[i] && now - s_meterGroupLogTick[i] < 250) return;
    typedef int(__stdcall* GetName_t)(void* grp, char* name, int length);
    static GetName_t getName = (GetName_t)GetProcAddress(GetModuleHandleA("fmodex.dll"), "_FMOD_ChannelGroup_GetName@12");
    char name[32] = "?";
    if (getName) getName(group, name, sizeof(name));
    if (applied != v)
        Log("ducking: group %d '%s' stock volume %.2f -> %.2f (applied %.2f)", i + 1, name, last, v, applied);
    else
        Log("ducking: group %d '%s' volume %.2f -> %.2f", i + 1, name, last, v);
    logged++;
    last = v;
    s_meterGroupLogTick[i] = now;
}

// ---- sound placement (audio thread only for registration/attach; mixer thread for VoiceRead)

static void* s_voiceUnits[128];   // units made with our read callback (cleared on sound-system reset)
static void* s_voiceGroups[128];  // the group each unit was last attached to (cleared only on sound-system reset)
static int s_voiceUnitCount;
static bool s_voiceTableFull;
static int s_raceVoiceNum;
static LONG s_raceVoiceAttached, s_raceVoiceFailed, s_raceVoiceLogged;

static int __stdcall VoiceRead(void* state, float* in, float* out, unsigned length, int inChannels, int outChannels)
{
    // FMOD mixes on one thread, these are not shared
    static float s_in[256 * 6], s_out[256 * 6];
    for (unsigned done = 0; done < length; done += 256) {
        const unsigned n = length - done < 256 ? length - done : 256;
        memset(s_in, 0, sizeof(s_in));
        // Taking only channels 0 and 1 matches the stock VoiceDSP path: 0x8A5F20 sets all six speaker
        // levels on the event to 1.0 (0xBA0B20), and 0x8A8520 deinterleaves only channels 0 and 1,
        // writing 0..5 and copying 6+ unchanged. Any higher input channel carries copies from those
        // 1.0 speaker levels, so folding them in would double the signal.
        for (unsigned f = 0; f < n; f++) {
            const float* src = in + (done + f) * inChannels;
            s_in[f * 6] = src[0];
            s_in[f * 6 + 1] = inChannels > 1 ? src[1] : src[0];
        }
        s_voiceRead(state, s_in, s_out, 256, 6, 6);
        for (unsigned f = 0; f < n; f++) {
            const float* s = s_out + f * 6;  // L R C LFE rear-L rear-R
            float* dst = out + (done + f) * outChannels;
            if (outChannels >= 6) {
                for (int c = 0; c < outChannels; c++) dst[c] = c < 6 ? s[c] : 0.0f;
            } else if (outChannels >= 2) {  // defensive ITU-R BS.775 guard (Steam build forces 5.1 at 0x8674F0)
                dst[0] = s[0] + 0.7071f * (s[2] + s[4]);
                dst[1] = s[1] + 0.7071f * (s[2] + s[5]);
                for (int c = 2; c < outChannels; c++) dst[c] = 0.0f;
            } else {
                dst[0] = 0.5f * (s[0] + s[1]) + 0.7071f * s[2] + 0.3536f * (s[4] + s[5]);
            }
        }
    }
    return 0;
}

static int __stdcall Hook_CreateDsp(void* system, const void* description, void** dsp)
{
    const DWORD kReadOffset = 0x34;  // FMOD_DSP_DESCRIPTION: name[32], version, channels, create, release, reset, read
    if (!description || *(const DWORD*)((const BYTE*)description + kReadOffset) != 0x008A5470)
        return s_createDsp(system, description, dsp);
    BYTE copy[0x5C];  // FMOD keeps its own copy of the description
    memcpy(copy, description, sizeof(copy));
    *(void**)(copy + kReadOffset) = (void*)VoiceRead;
    int r = s_createDsp(system, copy, dsp);
    if (r == 0 && dsp && *dsp) {
        AudioSystemEnsureCurrent(system, "new FMOD::System in createDSP");
        int slot = -1;
        for (int i = 0; i < s_voiceUnitCount; i++)
            if (s_voiceUnits[i] == *dsp) {
                slot = i;
                break;
            }
        if (slot >= 0) {
            s_voiceGroups[slot] = NULL;
        } else if (s_voiceUnitCount < 128) {
            s_voiceUnits[s_voiceUnitCount] = *dsp;
            s_voiceGroups[s_voiceUnitCount] = NULL;
            s_voiceUnitCount++;
        } else if (!s_voiceTableFull) {
            s_voiceTableFull = true;
            Log("sound placement: voice unit table full (128), extra units stay unattached");
        }
    }
    return r;
}

static void* __fastcall AttachVoiceDsp(BYTE* self, void*, void* group)
{
    void* dsp = *(void**)(self + kVoiceDsp.dspOffset);
    if (!dsp || !group || !s_setFileSystemWrapped) return dsp;
    void* sys = NULL;
    if (s_groupGetSystem && s_groupGetSystem(group, &sys) != 0) sys = NULL;
    AudioSystemEnsureCurrent(sys, "new FMOD::System in VoiceDSP");
    int unit = -1;
    for (int i = 0; i < s_voiceUnitCount; i++)
        if (s_voiceUnits[i] == dsp) unit = i;
    if (unit < 0) {
        if (!s_voiceTableFull) {
            static bool logged;
            if (!logged) Log("sound placement: a voice unit not made through the wrapped createDSP stays unattached");
            logged = true;
        }
        return dsp;
    }
    for (int i = 0; i < s_voiceUnitCount; i++)  // the group's previous unit: its voice has ended
        if (i != unit && s_voiceGroups[i] == group) {
            s_dspRemove(s_voiceUnits[i]);
            s_voiceGroups[i] = NULL;
        }
    // Always re-attach idempotently (no same-group address shortcut): a recycled ChannelGroup* never skips addDSP.
    s_dspRemove(dsp);
    int r = s_groupAddDsp(group, dsp, NULL);
    s_dspSetActive(dsp, 1);
    s_voiceGroups[unit] = r == 0 ? group : NULL;
    if (r == 0) s_raceVoiceAttached++;
    else s_raceVoiceFailed++;
    if (g_cfg.log && s_raceVoiceLogged < 10) {
        s_raceVoiceLogged++;
        Log("sound placement: race %d voice unit %d attached -> %d", s_raceVoiceNum + 1, unit, r);
    }
    return dsp;
}

// audio thread, before Msg 51 unloads race/CPUCar projects: log per-race attach counts and reset per-race counters.
// Keep s_voiceGroups intact across race unloads so any ChannelGroup* that survives Msg 51 still evicts its previous
// pooled VoiceDSP on the next AttachVoiceDsp (and a recycled address still runs idempotent remove+addDSP). Cleared
// only on full sound-system reset (ResetAllAudioState).
static void SoundPlacementRaceUnload()
{
    if (!s_voiceAttachPatched) return;
    if (g_cfg.log && (s_raceVoiceAttached || s_raceVoiceFailed))
        Log("sound placement: race %d unload - %ld attached (%ld failed), %d voice units tracked", s_raceVoiceNum + 1,
            s_raceVoiceAttached, s_raceVoiceFailed, s_voiceUnitCount);
    if (s_raceVoiceAttached || s_raceVoiceFailed) s_raceVoiceNum++;
    s_raceVoiceAttached = 0;
    s_raceVoiceFailed = 0;
    s_raceVoiceLogged = 0;
}

static void ResetAllAudioState(void* newSystem, const char* why)
{
    void* oldSys = s_audioSystem;
    LONG prevGen = s_audioSystemGen;
    LONG newGen = ++s_audioSystemGen;
    s_audioSystem = newSystem;
    memset(s_voiceUnits, 0, sizeof(s_voiceUnits));
    memset(s_voiceGroups, 0, sizeof(s_voiceGroups));
    s_voiceUnitCount = 0;
    s_voiceTableFull = false;
    s_raceVoiceAttached = 0;
    s_raceVoiceFailed = 0;
    s_raceVoiceLogged = 0;
    memset(s_meterGroups, 0, sizeof(s_meterGroups));
    for (float& v : s_meterGroupVolume) v = -1.0f;
    memset(s_meterGroupLogTick, 0, sizeof(s_meterGroupLogTick));
    s_meterGroupCount = 0;
    s_eventPeak = 0;
    s_eventPeakCap = 0;
    s_raceMatched = false;
    AudioDiagResetState();
    Log("audio: sound system reset (gen %ld -> %ld, %p -> %p, %s): diagnostic, volume and DSP tables cleared",
        prevGen, newGen, oldSys, newSystem, why);
}

bool AudioSystemInitHooked() { return s_setFileSystemWrapped; }

void AudioSystemEnsureCurrent(void* system, const char* why)
{
    if (!system) return;
    if (s_audioSystem && system != s_audioSystem) ResetAllAudioState(system, why);
    else if (!s_audioSystem) s_audioSystem = system;
}

// audio thread, from Hook_SetFileSystem (sole call site 0x86E4D2 inside 0x8674F0 FMOD init):
// runs strictly before any VolumeMeter attach, VoiceDSP createDSP, or Event::start for the new
// generation, so clearing all registries here handles even same-address FMOD::System* reuse.
static void OnSoundSystemInit(void* system)
{
    if (s_audioSystemGen == 0 && !s_audioSystem) {
        s_audioSystemGen = 1;
        s_audioSystem = system;
        return;
    }
    ResetAllAudioState(system, "System::setFileSystem");
}

// At DLL load, before the game builds its audio groups and voices.
void FixGameDspEffects()
{
    const bool flanger = g_cfg.powerPlayFlanger && (g_cfg.patchMask & PATCH_POWERPLAY_FLANGER);
    if (flanger || g_cfg.ducking || g_cfg.soundPlacement) ResolveFmod();
    if (flanger) {
        if (CurrentBuild() != BUILD_STEAM)
            Log("power play flanger: only on the Steam build");
        else if (!s_groupAddDsp)
            Log("power play flanger: FMOD ChannelGroup::addDSP not found, left alone");
        else if (PatchAttach(kPowerPlayFlanger, (void*)AttachPowerPlayFlanger))
            Log("power play flanger: attach hook installed");
    }
    if (!g_cfg.ducking && !g_cfg.soundPlacement) return;
    if (CurrentBuild() != BUILD_STEAM) {
        Log("ducking, sound placement: only on the Steam build");
        return;
    }
    if (!s_groupAddDsp || !s_dspRemove || !s_dspSetActive) {
        Log("ducking, sound placement: fmodex.dll functions not found, left alone");
        return;
    }
    if (g_cfg.ducking && PatchAttach(kVolumeMeter, (void*)AttachVolumeMeter))
        Log("ducking: volume meters attach to their audio groups");
    if (g_cfg.soundPlacement && PatchAttach(kVoiceDsp, (void*)AttachVoiceDsp)) {
        s_voiceAttachPatched = true;
        Log("sound placement: voice units attach to their voices");
    }
}

// after HookSoundBanks (the imports are bound by then): voice units get our read callback only when
// System::setFileSystem is wrapped, so DSP identities are never carried across an untracked system lifetime
void HookVoiceDspCreation()
{
    if (!s_voiceAttachPatched) return;  // SoundPlacement off, not the Steam build, or the attach not in place
    if (!s_setFileSystemWrapped) {
        Log("sound placement: System::setFileSystem not wrapped, voice units stay unattached");
        return;
    }
    void** slot = FindImport("fmodex.dll", "?createDSP@System@FMOD@@QAG?AW4FMOD_RESULT@@PAUFMOD_DSP_DESCRIPTION@@PAPAVDSP@2@@Z");
    if (!slot) {
        Log("sound placement: System::createDSP import not found, voice units stay unattached");
        return;
    }
    s_voiceRead = (FmodDspRead_t)0x008A5470;
    s_createDsp = (FmodCreateDsp_t)*slot;
    if (!WriteSlot(slot, (void*)Hook_CreateDsp)) {
        Log("sound placement: the System::createDSP import could not be written, voice units stay unattached");
        return;
    }
    Log("sound placement: voice units are made with a 256-frame read callback");
}

// ---------------------------------------------------------------- sound bank offsets
//
// Each PC sound bank (FSB4, MPEG) pads every sound's data to a 32-byte boundary, and the header's data size
// counts the padding. fmodex.dll 4.28 finds a sound by adding up the compressed lengths of the sounds before it,
// without the padding, so it starts reading up to ~2 KB early: the last MPEG frames of the previous sound play
// first (music.fsb: every result screen track starts ~2 KB early, inside the track before it). FMOD reads the
// banks through the game's own file callbacks (System::setFileSystem); those are wrapped and, for a bank whose
// header shows exactly this layout, each compressed length is rounded up to 32 as the header passes through a
// read. The file on disk is not changed and the sound data passes unchanged.

typedef int(__stdcall* FsOpen_t)(const char* name, int unicode, unsigned* size, void** handle, void** user);
typedef int(__stdcall* FsClose_t)(void* handle, void* user);
typedef int(__stdcall* FsRead_t)(void* handle, void* buf, unsigned bytes, unsigned* got, void* user);
typedef int(__stdcall* FsSeek_t)(void* handle, unsigned pos, void* user);
typedef int(__stdcall* SetFileSystem_t)(void* system, FsOpen_t, FsClose_t, FsRead_t, FsSeek_t, int blockAlign);

static SetFileSystem_t s_setFileSystem;
static FsOpen_t s_fsOpen;
static FsClose_t s_fsClose;
static FsRead_t s_fsRead;
static FsSeek_t s_fsSeek;
static CRITICAL_SECTION s_bankLock;

// one open bank whose header is corrected: where the reader is, and the length fields (file offsets) to round up
struct OpenBank {
    void* handle;
    unsigned pos;
    bool posKnown;  // false after a failed read or seek, until a seek succeeds
    unsigned headerEnd;
    unsigned* fields;  // pairs: file offset of a compressed length, its value rounded up to 32
    unsigned count;
};
static OpenBank s_banks[16];
// one I/O lock per bank slot: held across the real read or seek of that bank, so its cursor follows them in
// order. A blocking wait is always slot first, table second (LockBank, close); with the table held a slot is
// only tried, never waited for
static CRITICAL_SECTION s_bankIo[16];

static bool IsBankName(const char* name, int unicode)
{
    if (!name) return false;
    char low[MAX_PATH];
    size_t n = 0;
    if (unicode) {
        for (const wchar_t* w = (const wchar_t*)name; *w && n < sizeof low - 1; w++) low[n++] = (char)tolower(*w & 0x7F);
    } else {
        for (const char* c = name; *c && n < sizeof low - 1; c++) low[n++] = (char)tolower((BYTE)*c);
    }
    low[n] = 0;
    return n > 4 && !strcmp(low + n - 4, ".fsb");
}

// once per bank and outcome (streams open their bank again and again); FMOD opens banks on its own threads
static void LogBank(const char* name, int unicode, const char* fmt, ...)
{
    static DWORD seen[256];
    static int count;
    char path[MAX_PATH], text[256];
    if (unicode) _snprintf_s(path, _TRUNCATE, "%ls", (const wchar_t*)name);
    else _snprintf_s(path, _TRUNCATE, "%s", name);
    const char* base = path;  // the file name: the part after the last separator
    for (const char* c = path; *c; c++)
        if (*c == '/' || *c == '\\' || *c == '|') base = c + 1;
    DWORD h = 2166136261u;
    for (const char* c = path; *c; c++) h = (h ^ (BYTE)*c) * 16777619u;
    for (const char* c = fmt; *c; c++) h = (h ^ (BYTE)*c) * 16777619u;
    EnterCriticalSection(&s_bankLock);
    bool fresh = count < 256;
    for (int i = 0; i < count && fresh; i++)
        if (seen[i] == h) fresh = false;
    if (fresh) seen[count++] = h;
    LeaveCriticalSection(&s_bankLock);
    if (!fresh) return;
    va_list a;
    va_start(a, fmt);
    _vsnprintf_s(text, _TRUNCATE, fmt, a);
    va_end(a);
    Log("sound banks: %s: %s", base, text);
}

// the bank's header through the game's reader; the length fields to round, or 0. *rewound: the handle is back
// at position 0 (false: it is not, and must not be handed to FMOD as it is)
static unsigned ExamineBank(void* handle, void* user, unsigned size, const char* name, int unicode, unsigned** fieldsOut,
                            unsigned* headerEnd, bool* rewound)
{
    BYTE head[48];
    unsigned got = 0;
    bool read = s_fsSeek(handle, 0, user) == 0 && s_fsRead(handle, head, sizeof head, &got, user) == 0 && got == sizeof head;
    if (!read || memcmp(head, "FSB4", 4)) {
        *rewound = s_fsSeek(handle, 0, user) == 0;
        if (!read) LogBank(name, unicode, "header not readable, read as shipped");
        return 0;
    }
    unsigned sounds = *(unsigned*)(head + 4), headers = *(unsigned*)(head + 8), data = *(unsigned*)(head + 12);
    if (!sounds || sounds > 4096 || headers < 80 * sounds || headers > (1u << 20) || 48ull + headers + data != size) {
        *rewound = s_fsSeek(handle, 0, user) == 0;
        LogBank(name, unicode, "header not as expected, read as shipped");
        return 0;
    }
    BYTE* h = (BYTE*)HeapAlloc(GetProcessHeap(), 0, headers);
    unsigned* fields = (unsigned*)HeapAlloc(GetProcessHeap(), 0, sounds * 2 * sizeof(unsigned));
    got = 0;
    read = h && fields && s_fsRead(handle, h, headers, &got, user) == 0 && got == headers;
    *rewound = s_fsSeek(handle, 0, user) == 0;
    unsigned long long packed = 0, padded = 0;
    unsigned at = 0, mpeg = 0, count = 0;
    // the sample headers, back to back; the block may end in padding up to the data's 32-byte boundary
    for (unsigned k = 0; read && k < sounds; k++) {
        unsigned len = at + 2 <= headers ? *(WORD*)(h + at) : 0;
        if (len < 80 || at + len > headers) {
            read = false;
            break;
        }
        unsigned compressed = *(unsigned*)(h + at + 36), mode = *(unsigned*)(h + at + 48);
        unsigned long long rounded = ((unsigned long long)compressed + 31) & ~31ull;  // 64-bit: no wrap
        if (rounded > data) {  // larger than the whole data block: not a bank to touch
            read = false;
            break;
        }
        if (mode & 0x40000) mpeg++;  // FSOUND_MPEG
        if (compressed % 32) {
            fields[2 * count] = 48 + at + 36;
            fields[2 * count + 1] = (unsigned)rounded;
            count++;
        }
        packed += compressed;
        padded += rounded;
        at += len;
    }
    if (h) HeapFree(GetProcessHeap(), 0, h);
    if (!read || !*rewound || at > headers || headers - at >= 32 || mpeg != sounds || padded != data || packed >= data) {
        if (fields) HeapFree(GetProcessHeap(), 0, fields);
        LogBank(name, unicode, "%u sounds (%u MPEG), headers %u of %u bytes walked (%s), lengths %llu packed / %llu padded, "
                "data %u: read as shipped", sounds, mpeg, at, headers, read ? "read" : "not readable", packed, padded, data);
        return 0;
    }
    LogBank(name, unicode, "%u sounds padded to 32 bytes, %u lengths rounded up so FMOD finds each sound's real start",
            sounds, count);
    *fieldsOut = fields;
    *headerEnd = 48 + headers;
    return count;
}

static OpenBank* FindBank(void* handle)
{
    for (OpenBank& b : s_banks)
        if (b.handle == handle) return &b;
    return NULL;
}

static int __stdcall Hook_FsOpen(const char* name, int unicode, unsigned* size, void** handle, void** user)
{
    int r = s_fsOpen(name, unicode, size, handle, user);
    if (r || !size || !handle || !*handle || !IsBankName(name, unicode)) return r;
    unsigned* fields = NULL;
    unsigned headerEnd = 0;
    bool rewound = false;
    unsigned count = ExamineBank(*handle, user ? *user : NULL, *size, name, unicode, &fields, &headerEnd, &rewound);
    if (!rewound) {  // the handle is not at its start: a fresh one, as shipped
        s_fsClose(*handle, user ? *user : NULL);
        LogBank(name, unicode, "could not rewind after reading the header, opened again as shipped");
        return s_fsOpen(name, unicode, size, handle, user);
    }
    if (!count) return r;
    EnterCriticalSection(&s_bankLock);
    OpenBank* b = FindBank(NULL);
    if (b) *b = { *handle, 0, true, headerEnd, fields, count };
    LeaveCriticalSection(&s_bankLock);
    if (!b) {
        HeapFree(GetProcessHeap(), 0, fields);
        LogBank(name, unicode, "more than 16 banks open, this one read as shipped");
    }
    return r;
}

// a corrected bank's slot, with its I/O lock taken (the table lock is not held on return); NULL for other files.
// A slot busy with another thread's read is waited for outside the table lock, so other files go on meanwhile.
static OpenBank* LockBank(void* handle)
{
    if (!handle) return NULL;  // unused slots hold NULL
    for (;;) {
        EnterCriticalSection(&s_bankLock);
        OpenBank* b = FindBank(handle);
        bool got = !b || TryEnterCriticalSection(&s_bankIo[b - s_banks]);
        LeaveCriticalSection(&s_bankLock);
        if (got) return b;
        // busy: wait for that slot without the table, then check it still holds this handle
        CRITICAL_SECTION* io = &s_bankIo[b - s_banks];
        EnterCriticalSection(io);
        EnterCriticalSection(&s_bankLock);
        bool same = b->handle == handle;
        LeaveCriticalSection(&s_bankLock);
        if (same) return b;
        LeaveCriticalSection(io);  // closed meanwhile (and maybe reused): look again
    }
}

static int __stdcall Hook_FsClose(void* handle, void* user)
{
    if (OpenBank* b = LockBank(handle)) {  // a read still running on the handle finishes first
        EnterCriticalSection(&s_bankLock);
        HeapFree(GetProcessHeap(), 0, b->fields);
        *b = {};
        LeaveCriticalSection(&s_bankLock);
        LeaveCriticalSection(&s_bankIo[b - s_banks]);
    }
    return s_fsClose(handle, user);
}

static void UnlockBank(OpenBank* b)
{
    LeaveCriticalSection(&s_bankIo[b - s_banks]);
}

// The real read and seek of a corrected bank run under its own lock, so its cursor follows them in order even
// if FMOD used one handle from two threads; other files and other banks are not held up.
static int __stdcall Hook_FsSeek(void* handle, unsigned pos, void* user)
{
    OpenBank* b = LockBank(handle);
    if (!b) return s_fsSeek(handle, pos, user);
    int r = s_fsSeek(handle, pos, user);
    b->pos = pos;
    b->posKnown = r == 0;  // a failed seek leaves the position unknown: no corrections until a seek succeeds
    UnlockBank(b);
    return r;
}

static int __stdcall Hook_FsRead(void* handle, void* buf, unsigned bytes, unsigned* got, void* user)
{
    OpenBank* b = LockBank(handle);
    if (!b) return s_fsRead(handle, buf, bytes, got, user);
    if (got) *got = 0;  // a reader that fails without setting it reports nothing read
    int r = s_fsRead(handle, buf, bytes, got, user);
    unsigned start = b->pos, n = got ? *got : 0;  // a failed read may still report bytes (end of file)
    if (n > bytes) n = bytes;
    if (b->posKnown && buf && start < b->headerEnd)
        for (unsigned i = 0; i < b->count; i++) {
            unsigned f = b->fields[2 * i];
            const BYTE* value = (const BYTE*)&b->fields[2 * i + 1];
            for (unsigned k = 0; k < 4; k++)  // byte by byte: a read may end inside the field
                if (f + k >= start && f + k - start < n) ((BYTE*)buf)[f + k - start] = value[k];
        }
    b->pos = start + n;
    if (r != 0 && n < bytes) b->posKnown = false;  // where the reader stands now is not certain
    UnlockBank(b);
    return r;
}

static int __stdcall Hook_SetFileSystem(void* system, FsOpen_t open, FsClose_t close, FsRead_t read, FsSeek_t seek, int align)
{
    OnSoundSystemInit(system);
    if (!g_cfg.soundBankFix) return s_setFileSystem(system, open, close, read, seek, align);
    if (!open || !close || !read || !seek) {
        Log("sound banks: the game set no file callbacks of its own, banks read as shipped");
        return s_setFileSystem(system, open, close, read, seek, align);
    }
    // the first set of callbacks is kept for good and wrapped (again, if it comes again); another set passes
    // through as it is, so handles already open keep the callbacks they were opened with
    EnterCriticalSection(&s_bankLock);
    bool first = !s_fsOpen;
    bool same = first || (open == s_fsOpen && close == s_fsClose && read == s_fsRead && seek == s_fsSeek);
    if (first) {
        s_fsOpen = open;
        s_fsClose = close;
        s_fsRead = read;
        s_fsSeek = seek;
    }
    LeaveCriticalSection(&s_bankLock);
    if (!same) {
        Log("sound banks: a second set of file callbacks, passed through as it is");
        return s_setFileSystem(system, open, close, read, seek, align);
    }
    if (first) Log("sound banks: the game's file callbacks wrapped");
    return s_setFileSystem(system, Hook_FsOpen, Hook_FsClose, Hook_FsRead, Hook_FsSeek, align);
}

void HookSoundBanks()
{
    const bool needLifecycle = g_cfg.opponentEngines || g_cfg.soundPlacement || g_cfg.ducking ||
                               g_cfg.photoMode || g_cfg.log;
    if (!g_cfg.soundBankFix && !needLifecycle) return;
    void** slot = FindImport("fmodex.dll", "?setFileSystem@System@FMOD@@QAG?AW4FMOD_RESULT@@P6G?AW43@PBDHPAIPAPAX2@Z"
                                           "P6G?AW43@PAX4@ZP6G?AW43@44I14@ZP6G?AW43@4I4@ZH@Z");
    if (!slot) {
        Log("audio: System::setFileSystem import not found%s%s",
            g_cfg.soundBankFix ? ", banks read as shipped" : "",
            (g_cfg.ducking || g_cfg.soundPlacement) ? "; ducking/sound placement stay unattached" : "");
        return;
    }
    if (g_cfg.soundBankFix) {
        InitializeCriticalSection(&s_bankLock);
        for (CRITICAL_SECTION& io : s_bankIo) InitializeCriticalSection(&io);
    }
    s_setFileSystem = (SetFileSystem_t)*slot;
    if (!WriteSlot(slot, (void*)Hook_SetFileSystem)) {
        Log("audio: System::setFileSystem import cannot be written%s%s",
            g_cfg.soundBankFix ? ", banks read as shipped" : "",
            (g_cfg.ducking || g_cfg.soundPlacement) ? "; ducking/sound placement stay unattached" : "");
        return;
    }
    s_setFileSystemWrapped = true;
}

// Log=1: every reverb change the game hands FMOD (the presets above after the trigger zones and the
// mixer have blended them), so a jump can be matched against the music and speech starts in the log
typedef int(__stdcall* FmodSetReverb_t)(void* system, const ReverbProps* props);
static FmodSetReverb_t s_setReverb;

static int __stdcall Hook_SetReverb(void* system, const ReverbProps* p)
{
    static ReverbProps last;
    static int lines;
    if (p && lines < 2000 && memcmp(&last, p, sizeof(last))) {
        last = *p;
        lines++;
        Log("reverb set: instance %d environment %d, room %d, room HF %d, decay %.2f s, reflections %d, reverb %d, "
            "diffusion %.0f, density %.0f, flags 0x%X",
            p->Instance, p->Environment, p->Room, p->RoomHF, p->DecayTime, p->Reflections, p->Reverb, p->Diffusion,
            p->Density, p->Flags);
    }
    return s_setReverb(system, p);
}

void HookReverbLog()
{
    if (!g_cfg.log) return;
    void** slot = FindImport("fmodex.dll", "?setReverbProperties@System@FMOD@@QAG?AW4FMOD_RESULT@@PBUFMOD_REVERB_PROPERTIES@@@Z");
    if (!slot) {
        Log("reverb: System::setReverbProperties import not found, changes not logged");
        return;
    }
    s_setReverb = (FmodSetReverb_t)*slot;
    if (!WriteSlot(slot, (void*)Hook_SetReverb)) Log("reverb: setReverbProperties import cannot be written, changes not logged");
}

// later, once the game runs: the value is still ours (nothing set it back while the game started)
void CheckOpponentEngineBanks()
{
    if (!g_cfg.opponentEngines || CurrentBuild() == BUILD_UNKNOWN) return;
    Log("opponent engines: bank slot size now %lu bytes", *(DWORD*)G(0x00D3DBC0));
}
