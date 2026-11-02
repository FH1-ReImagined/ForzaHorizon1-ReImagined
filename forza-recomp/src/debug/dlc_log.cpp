// forza - FORZA_DLC_LOG=1 logs the game's answers about DLC cars (docs/dlc.md).
//
// The game's database has SQL functions for content (registered in sub_8250B9xx):
// IsCarInstalled (sub_82502DD8), IsCarInstalledAndPurchased (sub_82502E18) and IsCarOnDisc
// (sub_824F1A80). Their argument is the car id (r3) and a content type (r4). Each
// (function, id, type) is logged once, with the answer, so opening the Autoshow lists which
// cars the game counts as installed and owned.
//
// LogDlcPackages (always, at start) lists the DLC packages in the user data folder by their
// offers (Media/<offer>.puboffer: 1600 the Rally Expansion Pack, 2700 the 1000 Club), and warns
// when a Title Update 4 build has no Rally Expansion Pack: Horizon Rally then says "Xbox LIVE
// Marketplace error: Failed to download required Xbox LIVE Marketplace data" (#79).

#include "../kernel/live.h"
#include "../platform/guest_addr.h"

#include <rex/filesystem.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <set>
#include <string>
#include <system_error>
#include <tuple>
#include <vector>

extern "C" REX_FUNC(__imp__sub_82502DD8);
extern "C" REX_FUNC(__imp__sub_82502E18);
extern "C" REX_FUNC(__imp__sub_824F1A80);

namespace {

bool Enabled() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_DLC_LOG");
    return v && *v && *v != '0';
  }();
  return on;
}

void Log(const char* fn, uint32_t id, uint32_t type, uint32_t result) {
  static std::mutex mutex;
  static std::set<std::tuple<const char*, uint32_t, uint32_t>> seen;
  std::lock_guard<std::mutex> lock(mutex);
  if (seen.emplace(fn, id, type).second) {
    REXLOG_INFO("DLC: {}(id {}, type {}) = {}", fn, id, type, result & 0xFF);
  }
}

}  // namespace

extern "C" REX_FUNC(sub_82502DD8) {
  const uint32_t id = ctx.r3.u32, type = ctx.r4.u32;
  __imp__sub_82502DD8(ctx, base);
  if (Enabled()) Log("IsCarInstalled", id, type, ctx.r3.u32);
}

extern "C" REX_FUNC(sub_82502E18) {
  const uint32_t id = ctx.r3.u32, type = ctx.r4.u32;
  __imp__sub_82502E18(ctx, base);
  if (Enabled()) Log("IsCarInstalledAndPurchased", id, type, ctx.r3.u32);
}

extern "C" REX_FUNC(sub_824F1A80) {
  const uint32_t id = ctx.r3.u32, type = ctx.r4.u32;
  __imp__sub_824F1A80(ctx, base);
  if (Enabled()) Log("IsCarOnDisc", id, type, ctx.r3.u32);
}

namespace forza {

void LogDlcPackages(const std::filesystem::path& user_data_root) {
  namespace fs = std::filesystem;
  const fs::path title = user_data_root / "0000000000000000" / "4D5309C9";
  const fs::path dir = title / "00000002";
  auto lower = [](std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return std::tolower(c); });
    return s;
  };
  std::error_code ec;
  std::set<int> offers;
  std::vector<std::string> bare;
  int packages = 0;
  for (const auto& pkg : fs::directory_iterator(dir, ec)) {
    std::error_code e2;
    if (!pkg.is_directory(e2)) continue;
    const std::string name = pkg.path().filename().string();
    if (name.size() > 5 && name.compare(name.size() - 5, 5, ".part") == 0) continue;
    ++packages;
    if (!fs::is_regular_file(title / "Headers" / "00000002" / (name + ".header"), e2)) {
      bare.push_back(name);
    }
    for (const auto& sub : fs::directory_iterator(pkg.path(), e2)) {
      if (lower(sub.path().filename().string()) != "media") continue;
      std::error_code e3;
      for (const auto& f : fs::directory_iterator(sub.path(), e3)) {
        const std::string fname = lower(f.path().filename().string());
        if (fname.size() > 9 && fname.compare(fname.size() - 9, 9, ".puboffer") == 0) {
          offers.insert(std::atoi(fname.c_str()));
        }
      }
    }
  }
  {
    std::set<uint32_t> installed;
    for (int o : offers) {
      if (o > 0) installed.insert(uint32_t(o));
    }
    SetInstalledDlcOffers(installed);  // the content manifest the server gets (#567)
  }
  if (packages == 0) {
    REXLOG_INFO("DLC: no packages in {}", rex::path_to_utf8(dir));
  } else {
    std::string list;
    for (int o : offers) list += (list.empty() ? "" : " ") + std::to_string(o);
    REXLOG_INFO("DLC: {} packages in {}, offers {}", packages, rex::path_to_utf8(dir), list);
    if (!bare.empty()) {
      REXLOG_WARN("DLC: {} package(s) without a header in Headers\\00000002 (first {})", bare.size(),
                  bare.front());
    }
  }
#if FORZA_TU4
  if (!offers.count(1600)) {
    REXLOG_WARN(
        "DLC: no Rally Expansion Pack (offer 1600): Horizon Rally says \"Xbox LIVE Marketplace "
        "error\" (install the DLC with the Rally Expansion Pack, launcher Setup step 2)");
  }
#endif
}

}  // namespace forza
