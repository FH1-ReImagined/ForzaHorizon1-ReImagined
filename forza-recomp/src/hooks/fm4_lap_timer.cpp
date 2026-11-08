// forza - the lap timer on a converted FM4 circuit (#648, experimental).
//
// The mounted circuit's lap.txt (fm4_track_mount.cpp; written by the launcher's import with
// tools/fm4_timing.py: FM4's AI line, start/finish at its point 0, the sectors from FM4's
// SplitPoints) is timed by platform/fm4_lap.h once a frame, from the world update
// (hooks/telemetry.cpp). The clock runs only while the world is loaded, not paused and not
// rewinding (a rewind ends the lap uncounted). The lap, the sectors and the best lap show in
// the game's own notification HUD (platform/challenge_hud.h), and every lap that counts is
// added to <data>/fm4-tracks/laps/<track>.jsonl (one JSON object a line: time, sectors, date,
// whether it was the best so far), which the launcher can read.
//
// Time attack: the best lap's trace is the ghost (<track>.ghost.txt beside the laps, kept
// across sessions), and the HUD shows the time to it at the car's place on the lap as well.
//
// FORZA_FM4_LAP=<lap.txt>[|<laps.jsonl>] times a circuit without the mount (development).
#include "fm4_lap_timer.h"

#include "../platform/challenge_hud.h"
#include "../platform/fm4_lap.h"
#include "../platform/telemetry.h"

#include <rex/logging.h>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>

namespace forza::fm4_lap {
namespace {

namespace fs = std::filesystem;

struct State {
  Line line;
  std::unique_ptr<Timer> timer;
  fs::path laps;
  double clock = 0;            // seconds of driving
  double last_frame = 0;       // steady seconds of the last world update
  double next_hud = 0;
  double flash_until = 0;      // a lap or sector result stays on the HUD this long
  std::string flash_first, flash_second;
  bool rewinding = false;
};

std::mutex mutex;
State* state = nullptr;

double Steady() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::string Upper(std::string s) {
  for (auto& c : s) c = char(std::toupper(static_cast<unsigned char>(c)));
  return s;
}

// The best lap of the circuit's earlier laps (each line's "time").
double BestOf(const fs::path& laps) {
  std::ifstream in(laps);
  std::string row;
  double best = -1;
  while (std::getline(in, row)) {
    const auto at = row.find("\"time\":");
    if (at == std::string::npos) continue;
    const double t = std::strtod(row.c_str() + at + 7, nullptr);
    if (t > 0 && (best < 0 || t < best)) best = t;
  }
  return best;
}

fs::path GhostPath(const fs::path& laps) {
  fs::path p = laps;
  return p.replace_extension(".ghost.txt");
}

void SaveGhost(const State& s) {
  if (s.laps.empty() || s.timer->ghost().empty()) return;
  const fs::path p = GhostPath(s.laps), tmp = fs::path(p).concat(".new");
  {
    std::ofstream out(tmp, std::ios::binary | std::ios::trunc);
    out << WriteTrace(s.timer->best(), s.timer->ghost());
    if (!out) return;
  }
  std::error_code ec;
  fs::rename(tmp, p, ec);
}

void LoadGhost(State& s) {
  if (s.laps.empty()) return;
  std::ifstream in(GhostPath(s.laps), std::ios::binary);
  if (!in) return;
  std::stringstream text;
  text << in.rdbuf();
  double best = -1;
  Trace trace;
  if (ReadTrace(text.str(), best, trace)) s.timer->SetGhost(best, std::move(trace));
}

std::string Signed(double d) {
  if (!std::isfinite(d)) return "";
  char buf[16];
  std::snprintf(buf, sizeof buf, "%+.2f", d);
  return buf;
}

void Save(const State& s, const Event& e) {
  if (s.laps.empty()) return;
  std::error_code ec;
  fs::create_directories(s.laps.parent_path(), ec);
  std::ofstream out(s.laps, std::ios::app);
  const std::time_t now = std::time(nullptr);
  char date[32];
  std::strftime(date, sizeof date, "%Y-%m-%d %H:%M:%S", std::localtime(&now));
  std::ostringstream row;
  row.precision(3);
  row << std::fixed << "{\"time\": " << e.time << ", \"sectors\": [";
  const auto& splits = s.timer->last_splits();
  for (size_t i = 0; i < splits.size(); ++i) row << (i ? ", " : "") << splits[i];
  row << "], \"lap\": " << e.index << ", \"best\": " << (e.best ? "true" : "false")
      << ", \"date\": \"" << date << "\", \"length\": " << s.line.total << "}\n";
  out << row.str();
  if (!out) REXLOG_WARN("FM4 lap: could not add the lap to {}", s.laps.string());
}

void Hud(State& s, double now, bool force) {
  if (!force && now < s.next_hud) return;
  s.next_hud = now + 0.25;
  const Timer& t = *s.timer;
  std::string first, second;
  if (now < s.flash_until) {
    first = s.flash_first;
    second = s.flash_second;
  } else if (t.timing()) {
    first = "LAP " + std::to_string(t.lap()) + "   " + Clock(s.clock - t.lap_start());
    if (const std::string d = Signed(t.Delta(s.clock)); !d.empty()) first += "   " + d;
    second = "BEST " + Clock(t.best());
    if (t.last() > 0) second += "   LAST " + Clock(t.last());
  } else {
    first = "CROSS THE START LINE";
    second = t.best() > 0 ? "BEST " + Clock(t.best()) : "to start a timed lap";
  }
  forza::ChallengeHudShow(Upper(s.line.name), first + "\n" + second, 1.0, false);
}

void Flash(State& s, double now, std::string first, std::string second, double seconds) {
  s.flash_first = std::move(first);
  s.flash_second = std::move(second);
  s.flash_until = now + seconds;
  REXLOG_INFO("FM4 lap: {} | {}", s.flash_first, s.flash_second);
  Hud(s, now, true);
}

void Handle(State& s, const Event& e, double now) {
  const Timer& t = *s.timer;
  switch (e.kind) {
    case Kind::kStarted:
      REXLOG_INFO("FM4 lap: lap 1 started on {}", s.line.name);
      break;
    case Kind::kSplit: {
      std::string delta;
      // against the best lap's sector time, or the ghost's there (a best from an earlier session)
      const auto& best = t.best_splits();
      double ref = size_t(e.index) < best.size() ? best[size_t(e.index)] : -1;
      if (ref < 0 && size_t(e.index) < s.line.splits.size())
        ref = TimeAt(t.ghost(), s.line.splits[size_t(e.index)] * s.line.total);
      if (ref > 0) delta = "  " + Signed(e.time - ref);
      Flash(s, now, "SECTOR " + std::to_string(e.index + 1) + "   " + Clock(e.time) + delta,
            "LAP " + std::to_string(t.lap()) + "   BEST " + Clock(t.best()), 3);
      break;
    }
    case Kind::kLap:
      Flash(s, now, "LAP " + std::to_string(e.index) + "   " + Clock(e.time),
            e.best ? "NEW BEST LAP" : "BEST " + Clock(t.best()), 5);
      Save(s, e);
      if (e.best) SaveGhost(s);
      break;
    case Kind::kEnded:
      Flash(s, now, "LAP NOT COUNTED", e.why, 3);
      break;
    case Kind::kNone:
      break;
  }
}

}  // namespace

void Configure(const std::filesystem::path& lap_file, const std::filesystem::path& laps_file) {
  std::ifstream in(lap_file, std::ios::binary);
  if (!in) {
    REXLOG_WARN("FM4 lap: no {} (import the circuit again for lap times)", lap_file.string());
    return;
  }
  std::stringstream text;
  text << in.rdbuf();
  auto s = std::make_unique<State>();
  if (!Parse(text.str(), s->line)) {
    REXLOG_WARN("FM4 lap: {} is not a lap line", lap_file.string());
    return;
  }
  s->timer = std::make_unique<Timer>(s->line);
  s->laps = laps_file;
  if (!laps_file.empty()) {
    s->timer->SetBest(BestOf(laps_file));
    LoadGhost(*s);
  }
  REXLOG_INFO("FM4 lap: {}: {} points, {:.0f} m round the line ({:.0f} m in FM4's database), {} sector(s){}{}",
              s->line.name, s->line.points.size(), s->line.total, s->line.length, s->line.splits.size() + 1,
              s->timer->best() > 0 ? ", best " + Clock(s->timer->best()) : "",
              s->timer->ghost().empty() ? "" : ", with its ghost");
  std::lock_guard lock(mutex);
  delete state;
  state = s.release();
}

void WorldFrame(uint8_t* base, bool driving, bool rewinding) {
  static const bool from_env = [] {
    const char* v = std::getenv("FORZA_FM4_LAP");
    if (!v || !*v) return false;
    std::string spec = v;
    const auto bar = spec.find('|');
    Configure(spec.substr(0, bar), bar == std::string::npos ? fs::path() : fs::path(spec.substr(bar + 1)));
    return true;
  }();
  (void)from_env;
  std::lock_guard lock(mutex);
  if (!state) return;
  State& s = *state;
  const double now = Steady();
  const double dt = s.last_frame > 0 ? now - s.last_frame : 0;
  s.last_frame = now;
  if (rewinding && !s.rewinding) Handle(s, s.timer->Invalidate("rewind used"), now);
  s.rewinding = rewinding;
  if (!driving || rewinding) return;
  s.clock += std::min(dt, 0.1);
  float pos[3], fwd[3];
  if (!forza::telemetry::LocalCarPose(base, pos, fwd)) {
    s.timer->Lost();
    return;
  }
  Handle(s, s.timer->Update(s.clock, pos), now);
  Hud(s, now, false);
}

}  // namespace forza::fm4_lap
