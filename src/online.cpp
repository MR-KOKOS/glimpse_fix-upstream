// Split/Second controller fix - SteamLAN=1: the game's LAN mode over Steam (PROTOTYPE)
//
// The game's network code is Quazal's: Net-Z sessions over PRUDP (reliable UDP). In LAN mode the
// host answers session discovery broadcasts on UDP port 9100 (to 255.255.255.255, 0xAF8170), and
// the players connect to the host's PRUDP port (9104-9199, 0x5D33C0). Quazal's sockets go through a
// replaceable transport, its SocketDriver: when Network starts (0x5C33E0) it takes the driver from
// [0xD77734] (null by default: plain Winsock) into [0xD779FC]; every socket then asks the driver for
// its own socket object (0x5D3360) and opens, binds, sends, receives and waits through it
// (0x5D35C0, 0x5D33C0, 0x5D3700, 0x5D3840, 0x5E1870).
//
// This puts a driver there that carries the datagrams through Steam (ISteamNetworkingMessages,
// relayed by Steam, so no ports have to be opened) to the members of a Steam lobby. Every player
// has a virtual address 10.x.y.z made from the Steam account id; a broadcast goes to every member.
// Quazal learns its own address from the interface list (WSAIoctl SIO_GET_INTERFACE_LIST, 0x5E1A80);
// that call is answered with the virtual address through the import slot, so the address the host
// advertises is one the others can reach. A data write and an import slot change, no game code.
//
// steam_api.dll (Steamworks SDK, flat API) is loaded from the game folder at run time; the game runs
// as its own app 297860, which has Steam lobbies and the relay network.
#include "common.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <deque>
#include <vector>

// ---------------------------------------------------------------- Quazal's SocketDriver interface

// address as Quazal passes it: IPv4 in host byte order, then the port
struct Endpoint {
    uint32_t ip;
    uint16_t port;
};

enum { Q_OK = 0, Q_ERROR = 1, Q_WOULDBLOCK = 2 };  // results of the socket calls (0x5D34A0)

// vtable order of Quazal's SocketDriver::Socket (BerkeleySocket 0xBDE06C); thiscall, the callee
// pops its arguments, exactly what MSVC makes of these virtual functions
class QSocket {
public:
    virtual bool Open(int type);                               // 0 = UDP, 1 = TCP
    virtual void Close();
    virtual bool Bind(unsigned port);
    virtual int RecvFrom(char* buf, int len, Endpoint* from, int* got);
    virtual int SendTo(const char* buf, int len, const Endpoint* to, int* sent);
    virtual int Connect(const Endpoint* to);
    virtual int Recv(char* buf, int len, int* got);
    virtual int Send(const char* buf, int len, int* sent);
    virtual bool SetOption(int value);
    virtual QSocket* Destroy(unsigned flags);

    struct Packet {
        Endpoint from;
        std::vector<char> data;
    };
    uint16_t port = 0;
    bool udp = false;
    std::deque<Packet> queue;
};

struct SelectEntry {
    QSocket* socket;
    int want;   // 1 readable, 2 writable
    int ready;
};

// vtable order of Quazal's SocketDriver (BerkeleySocketDriver 0xBDD270)
class QDriver {
public:
    virtual QDriver* Destroy(unsigned) { return this; }  // static object, never freed
    virtual QSocket* CreateSocket();
    virtual void DeleteSocket(QSocket* s);
    virtual bool Select(SelectEntry* entries, unsigned count, unsigned timeoutMs);
};

static QDriver s_driver;
static CRITICAL_SECTION s_lock;          // sockets, their queues, the peers
static HANDLE s_arrived;                 // set when a datagram was queued
static std::vector<QSocket*> s_sockets;
static uint32_t s_myIp;                  // 10.x.y.z, host byte order; 0 until Steam is up
static uint16_t s_nextPort = 50000;

// Log=1: traffic counters, summed up every 10 seconds
static volatile LONG s_sentDirect, s_sentBroadcast, s_sentLocal, s_received, s_selects, s_dropped;

// ---------------------------------------------------------------- Steam (flat API)

typedef uint64_t SteamAPICall_t;
struct SteamNetworkingIdentity { BYTE raw[136]; };
struct SteamNetworkingMessage_t { void* data; int size; };  // the leading fields, the rest is not read
#pragma pack(push, 8)
struct LobbyCreated_t { int result; uint64_t lobby; };                                    // 513
struct LobbyEnter_t { uint64_t lobby; uint32_t permissions; bool locked; uint32_t response; };  // 504
struct LobbyMatchList_t { uint32_t count; };                                              // 510
#pragma pack(pop)

static struct {
    int (__cdecl* InitFlat)(char* err);
    void (__cdecl* RunCallbacks)();
    void* (__cdecl* User)();
    void* (__cdecl* Utils)();
    void* (__cdecl* Matchmaking)();
    void* (__cdecl* Messages)();
    void* (__cdecl* NetUtils)();
    uint64_t (__cdecl* GetSteamID)(void*);
    bool (__cdecl* IsDone)(void*, SteamAPICall_t, bool*);
    bool (__cdecl* Result)(void*, SteamAPICall_t, void*, int, int, bool*);
    SteamAPICall_t (__cdecl* CreateLobby)(void*, int, int);
    SteamAPICall_t (__cdecl* JoinLobby)(void*, uint64_t);
    void (__cdecl* LeaveLobby)(void*, uint64_t);
    SteamAPICall_t (__cdecl* RequestLobbyList)(void*);
    void (__cdecl* StringFilter)(void*, const char*, const char*, int);
    void (__cdecl* DistanceFilter)(void*, int);
    uint64_t (__cdecl* LobbyByIndex)(void*, int);
    bool (__cdecl* SetLobbyData)(void*, uint64_t, const char*, const char*);
    int (__cdecl* NumMembers)(void*, uint64_t);
    uint64_t (__cdecl* MemberByIndex)(void*, uint64_t, int);
    int (__cdecl* SendToUser)(void*, const SteamNetworkingIdentity*, const void*, uint32_t, int, int);
    int (__cdecl* ReceiveOnChannel)(void*, int, SteamNetworkingMessage_t**, int);
    bool (__cdecl* AcceptSession)(void*, const SteamNetworkingIdentity*);
    void (__cdecl* ReleaseMessage)(SteamNetworkingMessage_t*);
    void (__cdecl* IdentitySetSteamID)(SteamNetworkingIdentity*, uint64_t);
    void (__cdecl* IdentityClear)(SteamNetworkingIdentity*);
    void (__cdecl* InitRelay)(void*);
} S;

static void *s_user, *s_utils, *s_mm, *s_msg;
static CRITICAL_SECTION s_steamLock;     // Steam calls from the game's network threads and ours
static uint64_t s_lobby, s_mySteamID;

struct Peer {
    uint64_t steamID;
    uint32_t ip;
    SteamNetworkingIdentity identity;
};
static std::vector<Peer> s_peers;        // the other lobby members

static const uint32_t kMagic = 0x314C4C47;  // "GLL1"
static const int kSendFlags = 1 | 32;       // unreliable without Nagle delay, restart broken sessions
static const char kLobbyVersion[] = "1";

#pragma pack(push, 1)
struct Wire {
    uint32_t magic;
    uint32_t srcIp;
    uint16_t srcPort;
    uint16_t dstPort;  // 0: hello, opens the session
};
#pragma pack(pop)

static uint32_t VirtualIp(uint64_t steamID)
{
    uint32_t ip = 0x0A000000 | ((uint32_t)steamID & 0x00FFFFFF);
    if ((ip & 0xFF) == 0 || (ip & 0xFF) == 0xFF) ip ^= 0x01;
    return ip;
}

static void IpText(uint32_t ip, char* out, size_t n)
{
    _snprintf_s(out, n, _TRUNCATE, "%u.%u.%u.%u", ip >> 24, (ip >> 16) & 0xFF, (ip >> 8) & 0xFF, ip & 0xFF);
}

template <class T> static bool Load(HMODULE dll, T& fn, const char* name)
{
    fn = (T)GetProcAddress(dll, name);
    if (!fn) Log("online: steam_api.dll has no %s", name);
    return fn != NULL;
}

static bool LoadSteam()
{
    char path[MAX_PATH];
    GetModuleFileNameA(NULL, path, MAX_PATH);
    char* slash = strrchr(path, '\\');
    strcpy_s(slash ? slash + 1 : path, MAX_PATH - (slash ? slash + 1 - path : 0), "steam_api.dll");
    HMODULE dll = LoadLibraryA(path);
    if (!dll) {
        Log("online: %s not found (error %lu), SteamLAN off", path, GetLastError());
        return false;
    }
    bool ok = Load(dll, S.InitFlat, "SteamAPI_InitFlat") & Load(dll, S.RunCallbacks, "SteamAPI_RunCallbacks") &
              Load(dll, S.User, "SteamAPI_SteamUser_v023") & Load(dll, S.Utils, "SteamAPI_SteamUtils_v010") &
              Load(dll, S.Matchmaking, "SteamAPI_SteamMatchmaking_v009") &
              Load(dll, S.Messages, "SteamAPI_SteamNetworkingMessages_SteamAPI_v002") &
              Load(dll, S.NetUtils, "SteamAPI_SteamNetworkingUtils_SteamAPI_v004") &
              Load(dll, S.GetSteamID, "SteamAPI_ISteamUser_GetSteamID") &
              Load(dll, S.IsDone, "SteamAPI_ISteamUtils_IsAPICallCompleted") &
              Load(dll, S.Result, "SteamAPI_ISteamUtils_GetAPICallResult") &
              Load(dll, S.CreateLobby, "SteamAPI_ISteamMatchmaking_CreateLobby") &
              Load(dll, S.JoinLobby, "SteamAPI_ISteamMatchmaking_JoinLobby") &
              Load(dll, S.LeaveLobby, "SteamAPI_ISteamMatchmaking_LeaveLobby") &
              Load(dll, S.RequestLobbyList, "SteamAPI_ISteamMatchmaking_RequestLobbyList") &
              Load(dll, S.StringFilter, "SteamAPI_ISteamMatchmaking_AddRequestLobbyListStringFilter") &
              Load(dll, S.DistanceFilter, "SteamAPI_ISteamMatchmaking_AddRequestLobbyListDistanceFilter") &
              Load(dll, S.LobbyByIndex, "SteamAPI_ISteamMatchmaking_GetLobbyByIndex") &
              Load(dll, S.SetLobbyData, "SteamAPI_ISteamMatchmaking_SetLobbyData") &
              Load(dll, S.NumMembers, "SteamAPI_ISteamMatchmaking_GetNumLobbyMembers") &
              Load(dll, S.MemberByIndex, "SteamAPI_ISteamMatchmaking_GetLobbyMemberByIndex") &
              Load(dll, S.SendToUser, "SteamAPI_ISteamNetworkingMessages_SendMessageToUser") &
              Load(dll, S.ReceiveOnChannel, "SteamAPI_ISteamNetworkingMessages_ReceiveMessagesOnChannel") &
              Load(dll, S.AcceptSession, "SteamAPI_ISteamNetworkingMessages_AcceptSessionWithUser") &
              Load(dll, S.ReleaseMessage, "SteamAPI_SteamNetworkingMessage_t_Release") &
              Load(dll, S.IdentitySetSteamID, "SteamAPI_SteamNetworkingIdentity_SetSteamID64") &
              Load(dll, S.IdentityClear, "SteamAPI_SteamNetworkingIdentity_Clear") &
              Load(dll, S.InitRelay, "SteamAPI_ISteamNetworkingUtils_InitRelayNetworkAccess");
    if (!ok) {
        Log("online: this steam_api.dll is too old (needs SteamNetworkingMessages002), SteamLAN off");
        return false;
    }
    char app[16];
    if (!GetEnvironmentVariableA("SteamAppId", app, sizeof(app))) SetEnvironmentVariableA("SteamAppId", "297860");
    char err[1024] = "";
    int init = S.InitFlat(err);
    if (init != 0) {
        Log("online: SteamAPI_InitFlat failed (%d: %s), SteamLAN off", init, err);
        return false;
    }
    s_user = S.User();
    s_utils = S.Utils();
    s_mm = S.Matchmaking();
    s_msg = S.Messages();
    s_mySteamID = S.GetSteamID(s_user);
    S.InitRelay(S.NetUtils());
    return s_user && s_utils && s_mm && s_msg && s_mySteamID;
}

// waits for a call result on the online thread
static bool Wait(SteamAPICall_t call, void* out, int size, int id)
{
    bool failed = false;
    for (int i = 0; i < 400 && call; i++) {
        S.RunCallbacks();
        if (S.IsDone(s_utils, call, &failed)) break;
        Sleep(25);
    }
    return call && S.Result(s_utils, call, out, size, id, &failed) && !failed;
}

// ---------------------------------------------------------------- datagrams

static QSocket* SocketOnPort(uint16_t port)
{
    for (QSocket* s : s_sockets)
        if (s->udp && s->port == port) return s;
    return NULL;
}

static void Deliver(uint32_t srcIp, uint16_t srcPort, uint16_t dstPort, const char* data, int size)
{
    EnterCriticalSection(&s_lock);
    if (QSocket* s = SocketOnPort(dstPort)) {
        if (s->queue.size() >= 256) s->queue.pop_front();
        QSocket::Packet p;
        p.from.ip = srcIp;
        p.from.port = srcPort;
        p.data.assign(data, data + size);
        s->queue.push_back(std::move(p));
        SetEvent(s_arrived);
    } else {
        InterlockedIncrement(&s_dropped);  // nobody listens on that port (yet)
    }
    LeaveCriticalSection(&s_lock);
}

static void SendWire(const SteamNetworkingIdentity* to, uint16_t srcPort, uint16_t dstPort, const char* data, int size)
{
    char buf[2048];
    if (size < 0 || size > (int)(sizeof(buf) - sizeof(Wire))) return;
    Wire w = { kMagic, s_myIp, srcPort, dstPort };
    memcpy(buf, &w, sizeof(w));
    if (size) memcpy(buf + sizeof(w), data, size);
    EnterCriticalSection(&s_steamLock);
    S.SendToUser(s_msg, to, buf, (uint32_t)(sizeof(w) + size), kSendFlags, 0);
    LeaveCriticalSection(&s_steamLock);
}

static void SendDatagram(uint16_t srcPort, const Endpoint* to, const char* data, int size)
{
    bool broadcast = to->ip == 0xFFFFFFFF || (to->ip & 0xFF) == 0xFF;
    if (broadcast) InterlockedIncrement(&s_sentBroadcast);
    if (broadcast || to->ip == s_myIp || (to->ip >> 24) == 127) {
        if (!broadcast) InterlockedIncrement(&s_sentLocal);
        Deliver(s_myIp ? s_myIp : 0x7F000001, srcPort, to->port, data, size);  // a broadcast reaches us too
    }
    if (!s_myIp) return;
    EnterCriticalSection(&s_lock);
    std::vector<SteamNetworkingIdentity> targets;
    for (const Peer& p : s_peers)
        if (broadcast || p.ip == to->ip) targets.push_back(p.identity);
    LeaveCriticalSection(&s_lock);
    if (targets.empty() && !broadcast && to->ip != s_myIp && (to->ip >> 24) != 127) {
        static uint32_t unknown;
        if (unknown != to->ip) {
            char t[20];
            IpText(to->ip, t, sizeof(t));
            Log("online: no lobby member has the address %s, datagram dropped", t);
            unknown = to->ip;
        }
    }
    for (const SteamNetworkingIdentity& id : targets) {
        SendWire(&id, srcPort, to->port, data, size);
        InterlockedIncrement(&s_sentDirect);
    }
}

// ---------------------------------------------------------------- the driver

QSocket* QDriver::CreateSocket()
{
    QSocket* s = new QSocket();
    EnterCriticalSection(&s_lock);
    s_sockets.push_back(s);
    size_t n = s_sockets.size();
    LeaveCriticalSection(&s_lock);
    Log("online: Quazal made a socket through the driver (%u open)", (unsigned)n);
    return s;
}

void QDriver::DeleteSocket(QSocket* s)
{
    if (s) s->Destroy(1);
}

bool QDriver::Select(SelectEntry* entries, unsigned count, unsigned timeoutMs)
{
    InterlockedIncrement(&s_selects);
    DWORD start = GetTickCount();
    for (;;) {
        bool any = false;
        EnterCriticalSection(&s_lock);
        for (unsigned i = 0; i < count; i++) {
            SelectEntry& e = entries[i];
            e.ready = 0;
            if (!e.socket) continue;  // made by Winsock before the driver was there
            if ((e.want & 1) && !e.socket->queue.empty()) e.ready |= 1;
            if (e.want & 2) e.ready |= 2;  // sending never waits
            any |= e.ready != 0;
        }
        LeaveCriticalSection(&s_lock);
        if (any) return true;
        DWORD spent = GetTickCount() - start;
        if (spent >= timeoutMs) return false;
        DWORD left = timeoutMs - spent;
        WaitForSingleObject(s_arrived, left < 5 ? left : 5);  // several threads may wait: poll as well
    }
}

bool QSocket::Open(int type)
{
    if (type != 0) {
        Log("online: Quazal asked for a TCP socket, not carried over Steam");
        return false;
    }
    udp = true;
    return true;
}

void QSocket::Close()
{
    EnterCriticalSection(&s_lock);
    port = 0;
    queue.clear();
    LeaveCriticalSection(&s_lock);
}

bool QSocket::Bind(unsigned wanted)
{
    uint16_t p = (uint16_t)wanted;
    EnterCriticalSection(&s_lock);
    if (!p) {
        while (SocketOnPort(s_nextPort)) s_nextPort = s_nextPort >= 60000 ? 50000 : s_nextPort + 1;
        p = s_nextPort++;
    }
    bool ok = !SocketOnPort(p);
    if (ok) port = p;
    LeaveCriticalSection(&s_lock);
    if (wanted) Log(ok ? "online: socket bound to port %u" : "online: port %u already in use", p);
    else Log("online: socket bound to free port %u", p);
    return ok;
}

int QSocket::RecvFrom(char* buf, int len, Endpoint* from, int* got)
{
    EnterCriticalSection(&s_lock);
    if (queue.empty()) {
        LeaveCriticalSection(&s_lock);
        return Q_WOULDBLOCK;
    }
    Packet p = std::move(queue.front());
    queue.pop_front();
    LeaveCriticalSection(&s_lock);
    int n = (int)p.data.size() < len ? (int)p.data.size() : len;
    if (n > 0) memcpy(buf, p.data.data(), n);
    if (from) *from = p.from;
    if (got) *got = n;
    InterlockedIncrement(&s_received);
    return Q_OK;
}

int QSocket::SendTo(const char* buf, int len, const Endpoint* to, int* sent)
{
    if (!to || len < 0) return Q_ERROR;
    if (!port) Bind(0);  // like an unbound UDP socket, which gets a port on its first send
    SendDatagram(port, to, buf, len);
    if (sent) *sent = len;
    return Q_OK;
}

int QSocket::Connect(const Endpoint*) { return Q_ERROR; }
int QSocket::Recv(char*, int, int*) { return Q_ERROR; }
int QSocket::Send(const char*, int, int*) { return Q_ERROR; }
bool QSocket::SetOption(int) { return true; }

QSocket* QSocket::Destroy(unsigned flags)
{
    EnterCriticalSection(&s_lock);
    for (size_t i = 0; i < s_sockets.size(); i++)
        if (s_sockets[i] == this) {
            s_sockets.erase(s_sockets.begin() + i);
            break;
        }
    LeaveCriticalSection(&s_lock);
    if (flags & 1) {
        delete this;
        return NULL;
    }
    return this;
}

// ---------------------------------------------------------------- own address

// INTERFACE_INFO: flags, then address, broadcast address and net mask (sockaddr_gen, 24 bytes each)
#pragma pack(push, 1)
struct InterfaceInfo {
    uint32_t flags;
    struct { uint16_t family, port; uint32_t addr; BYTE pad[16]; } address, broadcast, mask;
};
#pragma pack(pop)
static_assert(sizeof(InterfaceInfo) == 76, "INTERFACE_INFO is 76 bytes");

typedef int(WINAPI* WSAIoctl_t)(UINT_PTR, DWORD, void*, DWORD, void*, DWORD, DWORD*, void*, void*);
static WSAIoctl_t s_origIoctl;

static int WINAPI Hook_WSAIoctl(UINT_PTR s, DWORD code, void* in, DWORD inLen, void* out, DWORD outLen, DWORD* returned,
                                void* overlapped, void* completion)
{
    const DWORD kGetInterfaceList = 0x4004747F;
    if (code == kGetInterfaceList && s_myIp && out && outLen >= sizeof(InterfaceInfo) && !overlapped) {
        InterfaceInfo info = {};
        info.flags = 1 | 2;  // up, broadcast
        info.address.family = info.broadcast.family = info.mask.family = 2;
        info.address.addr = _byteswap_ulong(s_myIp);
        info.broadcast.addr = 0xFFFFFFFF;
        info.mask.addr = _byteswap_ulong(0xFF000000);
        memcpy(out, &info, sizeof(info));
        if (returned) *returned = sizeof(info);
        static bool logged;
        if (!logged) {
            char t[20];
            IpText(s_myIp, t, sizeof(t));
            Log("online: interface list answered with %s", t);
            logged = true;
        }
        return 0;
    }
    return s_origIoctl(s, code, in, inLen, out, outLen, returned, overlapped, completion);
}

// ---------------------------------------------------------------- lobby

static void LobbyKeys(char* name, size_t n)
{
    const char* src = g_cfg.lobbyName[0] ? g_cfg.lobbyName : "public";
    size_t i = 0;
    for (; src[i] && i + 1 < n; i++) name[i] = src[i] >= 'A' && src[i] <= 'Z' ? (char)(src[i] + 32) : src[i];
    name[i] = 0;
}

static uint64_t FindLobby()
{
    char name[64];
    LobbyKeys(name, sizeof(name));
    S.StringFilter(s_mm, "glimpse_lan", kLobbyVersion, 0);
    S.StringFilter(s_mm, "glimpse_name", name, 0);
    S.DistanceFilter(s_mm, 3);  // worldwide
    LobbyMatchList_t list = {};
    if (!Wait(S.RequestLobbyList(s_mm), &list, sizeof(list), 510)) return 0;
    uint64_t best = 0;
    for (uint32_t i = 0; i < list.count; i++) {
        uint64_t id = S.LobbyByIndex(s_mm, (int)i);
        if (id && id != s_lobby && (!best || id < best)) best = id;  // the oldest one, so everybody picks the same
    }
    return best;
}

static bool Join(uint64_t lobby)
{
    LobbyEnter_t entered = {};
    if (!Wait(S.JoinLobby(s_mm, lobby), &entered, sizeof(entered), 504) || entered.response != 1) {
        Log("online: could not join lobby %llu (response %u)", (unsigned long long)lobby, entered.response);
        return false;
    }
    s_lobby = lobby;
    Log("online: joined lobby %llu", (unsigned long long)lobby);
    return true;
}

static bool Create()
{
    LobbyCreated_t created = {};
    if (!Wait(S.CreateLobby(s_mm, 2 /* public */, 8), &created, sizeof(created), 513) || created.result != 1) {
        Log("online: could not create a lobby (result %d)", created.result);
        return false;
    }
    s_lobby = created.lobby;
    char name[64];
    LobbyKeys(name, sizeof(name));
    S.SetLobbyData(s_mm, s_lobby, "glimpse_lan", kLobbyVersion);
    S.SetLobbyData(s_mm, s_lobby, "glimpse_name", name);
    Log("online: created lobby %llu ('%s'), waiting for players", (unsigned long long)s_lobby, name);
    return true;
}

static void RefreshMembers()
{
    std::vector<Peer> now;
    int n = S.NumMembers(s_mm, s_lobby);
    for (int i = 0; i < n; i++) {
        uint64_t id = S.MemberByIndex(s_mm, s_lobby, i);
        if (!id || id == s_mySteamID) continue;
        Peer p = {};
        p.steamID = id;
        p.ip = VirtualIp(id);
        S.IdentityClear(&p.identity);
        S.IdentitySetSteamID(&p.identity, id);
        now.push_back(p);
    }
    EnterCriticalSection(&s_lock);
    std::vector<Peer> before = s_peers;
    s_peers = now;
    LeaveCriticalSection(&s_lock);
    for (const Peer& p : now) {
        bool known = false;
        for (const Peer& b : before) known |= b.steamID == p.steamID;
        if (known) continue;
        char t[20];
        IpText(p.ip, t, sizeof(t));
        Log("online: player %s joined the lobby (%d in it)", t, n);
        EnterCriticalSection(&s_steamLock);
        S.AcceptSession(s_msg, &p.identity);
        LeaveCriticalSection(&s_steamLock);
        SendWire(&p.identity, 0, 0, NULL, 0);  // hello: our first message opens the session both ways
    }
    for (const Peer& b : before) {
        bool still = false;
        for (const Peer& p : now) still |= b.steamID == p.steamID;
        if (still) continue;
        char t[20];
        IpText(b.ip, t, sizeof(t));
        Log("online: player %s left the lobby", t);
    }
}

static void Pump()
{
    SteamNetworkingMessage_t* msgs[32];
    for (;;) {
        EnterCriticalSection(&s_steamLock);
        int n = S.ReceiveOnChannel(s_msg, 0, msgs, 32);
        LeaveCriticalSection(&s_steamLock);
        if (n <= 0) return;
        for (int i = 0; i < n; i++) {
            const SteamNetworkingMessage_t* m = msgs[i];
            if (m->size >= (int)sizeof(Wire)) {
                Wire w;
                memcpy(&w, m->data, sizeof(w));
                if (w.magic == kMagic && w.dstPort)
                    Deliver(w.srcIp, w.srcPort, w.dstPort, (const char*)m->data + sizeof(w), m->size - (int)sizeof(w));
            }
            S.ReleaseMessage(msgs[i]);
        }
    }
}

static QDriver* const* s_activeDriver;  // Quazal's driver in use, [0xD779FC]

// Log=1: what went through the driver in the last 10 seconds
static void LogTraffic()
{
    static bool wasActive;
    bool active = *s_activeDriver == &s_driver;
    if (active != wasActive) {
        Log(active ? "online: Quazal's network runs on the Steam driver" : "online: Quazal's network does not use the driver (yet)");
        wasActive = active;
    }
    LONG direct = InterlockedExchange(&s_sentDirect, 0), bcast = InterlockedExchange(&s_sentBroadcast, 0);
    LONG local = InterlockedExchange(&s_sentLocal, 0), recv = InterlockedExchange(&s_received, 0);
    LONG sel = InterlockedExchange(&s_selects, 0), drop = InterlockedExchange(&s_dropped, 0);
    if (direct || bcast || local || recv || drop) {
        EnterCriticalSection(&s_lock);
        size_t peers = s_peers.size(), sockets = s_sockets.size();
        LeaveCriticalSection(&s_lock);
        Log("online: 10 s: %ld to players, %ld broadcasts, %ld to itself, %ld received, %ld without a socket, "
            "%ld waits; %u sockets, %u players", direct, bcast, local, recv, drop, sel, (unsigned)sockets, (unsigned)peers);
    }
}

static DWORD WINAPI OnlineThread(void*)
{
    uint64_t lobby = FindLobby();
    if (!(lobby && Join(lobby)) && !Create()) {
        Log("online: no lobby, SteamLAN stays local");
        return 0;
    }
    DWORD lastMembers = 0, lastMatch = GetTickCount(), lastTraffic = GetTickCount();
    for (;;) {
        S.RunCallbacks();
        Pump();
        DWORD now = GetTickCount();
        if (now - lastMembers >= 1000) {
            lastMembers = now;
            RefreshMembers();
        }
        if (now - lastTraffic >= 10000) {
            lastTraffic = now;
            LogTraffic();
        }
        // two players who started at the same time each made a lobby: while alone, look for an older one
        if (now - lastMatch >= 15000) {
            lastMatch = now;
            bool alone;
            EnterCriticalSection(&s_lock);
            alone = s_peers.empty();
            LeaveCriticalSection(&s_lock);
            if (alone) {
                uint64_t other = FindLobby();
                if (other && other < s_lobby) {
                    uint64_t mine = s_lobby;
                    S.LeaveLobby(s_mm, mine);
                    if (!Join(other) && !Join(mine)) Create();
                }
            }
        }
        Sleep(1);
    }
}

void OnlineStart()
{
    if (CurrentBuild() != BUILD_STEAM) {
        Log("online: SteamLAN only on the supported build for now");
        return;
    }
    InitializeCriticalSection(&s_lock);
    InitializeCriticalSection(&s_steamLock);
    s_arrived = CreateEventA(NULL, FALSE, FALSE, NULL);
    // the transport Network takes when it starts (it has not yet in the main menu)
    QDriver** setting = (QDriver**)G(0x00D77734);
    QDriver* const* active = (QDriver* const*)G(0x00D779FC);
    s_activeDriver = active;
    if (*active || *setting) {
        Log("online: Quazal already has a socket driver, SteamLAN off");
        return;
    }
    // the interface list Quazal reads for its own address
    void** ioctlSlot = (void**)G(0x00B9B368);  // WS2_32!WSAIoctl import
    HMODULE ws2 = GetModuleHandleA("ws2_32.dll");
    if (!ws2 || *ioctlSlot != (void*)GetProcAddress(ws2, "WSAIoctl")) {
        Log("online: WSAIoctl import not as expected, SteamLAN off");
        return;
    }
    if (!LoadSteam()) return;
    s_myIp = VirtualIp(s_mySteamID);

    s_origIoctl = (WSAIoctl_t)*ioctlSlot;
    void* hook = (void*)Hook_WSAIoctl;
    WriteMemory(ioctlSlot, &hook, sizeof(hook));
    QDriver* driver = &s_driver;
    WriteMemory(setting, &driver, sizeof(driver));

    char t[20];
    IpText(s_myIp, t, sizeof(t));
    Log("online: SteamLAN on, this player is %s, socket driver set", t);
    CloseHandle(CreateThread(NULL, 0, OnlineThread, NULL, 0, NULL));
}
