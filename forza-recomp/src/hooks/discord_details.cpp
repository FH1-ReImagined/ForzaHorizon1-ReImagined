// forza - what the Discord status shows besides the activity (#527): the player's car with its
// class and PI, the event and, in free roam, the zone of Colorado the player is in
// (platform/discord_model.h formats them, platform/discord_presence.cpp sends them).
//
// Sampled every 2 s on the main loop thread after the world update (hooks/telemetry.cpp,
// sub_823E40D8, TU4 82414068: once a frame while a world is loaded), only while Discord
// sharing and discord_presence_details are on. Every name is one the game shows, read from
// its own database through its string tables (in the game's language, DLC cars included):
// - The car: the loaded car of the player facade (82915BE0, vfunc +0x78), as the pause menu's
//   PlayerBar reads it (platform/fh1_current_car.h): its Data_Car Id (826BDCB0 -> +8), class
//   (825E6518) and performance index (825E6620, 0-1), shown as the class badge's PI
//   (82C9F6A0, as 8288F2B0 does). Year, make and model: Data_Car joined to List_CarMake, as
//   the PlayerBar's query; the class letter: CarClasses.DisplayName.
// - The event: the current career event id (0x833ED728, stored by the event setup 825C54B0,
//   docs/coop_campaign.md) -> Events.Name (free roam's own FREE_ROAM event is left out).
// - The zone: the game files every race track under one of Colorado's zones (Tracks.
//   EnvironmentId -> Environments.ShortDisplayName, the event cards' ZoneName: Red Rock, The
//   Rockies, ...). The zone of the track start nearest to the car is the player's: the start is
//   start_location_00 of the track's route (media/tracks/colorado/Ribbon_NN/TrackRouteNNN.xml,
//   99 files read once, a few ms). The car's position comes from telemetry.cpp.
// Text columns go through the recordset's localized getter (vfunc +0x4C): it turns a "_&N"
// string id into the game's text object, whose vfunc +0x1C gives its UTF-16 characters.
// Nothing is guessed: a name the game does not give leaves that part out.
// tu_address_map: TU4 addresses

#include "discord_details.h"

#include "../platform/discord_model.h"
#include "../platform/discord_presence.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/telemetry.h"

#include <rex/filesystem.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <optional>
#include <span>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace forza::discord {
#if FORZA_TU4
namespace {
using Clock = std::chrono::steady_clock;
using rex::X_STATUS;

// Guest scratch: +0 the recordset and text slots, +16 a column name (the game's string),
// +256 the column name's characters, +512 the SQL.
constexpr uint32_t kScratchSize = 4096;
constexpr uint32_t kColumn = 256, kSql = 512;

uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
bool Plausible(uint32_t address) { return address >= 0x10000 && address < 0xF0000000; }

class Guest {
 public:
  Guest(const PPCContext& context, uint8_t* memory, uint32_t scratch)
      : ctx(context), base(memory), scratch(scratch) {}
  uint32_t Call(uint32_t function, std::initializer_list<uint32_t> args) {
    PPCFunc* fn = function ? rex::runtime::ResolveIndirectFunction(function) : nullptr;
    if (!fn) return 0;
    unsigned i = 3;
    for (uint32_t value : args) {
      switch (i++) {
        case 3: ctx.r3.u64 = value; break;
        case 4: ctx.r4.u64 = value; break;
        case 5: ctx.r5.u64 = value; break;
        case 6: ctx.r6.u64 = value; break;
        case 7: ctx.r7.u64 = value; break;
      }
    }
    fn(ctx, base);
    return ctx.r3.u32;
  }
  uint32_t Virtual(uint32_t object, uint32_t offset, std::initializer_list<uint32_t> args) {
    if (!Plausible(object)) return 0;
    const uint32_t table = Read(base, object);
    return Plausible(table) ? Call(Read(base, table + offset), args) : 0;
  }
  uint32_t Put(uint32_t offset, std::string_view text) {
    std::memcpy(GuestPtr(base, scratch + offset), text.data(), text.size());
    GuestPtr(base, scratch + offset)[text.size()] = 0;
    return scratch + offset;
  }
  PPCContext ctx;
  uint8_t* base;
  uint32_t scratch;
};

// The game's UTF-16 (big-endian) text as UTF-8, on one line.
std::string Utf8(uint8_t* base, uint32_t chars) {
  std::string out;
  if (!Plausible(chars)) return out;
  for (unsigned i = 0; i < 160; ++i) {
    const auto* p = GuestPtr(base, chars + 2 * i);
    uint32_t c = uint32_t(p[0]) << 8 | p[1];
    if (!c) break;
    if (c >= 0xD800 && c <= 0xDBFF) {
      const auto* q = GuestPtr(base, chars + 2 * i + 2);
      const uint32_t low = uint32_t(q[0]) << 8 | q[1];
      if (low < 0xDC00 || low > 0xDFFF) return {};
      c = 0x10000 + ((c - 0xD800) << 10) + (low - 0xDC00);
      ++i;
    } else if (c >= 0xDC00 && c <= 0xDFFF) {
      return {};
    }
    if (c < 32 || c == 127) c = ' ';
    if (c < 0x80) {
      out += char(c);
    } else if (c < 0x800) {
      out += char(0xC0 | c >> 6); out += char(0x80 | (c & 63));
    } else if (c < 0x10000) {
      out += char(0xE0 | c >> 12); out += char(0x80 | (c >> 6 & 63)); out += char(0x80 | (c & 63));
    } else {
      out += char(0xF0 | c >> 18); out += char(0x80 | (c >> 12 & 63));
      out += char(0x80 | (c >> 6 & 63)); out += char(0x80 | (c & 63));
    }
  }
  // Names only: one line, no surrounding or doubled spaces.
  std::string clean;
  for (char c : out)
    if (c != ' ' || (!clean.empty() && clean.back() != ' ')) clean += c;
  while (!clean.empty() && clean.back() == ' ') clean.pop_back();
  return clean;
}

enum class Kind { kText, kNumber, kRaw };
struct Column { const char* name; Kind kind; };

// One statement and its first row: kText through the localized getter, kNumber as an
// integer, kRaw the column's own characters (ASCII). Empty when there is no row.
std::optional<std::vector<std::string>> Query(Guest& g, const std::string& sql,
                                              std::initializer_list<Column> columns) {
  if (!g.scratch || sql.size() >= kScratchSize - kSql) return std::nullopt;
  const uint32_t services = Read(g.base, 0x833CAAE4);
  const uint32_t holder = services ? g.Call(0x82F1E918, {services}) : 0;
  const uint32_t database = Plausible(holder) ? Read(g.base, holder) : 0;
  if (!Plausible(database)) return std::nullopt;
  const uint32_t slots = g.scratch, key = g.scratch + 16;
  std::memset(GuestPtr(g.base, slots), 0, 48);
  g.Virtual(database, 0x20, {slots, database, g.Put(kSql, sql)});
  const uint32_t records = Read(g.base, slots);
  std::optional<std::vector<std::string>> row;
  if (Plausible(records) && int32_t(g.Virtual(records, 0xB8, {records})) > 0) {
    const uint32_t table = Read(g.base, records);
    row.emplace();
    for (const Column& column : columns) {
      g.Call(0x82480600, {key, g.Put(kColumn, column.name)});
      std::string value;
      if (column.kind == Kind::kText) {
        g.Call(Read(g.base, table + 0x4C), {slots + 4, records, 0, key, 0});
        const uint32_t text = Read(g.base, slots + 4);
        if (Plausible(text)) value = Utf8(g.base, g.Virtual(text, 0x1C, {text}));
        g.Call(0x824CEAB0, {slots + 4});
      } else if (column.kind == Kind::kNumber) {
        value = std::to_string(int32_t(g.Call(Read(g.base, table + 0x94), {records, key})));
      } else {
        // The raw getter returns the game's std::string: 16 bytes inline or a pointer, then
        // its length and capacity.
        const uint32_t s = g.Call(Read(g.base, table + 0x44), {records, 0, key, 0});
        const uint32_t length = Plausible(s) ? Read(g.base, s + 16) : 0;
        const uint32_t capacity = Plausible(s) ? Read(g.base, s + 20) : 0;
        const uint32_t chars = capacity < 16 ? s : Read(g.base, s);
        if (Plausible(chars) && length <= 4096 && length <= capacity)
          for (uint32_t i = 0; i < length; ++i) {
            const char c = char(GuestPtr(g.base, chars + i)[0]);
            if (c < 32 || c > 126) { value.clear(); break; }
            value += c;
          }
      }
      g.Call(0x82441428, {key, 1, 0});
      row->push_back(std::move(value));
    }
  }
  g.Call(0x824CEAB0, {slots + 4});
  g.Call(0x824CEAB0, {slots});
  return row;
}

// start_location_00 (else the first start location) of a Colorado route file: (x, z).
std::optional<std::pair<float, float>> RouteStart(int route, int ribbon) {
  char path[96];
  std::snprintf(path, sizeof(path), "game:\\media\\tracks\\colorado\\Ribbon_%02d\\TrackRoute%03d.xml",
                ribbon, route);
  auto* vfs = rex::system::kernel_state()->file_system();
  auto* entry = vfs ? vfs->ResolvePath(path) : nullptr;
  if (!entry || entry->size() == 0 || entry->size() > (1u << 20)) return std::nullopt;
  rex::filesystem::File* file = nullptr;
  if (entry->Open(rex::filesystem::FileAccess::kFileReadData, &file) != X_STATUS_SUCCESS || !file)
    return std::nullopt;
  std::string text(entry->size(), '\0');
  size_t read = 0;
  const bool ok = file->ReadSync(std::span(reinterpret_cast<uint8_t*>(text.data()), text.size()), 0,
                                 &read) == X_STATUS_SUCCESS;
  file->Destroy();
  if (!ok) return std::nullopt;
  text.resize(read);
  auto attribute = [&](size_t from, std::string_view name) -> std::optional<float> {
    const size_t end = text.find("/>", from);
    size_t at = text.find(name, from);
    if (at == std::string::npos || at > end || at + name.size() + 1 >= text.size()) return std::nullopt;
    at += name.size();
    if (text[at] != '=' || (text[at + 1] != '\'' && text[at + 1] != '"')) return std::nullopt;
    char* stop = nullptr;
    const float value = std::strtof(text.c_str() + at + 2, &stop);
    if (stop == text.c_str() + at + 2 || !std::isfinite(value) || std::fabs(value) > 100000) return std::nullopt;
    return value;
  };
  std::optional<std::pair<float, float>> first;
  for (size_t at = text.find("start_location_"); at != std::string::npos;
       at = text.find("start_location_", at + 1)) {
    const size_t transform = text.find("<Transform", at);
    if (transform == std::string::npos) break;
    const auto x = attribute(transform, "pos.x"), z = attribute(transform, "pos.z");
    if (!x || !z) continue;
    if (text.compare(at, 17, "start_location_00") == 0) return std::pair(*x, *z);
    if (!first) first = std::pair(*x, *z);
  }
  return first;
}

// A track start and the zone (Environments) the game files its track under.
struct Spot {
  float x, z;
  uint32_t zone;
};

struct Cache {
  uint32_t scratch = 0;
  Clock::time_point next{};
  uint32_t model = 0;
  std::string car;
  std::unordered_map<uint32_t, std::string> classes;
  uint32_t event = 0;
  std::string event_name;
  bool zones_loaded = false;
  int zone_attempts = 0;
  std::vector<Spot> spots;
  std::unordered_map<uint32_t, std::string> zones;
};
Cache& State() { static Cache cache; return cache; }

// The zones of Colorado's free roam: every environment of a Colorado race track except the
// free roam's own (the whole map), each with the starts of its tracks.
void LoadZones(Guest& g, Cache& c) {
  if (c.zones_loaded || ++c.zone_attempts > 5) return;
  const auto t0 = Clock::now();
  const auto list = Query(g,
      "SELECT group_concat(t.RouteId || ',' || t.RibbonIndex || ',' || t.EnvironmentId, ';') AS spots "
      "FROM Tracks t INNER JOIN Environments e ON e.Id = t.EnvironmentId WHERE e.MediaName = 'Colorado' "
      "AND t.DevOnly = 0 AND t.RouteId > 0 AND t.EnvironmentId NOT IN (SELECT k.EnvironmentId FROM "
      "Events v INNER JOIN Races r ON r.EventId = v.Id INNER JOIN Tracks k ON k.id = r.TrackId "
      "WHERE v.HorizonEventID = 'FREE_ROAM')",
      {{"spots", Kind::kRaw}});
  if (!list || list->at(0).empty()) return;
  c.zones_loaded = true;
  const std::string& text = list->at(0);
  for (size_t at = 0; at < text.size();) {
    size_t end = text.find(';', at);
    if (end == std::string::npos) end = text.size();
    int route = 0, ribbon = 0;
    unsigned zone = 0;
    if (std::sscanf(text.substr(at, end - at).c_str(), "%d,%d,%u", &route, &ribbon, &zone) == 3 &&
        route > 0 && route < 1000 && ribbon >= 0 && ribbon < 100) {
      if (!c.zones.contains(zone)) {
        const auto name = Query(g, "SELECT ShortDisplayName AS name FROM Environments WHERE Id = " +
                                       std::to_string(zone), {{"name", Kind::kText}});
        c.zones[zone] = name ? name->at(0) : std::string();
      }
      if (!c.zones[zone].empty())
        if (const auto start = RouteStart(route, ribbon)) c.spots.push_back({start->first, start->second, zone});
    }
    at = end + 1;
  }
  REXLOG_INFO("DISCORD: {} zones of Colorado from {} track starts for the status ({} ms)",
              std::count_if(c.zones.begin(), c.zones.end(), [](const auto& z) { return !z.second.empty(); }),
              c.spots.size(),
              std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - t0).count());
}

std::string CarName(Guest& g, uint32_t model) {
  const auto row = Query(g,
      "SELECT c.Year AS carYear, c.DisplayName AS carName, m.DisplayName AS makeName "
      "FROM Data_Car c INNER JOIN List_CarMake m ON c.MakeId = m.ID WHERE c.Id = " +
          std::to_string(model),
      {{"carYear", Kind::kNumber}, {"makeName", Kind::kText}, {"carName", Kind::kText}});
  if (!row || row->at(2).empty()) return {};
  std::string name;
  const int year = std::atoi(row->at(0).c_str());
  if (year >= 1886 && year <= 2100) name = row->at(0);
  for (const std::string* part : {&row->at(1), &row->at(2)})
    if (!part->empty()) name += (name.empty() ? "" : " ") + *part;
  return name;
}
}  // namespace

void WorldFrame(const PPCContext& ctx, uint8_t* base) {
  if (!Wanted()) return;
  Cache& c = State();
  const auto now = Clock::now();
  if (now < c.next) return;
  c.next = now + std::chrono::seconds(2);
  if (!c.scratch) c.scratch = REX_KERNEL_MEMORY()->SystemHeapAlloc(kScratchSize);
  if (!c.scratch) return;
  Guest g(ctx, base, c.scratch);
  GameDetails details;

  // The loaded car, as the PlayerBar reads it.
  const uint32_t player = g.Call(0x82915BE0, {});
  const uint32_t car = Plausible(player) ? g.Virtual(player, 0x78, {player}) : 0;
  const uint32_t model_slot = Plausible(car) ? g.Call(0x826BDCB0, {car}) : 0;
  const uint32_t model = Plausible(model_slot) ? Read(base, model_slot) : 0;
  double raw_pi = 0;
  uint32_t raw_class = 0;
  if (model) {
    if (model != c.model) {
      c.model = model;
      c.car = CarName(g, model);
    }
    details.car = c.car;
    const uint32_t car_class = g.Call(0x825E6518, {car});
    g.Call(0x825E6620, {car});
    raw_pi = g.ctx.f1.f64;
    raw_class = car_class;
    // The PI as the class badge shows it: 82C9F6A0 maps the car's 0-1 performance index
    // into its class's display range with the CarClasses table at *0x8352EE3C (8288F2B0).
    const uint32_t ranges = Read(base, 0x8352EE3C);
    uint32_t pi = 0;
    if (Plausible(ranges) && std::isfinite(raw_pi) && raw_pi > 0 && raw_pi <= 1.5) {
      g.ctx.f1.f64 = raw_pi;
      pi = g.Call(0x82C9F6A0, {ranges});
    }
    if (car_class <= 10 && pi >= 1 && pi <= 9999) {
      auto it = c.classes.find(car_class);
      if (it == c.classes.end()) {
        const auto row = Query(g, "SELECT DisplayName AS name FROM CarClasses WHERE Id = " +
                                      std::to_string(car_class), {{"name", Kind::kText}});
        it = c.classes.emplace(car_class, row ? row->at(0) : std::string()).first;
      }
      details.car_class = it->second;
      details.pi = pi;
    }
  }

  // The current career event. Free roam is an event of its own (FREE_ROAM), which the game
  // sets before it leaves the event's presence: the last event's name stays until the next.
  // Any other event replaces it, with no name when the game gives none.
  const uint32_t event = Read(base, 0x833ED728);
  if (event != c.event) {
    c.event = event;
    std::optional<std::vector<std::string>> row;
    if (event && event < 100000)
      row = Query(g, "SELECT Name AS name, HorizonEventID AS hid FROM Events WHERE Id = " +
                         std::to_string(event), {{"name", Kind::kText}, {"hid", Kind::kRaw}});
    if (!row || row->at(1) != "FREE_ROAM") c.event_name = row ? row->at(0) : std::string();
  }
  details.event = c.event_name;

  // The zone of the nearest track start.
  LoadZones(g, c);
  float x = 0, z = 0;
  if (!c.spots.empty() && telemetry::LocalCarPosition(base, x, z)) {
    const Spot* nearest = nullptr;
    float best = 0;
    for (const Spot& spot : c.spots) {
      const float d = (spot.x - x) * (spot.x - x) + (spot.z - z) * (spot.z - z);
      if (!nearest || d < best) { nearest = &spot; best = d; }
    }
    details.place = c.zones[nearest->zone];
  }
  static const bool trace = [] {
    const char* v = std::getenv("FORZA_DISCORD_TRACE");
    return v && *v && *v != '0';
  }();
  if (trace)
    REXLOG_INFO("DISCORD: sample: car {} class {} PI {:.4f} event {} at {:.0f}, {:.0f}: \"{}\" {} {} "
                "\"{}\" \"{}\"", model, raw_class, raw_pi, event, x, z, details.car,
                details.car_class, details.pi, details.event, details.place);
  Details(details);
}
#else
void WorldFrame(const PPCContext&, uint8_t*) {}
#endif
}  // namespace forza::discord
