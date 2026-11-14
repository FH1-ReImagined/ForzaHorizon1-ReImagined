// forza - a garage car whose upgrade part belongs to another car loads with the stock part
// (src/hooks/car_part_fallback.cpp, #836). No runtime in here:
// forza-recomp/tests/car_part_fallback_test.cpp.
//
// The car physics loader (TU4 sub_82CB4500, base sub_82BF4FB0) builds one record per upgrade
// part of the car (the part ids are the Career_Garage row's columns) through five creators,
// each "SELECT * FROM <table> WHERE Id=<part>" and a check that the row's parent column is the
// one the car has:
//
//   creator (TU4 / base)                              types   parent column
//   CreateCarPartUpgradeRecord        82CACA70 / 82BED520   0-11    Ordinal (the car)
//   CreateEnginePartUpgradeRecord     82CACCD8 / 82BED788  12-29    EngineID
//   CreateMotorPartUpgradeRecord      82CACF40 / 82BED9F0    42     MotorID
//   CreateDrivetrainPartUpgradeRecord 82CAD1A8 / 82BEDC58  30-33    DrivetrainID
//   CreateCarBodyPartUpgradeRecord    82CAD410 / 82BEDEC0  34-41    CarBodyID
//
// When the check fails the creator logs "ERROR: Create...: ... doesn't match part's" (a
// guest string nobody sees), releases the record and leaves it NULL; the loader dereferences it
// (a clutch: sub_82CAAB60, lr 82CAAB8C), so pressing START on the title screen ended the game
// with exit code 134 for a save with such a car as its current car. Only an edited save or a
// mod's database makes one (the official data has none). A part id that matches no row (-1, a
// part the car doesn't have) gives an empty record, which the loader handles.
//
// The fallback asks the table for the part of the expected parent marked IsStock (every
// table has the column; the official data has one stock row per parent at most) and runs the
// creator again with it; a parent without a stock part (no turbo on that engine) gets -1, the
// car's "no such part". The type indexes the game's table of part tables (TU4 0x8338D650,
// 16-byte entries, the name first).
//
// The loader takes the sub-parts' parent from the car-level part's record: EngineID from the
// Engine record (it skips the engine parts when that record has no row), DrivetrainID from the
// Drivetrain record and CarBodyID from the CarBody record without such a check. A record with
// no row reads 2147483647 for any column, so a Drivetrain part id the database doesn't have (a
// removed mod's drivetrain swap) gave every drivetrain part that parent; none of them matched
// it, the fallback found no stock part for it and loaded the car without a clutch, gearbox,
// driveline and differential, and the loading screen after START never ended (#862). So a part
// the car can't go without (Required: every car, drivetrain, car body or engine of the disc and
// the TU4 database has a stock row for it) whose record is empty is loaded as the stock part
// too, and a sub-part whose parent is not in the database, or that parent has no stock part of
// a required kind, takes the stock part of the car's own stock parent (CarParentSql).

#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>

namespace forza::car_part_fallback {

enum class Creator { kCar, kEngine, kMotor, kDrivetrain, kCarBody };

// The game's part tables by upgrade type (the creators' r5), in its order.
inline constexpr std::array<std::string_view, 43> kTables = {
    "List_UpgradeEngine",
    "List_UpgradeDrivetrain",
    "List_UpgradeCarBody",
    "List_UpgradeMotor",
    "List_UpgradeBrakes",
    "List_UpgradeSpringDamper",
    "List_UpgradeAntiSwayFront",
    "List_UpgradeAntiSwayRear",
    "List_UpgradeTireCompound",
    "List_UpgradeRearWing",
    "List_UpgradeRimSizeFront",
    "List_UpgradeRimSizeRear",
    "List_UpgradeEngineCamshaft",
    "List_UpgradeEngineValves",
    "List_UpgradeEngineDisplacement",
    "List_UpgradeEnginePistonsCompression",
    "List_UpgradeEngineFuelSystem",
    "List_UpgradeEngineIgnition",
    "List_UpgradeEngineExhaust",
    "List_UpgradeEngineIntake",
    "List_UpgradeEngineFlywheel",
    "List_UpgradeEngineManifold",
    "List_UpgradeEngineRestrictorPlate",
    "List_UpgradeEngineOilCooling",
    "List_UpgradeEngineTurboSingle",
    "List_UpgradeEngineTurboTwin",
    "List_UpgradeEngineTurboQuad",
    "List_UpgradeEngineCSC",
    "List_UpgradeEngineDSC",
    "List_UpgradeEngineIntercooler",
    "List_UpgradeDrivetrainClutch",
    "List_UpgradeDrivetrainTransmission",
    "List_UpgradeDrivetrainDriveline",
    "List_UpgradeDrivetrainDifferential",
    "List_UpgradeCarBodyFrontBumper",
    "List_UpgradeCarBodyRearBumper",
    "List_UpgradeCarBodyHood",
    "List_UpgradeCarBodySideSkirt",
    "List_UpgradeCarBodyTireWidthFront",
    "List_UpgradeCarBodyTireWidthRear",
    "List_UpgradeCarBodyWeight",
    "List_UpgradeCarBodyChassisStiffness",
    "List_UpgradeMotorParts",
};

// The part id of a part the car doesn't have (Career_Garage stores -1): no row, empty record.
inline constexpr int32_t kNoPart = -1;

inline constexpr std::string_view ParentColumn(Creator creator) {
  switch (creator) {
    case Creator::kCar: return "Ordinal";
    case Creator::kEngine: return "EngineID";
    case Creator::kMotor: return "MotorID";
    case Creator::kDrivetrain: return "DrivetrainID";
    case Creator::kCarBody: return "CarBodyID";
  }
  return {};
}

// The creator's part types, [first, last].
inline constexpr bool Handles(Creator creator, uint32_t type) {
  switch (creator) {
    case Creator::kCar: return type <= 11;
    case Creator::kEngine: return type >= 12 && type <= 29;
    case Creator::kMotor: return type == 42;
    case Creator::kDrivetrain: return type >= 30 && type <= 33;
    case Creator::kCarBody: return type >= 34 && type <= 41;
  }
  return false;
}

// "Clutch" for List_UpgradeDrivetrainClutch: the part's name in the log.
inline std::string_view PartName(uint32_t type) {
  if (type >= kTables.size()) return {};
  std::string_view name = kTables[type];
  name.remove_prefix(std::string_view("List_Upgrade").size());
  for (std::string_view group : {"Drivetrain", "CarBody", "Engine"}) {
    if (name.size() > group.size() && name.substr(0, group.size()) == group) {
      name.remove_prefix(group.size());
      break;
    }
  }
  return name;
}

// The query for the expected parent's stock part ("" when the creator doesn't make `type`).
inline std::string StockPartSql(Creator creator, uint32_t type, int32_t parent) {
  if (!Handles(creator, type)) return {};
  std::string sql = "SELECT Id FROM ";
  sql += kTables[type];
  sql += " WHERE ";
  sql += ParentColumn(creator);
  sql += "=" + std::to_string(parent) + " AND IsStock=1 ORDER BY Id LIMIT 1";
  return sql;
}

// The part the creator runs again with: the stock part, or none.
inline constexpr int32_t Replacement(bool found, int32_t stock) { return found ? stock : kNoPart; }

// A part every car has: in both game databases each car has a stock row of types 0-2 (Engine,
// Drivetrain, CarBody) and 4-11, each engine of 12-20 and 23 (camshaft to flywheel, oil
// cooling), each drivetrain of 30-33 and each car body of 34-41. The manifold, restrictor plate,
// turbos, superchargers and intercooler (21, 22, 24-29) and the motor (3, 42; no rows) are
// optional: an engine without them has none.
inline constexpr bool Required(uint32_t type) {
  return type <= 2 || (type >= 4 && type <= 20) || type == 23 || (type >= 30 && type <= 41);
}

// The car-level part a sub-part creator's parent is a column of ("" for the car's own parts).
inline constexpr uint32_t ParentType(Creator creator) {
  switch (creator) {
    case Creator::kEngine: return 0;
    case Creator::kDrivetrain: return 1;
    case Creator::kCarBody: return 2;
    case Creator::kMotor: return 3;
    case Creator::kCar: break;
  }
  return uint32_t(-1);
}

// Whether the parent is in the database: "SELECT Id FROM List_UpgradeDrivetrain WHERE
// DrivetrainID=923 LIMIT 1" ("" for the car's own parts, whose parent is the car).
inline std::string ParentSql(Creator creator, int32_t parent) {
  const uint32_t type = ParentType(creator);
  if (type >= kTables.size()) return {};
  std::string sql = "SELECT Id FROM ";
  sql += kTables[type];
  sql += " WHERE ";
  sql += ParentColumn(creator);
  sql += "=" + std::to_string(parent) + " LIMIT 1";
  return sql;
}

// The parent the car's own stock part has: "SELECT DrivetrainID FROM List_UpgradeDrivetrain
// WHERE Ordinal=1516 AND IsStock=1 ORDER BY Id LIMIT 1" (923 for the RUF Yellowbird).
inline std::string CarParentSql(Creator creator, int32_t car) {
  const uint32_t type = ParentType(creator);
  if (type >= kTables.size()) return {};
  std::string sql = "SELECT ";
  sql += ParentColumn(creator);
  sql += " FROM ";
  sql += kTables[type];
  sql += " WHERE Ordinal=" + std::to_string(car) + " AND IsStock=1 ORDER BY Id LIMIT 1";
  return sql;
}

// Whether a sub-part with no stock part of its expected parent takes the car's own parent's
// instead: when that parent is not in the database (2147483647, the empty record's value) or
// the part is one every parent has.
inline constexpr bool UseCarParent(Creator creator, uint32_t type, bool parent_known) {
  return creator != Creator::kCar && Handles(creator, type) && (!parent_known || Required(type));
}

// What the fallback did, for the log line.
struct Outcome {
  int32_t car = 0;
  Creator creator = Creator::kCar;
  uint32_t type = 0;
  int32_t part = kNoPart;
  int32_t parent = 0;           // the parent the loader passed
  bool missing = false;         // the part has no row (an empty record), not another parent's
  bool parent_known = true;     // the parent passed is in the database
  int32_t used_parent = 0;      // the parent of the part loaded instead
  bool found = false;           // a stock part was found (else the car loads without one)
  int32_t replacement = kNoPart;
  bool made = true;             // the creator made a record with it
};

// "Car 1398: its Clutch part 3000 doesn't belong to its DrivetrainID 806: loaded with the stock
// part 806000 instead (#836)" and the other cases' lines.
inline std::string Describe(const Outcome& o) {
  const std::string name(PartName(o.type));
  const std::string column(ParentColumn(o.creator));
  std::string line = "Car " + std::to_string(o.car) + ": its " + name + " part " +
                     std::to_string(o.part);
  if (o.missing) {
    line += " is not in the game's database";
  } else {
    line += " doesn't belong to its " + column + " " + std::to_string(o.parent);
    if (!o.parent_known) line += ", which is not in the game's database";
  }
  const bool other_parent = o.found && o.used_parent != o.parent;
  if (o.found) {
    line += ": loaded with the stock part " + std::to_string(o.replacement);
    if (other_parent) line += " of the car's " + column + " " + std::to_string(o.used_parent);
    line += " instead";
  } else {
    if (o.parent_known && !o.missing) line += ", which has no stock " + name;
    line += ": loaded without one";
  }
  if (!o.made) line += ", which failed too";
  line += (o.missing || other_parent || !o.parent_known) ? " (#862)" : " (#836)";
  return line;
}

}  // namespace forza::car_part_fallback
