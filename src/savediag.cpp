// Split/Second PC fix - save game monitor (Log=1 only) and the backups at every start ([Gameplay] SaveBackups)
//
// The Steam build is known to break profiles around episode 6 ("save file is corrupted"). Reading
// and writing the .sav files (0x590C50, 0x590E90) runs inside SecuROM's VM. A hook on the save
// interface's vtable in .rdata (0xBA4820) coincided with failing integrity checks, so the game is
// left alone: the profile files are only watched from outside and every change is logged. The files are the
// plaintext profile XORed with a fixed key stream, so a logged file can be decrypted afterwards.
// The backups are plain file copies made from outside as well; the game's code and memory are not touched.
#include "common.h"
#include <shlobj.h>
#include <stdio.h>
#include <algorithm>
#include <string>
#include <vector>

// ---------------------------------------------------------------- backups at every start
// SaveBackups = n (1-5): at every start, the files of the save folder (Documents\Disney Interactive
// Studios\Split Second, the profiles *.sav and config_ss.cfg; no subfolders) are copied to
// %LOCALAPPDATA%\glimpse_fix\save_backups\<nnnnnn_yyyymmdd_hhmmss>, and only the n newest copies are kept. A start with
// files that equal the newest copy byte for byte makes no new one, so starting the game again and again never
// pushes an older, different state out. The game's saves cannot be checked for damage (their format is read and
// written inside SecuROM's VM), so a damaged profile is copied like any other; the older copies stay for that.
// Runs on a thread of its own, started at DLL load: it waits for the loader and is done long before the
// title screen loads a profile. A file the game writes while it is copied is copied again.

static std::string SaveFolder()
{
    char dir[MAX_PATH];
    if (FAILED(SHGetFolderPathA(NULL, CSIDL_PERSONAL, NULL, 0, dir))) return "";
    return std::string(dir) + "\\Disney Interactive Studios\\Split Second";
}

static std::string BackupRoot()
{
    char dir[MAX_PATH];
    if (FAILED(SHGetFolderPathA(NULL, CSIDL_LOCAL_APPDATA, NULL, 0, dir))) return "";
    std::string root = std::string(dir) + "\\glimpse_fix";
    CreateDirectoryA(root.c_str(), NULL);
    root += "\\save_backups";
    CreateDirectoryA(root.c_str(), NULL);
    return root;
}

// the plain files directly in dir, sorted by name
static std::vector<std::string> FilesIn(const std::string& dir)
{
    std::vector<std::string> out;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((dir + "\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return out;
    do {
        if (!(fd.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT))) out.push_back(fd.cFileName);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    std::sort(out.begin(), out.end());
    return out;
}

// a backup folder's name: nnnnnn_yyyymmdd_hhmmss, a running number (which orders them, also when the clock goes
// back or two starts fall into one second) and the local time of the start; ".part" while it is written
static const char kBackupPattern[] = "000000_00000000_000000";
static bool IsBackupName(const char* n, bool* partial)
{
    size_t len = strlen(n);
    *partial = len > 5 && !_stricmp(n + len - 5, ".part");
    if (*partial) len -= 5;
    if (len != sizeof(kBackupPattern) - 1) return false;
    for (size_t i = 0; i < len; i++)
        if (kBackupPattern[i] == '_' ? n[i] != '_' : !(n[i] >= '0' && n[i] <= '9')) return false;
    return true;
}

// the finished backups in root (oldest first: the running number sorts them) and the unfinished ones (.part)
static void ListBackups(const std::string& root, std::vector<std::string>* done, std::vector<std::string>* part)
{
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((root + "\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        bool partial;
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && !(fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
            IsBackupName(fd.cFileName, &partial))
            (partial ? part : done)->push_back(fd.cFileName);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    std::sort(done->begin(), done->end());
}

// a backup folder of ours and the files in it (it has no subfolders)
static bool RemoveBackup(const std::string& dir)
{
    for (const std::string& f : FilesIn(dir)) DeleteFileA((dir + "\\" + f).c_str());
    return RemoveDirectoryA(dir.c_str()) != 0;
}

static bool ReadAll(const std::string& path, std::vector<char>* out)
{
    HANDLE h = CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                           OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return false;
    LARGE_INTEGER size;
    bool ok = GetFileSizeEx(h, &size) && size.QuadPart < (64LL << 20);
    if (ok) {
        out->resize((size_t)size.QuadPart);
        DWORD got = 0;
        ok = out->empty() || (ReadFile(h, out->data(), (DWORD)out->size(), &got, NULL) && got == out->size());
    }
    CloseHandle(h);
    return ok;
}

// the save folder's files equal those of the backup, names and bytes
static bool SameAsBackup(const std::string& saves, const std::vector<std::string>& names, const std::string& backup)
{
    if (FilesIn(backup) != names) return false;
    std::vector<char> a, b;
    for (const std::string& n : names)
        if (!ReadAll(saves + "\\" + n, &a) || !ReadAll(backup + "\\" + n, &b) || a != b) return false;
    return true;
}

static bool Stamp(const std::string& path, WIN32_FILE_ATTRIBUTE_DATA* d)
{
    return GetFileAttributesExA(path.c_str(), GetFileExInfoStandard, d) != 0;
}

// one file, again when the game changed it while it was copied
static bool CopyStable(const std::string& from, const std::string& to)
{
    for (int i = 0; i < 3; i++) {
        WIN32_FILE_ATTRIBUTE_DATA a, b;
        if (!Stamp(from, &a) || !CopyFileA(from.c_str(), to.c_str(), FALSE)) return false;
        if (Stamp(from, &b) && !CompareFileTime(&a.ftLastWriteTime, &b.ftLastWriteTime) &&
            a.nFileSizeLow == b.nFileSizeLow && a.nFileSizeHigh == b.nFileSizeHigh)
            return true;
        Sleep(200);
    }
    return false;
}

static void BackupSavesAtStart()
{
    const int keep = g_cfg.saveBackups;
    const std::string saves = SaveFolder(), root = BackupRoot();
    if (saves.empty() || root.empty()) {
        Log("save backup: the save or backup folder is not known, no backup");
        return;
    }
    const std::vector<std::string> names = FilesIn(saves);
    if (names.empty()) {
        Log("save backup: no files in %s, no backup", saves.c_str());
        return;
    }
    std::vector<std::string> done, part;
    ListBackups(root, &done, &part);
    for (const std::string& p : part) RemoveBackup(root + "\\" + p);  // left by a run that ended while copying
    if (!done.empty() && SameAsBackup(saves, names, root + "\\" + done.back())) {
        Log("save backup: the saves equal the newest backup %s, no new one", done.back().c_str());
    } else {
        SYSTEMTIME t;
        GetLocalTime(&t);
        const unsigned seq = done.empty() ? 1 : (unsigned)atoi(done.back().c_str()) % 999999 + 1;
        char ts[32];
        sprintf_s(ts, "%06u_%04u%02u%02u_%02u%02u%02u", seq, t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
        const std::string name = ts;
        const std::string tmp = root + "\\" + name + ".part";
        if (!CreateDirectoryA(tmp.c_str(), NULL)) {
            Log("save backup: %s cannot be made (error %lu), no backup", tmp.c_str(), GetLastError());
            return;
        }
        for (const std::string& n : names)
            if (!CopyStable(saves + "\\" + n, tmp + "\\" + n)) {
                Log("save backup: %s could not be copied (error %lu), no backup", n.c_str(), GetLastError());
                RemoveBackup(tmp);
                return;
            }
        if (!MoveFileA(tmp.c_str(), (root + "\\" + name).c_str())) {
            Log("save backup: %s cannot be renamed (error %lu), no backup", tmp.c_str(), GetLastError());
            RemoveBackup(tmp);
            return;
        }
        done.push_back(name);
        Log("save backup: %u files of %s copied to %s\\%s", (unsigned)names.size(), saves.c_str(), root.c_str(), name.c_str());
    }
    while ((int)done.size() > keep) {  // the oldest first
        if (RemoveBackup(root + "\\" + done.front())) Log("save backup: oldest backup %s removed", done.front().c_str());
        else Log("save backup: oldest backup %s could not be removed", done.front().c_str());
        done.erase(done.begin());
    }
}

static DWORD WINAPI SaveBackupThread(LPVOID)
{
    try {
        BackupSavesAtStart();
    } catch (...) {
        Log("save backup: out of memory, no backup");
    }
    return 0;
}

void StartSaveBackup()
{
    if (g_cfg.saveBackups < 1) return;
    HANDLE t = CreateThread(NULL, 0, SaveBackupThread, NULL, 0, NULL);
    if (t) CloseHandle(t);
    else Log("save backup: thread not started, no backup");
}

struct SaveFile {
    char name[MAX_PATH];
    DWORD size;
    FILETIME written;
};
static SaveFile s_files[16];
static int s_count;

void WatchSaveFiles()
{
    static DWORD last;
    static char pattern[MAX_PATH];
    DWORD now = GetTickCount();
    if (now - last < 2000) return;
    last = now;
    if (!pattern[0]) {
        if (FAILED(SHGetFolderPathA(NULL, CSIDL_PERSONAL, NULL, 0, pattern))) return;
        strcat_s(pattern, "\\Disney Interactive Studios\\Split Second\\*.sav");
    }
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        int i = 0;
        while (i < s_count && strcmp(s_files[i].name, fd.cFileName)) i++;
        bool known = i < s_count;
        if (!known) {
            if (s_count == 16) continue;
            s_count++;
            strcpy_s(s_files[i].name, fd.cFileName);
        }
        SaveFile& f = s_files[i];
        if (known && f.size == fd.nFileSizeLow && !CompareFileTime(&f.written, &fd.ftLastWriteTime)) continue;
        f.size = fd.nFileSizeLow;
        f.written = fd.ftLastWriteTime;
        SYSTEMTIME st;
        FileTimeToSystemTime(&fd.ftLastWriteTime, &st);
        Log("save file %s %s: %u bytes, written %02u:%02u:%02u UTC", fd.cFileName, known ? "changed" : "found",
            f.size, st.wHour, st.wMinute, st.wSecond);
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}
