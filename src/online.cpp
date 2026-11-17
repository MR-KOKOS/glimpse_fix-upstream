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
static volatile LONG s_sendFailed, s_lastSendResult, s_queueFull, s_truncated, s_badWire, s_tooBig;

// Log=1: each kind of event logged the first time only (per key), so a fault shows without flooding
static CRITICAL_SECTION s_diagLock;
static std::vector<uint64_t> s_seen;
static bool FirstTime(uint64_t key)
{
    EnterCriticalSection(&s_diagLock);
    bool first = s_seen.size() < 512;
    for (uint64_t k : s_seen)
        if (k == key) {
            first = false;
            break;
        }
    if (first) s_seen.push_back(key);
    LeaveCriticalSection(&s_diagLock);
    return first;
}
enum DiagKind : uint64_t {
    DIAG_NO_SOCKET = 1ull << 56, DIAG_FIRST_IN = 2ull << 56, DIAG_FIRST_OUT = 3ull << 56, DIAG_QUEUE_FULL = 4ull << 56,
    DIAG_TRUNCATED = 5ull << 56, DIAG_BAD_WIRE = 6ull << 56, DIAG_TOO_BIG = 7ull << 56, DIAG_UNSUPPORTED = 8ull << 56,
    DIAG_WINSOCK_SOCKET = 9ull << 56, DIAG_SEND_FAILED = 10ull << 56, DIAG_IP_CLASH = 11ull << 56,
};
static uint64_t Key(DiagKind kind, uint32_t ip, uint16_t port) { return kind | ((uint64_t)ip << 16) | port; }

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
    int (__cdecl* SessionInfo)(void*, const SteamNetworkingIdentity*, void* info, void* status);
} S;

// SteamNetConnectionInfo_t (pack 8), the fields read here
#pragma pack(push, 8)
struct ConnectionInfo {
    SteamNetworkingIdentity remote;  // 0
    int64_t userData;                // 136
    uint32_t listenSocket;           // 144
    BYTE addrRemote[18];             // 148
    uint16_t pad;                    // 166
    uint32_t popRemote;              // 168
    uint32_t popRelay;               // 172, non-zero: through a Steam relay
    int state;                       // 176
    int endReason;                   // 180
    char endDebug[128];              // 184
    char description[128];           // 312
    int flags;                       // 440
    uint32_t reserved[63];
};
// SteamNetConnectionRealTimeStatus_t, the leading fields
struct ConnectionStatus {
    int state;
    int ping;
    float qualityLocal, qualityRemote;
    float outPackets, outBytes, inPackets, inBytes;
    int sendRate, pendingUnreliable, pendingReliable, sentUnacked;
    int64_t queueTime;
    uint32_t reserved[16];
};
#pragma pack(pop)
static_assert(offsetof(ConnectionInfo, state) == 176 && offsetof(ConnectionInfo, flags) == 440, "SteamNetConnectionInfo_t");

static void *s_user, *s_utils, *s_mm, *s_msg;
static CRITICAL_SECTION s_steamLock;     // Steam calls from the game's network threads and ours
static uint64_t s_lobby, s_mySteamID;

struct Peer {
    uint64_t steamID;
    uint32_t ip;
    SteamNetworkingIdentity identity;
    int state;      // Steam connection state last logged
    bool relayed;
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
              Load(dll, S.InitRelay, "SteamAPI_ISteamNetworkingUtils_InitRelayNetworkAccess") &
              Load(dll, S.SessionInfo, "SteamAPI_ISteamNetworkingMessages_GetSessionConnectionInfo");
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
    bool queued = false, full = false;
    EnterCriticalSection(&s_lock);
    if (QSocket* s = SocketOnPort(dstPort)) {
        if (s->queue.size() >= 256) {
            s->queue.pop_front();
            full = true;
        }
        QSocket::Packet p;
        p.from.ip = srcIp;
        p.from.port = srcPort;
        p.data.assign(data, data + size);
        s->queue.push_back(std::move(p));
        SetEvent(s_arrived);
        queued = true;
    }
    LeaveCriticalSection(&s_lock);
    char t[20];
    IpText(srcIp, t, sizeof(t));
    if (!queued) {
        InterlockedIncrement(&s_dropped);  // nobody listens on that port (yet)
        if (FirstTime(Key(DIAG_NO_SOCKET, srcIp, dstPort)))
            Log("online: datagram from %s:%u for port %u, but no socket there (%d bytes)", t, srcPort, dstPort, size);
        return;
    }
    if (full) {
        InterlockedIncrement(&s_queueFull);
        if (FirstTime(Key(DIAG_QUEUE_FULL, 0, dstPort)))
            Log("online: receive queue of port %u full (256), the oldest datagram dropped: Quazal reads too slowly", dstPort);
    }
    if (FirstTime(Key(DIAG_FIRST_IN, srcIp, dstPort)))
        Log("online: first datagram from %s:%u to port %u (%d bytes)", t, srcPort, dstPort, size);
}

static const char* SteamResultText(int r)
{
    switch (r) {
    case 1: return "OK";
    case 2: return "fail";
    case 3: return "no connection";
    case 8: return "invalid parameter";
    case 10: return "busy";
    case 16: return "timeout";
    case 25: return "limit exceeded";
    case 37: return "connect failed";
    case 41: return "ignored";
    default: return "?";
    }
}

static void SendWire(const SteamNetworkingIdentity* to, uint32_t toIp, uint16_t srcPort, uint16_t dstPort, const char* data,
                     int size)
{
    char buf[2048];
    if (size < 0 || size > (int)(sizeof(buf) - sizeof(Wire))) {
        InterlockedIncrement(&s_tooBig);
        if (FirstTime(Key(DIAG_TOO_BIG, 0, dstPort)))
            Log("online: datagram to port %u too big for the tunnel (%d bytes, at most %u), dropped", dstPort, size,
                (unsigned)(sizeof(buf) - sizeof(Wire)));
        return;
    }
    Wire w = { kMagic, s_myIp, srcPort, dstPort };
    memcpy(buf, &w, sizeof(w));
    if (size) memcpy(buf + sizeof(w), data, size);
    EnterCriticalSection(&s_steamLock);
    int r = S.SendToUser(s_msg, to, buf, (uint32_t)(sizeof(w) + size), kSendFlags, 0);
    LeaveCriticalSection(&s_steamLock);
    if (r != 1) {
        InterlockedIncrement(&s_sendFailed);
        InterlockedExchange(&s_lastSendResult, r);
        if (FirstTime(Key(DIAG_SEND_FAILED, toIp, (uint16_t)r))) {
            char t[20];
            IpText(toIp, t, sizeof(t));
            Log("online: Steam did not take a datagram to %s:%u: result %d (%s)", t, dstPort, r, SteamResultText(r));
        }
    }
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
    std::vector<std::pair<SteamNetworkingIdentity, uint32_t>> targets;
    for (const Peer& p : s_peers)
        if (broadcast || p.ip == to->ip) targets.push_back(std::make_pair(p.identity, p.ip));
    LeaveCriticalSection(&s_lock);
    char t[20];
    IpText(to->ip, t, sizeof(t));
    if (targets.empty() && !broadcast && to->ip != s_myIp && (to->ip >> 24) != 127) {
        InterlockedIncrement(&s_dropped);
        if (FirstTime(Key(DIAG_NO_SOCKET, to->ip, 0)))
            Log("online: no lobby member has the address %s (datagram to port %u dropped)", t, to->port);
        return;
    }
    if (!broadcast && !targets.empty() && FirstTime(Key(DIAG_FIRST_OUT, to->ip, to->port)))
        Log("online: first datagram to %s:%u from port %u (%d bytes)", t, to->port, srcPort, size);
    if (broadcast && FirstTime(Key(DIAG_FIRST_OUT, 0xFFFFFFFF, to->port)))
        Log("online: first broadcast to port %u from port %u (%d bytes, %u players)", to->port, srcPort, size,
            (unsigned)targets.size());
    for (const auto& target : targets) {
        SendWire(&target.first, target.second, srcPort, to->port, data, size);
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
            if (!e.socket) {  // made by Winsock before the driver was there: never served
                if (FirstTime(Key(DIAG_WINSOCK_SOCKET, 0, 0)))
                    Log("online: Quazal waits on a socket made before the driver, it will not get anything");
                continue;
            }
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
    uint16_t was = port;
    size_t unread = queue.size();
    port = 0;
    queue.clear();
    LeaveCriticalSection(&s_lock);
    if (was) Log("online: socket on port %u closed (%u datagrams unread)", was, (unsigned)unread);
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
    if (n < (int)p.data.size()) {
        InterlockedIncrement(&s_truncated);
        if (FirstTime(Key(DIAG_TRUNCATED, 0, port)))
            Log("online: datagram on port %u cut to Quazal's buffer (%u of %d bytes)", port, (unsigned)p.data.size(), len);
    }
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

static int Unsupported(const char* what)
{
    if (FirstTime(Key(DIAG_UNSUPPORTED, 0, what[0] | (what[1] << 8))))
        Log("online: Quazal called %s on a socket, which the Steam driver does not carry (TCP)", what);
    return Q_ERROR;
}
int QSocket::Connect(const Endpoint*) { return Unsupported("Connect"); }
int QSocket::Recv(char*, int, int*) { return Unsupported("Recv"); }
int QSocket::Send(const char*, int, int*) { return Unsupported("Send"); }
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
        p.state = -1;
        S.IdentityClear(&p.identity);
        S.IdentitySetSteamID(&p.identity, id);
        now.push_back(p);
    }
    EnterCriticalSection(&s_lock);
    std::vector<Peer> before = s_peers;
    for (Peer& p : now)
        for (const Peer& b : before)
            if (b.steamID == p.steamID) {
                p.state = b.state;
                p.relayed = b.relayed;
            }
    s_peers = now;
    LeaveCriticalSection(&s_lock);
    for (size_t i = 0; i < now.size(); i++)
        for (size_t j = i + 1; j < now.size(); j++)
            if (now[i].ip == now[j].ip && FirstTime(Key(DIAG_IP_CLASH, now[i].ip, 0))) {
                char t[20];
                IpText(now[i].ip, t, sizeof(t));
                Log("online: two players got the same address %s, one of them cannot be reached", t);
            }
    for (const Peer& p : now)
        if (p.ip == s_myIp && FirstTime(Key(DIAG_IP_CLASH, p.ip, 1))) Log("online: a player got this player's own address");
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
        SendWire(&p.identity, p.ip, 0, 0, NULL, 0);  // hello: our first message opens the session both ways
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
            Wire w = {};
            if (m->size >= (int)sizeof(Wire)) memcpy(&w, m->data, sizeof(w));
            if (w.magic != kMagic) {
                InterlockedIncrement(&s_badWire);
                if (FirstTime(Key(DIAG_BAD_WIRE, 0, 0)))
                    Log("online: message from Steam that is not ours (%d bytes): other version of the fix?", m->size);
            } else if (!w.dstPort) {
                char t[20];
                IpText(w.srcIp, t, sizeof(t));
                if (FirstTime(Key(DIAG_FIRST_IN, w.srcIp, 0))) Log("online: hello from %s, the Steam session is open", t);
            } else {
                Deliver(w.srcIp, w.srcPort, w.dstPort, (const char*)m->data + sizeof(w), m->size - (int)sizeof(w));
            }
            S.ReleaseMessage(msgs[i]);
        }
    }
}

static const char* StateText(int s)
{
    switch (s) {
    case 0: return "no session";
    case 1: return "connecting";
    case 2: return "finding a route";
    case 3: return "connected";
    case 4: return "closed by the other player";
    case 5: return "lost (problem on this side)";
    default: return "?";
    }
}

// Steam's connection to each player: logged when it changes, with ping and route every 10 s (full)
static void PeerConnections(bool full)
{
    EnterCriticalSection(&s_lock);
    std::vector<Peer> peers = s_peers;
    LeaveCriticalSection(&s_lock);
    for (Peer& p : peers) {
        ConnectionInfo info;
        ConnectionStatus status;
        memset(&info, 0, sizeof(info));
        memset(&status, 0, sizeof(status));
        EnterCriticalSection(&s_steamLock);
        int state = S.SessionInfo(s_msg, &p.identity, &info, &status);
        LeaveCriticalSection(&s_steamLock);
        bool infoOk = info.state == state;  // the structure read as laid out above
        bool relayed = infoOk && info.popRelay != 0;
        char t[20];
        IpText(p.ip, t, sizeof(t));
        if (state != p.state || relayed != p.relayed) {
            if ((state == 4 || state == 5) && infoOk) {
                info.endDebug[sizeof(info.endDebug) - 1] = 0;
                Log("online: connection to %s %s, reason %d: %s", t, StateText(state), info.endReason, info.endDebug);
            } else if (state == 3) {
                Log("online: connection to %s connected, %s, ping %d ms", t, infoOk ? (relayed ? "through a Steam relay" : "direct") : "route unknown",
                    status.ping);
            } else {
                Log("online: connection to %s: %s", t, StateText(state));
            }
            EnterCriticalSection(&s_lock);
            for (Peer& q : s_peers)
                if (q.steamID == p.steamID) {
                    q.state = state;
                    q.relayed = relayed;
                }
            LeaveCriticalSection(&s_lock);
        } else if (full && state == 3) {
            Log("online: %s: ping %d ms, quality %.0f%%/%.0f%%, out %.0f/s, in %.0f/s, %d bytes waiting to be sent", t,
                status.ping, status.qualityLocal * 100, status.qualityRemote * 100, status.outPackets, status.inPackets,
                status.pendingUnreliable + status.pendingReliable);
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
    LONG failed = InterlockedExchange(&s_sendFailed, 0), full = InterlockedExchange(&s_queueFull, 0);
    LONG cut = InterlockedExchange(&s_truncated, 0), bad = InterlockedExchange(&s_badWire, 0), big = InterlockedExchange(&s_tooBig, 0);
    if (failed || full || cut || bad || big)
        Log("online: 10 s PROBLEMS: %ld sends refused by Steam (last result %ld), %ld dropped from full queues, %ld cut short, "
            "%ld foreign messages, %ld too big", failed, s_lastSendResult, full, cut, bad, big);
    PeerConnections(true);
}

// ---------------------------------------------------------------- crashes and the game's debug text

static LPTOP_LEVEL_EXCEPTION_FILTER s_prevFilter;

static LONG WINAPI CrashFilter(EXCEPTION_POINTERS* ep)
{
    const EXCEPTION_RECORD* r = ep->ExceptionRecord;
    HMODULE mod = NULL;
    char name[MAX_PATH] = "?";
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)r->ExceptionAddress, &mod);
    if (mod) GetModuleFileNameA(mod, name, sizeof(name));
    const char* file = strrchr(name, '\\');
    Log("CRASH: exception %08lX at %p (%s+0x%lX), thread %lu, access %s %p", r->ExceptionCode, r->ExceptionAddress,
        file ? file + 1 : name, (unsigned long)((BYTE*)r->ExceptionAddress - (BYTE*)mod), GetCurrentThreadId(),
        r->NumberParameters >= 2 ? (r->ExceptionInformation[0] ? "write" : "read") : "-",
        r->NumberParameters >= 2 ? (void*)r->ExceptionInformation[1] : NULL);
    const CONTEXT* c = ep->ContextRecord;
    Log("CRASH: eax %08lX ebx %08lX ecx %08lX edx %08lX esi %08lX edi %08lX ebp %08lX esp %08lX", c->Eax, c->Ebx, c->Ecx,
        c->Edx, c->Esi, c->Edi, c->Ebp, c->Esp);
    return s_prevFilter ? s_prevFilter(ep) : EXCEPTION_CONTINUE_SEARCH;
}

// OutputDebugStringA through the import slot: Quazal's log device (LogDeviceDebugOutput, 0xAF7700) and the
// game's own debug text, into the log; at most 100 lines per 10 seconds
typedef void(WINAPI* OutputDebugStringA_t)(LPCSTR);
static OutputDebugStringA_t s_origOds;
static void WINAPI Hook_OutputDebugStringA(LPCSTR text)
{
    s_origOds(text);
    if (!text) return;
    static DWORD windowStart;
    static LONG lines, skipped;
    DWORD now = GetTickCount();
    if (now - windowStart > 10000) {
        if (skipped) Log("debug text: %ld more lines left out", skipped);
        windowStart = now;
        lines = skipped = 0;
    }
    if (InterlockedIncrement(&lines) > 100) {
        InterlockedIncrement(&skipped);
        return;
    }
    char line[512];
    lstrcpynA(line, text, sizeof(line));
    size_t n = strlen(line);
    while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
    if (n) Log("debug text: %s", line);
}

static void InstallDiagnostics()
{
    s_prevFilter = SetUnhandledExceptionFilter(CrashFilter);
    void** slot = (void**)G(0x00B9B174);  // KERNEL32!OutputDebugStringA import
    HMODULE k32 = GetModuleHandleA("kernel32.dll");
    if (k32 && *slot == (void*)GetProcAddress(k32, "OutputDebugStringA")) {
        s_origOds = (OutputDebugStringA_t)*slot;
        void* hook = (void*)Hook_OutputDebugStringA;
        WriteMemory(slot, &hook, sizeof(hook));
        Log("online: crash handler set, the game's debug text goes to the log");
    } else {
        Log("online: crash handler set; OutputDebugStringA import not as expected, debug text not logged");
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
            PeerConnections(false);
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
    InitializeCriticalSection(&s_diagLock);
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
    if (g_cfg.log) InstallDiagnostics();
    CloseHandle(CreateThread(NULL, 0, OnlineThread, NULL, 0, NULL));
}
