// Virtual archive (see vark.h, DESIGN.md)
#include "vark.h"
#include <algorithm>
#include <map>
#include <string.h>

namespace {

const uint32_t kMagic = 0x00010000;   // version 1, no stream compression (0x5718E0, 0x571200)
const uint32_t kLimit = 0x7FF00000;   // the game's stream positions are signed 32-bit (0x5728E0); headroom
const uint32_t kAlign = 16;

bool ReadAt(HANDLE f, uint64_t pos, void* dst, uint32_t n, uint32_t* got)
{
    OVERLAPPED ov = {};
    ov.Offset = (DWORD)pos;
    ov.OffsetHigh = (DWORD)(pos >> 32);
    DWORD g = 0;
    // positional read on a synchronous handle: blocks, does not depend on the file pointer
    if (!ReadFile(f, dst, n, &g, &ov)) {
        if (GetLastError() != ERROR_HANDLE_EOF) return false;
        g = 0;
    }
    *got = g;
    return true;
}

}  // namespace

uint32_t ArkKey(const char* path)
{
    uint32_t h = 0;
    for (const unsigned char* p = (const unsigned char*)path; *p; p++) {
        unsigned c = *p == '\\' ? '/' : *p;
        if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
        h = h * 31 + c;
    }
    return h;
}

bool ReadArkIndex(HANDLE f, uint32_t* hsize, uint32_t* fileSize, std::vector<ArkEntry>* out, std::string* err)
{
    LARGE_INTEGER sz;
    if (!GetFileSizeEx(f, &sz) || sz.QuadPart > kLimit) {
        *err = "size";
        return false;
    }
    uint32_t head[6], got = 0;
    if (!ReadAt(f, 0, head, sizeof(head), &got) || got != sizeof(head)) {
        *err = "header";
        return false;
    }
    if (head[0] != kMagic) {
        *err = "not a PC archive (magic)";
        return false;
    }
    uint32_t cA = head[2], offA = head[3], cB = head[4];
    // the game's check (0x5712F0); name-keyed entries (cB) never occur in the PC archives
    if ((uint64_t)head[1] != 16ull + 20ull * ((uint64_t)cA + cB) || cB != 0 || 8ull + head[1] > (uint64_t)sz.QuadPart) {
        *err = "index layout";
        return false;
    }
    // the A entries inside the index block, after its 16-byte head (offA is relative to file offset 8)
    if (offA < 16 || (uint64_t)offA + 20ull * cA > head[1]) {
        *err = "index layout (entry offset)";
        return false;
    }
    out->resize(cA);
    if (cA && (!ReadAt(f, 8 + offA, out->data(), cA * 20, &got) || got != cA * 20)) {
        *err = "index";
        return false;
    }
    *hsize = head[1];
    *fileSize = (uint32_t)sz.QuadPart;
    return true;
}

VirtualArchive::~VirtualArchive()
{
    for (HANDLE h : m_files) CloseHandle(h);
}

bool VirtualArchive::Build(const char* base, const std::vector<std::string>& overlays, std::string* err)
{
    std::vector<std::string> names;
    names.push_back(base);
    names.insert(names.end(), overlays.begin(), overlays.end());
    std::vector<uint32_t> hsizes(names.size()), sizes(names.size());
    std::map<uint32_t, std::pair<int, ArkEntry>> merged;  // key -> (source, entry); ordered = sorted unsigned
    for (size_t i = 0; i < names.size(); i++) {
        HANDLE f = CreateFileA(names[i].c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
                               FILE_ATTRIBUTE_NORMAL, NULL);
        if (f == INVALID_HANDLE_VALUE) {
            *err = names[i] + ": cannot open";
            return false;
        }
        m_files.push_back(f);
        std::vector<ArkEntry> ents;
        std::string e;
        if (!ReadArkIndex(f, &hsizes[i], &sizes[i], &ents, &e)) {
            *err = names[i] + ": " + e;
            return false;
        }
        // an overlay's entries must lie in its own data region: anything else would point into another part
        // of the merged archive and hand the game wrong bytes
        const uint64_t d0 = 8ull + hsizes[i];
        for (const ArkEntry& a : ents)
            if (i > 0 && (a.type > 1 || a.offset < d0 || (uint64_t)a.offset + a.csize > sizes[i])) {
                *err = names[i] + ": an entry lies outside its data";
                return false;
            }
        for (const ArkEntry& a : ents) merged[a.key] = std::make_pair((int)i, a);
    }
    const uint32_t n = (uint32_t)merged.size();
    const uint64_t H = 8 + 16 + 20ull * n;
    std::vector<uint64_t> shift(names.size());
    const uint64_t base0 = 8 + hsizes[0];
    if (H < base0) {  // the base holds a key twice
        *err = "base index has duplicate keys";
        return false;
    }
    m_delta = (uint32_t)(H - base0);  // >= 0: the merged index holds at least the base's keys
    shift[0] = m_delta;
    m_segs.push_back({0, (uint32_t)H, -1, 0});
    uint64_t cur = sizes[0] + (uint64_t)m_delta;
    m_baseStart = (uint32_t)H;
    m_baseEnd = (uint32_t)cur;
    m_segs.push_back({(uint32_t)H, (uint32_t)cur, 0, (uint32_t)base0});
    for (size_t i = 1; i < names.size(); i++) {
        cur = (cur + kAlign - 1) & ~(uint64_t)(kAlign - 1);
        const uint64_t d0 = 8 + hsizes[i], len = sizes[i] - d0;
        if (cur + len > kLimit) {
            *err = "virtual archive larger than 2 GB";
            return false;
        }
        shift[i] = cur - d0;
        m_segs.push_back({(uint32_t)cur, (uint32_t)(cur + len), (int)i, (uint32_t)d0});
        cur += len;
    }
    m_size = (uint32_t)cur;
    m_head.resize((size_t)H);
    uint32_t* w = (uint32_t*)m_head.data();
    w[0] = kMagic;
    w[1] = 16 + 20 * n;  // hsize
    w[2] = n;            // cA
    w[3] = 16;           // offA, relative to the index block (file offset 8)
    w[4] = 0;            // cB
    w[5] = 16 + 20 * n;  // offB (as the game's own archives: end of the A entries)
    ArkEntry* out = (ArkEntry*)(m_head.data() + 24);
    for (const auto& kv : merged) {
        ArkEntry e = kv.second.second;
        e.offset = (uint32_t)(e.offset + shift[kv.second.first]);
        *out++ = e;
    }
    return true;
}

bool VirtualArchive::Read(uint32_t pos, void* dst, uint32_t n, uint32_t* got) const
{
    uint8_t* d = (uint8_t*)dst;
    const uint32_t end = (uint32_t)std::min<uint64_t>((uint64_t)pos + n, m_size);
    *got = 0;
    if (pos >= end) return true;
    memset(d, 0, end - pos);  // alignment gaps between segments read as zeros
    for (const VSegment& s : m_segs) {
        if (s.vend <= pos || s.vstart >= end) continue;
        const uint32_t a = std::max(pos, s.vstart), b = std::min(end, s.vend);
        if (s.src < 0) {
            memcpy(d + (a - pos), m_head.data() + a, b - a);
        } else {
            uint32_t g = 0;
            if (!ReadAt(m_files[s.src], (uint64_t)s.srcoff + (a - s.vstart), d + (a - pos), b - a, &g) || g != b - a)
                return false;
        }
    }
    *got = end - pos;
    return true;
}
