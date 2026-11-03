#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <unistd.h>
#endif
#include "data_out.h"
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <mutex>
#include <thread>
#include <string>
#include <atomic>
#include <cstdio>

namespace forza::data_out {
namespace {
using Clock = std::chrono::steady_clock;
struct Sender {
  std::atomic<bool> enabled{false};
  bool horizon = true, stop = false;
  unsigned port = 0, rate = 60;
  std::string ip;
  std::mutex mutex;
  std::condition_variable wake;
  Sample latest;
  Clock::time_point sampled{};
  std::thread worker;
  Sender() {
    const char* on = std::getenv("FORZA_DATA_OUT_ENABLE");
    if (!on || std::strcmp(on, "1") != 0) return;
    const char* address = std::getenv("FORZA_DATA_OUT_IP");
    const char* port_text = std::getenv("FORZA_DATA_OUT_PORT");
    const char* rate_text = std::getenv("FORZA_DATA_OUT_RATE");
    const char* format = std::getenv("FORZA_DATA_OUT_FORMAT");
    ip = address ? address : "127.0.0.1";
    auto integer = [](const char* text, long fallback) {
      if (!text) return fallback;
      if (!*text) return -1l;
      for (auto* p = text; *p; ++p) if (*p < '0' || *p > '9') return -1l;
      if (std::strlen(text) > 5) return -1l;
      return std::strtol(text, nullptr, 10);
    };
    long p = integer(port_text, 5300), r = integer(rate_text, 60);
    if (p < 1 || p > 65535 || r < 1 || r > 60) return;
    if (format && std::strcmp(format, "horizon") && std::strcmp(format, "fm7")) return;
    horizon = !format || std::strcmp(format, "fm7") != 0;
    port = unsigned(p); rate = unsigned(r); enabled = true;
    try { worker = std::thread([this] { Run(); }); }
    catch (const std::exception& error) {
      enabled = false;
      std::fprintf(stderr, "Data Out: cannot start sender: %s\n", error.what());
    }
  }
  ~Sender() { Shutdown(); }
  void Shutdown() {
    enabled = false;
    { std::lock_guard lock(mutex); stop = true; }
    wake.notify_all();
    if (worker.joinable()) worker.join();
  }
  void Run() {
#ifdef _WIN32
    WSADATA startup;
    if (WSAStartup(MAKEWORD(2, 2), &startup)) { enabled = false; return; }
    using Socket = SOCKET;
    constexpr Socket bad = INVALID_SOCKET;
#else
    using Socket = int;
    constexpr Socket bad = -1;
#endif
    sockaddr_in target{};
    target.sin_family = AF_INET; target.sin_port = htons(uint16_t(port));
    // Numeric IPv4 only: never resolve DNS on a game/guest thread.
    Socket socket = bad;
    if (inet_pton(AF_INET, ip.c_str(), &target.sin_addr) == 1)
      socket = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (socket != bad) {
#ifdef _WIN32
      u_long nonblocking = 1;
      if (ioctlsocket(socket, FIONBIO, &nonblocking)) { closesocket(socket); socket = bad; }
#else
      if (fcntl(socket, F_SETFL, O_NONBLOCK) < 0) { close(socket); socket = bad; }
#endif
    }
    const auto started = Clock::now();
    if (socket == bad) {
      enabled = false;
      std::fprintf(stderr, "Data Out: cannot open UDP output to %s:%u\n", ip.c_str(), port);
#ifdef _WIN32
      WSACleanup();
#endif
      return;
    }
    std::unique_lock lock(mutex);
    auto send = [&](Sample sample) {
      const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - started).count();
      const auto packet = Encode(sample, uint32_t(ms), horizon);
      if (socket != bad) sendto(socket, reinterpret_cast<const char*>(packet.data()), horizon ? 324 : 311,
                               0, reinterpret_cast<const sockaddr*>(&target), sizeof(target));
    };
    while (!stop) {
      wake.wait_for(lock, std::chrono::microseconds(1000000 / rate), [this] { return stop; });
      if (stop) break;
      auto sample = latest;
      if (sampled == Clock::time_point{} || Clock::now() - sampled > std::chrono::milliseconds(150))
        sample.driving = false;
      lock.unlock(); send(sample); lock.lock();
    }
    lock.unlock(); send({});
    if (socket != bad) {
#ifdef _WIN32
      closesocket(socket);
#else
      close(socket);
#endif
    }
#ifdef _WIN32
    WSACleanup();
#endif
  }
};
Sender& State() { static Sender sender; return sender; }
}
bool Enabled() { return State().enabled; }
void Publish(const Sample& sample) {
  auto& s = State();
  if (!s.enabled) return;
  std::unique_lock lock(s.mutex, std::try_to_lock);
  if (!lock.owns_lock()) return;
  s.latest = sample; s.sampled = Clock::now();
}
void Stop() { State().Shutdown(); }
}
