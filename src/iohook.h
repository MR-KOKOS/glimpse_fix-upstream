// File I/O hooks that make an archive handle read as a VirtualArchive (prototype, see DESIGN.md).
//
// The game opens an archive with CreateFileA(GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING,
// FILE_FLAG_OVERLAPPED | FILE_FLAG_SEQUENTIAL_SCAN) and calls GetFileSize right after it (0x573490); it
// seeks by writing OVERLAPPED.Offset (0x5730B0) and reads with ReadFile + OVERLAPPED, adding the requested
// size to Offset on success and calling GetOverlappedResult on ERROR_IO_PENDING (0x5730F0, 0x573180).
// CreateFileA is not hooked: the game's import slot holds SecuROM's wrapper. A handle becomes virtual in the
// GetFileSize hook when it is the registered file (same volume and file index).
#pragma once
#include "vark.h"

typedef BOOL(WINAPI* ReadFile_t)(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
typedef DWORD(WINAPI* SetFilePointer_t)(HANDLE, LONG, PLONG, DWORD);
typedef DWORD(WINAPI* GetFileSize_t)(HANDLE, LPDWORD);
typedef BOOL(WINAPI* CloseHandle_t)(HANDLE);

struct IoReal {  // the functions the hooks forward to (what the swapped slots held)
    ReadFile_t ReadFile;
    SetFilePointer_t SetFilePointer;
    GetFileSize_t GetFileSize;
    CloseHandle_t CloseHandle;
};
extern IoReal g_ioReal;

void IoInitReal();  // g_ioReal = kernel32's exports (for the offline test before anything is swapped)
// path: the archive file to present as va (e.g. <game>\data\resident.ark); va must outlive the hooks
bool IoAddVirtualFile(const char* path, const VirtualArchive* va);
size_t IoVirtualHandleCount();

BOOL WINAPI Hook_ReadFile(HANDLE h, LPVOID buf, DWORD n, LPDWORD got, LPOVERLAPPED ov);
DWORD WINAPI Hook_SetFilePointer(HANDLE h, LONG dist, PLONG high, DWORD method);
DWORD WINAPI Hook_GetFileSize(HANDLE h, LPDWORD high);
BOOL WINAPI Hook_CloseHandle(HANDLE h);

struct IoStats {
    volatile LONG synthetic, translated, passthrough, registered;
};
extern IoStats g_ioStats;

// Installation (a data write, no code is changed): kernel32's own import slots behind its export thunks
// (FF 25 = jmp [slot]); works from DllMain, before the game's import table exists, and catches every kernel32
// caller in the process. The game's own import slots (SecuROM fills them) are not touched. Returns 4, or 0 when
// not all four could be swapped (then none is); a second call with the hooks in place returns 4 again.
int IoInstallKernel32Slots();
void IoUninstall();  // puts the saved slot values back

// File name redirection ([DLC] Tracks): kernel32's own import slots for CreateFileA, CreateFileW and FindFirstFileA
// (the same data write as above; the game's slot 0xB9B1A8 with SecuROM's wrapper is not touched). fn gets every
// opened or searched name as ANSI and returns true with another name to use instead. Returns 3, or 0 when not
// all three could be swapped (then none is).
typedef bool (*IoRedirect_t)(const char* path, char* out, size_t outSize);
int IoInstallCreateFileRedirect(IoRedirect_t fn);
