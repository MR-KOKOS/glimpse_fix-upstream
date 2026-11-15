// Split/Second controller fix - which SplitSecond.exe runs, and where the fixed addresses are in it.
// The Russian depot (297867) ships the same game compiled two days later: the same functions and
// data, moved by a few bytes in the code and by 0x1000 in the data. Every address the fix uses is
// listed with its place there; a missing entry makes the build count as unknown.
#include "common.h"

struct AddrPair { DWORD steam, other; };

static const AddrPair kRussian[] = {
#include "build_ru.inc"
};

static GameBuild s_build;
static bool s_detected, s_missing;

GameBuild CurrentBuild()
{
    if (!s_detected) {
        // the header's link time; SecuROM keeps it as in the file
        BYTE* base = (BYTE*)GetModuleHandleA(NULL);
        IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + ((IMAGE_DOS_HEADER*)base)->e_lfanew);
        switch (nt->FileHeader.TimeDateStamp) {
        case 0x4BE13C66: s_build = BUILD_STEAM; break;
        case 0x4BE3EA74: s_build = BUILD_STEAM_RU; break;
        default: s_build = BUILD_UNKNOWN; break;
        }
        s_detected = true;
    }
    return s_build;
}

const char* BuildName()
{
    switch (CurrentBuild()) {
    case BUILD_STEAM: return "Steam 1.0.0.1";
    case BUILD_STEAM_RU: return "Steam 1.0.0.1, Russian depot 297867";
    default: return "unknown";
    }
}

static const AddrPair* Find(DWORD addr)
{
    int lo = 0, hi = (int)(sizeof(kRussian) / sizeof(kRussian[0])) - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (kRussian[mid].steam == addr) return &kRussian[mid];
        if (kRussian[mid].steam < addr) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

DWORD G(DWORD addr)
{
    if (CurrentBuild() != BUILD_STEAM_RU) return addr;
    if (const AddrPair* p = Find(addr)) return p->other;
    s_missing = true;
    return 0;
}

bool MapEmbedded(DWORD value, DWORD* mapped)
{
    if (CurrentBuild() != BUILD_STEAM_RU) return false;
    const AddrPair* p = Find(value);
    if (!p) return false;
    *mapped = p->other;
    return true;
}

bool BuildMapComplete() { return !s_missing; }
