// Standalone: c++ -std=c++17 tools/test_lsp_request_header.cpp -o test_lsp_request_header
// The header lines added to the game's title server requests once each is complete, and the
// nonblocking stream that sends them (forza-recomp/src/kernel/lsp_request_header.h).
#include "../forza-recomp/src/kernel/lsp_request_header.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>

#ifndef _WIN32
#include <cerrno>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)

namespace {
const std::string kLine = "X-Forza-Auth: FORZA-ED25519-V1 Signature=00\r\n";

// The lines for a request: kLine, unless the request already has the header.
std::string Lines(const std::string& head, const std::string&) {
  return forza::LspRequestHeader::Has(head, "x-forza-auth") ? std::string() : kLine;
}

std::string Feed(forza::LspRequestHeader& h, const std::string& s) {
  return h.Feed(reinterpret_cast<const uint8_t*>(s.data()), s.size());
}

std::string Request(const std::string& body, const std::string& extra = "") {
  return "POST /Services.Horizon/o.xtsw HTTP/1.1\r\nHost: forza\r\nContent-Type: bin/xtsw\r\n"
         "Content-Length: " + std::to_string(body.size()) + "\r\nX-UserId: 0x9000000001241\r\n" +
         extra + "\r\n" + body;
}

std::string Expected(const std::string& body) {
  std::string r = Request(body);
  r.insert(r.find("\r\n\r\n") + 2, kLine);
  return r;
}

// A socket with `room` bytes of buffer: send takes what fits and says "would block" when full.
struct FakeSocket {
  size_t room = 1000;
  size_t used = 0;
  std::string wire;   // what the peer has received
  bool broken = false;
  int64_t operator()(const char* data, size_t size, bool& would_block) {
    if (broken) return -1;
    if (used >= room) {
      would_block = true;
      return -1;
    }
    const size_t n = std::min(size, room - used);
    wire.append(data, n);
    used += n;
    return int64_t(n);
  }
  void Drain() { used = 0; }  // the peer read everything
};

// The game's send loop: chunks of `chunk` bytes, a WSAEWOULDBLOCK means try the same bytes
// again once the socket has room (the peer reads, `on_block`).
template <class Send, class OnBlock>
void GuestSend(forza::LspTitleStream& stream, Send& send, const std::string& data, size_t chunk,
               OnBlock on_block, size_t* max_pending = nullptr) {
  size_t at = 0;
  while (at < data.size()) {
    const size_t n = std::min(chunk, data.size() - at);
    const auto t0 = std::chrono::steady_clock::now();
    const int64_t took = stream.Send(reinterpret_cast<const uint8_t*>(data.data()) + at, n, send);
    CHECK(std::chrono::steady_clock::now() - t0 < std::chrono::milliseconds(50));  // never waits
    if (max_pending) *max_pending = std::max(*max_pending, stream.Pending());
    if (took == forza::LspTitleStream::kWouldBlock) {
      on_block();
      continue;
    }
    CHECK(took == int64_t(n));  // all or nothing
    at += n;
  }
}
}  // namespace

int main() {
  {  // one call: head and body together, then a second request on the kept-alive connection
    forza::LspRequestHeader h(Lines);
    const std::string body("\0\1\r\n\r\nbody bytes that look like a head", 39);
    CHECK(Feed(h, Request(body)) == Expected(body));
    CHECK(Feed(h, Request("x")) == Expected("x"));
    CHECK(h.Pending() == 0);
  }
  {  // byte by byte: nothing goes out before the request is complete
    forza::LspRequestHeader h(Lines);
    const std::string req = Request("abc");
    std::string out;
    for (size_t i = 0; i < req.size(); ++i) {
      out += Feed(h, req.substr(i, 1));
      if (i + 1 < req.size()) CHECK(out.empty() && h.Pending() == i + 1);
    }
    CHECK(out == Expected("abc") && h.Pending() == 0);
  }
  {  // the build line (#517) goes in next to the signature, and is not added to a request that has one
    CHECK(forza::LspRequestHeader::BuildLine(Request("a"), "0.2.1", 4) == "X-Forza-Build: 0.2.1/tu4\r\n");
    CHECK(forza::LspRequestHeader::BuildLine(Request("a"), "git-908ea56c5b", 0) ==
          "X-Forza-Build: git-908ea56c5b/tu0\r\n");
    CHECK(forza::LspRequestHeader::BuildLine(Request("a", "x-forza-build: other/tu4\r\n"), "0.2.1", 4).empty());
    forza::LspRequestHeader h([](const std::string& head, const std::string& body) {
      return forza::LspRequestHeader::BuildLine(head, "0.2.1", 4) + Lines(head, body);
    });
    std::string want = Request("abc");
    want.insert(want.find("\r\n\r\n") + 2, "X-Forza-Build: 0.2.1/tu4\r\n" + kLine);
    CHECK(Feed(h, Request("abc")) == want);
    // a head that names its build keeps that one and gets the signature only
    std::string named = Request("abc", "X-Forza-Build: other/tu4\r\n");
    std::string want_named = named;
    want_named.insert(want_named.find("\r\n\r\n") + 2, kLine);
    CHECK(Feed(h, named) == want_named);
  }
  {  // the lines see the whole head and body
    std::string seen_head, seen_body;
    forza::LspRequestHeader h([&](const std::string& head, const std::string& body) {
      seen_head = head;
      seen_body = body;
      return kLine;
    });
    const std::string body("x\0y\r\n\r\nz", 8);
    CHECK(Feed(h, Request(body)) == Expected(body));
    CHECK(seen_head + seen_body == Request(body) && seen_body == body);
  }
  {  // a body over kMaxBody passes through unsigned
    forza::LspRequestHeader h(Lines);
    const std::string head = "POST /big HTTP/1.1\r\nContent-Length: " +
                             std::to_string(forza::LspRequestHeader::kMaxBody + 1) + "\r\n\r\n";
    CHECK(Feed(h, head + "abc") == head + "abc");
    CHECK(h.Pending() == 0);
  }
  {  // the head split across calls, body and the next head in one buffer
    forza::LspRequestHeader h(Lines);
    const std::string two = Request("12345") + Request("");
    std::string out = Feed(h, two.substr(0, 30));
    CHECK(out.empty() && h.Pending() == 30);
    out += Feed(h, two.substr(30));
    CHECK(out == Expected("12345") + Expected(""));
    CHECK(h.Pending() == 0);
  }
  {  // a request that already has the header keeps its own
    forza::LspRequestHeader h(Lines);
    const std::string req = Request("z", "x-forza-auth: FFFF\r\n");
    CHECK(Feed(h, req) == req);
  }
  {  // no Content-Length: a request without a body
    forza::LspRequestHeader h(Lines);
    const std::string get = "GET /status HTTP/1.1\r\nHost: forza\r\n\r\n";
    std::string want = get;
    want.insert(want.size() - 2, kLine);
    CHECK(Feed(h, get + get) == want + want);
  }
  {  // not HTTP: a head that never ends is passed through after kMaxHead bytes
    forza::LspRequestHeader h(Lines);
    const std::string junk(forza::LspRequestHeader::kMaxHead + 10, 'j');
    CHECK(Feed(h, junk) == junk);
    CHECK(Feed(h, "more\r\n\r\n") == "more\r\n\r\n");
  }
  {  // a full socket: the game's bytes are taken all or none, the stream goes out once
    forza::LspTitleStream stream(Lines);
    FakeSocket sock;
    std::string body(200000, '\0');
    for (size_t i = 0; i < body.size(); ++i) body[i] = char(i * 7);
    // the second request's sends wait while the first one goes out
    const std::string three = Request(body) + Request(body) + Request("tail");
    size_t blocked = 0, max_pending = 0;
    GuestSend(stream, sock, three, 4096, [&] { ++blocked; sock.Drain(); }, &max_pending);
    while (stream.Pending()) {
      CHECK(stream.Flush(sock) == forza::LspTitleStream::kWouldBlock);
      sock.Drain();
      if (stream.Flush(sock) == 0) break;
    }
    CHECK(sock.wire == Expected(body) + Expected(body) + Expected("tail"));
    CHECK(blocked > 100);
    // a request goes out once complete: at most the first request, then one send's worth
    CHECK(max_pending <= Expected(body).size() + 4096);
  }
  {  // a socket that fails after taking bytes: those are not taken twice, the error follows
    forza::LspTitleStream stream(Lines);
    FakeSocket sock;
    sock.room = 10;
    const std::string req = Request("abc");
    CHECK(stream.Send(reinterpret_cast<const uint8_t*>(req.data()), req.size(), sock) == int64_t(req.size()));
    sock.broken = true;
    CHECK(stream.Flush(sock) == forza::LspTitleStream::kError);
    CHECK(stream.Send(reinterpret_cast<const uint8_t*>(req.data()), req.size(), sock) == forza::LspTitleStream::kError);
    CHECK(sock.wire == Expected("abc").substr(0, 10));
  }
#ifndef _WIN32
  {  // a real nonblocking socket whose peer stops reading: no call waits, nothing is lost or
     // sent twice, the line goes in once per request
    int sv[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, sv) == 0);
    int small = 4096;
    setsockopt(sv[0], SOL_SOCKET, SO_SNDBUF, &small, sizeof(small));
    CHECK(fcntl(sv[0], F_SETFL, fcntl(sv[0], F_GETFL) | O_NONBLOCK) == 0);
    CHECK(fcntl(sv[1], F_SETFL, fcntl(sv[1], F_GETFL) | O_NONBLOCK) == 0);
    auto send = [&](const char* data, size_t size, bool& would_block) -> int64_t {
      const ssize_t n = ::send(sv[0], data, size, MSG_NOSIGNAL);
      if (n < 0) would_block = errno == EAGAIN || errno == EWOULDBLOCK;
      return n;
    };
    std::string received;
    auto read_some = [&] {
      char buf[65536];
      ssize_t n;
      while ((n = ::recv(sv[1], buf, sizeof(buf), 0)) > 0) received.append(buf, size_t(n));
    };
    forza::LspTitleStream stream(Lines);
    std::string body(1 << 20, 'b');
    const std::string req = Request(body) + Request("second");
    // the peer reads nothing at first: the stream holds the first request until it is
    // complete, fills the socket with it, then says "would block"
    int64_t took = 0;
    size_t at = 0, blocked = 0;
    while (blocked == 0 && at < req.size()) {
      const auto t0 = std::chrono::steady_clock::now();
      took = stream.Send(reinterpret_cast<const uint8_t*>(req.data()) + at,
                         std::min<size_t>(4096, req.size() - at), send);
      CHECK(std::chrono::steady_clock::now() - t0 < std::chrono::milliseconds(50));
      if (took == forza::LspTitleStream::kWouldBlock) ++blocked;
      else at += size_t(took);
    }
    // then it reads as the rest goes out
    GuestSend(stream, send, req.substr(at), 4096, read_some);
    while (stream.Pending()) {
      stream.Flush(send);
      read_some();
    }
    read_some();
    CHECK(received == Expected(body) + Expected("second"));
    close(sv[0]);
    close(sv[1]);
  }
#endif
  std::puts("lsp_request_header: OK");
  return 0;
}
