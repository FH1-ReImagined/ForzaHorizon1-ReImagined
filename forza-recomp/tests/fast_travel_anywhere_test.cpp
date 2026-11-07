// fast_travel_anywhere (src/hooks/fast_travel_anywhere.cpp, #424): what the path getter's value
// becomes for "Main/FastTravelAnywhere" (src/platform/fast_travel_anywhere.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/fast_travel_anywhere_test.cpp
#include "platform/fast_travel_anywhere.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>

namespace fta = forza::fast_travel_anywhere;

#define CHECK(x)                                                    \
  do {                                                              \
    if (!(x)) {                                                     \
      std::fprintf(stderr, "fast travel anywhere: FAIL: %s\n", #x); \
      std::exit(1);                                                 \
    }                                                               \
  } while (false)

namespace {

struct Value {
  uint8_t bytes[fta::kValueSize];
};

void StoreType(Value& v, uint32_t type) {
  const uint32_t be = __builtin_bswap32(type);
  std::memcpy(v.bytes + fta::kValueType, &be, 4);
}

// As sub_82CC0C18 leaves it (the vtable 0x821551C0), before the getter copies anything in.
Value Empty() {
  Value v{};
  const uint8_t vtable[4] = {0x82, 0x15, 0x51, 0xC0};
  std::memcpy(v.bytes, vtable, 4);
  StoreType(v, fta::kTypeEmpty);
  return v;
}

Value Bool(bool owned) {
  Value v = Empty();
  StoreType(v, fta::kTypeBool);
  v.bytes[fta::kValueData] = owned ? 1 : 0;
  return v;
}

}  // namespace

int main() {
  // The path, as the executable has it.
  const char stored[] = "Main/FastTravelAnywhere";
  CHECK(fta::PathMatches(reinterpret_cast<const uint8_t*>(stored), sizeof(stored)));
  const char other[] = "Main/FastTravelAnywherf";
  CHECK(!fta::PathMatches(reinterpret_cast<const uint8_t*>(other), sizeof(other)));
  const char longer[] = "Main/FastTravelAnywhereX";  // no terminator where it should be
  CHECK(!fta::PathMatches(reinterpret_cast<const uint8_t*>(longer), sizeof(longer)));
  CHECK(!fta::PathMatches(reinterpret_cast<const uint8_t*>(stored), sizeof(stored) - 1));

  // A profile that never had the field: an empty value, read as not owned.
  Value empty = Empty();
  CHECK(!fta::Owned(empty.bytes));
  CHECK(fta::Grant(empty.bytes));
  CHECK(fta::Owned(empty.bytes));
  CHECK(fta::LoadType(empty.bytes) == fta::kTypeBool);
  CHECK(empty.bytes[0] == 0x82 && empty.bytes[3] == 0xC0);  // the vtable stays
  for (uint32_t i = fta::kValueData + 1; i < fta::kValueData + 8; ++i) CHECK(empty.bytes[i] == 0);

  // A Title Update 4 save stores it as false until bought.
  Value no = Bool(false);
  CHECK(!fta::Owned(no.bytes));
  CHECK(fta::Grant(no.bytes));
  CHECK(fta::Owned(no.bytes));
  CHECK(!fta::Grant(no.bytes));  // once is enough

  // Bought: as it is.
  Value yes = Bool(true);
  const Value before = yes;
  CHECK(fta::Owned(yes.bytes));
  CHECK(!fta::Grant(yes.bytes));
  CHECK(std::memcmp(&yes, &before, sizeof(Value)) == 0);

  // Any other type is not the game's bool: left alone (an int, a string that owns memory).
  for (uint32_t type : {3u, 0xCu, 0xFu}) {
    Value v = Empty();
    StoreType(v, type);
    v.bytes[fta::kValueData] = 0x40;
    const Value kept = v;
    CHECK(!fta::Owned(v.bytes));
    CHECK(!fta::Grant(v.bytes));
    CHECK(std::memcmp(&v, &kept, sizeof(Value)) == 0);
  }

  std::puts("fast travel anywhere: ok");
  return 0;
}
