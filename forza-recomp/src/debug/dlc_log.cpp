// forza - FORZA_DLC_LOG=1 logs the game's answers about DLC cars (docs/dlc.md).
//
// The game's database has SQL functions for content (registered in sub_8250B9xx):
// IsCarInstalled (sub_82502DD8), IsCarInstalledAndPurchased (sub_82502E18) and IsCarOnDisc
// (sub_824F1A80). Their argument is the car id (r3) and a content type (r4). Each
// (function, id, type) is logged once, with the answer, so opening the Autoshow lists which
// cars the game counts as installed and owned.
//
// LogDlcPackages (always, at start) lists the DLC packages in the user data folder by their
// offers (Media/<offer>.puboffer: 1600 the Rally Expansion Pack, 2700 the 1000 Club), then one
// line per package with its folder, offers and the display name of its header, so a log names
// a package that is not one of the game's DLC (a fan-made pack hung loads in #66, #76, #115;
// #771), warns about the fan-made pack with offer 1975 (it hangs the first Single Player load),
// and warns when a Title Update 4 build has no Rally Expansion Pack: Horizon Rally then
// says "Xbox LIVE Marketplace error: Failed to download required Xbox LIVE Marketplace data" (#79).

#include "../kernel/live.h"
#include "../platform/guest_addr.h"

#include <rex/filesystem.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
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

namespace {

// The offer of the fan-made "Forza Horizon - Unicorn cars unlock (by Madmatoster)" pack, which no
// official package has; with it the first Single Player load never finishes (#115, docs/dlc.md).
constexpr int kFanPackOffer = 1975;

// The display name in a package's header file (Headers/00000002/<name>.header, written by the
// launcher, tools/install_dlc.py header_bytes: UTF-16BE at +8, 128 characters). Characters
// outside ASCII are written as '?', and '"' as '\'' so the quoted field ends where it seems
// to: it is only for the log.
std::string HeaderDisplayName(const std::filesystem::path& header) {
  std::FILE* f = rex::filesystem::OpenFile(header, "rb");
  if (!f) return {};
  unsigned char data[8 + 256] = {};
  const size_t got = std::fread(data, 1, sizeof(data), f);
  std::fclose(f);
  std::string name;
  for (size_t i = 8; i + 1 < got; i += 2) {
    const unsigned c = (unsigned(data[i]) << 8) | data[i + 1];
    if (!c) break;
    name += c == '"' ? '\'' : (c >= 0x20 && c < 0x7F) ? char(c) : '?';
  }
  return name;
}

}  // namespace

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
  std::vector<std::string> lines;  // one per package: offers, folder, display name
  std::vector<std::string> fan_packs;  // packages with offer 1975: folder and display name
  int packages = 0;
  for (const auto& pkg : fs::directory_iterator(dir, ec)) {
    std::error_code e2;
    if (!pkg.is_directory(e2)) continue;
    const std::string name = pkg.path().filename().string();
    if (name.size() > 5 && name.compare(name.size() - 5, 5, ".part") == 0) continue;
    ++packages;
    const fs::path header = title / "Headers" / "00000002" / (name + ".header");
    const bool has_header = fs::is_regular_file(header, e2);
    if (!has_header) bare.push_back(name);
    std::string own;  // this package's offers
    bool fan_pack = false;
    for (const auto& sub : fs::directory_iterator(pkg.path(), e2)) {
      if (lower(sub.path().filename().string()) != "media") continue;
      std::error_code e3;
      for (const auto& f : fs::directory_iterator(sub.path(), e3)) {
        const std::string fname = lower(f.path().filename().string());
        if (fname.size() > 9 && fname.compare(fname.size() - 9, 9, ".puboffer") == 0) {
          const int offer = std::atoi(fname.c_str());
          offers.insert(offer);
          own += (own.empty() ? "" : ",") + std::to_string(offer);
          fan_pack = fan_pack || offer == kFanPackOffer;
        }
      }
    }
    const std::string display = has_header ? HeaderDisplayName(header) : std::string();
    lines.push_back("offer " + (own.empty() ? std::string("none") : own) + " " + name + " \"" +
                    display + "\"");
    if (fan_pack) fan_packs.push_back(name + (display.empty() ? "" : " \"" + display + "\""));
  }
  std::sort(lines.begin(), lines.end());
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
    for (const std::string& line : lines) REXLOG_INFO("DLC:   {}", line);
    for (const std::string& pack : fan_packs) {
      REXLOG_WARN(
          "DLC: {} is the fan-made unicorn cars pack (offer 1975), not one of the game's DLC: with "
          "it the first Single Player load never finishes (the loading screen runs for ever). Move "
          "it out of the installed DLC (launcher: Setup or Play, \"Move the pack out\"); for the "
          "unicorn cars without it: Saves > Edit profile > Unicorn cars > Add missing unicorns",
          pack);
    }
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
