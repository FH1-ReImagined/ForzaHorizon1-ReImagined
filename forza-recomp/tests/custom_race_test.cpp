// Custom Race (src/hooks/custom_race.cpp): the option values it accepts and the SQL that
// shadows the event (src/platform/custom_race.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/custom_race_test.cpp
#include "platform/custom_race.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace cr = forza::custom_race;

#define CHECK(x)                                              \
  do {                                                        \
    if (!(x)) {                                               \
      std::fprintf(stderr, "custom race: FAIL: %s\n", #x);    \
      std::exit(1);                                           \
    }                                                         \
  } while (false)

namespace {

bool Has(const std::string& s, const std::string& part) { return s.find(part) != std::string::npos; }

// The index of the first statement containing `part`, or -1.
int Find(const std::vector<std::string>& sql, const std::string& part) {
  for (size_t i = 0; i < sql.size(); ++i)
    if (Has(sql[i], part)) return int(i);
  return -1;
}

int Count(const std::vector<std::string>& sql, const std::string& part) {
  int n = 0;
  for (const auto& s : sql) n += Has(s, part);
  return n;
}

cr::Settings Make(uint32_t cls, uint32_t laps, uint32_t opponents, uint32_t time) {
  cr::Settings s;
  s.event = 61;
  s.class_id = cls;
  s.laps = laps;
  s.opponents = opponents;
  s.time_of_day = time;
  return s;
}

void Clamping() {
  // Out-of-range values become the nearest the page offers.
  auto s = cr::Sanitize(Make(0, 0, 0, 12345));
  CHECK(s.class_id == 1 && s.laps == 1 && s.opponents == 1 && s.time_of_day == 0);
  s = cr::Sanitize(Make(99, 50, 12, 70200));
  CHECK(s.class_id == 9 && s.laps == 10 && s.opponents == 7 && s.time_of_day == 70200);
  // In range: unchanged.
  s = cr::Sanitize(Make(5, 3, 4, 45000));
  CHECK(s.class_id == 5 && s.laps == 3 && s.opponents == 4 && s.time_of_day == 45000);

  // An event's own values as defaults, clamped the same way (a 0-driver event gets 1).
  cr::EventInfo e;
  e.id = 4;
  e.target_class = 4;
  e.laps = 2;
  e.drivers = 0;
  auto d = cr::Defaults(e);
  CHECK(d.event == 4 && d.class_id == 4 && d.laps == 2 && d.opponents == 1 && d.time_of_day == 0);

  // The arming SQL clamps too: 12 opponents place 7 cars, 0 laps write 1.
  const auto sql = cr::ArmStatements(Make(5, 0, 12, 0), true);
  CHECK(Count(sql, "INSERT INTO temp.EventParticipants") == 7);
  CHECK(Find(sql, "NumberOfDrivers=7,") >= 0);
  CHECK(Find(sql, "SET NumLaps=1 WHERE EventId=61") >= 0);
}

void Order() {
  const auto sql = cr::ArmStatements(Make(6, 3, 5, 45000), true);
  const auto drop = cr::DisarmStatements();
  // Arming starts by dropping any earlier shadow, every table of it.
  CHECK(sql.size() > drop.size());
  for (size_t i = 0; i < drop.size(); ++i) CHECK(sql[i] == drop[i]);
  for (const char* t : {"Events", "Races", "EventParticipants", "EventRestrictions",
                        "EventRecommendedCars", "CustomRacePool", "CustomRaceDrivers"})
    CHECK(Find(drop, std::string("DROP TABLE IF EXISTS temp.") + t) >= 0);

  // The named drivers are read before EventParticipants is shadowed, and the car pool exists
  // before the participants are placed.
  const int drivers = Find(sql, "CREATE TEMP TABLE CustomRaceDrivers");
  const int pool = Find(sql, "CREATE TEMP TABLE CustomRacePool");
  const int shadow = Find(sql, "CREATE TEMP TABLE EventParticipants");
  const int place = Find(sql, "INSERT INTO temp.EventParticipants");
  CHECK(drivers >= 0 && pool >= 0 && shadow >= 0 && place >= 0);
  CHECK(drivers < shadow && pool < place && shadow < place);
  CHECK(Count(sql, "INSERT INTO temp.EventParticipants") == 5);

  // Each table is copied before its event row changes.
  CHECK(Find(sql, "CREATE TEMP TABLE Events") < Find(sql, "UPDATE temp.Events"));
  CHECK(Find(sql, "CREATE TEMP TABLE Races") < Find(sql, "UPDATE temp.Races"));
  CHECK(Find(sql, "CREATE TEMP TABLE EventRestrictions") <
        Find(sql, "UPDATE temp.EventRestrictions"));
  CHECK(Find(sql, "CREATE TEMP TABLE EventRecommendedCars") <
        Find(sql, "INSERT INTO temp.EventRecommendedCars"));

  // The event's own restriction row stays (with no field mask), the class is the custom one.
  CHECK(Find(sql, "CREATE TEMP TABLE EventRestrictions AS SELECT * FROM EventRestrictions") >= 0);
  CHECK(Find(sql, "Value='00000000'||substr(Value,9,2) WHERE EventID=61") >= 0);
  CHECK(Find(sql, "TargetClass=6,") >= 0);
  CHECK(Find(sql, "TimeOfDayStart=45000") >= 0);
  CHECK(Find(sql, "ClassID=6") >= 0);
  // Every statement names its event.
  CHECK(Find(sql, "WHERE Id=61") >= 0);
}

void LapsOnlyForCircuits() {
  CHECK(cr::IsCircuit(2) && !cr::IsCircuit(1) && !cr::IsCircuit(4));
  const auto circuit = cr::ArmStatements(Make(4, 5, 3, 0), true);
  const auto sprint = cr::ArmStatements(Make(4, 5, 3, 0), false);
  CHECK(Find(circuit, "UPDATE temp.Races SET NumLaps=5 WHERE EventId=61") >= 0);
  CHECK(Count(sprint, "UPDATE temp.Races") == 0);
  // Races is shadowed either way, so the table is consistent for the race.
  CHECK(Find(sprint, "CREATE TEMP TABLE Races") >= 0);
  // Event default time leaves TimeOfDayStart alone.
  CHECK(Count(sprint, "TimeOfDayStart") == 0);
}

void Types() {
  CHECK(cr::SupportedType(1) && cr::SupportedType(2) && cr::SupportedType(4));
  CHECK(!cr::SupportedType(0) && !cr::SupportedType(3) && !cr::SupportedType(7) &&
        !cr::SupportedType(11) && !cr::SupportedType(12));
}

}  // namespace

int main() {
  Clamping();
  Order();
  LapsOnlyForCircuits();
  Types();
  std::puts("custom race: OK");
  return 0;
}
