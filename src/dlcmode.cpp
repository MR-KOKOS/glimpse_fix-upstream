// Split/Second PC fix - [DLC] Modes=1 (experimental): the converted DLC game modes in quick play: Deadline
// (internal name TimeTrialPlus, tools\dlc\mode\deadline_mode.py) selectable, Deadline and Survival Race
// (NemesisRace, tools\dlc\mode\nemesis_mode.py) with their own quick play movies.
//
// A mode comes as data in an overlay archive (dlcfs.cpp): GameLogic/GameModes/<Name>.params, its HUD file, the
// GAMEMODES line of directory.txt, a PlayableGameMode in TrackFiltration.xml and the texts. The game lists it.
// Deadline is in the SinglePlayer list, and locked: 0x77A9F0 builds the quick play mode list as 12-byte records
// {GameMode*, movie id, locked} (count at list +8, records at list +0xC, room for 16: 0x486BE0 allocates
// 16 x 12) and sets locked for a single-player mode unless an unlocked Season.xml event (12 episodes) uses
// the mode's name, or DLCUnlocks11/12 is set (0x486DE0). No season event uses TimeTrialPlus. Online and split
// screen lists are never locked (0x486D90 passes flag 1 online, 2 with two players); Survival Race is only in the
// split screen list (nemesis_mode.py).
// Every reader of the lock goes through the record's +8 (0x4866D0, 0x486F40 for the current selection with
// TEXT_CHOOSE_GAMEMODE and its buttons (0x479D60), 0x486C90 when the choice is taken (0x486FF0 / 0x486D30),
// and the item update itself: TEXT_LOCKED and FE_Locked.bik); UI/All Gamemodes Unlocked (0xD6FC54) opens all.
// The list is rebuilt by 0x486D90 when the quick play screen opens (cNewQuickPlay slot 1, 0x479B30).
// Its item update is the delegate cQuickPlayEventSelectDynamicMenuConfigObject (vtable 0xBB2360, made at
// 0x486C4C with the list at +4; the list has the screen at +0x14), slot 1 = 0x4867B0 (__thiscall index,
// selected, widget; ret 0xC), called for the shown items whenever the menu refreshes, the movie widget included.
// Its slot is swapped: before the game's update, the record whose GameMode name (GameMode +0xC, char*, as
// 0x77A5F0 compares it) is a DLC mode is changed: Deadline set unlocked; the movie id (+4, read only by 0x486780
// for the item update's 0x479720) set to the id dlcfs.cpp serves that mode's movie under (cNewQuickPlay slot 24,
// ids from 64 on), when the movie file is there. Every other mode keeps the game's state. A data write into the
// freshly built list: the next rebuild resets it and the next update changes it again. Nothing goes into the
// profile, the DLC unlock flags stay as they are, no game code byte changes.
// Without the movie: Deadline is the 7th SinglePlayer mode, movie id 6 = FE_QP_Track10.bik (0x479680; locked:
// FE_Locked.bik); Survival Race is in no SinglePlayer list, movie id 0 = FE_Race.bik.
#include "common.h"

// dlcfs.cpp
int DlcQpAddModeMovie(const char* path);
bool DlcQpInstallMovies();
bool DlcDataFileExists(const char* rel);
bool DlcQpIsMovieWidget(DWORD widget);
void DlcQpBeforeMovieRequest(void* screen, bool dlc21, int state);

typedef void(__thiscall* QpModeItemUpdate_t)(void* self, int index, DWORD selected, DWORD widget);
static void** const kModeItemUpdateSlot = (void**)0x00BB2364;  // vtable 0xBB2360 slot 1
static const DWORD kModeItemUpdate = 0x004867B0;
static const DWORD kModeItemCol = 0x00C7A40C;  // RTTI complete object locator at vtable -4
static const char kModeItemClass[] = ".?AVcQuickPlayEventSelectDynamicMenuConfigObject@Frontend@@";

struct DlcMode {
    const char* name;      // GameMode name
    const char* params;    // its mode file in the merged resident.ark
    bool unlock;           // single-player list: open it
    const char* movie;     // quick play movie (data|...), served by dlcfs.cpp
    const char* movieRel;  // the same below data (or below glimpse_dlc\<dir>\data)
    const char* shown;     // name in the log
};
static const DlcMode kDlcModes[] = {
    { "TimeTrialPlus", "GameLogic/GameModes/TimeTrialPlus.params", true,
      "data|Movies/Screens/QuickPlay/FE_Deadline.bik", "movies\\screens\\quickplay\\fe_deadline.bik", "Deadline" },
    { "NemesisRace", "GameLogic/GameModes/NemesisRace.params", false,
      "data|Movies/Screens/QuickPlay/FE_Survival_Race.bik", "movies\\screens\\quickplay\\fe_survival_race.bik",
      "Survival Race" },
};
static const int kNumDlcModes = sizeof(kDlcModes) / sizeof(kDlcModes[0]);
static bool s_modePresent[kNumDlcModes];
static int s_modeMovieId[kNumDlcModes];
static QpModeItemUpdate_t s_origModeItemUpdate;
static bool s_dlcModesOn;  // DlcInstallModes found a mode to change (the hook may be there for the lap choice alone)
static bool s_modeUnlockLogged[kNumDlcModes];

static int DlcModeOf(const char* name)
{
    for (int i = 0; i < kNumDlcModes; i++)
        if (s_modePresent[i] && !_stricmp(name, kDlcModes[i].name)) return i;
    return -1;
}

static void UpdateModeRecords(const BYTE* self, int index, DWORD selected, DWORD widget)
{
    __try {
        const BYTE* list = *(const BYTE* const*)(self + 4);
        if (!list) return;
        int capacity = *(const int*)(list + 4);
        int count = *(const int*)(list + 8);
        BYTE* records = *(BYTE* const*)(list + 0xC);
        if (count <= 0 || count > capacity || count > 64 || !records) return;
        for (int i = 0; i < count; i++) {
            BYTE* r = records + i * 12;
            const BYTE* mode = *(const BYTE* const*)r;
            if (!mode) continue;
            const char* name = *(const char* const*)(mode + 0xC);
            if (!name || IsBadStringPtrA(name, 64)) continue;
            int m = DlcModeOf(name);
            if (m < 0) continue;
            if (kDlcModes[m].unlock && r[8]) {
                r[8] = 0;
                if (!s_modeUnlockLogged[m]) {
                    s_modeUnlockLogged[m] = true;
                    Log("DLC modes: %s unlocked in the quick play mode list (record %d of %d, movie id %lu, list %p)",
                        name, i, count, *(const DWORD*)(r + 4), list);
                }
            }
            if (s_modeMovieId[m] >= 0) *(DWORD*)(r + 4) = (DWORD)s_modeMovieId[m];
        }
        // the movie widget asks for 21 only for a locked mode (FE_Locked.bik): tell dlcfs.cpp, which serves 21
        // for a DLC track too; the update asks for a movie only with the screen in state 0 (0x4867DF)
        if (selected && index >= 0 && index < count && DlcQpIsMovieWidget(widget))
            DlcQpBeforeMovieRequest(*(void* const*)(list + 0x14), false, 0);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
}

// Match the game's selected-byte and owning-screen phase test (0x4867D1).
static const BYTE* HighlightedMode(const BYTE* self, int index, char* out, size_t n)
{
    out[0] = 0;
    __try {
        const BYTE* list = *(const BYTE* const*)(self + 4);
        const BYTE* screen = list ? *(const BYTE* const*)(list + 0x14) : NULL;
        if (!screen || *(const DWORD*)(screen + 0x240) != 0) return NULL;
        if (index >= 0 && index < 64 && index < *(const int*)(list + 8)) {
            const BYTE* mode = *(const BYTE* const*)(*(BYTE* const*)(list + 0xC) + index * 12);
            const char* name = mode ? *(const char* const*)(mode + 0xC) : NULL;
            if (name) strncpy_s(out, n, name, _TRUNCATE);
        }
        return screen;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return NULL;
    }
}

static void __fastcall Hook_ModeItemUpdate(void* self, void*, int index, DWORD selected, DWORD widget)
{
    if (s_dlcModesOn) UpdateModeRecords((const BYTE*)self, index, selected, widget);
    if ((BYTE)selected && LapMenuInstalled()) {
        char name[64];
        const BYTE* screen = HighlightedMode((const BYTE*)self, index, name, sizeof name);
        if (screen) LapMenuModeHighlighted(screen, name);
    }
    s_origModeItemUpdate(self, index, selected, widget);
}

// the class name behind the vtable's complete object locator {sig 0, offset, cdOffset, TypeDescriptor*, ...}
static bool ModeListClassMatches()
{
    const DWORD* col = (const DWORD*)kModeItemCol;
    if (col[0] != 0) return false;
    const char* name = (const char*)(col[3] + 8);  // TypeDescriptor {vftable, spare, name[]}
    return !IsBadStringPtrA(name, sizeof(kModeItemClass)) && !strcmp(name, kModeItemClass);
}

// The slot swap, shared by the DLC modes and the lap choice: once, after the list's class and update are checked
bool QuickPlayModeListReady()
{
    if (s_origModeItemUpdate) return true;
    if (CurrentBuild() != BUILD_STEAM) return false;
    __try {
        if ((DWORD)kModeItemUpdateSlot[-2] != kModeItemCol || !ModeListClassMatches() ||
            (DWORD)kModeItemUpdateSlot[0] != kModeItemUpdate ||
            memcmp((const void*)kModeItemUpdate, "\x6A\xFF\x68\x85\x3D\xB3\x00", 7))
            return false;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
    return true;
}

bool HookQuickPlayModeList()
{
    if (s_origModeItemUpdate) return true;
    if (!QuickPlayModeListReady()) return false;
    DWORD old;
    if (!VirtualProtect(kModeItemUpdateSlot, 4, PAGE_READWRITE, &old)) return false;
    // the original is stored before the slot points at the hook (as iohook.cpp's Swap does)
    s_origModeItemUpdate = (QpModeItemUpdate_t)*kModeItemUpdateSlot;
    InterlockedExchangePointer(kModeItemUpdateSlot, (void*)Hook_ModeItemUpdate);
    VirtualProtect(kModeItemUpdateSlot, 4, old, &old);
    Log("quick play mode list hooked (vtable 0xBB2360 slot 1)");
    return true;
}

void DlcInstallModes()
{
    if (!g_cfg.dlcModes) return;
    if (CurrentBuild() != BUILD_STEAM) {
        Log("DLC modes: not on this build (%s), Deadline stays locked", BuildName());
        return;
    }
    int present = 0;
    for (int i = 0; i < kNumDlcModes; i++) {
        s_modePresent[i] = DlcOverlayHas(kDlcModes[i].params);
        s_modeMovieId[i] = -1;
        present += s_modePresent[i];
    }
    if (!present) {
        Log("DLC modes: no overlay in use brings %s or %s (needs [DLC] Overlays=1 and the mode pack in glimpse_dlc), "
            "nothing changed", kDlcModes[0].params, kDlcModes[1].params);
        return;
    }
    if (!HookQuickPlayModeList()) {
        Log("DLC modes: quick play mode list not as expected, Deadline stays locked there unless UnlockAll=1");
        return;
    }
    // the modes' own movies, when their files are there (data, or glimpse_dlc\<dir>\data through the redirect)
    for (int i = 0; i < kNumDlcModes; i++) {
        if (!s_modePresent[i]) continue;
        if (!DlcDataFileExists(kDlcModes[i].movieRel)) {
            Log("DLC modes: %s: no %s, the quick play screen shows the stock movie", kDlcModes[i].shown,
                kDlcModes[i].movieRel);
            continue;
        }
        if (!DlcQpInstallMovies()) break;
        s_modeMovieId[i] = DlcQpAddModeMovie(kDlcModes[i].movie);
        Log("DLC modes: %s shows %s (movie id %d)", kDlcModes[i].shown, kDlcModes[i].movie, s_modeMovieId[i]);
    }
    s_dlcModesOn = true;
    for (int i = 0; i < kNumDlcModes; i++)
        if (s_modePresent[i])
            Log("DLC modes: quick play mode list changed for %s (%s)%s", kDlcModes[i].shown,
                kDlcModes[i].name, kDlcModes[i].unlock ? " will be selectable" : " is never locked (split screen)");
}
