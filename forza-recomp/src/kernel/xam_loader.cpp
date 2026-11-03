// forza - the game restarting itself (XamLoaderLaunchTitle with its own executable).
//
// Accepting an invite outside the multiplayer menus (in free roam, #81) makes the game
// restart itself to join: the invite state machine (sub_8291A298) calls sub_824AC7E8,
// which keeps the accepted invite (X_INVITE_INFO, 0x58 bytes) as launch data
// (XamLoaderSetLaunchData) and launches its own executable (XLaunchNewImage ->
// XamLoaderLaunchTitle with the name from its command line). At boot the game reads the
// launch data (sub_824B32E0: 0x58 bytes = "started from an invite") and joins that
// session. The SDK's XamLoaderLaunchTitle only ends the title, so the game closed.
//
// Here a launch of a title path starts this program again with the same command line and
// hands the launch data to the new run in a file (DataDir()/relaunch_launch_data.bin,
// read only by a run with FORZA_RELAUNCHED=1 in its environment):
// - Linux: execv of this executable in the same process (the launcher, play.sh or a
//   terminal keep waiting on the same PID). Every descriptor is closed on exec (the
//   game's sockets free their ports) and the guest memory file is unlinked first.
// - Windows: with FORZA_LAUNCHER_RELAUNCH=1 (set by the launcher) the game exits with
//   kRelaunchExitCode and the launcher starts it again; otherwise it starts a new
//   process of itself and exits.
// A launch without a path (the dashboard) and title_relaunch=false go to the SDK.
//
// The restarted game is a "soft reboot": any launch data the boot accepts (the invite, or
// the "softreboot" text sub_824AC7E8 sets when it has none) sets the game object's byte
// +39, and sub_824B3C00 then skips the boot movies. The byte stays set, and with it
// Single Player never loaded afterwards (picked from the main menu after leaving the
// party, the menu stayed up; checked with a boot from "softreboot" data alone too). The
// game object is cleared of it when its main loop starts (sub_823ED888, after the boot
// read it): the invite join and the menus work as before, and Single Player loads.
// skip_startup_movies uses the same boot on a first start: with it the game reads the
// "softreboot" text as launch data and skips the movies.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/sdk_import.h"
#include "identity.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <string>
#include <system_error>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

using forza::GuestPtr;

REXCVAR_DEFINE_BOOL(title_relaunch, true, "Network/Forza Live",
                    "When the game restarts itself (accepting an invite in free roam), "
                    "start it again instead of closing");

REXCVAR_DEFINE_BOOL(skip_startup_movies, false, "Gameplay",
                    "Skip the startup movies (Microsoft Studios, Dolby, Playground Games): the game "
                    "boots as after a restart of itself");

namespace {

using GuestFunc = void (*)(PPCContext&, uint8_t*);

constexpr int kRelaunchExitCode = 75;  // the launcher starts the game again (Windows)
constexpr uint32_t kErrorNotFound = 0x490;  // ERROR_NOT_FOUND
constexpr uint32_t kErrorInvalidParameter = 0x57;

std::mutex mutex;
std::vector<uint8_t> set_data;   // the game's last XamLoaderSetLaunchData
bool boot_loaded = false;        // the launch data of this run: read once
bool boot_present = false;
std::vector<uint8_t> boot_data;  // what the previous run left us
bool invite_taken = false;       // the boot invite went to XInviteGetAcceptedInfo

std::filesystem::path LaunchDataFile() { return forza::DataDir() / "relaunch_launch_data.bin"; }

void LoadBootDataLocked() {
  if (boot_loaded) return;
  boot_loaded = true;
  const char* relaunched = std::getenv("FORZA_RELAUNCHED");
  if (!relaunched || std::strcmp(relaunched, "1") != 0) {
    // skip_startup_movies: a cold boot with the launch data of a soft reboot, the text
    // sub_824AC7E8 sets ("softreboot", 11 bytes with the NUL). sub_824B32E0 then sets the
    // byte +39 (cleared when the main loop starts) and the boot skips its movies.
    if (REXCVAR_GET(skip_startup_movies)) {
      static const char kSoftReboot[] = "softreboot";
      boot_data.assign(kSoftReboot, kSoftReboot + sizeof(kSoftReboot));
      boot_present = true;
      REXLOG_INFO("FORZA_LOADER: skip_startup_movies: booting as a soft reboot");
    }
    return;
  }
  const auto path = LaunchDataFile();
  std::ifstream in(path, std::ios::binary);
  if (in) {
    boot_data.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
    boot_present = !boot_data.empty();
  }
  in.close();
  std::error_code ec;
  std::filesystem::remove(path, ec);
  REXLOG_INFO("FORZA_LOADER: restarted by the game; launch data {} bytes", boot_present ? boot_data.size() : 0);
}

std::string Hex(const std::vector<uint8_t>& data, size_t max) {
  static const char digits[] = "0123456789ABCDEF";
  std::string s;
  for (size_t i = 0; i < data.size() && i < max; ++i) {
    s += digits[data[i] >> 4];
    s += digits[data[i] & 15];
  }
  return s;
}

#ifndef _WIN32
// This process's command line, as it was started.
std::vector<std::string> CommandLine() {
  std::vector<std::string> args;
  std::ifstream in("/proc/self/cmdline", std::ios::binary);
  std::string arg;
  while (std::getline(in, arg, '\0')) args.push_back(arg);
  return args;
}

// The guest memory file (/dev/shm/xenia_memory_*, 5 GB): exec drops the mapping but not
// the file.
void UnlinkGuestMemoryFile() {
  std::ifstream maps("/proc/self/maps");
  std::string line;
  while (std::getline(maps, line)) {
    const size_t at = line.find("/dev/shm/xenia_memory_");
    if (at == std::string::npos) continue;
    std::string path = line.substr(at);
    if (const size_t end = path.find(' '); end != std::string::npos) path.resize(end);
    unlink(path.c_str());
    return;
  }
}

void CloseOnExec() {
#ifdef SYS_close_range
  if (syscall(SYS_close_range, 3u, ~0u, 4u /* CLOSE_RANGE_CLOEXEC */) == 0) return;
#endif
  for (int fd = 3; fd < 65536; ++fd) {
    const int flags = fcntl(fd, F_GETFD);
    if (flags >= 0) fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
  }
}
#endif

// Starts the game again; returns only when that failed.
void Relaunch() {
  rex::FlushLogging();
#ifdef _WIN32
  SetEnvironmentVariableA("FORZA_RELAUNCHED", "1");
  const char* by_launcher = std::getenv("FORZA_LAUNCHER_RELAUNCH");
  if (by_launcher && std::strcmp(by_launcher, "1") == 0) {
    REXLOG_INFO("FORZA_LOADER: exiting with code {} for the launcher to start the game again",
                kRelaunchExitCode);
    rex::FlushLogging();
    TerminateProcess(GetCurrentProcess(), kRelaunchExitCode);
    return;
  }
  wchar_t exe[MAX_PATH * 4];
  if (!GetModuleFileNameW(nullptr, exe, DWORD(std::size(exe)))) return;
  std::wstring command_line = GetCommandLineW();
  STARTUPINFOW si{};
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi{};
  if (!CreateProcessW(exe, command_line.data(), nullptr, nullptr, FALSE, 0, nullptr, nullptr,
                      &si, &pi)) {
    REXLOG_ERROR("FORZA_LOADER: could not start the game again (error {})", GetLastError());
    return;
  }
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  REXLOG_INFO("FORZA_LOADER: started the game again (PID {}); this one exits", pi.dwProcessId);
  rex::FlushLogging();
  TerminateProcess(GetCurrentProcess(), 0);
#else
  std::vector<std::string> args = CommandLine();
  if (args.empty()) return;
  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(a.data());
  argv.push_back(nullptr);
  setenv("FORZA_RELAUNCHED", "1", 1);
  UnlinkGuestMemoryFile();
  CloseOnExec();
  REXLOG_INFO("FORZA_LOADER: starting the game again in this process");
  rex::FlushLogging();
  // The executable by its own path (via /proc/self/exe the process would be named "exe").
  std::error_code ec;
  const std::string exe = std::filesystem::read_symlink("/proc/self/exe", ec).string();
  execv(ec || exe.empty() ? "/proc/self/exe" : exe.c_str(), argv.data());
  REXLOG_ERROR("FORZA_LOADER: could not start the game again (errno {})", errno);
#endif
}

}  // namespace

namespace forza {
void RestartForBenchmark() { Relaunch(); }
}

// The game's main loop, sub_823ED888(game object): entered once, after the boot.
extern "C" REX_FUNC(__imp__sub_823ED888);
extern "C" REX_FUNC(sub_823ED888) {
  const uint32_t game = ctx.r3.u32;
  if (game && *GuestPtr(base, game + 39)) {
    *GuestPtr(base, game + 39) = 0;
    REXLOG_INFO("FORZA_LOADER: soft reboot: boot done, flag cleared");
  }
  __imp__sub_823ED888(ctx, base);
}

namespace forza {
// The invite the previous run kept as launch data (X_INVITE_INFO, 0x54 bytes), once.
bool LoaderTakeBootInvite(uint8_t* out) {
  std::lock_guard<std::mutex> lock(mutex);
  LoadBootDataLocked();
  if (invite_taken || !boot_present || boot_data.size() < 0x54) return false;
  invite_taken = true;
  std::memcpy(out, boot_data.data(), 0x54);
  return true;
}
}  // namespace forza

// DWORD XamLoaderSetLaunchData(const void* data, DWORD size)
extern "C" REX_FUNC(__imp__XamLoaderSetLaunchData) {
  {
    std::lock_guard<std::mutex> lock(mutex);
    const uint32_t data = ctx.r3.u32, size = ctx.r4.u32;
    if (data && size && size <= 0x10000) {
      const uint8_t* p = GuestPtr(base, data);
      set_data.assign(p, p + size);
    } else {
      set_data.clear();
    }
    REXLOG_INFO("FORZA_LOADER: XamLoaderSetLaunchData {} bytes {}", set_data.size(), Hex(set_data, 32));
  }
  static GuestFunc sdk = forza::SdkImport("__imp__XamLoaderSetLaunchData");
  if (sdk) sdk(ctx, base);
}

// DWORD XamLoaderGetLaunchDataSize(DWORD* size)
extern "C" REX_FUNC(__imp__XamLoaderGetLaunchDataSize) {
  std::lock_guard<std::mutex> lock(mutex);
  LoadBootDataLocked();
  const uint32_t out = ctx.r3.u32;
  if (!out) {
    ctx.r3.u64 = kErrorInvalidParameter;
    return;
  }
  const uint32_t size = boot_present ? uint32_t(boot_data.size()) : 0;
  uint8_t* p = GuestPtr(base, out);
  p[0] = uint8_t(size >> 24), p[1] = uint8_t(size >> 16), p[2] = uint8_t(size >> 8), p[3] = uint8_t(size);
  ctx.r3.u64 = boot_present ? 0 : kErrorNotFound;
}

// DWORD XamLoaderGetLaunchData(void* buffer, DWORD size)
extern "C" REX_FUNC(__imp__XamLoaderGetLaunchData) {
  std::lock_guard<std::mutex> lock(mutex);
  LoadBootDataLocked();
  const uint32_t buffer = ctx.r3.u32, size = ctx.r4.u32;
  if (!boot_present) {
    ctx.r3.u64 = kErrorNotFound;
    return;
  }
  if (buffer) std::memcpy(GuestPtr(base, buffer), boot_data.data(), std::min<size_t>(size, boot_data.size()));
  REXLOG_INFO("FORZA_LOADER: XamLoaderGetLaunchData: {} bytes", boot_data.size());
  ctx.r3.u64 = 0;
}

// void XamLoaderLaunchTitle(const char* path, DWORD flags)
extern "C" REX_FUNC(__imp__XamLoaderLaunchTitle) {
  const uint32_t path_ptr = ctx.r3.u32, flags = ctx.r4.u32;
  const std::string path = path_ptr ? reinterpret_cast<const char*>(GuestPtr(base, path_ptr)) : "";
  std::vector<uint8_t> data;
  {
    std::lock_guard<std::mutex> lock(mutex);
    data = set_data;
  }
  REXLOG_INFO("FORZA_LOADER: XamLoaderLaunchTitle('{}', flags {:08X}), launch data {} bytes", path,
              flags, data.size());
  if (!path.empty() && REXCVAR_GET(title_relaunch)) {
    std::error_code ec;
    std::filesystem::create_directories(forza::DataDir(), ec);
    std::ofstream out(LaunchDataFile(), std::ios::binary | std::ios::trunc);
    out.write(reinterpret_cast<const char*>(data.data()), std::streamsize(data.size()));
    out.close();
    if (out) {
      Relaunch();
    } else {
      REXLOG_ERROR("FORZA_LOADER: could not write {}", LaunchDataFile().string());
    }
  }
  static GuestFunc sdk = forza::SdkImport("__imp__XamLoaderLaunchTitle");
  if (sdk) sdk(ctx, base);
}

// void XamLoaderTerminateTitle()
extern "C" REX_FUNC(__imp__XamLoaderTerminateTitle) {
  REXLOG_INFO("FORZA_LOADER: XamLoaderTerminateTitle");
  static GuestFunc sdk = forza::SdkImport("__imp__XamLoaderTerminateTitle");
  if (sdk) sdk(ctx, base);
}
