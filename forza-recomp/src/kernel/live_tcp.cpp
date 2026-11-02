// forza - the game's TCP sockets, with Winsock behaviour.
//
// Reach talks HTTP/1.0 to its title servers over nonblocking TCP: socket, ioctlsocket
// (FIONBIO), connect (expects WSAEWOULDBLOCK), select for writability, send, recv,
// closesocket (the transport code around sub_822A4C58). The SDK's host sockets pass
// the Windows ioctl code to Linux and report no Winsock errors, so every connection
// failed before connect. With the virtual network on (FORZA_NET / FORZA_SERVER),
// net.cpp hands stream sockets to this file instead: host TCP sockets, Winsock error
// codes, and connections to the Forza Live server's address sent to its HTTP port
// (forza::LiveServerHttp), whatever LSP port the game picked. Requests on those connections
// are signed with our Live identity's friends key (lsp_request_header.h, live_auth.cpp; the key
// itself is never sent), which tells the server the call is this player's own, and name the
// game's build (X-Forza-Build), which the server keeps with each leaderboard row (#517).

#include "live_tcp.h"

#include "../platform/guest_memory.h"
#include "live.h"
#include "live_auth.h"
#include "lsp_request_header.h"

#include "../platform/socket.h"

#include <rex/logging.h>
#include <rex/system/xthread.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

using forza::GuestPtr;

namespace forza::tcp {

namespace {

constexpr uint32_t kHandleBase = 0x52540000;  // 'RT' + index
constexpr uint32_t kFionbio = 0x8004667E;
constexpr uint32_t kFionread = 0x4004667F;

using sock::kEInval;
using sock::kENotConn;
using sock::kENotSock;

int32_t Fail(uint32_t error) {
  rex::system::XThread::SetLastError(error);
  return -1;
}

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}

std::mutex mutex;
std::unordered_map<uint32_t, sock::Handle> fds;  // guest handle -> host socket
// Connections to the title servers: each request is signed, and what the socket has not taken
// yet waits in the stream (lsp_request_header.h LspTitleStream).
struct Title {
  Title() : stream(forza::live_auth::RequestLines) {}
  std::mutex mutex;
  LspTitleStream stream;
};
std::unordered_map<uint32_t, std::shared_ptr<Title>> titles;
uint32_t next_handle = 1;

std::shared_ptr<Title> TitleOf(uint32_t handle) {
  std::lock_guard<std::mutex> lock(mutex);
  auto it = titles.find(handle);
  return it == titles.end() ? nullptr : it->second;
}

// The socket write a title stream uses: bytes written, or -1 (would_block when it is full;
// the error stays in the socket's last error otherwise).
struct SocketSend {
  sock::Handle fd;
  uint32_t* error;
  int64_t operator()(const char* data, size_t size, bool& would_block) const {
    const int64_t n = sock::Send(fd, data, size);
    if (n < 0) {
      *error = sock::LastError();
      would_block = *error == sock::kEWouldBlock;
    }
    return n;
  }
};

// Moves a title stream's waiting output on (nothing waits). True while some of it still
// waits for room in the socket (false when none is left, or the socket failed: the game's
// next send or recv gets the error).
bool TitleWaiting(uint32_t handle, sock::Handle fd) {
  const auto title = TitleOf(handle);
  if (!title) return false;
  std::lock_guard<std::mutex> lock(title->mutex);
  uint32_t error = 0;
  return title->stream.Flush(SocketSend{fd, &error}) == LspTitleStream::kWouldBlock;
}

sock::Handle Fd(uint32_t handle) {
  std::lock_guard<std::mutex> lock(mutex);
  auto it = fds.find(handle);
  return it == fds.end() ? sock::kInvalid : it->second;
}

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

}  // namespace

bool IsOurs(uint32_t handle) {
  return (handle & 0xFFFF0000) == kHandleBase && Fd(handle) != sock::kInvalid;
}

uint32_t Socket() {
  sock::Startup();
  sock::Handle fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd == sock::kInvalid) return uint32_t(Fail(sock::LastError()));
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&one), sizeof(one));
  std::lock_guard<std::mutex> lock(mutex);
  uint32_t handle = kHandleBase + (next_handle++ & 0xFFFF);
  fds[handle] = fd;
  return handle;
}

int32_t Connect(uint32_t handle, uint32_t ip, uint16_t port) {
  sock::Handle fd = Fd(handle);
  if (fd == sock::kInvalid) return Fail(kENotSock);
  uint32_t server_ip;
  uint16_t http_port;
  if (forza::LiveServerHttp(server_ip, http_port) && ip == server_ip) {
    static std::atomic<int> logged{0};
    if (logged.fetch_add(1) < 10) {
      REXLOG_INFO("FORZA_LIVE: title server connection (LSP port {}) goes to HTTP port {}", port,
                  http_port);
    }
    port = http_port;
    if (forza::live_auth::Ready()) {
      std::lock_guard<std::mutex> lock(mutex);
      titles[handle] = std::make_shared<Title>();
    }
  }
  sockaddr_in a{};
  a.sin_family = AF_INET;
  a.sin_port = htons(port);
  a.sin_addr.s_addr = htonl(ip);
  if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0) return 0;
  return Fail(sock::LastError());
}

int32_t Ioctl(uint32_t handle, uint32_t cmd, uint8_t* arg) {
  sock::Handle fd = Fd(handle);
  if (fd == sock::kInvalid) return Fail(kENotSock);
  if (cmd == kFionbio) {
    if (!sock::SetNonblocking(fd, Load32(arg) != 0)) return Fail(sock::LastError());
    return 0;
  }
  if (cmd == kFionread) {
    uint32_t available = 0;
    if (!sock::BytesAvailable(fd, available)) return Fail(sock::LastError());
    Store32(arg, available);
    return 0;
  }
  return Fail(kEInval);
}

int32_t Send(uint32_t handle, const uint8_t* data, uint32_t size) {
  sock::Handle fd = Fd(handle);
  if (fd == sock::kInvalid) return Fail(kENotSock);
  if (const auto title = TitleOf(handle)) {
    // The request stream with the signature, nonblocking: the game's bytes are taken once
    // older output has gone out (else WSAEWOULDBLOCK, nothing taken), and what the socket
    // does not take now waits for the next send, recv or select.
    std::lock_guard<std::mutex> lock(title->mutex);
    uint32_t error = 0;
    const int64_t n = title->stream.Send(data, size, SocketSend{fd, &error});
    if (n == LspTitleStream::kWouldBlock) return Fail(sock::kEWouldBlock);
    if (n == LspTitleStream::kError) return Fail(error ? error : kENotConn);
    return int32_t(n);
  }
  const int64_t n = sock::Send(fd, data, size);
  return n < 0 ? Fail(sock::LastError()) : int32_t(n);
}

int32_t Recv(uint32_t handle, uint8_t* data, uint32_t size) {
  sock::Handle fd = Fd(handle);
  if (fd == sock::kInvalid) return Fail(kENotSock);
  TitleWaiting(handle, fd);  // the rest of a request the answer waits for
  const int64_t n = sock::Recv(fd, data, size);
  return n < 0 ? Fail(sock::LastError()) : int32_t(n);
}

int32_t Shutdown(uint32_t handle, uint32_t how) {
  sock::Handle fd = Fd(handle);
  if (fd == sock::kInvalid) return Fail(kENotSock);
  // SD_RECEIVE 0, SD_SEND 1, SD_BOTH 2 match SHUT_RD / SHUT_WR / SHUT_RDWR.
  if (shutdown(fd, int(how)) != 0) {
    const uint32_t error = sock::LastError();
    if (error != kENotConn) return Fail(error);
  }
  return 0;
}

int32_t Close(uint32_t handle) {
  sock::Handle fd;
  {
    std::lock_guard<std::mutex> lock(mutex);
    auto it = fds.find(handle);
    if (it == fds.end()) return Fail(kENotSock);
    fd = it->second;
    fds.erase(it);
    titles.erase(handle);
  }
  sock::Close(fd);
  return 0;
}

bool AllOurs(uint8_t* base, uint32_t readfds, uint32_t writefds, uint32_t exceptfds) {
  size_t total = 0;
  for (uint32_t set : {readfds, writefds, exceptfds}) {
    for (uint32_t h : ReadSet(base, set)) {
      if (!IsOurs(h)) return false;
      ++total;
    }
  }
  return total != 0;
}

int32_t Select(uint8_t* base, uint32_t readfds, uint32_t writefds, uint32_t exceptfds,
               uint32_t timeout) {
  const std::vector<uint32_t> sets[3] = {ReadSet(base, readfds), ReadSet(base, writefds),
                                         ReadSet(base, exceptfds)};
  std::vector<sock::PollFd> polls;
  std::vector<std::pair<int, uint32_t>> owners;  // which set and handle each pollfd is for
  for (int s = 0; s < 3; ++s) {
    for (uint32_t h : sets[s]) {
      sock::Handle fd = Fd(h);
      if (fd == sock::kInvalid) return Fail(kENotSock);
      const short events = s == 0 ? POLLIN : s == 1 ? POLLOUT : sock::kPollExcept;
      polls.push_back({fd, events, 0});
      owners.push_back({s, h});
    }
  }
  int wait_ms = -1;
  if (timeout) {
    int64_t us = int64_t(int32_t(Load32(GuestPtr(base, timeout)))) * 1000000 +
                 int32_t(Load32(GuestPtr(base, timeout + 4)));
    wait_ms = int(std::max<int64_t>(0, (us + 999) / 1000));
  }
  // A title stream with output waiting (a request the socket has not taken all of) is not
  // writable for the game, and its output has to move on while the game waits, also when it
  // only waits for the answer: then the wait goes in short slices, each followed by a flush.
  std::vector<short> events;
  for (const auto& p : polls) events.push_back(p.events);
  const auto start = std::chrono::steady_clock::now();
  std::vector<uint32_t> ready[3];
  for (;;) {
    bool waiting = false;
    for (size_t i = 0; i < polls.size(); ++i) {
      polls[i].revents = 0;
      polls[i].events = events[i];
      if (TitleWaiting(owners[i].second, polls[i].fd)) {
        waiting = true;
        polls[i].events |= POLLOUT;
      }
    }
    int left = -1;
    if (wait_ms >= 0) {
      const int64_t elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                                  std::chrono::steady_clock::now() - start)
                                  .count();
      left = int(std::max<int64_t>(0, wait_ms - elapsed));
    }
    const int slice = waiting ? (left < 0 ? 10 : std::min(10, left)) : left;
    const int n = sock::Poll(polls.data(), polls.size(), slice);
    if (n < 0) return Fail(sock::LastError());
    for (auto& r : ready) r.clear();
    for (size_t i = 0; i < polls.size(); ++i) {
      const short r = polls[i].revents;
      const int s = owners[i].first;
      // writable for the game only once its waiting output is gone
      const bool still = (r & POLLOUT) && TitleWaiting(owners[i].second, polls[i].fd);
      bool hit = false;
      if (s == 0) hit = r & (POLLIN | POLLHUP | POLLERR);
      // A failed nonblocking connect shows in the except set on Winsock, not as writable.
      if (s == 1) hit = (r & POLLOUT) && !(r & POLLERR) && !still;
      if (s == 2) hit = r & (sock::kPollExcept | POLLERR);
      if (hit) ready[s].push_back(owners[i].second);
    }
    const bool any = !ready[0].empty() || !ready[1].empty() || !ready[2].empty();
    if (any || !waiting || left == 0) break;
  }
  WriteSet(base, readfds, ready[0]);
  WriteSet(base, writefds, ready[1]);
  WriteSet(base, exceptfds, ready[2]);
  return int32_t(ready[0].size() + ready[1].size() + ready[2].size());
}

}  // namespace forza::tcp
