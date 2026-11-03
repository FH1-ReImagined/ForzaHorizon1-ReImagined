// forza - header lines added to every HTTP request the game sends to its title servers.
//
// The Forza Live server takes a title service call's player from the X-UserId header the
// game writes, which anyone can write. Our game signs each call with its Live identity's
// friends key (X-Forza-Auth, live_signing.h; the launcher's shader list upload proves itself
// the same way), so the server can tell the player's own calls from someone else's
// (server/forza_live/title_services/lsp.py verify_caller) without the key ever being sent. The game's
// requests are HTTP/1.1 with Content-Length over one kept-alive connection
// (docs/title_services.md); each request is held until its head and body are complete (the
// signature covers the body), the lines go in before the head's blank line, and the request
// goes out. A body over kMaxBody passes through unsigned (the server takes 8 MB). The lines
// also name the game's build (BuildLine, X-Forza-Build).
// LspTitleStream keeps the socket's nonblocking contract on top of that (live_tcp.cpp).
// Header-only so tools/test_lsp_request_header.cpp can test it without the runtime.

#pragma once

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

namespace forza {

class LspRequestHeader {
 public:
  static constexpr size_t kMaxHead = 64 * 1024;
  static constexpr uint64_t kMaxBody = 16 * 1024 * 1024;

  // The header lines ("Name: value\r\n" each, or nothing) for a complete request: its head
  // (up to and including the blank line) and its body.
  using Lines = std::function<std::string(const std::string& head, const std::string& body)>;

  explicit LspRequestHeader(Lines lines) : lines_(std::move(lines)) {}

  // The bytes to send for these bytes of the request stream: every complete request with its
  // lines; a request not complete yet is held for the next call.
  std::string Feed(const uint8_t* data, size_t size) {
    std::string out;
    size_t i = 0;
    while (i < size) {
      if (passthrough_) {
        out.append(reinterpret_cast<const char*>(data) + i, size - i);
        break;
      }
      if (body_left_ > 0) {  // a body too large to hold: it goes out as it comes
        const size_t take = size_t(std::min<uint64_t>(body_left_, size - i));
        out.append(reinterpret_cast<const char*>(data) + i, take);
        i += take;
        body_left_ -= take;
        continue;
      }
      if (have_head_) {  // the body of the held head
        const size_t take = std::min(body_want_ - body_.size(), size - i);
        body_.append(reinterpret_cast<const char*>(data) + i, take);
        i += take;
        if (body_.size() == body_want_) out += Finish();
        continue;
      }
      const size_t search = head_.size() >= 3 ? head_.size() - 3 : 0;
      head_.append(reinterpret_cast<const char*>(data) + i, size - i);
      const size_t end = head_.find("\r\n\r\n", search);
      if (end == std::string::npos) {
        if (head_.size() > kMaxHead) {  // not HTTP as we know it: leave the stream alone
          out += head_;
          head_.clear();
          passthrough_ = true;
        }
        break;
      }
      const size_t head_size = end + 4;
      const size_t rest = head_.size() - head_size;  // after the head, from this call's data
      head_.resize(head_size);
      i = size - rest;
      const uint64_t length = ContentLength(head_);
      if (length > kMaxBody) {
        out += head_;
        head_.clear();
        body_left_ = length;
        continue;
      }
      have_head_ = true;
      body_want_ = size_t(length);
      if (body_want_ == 0) out += Finish();
    }
    return out;
  }

  // Bytes of a request held back until it is complete.
  size_t Pending() const { return head_.size() + body_.size(); }

  // Whether the head has a header of this (lower-case) name.
  static bool Has(const std::string& head, const std::string& name) {
    const std::string lower = Lower(head);
    return !name.empty() && lower.find("\r\n" + name + ":") != std::string::npos;
  }

  // The line that names the game's build to the server (X-Forza-Build, #517; the server keeps it
  // with each leaderboard row): "<id>/tu<0|4>", the id being the launcher's version or
  // git-<commit>. Nothing when the head names one already.
  static std::string BuildLine(const std::string& head, const std::string& build_id,
                               int title_update) {
    if (Has(head, "x-forza-build")) return {};
    return "X-Forza-Build: " + build_id + "/tu" + std::to_string(title_update) + "\r\n";
  }

 private:
  std::string Finish() {
    std::string head = std::move(head_), body = std::move(body_);
    head_.clear();
    body_.clear();
    have_head_ = false;
    body_want_ = 0;
    if (lines_) head.insert(head.size() - 2, lines_(head, body));
    return head + body;
  }

  static std::string Lower(std::string s) {
    for (char& c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
    return s;
  }

  static uint64_t ContentLength(const std::string& head) {
    const std::string lower = Lower(head);
    const size_t at = lower.find("\r\ncontent-length:");
    if (at == std::string::npos) return 0;
    uint64_t value = 0;
    for (size_t p = at + 17; p < lower.size(); ++p) {
      const char c = lower[p];
      if (c == ' ' || c == '\t') continue;
      if (c < '0' || c > '9') break;
      value = value * 10 + uint64_t(c - '0');
      if (value > (uint64_t(1) << 40)) break;
    }
    return value;
  }

  Lines lines_;
  std::string head_, body_;
  bool have_head_ = false;
  size_t body_want_ = 0;
  uint64_t body_left_ = 0;
  bool passthrough_ = false;
};

// A title server connection's outgoing side with the lines added, behaving like the
// nonblocking socket the game made (FIONBIO) and with exact progress: the bytes the lines add
// mean the request stream that goes out differs from the game's, so the stream keeps what
// the socket has not taken yet (Pending) and sends it before anything newer.
//
// Send(data, size, send) returns how many of the game's bytes were taken (all of them, once
// nothing older is waiting: they go through LspRequestHeader, which holds a request until it
// is complete, and whatever the socket does not take now waits), kWouldBlock when older
// output is still waiting (nothing taken, as a full socket would say), or kError after a hard
// socket error (the connection is dead; the error is the socket's). Taken bytes are never
// sent twice or dropped, and nothing here waits: Flush (from send, recv and select) moves
// waiting output on as the socket takes it.
//
// `send(data, size, would_block)` writes to the socket: bytes written, or -1 with
// would_block set when it is full.
class LspTitleStream {
 public:
  static constexpr int64_t kWouldBlock = -2;
  static constexpr int64_t kError = -1;

  explicit LspTitleStream(LspRequestHeader::Lines lines) : header_(std::move(lines)) {}

  template <class SendFn>
  int64_t Send(const uint8_t* data, size_t size, SendFn&& send) {
    if (failed_) return kError;
    const int64_t flushed = Flush(send);
    if (flushed != 0) return flushed;  // older output waits (or the socket failed)
    out_ = header_.Feed(data, size);
    sent_ = 0;
    if (Flush(send) == kError) failed_ = true;  // reported by the next call
    return int64_t(size);
  }

  // Sends what is waiting: 0 when nothing is left, kWouldBlock when the socket is full, kError.
  template <class SendFn>
  int64_t Flush(SendFn&& send) {
    if (failed_) return kError;
    while (sent_ < out_.size()) {
      bool would_block = false;
      const int64_t n = send(out_.data() + sent_, out_.size() - sent_, would_block);
      if (n > 0) {
        sent_ += size_t(n);
        continue;
      }
      if (n < 0 && !would_block) {
        failed_ = true;
        return kError;
      }
      return kWouldBlock;
    }
    out_.clear();
    sent_ = 0;
    return 0;
  }

  // Bytes taken from the game that the socket has not taken yet.
  size_t Pending() const { return out_.size() - sent_; }
  // A request held until it is complete (tests).
  size_t HeldHead() const { return header_.Pending(); }

 private:
  LspRequestHeader header_;
  std::string out_;
  size_t sent_ = 0;
  bool failed_ = false;
};

}  // namespace forza
