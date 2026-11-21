// Virtual archive: the game's resident.ark plus overlay archives, presented as one archive file.
// Prototype for glimpse_fix (see DESIGN.md); same layout as vark.py.
//
//   [0, H)              header {0x00010000, hsize} + merged index (16 + 20 * n bytes), built in memory
//   [H, ...)            the base file's data region [8 + hsize, size), shifted by delta = H - (8 + base hsize)
//   [o_i, ...)          overlay i's data region, each 16-aligned after the previous one
// Overlays override base entries with the same key, later overlays earlier ones; the index stays sorted by
// unsigned key (the game's binary search, 0x5710C0).
#pragma once
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <stdint.h>
#include <string>
#include <vector>

struct ArkEntry {
    uint32_t type, offset, csize, size, key;  // type 0 stored, 1 LZSS; offset absolute in the file
};

struct VSegment {
    uint32_t vstart, vend;  // virtual range
    int src;                // -1: the in-memory head, else index into the source files
    uint32_t srcoff;        // file offset of vstart in that source
};

class VirtualArchive {
public:
    ~VirtualArchive();
    // base: the game's archive; overlays: PC-format archives (arkfmt.write_ark), applied in order
    bool Build(const char* base, const std::vector<std::string>& overlays, std::string* err);
    uint32_t Size() const { return m_size; }
    uint32_t Delta() const { return m_delta; }  // base data offset shift
    // the base file's data region in virtual coordinates: reads inside it map 1:1 to base offset - Delta()
    uint32_t BaseDataStart() const { return m_baseStart; }
    uint32_t BaseDataEnd() const { return m_baseEnd; }
    const std::vector<uint8_t>& Head() const { return m_head; }
    const std::vector<VSegment>& Segments() const { return m_segs; }
    // reads [pos, pos + n) clipped to Size(); gaps read as zeros; thread-safe (positional reads)
    bool Read(uint32_t pos, void* dst, uint32_t n, uint32_t* got) const;

private:
    std::vector<uint8_t> m_head;
    std::vector<VSegment> m_segs;
    std::vector<HANDLE> m_files;
    uint32_t m_size = 0, m_delta = 0, m_baseStart = 0, m_baseEnd = 0;
};

// reads the index of a PC archive (no checks beyond the ones the game makes)
bool ReadArkIndex(HANDLE f, uint32_t* hsize, uint32_t* fileSize, std::vector<ArkEntry>* out, std::string* err);
uint32_t ArkKey(const char* path);  // the game's 0x571080: h * 31 + tolower(c), '\' as '/'
