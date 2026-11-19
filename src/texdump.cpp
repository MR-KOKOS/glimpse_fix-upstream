// Split/Second PC fix - DumpTextures=1: the game's textures as DDS files
//
// The game's .ark archives (read by its ArkLoader) hold every texture as an entry of its own: a 136 byte
// "TEXS" block and an ordinary DDS file, packed with LZSS or stored. The dump walks each archive's index,
// unpacks the texture entries and writes their DDS files into a folder per archive under glimpse_textures,
// with an index.txt that lists every file with its size, format and mip levels.
//
// It runs once, when the game asks for Direct3D (before its window goes full screen): a small window shows
// the progress, then DumpTextures is set back to 0 in the INI and the game ends. Only the archive files are
// read; nothing in the game is changed.
#include "common.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <string>
#ifndef MAKEFOURCC
#define MAKEFOURCC(a, b, c, d) ((DWORD)(BYTE)(a) | ((DWORD)(BYTE)(b) << 8) | ((DWORD)(BYTE)(c) << 16) | ((DWORD)(BYTE)(d) << 24))
#endif
#include <vector>

namespace {

// the archives' LZSS: flag byte (bit set = literal, low bit first), a reference is two bytes, a 12 bit
// position in a 4096 byte window (starting at 0xFEE, zeroed) and a 4 bit length + 3
bool LzssMem(const uint8_t* src, uint32_t srcLen, uint8_t* out, uint32_t size)
{
    uint8_t win[4096] = {};
    unsigned pos = 0xFEE, flags = 0;
    uint32_t n = 0, i = 0;
    while (n < size) {
        flags >>= 1;
        if (!(flags & 0x100)) {
            if (i >= srcLen) return false;
            flags = (unsigned)src[i++] | 0xFF00;
        }
        if (flags & 1) {
            if (i >= srcLen) return false;
            uint8_t c = src[i++];
            out[n++] = c;
            win[pos] = c;
            pos = (pos + 1) & 0xFFF;
        } else {
            if (i + 1 >= srcLen) return false;
            unsigned a = src[i], b = src[i + 1];
            i += 2;
            unsigned o = a | ((b & 0xF0) << 4), len = (b & 0x0F) + 3;
            for (unsigned k = 0; k < len && n < size; k++) {
                uint8_t c = win[(o + k) & 0xFFF];
                out[n++] = c;
                win[pos] = c;
                pos = (pos + 1) & 0xFFF;
            }
        }
    }
    return true;
}

struct DdsInfo {
    uint32_t width, height, mips, faces;
    char format[16];
    uint64_t size;  // header and all data
};

uint32_t U32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

// the size a DDS file has by its header (2D textures and cube maps), false when the header makes no sense
bool ParseDds(const uint8_t* h, DdsInfo* d)
{
    if (memcmp(h, "DDS |", 5) || U32(h + 4) != 124 || U32(h + 76) != 32) return false;
    d->height = U32(h + 12);
    d->width = U32(h + 16);
    d->mips = U32(h + 28) ? U32(h + 28) : 1;
    uint32_t pfFlags = U32(h + 80), fourcc = U32(h + 84), bits = U32(h + 88), caps2 = U32(h + 112);
    if (!d->width || !d->height || d->width > 16384 || d->height > 16384 || d->mips > 16) return false;
    if (caps2 & 0x200000) return false;  // volume textures are not expected here
    d->faces = (caps2 & 0x200) ? 6 : 1;
    uint32_t block = 0, bpp = 0;
    if (pfFlags & 4) {
        memcpy(d->format, &fourcc, 4);
        d->format[4] = 0;
        if (fourcc == MAKEFOURCC('D', 'X', 'T', '1') || fourcc == MAKEFOURCC('A', 'T', 'I', '1')) block = 8;
        else if (fourcc == MAKEFOURCC('D', 'X', 'T', '2') || fourcc == MAKEFOURCC('D', 'X', 'T', '3') ||
                 fourcc == MAKEFOURCC('D', 'X', 'T', '4') || fourcc == MAKEFOURCC('D', 'X', 'T', '5') ||
                 fourcc == MAKEFOURCC('A', 'T', 'I', '2'))
            block = 16;
        else if (fourcc == 113) { strcpy_s(d->format, "A16B16G16R16F"); bpp = 64; }
        else if (fourcc == 116) { strcpy_s(d->format, "A32B32G32R32F"); bpp = 128; }
        else if (fourcc == 111) { strcpy_s(d->format, "R16F"); bpp = 16; }
        else if (fourcc == 114) { strcpy_s(d->format, "R32F"); bpp = 32; }
        else return false;
    } else {
        bpp = bits;
        if (bpp != 8 && bpp != 16 && bpp != 24 && bpp != 32) return false;
        sprintf_s(d->format, "RGB%u", bpp);
    }
    uint64_t total = 0;
    for (uint32_t m = 0; m < d->mips; m++) {
        uint32_t w = d->width >> m ? d->width >> m : 1, hh = d->height >> m ? d->height >> m : 1;
        total += block ? (uint64_t)((w + 3) / 4) * ((hh + 3) / 4) * block : (uint64_t)w * hh * bpp / 8;
    }
    d->size = 128 + total * d->faces;
    return d->size <= (64u << 20);
}

volatile LONG s_percent;
volatile LONG s_done;
char s_status[300];
CRITICAL_SECTION s_statusLock;

void SetStatus(const char* fmt, ...)
{
    char t[300];
    va_list a;
    va_start(a, fmt);
    vsprintf_s(t, fmt, a);
    va_end(a);
    EnterCriticalSection(&s_statusLock);
    strcpy_s(s_status, t);
    LeaveCriticalSection(&s_statusLock);
}

void FindArks(const std::string& dir, std::vector<std::string>* out)
{
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((dir + "\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        std::string name = fd.cFileName;
        if (name == "." || name == "..") continue;
        std::string path = dir + "\\" + name;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) FindArks(path, out);
        else if (name.size() > 4 && !_stricmp(name.c_str() + name.size() - 4, ".ark")) out->push_back(path);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

bool WriteAll(const std::string& path, const void* data, size_t len)
{
    HANDLE f = CreateFileA(path.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return false;
    DWORD w = 0;
    bool ok = WriteFile(f, data, (DWORD)len, &w, NULL) && w == len;
    CloseHandle(f);
    return ok;
}

struct DumpTotals {
    unsigned files, packed, stored, skipped;
    uint64_t bytes;
};

// one archive, by its index (ArkLoader, 0x5710C0): {u32 version 0x10000, u32 header size}, then the
// header {count, offset of the entries, 0, ...} and count entries of 20 bytes {type (0 stored, 1 LZSS),
// file offset, packed size, size, key}; the key is the name's hash (h = h * 31 + lower case char, '/' as
// separator). A packed entry starts with its u32 size, then the LZSS stream. A texture entry is a 136 byte
// "TEXS" block followed by one DDS file.
void DumpArchive(const std::string& arkPath, const std::string& outDir, uint64_t doneBefore, uint64_t allBytes,
                 DumpTotals* totals)
{
    HANDLE f = CreateFileA(arkPath.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) {
        Log("textures: %s could not be opened", arkPath.c_str());
        return;
    }
    LARGE_INTEGER fsz;
    GetFileSizeEx(f, &fsz);
    uint64_t fileSize = (uint64_t)fsz.QuadPart;
    auto readAt = [&](uint64_t pos, void* dst, uint32_t len) {
        LARGE_INTEGER li;
        li.QuadPart = (LONGLONG)pos;
        DWORD got = 0;
        return SetFilePointerEx(f, li, NULL, FILE_BEGIN) && ReadFile(f, dst, len, &got, NULL) && got == len;
    };
    uint32_t head[6];
    if (!readAt(0, head, sizeof(head)) || head[0] != 0x10000 || head[2] > 200000 || head[3] + 8 + head[2] * 20ull > fileSize) {
        Log("textures: %s is not an archive of the known kind", arkPath.c_str());
        CloseHandle(f);
        return;
    }
    uint32_t count = head[2];
    std::vector<uint32_t> idx(count * 5);
    if (!readAt(8 + head[3], idx.data(), count * 20)) {
        CloseHandle(f);
        return;
    }
    std::string index;
    unsigned written = 0;
    std::vector<uint8_t> packed, out;
    for (uint32_t k = 0; k < count; k++) {
        uint32_t type = idx[k * 5], off = idx[k * 5 + 1], csize = idx[k * 5 + 2], usize = idx[k * 5 + 3], key = idx[k * 5 + 4];
        InterlockedExchange(&s_percent, (LONG)((doneBefore + (uint64_t)off) * 1000 / (allBytes ? allBytes : 1)));
        if (usize < 136 + 128 || usize > (96u << 20) || csize > (96u << 20) || (uint64_t)off + csize > fileSize) continue;
        if (type == 0) {
            out.resize(usize);
            if (!readAt(off, out.data(), usize)) continue;
        } else if (type == 1) {
            if (csize < 8) continue;
            packed.resize(csize);
            if (!readAt(off, packed.data(), csize)) continue;
            out.resize(usize);
            if (!LzssMem(packed.data() + 4, csize - 4, out.data(), usize)) continue;
        } else {
            continue;
        }
        if (memcmp(out.data(), "SXET", 4) || memcmp(out.data() + 136, "DDS |", 5)) continue;
        DdsInfo info;
        if (!ParseDds(out.data() + 136, &info) || 136 + info.size > usize) {
            totals->skipped++;
            continue;
        }
        char name[32];
        sprintf_s(name, "%08X.dds", key);
        if (!WriteAll(outDir + "\\" + name, out.data() + 136, (size_t)info.size)) continue;
        char line[240];
        sprintf_s(line, "%s  %ux%u  %s  mips %u%s  %llu bytes  %s\r\n", name, info.width, info.height, info.format, info.mips,
                  info.faces == 6 ? "  cube map" : "", (unsigned long long)info.size, type ? "packed" : "stored");
        index += line;
        written++;
        totals->files++;
        if (type) totals->packed++;
        else totals->stored++;
        totals->bytes += info.size;
    }
    CloseHandle(f);
    if (written) {
        std::string text = "glimpse_fix texture dump of " + arkPath + "\r\n" + std::to_string(written) +
                           " textures. A file's name is the archive key of its texture (the hash of the game's own"
                           " file name, which the archive does not keep).\r\n\r\n";
        text += index;
        WriteAll(outDir + "\\index.txt", text.data(), text.size());
    } else {
        RemoveDirectoryA(outDir.c_str());
    }
    Log("textures: %s: %u", arkPath.c_str(), written);
}

std::string s_gameDir, s_outRoot;  // the game folder and the dump folder in it

DWORD WINAPI DumpThread(void*)
{
    std::string data = s_gameDir + "\\data", outRoot = s_outRoot;
    std::vector<std::string> arks;
    FindArks(data, &arks);
    uint64_t all = 0;
    std::vector<uint64_t> sizes;
    for (const std::string& a : arks) {
        WIN32_FILE_ATTRIBUTE_DATA fa;
        uint64_t s = GetFileAttributesExA(a.c_str(), GetFileExInfoStandard, &fa) ? ((uint64_t)fa.nFileSizeHigh << 32) | fa.nFileSizeLow : 0;
        sizes.push_back(s);
        all += s;
    }
    CreateDirectoryA(outRoot.c_str(), NULL);
    Log("textures: dump of %u archives (%llu MB) to %s", (unsigned)arks.size(), (unsigned long long)(all >> 20), outRoot.c_str());
    DumpTotals totals = {};
    uint64_t done = 0;
    for (size_t i = 0; i < arks.size(); i++) {
        // folder: the archive's path below data, with \ as _ and without .ark
        std::string rel = arks[i].substr(data.size() + 1);
        rel = rel.substr(0, rel.size() - 4);
        for (char& c : rel)
            if (c == '\\' || c == '/') c = '_';
        std::string dir = outRoot + "\\" + rel;
        CreateDirectoryA(dir.c_str(), NULL);
        SetStatus("Archive %u of %u: %s  (%u textures so far)", (unsigned)(i + 1), (unsigned)arks.size(), rel.c_str(), totals.files);
        DumpArchive(arks[i], dir, done, all, &totals);
        done += sizes[i];
    }
    Log("textures: done, %u textures (%u packed, %u stored, %u other DDS marks), %llu MB", totals.files, totals.packed,
        totals.stored, totals.skipped, (unsigned long long)(totals.bytes >> 20));
    SetStatus("Done: %u textures (%llu MB) in glimpse_textures. The game closes now.", totals.files,
              (unsigned long long)(totals.bytes >> 20));
    InterlockedExchange(&s_percent, 1000);
    InterlockedExchange(&s_done, 1);
    return 0;
}

LRESULT CALLBACK DumpWndProc(HWND w, UINT m, WPARAM wp, LPARAM lp)
{
    if (m == WM_PAINT) {
        PAINTSTRUCT ps;
        HDC dc = BeginPaint(w, &ps);
        RECT r;
        GetClientRect(w, &r);
        HBRUSH bg = CreateSolidBrush(RGB(24, 25, 28));
        FillRect(dc, &r, bg);
        DeleteObject(bg);
        SetBkMode(dc, TRANSPARENT);
        HFONT font = CreateFontA(-18, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI");
        HGDIOBJ old = SelectObject(dc, font);
        SetTextColor(dc, RGB(255, 120, 20));
        RECT t = { 24, 20, r.right - 24, 50 };
        DrawTextA(dc, "glimpse_fix: dumping the game's textures, please wait", -1, &t, DT_SINGLELINE | DT_LEFT);
        SelectObject(dc, old);
        DeleteObject(font);
        font = CreateFontA(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI");
        old = SelectObject(dc, font);
        SetTextColor(dc, RGB(220, 222, 228));
        char status[300];
        EnterCriticalSection(&s_statusLock);
        strcpy_s(status, s_status);
        LeaveCriticalSection(&s_statusLock);
        RECT s = { 24, 60, r.right - 24, 84 };
        DrawTextA(dc, status, -1, &s, DT_SINGLELINE | DT_LEFT | DT_END_ELLIPSIS);
        RECT bar = { 24, 100, r.right - 24, 118 };
        HBRUSH track = CreateSolidBrush(RGB(46, 48, 55)), fill = CreateSolidBrush(RGB(255, 120, 20));
        FillRect(dc, &bar, track);
        RECT f = bar;
        f.right = bar.left + (LONG)((bar.right - bar.left) * (int64_t)s_percent / 1000);
        FillRect(dc, &f, fill);
        DeleteObject(track);
        DeleteObject(fill);
        SelectObject(dc, old);
        DeleteObject(font);
        EndPaint(w, &ps);
        return 0;
    }
    if (m == WM_CLOSE) return 0;  // runs to the end
    return DefWindowProcA(w, m, wp, lp);
}

}  // namespace

#ifdef TEXDUMP_TEST
void TexDumpTest(const char* gameDir, const char* outRoot)
{
    InitializeCriticalSection(&s_statusLock);
    s_gameDir = gameDir;
    s_outRoot = outRoot;
    DumpThread(NULL);
    printf("%s\n", s_status);
}
#endif

void RunTextureDump()
{
    char exe[MAX_PATH];
    GetModuleFileNameA(NULL, exe, MAX_PATH);
    s_gameDir = exe;
    s_gameDir = s_gameDir.substr(0, s_gameDir.find_last_of('\\'));
    s_outRoot = s_gameDir + "\\glimpse_textures";
    InitializeCriticalSection(&s_statusLock);
    SetStatus("Looking for the archives ...");
    WNDCLASSA wc = {};
    wc.lpfnWndProc = DumpWndProc;
    wc.hInstance = GetModuleHandleA(NULL);
    wc.hCursor = LoadCursor(NULL, IDC_WAIT);
    wc.lpszClassName = "glimpse_texdump";
    RegisterClassA(&wc);
    int w = 640, h = 150;
    HWND wnd = CreateWindowExA(WS_EX_TOPMOST, wc.lpszClassName, "glimpse_fix - texture dump", WS_POPUP | WS_CAPTION | WS_VISIBLE,
                               (GetSystemMetrics(SM_CXSCREEN) - w) / 2, (GetSystemMetrics(SM_CYSCREEN) - h) / 2, w, h, NULL,
                               NULL, wc.hInstance, NULL);
    HANDLE th = CreateThread(NULL, 0, DumpThread, NULL, 0, NULL);
    DWORD doneAt = 0;
    for (;;) {
        MSG msg;
        while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageA(&msg);
        }
        if (wnd) InvalidateRect(wnd, NULL, FALSE);
        if (s_done && !doneAt) doneAt = GetTickCount();
        if (doneAt && GetTickCount() - doneAt > 4000) break;
        MsgWaitForMultipleObjects(0, NULL, FALSE, 200, QS_ALLINPUT);
    }
    if (th) CloseHandle(th);
    // switched off again, so the next start is a normal one
    char ini[MAX_PATH];
    ModulePath(ini, "SplitSecondPad.ini");
    WritePrivateProfileStringA("Debug", "DumpTextures", "0", ini);
    Log("textures: DumpTextures set back to 0, the game ends");
    ExitProcess(0);
}
