// Split/Second PC fix - audio faults of the PC release
//
// Opponent engines. Every opponent's engine is a sound project of its own, loaded into one of seven
// "CPUCar" sound bank slots. When the audio system starts (0x7F5900) it makes those slots with
// [0xD3DBC0] bytes each (0x19999, 104857: the Xbox 360's size, whose car banks are 57..94 KB of XMA);
// the PC's banks are MP2 and larger, the loader (0x8695D0) refuses a bank that does not fit, and the
// opponents race without an engine sound. The value is plain data in the exe file, so it is raised as
// soon as this DLL is loaded, before the game's code runs; seven slots of 256 KB come out of the game's
// own sound bank memory. Found by the Back on Air project (github.com/xenophobentx/back-on-air) and
// checked here against this build.
#include "common.h"

void FixOpponentEngineBanks()
{
    if (!g_cfg.opponentEngines) return;
    if (CurrentBuild() != BUILD_STEAM) {
        Log("opponent engines: only on the supported build for now");
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
    void* get = (void*)GetProcAddress(ev, "?getReverbPreset@EventSystemI@FMOD@@UAG?AW4FMOD_RESULT@@PBDPAUFMOD_REVERB_PROPERTIES@@PAH@Z");
    void* byIndex = (void*)GetProcAddress(ev, "?getReverbPresetByIndex@EventSystemI@FMOD@@UAG?AW4FMOD_RESULT@@HPAUFMOD_REVERB_PROPERTIES@@PAPAD@Z");
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

// later, once the game runs: the value is still ours (nothing set it back while the game started)
void CheckOpponentEngineBanks()
{
    if (!g_cfg.opponentEngines || CurrentBuild() != BUILD_STEAM) return;
    Log("opponent engines: bank slot size now %lu bytes", *(DWORD*)G(0x00D3DBC0));
}
