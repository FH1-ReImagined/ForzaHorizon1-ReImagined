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

  std::puts("car_part_fallback_test: ok");
  return 0;
}
