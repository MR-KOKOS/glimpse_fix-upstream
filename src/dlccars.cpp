// Split/Second PC fix - [DLC] Cars=1 (experimental): the converted DLC cars (tools\dlc\assemble\build_pack.py) can
// be chosen in the front-end car select without UnlockAll; with [DLC] CarsCareer=1 also in the career's.
//
// Why the game does not list them: the car lists come from the unlock manager (object at [0xD64E48], ctor
// 0xA05410), and every car in it comes from UI/Logic/Season.xml (loader 0xA05820: InitialCars, the 20 basic and 4
// elite career cars) or from the profile's DLC unlock flags (hard-wired PC bonus cars, 0xA05E30). A DLC record
// added to VehicleDatabase.xml is in none of them. The front-end car select Frontend::cVehicleSelect (ctor
// 0x472B90, vtable 0xBAEC5C, RTTI locator 0xC799A4) builds its list in 0x4734A0: list 2 of the manager (list 1 in
// the career, session flag [0xD64E40]+8), or every record with Locked (+0x1A0) != 1 and Player (+0x1A2) != 0 when
// "UI/Vehicle Select/All Vehicles Unlocked" (0xD6F8F0, UnlockAll=1) or game_setup/use_dev_menus (0xD71A34) is set.
// The list is a vector of record indices at +0x13C {capacity, count +0x140, data +0x144} (0x100 entries allocated
// by the ctor, 0x472CB5) with a second count at +0x138 that the navigation uses (0x474395); 0x4734A0 resets both,
// appends, and sends ClearMiniMenu (0x536B00) and one AddCarToMiniMenu per car (0x536AC0, behind 0x472020) to the
// movie [this+0x1AC]. When the game type [0xD6EE78] is 0 or 1 (training / tutorial) it lists one car only.
// Putting the DLC cars into Season.xml instead would also put them into the in-game car select (0x809850), whose
// array has room for 30 (3 + 20 + 4 Season.xml cars already).
//
// Hook: vtable 0xBAEC5C slot 25 (0xBAECC0) = 0x473F80, the screen's activation: 0x4734A0 (build the list), then, if
// +0x164 is clear, 0x4733F0 (select the profile's car, slot from +0x15C and the game type) and the model load,
// and 0x4730B0 (name, MiniMenuSetActive, model, camera; it also writes the shown car into the profile, 0x41AA90 at
// 0x473202). The slot is swapped for a wrapper that reads the profile's car (0x41A5D0, a pure lookup) before the
// original when it will select anew (+0x164 clear), calls the original, then appends the DLC records to the list
// (both counts, capacity respected, no index twice) and sends the movie one AddCarToMiniMenu per added record (the
// game's cars are in the movie already; 0x4734A0's ClearMiniMenu only runs once +0x90 is set, which on a fresh
// entry happens after slot 25 (slot 1 0x473FF0 -> slot 25, then slot 18 0x4739E0 -> 0x457A00), so the list is not
// sent again). If the profile's car is one of the added records, +0x160 is set to its position and 0x4730B0 runs,
// as 0x4733F0's own match does (0x473482/0x473488: profile car, colour +0x1A0 via 0x41A700, name, model);
// otherwise MiniMenuSetActive for the current index follows. If the screen came back with its index kept and that
// index is past the list (a DLC car no longer listed), the first car is shown, as 0x4733F0 falls back to it.
// DLC records: the Placeholder slots 62..81 of the PC database whose ID equals their ModelFile (finish_car.py
// gives them ID = ModelFile), with CarSelectOrder > 0, Player and not Locked, and whose
// Vehicles/Frontend/Bodies/<ID>/ChassisName.txt the merged resident.ark has; added in CarSelectOrder order.
// Inert: with UnlockAll=1 (not installed) or whenever the game takes its all-records branch (0xD6F8F0, 0xD71A34:
// the cars are listed already), in the career unless [DLC] CarsCareer=1, for game types 0/1 (one car) and 6/7/9
// (online screens), and with SteamLAN in an online session. Only data writes into the screen's own list, one
// vtable slot and calls of game functions. With UnlockAll=1 the game's all-records branch lists the DLC cars on the
// online car select too (no game type check); with SteamLAN=1 the LAN signature then covers them (DlcCarsInOverlay).
//
// Career ([DLC] CarsCareer=1, static RE 2026-11-21). The career is session flag [0xD64E40]+8 = 1 with game type 2
// (both set by the episode screen, 0x47D277/0x47D284); the hook then appends to the list-1 list as in quick play,
// and only with type 2 (anything else with the flag set is left alone). Why that is safe:
// - The front-end car select opens in the career only for an event without a fixed car (0x47C0E0: strlen of
//   event +0x110 at 0x47C4C7, 0 -> screen 0x2D at 0x47C796; FixedCar is the only car rule Season.xml events have).
//   The screen indexes only its own list (+0x144, 0x100 entries; 0x4730B0, 0x474100 bounded by +0x138). Its
//   confirm 0x473B70 hands the car on by name: profile slot 0 (0x41AA90, CRC of the ID), the per-record garage
//   colour (0x41A720 -> 0x41A6A0, a table of 0x60 entries built from every record, 0x41A740), the player slot,
//   and in the career the session car [0xD64E40]+0x9B0/+0x9D8 (0x471FB0 at 0x473C4B).
// - The manager's list 1 is read only by 0x4734A0 and the in-game car select 0x809850 (the only callers of the
//   list getter 0xA05360); nothing reads this screen's list. The other manager readers (credits/unlock texts
//   0x807BF0, 0x7D5D70, 0x80B220/0x80B730, unlock movies 0x79AA70, achievement 0x4D3890, 0x773F50, 0x41AB30) use
//   the manager's own Season.xml arrays and never the player's car.
// - Race start: 0x414050 takes the event's fixed car or the session car (name, +0x9B0); 0x517E90 picks the AI by
//   the player car's rating (record +0x19C looked up by name over all records, 0x516D90/0x517280), the same code
//   as quick play. The session car is otherwise read only by the end sequence 0x414330 (name) and 0x8096C0 (a
//   compare); it is not saved. The profile keeps only FavouriteVehicle%d (CRC) and the garage (per record).
// - In-game car select 0x54 between career events (0x809850): it fills its 30-entry array (+0x280) from list 1
//   only, so the DLC cars never enter it; a profile car not in it leaves the index at 0 (0x8098FD, matched at
//   0x809AD4), so it shows the first car. Accepting there (0x8096C0) makes that car the profile and session car;
//   backing out keeps the DLC car. No out-of-range index either way. So in the career a DLC car can be chosen
//   for events started from the front-end episode screen; the next event chosen in-game offers Season.xml cars.
// - SecuROM: none of 0x4734A0, 0x4733F0, 0x4730B0, 0x473B70, 0x474100, 0x41A5D0/0x41AA90/0x41A6A0/0x41A700/
//   0x41A720, 0x471FB0, 0x414050, 0x414330, 0x517E90, 0x517280, 0x516D90, 0x809850, 0x8096C0, 0x80A3C0 jumps out
//   of the dump; the virtualised 0xA05600/0xA056C0 run only inside 0xA05FE0 (the game's own call in 0x4734A0,
//   as in quick play) on Season.xml entries, and the hook calls neither.
#include "common.h"
#include <algorithm>
#include <ctype.h>

typedef void(__thiscall* VsActivate_t)(void* self);
typedef void(__thiscall* VsShow_t)(void* self);                                     // 0x4730B0, plain ret
typedef int(__thiscall* ProfileCar_t)(void* cars, int slot);                       // 0x41A5D0, ret 4
typedef int(__thiscall* MovieInvoke_t)(void* movie, const char* name, void* args, int n);  // 0x536AC0, ret 0xC

static void** const kVsActivateSlot = (void**)0x00BAECC0;  // vtable 0xBAEC5C slot 25
static const DWORD kVsActivate = 0x00473F80;
static const DWORD kVsCol = 0x00C799A4;                     // RTTI complete object locator at vtable -4
static const char kVsClass[] = ".?AVcVehicleSelect@Frontend@@";
static const VsShow_t kVsShow = (VsShow_t)0x004730B0;
static const ProfileCar_t kProfileCar = (ProfileCar_t)0x0041A5D0;
static const MovieInvoke_t kMovieInvoke = (MovieInvoke_t)0x00536AC0;
static const int kFirstPlaceholder = 62, kLastPlaceholder = 81;  // Placeholder_01..20 in the PC VehicleDatabase.xml
static const int kRecordSize = 0x1BC;
// the DLC cars the converter knows (any of them in the merged archive turns the hook on)
static const char* const kKnownCars[] = { "Musclecar_17", "Musclecar_18", "Musclecar_19", "Truck_09", "Supercar_17",
                                          "Supercar_18", "Supercar_19", "Supercar_20", "Unique_21", "Unique_22",
                                          "Unique_23" };
static VsActivate_t s_origActivate;

// a GFx call argument as the game builds them: {type, unused, value}; 4 = string, 3 = number (double)
struct MovieArg {
    DWORD type;
    DWORD unused;
    union {
        const char* str;
        double num;
    };
};

static bool OverlayHasCar(const char* id)
{
    char path[96];
    if (strlen(id) > 40) return false;
    sprintf_s(path, "Vehicles/Frontend/Bodies/%s/ChassisName.txt", id);
    return DlcOverlayHas(path);
}

static bool IsCarName(const char* s)
{
    // <Musclecar|Supercar|Truck|Unique|Hypercar>_<two digits>, as the converter names them
    static const char* const prefix[] = { "Musclecar_", "Supercar_", "Truck_", "Unique_", "Hypercar_" };
    for (const char* p : prefix) {
        size_t n = strlen(p);
        if (!_strnicmp(s, p, n) && isdigit((unsigned char)s[n]) && isdigit((unsigned char)s[n + 1]) && !s[n + 2])
            return true;
    }
    return false;
}

struct Cand {
    int index;
    int order;
    char id[24];
};

// the DLC records of the database (game memory: inside __try, no C++ objects)
static int FindDlcRecords(Cand* out, int room)
{
    int n = 0;
    __try {
        const BYTE* db = *(const BYTE* const*)0x00D64E4C;
        if (!db) return 0;
        int count = *(const int*)(db + 4);
        const BYTE* recs = *(const BYTE* const*)(db + 8);
        if (!recs || count <= kLastPlaceholder || count > 256) return 0;
        for (int i = kFirstPlaceholder; i <= kLastPlaceholder && n < room; i++) {
            const BYTE* r = recs + i * kRecordSize;
            const char* id = *(const char* const*)r;
            const char* model = *(const char* const*)(r + 0x28);
            if (!id || !model || IsBadStringPtrA(id, 32) || IsBadStringPtrA(model, 32)) continue;
            if (strlen(id) >= sizeof(out[0].id) || _stricmp(id, model) || !IsCarName(id)) continue;
            int order = *(const int*)(r + 0x1B8);
            if (order <= 0 || r[0x1A0] == 1 || r[0x1A2] == 0) continue;
            out[n].index = i;
            out[n].order = order;
            strcpy_s(out[n].id, id);
            n++;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
    return n;
}

enum Gate { GATE_OK, GATE_ALL, GATE_CAREER, GATE_ONECAR, GATE_ONLINE, GATE_STATE };

static Gate GameGate(int* type, bool* career)
{
    __try {
        if (*(const BYTE*)0x00D6F8F0 || *(const BYTE*)0x00D71A34) return GATE_ALL;
        const BYTE* session = *(const BYTE* const*)0x00D64E40;
        if (!session) return GATE_STATE;
        *type = *(const int*)0x00D6EE78;
        if (session[8]) {
            if (!g_cfg.dlcCarsCareer) return GATE_CAREER;
            // the career as the episode screen sets it up (0x47D277 flag, 0x47D284 game type 2); else left alone
            if (*type != 2) return GATE_STATE;
            *career = true;
        }
        if (*type == 0 || *type == 1) return GATE_ONECAR;
        // 6 (online invite screens) and 7 as 0x4733F0 treats them; 9 (quick match screen) is not confirmed, harmless
        if (*type == 6 || *type == 7 || *type == 9) return GATE_ONLINE;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return GATE_STATE;
    }
    if (g_cfg.steamLan && OnlineSession()) return GATE_ONLINE;
    return GATE_OK;
}

// appends the records to the screen's list; returns how many were added (game memory: __try only)
static int AppendToList(BYTE* self, const Cand* c, int nc, int* added)
{
    int n = 0;
    __try {
        int cap = *(const int*)(self + 0x13C);
        int count = *(const int*)(self + 0x140);
        int* data = *(int* const*)(self + 0x144);
        if (!data || cap <= 0 || cap > 0x100 || count < 0 || count > cap || *(const int*)(self + 0x138) != count) return -1;
        for (int i = 0; i < nc && count < cap; i++) {
            bool have = false;
            for (int j = 0; j < count && !have; j++) have = data[j] == c[i].index;
            if (have) continue;
            data[count++] = c[i].index;
            added[n++] = c[i].index;
        }
        // both counts, as 0x4734A0 keeps them
        *(int*)(self + 0x140) = count;
        *(int*)(self + 0x138) = count;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
    return n;
}

// The movie already holds the game's own cars: 0x4734A0 sent them (its ClearMiniMenu only runs once the screen
// is set up, +0x90, which on a fresh entry happens after slot 25, so the list is not sent again). Only the added
// records follow, as 0x4734A0 sends each car (AddCarToMiniMenu with the record's ID, behind the 0x472020 check:
// movie [this+0x1AC] and its +4 set). The game memory is read inside __try, the game's functions outside it.
static void* ReadMovie(const BYTE* self)
{
    __try {
        void* movie = *(void* const*)(self + 0x1AC);
        if (!movie || !*(const void* const*)((const BYTE*)movie + 4)) return NULL;
        return movie;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return NULL;
    }
}

static const char* RecordId(int index)
{
    __try {
        const BYTE* db = *(const BYTE* const*)0x00D64E4C;
        const BYTE* recs = *(const BYTE* const*)(db + 8);
        if (!recs || index < 0 || index >= *(const int*)(db + 4)) return NULL;
        const char* id = *(const char* const*)(recs + index * kRecordSize);
        return id ? id : *(const char* const*)0x00D728F0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return NULL;
    }
}

static void SendAdded(const BYTE* self, const int* added, int n)
{
    void* movie = ReadMovie(self);
    if (!movie) return;
    for (int i = 0; i < n; i++) {
        const char* id = RecordId(added[i]);
        if (!id) continue;
        MovieArg a = {};
        a.type = 4;
        a.str = id;
        kMovieInvoke(movie, "AddCarToMiniMenu", &a, 1);
    }
}

// MiniMenuSetActive for the current index, as 0x4730B0 sends it
static void SendActive(const BYTE* self, int index)
{
    void* movie = ReadMovie(self);
    if (!movie) return;
    MovieArg s = {};
    s.type = 3;
    s.num = (double)index;
    kMovieInvoke(movie, "MiniMenuSetActive", &s, 1);
}

// list position of a record index, -1 if not listed; and the current index / list count
static int ListPos(const BYTE* self, int record, int* cur, int* count)
{
    __try {
        *cur = *(const int*)(self + 0x160);
        *count = *(const int*)(self + 0x138);
        const int* data = *(const int* const*)(self + 0x144);
        if (!data || *count < 0 || *count > 0x100) return -1;
        for (int i = 0; i < *count; i++)
            if (data[i] == record) return i;
        return -1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

static bool WriteIndex(BYTE* self, int index)
{
    __try {
        *(int*)(self + 0x160) = index;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

// the profile's car list object and slot for this screen (as 0x4733F0 picks them)
static bool ProfileSlot(const BYTE* self, int type, void** cars, int* slot)
{
    __try {
        *slot = *(const int*)(self + 0x15C) > 0 ? 2 : (type == 6 || type == 7) ? 1 : 0;
        BYTE* prof = *(BYTE* const*)0x00D64E20;
        if (!prof) return false;
        *cars = prof + 0x2C4;
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

static BYTE ReadByte(const BYTE* p)
{
    __try {
        return *p;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static void __fastcall Hook_VsActivate(void* self, void*)
{
    static bool loggedAdd[2], loggedGate[GATE_STATE + 1], loggedState;
    BYTE* s = (BYTE*)self;
    BYTE keepIndex = ReadByte(s + 0x164);
    int type = -1;
    bool career = false;
    Gate g = GameGate(&type, &career);  // the state the original reads too; activation does not change it
    // the profile's car BEFORE the original: its 0x4730B0 writes the shown car (list[+0x160], the first car when
    // 0x4733F0 found no match) into the profile (0x41AA90 at 0x473202). 0x41A5D0 is a pure lookup (ret 4).
    int profileCar = -1;
    if (g == GATE_OK && !keepIndex) {
        void* cars;
        int slot;
        if (ProfileSlot(s, type, &cars, &slot)) profileCar = kProfileCar(cars, slot);
    }
    s_origActivate(self);  // the game's list, selection and model, outside __try

    int n = 0;
    Cand c[kLastPlaceholder - kFirstPlaceholder + 1];
    int nc = 0;
    if (g != GATE_OK) {
        if (!loggedGate[g]) {
            loggedGate[g] = true;
            static const char* const why[] = { "", "the game lists every car itself (All Vehicles Unlocked / dev menus)",
                                               "career (CarsCareer=0)", "a one-car mode (training/tutorial)",
                                               "online", "unexpected game state" };
            Log("DLC cars: car select opened, DLC cars not added: %s (game type %d)", why[g], type);
        }
    } else {
        nc = FindDlcRecords(c, _countof(c));
        int k = 0;
        for (int i = 0; i < nc; i++)
            if (OverlayHasCar(c[i].id)) c[k++] = c[i];
        nc = k;
        std::sort(c, c + nc, [](const Cand& a, const Cand& b) { return a.order < b.order; });
        int added[_countof(c)];
        n = nc ? AppendToList(s, c, nc, added) : 0;
        if (n < 0) {
            if (!loggedState) {
                loggedState = true;
                Log("DLC cars: car select list not as expected, DLC cars not added");
            }
            n = 0;
        } else if (n > 0) {
            SendAdded(s, added, n);
            int cur = 0, count = 0;
            int p = ListPos(s, profileCar, &cur, &count);  // -1 for no profile car (indices are >= 0)
            bool mine = false;
            for (int i = 0; i < n && p >= 0; i++) mine |= added[i] == profileCar;
            if (mine && WriteIndex(s, p))
                kVsShow(self);  // as 0x4733F0's own match (0x473482/0x473488): profile car, colour, name, model
            else
                SendActive(s, cur);
        }
    }
    // the screen came back with its index kept (+0x164) and that index points past the list (a DLC car of an
    // earlier activation, now not listed): the first car, as 0x4733F0 falls back to it
    if (keepIndex) {
        int cur = 0, count = 0;
        ListPos(s, -1, &cur, &count);
        if (count > 0 && (cur < 0 || cur >= count) && WriteIndex(s, 0)) kVsShow(self);
    }
    if (n > 0 && !loggedAdd[career]) {
        loggedAdd[career] = true;
        char ids[512] = "";  // cut short, never overrun (strcat_s would end the game on overflow)
        for (int i = 0; i < nc; i++) {
            if (i) strncat_s(ids, ", ", _TRUNCATE);
            strncat_s(ids, c[i].id, _TRUNCATE);
        }
        Log("DLC cars: %d DLC cars added to the car select (game type %d%s): %s", n, type, career ? ", career" : "",
            ids);
    }
}

// for the LAN check (dlcfs.cpp DlcOnlineSignature): the cars a player with UnlockAll=1 can pick online. The game
// hands a player's car to the others by its ID only (Net-Z dataset cATVNetDSVehicleData {mVehicleName, mColour} of
// cATVNetDOPlayerSetup at +0x80, filled from the race setup's car by 0x883160; every player looks the ID up in his
// own database, 0x51A1E0 -> 0x51BBE0, an unknown one gives "Couldn't find vehicle info" and no vehicle), so only
// players with the same cars may race together then
int DlcCarsInOverlay(char* ids, size_t idsSize)
{
    int n = 0;
    if (idsSize) ids[0] = 0;
    for (const char* car : kKnownCars)
        if (OverlayHasCar(car)) {
            if (n++) strncat_s(ids, idsSize, ",", _TRUNCATE);
            strncat_s(ids, idsSize, car, _TRUNCATE);
        }
    return n;
}

static bool VsClassMatches()
{
    const DWORD* col = (const DWORD*)kVsCol;
    if (col[0] != 0) return false;
    const char* name = (const char*)(col[3] + 8);  // TypeDescriptor {vftable, spare, name[]}
    return !IsBadStringPtrA(name, sizeof(kVsClass)) && !strcmp(name, kVsClass);
}

void DlcInstallCars()
{
    if (!g_cfg.dlcCars || !g_cfg.dlcOverlays) return;
    if (g_cfg.unlockAll) {
        Log("DLC cars: UnlockAll=1, the game lists the DLC cars itself, nothing hooked");
        return;
    }
    if (CurrentBuild() != BUILD_STEAM) {
        Log("DLC cars: not on this build (%s), DLC cars only with UnlockAll=1", BuildName());
        return;
    }
    const char* any = NULL;
    for (const char* car : kKnownCars)
        if (OverlayHasCar(car)) {
            any = car;
            break;
        }
    if (!any) {
        Log("DLC cars: no overlay in use brings a DLC car (needs [DLC] Overlays=1 and a car pack in glimpse_dlc), "
            "nothing hooked");
        return;
    }
    __try {
        if ((DWORD)kVsActivateSlot[-26] != kVsCol || !VsClassMatches() || (DWORD)kVsActivateSlot[0] != kVsActivate ||
            memcmp((const void*)kVsActivate, "\x56\x8B\xF1\xE8\x18\xF5\xFF\xFF", 8) ||
            memcmp((const void*)0x004734A0, "\x83\xEC\x20\x53\x55\x56\x8B\xF1", 8) ||
            memcmp((const void*)0x004733F0, "\x53\x56\x57\x8B\xF1\x33\xFF\x39", 8) ||
            memcmp((const void*)0x004730B0, "\x6A\xFF\x68\x93\x31\xB3\x00", 7) ||
            memcmp((const void*)0x0041A5D0, "\x51\x53\x8B\x1D\x4C\x4E\xD6\x00", 8) ||
            memcmp((const void*)0x00536AC0, "\x83\xEC\x10\x8B\x54\x24\x1C\x8B", 8)) {
            Log("DLC cars: car select not as expected, DLC cars only with UnlockAll=1");
            return;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return;
    }
    DWORD old;
    if (!VirtualProtect(kVsActivateSlot, 4, PAGE_READWRITE, &old)) return;
    // the original is stored before the slot points at the hook (as iohook.cpp's Swap does)
    s_origActivate = (VsActivate_t)*kVsActivateSlot;
    InterlockedExchangePointer(kVsActivateSlot, (void*)Hook_VsActivate);
    VirtualProtect(kVsActivateSlot, 4, old, &old);
    Log("DLC cars: car select hooked (vtable 0xBAEC5C slot 25), the overlay's DLC cars (%s, ...) will be listed%s", any,
        g_cfg.dlcCarsCareer ? ", also in the career (CarsCareer=1)" : "");
}
