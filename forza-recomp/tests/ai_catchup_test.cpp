// Comparison isolation is confined to verified relative-performance callers.
#include "platform/ai_catchup.h"
#include <array>
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
namespace ac = forza::ai_catchup;
int main() {
  assert(ac::ParseMode("off") == ac::Mode::kOff);
  for (auto text : {"", "game", "Off", "unknown"})
    assert(ac::ParseMode(text) == ac::Mode::kGame);
  for (bool tu4 : {false,true}) {
    for (const auto& site : ac::kComparisonSites) {
      const uint32_t caller = tu4 ? site.tu4 : site.base;
      for (uint32_t offline : {3u,12u,17u,19u,20u}) {
        assert(ac::IsolatedComparison(ac::Mode::kOff,offline,site.query,caller,tu4) == site.use);
        assert(ac::IsolatedComparison(ac::Mode::kGame,offline,site.query,caller,tu4) == ac::Comparison::None);
      }
      for (uint32_t excluded : {6u,18u,UINT32_MAX})
        assert(ac::IsolatedComparison(ac::Mode::kOff,excluded,site.query,caller,tu4) == ac::Comparison::None);
      const auto other = site.query == ac::Query::LeadingHuman ? ac::Query::TrailingHuman : ac::Query::LeadingHuman;
      assert(ac::ClassifyComparison(other,caller,tu4) == ac::Comparison::None);
      assert(ac::ClassifyComparison(site.query,caller+4,tu4) == ac::Comparison::None);
      assert(ac::ClassifyComparison(site.query,caller,!tu4) == ac::Comparison::None);
    }
    for (auto query : {ac::Query::LeadingHuman,ac::Query::TrailingHuman})
      for (uint32_t unrelated : {0u,0x82300000u,0x82C52060u,UINT32_MAX})
        assert(ac::IsolatedComparison(ac::Mode::kOff,3,query,unrelated,tu4) == ac::Comparison::None);
  }
  // Retire only the live blend cache. Config values are not an input to the policy.
  std::array<uint8_t,232> controller;
  std::array<uint8_t,144> skill;
  controller.fill(0xA5); skill.fill(0x5A);
  ac::RetireCatchUpBlend(controller,skill);
  for (unsigned i=0;i<controller.size();++i) assert(controller[i] == (i==168?0:0xA5));
  for (unsigned i=0;i<skill.size();++i) assert(skill[i] == (i>=140?0:0x5A));
  auto before_controller=controller;auto before_skill=skill;
  ac::RetireCatchUpBlend(controller,skill);
  assert(controller==before_controller && skill==before_skill);
  std::array<uint8_t,8> short_record;short_record.fill(0x7B);
  ac::RetireCatchUpBlend(short_record,short_record);
  for(auto value:short_record)assert(value==0x7B);
}
