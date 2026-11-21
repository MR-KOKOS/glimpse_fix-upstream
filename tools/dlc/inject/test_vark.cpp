// Offline harness for the virtual archive hooks (no game process). Run test_vark.py first; it writes
// test_out\expect.txt (base and overlay paths, entry sizes and CRCs). This program builds the same virtual
// archive, opens the base file the way the game's stream does (0x573490: GENERIC_READ, FILE_SHARE_READ,
// FILE_FLAG_OVERLAPPED | FILE_FLAG_SEQUENTIAL_SCAN, then GetFileSize), reads the header, the index and
// the entries through the hooks like ArkLoader (seek = OVERLAPPED.Offset, ReadFile, GetOverlappedResult on
// ERROR_IO_PENDING, Offset += requested size), and compares with the expectations. Three passes:
//   1. the hook functions called directly, overlapped handle (the game's case)
//   2. the hook functions called directly, synchronous handle (SetFilePointer path)
//   3. this process's kernel32 import slots swapped (IoInstallKernel32Slots), plain API calls
//
//   test_vark.exe <expect.txt>
// Built from the DLL's own src\vark.cpp and src\iohook.cpp (build_test.bat).
#include "iohook.h"
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <string>
#include <vector>

// the DLL's log (main.cpp), here on the console
void Log(const char* fmt, ...)
{
    va_list a;
    va_start(a, fmt);
    printf("log: ");
    vprintf(fmt, a);
    printf("\n");
    va_end(a);
}

namespace {

int g_fails;

void Check(bool ok, const char* fmt, ...)
{
    if (ok) return;
    g_fails++;
    va_list a;
    va_start(a, fmt);
    printf("FAIL ");
    vprintf(fmt, a);
    printf("\n");
    va_end(a);
}

uint32_t Crc32(const uint8_t* p, size_t n)
{
    static uint32_t table[256];
    if (!table[1])
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            table[i] = c;
        }
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) c = table[(c ^ p[i]) & 0xFF] ^ (c >> 8);
    return c ^ 0xFFFFFFFFu;
}

// the archives' LZSS (as texdump.cpp)
bool Lzss(const uint8_t* src, uint32_t srcLen, uint8_t* out, uint32_t size)
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

struct Api {  // what the "game" calls: the hooks directly, or the plain kernel32 functions
    ReadFile_t ReadFile;
    SetFilePointer_t SetFilePointer;
    GetFileSize_t GetFileSize;
    CloseHandle_t CloseHandle;
};

// The game's buffered stream: fills its buffer at buffer-aligned positions (0x5728E0, 0x572850), the file
// layer seeks through OVERLAPPED.Offset (0x5730B0) or SetFilePointer and reads with ReadFile (0x5730F0).
struct GameStream {
    const Api* api;
    HANDLE h;
    OVERLAPPED* ov;  // NULL: synchronous handle
    uint32_t bufSize, size;
    std::vector<uint8_t> buf;
    uint32_t bufPos = 0, bufLen = 0, pos = 0;

    bool Fill(uint32_t at)
    {
        uint32_t want = bufSize;
        if (at >= size) return false;
        if (want > size - at) want = size - at;
        buf.resize(bufSize);
        DWORD got = 0;
        BOOL ok;
        if (ov) {
            ov->Offset = at;  // seek
            ov->OffsetHigh = 0;
            ok = api->ReadFile(h, buf.data(), want, &got, ov);
            if (!ok && GetLastError() == ERROR_IO_PENDING) ok = GetOverlappedResult(h, ov, &got, TRUE);
            if (ok) ov->Offset += want;  // the game adds the requested size
        } else {
            LONG hi = 0;
            api->SetFilePointer(h, (LONG)at, &hi, FILE_BEGIN);
            ok = api->ReadFile(h, buf.data(), want, &got, NULL);
        }
        if (!ok || got != want) return false;
        bufPos = at;
        bufLen = got;
        return true;
    }
    bool Read(void* dst, uint32_t n)
    {
        uint8_t* d = (uint8_t*)dst;
        while (n) {
            if (pos < bufPos || pos >= bufPos + bufLen)
                if (!Fill(pos - pos % bufSize)) return false;
            uint32_t o = pos - bufPos, take = bufLen - o;
            if (take > n) take = n;
            memcpy(d, buf.data() + o, take);
            d += take;
            pos += take;
            n -= take;
        }
        return true;
    }
};

struct Expect {
    std::string name;  // path, or #KEY for base samples
    uint32_t key, type, csize, size, rawCrc, decCrc;
    bool hasDec;
};

struct Expectations {
    std::string base;
    std::vector<std::string> overlays;
    uint32_t vsize = 0, delta = 0, headCrc = 0, entries = 0;
    std::vector<Expect> list;
};

bool LoadExpect(const char* fn, Expectations* x)
{
    FILE* f = fopen(fn, "r");
    if (!f) return false;
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0] || line[0] == '#' && line[1] == ' ') continue;
        if (!strncmp(line, "base ", 5)) x->base = line + 5;
        else if (!strncmp(line, "overlay ", 8)) x->overlays.push_back(line + 8);
        else if (!strncmp(line, "vsize ", 6))
            sscanf(line, "vsize %u delta %u headcrc %x entries %u", &x->vsize, &x->delta, &x->headCrc, &x->entries);
        else {
            Expect e = {};
            char name[512], dec[16];
            if (sscanf(line, "%511s %u %u %u %x %15s", name, &e.type, &e.csize, &e.size, &e.rawCrc, dec) != 6) continue;
            e.name = name;
            e.key = name[0] == '#' ? (uint32_t)strtoul(name + 1, NULL, 16) : ArkKey(name);
            e.hasDec = strcmp(dec, "-") != 0;
            if (e.hasDec) e.decCrc = (uint32_t)strtoul(dec, NULL, 16);
            x->list.push_back(e);
        }
    }
    fclose(f);
    return !x->base.empty();
}

// opens the base archive as the game does and checks everything in x through api
void Pass(const char* title, const Api& api, const Expectations& x, bool overlapped, uint32_t bufSize)
{
    printf("-- %s (%s handle, %u byte buffer)\n", title, overlapped ? "overlapped" : "synchronous", bufSize);
    int before = g_fails;
    HANDLE h = CreateFileA(x.base.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                           (overlapped ? FILE_FLAG_OVERLAPPED : 0) | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    Check(h != INVALID_HANDLE_VALUE, "open %s", x.base.c_str());
    if (h == INVALID_HANDLE_VALUE) return;
    DWORD size = api.GetFileSize(h, NULL);  // the game's first call on the new handle (0x5734E1)
    Check(size == x.vsize, "GetFileSize %u, expected %u", size, x.vsize);
    Check(IoVirtualHandleCount() == 1, "handle registered");
    OVERLAPPED ov = {};  // the game allocates a zeroed one (0x5733A0)
    GameStream s{&api, h, overlapped ? &ov : NULL, bufSize, size};
    // ArkLoader: u32 version/compression, u32 index size, the index block
    uint32_t magic = 0, hsize = 0;
    Check(s.Read(&magic, 4) && s.Read(&hsize, 4), "header read");
    Check(magic == 0x10000, "magic %08X", magic);
    std::vector<uint8_t> block(hsize);
    Check(hsize >= 16 && s.Read(block.data(), hsize), "index read");
    const uint32_t* b = (const uint32_t*)block.data();
    const uint32_t cA = b[0], offA = b[1], cB = b[2];
    Check(cB * 20 + 16 + cA * 20 == hsize, "index size check (0x5712F0)");
    Check(cA == x.entries, "entry count %u, expected %u", cA, x.entries);
    std::vector<uint8_t> head(8 + hsize);
    memcpy(head.data(), &magic, 4);
    memcpy(head.data() + 4, &hsize, 4);
    memcpy(head.data() + 8, block.data(), hsize);
    Check(Crc32(head.data(), head.size()) == x.headCrc, "merged index CRC");
    const ArkEntry* ents = (const ArkEntry*)(block.data() + offA);
    std::vector<uint8_t> raw, dec;
    int checked = 0;
    for (const Expect& e : x.list) {
        // 0x5710C0: binary search, unsigned keys
        int lo = 0, hi = (int)cA - 1;
        const ArkEntry* hit = NULL;
        while (lo <= hi) {
            int mid = (lo + hi) / 2;
            if (ents[mid].key == e.key) {
                hit = &ents[mid];
                break;
            }
            if (e.key < ents[mid].key) hi = mid - 1;
            else lo = mid + 1;
        }
        Check(hit != NULL, "%s not in the index", e.name.c_str());
        if (!hit) continue;
        Check(hit->type == e.type && hit->csize == e.csize && hit->size == e.size, "%s entry fields", e.name.c_str());
        raw.resize(hit->csize);
        s.pos = hit->offset;  // member open seeks to the entry offset (0x571522)
        bool ok = !hit->csize || s.Read(raw.data(), hit->csize);
        Check(ok, "%s read", e.name.c_str());
        if (!ok) continue;
        Check(Crc32(raw.data(), raw.size()) == e.rawCrc, "%s raw CRC", e.name.c_str());
        if (e.hasDec) {
            if (hit->type == 1) {
                dec.resize(hit->size);
                Check(hit->csize >= 4 && Lzss(raw.data() + 4, hit->csize - 4, dec.data(), hit->size), "%s LZSS",
                      e.name.c_str());
                Check(Crc32(dec.data(), dec.size()) == e.decCrc, "%s decoded CRC", e.name.c_str());
            } else {
                Check(Crc32(raw.data(), raw.size()) == e.decCrc, "%s CRC", e.name.c_str());
            }
        }
        checked++;
    }
    // a read at the very end and past it (the game never does, the hook must not crash)
    if (overlapped) {
        uint8_t t[64];
        DWORD got = 0;
        ov.Offset = size - 8;
        BOOL r = api.ReadFile(h, t, sizeof(t), &got, &ov);
        if (!r && GetLastError() == ERROR_IO_PENDING) r = GetOverlappedResult(h, &ov, &got, TRUE);
        Check(r && got == 8, "short read at the end (%u)", got);
        ov.Offset = size + 100;
        r = api.ReadFile(h, t, sizeof(t), &got, &ov);
        Check(!r && GetLastError() == ERROR_HANDLE_EOF, "read past the end");
    }
    api.CloseHandle(h);
    Check(IoVirtualHandleCount() == 0, "handle unregistered on close");
    printf("   %d entries checked, %s\n", checked, g_fails == before ? "ok" : "FAILED");
}

}  // namespace

int main(int argc, char** argv)
{
    const char* fn = argc > 1 ? argv[1] : "test_out\\expect.txt";
    Expectations x;
    if (!LoadExpect(fn, &x)) {
        printf("cannot read %s (run test_vark.py first)\n", fn);
        return 2;
    }
    VirtualArchive va;
    std::string err;
    DWORD t0 = GetTickCount();
    if (!va.Build(x.base.c_str(), x.overlays, &err)) {
        printf("build: %s\n", err.c_str());
        return 2;
    }
    printf("virtual archive: %u bytes, delta %u, %zu segments, built in %u ms\n", va.Size(), va.Delta(),
           va.Segments().size(), GetTickCount() - t0);
    Check(va.Size() == x.vsize && va.Delta() == x.delta, "layout as vark.py (size %u/%u, delta %u/%u)", va.Size(),
          x.vsize, va.Delta(), x.delta);
    Check(Crc32(va.Head().data(), va.Head().size()) == x.headCrc, "head as vark.py");

    IoInitReal();
    Check(IoAddVirtualFile(x.base.c_str(), &va), "register %s", x.base.c_str());
    Api hooks = {Hook_ReadFile, Hook_SetFilePointer, Hook_GetFileSize, Hook_CloseHandle};
    Pass("1 hooks, direct", hooks, x, true, 0x10000);
    Pass("1b hooks, direct", hooks, x, true, 0x3000);
    Pass("2 hooks, direct", hooks, x, false, 0x10000);

    // pass-through: a handle that is not the archive reads as itself
    {
        HANDLE f = CreateFileA(fn, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
        char a[64] = {}, b[64] = {};
        DWORD ga = 0, gb = 0;
        Hook_GetFileSize(f, NULL);
        Hook_ReadFile(f, a, sizeof(a), &ga, NULL);
        SetFilePointer(f, 0, NULL, FILE_BEGIN);
        ReadFile(f, b, sizeof(b), &gb, NULL);
        Check(ga == gb && !memcmp(a, b, sizeof(a)) && IoVirtualHandleCount() == 0, "pass-through");
        Hook_CloseHandle(f);
    }

    int n = IoInstallKernel32Slots();
    Check(n == 4, "kernel32 import slots swapped (%d)", n);
    if (n == 4) {
        Api plain = {ReadFile, SetFilePointer, GetFileSize, CloseHandle};  // kernel32's exports
        Pass("3 kernel32 slots, plain API calls", plain, x, true, 0x10000);
        IoUninstall();
    }
    printf("stats: %ld registered, %ld synthetic reads, %ld translated, %ld passed through\n",
           g_ioStats.registered, g_ioStats.synthetic, g_ioStats.translated, g_ioStats.passthrough);
    printf(g_fails ? "FAILED: %d\n" : "PASS\n", g_fails);
    return g_fails ? 1 : 0;
}
