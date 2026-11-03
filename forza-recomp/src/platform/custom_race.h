// forza - Custom Race: replay a won festival or street event with your own settings
// (docs/custom-race.md).
//
// On a career event's title card (CCareerEventDescription, TU4 only), once the event has
// been won and while playing offline, SELECT opens a Custom Race page built on the game's
// own difficulty-style layout (like the in-game Graphics menu): car class, laps (circuit
// races), opponents (1-7) and time of day. A confirms: the event's database rows are
// shadowed for the race by TEMP tables (Events, Races, EventParticipants, EventRestrictions,
// EventRecommendedCars) and the event's car registration opens, filtered to the chosen
// class, then the normal career race loads. B returns to the title card and cancels.
// Off by default: cvar custom_race (launcher: Gameplay > Custom Race).
//
// This header is the game-independent part (option lists, clamping, the SQL); it has no SDK
// dependency so tests/custom_race_test.cpp builds it on its own. The hooks are in
// src/hooks/custom_race.cpp.
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

struct PPCContext;

namespace forza::custom_race {

struct ClassChoice {
  uint32_t id;        // CarClasses.Id / Data_Car.ClassID / Events.TargetClass
  const char* label;  // CarClasses code (CLASS_E ... CLASS_R1)
};
inline constexpr std::array<ClassChoice, 9> kClasses{{{1, "E"},
                                                      {2, "D"},
                                                      {3, "C"},
                                                      {4, "B"},
                                                      {5, "A"},
                                                      {6, "S"},
                                                      {7, "R3"},
                                                      {8, "R2"},
                                                      {9, "R1"}}};

struct TimeChoice {
  uint32_t seconds;  // Events.TimeOfDayStart (seconds after midnight); 0 keeps the event's
  const char* label;
};
inline constexpr std::array<TimeChoice, 7> kTimes{{{0, "EVENT DEFAULT"},
                                                   {23400, "DAWN"},
                                                   {32400, "MORNING"},
                                                   {45000, "MIDDAY"},
                                                   {55800, "AFTERNOON"},
                                                   {70200, "SUNSET"},
                                                   {86100, "MIDNIGHT"}}};

inline constexpr uint32_t kMinOpponents = 1, kMaxOpponents = 7;
inline constexpr uint32_t kMinLaps = 1, kMaxLaps = 10;

// CareerTypeId of the events this works on: 1 festival point-to-point, 2 festival circuit,
// 4 street race (point-to-point with traffic).
inline bool SupportedType(uint32_t career_type) {
  return career_type == 1 || career_type == 2 || career_type == 4;
}
inline bool IsCircuit(uint32_t career_type) { return career_type == 2; }

// What the event itself says (read when the title card binds).
struct EventInfo {
  uint32_t id = 0;
  uint32_t career_type = 0;
  uint32_t target_class = 4;
  uint32_t laps = 1;
  uint32_t drivers = 7;
  uint32_t time_of_day = 0;
};

// The player's choices.
struct Settings {
  uint32_t event = 0;
  uint32_t class_id = 4;
  uint32_t laps = 1;
  uint32_t opponents = 7;
  uint32_t time_of_day = 0;  // seconds, 0 = event default
};

inline uint32_t Clamp(uint32_t v, uint32_t lo, uint32_t hi) { return v < lo ? lo : v > hi ? hi : v; }

// Every value within what the page offers: class and laps and opponents clamped, a time of
// day that is not one of kTimes becomes the event default.
inline Settings Sanitize(Settings s) {
  s.class_id = Clamp(s.class_id, kClasses.front().id, kClasses.back().id);
  s.laps = Clamp(s.laps, kMinLaps, kMaxLaps);
  s.opponents = Clamp(s.opponents, kMinOpponents, kMaxOpponents);
  bool listed = false;
  for (const auto& t : kTimes) listed = listed || t.seconds == s.time_of_day;
  if (!listed) s.time_of_day = 0;
  return s;
}

inline Settings Defaults(const EventInfo& e) {
  Settings s;
  s.event = e.id;
  s.class_id = e.target_class;
  s.laps = e.laps;
  s.opponents = e.drivers;
  s.time_of_day = 0;
  return Sanitize(s);
}

// The SQL that removes the shadow again (also the first statements of ArmStatements).
inline std::vector<std::string> DisarmStatements() {
  std::vector<std::string> sql;
  for (const char* t : {"Events", "Races", "EventParticipants", "EventRestrictions",
                        "EventRecommendedCars", "CustomRacePool", "CustomRaceDrivers"}) {
    sql.push_back(std::string("DROP TABLE IF EXISTS temp.") + t);
  }
  return sql;
}

// The SQL that shadows the event for the race, run in order, one statement per call. TEMP
// tables are searched before the main database, so every query the game makes about the
// event (title card, car registration's class filter, the dealer, the race setup, the CPU
// grid) sees the custom values; other events are copied unchanged. Written for the game's
// SQLite 3.6.19 (no CTEs). The laps change only for a circuit.
inline std::vector<std::string> ArmStatements(Settings s, bool circuit) {
  s = Sanitize(s);
  const std::string e = std::to_string(s.event), c = std::to_string(s.class_id);
  const uint32_t d = s.opponents;
  std::vector<std::string> sql = DisarmStatements();
  // The event's named CPU drivers (before EventParticipants is shadowed).
  sql.push_back("CREATE TEMP TABLE CustomRaceDrivers AS SELECT AIPlayerID, HorizonEventID FROM "
                "EventParticipants WHERE EventID=" + e + " ORDER BY Priority, ID");
  // Their cars: the class's installed, selectable cars, shuffled (any installed car of the
  // class when none is selectable).
  sql.push_back("CREATE TEMP TABLE CustomRacePool AS SELECT Id FROM Data_Car WHERE ClassID=" + c +
                " AND IsCarInstalled(Id) AND IsSelectable=1 ORDER BY RANDOM()");
  sql.push_back("INSERT INTO temp.CustomRacePool SELECT Id FROM Data_Car WHERE ClassID=" + c +
                " AND IsCarInstalled(Id) AND (SELECT COUNT(*) FROM temp.CustomRacePool)=0 "
                "ORDER BY RANDOM()");
  sql.push_back("CREATE TEMP TABLE Events AS SELECT * FROM Events");
  std::string events = "UPDATE temp.Events SET TargetClass=" + c +
                       ", NumberOfDrivers=" + std::to_string(d) +
                       ", PlayerGridPosition=CASE WHEN PlayerGridPosition>" +
                       std::to_string(d + 1) + " THEN " + std::to_string(d + 1) +
                       " ELSE PlayerGridPosition END";
  if (s.time_of_day) events += ", TimeOfDayStart=" + std::to_string(s.time_of_day);
  sql.push_back(events + " WHERE Id=" + e);
  sql.push_back("CREATE TEMP TABLE Races AS SELECT * FROM Races");
  if (circuit) {
    sql.push_back("UPDATE temp.Races SET NumLaps=" + std::to_string(s.laps) + " WHERE EventId=" + e);
  }
  // No make/year/... restrictions: the class is the only one. The event keeps its row (the car
  // registration and the car check read it, and crash without it) with an empty field mask:
  // the first 8 hex digits are the mask, the next 2 a byte the row always has.
  sql.push_back("CREATE TEMP TABLE EventRestrictions AS SELECT * FROM EventRestrictions");
  sql.push_back("UPDATE temp.EventRestrictions SET Value='00000000'||substr(Value,9,2) WHERE "
                "EventID=" + e);
  sql.push_back("CREATE TEMP TABLE EventParticipants AS SELECT * FROM EventParticipants WHERE "
                "EventID<>" + e);
  // BUY NEW CAR: the dealer lists the event's EventRecommendedCars (then the class filter);
  // recommend every buyable car of the chosen class instead of the event's own picks.
  sql.push_back("CREATE TEMP TABLE EventRecommendedCars AS SELECT * FROM EventRecommendedCars "
                "WHERE EventID<>" + e);
  sql.push_back("INSERT INTO temp.EventRecommendedCars (EventID,CarID,FirstChoice) SELECT " + e +
                ",Id,0 FROM Data_Car WHERE ClassID=" + c +
                " AND IsSelectable=1 AND IsUnicorn=0 AND IsCarInstalled(Id) ORDER BY "
                "PerformanceIndex DESC");
  for (uint32_t n = 0; n < d; ++n) {
    const std::string k = std::to_string(n);
    sql.push_back(
        "INSERT INTO temp.EventParticipants (ID,EventID,Priority,Ordinal,ColorSequence,BucketId,"
        "HorizonEventID,AIPlayerID,LiveryLevel,TuningLevel) SELECT " +
        std::to_string(900000 + n) + "," + e + "," + k + ",p.Id," + std::to_string(n + 1) +
        ",0,COALESCE((SELECT HorizonEventID FROM temp.CustomRaceDrivers LIMIT 1),''),"
        "COALESCE((SELECT AIPlayerID FROM temp.CustomRaceDrivers WHERE rowid=" + k +
        "%(SELECT COUNT(*) FROM temp.CustomRaceDrivers)+1)," + std::to_string(n + 1) +
        "),0,0 FROM temp.CustomRacePool p WHERE p.rowid=" + k +
        "%(SELECT COUNT(*) FROM temp.CustomRacePool)+1");
  }
  return sql;
}

// --- Hooks (src/hooks/custom_race.cpp, TU4 only; no-ops in the disc build) ----------

// The difficulty-style screen the Graphics menu also uses (src/hooks/graphics_menu.cpp
// owns its hooks and offers each call here first).
bool TryBind(PPCContext& ctx, uint8_t* base);
bool TryNotify(PPCContext& ctx, uint8_t* base);
bool TryDestroy(PPCContext& ctx, uint8_t* base);
bool TryInput(PPCContext& ctx, uint8_t* base);
// Every main-loop frame (graphics_menu::Tick).
void Tick(PPCContext& ctx, uint8_t* base);
}  // namespace forza::custom_race

namespace forza {
// Player 1's pad (kernel/input_autopress.cpp, not called while another overlay blocks the
// pad): watches SELECT for the title card and returns the buttons the game should see. While
// a confirmed Custom Race waits for the career title card, and only while that card is the
// active screen, it adds the A press that enters the event.
uint16_t CustomRacePad(uint16_t buttons);
}  // namespace forza
