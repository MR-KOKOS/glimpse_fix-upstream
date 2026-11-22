// File I/O hooks for virtual archives (see iohook.h, DESIGN.md)
#include "iohook.h"
#include <unordered_map>

IoReal g_ioReal;
IoStats g_ioStats;

namespace {

struct FileId {
    DWORD vol, hi, lo;
    bool operator==(const FileId& o) const { return vol == o.vol && hi == o.hi && lo == o.lo; }
};

struct Target {
    FileId id;
    DWORD size;  // the real file's size: only a handle of that size is asked for its file id
    const VirtualArchive* va;
};

struct VHandle {
    const VirtualArchive* va;
    uint64_t pos;  // file pointer for synchronous handles (overlapped ones carry it in OVERLAPPED)
    FileId id;     // the file it was recognised as (a handle value closed past this hook can come back for another)
};

SRWLOCK s_lock = SRWLOCK_INIT;
// on the heap and never freed: at process exit, CloseHandle calls of other modules can still arrive after
// this DLL's static objects are gone
std::vector<Target>& s_targets = *new std::vector<Target>();
std::unordered_map<HANDLE, VHandle>& s_handles = *new std::unordered_map<HANDLE, VHandle>();

struct Saved {
    void** slot;
    void* old;
};
std::vector<Saved>& s_saved = *new std::vector<Saved>();

bool GetId(HANDLE h, FileId* id)
{
    BY_HANDLE_FILE_INFORMATION fi;
    if (!GetFileInformationByHandle(h, &fi)) return false;
    id->vol = fi.dwVolumeSerialNumber;
    id->hi = fi.nFileIndexHigh;
    id->lo = fi.nFileIndexLow;
    return true;
}

bool Lookup(HANDLE h, VHandle* out)
{
    AcquireSRWLockShared(&s_lock);
    auto it = s_handles.find(h);
    bool found = it != s_handles.end();
    if (found) *out = it->second;
    ReleaseSRWLockShared(&s_lock);
    return found;
}

void SetPos(HANDLE h, uint64_t pos)
{
    AcquireSRWLockExclusive(&s_lock);
    auto it = s_handles.find(h);
    if (it != s_handles.end()) it->second.pos = pos;
    ReleaseSRWLockExclusive(&s_lock);
}

bool Swap(void** slot, void* hook)
{
    DWORD old;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old)) return false;
    InterlockedExchangePointer(slot, hook);
    VirtualProtect(slot, sizeof(void*), old, &old);
    return true;
}

}  // namespace

void IoInitReal()
{
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    g_ioReal.ReadFile = (ReadFile_t)GetProcAddress(k32, "ReadFile");
    g_ioReal.SetFilePointer = (SetFilePointer_t)GetProcAddress(k32, "SetFilePointer");
    g_ioReal.GetFileSize = (GetFileSize_t)GetProcAddress(k32, "GetFileSize");
    g_ioReal.CloseHandle = (CloseHandle_t)GetProcAddress(k32, "CloseHandle");
}

bool IoAddVirtualFile(const char* path, const VirtualArchive* va)
{
    HANDLE f = CreateFileA(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return false;
    Target t;
    bool ok = GetId(f, &t.id) && (t.id.vol || t.id.hi || t.id.lo);  // a volume without file ids: not used
    LARGE_INTEGER sz = {};
    ok = ok && GetFileSizeEx(f, &sz) && sz.QuadPart < 0xFFFFFFFF;
    t.size = ok ? (DWORD)sz.QuadPart : 0;
    (g_ioReal.CloseHandle ? g_ioReal.CloseHandle : CloseHandle)(f);
    if (!ok) return false;
    t.va = va;
    AcquireSRWLockExclusive(&s_lock);
    s_targets.push_back(t);
    ReleaseSRWLockExclusive(&s_lock);
    return true;
}

size_t IoVirtualHandleCount()
{
    AcquireSRWLockShared(&s_lock);
    size_t n = s_handles.size();
    ReleaseSRWLockShared(&s_lock);
    return n;
}

void Log(const char* fmt, ...);  // main.cpp

DWORD WINAPI Hook_GetFileSize(HANDLE h, LPDWORD high)
{
    VHandle v;
    bool known = Lookup(h, &v);
    if (known) {
        // still the file it was recognised as? A handle closed past CloseHandle (NtClose, another module's own
        // import of it) leaves its entry behind, and the same value may come back for another file: the game asks
        // for the size right after every open, so the entry is checked here before any read
        FileId id;
        if (!GetId(h, &id) || !(id == v.id)) {
            AcquireSRWLockExclusive(&s_lock);
            s_handles.erase(h);
            ReleaseSRWLockExclusive(&s_lock);
            static volatile LONG logged;
            if (InterlockedIncrement(&logged) <= 4) Log("DLC: a stale archive handle entry dropped (the handle is another file now)");
            known = false;
        }
    }
    if (!known) {
        // the game asks for the size right after opening (0x5734E1): the moment to recognise the archive.
        // Only a file of the registered size is asked for its id, so other handles cost one call.
        DWORD realHigh = 0;
        const DWORD real = g_ioReal.GetFileSize(h, &realHigh);
        const DWORD err = GetLastError();
        bool candidate = false;
        AcquireSRWLockShared(&s_lock);
        for (const Target& t : s_targets)
            if (real == t.size && realHigh == 0) candidate = true;
        ReleaseSRWLockShared(&s_lock);
        FileId id = {};
        const VirtualArchive* va = NULL;
        if (candidate && GetId(h, &id)) {
            AcquireSRWLockShared(&s_lock);
            for (const Target& t : s_targets)
                if (t.id == id && t.size == real) va = t.va;
            ReleaseSRWLockShared(&s_lock);
        }
        if (!va) {
            if (high) *high = realHigh;
            SetLastError(err);
            return real;
        }
        AcquireSRWLockExclusive(&s_lock);
        s_handles[h] = VHandle{va, 0, id};
        ReleaseSRWLockExclusive(&s_lock);
        InterlockedIncrement(&g_ioStats.registered);
        v.va = va;
    }
    if (high) *high = 0;
    SetLastError(NO_ERROR);
    return v.va->Size();
}

BOOL WINAPI Hook_ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov)
{
    VHandle v;
    if (!Lookup(h, &v)) {
        InterlockedIncrement(&g_ioStats.passthrough);
        return g_ioReal.ReadFile(h, buf, n, got, ov);
    }
    const VirtualArchive* va = v.va;
    const uint64_t pos = ov ? ((uint64_t)ov->OffsetHigh << 32 | ov->Offset) : v.pos;
    if (pos >= va->Size()) {
        if (got) *got = 0;
        if (ov) {
            SetLastError(ERROR_HANDLE_EOF);
            return FALSE;
        }
        return TRUE;
    }
    if (pos >= va->BaseDataStart() && pos + n <= va->BaseDataEnd()) {
        // entirely in the base file's data: the real read at the shifted offset (stays asynchronous)
        InterlockedIncrement(&g_ioStats.translated);
        const uint64_t real = pos - va->Delta();
        if (ov) {
            const DWORD so = ov->Offset, sh = ov->OffsetHigh;
            ov->Offset = (DWORD)real;  // read by the kernel when the request is issued
            ov->OffsetHigh = (DWORD)(real >> 32);
            BOOL r = g_ioReal.ReadFile(h, buf, n, got, ov);
            DWORD e = GetLastError();
            ov->Offset = so;  // the game keeps its own (virtual) position there
            ov->OffsetHigh = sh;
            SetLastError(e);
            return r;
        }
        LONG hi = (LONG)(real >> 32);
        if (g_ioReal.SetFilePointer(h, (LONG)(DWORD)real, &hi, FILE_BEGIN) == INVALID_SET_FILE_POINTER &&
            GetLastError() != NO_ERROR)
            return FALSE;
        DWORD g = 0;
        BOOL r = g_ioReal.ReadFile(h, buf, n, &g, NULL);
        if (got) *got = g;
        if (r) SetPos(h, pos + g);
        return r;
    }
    // header, merged index, overlay data or a read across a border: served from the virtual archive
    InterlockedIncrement(&g_ioStats.synthetic);
    uint32_t g = 0;
    if (!va->Read((uint32_t)pos, buf, n, &g)) {
        SetLastError(ERROR_READ_FAULT);
        return FALSE;
    }
    if (got) *got = g;
    if (ov) {
        // completed at once, as a cached read would; GetOverlappedResult finds Internal != STATUS_PENDING
        ov->Internal = 0;  // STATUS_SUCCESS
        ov->InternalHigh = g;
        HANDLE ev = (HANDLE)((ULONG_PTR)ov->hEvent & ~(ULONG_PTR)1);
        if (ev) SetEvent(ev);
    } else {
        SetPos(h, pos + g);
    }
    return TRUE;
}

DWORD WINAPI Hook_SetFilePointer(HANDLE h, LONG dist, PLONG high, DWORD method)
{
    VHandle v;
    if (!Lookup(h, &v)) return g_ioReal.SetFilePointer(h, dist, high, method);
    int64_t d = high ? ((int64_t)*high << 32) | (uint32_t)dist : (int64_t)dist;
    int64_t from = method == FILE_BEGIN ? 0 : method == FILE_CURRENT ? (int64_t)v.pos : (int64_t)v.va->Size();
    int64_t to = from + d;
    if (to < 0) {
        SetLastError(ERROR_NEGATIVE_SEEK);
        return INVALID_SET_FILE_POINTER;
    }
    SetPos(h, (uint64_t)to);
    if (high) *high = (LONG)(to >> 32);
    SetLastError(NO_ERROR);
    return (DWORD)to;
}

BOOL WINAPI Hook_CloseHandle(HANDLE h)
{
    // every CloseHandle of the process comes here: only a known handle takes the exclusive lock
    VHandle v;
    if (Lookup(h, &v)) {
        AcquireSRWLockExclusive(&s_lock);
        s_handles.erase(h);
        ReleaseSRWLockExclusive(&s_lock);
    }
    return g_ioReal.CloseHandle(h);
}

int IoInstallKernel32Slots()
{
    static const char* const names[4] = {"ReadFile", "SetFilePointer", "GetFileSize", "CloseHandle"};
    void* const hooks[4] = {(void*)Hook_ReadFile, (void*)Hook_SetFilePointer, (void*)Hook_GetFileSize,
                            (void*)Hook_CloseHandle};
    void** real[4] = {(void**)&g_ioReal.ReadFile, (void**)&g_ioReal.SetFilePointer, (void**)&g_ioReal.GetFileSize,
                      (void**)&g_ioReal.CloseHandle};
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    void** slots[4];
    for (int i = 0; i < 4; i++) {
        const BYTE* p = (const BYTE*)GetProcAddress(k32, names[i]);
        if (!p || p[0] != 0xFF || p[1] != 0x25) return 0;  // not a jmp [slot] thunk: leave everything alone
        slots[i] = *(void***)(p + 2);
        // the slot must be kernel32's own (its import table), and still hold a function outside kernel32 (an empty
        // slot or one that points back into kernel32 would become the "real" function every read and close goes to)
        HMODULE owner = NULL, target = NULL;
        if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                (LPCSTR)slots[i], &owner) || owner != k32 || !*slots[i])
            return 0;
        if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               (LPCSTR)*slots[i], &target) && target == k32)
            return 0;
    }
    // a second call: the slots hold our hooks already, which must not become the "real" functions (endless recursion)
    int ours = 0;
    for (int i = 0; i < 4; i++)
        if (*slots[i] == hooks[i]) ours++;
    if (ours) return ours == 4 ? 4 : 0;
    s_saved.reserve(s_saved.size() + 4);  // before any slot changes: a swapped slot is always in the list
    int done = 0;
    for (int i = 0; i < 4; i++) {
        *real[i] = *slots[i];  // KernelBase's function
        void* old = *slots[i];
        if (!Swap(slots[i], hooks[i])) break;
        s_saved.push_back({slots[i], old});
        done++;
    }
    if (done != 4) IoUninstall();
    return done == 4 ? 4 : 0;
}

typedef HANDLE(WINAPI* CreateFileA_t)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
typedef HANDLE(WINAPI* CreateFileW_t)(LPCWSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
static CreateFileA_t s_realCreateFileA;
static CreateFileW_t s_realCreateFileW;
static IoRedirect_t s_redirect;

static HANDLE WINAPI Hook_CreateFileA(LPCSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                      DWORD flags, HANDLE tmpl)
{
    char other[MAX_PATH];
    if (name && s_redirect && s_redirect(name, other, sizeof(other))) name = other;
    return s_realCreateFileA(name, access, share, sa, disp, flags, tmpl);
}

static HANDLE WINAPI Hook_CreateFileW(LPCWSTR name, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                      DWORD flags, HANDLE tmpl)
{
    // Bink and FMOD may open their files wide: names the ANSI code page holds exactly are offered for redirection
    // (a game folder with umlauts included; the redirect and the rest of the fix work with ANSI paths)
    char a[MAX_PATH], other[MAX_PATH];
    WCHAR w[MAX_PATH];
    if (name && s_redirect) {
        BOOL lossy = FALSE;
        if (WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, name, -1, a, MAX_PATH, NULL, &lossy) && !lossy &&
            s_redirect(a, other, sizeof(other)) && MultiByteToWideChar(CP_ACP, 0, other, -1, w, MAX_PATH))
            name = w;
    }
    return s_realCreateFileW(name, access, share, sa, disp, flags, tmpl);
}

// The game asks whether a file exists through its "" device's file finder (0x402300 -> WinFileFinder 0x573550),
// which calls FindFirstFileA: the level constructor 0x7C6140 builds its sector texture set only when
// <level>.Static.ark is found that way (0x7C2FE0). Names are redirected there as for CreateFile. The search name
// is a plain path (no wildcard) for these checks; a pattern is not one of the redirect's known names anyway.
typedef HANDLE(WINAPI* FindFirstFileA_t)(LPCSTR, LPWIN32_FIND_DATAA);
static FindFirstFileA_t s_realFindFirstFileA;

static HANDLE WINAPI Hook_FindFirstFileA(LPCSTR name, LPWIN32_FIND_DATAA data)
{
    char other[MAX_PATH];
    if (name && s_redirect && s_redirect(name, other, sizeof(other))) {
        // its own line: the test log shows that the game's existence check (0x573550) was sent there too
        static volatile LONG logged;
        HANDLE h = s_realFindFirstFileA(other, data);
        if (InterlockedIncrement(&logged) <= 8)
            Log("DLC tracks: existence check of %s found it at %s: %s", name, other,
                h != INVALID_HANDLE_VALUE ? "yes" : "no");
        return h;
    }
    return s_realFindFirstFileA(name, data);
}

int IoInstallCreateFileRedirect(IoRedirect_t fn)
{
    static const char* const names[3] = {"CreateFileA", "CreateFileW", "FindFirstFileA"};
    void* const hooks[3] = {(void*)Hook_CreateFileA, (void*)Hook_CreateFileW, (void*)Hook_FindFirstFileA};
    void** real[3] = {(void**)&s_realCreateFileA, (void**)&s_realCreateFileW, (void**)&s_realFindFirstFileA};
    if (s_redirect) return 0;  // once
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    void** slots[3];
    for (int i = 0; i < 3; i++) {
        const BYTE* p = (const BYTE*)GetProcAddress(k32, names[i]);
        if (!p || p[0] != 0xFF || p[1] != 0x25) return 0;  // not a jmp [slot] thunk: leave everything alone
        slots[i] = *(void***)(p + 2);
        HMODULE owner = NULL;
        if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                (LPCSTR)slots[i], &owner) || owner != k32 || !*slots[i])
            return 0;
    }
    s_saved.reserve(s_saved.size() + 3);  // before any slot changes: a swapped slot is always in the list
    s_redirect = fn;
    int done = 0;
    for (int i = 0; i < 3; i++) {
        *real[i] = *slots[i];  // KernelBase's function, set before the slot points at the hook
        void* old = *slots[i];
        if (!Swap(slots[i], hooks[i])) break;
        s_saved.push_back({slots[i], old});
        done++;
    }
    if (done != 3) {  // all three or none: a level found by CreateFile but not by FindFirstFile would crash
        for (int i = 0; i < done; i++) {
            Swap(s_saved.back().slot, s_saved.back().old);
            s_saved.pop_back();
        }
        s_redirect = NULL;
        return 0;
    }
    return done;
}

void IoUninstall()
{
    for (size_t i = s_saved.size(); i-- > 0;) Swap(s_saved[i].slot, s_saved[i].old);
    s_saved.clear();
}
