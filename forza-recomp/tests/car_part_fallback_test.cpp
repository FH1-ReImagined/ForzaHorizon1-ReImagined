// A garage car's foreign upgrade part replaced by its stock part (src/platform/car_part_fallback.h,
// #836): which creator makes which part, the stock-part query and the log's part names. Build
// from the repository root:
//   g++ -std=c++20 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/car_part_fallback_test.cpp
//       -o car-part-fallback-test && ./car-part-fallback-test
#include "platform/car_part_fallback.h"

#include <cstdio>
#include <cstdlib>
#include <string>

namespace cpf = forza::car_part_fallback;
using cpf::Creator;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}
}  // namespace

int main() {
  // Every type has exactly one creator, as the physics loader calls them (types 0-11, 12-29,
  // 42, 30-33, 34-41).
  for (uint32_t type = 0; type < cpf::kTables.size(); ++type) {
    int creators = 0;
    for (Creator c : {Creator::kCar, Creator::kEngine, Creator::kMotor, Creator::kDrivetrain,
                      Creator::kCarBody}) {
      creators += cpf::Handles(c, type);
    }
    Check(creators == 1, "one creator per part type");
  }
  Check(!cpf::Handles(Creator::kCar, 43) && !cpf::Handles(Creator::kMotor, 43), "no type 43");
  Check(!cpf::Handles(Creator::kCar, uint32_t(-1)), "no negative type");
  Check(cpf::Handles(Creator::kDrivetrain, 30) && cpf::Handles(Creator::kDrivetrain, 33),
        "the drivetrain parts");
  Check(cpf::Handles(Creator::kEngine, 12) && cpf::Handles(Creator::kEngine, 29),
        "the engine parts");
  Check(cpf::Handles(Creator::kCarBody, 34) && cpf::Handles(Creator::kCarBody, 41),
        "the car body parts");

  // The tables at the type boundaries (the game's table at TU4 0x8338D650).
  Check(cpf::kTables[0] == "List_UpgradeEngine", "type 0");
  Check(cpf::kTables[11] == "List_UpgradeRimSizeRear", "type 11");
  Check(cpf::kTables[12] == "List_UpgradeEngineCamshaft", "type 12");
  Check(cpf::kTables[29] == "List_UpgradeEngineIntercooler", "type 29");
  Check(cpf::kTables[30] == "List_UpgradeDrivetrainClutch", "type 30");
  Check(cpf::kTables[33] == "List_UpgradeDrivetrainDifferential", "type 33");
  Check(cpf::kTables[34] == "List_UpgradeCarBodyFrontBumper", "type 34");
  Check(cpf::kTables[41] == "List_UpgradeCarBodyChassisStiffness", "type 41");
  Check(cpf::kTables[42] == "List_UpgradeMotorParts", "type 42");

  // The repro of #836: the Aventador (drivetrain 806) with clutch 3000 (drivetrain 3).
  Check(cpf::StockPartSql(Creator::kDrivetrain, 30, 806) ==
            "SELECT Id FROM List_UpgradeDrivetrainClutch WHERE DrivetrainID=806 AND IsStock=1 "
            "ORDER BY Id LIMIT 1",
        "the clutch's stock query");
  Check(cpf::StockPartSql(Creator::kCar, 0, 1516) ==
            "SELECT Id FROM List_UpgradeEngine WHERE Ordinal=1516 AND IsStock=1 ORDER BY Id LIMIT 1",
        "the engine's stock query (by car)");
  Check(cpf::StockPartSql(Creator::kEngine, 24, 788) ==
            "SELECT Id FROM List_UpgradeEngineTurboSingle WHERE EngineID=788 AND IsStock=1 "
            "ORDER BY Id LIMIT 1",
        "an engine part's stock query");
  Check(cpf::StockPartSql(Creator::kMotor, 42, 5) ==
            "SELECT Id FROM List_UpgradeMotorParts WHERE MotorID=5 AND IsStock=1 ORDER BY Id LIMIT 1",
        "a motor part's stock query");
  Check(cpf::StockPartSql(Creator::kCarBody, 36, -2).find("CarBodyID=-2 AND") != std::string::npos,
        "a negative parent");
  Check(cpf::StockPartSql(Creator::kDrivetrain, 12, 806).empty(), "not the creator's type");
  Check(cpf::StockPartSql(Creator::kMotor, 99, 1).empty(), "an unknown type");

  // No stock part (an engine without a turbo): the car's "no such part".
  Check(cpf::Replacement(true, 806000) == 806000, "the stock part");
  Check(cpf::Replacement(false, 123) == cpf::kNoPart && cpf::kNoPart == -1, "no stock part");

  Check(cpf::PartName(30) == "Clutch", "Clutch");
  Check(cpf::PartName(31) == "Transmission", "Transmission");
  Check(cpf::PartName(0) == "Engine", "Engine");
  Check(cpf::PartName(1) == "Drivetrain", "Drivetrain");
  Check(cpf::PartName(12) == "Camshaft", "Camshaft");
  Check(cpf::PartName(36) == "Hood", "Hood");
  Check(cpf::PartName(42) == "MotorParts", "MotorParts");
  Check(cpf::PartName(43).empty(), "no name past the table");

  // The parts every car, engine, drivetrain and car body has a stock row of (both databases).
  for (uint32_t type : {0u, 1u, 2u, 4u, 11u, 12u, 20u, 23u, 30u, 33u, 34u, 41u}) {
    Check(cpf::Required(type), "a required part");
  }
  for (uint32_t type : {3u, 21u, 22u, 24u, 25u, 26u, 27u, 28u, 29u, 42u, 43u}) {
    Check(!cpf::Required(type), "an optional part (motor, manifold, restrictor, turbos)");
  }

  // #862: the RUF Yellowbird (1516) with a drivetrain part the database doesn't have; its
  // drivetrain parts were given DrivetrainID 2147483647, the empty record's value.
  Check(cpf::ParentType(Creator::kDrivetrain) == 1 && cpf::ParentType(Creator::kEngine) == 0 &&
            cpf::ParentType(Creator::kCarBody) == 2 && cpf::ParentType(Creator::kMotor) == 3,
        "the car-level part of each sub-part creator");
  Check(cpf::ParentSql(Creator::kDrivetrain, 2147483647) ==
            "SELECT Id FROM List_UpgradeDrivetrain WHERE DrivetrainID=2147483647 LIMIT 1",
        "the parent's row");
  Check(cpf::ParentSql(Creator::kCar, 1516).empty(), "the car's own parts have no parent part");
  Check(cpf::CarParentSql(Creator::kDrivetrain, 1516) ==
            "SELECT DrivetrainID FROM List_UpgradeDrivetrain WHERE Ordinal=1516 AND IsStock=1 "
            "ORDER BY Id LIMIT 1",
        "the car's stock drivetrain");
  Check(cpf::CarParentSql(Creator::kEngine, 1328) ==
            "SELECT EngineID FROM List_UpgradeEngine WHERE Ordinal=1328 AND IsStock=1 ORDER BY Id "
            "LIMIT 1",
        "the car's stock engine (the Veyron's, EngineID 788)");
  Check(cpf::CarParentSql(Creator::kCarBody, 1516).find("SELECT CarBodyID FROM "
                                                        "List_UpgradeCarBody WHERE Ordinal=1516") == 0,
        "the car's stock car body");
  Check(cpf::CarParentSql(Creator::kCar, 1516).empty(), "no car parent for the car's parts");

  Check(cpf::UseCarParent(Creator::kDrivetrain, 30, false), "unknown drivetrain: the car's");
  Check(cpf::UseCarParent(Creator::kDrivetrain, 33, true), "a drivetrain without a differential");
  Check(cpf::UseCarParent(Creator::kEngine, 25, false), "a turbo of an unknown engine");
  Check(!cpf::UseCarParent(Creator::kEngine, 25, true), "an engine without a twin turbo has none");
  Check(cpf::UseCarParent(Creator::kEngine, 12, true), "an engine without a camshaft");
  Check(!cpf::UseCarParent(Creator::kCar, 1, false), "the car's parts: by the car");
  Check(!cpf::UseCarParent(Creator::kDrivetrain, 12, false), "not the creator's type");

  // The log lines. #836's stay as docs/modding.md quotes them.
  cpf::Outcome o;
  o.car = 1398;
  o.creator = Creator::kDrivetrain;
  o.type = 30;
  o.part = 3000;
  o.parent = o.used_parent = 806;
  o.found = true;
  o.replacement = 806000;
  Check(cpf::Describe(o) == "Car 1398: its Clutch part 3000 doesn't belong to its DrivetrainID "
                            "806: loaded with the stock part 806000 instead (#836)",
        "#836's line");
  o.creator = Creator::kEngine;
  o.type = 25;
  o.part = 1000;
  o.parent = o.used_parent = 788;
  o.found = false;
  o.replacement = cpf::kNoPart;
  Check(cpf::Describe(o) == "Car 1398: its TurboTwin part 1000 doesn't belong to its EngineID "
                            "788, which has no stock TurboTwin: loaded without one (#836)",
        "no stock turbo");
  o.made = false;
  Check(cpf::Describe(o).find("loaded without one, which failed too (#836)") != std::string::npos,
        "a retry that failed too");

  cpf::Outcome drivetrain;
  drivetrain.car = drivetrain.parent = drivetrain.used_parent = 1516;
  drivetrain.creator = Creator::kCar;
  drivetrain.type = 1;
  drivetrain.part = 1516001;
  drivetrain.missing = true;
  drivetrain.found = true;
  drivetrain.replacement = 1516000;
  Check(cpf::Describe(drivetrain) == "Car 1516: its Drivetrain part 1516001 is not in the game's "
                                     "database: loaded with the stock part 1516000 instead (#862)",
        "a drivetrain the database doesn't have");

  cpf::Outcome clutch;
  clutch.car = 1516;
  clutch.creator = Creator::kDrivetrain;
  clutch.type = 30;
  clutch.part = 842003;
  clutch.parent = 2147483647;
  clutch.parent_known = false;
  clutch.used_parent = 923;
  clutch.found = true;
  clutch.replacement = 923000;
  Check(cpf::Describe(clutch) ==
            "Car 1516: its Clutch part 842003 doesn't belong to its DrivetrainID 2147483647, which "
            "is not in the game's database: loaded with the stock part 923000 of the car's "
            "DrivetrainID 923 instead (#862)",
        "#862's clutch, from the car's own drivetrain");
  clutch.parent_known = true;
  clutch.parent = 842;
  Check(cpf::Describe(clutch).find("DrivetrainID 842: loaded with the stock part 923000 of the "
                                   "car's DrivetrainID 923 instead (#862)") != std::string::npos,
        "a drivetrain without a stock clutch");
  clutch.parent_known = false;
  clutch.parent = 2147483647;
  clutch.found = false;
  clutch.used_parent = clutch.parent;
  clutch.replacement = cpf::kNoPart;
  Check(cpf::Describe(clutch).find("which is not in the game's database: loaded without one (#862)") !=
            std::string::npos,
        "no car to take the part from");

  cpf::Outcome missing_clutch = clutch;
  missing_clutch.parent = missing_clutch.used_parent = 923;
  missing_clutch.parent_known = true;
  missing_clutch.missing = true;
  missing_clutch.part = 923099;
  missing_clutch.found = true;
  missing_clutch.replacement = 923000;
  Check(cpf::Describe(missing_clutch) == "Car 1516: its Clutch part 923099 is not in the game's "
                                         "database: loaded with the stock part 923000 instead (#862)",
        "a clutch the database doesn't have");

  std::puts("car_part_fallback_test: ok");
  return 0;
}
