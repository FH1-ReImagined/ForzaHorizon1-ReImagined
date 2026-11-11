// forza - networking: a virtual System Link network and a call trace.
//
// FORZA_NET=1 turns on the virtual network (RecompiledHaloReach's docs/online_plan.md,
// M1/M2):
//
// - XNetGetEthernetLinkStatus reports a link, so the game offers System Link.
// - The game's UDP sockets (VDP port 1000 and broadcast port 1001) are ours,
//   not the SDK's host sockets: an instance owns one host UDP port, the first
//   free one in 21300-21307, and every datagram carries a header with the guest
//   source and destination ports. Privileged ports and port clashes between
//   instances on one machine never reach the host.
// - A broadcast goes to every port of that range on 127.0.0.1, and with
//   FORZA_NET_LAN=1 also to 255.255.255.255 (other machines).
// - XNetGetTitleXnAddr describes this instance by its host IP (FORZA_NET_IP,
//   else 127.0.0.1, or the LAN address with FORZA_NET_LAN=1) and host port.
//   XNetXnAddrToInAddr maps a peer's XNADDR to a virtual IP 0.77.0.N (a 360
//   secure address: the game connects only to addresses whose first byte is 0) that
//   sendto/recvfrom translate back to the peer's host endpoint (this instance
//   is 0.77.0.1).
// - XNetRandom returns random bytes (the SDK's are constant). Keys are random
//   system-link keys; registering, connecting and QoS listen
//   succeed. VDP is sent unencrypted: only copies of this port talk to it.
//
// FORZA_SERVER=host[:port] (default port 21200) connects to a Forza Live server
// (server/forza_live_server.py) and implies FORZA_NET=1. Everyone on the server, or
// in the same FORZA_ROOM on it, is on one virtual network: broadcasts go to all of
// them through the server, and datagrams for one player go directly once UDP hole
// punching opened a path (FORZA_SERVER_RELAY=1 disables that), through the server
// until then. Where hole punching failed, the server may offer a volunteer relay nearer
// both players (server/forza_live/network/relay_broker.py, #516): the datagrams for those
// players go through it, with a ticket the server signed (we echo the relay's cookie from
// our address first, which proves it is ours), and back through the server when
// the offer is withdrawn or the ticket runs out. The XNADDR of a Live player carries the id the server gave it
// (0xF0000000 | id) instead of a host address. FORZA_NET_PORT fixes the host UDP port,
// for a port forward.
//
// Other socket types (TCP) still go to the SDK.
//
// FORZA_NETTRACE=1 logs every networking import with its raw argument
// registers r3-r8 and the result; busy calls are logged for their first 20
// calls and then every 1000th.

#include "../platform/guest_memory.h"
#include "../platform/sdk_import.h"
#include "gamertag_text.h"
#include "identity.h"
#include "live.h"
#include "live_auth.h"
#include "live_connection.h"
#include "live_signin.h"
#include "live_tcp.h"
#include "../platform/socket.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/flags.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xevent.h>
#include <rex/system/xthread.h>


#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

using forza::GuestPtr;

namespace {

using GuestFunc = void (*)(PPCContext&, uint8_t*);

bool EnvFlag(const char* name) {
  const char* v = std::getenv(name);
  return v && *v && *v != '0';
}

// FORZA_NETTRACE=1 traces calls and datagrams, FORZA_NETTRACE=packets only datagrams.
bool NetTrace() {
  static const bool enabled = EnvFlag("FORZA_NETTRACE") &&
                              std::string(std::getenv("FORZA_NETTRACE")) != "packets";
  return enabled;
}

bool PacketTrace() {
  static const bool enabled = EnvFlag("FORZA_NETTRACE");
  return enabled;
}

bool NetOn() {
  static const bool enabled = EnvFlag("FORZA_NET") || forza::LiveMode();
  return enabled;
}

bool ShouldLog(std::atomic<uint64_t>& calls) {
  uint64_t n = calls.fetch_add(1, std::memory_order_relaxed);
  return n < 20 || n % 1000 == 0;
}

void Log(const char* name, std::atomic<uint64_t>& calls, const uint32_t (&in)[6], uint32_t result,
         const char* note, uint32_t lr = 0) {
  if (NetTrace() && ShouldLog(calls)) {
    REXLOG_INFO(
        "NETTRACE {}({:08X}, {:08X}, {:08X}, {:08X}, {:08X}, {:08X}) -> {:08X}{} [call {}] lr={:08X}",
        name, in[0], in[1], in[2], in[3], in[4], in[5], result, note, calls.load() - 1, lr);
  }
}

// Guest memory is big-endian.
uint16_t Load16(const uint8_t* p) { return uint16_t(p[0] << 8 | p[1]); }
uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
void Store16(uint8_t* p, uint16_t v) {
  p[0] = uint8_t(v >> 8);
  p[1] = uint8_t(v);
}
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}

// Guest return addresses of the calling frames: each frame's back chain is at
// r1, and the caller's LR is saved 8 bytes below the parent frame.
std::string GuestBacktrace(const PPCContext& ctx, const uint8_t* base, int depth) {
  std::string out;
  uint32_t frame = ctx.r1.u32;
  for (int i = 0; i < depth && frame; ++i) {
    uint32_t parent = Load32(GuestPtr(base, frame));
    if (parent <= frame || parent - frame > 0x10000 || (parent & 7)) break;
    uint32_t lr = Load32(GuestPtr(base, parent - 8));
    if (lr < 0x82000000 || lr >= 0x84000000) break;
    char buf[16];
    std::snprintf(buf, sizeof(buf), " %08X", lr);
    out += buf;
    // A 64 KB-aligned frame is the top of the thread's stack: nothing above it is mapped.
    if (!(parent & 0xFFFF)) break;
    frame = parent;
  }
  return out;
}

void Traced(const char* name, GuestFunc sdk, std::atomic<uint64_t>& calls, PPCContext& ctx,
            uint8_t* base) {
  const uint32_t in[6] = {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, ctx.r8.u32};
  const uint32_t lr = uint32_t(ctx.lr);
  if (sdk) sdk(ctx, base);
  Log(name, calls, in, ctx.r3.u32, "", lr);
}

// Winsock errors as the guest sees them.
constexpr uint32_t kWsaEWouldBlock = 10035;
constexpr uint32_t kWsaENotSock = 10038;
constexpr uint32_t kWsaEMsgSize = 10040;
constexpr uint32_t kWsaEAddrInUse = 10048;
constexpr uint32_t kWsaENetUnreach = 10051;
constexpr uint32_t kWsaEInval = 10022;
constexpr uint32_t kWsaOperationAborted = 995;
constexpr uint32_t kWsaIoIncomplete = 996;
constexpr uint32_t kWsaIoPending = 997;
constexpr uint32_t kWsaENotConn = 10057;

// WSAOVERLAPPED: u32 Internal (status), InternalHigh (bytes), Offset, OffsetHigh, hEvent.
constexpr uint32_t kStatusPending = 0x00000103;
constexpr uint32_t kStatusBufferOverflow = 0x80000005;  // datagram larger than the buffers
constexpr uint32_t kStatusCancelled = 0xC0000120;
// WSAEventSelect network events.
constexpr uint32_t kFdRead = 0x01, kFdWrite = 0x02;

// FORZA_NETTRACE also logs datagrams (the first 400, then every 500th).
void LogPacket(const char* dir, uint32_t vip, uint16_t source_port, uint16_t port,
               const uint8_t* data, size_t size) {
  static std::atomic<uint64_t> count{0};
  uint64_t n = count.fetch_add(1, std::memory_order_relaxed);
  if (!PacketTrace() || (n >= 2000 && n % 500 != 0)) return;
  char hex[3 * 256 + 1] = {};
  for (size_t i = 0; i < std::min<size_t>(size, 256); ++i) {
    std::snprintf(hex + 3 * i, 4, "%02X ", data[i]);
  }
  REXLOG_INFO("FORZA_NET {} {} bytes {}:{} -> port {} vip {:08X} [{}] #{}", dir, size, (vip >> 16) == 0x004D ? "vip" : "ip",
              source_port, port, vip, hex, n);
}

constexpr uint32_t kHandleBase = 0x52450000;  // our guest SOCKET handles: 'RE' + index
constexpr uint32_t kMagic = 0x524E4554;       // 'RNET', first word of every LAN datagram
constexpr size_t kHeaderSize = 8;             // magic, guest source port, guest destination port
constexpr uint16_t kHostPortFirst = 21300;  // RecompiledHaloReach uses 21000-21007
constexpr uint16_t kHostPortCount = 8;
// Secure addresses as XNetXnAddrToInAddr returns them on a 360: first byte 0.
// Halo: Reach only connects to such addresses (sub_82273FD0 there).
constexpr uint32_t kSelfVip = 0x004D0001;  // 0.77.0.1
constexpr uint32_t kVipNet = 0x004D0000;   // 0.77.0.0/16
constexpr uint32_t kBroadcast = 0xFFFFFFFF;

// Forza Live server protocol (server/forza_live_server.py; RecompiledHaloReach's
// docs/online_plan.md): every packet starts with 'RLV1' and a type byte.
constexpr uint32_t kLiveMagic = 0x524C5631;
constexpr uint16_t kLiveVersion = 1;
constexpr uint16_t kLiveDefaultPort = 21200;  // Reach Live uses 21100
enum LiveType : uint8_t {
  kHello = 1,    // client -> server: register / keep alive
  kWelcome,      // server -> client: our id, the server epoch, our public address
  kError,        // server -> client
  kBroadcastMsg, // client -> server: a broadcast datagram for everyone in the room
  kRelay,        // client -> server: a datagram for one player, through the server
  kForward,      // server -> client: a broadcast or relayed datagram and who sent it
  kPunch,        // player -> player: open a direct path through NATs
  kPunchAck,     // player -> player
  kData,         // player -> player: a datagram on the direct path
  kBye,
  kList,         // client -> server: who is in the room
  kPeers,        // server -> client: the room's players with their presence
  kPresence,     // client -> server: our presence (friend state, joinable session, status)
  kInvite,       // client -> server -> invitee: a game invite with the inviter's session
  kMatchPublish, // client -> server: our matchmaking session (live_xmsg.cpp)
  kMatchSearch,  // client -> server: matchmaking sessions for a playlist
  kMatchResults, // server -> client
  kWorldEvent,   // server -> a Horizon World's host: take an event (hooks/world_events.cpp)
  kChallenge,    // client <-> server: head-to-head challenges (hooks/world_challenge.cpp)
  kStats,        // client -> server: Xbox LIVE stats written or read (live_xmsg.cpp)
  kStatsRows,    // server -> client: the rows of a read
  kFriend,       // client <-> server: the friends list (live_friends.cpp, server/forza_live/social/friends.py)
  kChat,         // client <-> server: text chat (live_friends.cpp, server/forza_live/social/chat.py)
  // Volunteer relays (server/forza_live/network/relay_broker.py and relay_node.py, #516):
  kRelayInfo = 27,  // server <-> client: the relays to ping (op 0), our pings (1), an offer (2)
  kRelayBind,       // client -> relay: our ticket
  kRelayBound,      // relay -> client: session u64, our id u32, seconds the ticket has left u32
  kRelayPing,       // client -> relay
  kRelayPong,       // relay -> client: the ping's bytes
  kRelayChallenge,  // relay -> client: a cookie; we bind again with it, which proves our address
};
// XNADDR.ina of a player on a Forza Live server: 0xF0000000 | the id the server gave
// it. 240.0.0.0/8 is reserved, so it never collides with a LAN address.
constexpr uint32_t kLiveInaTag = 0xF0000000;

using Clock = std::chrono::steady_clock;

struct Datagram {
  uint32_t vip;
  uint16_t port;
  std::vector<uint8_t> data;
};

// An overlapped receive (WSARecvFrom/WSARecv) waiting for a datagram. Forza's session
// networking keeps one posted on its VDP socket and waits on the OVERLAPPED's event.
struct PendingRecv {
  uint32_t buffers = 0, count = 0;  // guest WSABUF array (u32 len, u32 buf)
  uint32_t from = 0, from_len = 0;  // guest sockaddr_in*, int* (0 for WSARecv)
  uint32_t overlapped = 0;
};

struct GuestSocket {
  uint16_t port = 0;  // bound guest port, 0 while unbound
  bool nonblocking = false;
  uint32_t connected_vip = 0;
  uint16_t connected_port = 0;
  std::deque<Datagram> queue;
  std::deque<PendingRecv> pending;
  uint32_t select_event = 0, select_mask = 0;  // WSAEventSelect
  std::unordered_map<uint32_t, uint32_t> options;  // setsockopt: (level << 16 | name) -> value
};

struct Endpoint {
  uint32_t ip = 0;  // host order
  uint16_t port = 0;
  bool operator==(const Endpoint& o) const { return ip == o.ip && port == o.port; }
  bool operator!=(const Endpoint& o) const { return !(*this == o); }
};

// Another player on the Forza Live server.
struct LivePeer {
  uint32_t vip = 0;
  Endpoint public_ep, local_ep;  // as the server saw it / as it reported its LAN side
  Endpoint direct;               // the path that answered a punch; ip 0: relay via the server
  Clock::time_point punch_until{}, next_punch{}, punch_started{};
};

std::string IpString(uint32_t ip) {
  return std::to_string(ip >> 24) + "." + std::to_string((ip >> 16) & 0xFF) + "." +
         std::to_string((ip >> 8) & 0xFF) + "." + std::to_string(ip & 0xFF);
}

// Another player's address as the log shows it. Players attach forza.log to public issues,
// so a public address keeps only its first two numbers; private and LAN addresses stay whole.
std::string PeerIpForLog(uint32_t ip) {
  const uint32_t a = ip >> 24, b = (ip >> 16) & 0xFF;
  const bool private_ip = a == 10 || a == 127 || (a == 172 && (b & 0xF0) == 16) ||
                          (a == 192 && b == 168) || (a == 100 && (b & 0xC0) == 64) ||
                          (a == 169 && b == 254);
  if (private_ip) return IpString(ip);
  return std::to_string(a) + "." + std::to_string(b) + ".x.x";
}

// Guest XNADDR: ina, inaOnline, wPortOnline, abEnet[6], abOnline[20].
// LAN form: the host IP and UDP port of the instance.
void WriteLanXnAddr(uint8_t* p, uint32_t ip, uint16_t port) {
  std::memset(p, 0, 36);
  Store32(p, ip);
  Store16(p + 8, port);
  const uint8_t mac[6] = {0x00, 0x22, 0x48, uint8_t(ip), uint8_t(port >> 8), uint8_t(port)};
  std::memcpy(p + 10, mac, 6);
}

// Forza Live form: the server id (tagged), the public IP and the server epoch.
void WriteLiveXnAddr(uint8_t* p, uint32_t id, uint16_t epoch, uint32_t public_ip) {
  std::memset(p, 0, 36);
  Store32(p, kLiveInaTag | id);
  Store32(p + 4, public_ip);
  Store16(p + 8, epoch);
  const uint8_t mac[6] = {0x02, uint8_t(id >> 16), uint8_t(id >> 8), uint8_t(id),
                          uint8_t(epoch >> 8), uint8_t(epoch)};
  std::memcpy(p + 10, mac, 6);
}

// Copies a datagram into guest WSABUFs (u32 len, u32 buf); returns the bytes copied.
uint32_t Scatter(uint8_t* base, uint32_t buffers, uint32_t count, const std::vector<uint8_t>& data) {
  size_t done = 0;
  for (uint32_t i = 0; i < count && done < data.size(); ++i) {
    const uint8_t* b = GuestPtr(base, buffers + 8 * i);
    const size_t n = std::min<size_t>(Load32(b), data.size() - done);
    if (n) std::memcpy(GuestPtr(base, Load32(b + 4)), data.data() + done, n);
    done += n;
  }
  return uint32_t(done);
}

// Joins guest WSABUFs into one datagram.
std::vector<uint8_t> Gather(uint8_t* base, uint32_t buffers, uint32_t count) {
  std::vector<uint8_t> data;
  for (uint32_t i = 0; i < count; ++i) {
    const uint8_t* b = GuestPtr(base, buffers + 8 * i);
    const uint8_t* p = GuestPtr(base, Load32(b + 4));
    data.insert(data.end(), p, p + Load32(b));
  }
  return data;
}

// Guest sockaddr_in: u16 family, u16 port, u32 address, 8 bytes of zero.
void WriteSockaddr(uint8_t* a, uint32_t ip, uint16_t port) {
  std::memset(a, 0, 16);
  Store16(a, 2);  // AF_INET
  Store16(a + 2, port);
  Store32(a + 4, ip);
}

void SignalEvent(uint32_t handle) {
  if (!handle) return;
  if (auto ev = REX_KERNEL_OBJECTS()->LookupObject<rex::system::XEvent>(handle)) ev->Set(0, false);
}

class VNet {
 public:
  static VNet& Get() {
    static VNet net;
    return net;
  }

  bool Ready() const { return fd_ != forza::sock::kInvalid; }

  uint32_t CreateSocket() {
    std::lock_guard<std::mutex> lock(mutex_);
    uint32_t handle = kHandleBase + next_handle_++;
    sockets_[handle] = GuestSocket();
    return handle;
  }

  bool IsOurs(uint32_t handle) {
    std::lock_guard<std::mutex> lock(mutex_);
    return sockets_.count(handle) != 0;
  }

  // Closing a socket aborts its overlapped receives, as on Windows.
  void Close(uint32_t handle) {
    std::vector<uint32_t> events;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      auto it = sockets_.find(handle);
      if (it != sockets_.end()) {
        events = CancelRecvsLocked(it->second);
        sockets_.erase(it);
      }
      cv_.notify_all();
    }
    for (uint32_t e : events) SignalEvent(e);
  }

  bool LocalPort(uint32_t handle, uint16_t& port) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end()) return false;
    port = it->second.port;
    return true;
  }

  void SetOption(uint32_t handle, uint32_t key, uint32_t value) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it != sockets_.end()) it->second.options[key] = value;
  }

  bool Option(uint32_t handle, uint32_t key, uint32_t& value) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end()) return false;
    auto o = it->second.options.find(key);
    if (o == it->second.options.end()) return false;
    value = o->second;
    return true;
  }

  // The guest memory base, for completing overlapped receives on the receive thread.
  void SetBase(uint8_t* base) { base_.store(base, std::memory_order_relaxed); }

  // WSARecvFrom/WSARecv with an OVERLAPPED. A queued datagram completes it now (returns
  // 0, or kWsaEMsgSize when it was cut short; `bytes` set); otherwise it stays pending
  // (kWsaIoPending) until Deliver completes it.
  uint32_t RecvOverlapped(uint32_t handle, const PendingRecv& r, uint32_t& bytes) {
    uint32_t event = 0, result = kWsaIoPending;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      auto it = sockets_.find(handle);
      if (it == sockets_.end()) return kWsaENotSock;
      GuestSocket& s = it->second;
      if (s.pending.empty() && !s.queue.empty()) {
        Datagram d = std::move(s.queue.front());
        s.queue.pop_front();
        result = CompleteRecvLocked(r, d, bytes, event) ? kWsaEMsgSize : 0;
      } else {
        uint8_t* ov = GuestPtr(base_.load(std::memory_order_relaxed), r.overlapped);
        Store32(ov + 4, 0);
        Store32(ov, kStatusPending);
        s.pending.push_back(r);
      }
    }
    SignalEvent(event);
    return result;
  }

  // WSACancelOverlappedIO.
  void CancelRecvs(uint32_t handle) {
    std::vector<uint32_t> events;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      auto it = sockets_.find(handle);
      if (it == sockets_.end()) return;
      events = CancelRecvsLocked(it->second);
    }
    for (uint32_t e : events) SignalEvent(e);
  }

  // Waits up to 100 ms for an OVERLAPPED (guest memory) to leave the pending state.
  void WaitOverlapped(const uint8_t* ov) {
    std::unique_lock<std::mutex> lock(mutex_);
    cv_.wait_for(lock, std::chrono::milliseconds(100),
                 [&] { return Load32(ov) != kStatusPending; });
  }

  // WSAEventSelect: `event` is set when a datagram arrives (FD_READ) and right away for
  // FD_WRITE (a datagram socket can always send). The socket becomes nonblocking.
  uint32_t EventSelect(uint32_t handle, uint32_t event, uint32_t mask) {
    bool signal;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      auto it = sockets_.find(handle);
      if (it == sockets_.end()) return kWsaENotSock;
      GuestSocket& s = it->second;
      s.select_event = event;
      s.select_mask = event ? mask : 0;
      if (s.select_mask) s.nonblocking = true;
      signal = (s.select_mask & kFdWrite) || ((s.select_mask & kFdRead) && !s.queue.empty());
    }
    if (signal) SignalEvent(event);
    return 0;
  }

  uint32_t Bind(uint32_t handle, uint16_t port) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end()) return kWsaENotSock;
    if (port == 0) port = next_ephemeral_++;
    for (auto& [h, s] : sockets_) {
      if (h != handle && s.port == port) return kWsaEAddrInUse;
    }
    it->second.port = port;
    return 0;
  }

  uint32_t SetNonblocking(uint32_t handle, bool nonblocking) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end()) return kWsaENotSock;
    it->second.nonblocking = nonblocking;
    return 0;
  }

  uint32_t Connect(uint32_t handle, uint32_t vip, uint16_t port) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end()) return kWsaENotSock;
    it->second.connected_vip = vip;
    it->second.connected_port = port;
    return 0;
  }

  bool Connected(uint32_t handle, uint32_t& vip, uint16_t& port) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end() || !it->second.connected_vip) return false;
    vip = it->second.connected_vip;
    port = it->second.connected_port;
    return true;
  }

  uint32_t PendingBytes(uint32_t handle) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = sockets_.find(handle);
    if (it == sockets_.end() || it->second.queue.empty()) return 0;
    return uint32_t(it->second.queue.front().data.size());
  }

  // Sends one guest datagram; returns 0 or a Winsock error.
  uint32_t SendTo(uint32_t handle, uint32_t vip, uint16_t port, const uint8_t* data, size_t size) {
    uint16_t source_port;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      auto it = sockets_.find(handle);
      if (it == sockets_.end()) return kWsaENotSock;
      if (!it->second.port) it->second.port = next_ephemeral_++;
      source_port = it->second.port;
    }
    LogPacket("send", vip, source_port, port, data, size);
    if (vip == kSelfVip) {
      Deliver(kSelfVip, source_port, port, data, size);
      return 0;
    }
    if (vip == kBroadcast || (vip & 0xFF) == 0xFF) {
      if (!live_ || lan_) {
        std::vector<uint8_t> packet = LanPacket(source_port, port, data, size);
        for (uint16_t p = kHostPortFirst; p < kHostPortFirst + kHostPortCount; ++p) {
          if (p != host_port_) SendHost({INADDR_LOOPBACK, p}, packet);
          if (lan_) SendHost({INADDR_BROADCAST, p}, packet);
        }
      }
      if (live_ && live_id_) {
        std::vector<uint8_t> body(4 + size);
        Store16(body.data(), source_port);
        Store16(body.data() + 2, port);
        std::memcpy(body.data() + 4, data, size);
        SendLive(server_, kBroadcastMsg, body);
      }
      return 0;
    }
    if (live_) {
      uint32_t id = 0;
      Endpoint direct, relay = server_;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = live_id_by_vip_.find(vip);
        if (it != live_id_by_vip_.end()) {
          id = it->second;
          direct = live_peers_[id].direct;
          if (!direct.ip) StartPunchLocked(id);
          if (VolunteerRelayReachesLocked(id)) relay = vrelay_.ep;
        }
      }
      if (id) {
        if (direct.ip && !relay_only_) {
          std::vector<uint8_t> body(8 + size);
          Store32(body.data(), live_id_);
          Store16(body.data() + 4, source_port);
          Store16(body.data() + 6, port);
          std::memcpy(body.data() + 8, data, size);
          SendLive(direct, kData, body);
        } else {
          std::vector<uint8_t> body(8 + size);
          Store32(body.data(), id);
          Store16(body.data() + 4, source_port);
          Store16(body.data() + 6, port);
          std::memcpy(body.data() + 8, data, size);
          SendLive(relay, kRelay, body);  // the server's relay, or the volunteer relay it offered
        }
        return 0;
      }
    }
    Endpoint endpoint;
    if (!LanEndpointOf(vip, endpoint)) return kWsaENetUnreach;
    SendHost(endpoint, LanPacket(source_port, port, data, size));
    return 0;
  }

  // Receives one datagram. Returns 0, or a Winsock error (kWsaEWouldBlock when
  // a nonblocking socket has nothing queued). `size` is the payload size.
  uint32_t ReceiveFrom(uint32_t handle, Datagram& out) {
    std::unique_lock<std::mutex> lock(mutex_);
    for (;;) {
      auto it = sockets_.find(handle);
      if (it == sockets_.end()) return kWsaENotSock;
      if (!it->second.queue.empty()) {
        out = std::move(it->second.queue.front());
        it->second.queue.pop_front();
        return 0;
      }
      if (it->second.nonblocking) return kWsaEWouldBlock;
      cv_.wait_for(lock, std::chrono::milliseconds(100));
    }
  }

  // Waits until one of `handles` has a datagram queued or the timeout passes
  // (negative: forever). Returns the handles that are readable.
  std::vector<uint32_t> WaitReadable(const std::vector<uint32_t>& handles, int64_t timeout_us) {
    std::unique_lock<std::mutex> lock(mutex_);
    auto deadline = Clock::now() + std::chrono::microseconds(timeout_us);
    for (;;) {
      std::vector<uint32_t> ready;
      for (uint32_t h : handles) {
        auto it = sockets_.find(h);
        if (it != sockets_.end() && !it->second.queue.empty()) ready.push_back(h);
      }
      if (!ready.empty() || timeout_us == 0) return ready;
      if (timeout_us < 0) {
        cv_.wait_for(lock, std::chrono::milliseconds(100));
      } else if (cv_.wait_until(lock, deadline) == std::cv_status::timeout) {
        timeout_us = 0;
      }
    }
  }

  // The machine slept for `seconds` (LiveResumed): LiveLoop says HELLO at once.
  void Resumed(double seconds) { resumed_.store(seconds); }

  // This instance's XNADDR: the Forza Live form once the server has welcomed us,
  // else the LAN form.
  void WriteSelfXnAddr(uint8_t* xna) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (live_id_) {
      WriteLiveXnAddr(xna, live_id_, live_epoch_, public_ep_.ip);
    } else {
      WriteLanXnAddr(xna, self_ip_, host_port_);
    }
  }

  // The Forza Live server's address and the TCP port of its title servers (HTTP);
  // false without a server or before it told us the port.
  bool LiveServerHttp(uint32_t& ip, uint16_t& port) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!live_ || !server_.ip || !http_port_) return false;
    ip = server_.ip;
    port = http_port_;
    return true;
  }

  bool LiveConnected() {
    std::lock_guard<std::mutex> lock(mutex_);
    return forza::live_connection::IsFresh(
        live_, live_id_, last_welcome_received_, Clock::now());
  }

  std::vector<forza::LiveFriend> Roster() {
    std::lock_guard<std::mutex> lock(mutex_);
    return roster_;
  }

  void SetPresence(uint32_t state, const uint8_t* session_info, const std::string& status,
                   const std::vector<uint8_t>& extra) {
    {
      std::lock_guard<std::mutex> lock(mutex_);
      presence_.assign(4 + 0x3C, 0);
      Store32(presence_.data(), state);
      if (session_info) std::memcpy(presence_.data() + 4, session_info, 0x3C);
      const std::string line = status.substr(0, 255);
      presence_.push_back(uint8_t(line.size()));
      presence_.insert(presence_.end(), line.begin(), line.end());
      presence_.insert(presence_.end(), extra.begin(), extra.begin() + std::min<size_t>(extra.size(), 1024));
    }
    SendPresence();
  }

  void SendInvite(uint64_t xuid, const uint8_t* session_info) {
    std::vector<uint8_t> body(8 + 0x3C);
    Store32(body.data(), uint32_t(xuid >> 32));
    Store32(body.data() + 4, uint32_t(xuid));
    std::memcpy(body.data() + 8, session_info, 0x3C);
    SendLive(server_, kInvite, body);
  }

  bool SendMatch(LiveType type, const std::vector<uint8_t>& body) {
    std::vector<uint8_t> record;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (type == kMatchPublish && !body.empty()) {
        // op 1 publishes our session, 0 withdraws it; 2 and 4 our standby record, 3
        // withdraws that (a withdrawal is repeated too: it can be lost as well)
        std::vector<uint8_t>& kept = body[0] <= 1 ? match_record_ : standby_record_;
        kept = body;
        if (!live_id_) return false;
        PatchHostLocked(kept);
        record = kept;
      } else if (!live_id_) {
        return false;
      } else if (signin_held_ && (type == kFriend || type == kChat)) {
        // Back from a pause or an error 1, not welcomed again yet: the server may hold no
        // session for us, or not yet the proof of our key, and answers a FRIEND message then
        // with "Friends are off: this XUID's friends belong to another installation".
        return false;
      }
    }
    SendLive(server_, type, record.empty() ? body : record);
    return true;
  }

  // A published record names its host by our XNADDR (the server id we had when the game
  // made the session): after the server restarted we have another id, and a searcher
  // joining the old one waited forever. Needs mutex_ and live_id_.
  void PatchHostLocked(std::vector<uint8_t>& record) {
    constexpr size_t kHostAddress = 5 + 8;  // op, search key, XSESSION_INFO's session id
    if (record.empty() || record[0] == 0 || record[0] == 3) return;
    if (record.size() < kHostAddress + 36) return;
    WriteLiveXnAddr(record.data() + kHostAddress, live_id_, live_epoch_, public_ep_.ip);
  }

  // Our last matchmaking publications again (with every keep-alive): the server loses
  // the records when it restarts or drops us, and one lost datagram would leave a stale
  // record (Find Custom Game then missed a public lobby, #61).
  void ResendMatch() {
    std::vector<std::vector<uint8_t>> records;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!live_id_) return;
      for (auto* r : {&match_record_, &standby_record_}) {
        if (r->empty()) continue;
        PatchHostLocked(*r);
        records.push_back(*r);
      }
    }
    for (const auto& r : records) SendLive(server_, kMatchPublish, r);
  }

  // Virtual IP of a peer's XNADDR.
  uint32_t VipOfXnAddr(const uint8_t* xna) {
    const uint32_t ina = Load32(xna);
    if ((ina & 0xFF000000) == kLiveInaTag) {
      const uint32_t id = ina & 0x00FFFFFF;
      std::lock_guard<std::mutex> lock(mutex_);
      if (id == live_id_) return kSelfVip;
      return LivePeerLocked(id).vip;
    }
    return LanVipOf({ina, Load16(xna + 8)});
  }

  // The XNADDR behind a virtual IP; false when the IP is unknown.
  bool XnAddrOfVip(uint32_t vip, uint8_t* xna) {
    if (vip == kSelfVip) {
      WriteSelfXnAddr(xna);
      return true;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (auto it = live_id_by_vip_.find(vip); it != live_id_by_vip_.end()) {
      WriteLiveXnAddr(xna, it->second, live_epoch_, live_peers_[it->second].public_ep.ip);
      return true;
    }
    if (auto it = endpoint_by_vip_.find(vip); it != endpoint_by_vip_.end()) {
      WriteLanXnAddr(xna, it->second.ip, it->second.port);
      return true;
    }
    return false;
  }

 private:
  VNet() {
    lan_ = EnvFlag("FORZA_NET_LAN");
    live_ = forza::LiveMode();
    relay_only_ = EnvFlag("FORZA_SERVER_RELAY");
    room_ = forza::LiveRoom();
    forza::sock::Startup();  // Windows: before the first socket (LanAddress)
    self_ip_ = INADDR_LOOPBACK;
    if (const char* ip = std::getenv("FORZA_NET_IP"); ip && *ip) {
      in_addr a{};
      if (inet_pton(AF_INET, ip, &a) == 1) self_ip_ = ntohl(a.s_addr);
    } else if (lan_ || live_) {
      self_ip_ = LanAddress();
    }
    fd_ = socket(AF_INET, SOCK_DGRAM, 0);
    int one = 1;
    setsockopt(fd_, SOL_SOCKET, SO_BROADCAST, reinterpret_cast<const char*>(&one), sizeof(one));
    const bool any = lan_ || live_;  // reachable from other machines
    uint16_t first = kHostPortFirst, count = kHostPortCount;
    if (const char* port = std::getenv("FORZA_NET_PORT"); port && *port) {
      first = uint16_t(std::atoi(port));
      count = 1;
    }
    for (uint16_t p = first; p < first + count; ++p) {
      if (BindHost(any, p)) {
        host_port_ = p;
        break;
      }
    }
    if (!host_port_ && live_ && BindHost(any, 0)) {
      sockaddr_in a{};
      socklen_t len = sizeof(a);
      getsockname(fd_, reinterpret_cast<sockaddr*>(&a), &len);
      host_port_ = ntohs(a.sin_port);
    }
    if (!host_port_) {
      REXLOG_ERROR("FORZA_NET: no free host UDP port in {}-{}", first, first + count - 1);
      forza::sock::Close(fd_);
      fd_ = forza::sock::kInvalid;
      return;
    }
    REXLOG_INFO("FORZA_NET: virtual network on {} UDP {}{}", IpString(self_ip_), host_port_,
                lan_ ? " (LAN broadcast on)" : "");
    std::thread([this] { ReceiveLoop(); }).detach();
    if (live_) {
      std::random_device rd;
      live_token_ = uint64_t(rd()) << 32 | rd();
      ParseServerSpec();
      std::thread([this] { LiveLoop(); }).detach();
      // Give the server a moment, so the game sees its Live address from the start.
      std::unique_lock<std::mutex> lock(mutex_);
      cv_.wait_for(lock, std::chrono::seconds(3), [this] { return live_id_ != 0; });
      if (!live_id_ && server_heard_) {
        // It answered, with an error (logged above): not a firewall.
        REXLOG_WARN("FORZA_LIVE: {} has not let this game in yet (the server error above says "
                    "why); still trying", ServerName());
      } else if (!live_id_) {
        REXLOG_WARN("FORZA_LIVE: no answer from {} yet; still trying", ServerName());
      }
    }
  }

  bool BindHost(bool any, uint16_t port) {
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(port);
    a.sin_addr.s_addr = htonl(any ? INADDR_ANY : INADDR_LOOPBACK);
    return bind(fd_, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0;
  }

  static uint32_t LanAddress() {
    // The source address the kernel picks for an outgoing route; no packet is sent.
    forza::sock::Handle s = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(53);
    inet_pton(AF_INET, "192.0.2.1", &a.sin_addr);
    uint32_t ip = INADDR_LOOPBACK;
    if (connect(s, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0) {
      sockaddr_in local{};
      socklen_t len = sizeof(local);
      if (getsockname(s, reinterpret_cast<sockaddr*>(&local), &len) == 0) {
        ip = ntohl(local.sin_addr.s_addr);
      }
    }
    forza::sock::Close(s);
    return ip;
  }

  static std::vector<uint8_t> LanPacket(uint16_t source_port, uint16_t port, const uint8_t* data,
                                        size_t size) {
    std::vector<uint8_t> packet(kHeaderSize + size);
    Store32(packet.data(), kMagic);
    Store16(packet.data() + 4, source_port);
    Store16(packet.data() + 6, port);
    std::memcpy(packet.data() + kHeaderSize, data, size);
    return packet;
  }

  // Virtual IP of a LAN instance's host endpoint (its XNADDR's ina and wPortOnline).
  uint32_t LanVipOf(Endpoint endpoint) {
    if (endpoint.ip == self_ip_ && endpoint.port == host_port_) return kSelfVip;
    std::lock_guard<std::mutex> lock(mutex_);
    uint64_t key = uint64_t(endpoint.ip) << 16 | endpoint.port;
    auto it = vip_by_endpoint_.find(key);
    if (it != vip_by_endpoint_.end()) return it->second;
    uint32_t vip = kVipNet + next_vip_++;
    vip_by_endpoint_[key] = vip;
    endpoint_by_vip_[vip] = endpoint;
    REXLOG_INFO("FORZA_NET: peer {}:{} is 0.77.{}.{}", PeerIpForLog(endpoint.ip), endpoint.port,
                (vip >> 8) & 0xFF, vip & 0xFF);
    return vip;
  }

  bool LanEndpointOf(uint32_t vip, Endpoint& out) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = endpoint_by_vip_.find(vip);
    if (it == endpoint_by_vip_.end()) return false;
    out = it->second;
    return true;
  }

  // The Forza Live player with server id `id`, created on first sight. Needs mutex_.
  LivePeer& LivePeerLocked(uint32_t id) {
    LivePeer& peer = live_peers_[id];
    if (!peer.vip) {
      peer.vip = kVipNet + next_vip_++;
      live_id_by_vip_[peer.vip] = id;
      REXLOG_INFO("FORZA_LIVE: player #{} is 0.77.{}.{}", id, (peer.vip >> 8) & 0xFF,
                  peer.vip & 0xFF);
    }
    return peer;
  }

  // Starts hole punching toward `id` unless a direct path exists or a recent attempt
  // failed. Needs mutex_.
  void StartPunchLocked(uint32_t id) {
    if (relay_only_) return;
    LivePeer& peer = LivePeerLocked(id);
    const auto now = Clock::now();
    if (peer.direct.ip || !peer.public_ep.ip) return;
    if (peer.punch_started != Clock::time_point{} && now - peer.punch_started < std::chrono::seconds(30)) {
      return;
    }
    peer.punch_started = now;
    peer.punch_until = now + std::chrono::seconds(6);
    peer.next_punch = now;
  }

  // Whether datagrams for player `id` go through the volunteer relay: the server offered it
  // for that player, the relay took our ticket and the ticket has not run out. Needs mutex_.
  bool VolunteerRelayReachesLocked(uint32_t id) const {
    return vrelay_.bound && Clock::now() < vrelay_.expires &&
           std::find(vrelay_.peers.begin(), vrelay_.peers.end(), id) != vrelay_.peers.end();
  }

  // RELAY_INFO from the server (server/forza_live/network/relay_broker.py).
  void OnRelayInfo(const uint8_t* p, size_t n) {
    if (n < 1) return;
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = Clock::now();
    if (p[0] == 0 && n >= 4) {  // the relays to ping: version u16, count u8, (index, ip, port)
      const uint16_t version = Load16(p + 1);
      const size_t count = std::min<size_t>(p[3], (n - 4) / 7);
      probes_.clear();
      for (size_t i = 0; i < count; ++i) {
        const uint8_t* e = p + 4 + 7 * i;
        probes_.push_back({e[0], {Load32(e + 1), Load16(e + 5)}, {}, 0xFFFF, 0});
      }
      probe_version_ = version;
      probe_report_ = now + std::chrono::seconds(2);
      probe_reported_ = false;
      return;
    }
    if (p[0] != 2 || n < 8) return;  // an offer: ip u32, port u16, ticket, count u8, peer ids
    const Endpoint ep{Load32(p + 1), Load16(p + 5)};
    const size_t ticket_size = p[7];
    if (!ep.ip) {
      if (vrelay_.ep.ip) {
        REXLOG_INFO("FORZA_LIVE: the server withdrew the relay; its players go through the server");
      }
      vrelay_ = VolunteerRelay{};
      return;
    }
    if (8 + ticket_size + 1 > n) return;
    std::vector<uint8_t> ticket(p + 8, p + 8 + ticket_size);
    const size_t count = std::min<size_t>(p[8 + ticket_size], (n - 9 - ticket_size) / 4);
    std::vector<uint32_t> peers;
    for (size_t i = 0; i < count; ++i) peers.push_back(Load32(p + 9 + ticket_size + 4 * i));
    if (ep != vrelay_.ep) {
      REXLOG_INFO("FORZA_LIVE: the server offers the relay {}:{} for {} player(s)",
                  PeerIpForLog(ep.ip), ep.port, peers.size());
      vrelay_ = VolunteerRelay{};
      vrelay_.ep = ep;
    }
    if (ticket != vrelay_.ticket) {
      vrelay_.ticket = std::move(ticket);
      vrelay_.next_bind = now;  // bind with the new ticket now (a renewal keeps the slot)
    }
    vrelay_.peers = std::move(peers);
  }

  // Binds at the volunteer relay (and keeps the slot), pings the relays the server listed and
  // reports the pings. From LiveLoop.
  void RelayTick(Clock::time_point now) {
    std::vector<std::pair<Endpoint, std::vector<uint8_t>>> out;
    std::vector<uint8_t> report;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (vrelay_.ep.ip && vrelay_.bound && now >= vrelay_.expires) {
        REXLOG_INFO("FORZA_LIVE: the relay ticket ran out; its players go through the server");
        vrelay_.bound = false;
      }
      if (vrelay_.ep.ip && vrelay_.bound && now - vrelay_.last_bound > std::chrono::seconds(7)) {
        // The relay stopped answering our binds: back to the server's relay before the
        // game's session gives up on the other players (the server notices only after 30 s).
        REXLOG_INFO("FORZA_LIVE: the relay stopped answering; its players go through the server");
        vrelay_.bound = false;
      }
      if (vrelay_.ep.ip && !vrelay_.ticket.empty() && now >= vrelay_.next_bind) {
        // A bind every 3 s keeps the slot (unused slots expire after 60 s at the relay) and
        // shows the relay still answers; while unbound, every second.
        vrelay_.next_bind = now + std::chrono::seconds(vrelay_.bound ? 3 : 1);
        std::vector<uint8_t> bind = vrelay_.ticket;  // and the relay's last cookie, if any
        bind.insert(bind.end(), vrelay_.cookie.begin(), vrelay_.cookie.end());
        out.push_back({vrelay_.ep, std::move(bind)});
      }
      for (RelayProbe& probe : probes_) {
        if (probe.sent_count < 2 && now >= probe.sent + std::chrono::milliseconds(500) &&
            probe.ms == 0xFFFF && !probe_reported_) {
          probe.sent = now;
          probe.sent_count++;
          std::vector<uint8_t> body(3);
          Store16(body.data(), probe_version_);
          body[2] = probe.index;
          out.push_back({probe.ep, body});
        }
      }
      if (!probes_.empty() && !probe_reported_ && now >= probe_report_) {
        probe_reported_ = true;
        report = {1, 0, 0, uint8_t(probes_.size())};
        Store16(report.data() + 1, probe_version_);
        for (const RelayProbe& probe : probes_) {
          report.push_back(probe.index);
          report.push_back(uint8_t(probe.ms >> 8));
          report.push_back(uint8_t(probe.ms));
        }
      }
    }
    for (auto& [to, body] : out) {
      SendLive(to, body.size() == 3 ? kRelayPing : kRelayBind, body);
    }
    if (!report.empty()) SendLive(server_, kRelayInfo, report);
  }

  std::string ServerName() const { return server_host_ + ":" + std::to_string(server_port_); }

  // Host and port of FORZA_SERVER / live_server; set once, before LiveLoop starts.
  void ParseServerSpec() {
    std::string spec = forza::LiveServerSpec();
    server_port_ = kLiveDefaultPort;
    if (auto colon = spec.rfind(':'); colon != std::string::npos) {
      server_port_ = uint16_t(std::atoi(spec.c_str() + colon + 1));
      spec.resize(colon);
    }
    server_host_ = spec;
    forza::live_auth::SetServer(server_host_);
  }

  bool ResolveServer() {
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    addrinfo* result = nullptr;
    if (getaddrinfo(server_host_.c_str(), nullptr, &hints, &result) != 0 || !result) {
      REXLOG_WARN("FORZA_LIVE: cannot resolve server {}", server_host_);
      return false;
    }
    uint32_t ip = ntohl(reinterpret_cast<sockaddr_in*>(result->ai_addr)->sin_addr.s_addr);
    freeaddrinfo(result);
    std::lock_guard<std::mutex> lock(mutex_);
    if (ip != server_.ip) {
      // The address the game sends to, and the profile's country and language (the launcher's
      // Country ID and Language ID), for reports where the server never answers (#392).
      REXLOG_INFO("FORZA_LIVE: server {} is {} (country {}, language {})", ServerName(),
                  IpString(ip), REXCVAR_GET(user_country), REXCVAR_GET(user_language));
    }
    server_ = {ip, server_port_};
    return true;
  }

  void SendLive(Endpoint to, LiveType type, const std::vector<uint8_t>& body) {
    std::vector<uint8_t> packet(5 + body.size());
    Store32(packet.data(), kLiveMagic);
    packet[4] = type;
    std::memcpy(packet.data() + 5, body.data(), body.size());
    SendHost(to, packet);
  }

  void SendHello() {
    const std::string& name = forza::IdentityGamertag();
    std::vector<uint8_t> body(24);
    Store16(body.data(), kLiveVersion);
    Store32(body.data() + 2, uint32_t(live_token_ >> 32));
    Store32(body.data() + 6, uint32_t(live_token_));
    const uint64_t xuid = forza::IdentityXuid();
    Store32(body.data() + 10, uint32_t(xuid >> 32));
    Store32(body.data() + 14, uint32_t(xuid));
    Store32(body.data() + 18, self_ip_);
    Store16(body.data() + 22, host_port_);
    body.push_back(uint8_t(room_.size()));
    body.insert(body.end(), room_.begin(), room_.end());
    body.push_back(uint8_t(name.size()));
    body.insert(body.end(), name.begin(), name.end());
    // Features (older games send none): 0x01, invites go only to players the player picked
    // and an invite asks before joining (live_invites.cpp, #90). The server delivers invites
    // only between games that have it. 0x02, the game keeps its friends list on the server
    // (live_friends.cpp, #92), followed by the proof of our friends key (u8 length + bytes;
    // live_auth.cpp: the key itself never goes out), which the server binds our XUID's
    // friends to. 0x04, text chat (#92). 0x08, the Co-op Festival playlist runs as a Horizon
    // World (world_drop_in.cpp, world_events.cpp, #309): the server lists it only to games
    // that say so. 0x10, our standby records follow the lobby (its game type, and as the
    // game's host its own description; live_xmsg.cpp, #391). 0x40, we ping the volunteer
    // relays the server lists and take its offer of one (OnRelayInfo, #516).
    body.push_back(0x5F);
    const std::vector<uint8_t> tail = forza::live_auth::HelloTail(body);
    body.insert(body.end(), tail.begin(), tail.end());
    SendLive(server_, kHello, body);
  }

  void SendPresence() {
    std::vector<uint8_t> body;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      if (!live_id_ || presence_.empty()) return;
      body = presence_;
    }
    SendLive(server_, kPresence, body);
  }

  // Registration, keep-alive, presence, the roster and hole punching.
  void LiveLoop() {
    Clock::time_point next_hello{}, next_resolve{}, next_list{}, next_match{};
    bool resolved = false;
    // Never welcomed: when the first HELLO went out, the next lookup of the server's name,
    // and whether the player was told (#392).
    Clock::time_point first_hello{}, next_reresolve{};
    bool told_silent = false;
    Clock::time_point last_pass = Clock::now();
    for (;;) {
      const auto now = Clock::now();
      double away = resumed_.exchange(0);  // the machine slept (LiveResumed)
      if (now - last_pass >= std::chrono::seconds(2)) {
        // This thread did not run either: the game was stopped.
        away += std::chrono::duration<double>(now - last_pass).count();
      }
      if (away > 0 && resolved) {
        // The server may have dropped us meanwhile (#140): say HELLO now instead of in up to 5 s.
        REXLOG_INFO("FORZA_LIVE: back after {:.0f} s; signing in to {} again", away, ServerName());
        next_hello = next_list = now;
        rejoining_ = true;  // log the welcome even when the server kept our id
        std::lock_guard<std::mutex> lock(mutex_);
        last_welcome_ = now;  // not "lost the server": the time away was ours
        signin_held_ = true;  // no LIST before the server welcomes us again
      }
      if (!resolved && now >= next_resolve) {
        resolved = ResolveServer();
        next_resolve = now + std::chrono::seconds(10);
        if (resolved) {
          first_hello = now;
          next_reresolve = now + std::chrono::seconds(30);
        }
      }
      if (resolved) {
        bool welcomed;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          welcomed = live_id_ != 0;
        }
        if (!welcomed && now >= next_reresolve) {
          // Not a word from the server yet: its name may point elsewhere by now.
          ResolveServer();
          next_reresolve = now + std::chrono::seconds(30);
        }
        if (!welcomed && !told_silent && !server_heard_ &&
            now - first_hello >= std::chrono::seconds(30)) {
          // Every online feature of the game says "the Forza Horizon server is unavailable"
          // until the server answers once (#392).
          told_silent = true;
          Endpoint to;
          {
            std::lock_guard<std::mutex> lock(mutex_);
            to = server_;
          }
          REXLOG_WARN(
              "FORZA_LIVE: {} ({}:{}) has not answered in 30 s; online stays unavailable until it "
              "does. Something between this PC and the server drops its UDP traffic: a firewall "
              "or antivirus, a VPN or proxy, or the network or provider blocking the address",
              ServerName(), IpString(to.ip), to.port);
        }
      }
      if (resolved && now >= next_hello) {
        SendHello();
        SendPresence();
        ResendMatch();
        std::lock_guard<std::mutex> lock(mutex_);
        next_hello = now + std::chrono::seconds(live_id_ ? 5 : 1);
        if (live_id_ && now - last_welcome_ > std::chrono::seconds(20)) {
          REXLOG_WARN("FORZA_LIVE: lost the server {}; reconnecting", ServerName());
          last_welcome_ = now;
        }
      }
      // After the HELLO, and only once the server welcomed us (live_signin.h): a LIST from an
      // address it holds no session for is answered with error 1, "not registered", which
      // almost every log showed at the start and players blamed for not getting online.
      if (resolved && now >= next_list && forza::LiveSignin()) {
        bool signed_in;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          signed_in = forza::live_signin::MaySendSignedIn(live_id_, signin_held_,
                                                          last_welcome_received_, now);
        }
        if (signed_in) {
          next_list = now + std::chrono::seconds(3);
          SendLive(server_, kList, {});
        }
      }
      if (resolved) {
        bool warn;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          warn = resign_.ShouldWarn(now);
        }
        if (warn) {
          REXLOG_WARN("FORZA_LIVE: {} asked this game to sign in again and has not welcomed it "
                      "back in {} s; online stays unavailable until it does",
                      ServerName(), forza::live_signin::kResignGrace.count());
        }
      }
      std::vector<std::pair<Endpoint, std::vector<uint8_t>>> punches;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        for (auto& [id, peer] : live_peers_) {
          if (peer.direct.ip || now >= peer.punch_until || now < peer.next_punch) continue;
          peer.next_punch = now + std::chrono::milliseconds(300);
          std::vector<uint8_t> body(10);
          Store32(body.data(), live_id_);
          Store32(body.data() + 4, id);
          Store16(body.data() + 8, live_epoch_);
          punches.push_back({peer.public_ep, body});
          if (peer.local_ep.ip && peer.local_ep != peer.public_ep) {
            punches.push_back({peer.local_ep, body});
          }
        }
      }
      for (auto& [to, body] : punches) SendLive(to, kPunch, body);
      if (resolved) RelayTick(now);
      int roster_changed;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        roster_changed = roster_changed_;
        roster_changed_ = 0;
      }
      if (roster_changed) forza::LiveRosterChanged(roster_changed == 2);
      if (resolved && now >= next_match) {
        next_match = now + std::chrono::seconds(1);
        forza::LiveMatchTick();
        forza::LiveFriendsTick();
      }
      // From here: a pass that took long by itself (a slow name lookup) is no time away.
      last_pass = Clock::now();
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }

  void OnLive(Endpoint from, uint8_t type, const uint8_t* p, size_t n) {
    bool from_server, from_relay;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      from_server = from == server_;
      from_relay = !from_server && vrelay_.ep.ip && from == vrelay_.ep;
    }
    if (from_server) server_heard_ = true;
    if (from_relay && type == kError) {
      // 1: the relay has no slot for us (it restarted, or the slot expired): bind again.
      // 7: it refused the ticket; the server's relay carries our datagrams meanwhile.
      if (n < 3) return;
      std::lock_guard<std::mutex> lock(mutex_);
      if (vrelay_.bound) {
        const size_t size = std::min<size_t>(Load16(p + 1), n - 3);
        REXLOG_INFO("FORZA_LIVE: the relay says {}: {}", p[0],
                    std::string(reinterpret_cast<const char*>(p + 3), size));
      }
      vrelay_.bound = false;
      vrelay_.next_bind = Clock::now() + std::chrono::seconds(p[0] == 1 ? 0 : 5);
      return;
    }
    switch (type) {
      case kRelayInfo:
        if (from_server) OnRelayInfo(p, n);
        return;
      case kRelayChallenge: {
        // The relay proves our address before it sends us anything (a forged bind cannot aim
        // the session's traffic elsewhere): the same ticket back with its cookie, from here.
        if (!from_relay || n != 16) return;
        std::vector<uint8_t> bind;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          if (vrelay_.ticket.empty()) return;
          vrelay_.cookie.assign(p, p + n);
          bind = vrelay_.ticket;
          bind.insert(bind.end(), p, p + n);
        }
        SendLive(from, kRelayBind, bind);
        return;
      }
      case kRelayBound: {
        if (!from_relay || n < 16) return;
        std::lock_guard<std::mutex> lock(mutex_);
        if (!vrelay_.bound) {
          REXLOG_INFO("FORZA_LIVE: relayed through {}:{} for {} player(s) the punch did not reach",
                      PeerIpForLog(from.ip), from.port, vrelay_.peers.size());
        }
        vrelay_.bound = true;
        vrelay_.last_bound = Clock::now();
        vrelay_.expires = vrelay_.last_bound + std::chrono::seconds(Load32(p + 12));
        return;
      }
      case kRelayPong: {
        if (n < 3) return;
        std::lock_guard<std::mutex> lock(mutex_);
        for (RelayProbe& probe : probes_) {
          if (probe.ep == from && probe.index == p[2] && Load16(p) == probe_version_ &&
              probe.ms == 0xFFFF) {
            const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                Clock::now() - probe.sent).count();
            probe.ms = uint16_t(std::min<int64_t>(ms, 0xFFFE));
          }
        }
        return;
      }
      default:
        break;
    }
    switch (type) {
      case kWelcome: {
        if (!from_server || n < 14) return;
        const uint32_t id = Load32(p), public_ip = Load32(p + 6);
        const uint16_t epoch = Load16(p + 4), public_port = Load16(p + 10);
        const size_t motd_size = std::min<size_t>(Load16(p + 12), n - 14);
        // After the title server port (and no events byte: our HELLO has no feature 0x20): the
        // server's clock, our challenge and how our XUID is bound there (live_auth.cpp).
        const size_t auth_at = 14 + motd_size + 2;
        const bool hello_now = forza::live_auth::OnWelcome(n > auth_at ? p + auth_at : nullptr,
                                                           n > auth_at ? n - auth_at : 0);
        bool changed;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          if (n >= 14 + motd_size + 2) http_port_ = Load16(p + 14 + motd_size);
          changed = id != live_id_ || epoch != live_epoch_;
          live_id_ = id;
          live_epoch_ = epoch;
          public_ep_ = {public_ip, public_port};
          last_welcome_ = Clock::now();
          last_welcome_received_ = last_welcome_;
          signin_held_ = hello_now;  // a WELCOME with a new challenge waits for its answered HELLO
          resign_.Welcomed();
          cv_.notify_all();
        }
        if (rejoining_.exchange(false) || changed) {
          REXLOG_INFO("FORZA_LIVE: signed in to {} as {} (player #{}), public address {}:{}{}{}",
                      ServerName(), forza::IdentityGamertag(), id, IpString(public_ip),
                      public_port, motd_size ? ": " : "",
                      std::string(reinterpret_cast<const char*>(p + 14), motd_size));
        }
        if (hello_now) SendHello();  // the challenge's answer
        return;
      }
      case kError: {
        if (!from_server || n < 3) return;
        const uint8_t code = p[0];
        const std::string detail(reinterpret_cast<const char*>(p + 3),
                                 std::min<size_t>(Load16(p + 1), n - 3));
        bool log, last;
        std::string notice;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          // Lines are counted per code: a flood of one (error 1 after a server restart, a
          // refused HELLO each second) never hides another, such as a kick or a ban.
          log = server_errors_.Take(code, last);
          if (code == 1) {
            // "Not registered": the server restarted, or dropped us after a pause or 30 s
            // without a HELLO. The HELLO below signs in again; LiveLoop warns if no WELCOME
            // follows, and holds LIST until one does.
            resign_.Asked(Clock::now());
            signin_held_ = true;
          }
          if (forza::live_signin::IsRefusal(code) && !noticed_[code]) {
            noticed_[code] = true;  // on screen once per code and session
            notice = forza::live_signin::NoticeText(code);
          }
        }
        if (log) {
          const std::string text = forza::live_signin::ErrorText(code, ServerName(), detail);
          const char* more = last ? " (further lines for this error are not logged)" : "";
          if (code == 1) {
            REXLOG_INFO("FORZA_LIVE: {} (error 1){}", text, more);
          } else {
            REXLOG_WARN("FORZA_LIVE: server error {}: {}{}", code, text, more);
          }
        }
        if (!notice.empty()) forza::LiveOnlineNotice(notice);
        if (code == 1) SendHello();
        return;
      }
      case kForward: {
        if (!(from_server || from_relay) || n < 20) return;
        const uint32_t id = Load32(p);
        uint32_t vip;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          LivePeer& peer = LivePeerLocked(id);
          if (from_server) {  // a volunteer relay names no addresses
            peer.public_ep = {Load32(p + 4), Load16(p + 8)};
            peer.local_ep = {Load32(p + 10), Load16(p + 14)};
          }
          vip = peer.vip;
          if (!peer.direct.ip) StartPunchLocked(id);
        }
        Deliver(vip, Load16(p + 16), Load16(p + 18), p + 20, n - 20);
        return;
      }
      case kPunch:
      case kPunchAck: {
        if (n < 10) return;
        const uint32_t id = Load32(p);
        std::vector<uint8_t> ack;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          if (relay_only_ || Load32(p + 4) != live_id_ || Load16(p + 8) != live_epoch_) return;
          LivePeer& peer = LivePeerLocked(id);
          if (!peer.direct.ip) {  // the first path that works; punches may arrive on several
            REXLOG_INFO("FORZA_LIVE: direct path to player #{} via {}:{}", id, PeerIpForLog(from.ip),
                        from.port);
            peer.direct = from;
          }
          if (type == kPunch) {
            ack.resize(10);
            Store32(ack.data(), live_id_);
            Store32(ack.data() + 4, id);
            Store16(ack.data() + 8, live_epoch_);
          }
        }
        if (!ack.empty()) SendLive(from, kPunchAck, ack);
        return;
      }
      case kData: {
        if (n < 8) return;
        uint32_t vip;
        {
          std::lock_guard<std::mutex> lock(mutex_);
          auto it = live_peers_.find(Load32(p));
          if (it == live_peers_.end()) return;
          LivePeer& peer = it->second;
          if (from != peer.direct && from != peer.public_ep && from != peer.local_ep) return;
          if (!peer.direct.ip) peer.direct = from;
          vip = peer.vip;
        }
        Deliver(vip, Load16(p + 4), Load16(p + 6), p + 8, n - 8);
        return;
      }
      case kInvite: {
        // inviter id u32, inviter XUID u64, gamertag (u8 length + bytes), XSESSION_INFO
        if (!from_server || n < 13) return;
        forza::LiveInvite invite;
        invite.inviter_xuid = uint64_t(Load32(p + 4)) << 32 | Load32(p + 8);
        const size_t name_size = p[12];
        if (13 + name_size + 0x3C > n) return;
        invite.inviter.assign(reinterpret_cast<const char*>(p + 13), name_size);
        std::memcpy(invite.session, p + 13 + name_size, 0x3C);
        forza::LiveInviteReceived(invite);  // the player decides (live_invites.cpp)
        return;
      }
      case kMatchResults:
        if (from_server) forza::LiveMatchResults(p, n);
        return;
      case kWorldEvent:
        if (from_server) forza::LiveWorldEvent(p, n);
        return;
      case kChallenge:
        if (from_server) forza::LiveChallenge(p, n);
        return;
      case kStatsRows:
        if (from_server) forza::LiveStatsRows(p, n);
        return;
      case kFriend:
        if (from_server) forza::LiveFriendMessage(p, n);
        return;
      case kChat:
        if (from_server) forza::LiveChatMessage(p, n);
        return;
      case kPeers: {
        // Part header: total u16, first index u16, count u16; then the entries.
        if (!from_server || n < 6) return;
        const uint16_t total = Load16(p), first = Load16(p + 2), count = Load16(p + 4);
        size_t pos = 6;
        std::lock_guard<std::mutex> lock(mutex_);
        if (first == 0) roster_parts_.clear();
        if (first != roster_parts_seen_ && first != 0) return;  // a part went missing
        roster_parts_seen_ = first;
        for (uint16_t i = 0; i < count; ++i) {
          forza::LiveFriend f;
          if (pos + 13 > n) return;
          f.id = Load32(p + pos);
          f.xuid = uint64_t(Load32(p + pos + 4)) << 32 | Load32(p + pos + 8);
          const size_t name_size = p[pos + 12];
          pos += 13;
          if (pos + name_size + 4 + 0x3C + 1 > n) return;
          f.gamertag = forza::SanitizeGamertag(std::string_view(reinterpret_cast<const char*>(p + pos), name_size));
          pos += name_size;
          f.state = Load32(p + pos);
          std::memcpy(f.session, p + pos + 4, 0x3C);
          pos += 4 + 0x3C;
          const size_t status_size = std::min<size_t>(p[pos], n - pos - 1);
          f.status.assign(reinterpret_cast<const char*>(p + pos + 1), status_size);
          pos += 1 + status_size;
          if (pos + 2 > n) return;
          const size_t extra_size = std::min<size_t>(Load16(p + pos), n - pos - 2);
          f.extra.assign(p + pos + 2, p + pos + 2 + extra_size);
          pos += 2 + extra_size;
          roster_parts_seen_++;
          if (f.id != live_id_) roster_parts_.push_back(std::move(f));
        }
        if (roster_parts_seen_ >= total) {
          bool membership = roster_parts_.size() != roster_.size(), changed = membership;
          for (size_t i = 0; i < roster_parts_.size() && !membership; ++i) {
            const forza::LiveFriend &a = roster_parts_[i], &b = roster_[i];
            membership = a.id != b.id;
            changed = changed || membership || a.state != b.state || a.status != b.status ||
                      a.extra != b.extra ||
                      std::memcmp(a.session, b.session, sizeof(a.session)) != 0;
          }
          roster_ = roster_parts_;
          roster_parts_seen_ = 0;
          if (changed) roster_changed_ = membership ? 2 : std::max(roster_changed_, 1);
        }
        return;
      }
      default:
        return;
    }
  }

  void SendHost(Endpoint endpoint, const std::vector<uint8_t>& packet) {
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(endpoint.port);
    a.sin_addr.s_addr = htonl(endpoint.ip);
    forza::sock::SendTo(fd_, packet.data(), packet.size(), a);
  }

  void Deliver(uint32_t vip, uint16_t source_port, uint16_t port, const uint8_t* data,
               size_t size) {
    LogPacket("recv", vip, source_port, port, data, size);
    uint32_t events[2] = {0, 0};  // a completed receive's, WSAEventSelect's
    {
      std::lock_guard<std::mutex> lock(mutex_);
      for (auto& [handle, s] : sockets_) {
        if (s.port != port) continue;
        Datagram d{vip, source_port, std::vector<uint8_t>(data, data + size)};
        if (!s.pending.empty() && base_.load(std::memory_order_relaxed)) {
          const PendingRecv r = s.pending.front();
          s.pending.pop_front();
          uint32_t bytes;
          CompleteRecvLocked(r, d, bytes, events[0]);
        } else {
          if (s.queue.size() >= 256) s.queue.pop_front();
          s.queue.push_back(std::move(d));
          if (s.select_mask & kFdRead) events[1] = s.select_event;
        }
        cv_.notify_all();
        break;
      }
    }
    for (uint32_t e : events) SignalEvent(e);
  }

  // Copies a datagram into a pending receive's buffers and completes its OVERLAPPED:
  // the byte count, then the status. Returns the status; `event` is the OVERLAPPED's
  // event, which the caller sets once it has unlocked.
  uint32_t CompleteRecvLocked(const PendingRecv& r, const Datagram& d, uint32_t& bytes,
                              uint32_t& event) {
    uint8_t* base = base_.load(std::memory_order_relaxed);
    bytes = Scatter(base, r.buffers, r.count, d.data);
    if (r.from) WriteSockaddr(GuestPtr(base, r.from), d.vip, d.port);
    if (r.from_len) Store32(GuestPtr(base, r.from_len), 16);
    const uint32_t status = bytes < d.data.size() ? kStatusBufferOverflow : 0;
    uint8_t* ov = GuestPtr(base, r.overlapped);
    Store32(ov + 4, bytes);
    std::atomic_thread_fence(std::memory_order_release);
    Store32(ov, status);
    event = Load32(ov + 16);
    return status;
  }

  std::vector<uint32_t> CancelRecvsLocked(GuestSocket& s) {
    std::vector<uint32_t> events;
    uint8_t* base = base_.load(std::memory_order_relaxed);
    for (const PendingRecv& r : s.pending) {
      uint8_t* ov = GuestPtr(base, r.overlapped);
      Store32(ov + 4, 0);
      Store32(ov, kStatusCancelled);
      if (uint32_t e = Load32(ov + 16)) events.push_back(e);
    }
    s.pending.clear();
    cv_.notify_all();
    return events;
  }

  void ReceiveLoop() {
    std::vector<uint8_t> buffer(65536);
    for (;;) {
      sockaddr_in from{};
      const int64_t n = forza::sock::RecvFrom(fd_, buffer.data(), buffer.size(), from);
      if (n < 5) continue;
      Endpoint endpoint{ntohl(from.sin_addr.s_addr), ntohs(from.sin_port)};
      const uint32_t magic = Load32(buffer.data());
      if (magic == kLiveMagic) {
        OnLive(endpoint, buffer[4], buffer.data() + 5, size_t(n) - 5);
        continue;
      }
      if (n < int64_t(kHeaderSize) || magic != kMagic) continue;
      if (endpoint.port == host_port_ && (endpoint.ip == self_ip_ || endpoint.ip == INADDR_LOOPBACK)) {
        continue;  // our own broadcast
      }
      uint32_t vip = LanVipOf(endpoint);
      Deliver(vip, Load16(buffer.data() + 4), Load16(buffer.data() + 6),
              buffer.data() + kHeaderSize, size_t(n) - kHeaderSize);
    }
  }

  forza::sock::Handle fd_ = forza::sock::kInvalid;
  bool lan_ = false;
  uint32_t self_ip_ = 0;
  uint16_t host_port_ = 0;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::atomic<uint8_t*> base_{nullptr};
  std::unordered_map<uint32_t, GuestSocket> sockets_;
  uint32_t next_handle_ = 1;
  uint16_t next_ephemeral_ = 49152;
  std::unordered_map<uint64_t, uint32_t> vip_by_endpoint_;
  std::unordered_map<uint32_t, Endpoint> endpoint_by_vip_;
  uint32_t next_vip_ = 2;

  // Forza Live (FORZA_SERVER).
  bool live_ = false;
  bool relay_only_ = false;  // FORZA_SERVER_RELAY=1: never punch, for testing the relay
  std::string room_;
  std::string server_host_;
  uint16_t server_port_ = kLiveDefaultPort;
  Endpoint server_;
  uint64_t live_token_ = 0;  // tells the server a restarted game from a stale registration
  uint32_t live_id_ = 0;     // our id on the server, 0 until it welcomes us
  uint16_t live_epoch_ = 0;
  Endpoint public_ep_;
  uint16_t http_port_ = 0;  // the server's title servers (HTTP), from its welcome
  Clock::time_point last_welcome_{};
  // Receipt-only timestamp: reconnect logging must never refresh UI liveness.
  Clock::time_point last_welcome_received_{};
  std::atomic<double> resumed_{0};  // seconds the machine slept (LiveResumed), 0: none
  std::atomic<bool> rejoining_{false};  // back after a stop or a sleep, not welcomed yet
  std::atomic<bool> server_heard_{false};  // any datagram from the server so far
  // Sign-in (live_signin.h), under mutex_: no LIST until a WELCOME after a pause, a sleep or
  // an error 1; an error 1 without a WELCOME soon after; log lines per error code; refusals
  // already shown on screen.
  bool signin_held_ = false;
  forza::live_signin::Resign resign_;
  forza::live_signin::ErrorLog server_errors_;
  bool noticed_[6] = {};
  std::unordered_map<uint32_t, LivePeer> live_peers_;
  std::unordered_map<uint32_t, uint32_t> live_id_by_vip_;
  // The volunteer relay the server offered (#516): where, our ticket, the players it reaches.
  struct VolunteerRelay {
    Endpoint ep;
    std::vector<uint8_t> ticket;
    std::vector<uint8_t> cookie;  // the relay's last challenge (sent with each bind)
    std::vector<uint32_t> peers;
    bool bound = false;  // the relay took the ticket
    Clock::time_point next_bind{}, expires{}, last_bound{};
  } vrelay_;
  // The relays the server listed, pinged once per list (the server picks by our pings).
  struct RelayProbe {
    uint8_t index = 0;
    Endpoint ep;
    Clock::time_point sent{};
    uint16_t ms = 0xFFFF;  // no answer yet
    int sent_count = 0;
  };
  std::vector<RelayProbe> probes_;
  uint16_t probe_version_ = 0;
  Clock::time_point probe_report_{};
  bool probe_reported_ = true;
  std::vector<uint8_t> presence_;  // body of our last presence message
  std::vector<uint8_t> match_record_, standby_record_;  // our last MATCH_PUBLISH bodies
  std::vector<forza::LiveFriend> roster_, roster_parts_;
  uint16_t roster_parts_seen_ = 0;
  int roster_changed_ = 0;  // 1: presence changed, 2: players came or went; LiveLoop reports it
};

bool Ours(uint32_t handle) {
  return NetOn() && (handle & 0xFFFF0000) == kHandleBase && VNet::Get().IsOurs(handle);
}

void SetError(uint32_t error) { rex::system::XThread::SetLastError(error); }

}  // namespace

namespace forza {
bool NetworkOn() { return NetOn(); }

bool LiveConnected() { return NetOn() && VNet::Get().LiveConnected(); }

bool LiveServerHttp(uint32_t& ip, uint16_t& port) {
  return NetOn() && VNet::Get().LiveServerHttp(ip, port);
}

std::vector<LiveFriend> LiveRoster() {
  if (!NetOn()) return {};
  return VNet::Get().Roster();
}

void LiveSetPresence(uint32_t state, const uint8_t* session_info, const std::string& status,
                     const std::vector<uint8_t>& extra) {
  if (NetOn()) VNet::Get().SetPresence(state, session_info, status, extra);
}

void LiveSelfXnAddr(uint8_t* xnaddr) { VNet::Get().WriteSelfXnAddr(xnaddr); }

void LiveResumed(double seconds) {
  if (NetOn() && LiveMode()) VNet::Get().Resumed(seconds);
}

void LiveSendInvite(uint64_t xuid, const uint8_t* session_info) {
  if (NetOn()) VNet::Get().SendInvite(xuid, session_info);
}


bool LiveSendMatch(bool search, const std::vector<uint8_t>& body) {
  return NetOn() && VNet::Get().SendMatch(search ? kMatchSearch : kMatchPublish, body);
}

void LiveSendChallenge(const std::vector<uint8_t>& body) {
  if (NetOn()) VNet::Get().SendMatch(kChallenge, body);
}

void LiveSendStats(const std::vector<uint8_t>& body) {
  if (NetOn()) VNet::Get().SendMatch(kStats, body);
}

bool LiveSendFriend(const std::vector<uint8_t>& body) {
  return NetOn() && VNet::Get().SendMatch(kFriend, body);
}

bool LiveSendChat(const std::vector<uint8_t>& body) {
  return NetOn() && VNet::Get().SendMatch(kChat, body);
}
}  // namespace forza

#define FORZA_NET_SDK(name) \
  static GuestFunc sdk = forza::SdkImport("__imp__" #name)

#define FORZA_NET_TRACE(name)                                          \
  extern "C" REX_FUNC(__imp__##name) {                                 \
    FORZA_NET_SDK(name);                                               \
    static std::atomic<uint64_t> calls{0};                             \
    if (!NetTrace()) {                                                 \
      if (sdk) sdk(ctx, base);                                         \
      return;                                                          \
    }                                                                  \
    Traced(#name, sdk, calls, ctx, base);                              \
  }

// Defines an import that the virtual network implements when `cond` holds:
// `body` sets ctx.r3 (and the guest last error); otherwise the SDK runs.
#define FORZA_NET_FUNC(name, cond, body)                               \
  extern "C" REX_FUNC(__imp__##name) {                                 \
    FORZA_NET_SDK(name);                                               \
    static std::atomic<uint64_t> calls{0};                             \
    const uint32_t in[6] = {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32,        \
                            ctx.r6.u32, ctx.r7.u32, ctx.r8.u32};       \
    const uint32_t lr = uint32_t(ctx.lr);                              \
    if (cond) {                                                        \
      body;                                                            \
      Log(#name, calls, in, ctx.r3.u32, " (FORZA_NET)", lr);           \
      return;                                                          \
    }                                                                  \
    if (sdk) sdk(ctx, base);                                           \
    Log(#name, calls, in, ctx.r3.u32, "", lr);                         \
  }

// --- XNet ------------------------------------------------------------------

// DWORD XNetGetEthernetLinkStatus()
FORZA_NET_FUNC(NetDll_XNetGetEthernetLinkStatus, NetOn(), {
  // XNET_ETHERNET_LINK_ACTIVE | XNET_ETHERNET_LINK_100MBPS | XNET_ETHERNET_LINK_FULL_DUPLEX
  ctx.r3.u64 = 0x0B;
})

// DWORD XNetGetTitleXnAddr(XNADDR* pxna)
FORZA_NET_FUNC(NetDll_XNetGetTitleXnAddr, NetOn() && VNet::Get().Ready(), {
  if (ctx.r4.u32) VNet::Get().WriteSelfXnAddr(GuestPtr(base, ctx.r4.u32));
  // XNET_GET_XNADDR_STATIC | XNET_GET_XNADDR_ETHERNET, plus XNET_GET_XNADDR_ONLINE
  // when signed in to Live.
  ctx.r3.u64 = forza::LiveSignin() ? 0x86 : 0x06;
})

// INT XNetXnAddrToInAddr(const XNADDR* pxna, const XNKID* pxnkid, IN_ADDR* pina)
FORZA_NET_FUNC(NetDll_XNetXnAddrToInAddr, NetOn() && VNet::Get().Ready(), {
  Store32(GuestPtr(base, ctx.r6.u32), VNet::Get().VipOfXnAddr(GuestPtr(base, ctx.r4.u32)));
  ctx.r3.u64 = 0;
})

// INT XNetInAddrToXnAddr(IN_ADDR ina, XNADDR* pxna, XNKID* pxnkid)
FORZA_NET_FUNC(NetDll_XNetInAddrToXnAddr, NetOn() && VNet::Get().Ready(), {
  uint8_t xna[36];
  if (VNet::Get().XnAddrOfVip(ctx.r4.u32, xna)) {
    if (ctx.r5.u32) std::memcpy(GuestPtr(base, ctx.r5.u32), xna, sizeof(xna));
    ctx.r3.u64 = 0;
  } else {
    ctx.r3.u64 = kWsaEInval;
  }
})

// INT XNetCreateKey(XNKID* pxnkid, XNKEY* pxnkey)
FORZA_NET_FUNC(NetDll_XNetCreateKey, NetOn(), {
  static std::mt19937_64 rng(std::random_device{}());
  uint8_t* kid = GuestPtr(base, ctx.r4.u32);
  for (int i = 0; i < 8; ++i) kid[i] = uint8_t(rng());
  kid[0] &= 0x0F;  // XNET_XNKID_SYSTEM_LINK
  if (ctx.r5.u32) {
    for (int i = 0; i < 16; ++i) base[ctx.r5.u32 + i] = uint8_t(rng());
  }
  ctx.r3.u64 = 0;
})

FORZA_NET_FUNC(NetDll_XNetRegisterKey, NetOn(), ctx.r3.u64 = 0)
FORZA_NET_FUNC(NetDll_XNetUnregisterKey, NetOn(), ctx.r3.u64 = 0)
FORZA_NET_FUNC(NetDll_XNetUnregisterInAddr, NetOn(), ctx.r3.u64 = 0)
FORZA_NET_FUNC(NetDll_XNetConnect, NetOn(), ctx.r3.u64 = 0)
// XNET_CONNECT_STATUS_CONNECTED
FORZA_NET_FUNC(NetDll_XNetGetConnectStatus, NetOn(), ctx.r3.u64 = 2)
// XNetQosListen / XNetQosLookup: src/kernel/live_xmsg.cpp.

namespace {
// A console's machine id. With an identity (Forza Live) it comes from the player's XUID,
// as XNetLogonGetMachineID does (live_xmsg.cpp), so it stays the same across runs and
// server restarts; the game sends it to the title servers. Otherwise it comes from the
// XNADDR (host port and address).
uint64_t MachineIdOf(const uint8_t* xna) {
  const uint32_t ina = Load32(xna);
  uint64_t xuid = 0;
  uint8_t self[36];
  VNet::Get().WriteSelfXnAddr(self);
  if (std::memcmp(self, xna, 16) == 0) {
    xuid = forza::IdentityXuid();
  } else if ((ina & 0xFF000000) == kLiveInaTag) {
    for (const auto& f : VNet::Get().Roster()) {
      if (f.id == (ina & 0x00FFFFFF)) xuid = f.xuid;
    }
  }
  if (xuid) return 0xFA00000000000000ull | (xuid & 0xFFFFFFFFull);
  return uint64_t(0xFA000000u | Load16(xna + 8)) << 32 | ina;
}
}  // namespace

// INT XNetXnAddrToMachineId(const XNADDR* pxnaddr, ULONGLONG* pqwMachineId)
FORZA_NET_FUNC(NetDll_XNetXnAddrToMachineId, NetOn(), {
  const uint64_t id = MachineIdOf(GuestPtr(base, ctx.r4.u32));
  Store32(GuestPtr(base, ctx.r5.u32), uint32_t(id >> 32));
  Store32(GuestPtr(base, ctx.r5.u32 + 4), uint32_t(id));
  ctx.r3.u64 = 0;
})

FORZA_NET_TRACE(NetDll_XNetStartup)
FORZA_NET_TRACE(NetDll_XNetCleanup)
// INT XNetRandom(BYTE* pb, UINT cb): the SDK fills 0xBB, which gives every
// instance the same session nonces, so each takes the others' broadcast
// searches for its own.
FORZA_NET_FUNC(NetDll_XNetRandom, NetOn(), {
  static std::mutex rng_mutex;
  static std::mt19937_64 rng(std::random_device{}());
  std::lock_guard<std::mutex> lock(rng_mutex);
  for (uint32_t i = 0; i < ctx.r5.u32; ++i) base[ctx.r4.u32 + i] = uint8_t(rng() >> 24);
  if (NetTrace()) REXLOG_INFO("NETTRACE XNetRandom({}) from{}", ctx.r5.u32, GuestBacktrace(ctx, base, 12));
  ctx.r3.u64 = 0;
})
// INT XNetServerToInAddr(IN_ADDR ina, DWORD dwServiceId, IN_ADDR* pina): title server
// addresses (from the title server enumeration) are plain IPs here.
FORZA_NET_FUNC(NetDll_XNetServerToInAddr, forza::LiveSignin() && ctx.r6.u32, {
  Store32(GuestPtr(base, ctx.r6.u32), ctx.r4.u32);
  ctx.r3.u64 = 0;
})
FORZA_NET_TRACE(NetDll_XNetQosServiceLookup)
FORZA_NET_TRACE(NetDll_XNetQosRelease)
FORZA_NET_TRACE(NetDll_XNetQosGetListenStats)
// XNetLogonGetMachineID / XNetLogonGetTitleID: src/kernel/live_xmsg.cpp.

// --- Sockets ---------------------------------------------------------------

// --- TCP sockets (live_tcp.cpp): the title servers' HTTP connections ---------
namespace {
bool Tcp(uint32_t handle) { return NetOn() && forza::tcp::IsOurs(handle); }
uint64_t TcpResult(int32_t result) { return uint64_t(int64_t(result)); }
}  // namespace

// SOCKET socket(int af, int type, int protocol): UDP and VDP datagram sockets are ours;
// stream sockets go to live_tcp.cpp.
FORZA_NET_FUNC(NetDll_socket, NetOn() && ((ctx.r5.u32 == 2 && VNet::Get().Ready()) || ctx.r5.u32 == 1), {
  ctx.r3.u64 = ctx.r5.u32 == 1 ? forza::tcp::Socket() : VNet::Get().CreateSocket();
})

// int bind(SOCKET s, const sockaddr* name, int namelen)
FORZA_NET_FUNC(NetDll_bind, Ours(ctx.r4.u32), {
  uint32_t error = VNet::Get().Bind(ctx.r4.u32, Load16(GuestPtr(base, ctx.r5.u32 + 2)));
  if (error) SetError(error);
  ctx.r3.u64 = error ? uint64_t(-1) : 0;
})

// int connect(SOCKET s, const sockaddr* name, int namelen)
// Datagram sockets are ours. A TCP connection to the Forza Live server (the title
// servers, which XNetServerToInAddr resolves to it) goes to its HTTP port, whatever
// port the game asked for.
extern "C" REX_FUNC(__imp__NetDll_connect) {
  FORZA_NET_SDK(NetDll_connect);
  static std::atomic<uint64_t> calls{0};
  const uint32_t in[6] = {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, ctx.r8.u32};
  const uint32_t lr = uint32_t(ctx.lr);
  uint8_t* a = GuestPtr(base, ctx.r5.u32);
  if (Tcp(ctx.r4.u32)) {
    ctx.r3.u64 = TcpResult(forza::tcp::Connect(ctx.r4.u32, Load32(a + 4), Load16(a + 2)));
    Log("NetDll_connect", calls, in, ctx.r3.u32, " (TCP)", lr);
    return;
  }
  if (Ours(ctx.r4.u32)) {
    VNet::Get().Connect(ctx.r4.u32, Load32(a + 4), Load16(a + 2));
    ctx.r3.u64 = 0;
    Log("NetDll_connect", calls, in, ctx.r3.u32, " (FORZA_NET)", lr);
    return;
  }
  uint32_t server_ip;
  uint16_t http_port;
  const uint16_t port = Load16(a + 2);
  const bool to_server = NetOn() && ctx.r5.u32 && VNet::Get().LiveServerHttp(server_ip, http_port) &&
                         Load32(a + 4) == server_ip;
  if (to_server) {
    REXLOG_INFO("FORZA_LIVE: title server connection (port {}) goes to {}:{}", port,
                IpString(server_ip), http_port);
    Store16(a + 2, http_port);
  }
  if (sdk) sdk(ctx, base);
  if (to_server) Store16(a + 2, port);
  Log("NetDll_connect", calls, in, ctx.r3.u32, to_server ? " (title server)" : "", lr);
}

// int closesocket(SOCKET s)
FORZA_NET_FUNC(NetDll_closesocket, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32), {
  if (Tcp(ctx.r4.u32)) {
    ctx.r3.u64 = TcpResult(forza::tcp::Close(ctx.r4.u32));
  } else {
    VNet::Get().Close(ctx.r4.u32);
    ctx.r3.u64 = 0;
  }
})

namespace {
uint32_t OptionKey(uint32_t level, uint32_t name) { return level << 16 | (name & 0xFFFF); }

// int setsockopt(SOCKET s, int level, int optname, const char* optval, int optlen):
// accepted; a datagram socket remembers the value for getsockopt.
uint64_t SetSockOpt(PPCContext& ctx, uint8_t* base) {
  const uint32_t optval = ctx.r7.u32, optlen = ctx.r8.u32;
  if (Ours(ctx.r4.u32) && optval && optlen) {
    const uint8_t* v = GuestPtr(base, optval);
    VNet::Get().SetOption(ctx.r4.u32, OptionKey(ctx.r5.u32, ctx.r6.u32),
                          optlen >= 4 ? Load32(v) : v[0]);
  }
  return 0;
}

// int getsockopt(SOCKET s, int level, int optname, char* optval, int* optlen). Forza
// probes SO_RCVBUF on a scratch socket before it opens its session socket.
uint64_t GetSockOpt(PPCContext& ctx, uint8_t* base) {
  constexpr uint32_t kSolSocket = 0xFFFF, kSoSndBuf = 0x1001, kSoRcvBuf = 0x1002,
                     kSoType = 0x1008;
  const uint32_t level = ctx.r5.u32, name = ctx.r6.u32, optval = ctx.r7.u32, optlen = ctx.r8.u32;
  uint32_t value = 0;
  if (!VNet::Get().Option(ctx.r4.u32, OptionKey(level, name), value) && level == kSolSocket) {
    if (name == kSoSndBuf || name == kSoRcvBuf) value = 0x10000;
    if (name == kSoType) value = 2;  // SOCK_DGRAM
  }
  if (!optval || !optlen || Load32(GuestPtr(base, optlen)) < 4) {
    SetError(kWsaEInval);
    return uint64_t(-1);
  }
  Store32(GuestPtr(base, optval), value);
  Store32(GuestPtr(base, optlen), 4);
  return 0;
}

// int getsockname(SOCKET s, sockaddr* name, int* namelen): the bound port, any address
// (as Winsock reports a socket bound to INADDR_ANY). Forza reads its session socket's port.
uint64_t GetSockName(PPCContext& ctx, uint8_t* base) {
  uint16_t port = 0;
  VNet::Get().LocalPort(ctx.r4.u32, port);
  if (!port || !ctx.r5.u32) {
    SetError(kWsaEInval);
    return uint64_t(-1);
  }
  WriteSockaddr(GuestPtr(base, ctx.r5.u32), 0, port);
  if (ctx.r6.u32) Store32(GuestPtr(base, ctx.r6.u32), 16);
  return 0;
}

// int getpeername(SOCKET s, sockaddr* name, int* namelen)
uint64_t GetPeerName(PPCContext& ctx, uint8_t* base) {
  uint32_t vip = 0;
  uint16_t port = 0;
  if (!VNet::Get().Connected(ctx.r4.u32, vip, port) || !ctx.r5.u32) {
    SetError(kWsaENotConn);
    return uint64_t(-1);
  }
  WriteSockaddr(GuestPtr(base, ctx.r5.u32), vip, port);
  if (ctx.r6.u32) Store32(GuestPtr(base, ctx.r6.u32), 16);
  return 0;
}
}  // namespace

FORZA_NET_FUNC(NetDll_setsockopt, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32),
               ctx.r3.u64 = SetSockOpt(ctx, base))
FORZA_NET_FUNC(NetDll_getsockopt, Ours(ctx.r4.u32), ctx.r3.u64 = GetSockOpt(ctx, base))
FORZA_NET_FUNC(NetDll_getsockname, Ours(ctx.r4.u32), ctx.r3.u64 = GetSockName(ctx, base))
FORZA_NET_FUNC(NetDll_getpeername, Ours(ctx.r4.u32), ctx.r3.u64 = GetPeerName(ctx, base))
FORZA_NET_FUNC(NetDll_shutdown, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32), {
  ctx.r3.u64 = Tcp(ctx.r4.u32) ? TcpResult(forza::tcp::Shutdown(ctx.r4.u32, ctx.r5.u32)) : 0;
})

// int ioctlsocket(SOCKET s, long cmd, u_long* argp)
FORZA_NET_FUNC(NetDll_ioctlsocket, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32), {
  const uint32_t cmd = ctx.r5.u32;
  uint8_t* arg = GuestPtr(base, ctx.r6.u32);
  if (Tcp(ctx.r4.u32)) {
    ctx.r3.u64 = TcpResult(forza::tcp::Ioctl(ctx.r4.u32, cmd, arg));
  } else if (cmd == 0x8004667E) {  // FIONBIO
    VNet::Get().SetNonblocking(ctx.r4.u32, Load32(arg) != 0);
    ctx.r3.u64 = 0;
  } else if (cmd == 0x4004667F) {  // FIONREAD
    Store32(arg, VNet::Get().PendingBytes(ctx.r4.u32));
    ctx.r3.u64 = 0;
  } else {
    SetError(kWsaEInval);
    ctx.r3.u64 = uint64_t(-1);
  }
})

// int sendto(SOCKET s, const char* buf, int len, int flags, const sockaddr* to, int tolen)
FORZA_NET_FUNC(NetDll_sendto, Ours(ctx.r4.u32), {
  uint32_t vip = 0;
  uint16_t port = 0;
  if (ctx.r8.u32) {
    vip = Load32(GuestPtr(base, ctx.r8.u32 + 4));
    port = Load16(GuestPtr(base, ctx.r8.u32 + 2));
  } else {
    VNet::Get().Connected(ctx.r4.u32, vip, port);
  }
  uint32_t error = VNet::Get().SendTo(ctx.r4.u32, vip, port, GuestPtr(base, ctx.r5.u32), ctx.r6.u32);
  if (error) SetError(error);
  ctx.r3.u64 = error ? uint64_t(-1) : ctx.r6.u32;
})

// int send(SOCKET s, const char* buf, int len, int flags)
FORZA_NET_FUNC(NetDll_send, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32), {
  if (Tcp(ctx.r4.u32)) {
    ctx.r3.u64 = TcpResult(forza::tcp::Send(ctx.r4.u32, GuestPtr(base, ctx.r5.u32), ctx.r6.u32));
    return;
  }
  uint32_t vip = 0;
  uint16_t port = 0;
  VNet::Get().Connected(ctx.r4.u32, vip, port);
  uint32_t error = VNet::Get().SendTo(ctx.r4.u32, vip, port, GuestPtr(base, ctx.r5.u32), ctx.r6.u32);
  if (error) SetError(error);
  ctx.r3.u64 = error ? uint64_t(-1) : ctx.r6.u32;
})

namespace {
// recvfrom/recv: copies one datagram into the guest buffer.
uint64_t Receive(PPCContext& ctx, uint8_t* base, uint32_t from, uint32_t from_len) {
  Datagram d;
  uint32_t error = VNet::Get().ReceiveFrom(ctx.r4.u32, d);
  if (error) {
    SetError(error);
    return uint64_t(-1);
  }
  size_t n = std::min<size_t>(d.data.size(), ctx.r6.u32);
  std::memcpy(GuestPtr(base, ctx.r5.u32), d.data.data(), n);
  if (from) {
    uint8_t* a = GuestPtr(base, from);
    std::memset(a, 0, 16);
    Store16(a, 2);  // AF_INET
    Store16(a + 2, d.port);
    Store32(a + 4, d.vip);
    if (from_len) Store32(GuestPtr(base, from_len), 16);
  }
  if (n < d.data.size()) {
    SetError(kWsaEMsgSize);
    return uint64_t(-1);
  }
  return n;
}
}  // namespace

// int recvfrom(SOCKET s, char* buf, int len, int flags, sockaddr* from, int* fromlen)
FORZA_NET_FUNC(NetDll_recvfrom, Ours(ctx.r4.u32), {
  ctx.r3.u64 = Receive(ctx, base, ctx.r8.u32, ctx.r9.u32);
})

// int recv(SOCKET s, char* buf, int len, int flags)
FORZA_NET_FUNC(NetDll_recv, Ours(ctx.r4.u32) || Tcp(ctx.r4.u32), {
  ctx.r3.u64 = Tcp(ctx.r4.u32)
                   ? TcpResult(forza::tcp::Recv(ctx.r4.u32, GuestPtr(base, ctx.r5.u32), ctx.r6.u32))
                   : Receive(ctx, base, 0, 0);
})

// --- Overlapped I/O ----------------------------------------------------------
//
// Forza's session networking (0x8313xxxx-0x8315xxxx) keeps an overlapped WSARecvFrom
// posted on its VDP socket, waits for the OVERLAPPED's event and collects the datagram
// with WSAGetOverlappedResult; it sends with WSASendTo. The SDK has none of this for
// sockets (WSARecvFrom always fails), so the session socket was closed as soon as it
// was opened and the game crashed entering Multiplayer.

namespace {
// The n-th argument after the eighth: the caller's parameter area, r1 + 84 + 8n.
uint32_t StackArg(PPCContext& ctx, uint8_t* base, int n) {
  return Load32(GuestPtr(base, ctx.r1.u32 + 84 + 8 * n));
}

void WarnCompletionRoutine(uint32_t routine) {
  static std::atomic<bool> warned{false};
  if (routine && !warned.exchange(true)) {
    REXLOG_WARN("FORZA_NET: socket I/O completion routines are not supported ({:08X})", routine);
  }
}

// WSARecvFrom/WSARecv on a datagram socket: r4 socket, r5 WSABUF*, r6 count, r7 received*,
// r8 flags*.
uint64_t RecvBuffers(PPCContext& ctx, uint8_t* base, uint32_t from, uint32_t from_len,
                     uint32_t overlapped, uint32_t routine) {
  const uint32_t handle = ctx.r4.u32, buffers = ctx.r5.u32, count = ctx.r6.u32;
  const uint32_t received = ctx.r7.u32, flags = ctx.r8.u32;
  WarnCompletionRoutine(routine);
  if (flags) Store32(GuestPtr(base, flags), 0);
  uint32_t bytes = 0, error = 0;
  if (overlapped) {
    VNet::Get().SetBase(base);
    PendingRecv r;
    r.buffers = buffers;
    r.count = count;
    r.from = from;
    r.from_len = from_len;
    r.overlapped = overlapped;
    error = VNet::Get().RecvOverlapped(handle, r, bytes);
  } else {
    Datagram d;
    error = VNet::Get().ReceiveFrom(handle, d);
    if (!error) {
      bytes = Scatter(base, buffers, count, d.data);
      if (from) WriteSockaddr(GuestPtr(base, from), d.vip, d.port);
      if (from_len) Store32(GuestPtr(base, from_len), 16);
      if (bytes < d.data.size()) error = kWsaEMsgSize;
    }
  }
  if (received && (!error || error == kWsaEMsgSize)) Store32(GuestPtr(base, received), bytes);
  if (error) {
    SetError(error);  // kWsaIoPending: the receive completes later
    return uint64_t(-1);
  }
  return 0;
}

// WSASendTo/WSASend on a datagram socket: r4 socket, r5 WSABUF*, r6 count, r7 sent*.
// A send completes at once, overlapped or not.
uint64_t SendBuffers(PPCContext& ctx, uint8_t* base, uint32_t vip, uint16_t port,
                     uint32_t overlapped, uint32_t routine) {
  WarnCompletionRoutine(routine);
  const std::vector<uint8_t> data = Gather(base, ctx.r5.u32, ctx.r6.u32);
  const uint32_t error = VNet::Get().SendTo(ctx.r4.u32, vip, port, data.data(), data.size());
  if (error) {
    SetError(error);
    return uint64_t(-1);
  }
  const uint32_t size = uint32_t(data.size());
  if (ctx.r7.u32) Store32(GuestPtr(base, ctx.r7.u32), size);
  if (overlapped) {
    uint8_t* ov = GuestPtr(base, overlapped);
    Store32(ov + 4, size);
    Store32(ov, 0);
    SignalEvent(Load32(ov + 16));
  }
  return 0;
}

uint64_t WsaSendTo(PPCContext& ctx, uint8_t* base) {
  uint32_t vip = 0;
  uint16_t port = 0;
  if (ctx.r9.u32) {
    vip = Load32(GuestPtr(base, ctx.r9.u32 + 4));
    port = Load16(GuestPtr(base, ctx.r9.u32 + 2));
  } else {
    VNet::Get().Connected(ctx.r4.u32, vip, port);
  }
  return SendBuffers(ctx, base, vip, port, StackArg(ctx, base, 0), StackArg(ctx, base, 1));
}

uint64_t WsaSend(PPCContext& ctx, uint8_t* base) {
  uint32_t vip = 0;
  uint16_t port = 0;
  VNet::Get().Connected(ctx.r4.u32, vip, port);
  return SendBuffers(ctx, base, vip, port, ctx.r9.u32, ctx.r10.u32);
}

// BOOL WSAGetOverlappedResult(SOCKET s, WSAOVERLAPPED* overlapped, DWORD* transferred,
//                             BOOL wait, DWORD* flags)
uint64_t WsaGetOverlappedResult(PPCContext& ctx, uint8_t* base) {
  const uint8_t* ov = GuestPtr(base, ctx.r5.u32);
  uint32_t status = Load32(ov);
  while (status == kStatusPending && ctx.r7.u32 && Ours(ctx.r4.u32)) {
    VNet::Get().WaitOverlapped(ov);
    status = Load32(ov);
  }
  std::atomic_thread_fence(std::memory_order_acquire);
  if (ctx.r6.u32) Store32(GuestPtr(base, ctx.r6.u32), Load32(ov + 4));
  if (ctx.r8.u32) Store32(GuestPtr(base, ctx.r8.u32), 0);
  uint32_t error = kWsaEInval;
  switch (status) {
    case 0: error = 0; break;
    case kStatusPending: error = kWsaIoIncomplete; break;
    case kStatusCancelled: error = kWsaOperationAborted; break;
    case kStatusBufferOverflow: error = kWsaEMsgSize; break;
  }
  if (error) SetError(error);
  return error ? 0 : 1;
}

uint64_t WsaEventSelect(PPCContext& ctx) {
  const uint32_t error = VNet::Get().EventSelect(ctx.r4.u32, ctx.r5.u32, ctx.r6.u32);
  if (error) SetError(error);
  return error ? uint64_t(-1) : 0;
}
}  // namespace

// int WSARecvFrom(SOCKET s, WSABUF* buffers, DWORD count, DWORD* received, DWORD* flags,
//                 sockaddr* from, int* fromlen, WSAOVERLAPPED* overlapped, routine)
FORZA_NET_FUNC(NetDll_WSARecvFrom, Ours(ctx.r4.u32),
               ctx.r3.u64 = RecvBuffers(ctx, base, ctx.r9.u32, ctx.r10.u32,
                                        StackArg(ctx, base, 0), StackArg(ctx, base, 1)))
// int WSARecv(SOCKET s, WSABUF* buffers, DWORD count, DWORD* received, DWORD* flags,
//             WSAOVERLAPPED* overlapped, routine)
FORZA_NET_FUNC(NetDll_WSARecv, Ours(ctx.r4.u32),
               ctx.r3.u64 = RecvBuffers(ctx, base, 0, 0, ctx.r9.u32, ctx.r10.u32))
// int WSASendTo(SOCKET s, WSABUF* buffers, DWORD count, DWORD* sent, DWORD flags,
//               const sockaddr* to, int tolen, WSAOVERLAPPED* overlapped, routine)
FORZA_NET_FUNC(NetDll_WSASendTo, Ours(ctx.r4.u32), ctx.r3.u64 = WsaSendTo(ctx, base))
// int WSASend(SOCKET s, WSABUF* buffers, DWORD count, DWORD* sent, DWORD flags,
//             WSAOVERLAPPED* overlapped, routine)
FORZA_NET_FUNC(NetDll_WSASend, Ours(ctx.r4.u32), ctx.r3.u64 = WsaSend(ctx, base))
FORZA_NET_FUNC(NetDll_WSAGetOverlappedResult, Ours(ctx.r4.u32) && ctx.r5.u32,
               ctx.r3.u64 = WsaGetOverlappedResult(ctx, base))
// BOOL WSACancelOverlappedIO(SOCKET s)
FORZA_NET_FUNC(NetDll_WSACancelOverlappedIO, Ours(ctx.r4.u32), {
  VNet::Get().CancelRecvs(ctx.r4.u32);
  ctx.r3.u64 = 0;
})
// int WSAEventSelect(SOCKET s, WSAEVENT event, long network_events)
FORZA_NET_FUNC(NetDll_WSAEventSelect, Ours(ctx.r4.u32), ctx.r3.u64 = WsaEventSelect(ctx))

namespace {
// Guest fd_set: u32 fd_count, SOCKET fd_array[64].
std::vector<uint32_t> ReadSet(uint8_t* base, uint32_t set) {
  std::vector<uint32_t> handles;
  if (!set) return handles;
  uint32_t count = std::min<uint32_t>(Load32(GuestPtr(base, set)), 64);
  for (uint32_t i = 0; i < count; ++i) handles.push_back(Load32(GuestPtr(base, set + 4 + 4 * i)));
  return handles;
}

void WriteSet(uint8_t* base, uint32_t set, const std::vector<uint32_t>& handles) {
  if (!set) return;
  Store32(GuestPtr(base, set), uint32_t(handles.size()));
  for (size_t i = 0; i < handles.size(); ++i) Store32(GuestPtr(base, set + 4 + 4 * i), handles[i]);
}

bool AllOurs(uint8_t* base, uint32_t set) {
  for (uint32_t h : ReadSet(base, set)) {
    if (!Ours(h)) return false;
  }
  return true;
}
}  // namespace

// int select(int nfds, fd_set* readfds, fd_set* writefds, fd_set* exceptfds, const timeval* timeout)
FORZA_NET_FUNC(NetDll_select,
               NetOn() && (forza::tcp::AllOurs(base, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32) ||
                           (AllOurs(base, ctx.r5.u32) && AllOurs(base, ctx.r6.u32) &&
                            AllOurs(base, ctx.r7.u32) &&
                            !(ReadSet(base, ctx.r5.u32).empty() &&
                              ReadSet(base, ctx.r6.u32).empty()))),
               {
                 if (forza::tcp::AllOurs(base, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32)) {
                   ctx.r3.u64 = TcpResult(forza::tcp::Select(base, ctx.r5.u32, ctx.r6.u32,
                                                             ctx.r7.u32, ctx.r8.u32));
                   return;
                 }
                 int64_t timeout_us = -1;
                 if (ctx.r8.u32) {
                   timeout_us = int64_t(int32_t(Load32(GuestPtr(base, ctx.r8.u32)))) * 1000000 +
                                int32_t(Load32(GuestPtr(base, ctx.r8.u32 + 4)));
                 }
                 std::vector<uint32_t> writable = ReadSet(base, ctx.r6.u32);  // always writable
                 if (!writable.empty()) timeout_us = 0;
                 std::vector<uint32_t> readable =
                     VNet::Get().WaitReadable(ReadSet(base, ctx.r5.u32), timeout_us);
                 WriteSet(base, ctx.r5.u32, readable);
                 WriteSet(base, ctx.r6.u32, writable);
                 WriteSet(base, ctx.r7.u32, {});
                 ctx.r3.u64 = readable.size() + writable.size();
               })

FORZA_NET_TRACE(NetDll_WSAStartup)
FORZA_NET_TRACE(NetDll_WSACleanup)
FORZA_NET_TRACE(NetDll_WSAGetLastError)
FORZA_NET_TRACE(NetDll___WSAFDIsSet)
FORZA_NET_TRACE(NetDll_listen)
FORZA_NET_TRACE(NetDll_accept)
FORZA_NET_TRACE(NetDll_inet_addr)

// --- XAM sessions, voice and messages (trace only) -------------------------

FORZA_NET_TRACE(XamShowSigninUI)
FORZA_NET_TRACE(XamShowFriendRequestUI)
FORZA_NET_TRACE(XamShowGamerCardUIForXUID)
FORZA_NET_TRACE(XamSessionCreateHandle)
FORZA_NET_TRACE(XamSessionRefObjByHandle)
FORZA_NET_TRACE(XamVoiceCreate)
FORZA_NET_TRACE(XamVoiceClose)
FORZA_NET_TRACE(XamVoiceSubmitPacket)
FORZA_NET_TRACE(XamVoiceHeadsetPresent)
// XMsgStartIORequest[Ex] and XMsgInProcessCall: src/kernel/live_xmsg.cpp.
FORZA_NET_TRACE(XMsgCancelIORequest)
FORZA_NET_TRACE(XMsgCompleteIORequest)
