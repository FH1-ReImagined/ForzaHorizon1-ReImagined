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

}  // namespace forza::car_part_fallback
