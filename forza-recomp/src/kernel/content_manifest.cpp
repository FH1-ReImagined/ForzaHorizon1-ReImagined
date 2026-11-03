// forza - the content this game has, for the Forza Live server (kernel/content_manifest.h, #567).
//
// dlc_log.cpp reports the installed DLC offers and mod_overlay.cpp the loaded mods before the
// game starts; live_friends.cpp sends the text with the session ids (chat op 5).

#include "content_manifest.h"

#include "live.h"

#include <mutex>
#include <set>
#include <string>

namespace forza {

namespace {
std::mutex mutex;
std::set<uint32_t> dlc_offers;
std::set<std::string> car_mods;
}  // namespace

void SetInstalledDlcOffers(const std::set<uint32_t>& offers) {
  std::lock_guard<std::mutex> lock(mutex);
  dlc_offers = offers;
}

void NoteLoadedMod(const std::string& name, bool changes_cars) {
  if (!changes_cars) return;
  const std::string clean = CleanModName(name);
  if (clean.empty()) return;
  std::lock_guard<std::mutex> lock(mutex);
  car_mods.insert(clean);
}

std::string CurrentContentManifest() {
  std::lock_guard<std::mutex> lock(mutex);
  return FormatManifest(dlc_offers, car_mods);
}

}  // namespace forza
