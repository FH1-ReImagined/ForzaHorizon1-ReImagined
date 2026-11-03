#include "../src/platform/menu_model.h"
#include "../src/platform/native_pause_pages.h"
#include "../src/platform/native_pause_layout.h"

#include <cassert>
#include <cstdio>
#include <limits>

namespace {
using namespace forza::menu;

Tile MakeTile(std::string id, Rect bounds = {0, 0, 10, 10}) {
  return {id, "Label " + id, bounds, "action:" + id};
}

Page MakePage(std::string id) {
  return {id, "Page " + id,
          {MakeTile("a"), MakeTile("b", {20, 0, 10, 10}),
           MakeTile("c", {40, 0, 10, 10})}};
}

void TabAvailability() {
  // All combinations of disabled tabs, including none available. No fixed
  // page-count assumptions: exercise nine pages and both endpoint boundaries.
  for (unsigned mask = 0; mask < (1u << 9); ++mask) {
    std::vector<Page> pages;
    for (unsigned index = 0; index < 9; ++index) {
      pages.push_back(MakePage(std::to_string(index)));
      pages.back().enabled = (mask & (1u << index)) != 0;
    }
    Controller menu;
    menu.SetPages(pages);
    if (mask == 0) {
      assert(!menu.page_id());
      assert(!menu.focused_tile_id());
      assert(!menu.ChangePage(1) && !menu.ChangePage(-1));
      assert(!menu.Activate());
      continue;
    }
    for (unsigned current = 0; current < 9; ++current) {
      if (!(mask & (1u << current))) continue;
      for (int direction : {-1, 1}) {
        menu.SelectPage(std::to_string(current));
        int expected = static_cast<int>(current);
        for (int next = expected + direction; next >= 0 && next < 9; next += direction)
          if (mask & (1u << next)) { expected = next; break; }
        assert(menu.ChangePage(direction) == (expected != static_cast<int>(current)));
        assert(menu.page_id() == std::to_string(expected));
      }
    }
  }
  Controller menu;
  auto pages = std::vector{MakePage("home"), MakePage("hidden"), MakePage("cars")};
  pages[1].visible = false;
  menu.SetPages(pages);
  assert(!menu.SelectPage("hidden"));
  assert(menu.ChangePage(7) && menu.page_id() == "cars");
  assert(menu.ChangePage(-9) && menu.page_id() == "home");
  assert(!menu.ChangePage(0));
}

void GeometryAndUnavailableTiles() {
  Controller menu;
  Page page{"home", "Home", {
      MakeTile("origin", {0, 0, 40, 100}),
      MakeTile("near-center", {45, 45, 10, 10}),
      MakeTile("near-edge", {45, 0, 10, 10}),
      MakeTile("outside", {40, -20, 10, 10}),
      MakeTile("far", {200, 0, 10, 10}),
      MakeTile("below", {0, 120, 40, 20}),
  }};
  menu.SetPages({page});
  assert(menu.Move(Direction::Right) && menu.focused_tile_id() == "near-center");
  assert(menu.Move(Direction::Left) && menu.focused_tile_id() == "origin");
  assert(menu.Move(Direction::Down) && menu.focused_tile_id() == "below");
  assert(menu.Move(Direction::Up) && menu.focused_tile_id() == "origin");
  assert(!menu.Move(Direction::Left));

  // Facing edges beat center distance for different tile widths.
  page.tiles = {MakeTile("origin"), MakeTile("wide", {12, 0, 100, 10}),
                MakeTile("small", {20, 0, 5, 10})};
  menu.SetPages({page});
  assert(menu.Move(Direction::Right) && menu.focused_tile_id() == "wide");

  // Same-beam candidates beat diagonal candidates, including edge-touching
  // rectangles; geometric ties use stable IDs rather than list order.
  page.tiles = {MakeTile("origin"), MakeTile("z", {30, 0, 10, 10}),
                MakeTile("a", {30, 0, 10, 10}),
                MakeTile("diagonal", {10, 10, 10, 10})};
  menu.SetPages({page});
  menu.FocusTile("origin");
  assert(menu.Move(Direction::Right) && menu.focused_tile_id() == "a");
  std::reverse(page.tiles.begin(), page.tiles.end());
  menu.SetPages({page});
  menu.FocusTile("origin");
  assert(menu.Move(Direction::Right) && menu.focused_tile_id() == "a");

  page.tiles = {MakeTile("origin"), MakeTile("close", {15, 15, 10, 10}),
                MakeTile("distant", {11, 50, 10, 10})};
  menu.SetPages({page});
  assert(menu.Move(Direction::Right) && menu.focused_tile_id() == "close");

  page.tiles = {MakeTile("hidden"), MakeTile("disabled"), MakeTile("info"),
                MakeTile("zero"), MakeTile("nan"), MakeTile("infinite-edge"),
                MakeTile("valid")};
  page.tiles[0].visible = false;
  page.tiles[1].enabled = false;
  page.tiles[2].action.clear();
  page.tiles[3].bounds.width = 0;
  page.tiles[4].bounds.x = std::numeric_limits<double>::quiet_NaN();
  page.tiles[5].bounds.x = std::numeric_limits<double>::max();
  page.tiles[5].bounds.width = std::numeric_limits<double>::max();
  menu.SetPages({page});
  assert(menu.focused_tile_id() == "valid");
  for (const auto& tile : page.tiles)
    if (tile.id != "valid") assert(!menu.FocusTile(tile.id));
  assert(menu.Activate() == "action:valid");
  page.tiles.back().enabled = false;
  menu.SetPages({page});
  assert(menu.page_id() == "home" && !menu.focused_tile_id());
  assert(!menu.Move(Direction::Down) && !menu.Activate());
  menu.SetPages({Page{"home", "Empty", {}}});
  assert(menu.page_id() == "home" && !menu.focused_tile_id());

  page.tiles.clear();
  for (int index = 0; index < 32; ++index)
    page.tiles.push_back(MakeTile(std::to_string(index), {index * 20.0, 0, 10, 10}));
  menu.SetPages({page});
  for (int index = 1; index < 32; ++index) {
    assert(menu.Move(Direction::Right));
    assert(menu.focused_tile_id() == std::to_string(index));
  }
  assert(!menu.Move(Direction::Right));
}

void StableIdsAndOwnedData() {
  Controller menu;
  auto pages = std::vector{MakePage("home"), MakePage("cars")};
  menu.SetPages(pages);
  menu.FocusTile("c");
  const auto saved_action = menu.Activate();
  menu.SelectPage("cars");
  menu.FocusTile("b");
  std::reverse(pages.begin(), pages.end());
  for (auto& page : pages) std::reverse(page.tiles.begin(), page.tiles.end());
  menu.SetPages(pages);
  assert(menu.page_id() == "cars" && menu.focused_tile_id() == "b");
  menu.SelectPage("home");
  assert(menu.focused_tile_id() == "c");
  assert(saved_action == "action:c");
  pages[1].tiles.erase(pages[1].tiles.begin());  // Remove home/c.
  menu.SetPages(pages);
  assert(menu.focused_tile_id() == "b");
  pages[1].enabled = false;
  menu.SetPages(pages);
  assert(menu.page_id() == "cars" && menu.focused_tile_id() == "b");
  pages[1].enabled = true;
  menu.SetPages(pages);
  menu.SelectPage("home");
  assert(menu.focused_tile_id() == "b");
  pages.clear();  // Model does not borrow the caller's pages, tiles or strings.
  assert(menu.Activate() == "action:b");
  menu.SetPages({MakePage("new")});
  assert(menu.page_id() == "new" && menu.focused_tile_id() == "a");
  menu.SetPages({});
  assert(!menu.page_id() && !menu.focused_tile_id());

  menu.SetPages({MakePage("original")});
  for (int invalid = 0; invalid < 3; ++invalid) {
    auto bad = std::vector{MakePage("duplicate"), MakePage("duplicate")};
    if (invalid == 1) { bad.resize(1); bad[0].id.clear(); }
    if (invalid == 2) { bad.resize(1); bad[0].tiles[1].id = "a"; }
    bool rejected = false;
    try { menu.SetPages(std::move(bad)); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected && menu.page_id() == "original");
  }
}

void GateTiming() {
  InputGate gate;
  const auto right = Mask(Button::Right);
  assert(gate.Update(right, 100) == right);  // Initial edge ignores preceding delta.
  assert(gate.Update(right, .20) == 0);
  assert(gate.Update(right, .149) == 0);
  assert(gate.Update(right, .001) == right);  // Exact initial boundary.
  assert(gate.Update(right, .099) == 0);
  assert(gate.Update(right, .001) == right);  // Exact repeat boundary.
  assert(gate.Update(right, -.1) == 0);
  assert(gate.Update(right, std::numeric_limits<double>::quiet_NaN()) == 0);
  assert(gate.Update(right, std::numeric_limits<double>::infinity()) == 0);
  assert(gate.Update(right, 0) == 0);
  assert(gate.Update(right, .10) == right);
  assert(gate.Update(right, std::numeric_limits<double>::max()) == right);
  assert(gate.Update(right, 0) == 0);  // No queued burst after a very long stall.
  assert(gate.Update(0, 0) == 0);
  assert(gate.Update(right, 0) == right);

  const auto confirm = Mask(Button::Confirm);
  assert(gate.Update(confirm, 0) == confirm);
  assert(gate.Update(confirm, 1000) == 0);  // Confirm never repeats.
  gate.RequireRelease();
  assert(gate.Update(confirm, 1000) == 0);
  assert(gate.Update(confirm | Button::Right, 0) == right);  // A fresh button still works.
  assert(gate.Update(0, 0) == 0);
  assert(gate.Update(confirm, 0) == confirm);

  InputGate phase({.25, .125});
  assert(phase.Update(right, 0) == right);
  assert(phase.Update(right, .6875) == right);  // .0625 seconds to next repeat.
  assert(phase.Update(right, .0624) == 0);
  assert(phase.Update(right, .0001) == right);
  for (const RepeatTiming timing : {RepeatTiming{-.1, .1}, RepeatTiming{.1, 0},
          RepeatTiming{.1, std::numeric_limits<double>::infinity()}}) {
    bool rejected = false;
    try { InputGate invalid(timing); }
    catch (const std::invalid_argument&) { rejected = true; }
    assert(rejected);
  }
}

void ChildOwnershipAndInputPolicy() {
  Controller menu;
  auto pages = std::vector{MakePage("home"), MakePage("cars")};
  menu.SetPages(pages);
  const auto confirm = Mask(Button::Confirm);
  const auto right = Mask(Button::Right);
  assert(!menu.Update(confirm, 100).action);  // Held from the screen that opened us.
  menu.Update(0, 0);
  assert(menu.Update(right, 0).changed && menu.focused_tile_id() == "b");
  menu.SetPages(pages);  // A data refresh neither loses focus nor resets repeat.
  assert(menu.Update(right, .35).changed && menu.focused_tile_id() == "c");
  menu.Update(0, 0);
  assert(menu.Update(confirm, 0).action == "action:c");
  menu.Suspend();
  assert(menu.suspended());
  assert(!menu.Update(Mask(Button::Back), 100).back);
  assert(!menu.Update(right, 100).changed);
  assert(!menu.SelectPage("cars") && !menu.FocusTile("a"));
  assert(!menu.ChangePage(1) && !menu.Move(Direction::Left) && !menu.Activate());
  std::reverse(pages[0].tiles.begin(), pages[0].tiles.end());
  menu.SetPages(pages);
  menu.Resume();
  assert(menu.page_id() == "home" && menu.focused_tile_id() == "c");
  assert(!menu.Update(confirm | Button::Back, 100).back);
  assert(!menu.Update(confirm | Button::Back, 100).action);
  menu.Update(0, 0);
  assert(menu.Update(confirm, 0).action == "action:c");
  menu.Update(0, 0);
  auto result = menu.Update(confirm | Button::Back, 0);
  assert(result.back && !result.action && !result.changed);

  menu.FocusTile("a");
  menu.Update(0, 0);
  result = menu.Update(right | Button::Confirm, 0);
  assert(result.changed && !result.action && menu.focused_tile_id() == "b");
  menu.Update(0, 0);
  assert(!menu.Update(Button::Left | Button::Right, 0).changed);
  assert(!menu.Update(Button::Left | Button::Right, 100).changed);
  assert(menu.Update(right, 0).changed && menu.focused_tile_id() == "c");
  menu.Enter();
  menu.FocusTile("b");
  assert(!menu.Update(Button::Left | Button::Right, 0).changed);
  assert(!menu.Update(right, 1).changed);  // Opposition cannot bypass release guard.
  menu.Update(0, 0);
  assert(menu.Update(right, 0).changed);
  menu.Update(0, 0);
  assert(!menu.Update(Button::PreviousPage | Button::NextPage, 1).changed);
  assert(menu.Update(Mask(Button::NextPage), 0).changed && menu.page_id() == "cars");
}

void DefaultPausePages() {
  PauseCapabilities all{true,true,true,true,true,true,true,true,true,true,true,true,false,true};
  for (const auto& binding : kNativePauseBindings)
    all.native[binding.action]={true,true,std::string(binding.id)};
  const auto pages = MakePausePages(all);
  Controller menu;
  menu.SetPages(pages);
  std::unordered_set<std::string> used_actions;
  std::unordered_set<std::string> binding_ids;
  for (const auto& binding : kPauseActions)
    assert(binding_ids.insert(std::string(binding.id)).second);
  assert(!FindPauseAction("unknown_extension_action"));

  // Losing the server while Social is selected must remove online actions
  // without stranding focus. Connection help stays actionable offline.
  Controller social;
  social.SetPages(pages);
  assert(social.SelectPage("social"));
  assert(social.FocusTile("free_roam") && social.Activate()=="free_roam");
  assert(social.FocusTile("create_game") && social.Activate()=="create_game");
  auto disconnected=all;
  disconnected.friends=disconnected.free_roam=disconnected.create_game=false;
  disconnected.invite_friends=disconnected.leave_session=false;
  social.SetPages(MakePausePages(disconnected));
  assert(social.page_id()=="social");
  for(const auto* id:{"free_roam","create_game"})
    assert(!social.FocusTile(id));
  for(const auto* id:{"friends","chat","friend_requests","social_connection"}){
    social.FocusTile(id);assert(social.focused_tile_id()==id);
  }
  assert(social.Activate()=="social_session");
  const auto session_pages=MakePausePages(all,false,true);
  assert(session_pages[2].tiles[3].label=="LEAVE SESSION");
  auto world_host=all;world_host.end_free_roam=true;
  assert(MakePausePages(world_host,false,true)[2].tiles[3].label=="END FREE ROAM");
  assert(forza::native_pause_layout::Supports(session_pages));
  social.SetPages(session_pages);
  social.FocusTile("social_back");assert(social.Activate()=="social_back");
  social.FocusTile("session_invite");assert(social.Activate()=="session_invite");
  social.FocusTile("session_leave");assert(social.Activate()=="session_leave");
  social.SetPages(MakePausePages(disconnected,false,true));
  assert(!social.FocusTile("session_invite")&&!social.FocusTile("session_leave"));
  for(const auto& tile:session_pages[2].tiles)
    if(!tile.action.empty())used_actions.insert(tile.action);
  const auto help_pages=MakePausePages(disconnected,true);
  assert(forza::native_pause_layout::Supports(help_pages));
  social.SetPages(help_pages);
  assert(social.page_id()=="social");
  social.FocusTile("social_back");assert(social.Activate()=="social_back");
  assert(social.FocusTile("social_stats")&&social.Activate()=="social_stats");
  assert(!social.FocusTile("social_account")&&!social.FocusTile("social_help"));
  for(const auto& tile:help_pages[2].tiles)
    if(!tile.action.empty())used_actions.insert(tile.action);
  social.SetPages(pages);
  social.FocusTile("friends");assert(social.Activate()=="friends");

  // Walk the actual navigation graph from every starting tile. This catches
  // layouts whose directional rules strand a tile even though it can be
  // selected programmatically or happens to be the default focus.
  for (const auto& page : pages) {
    assert(Available(page));
    menu.SelectPage(page.id);
    std::unordered_set<std::string> focusable;
    for (const auto& tile : page.tiles) {
      if (!tile.action.empty()) {
        assert(FindPauseAction(tile.action));
        used_actions.insert(tile.action);
      }
      if (Focusable(tile)) focusable.insert(tile.id);
    }
    for (const auto& start : focusable) {
      std::unordered_set<std::string> reached{start};
      std::vector<std::string> pending{start};
      for (size_t index = 0; index < pending.size(); ++index) {
        // Copy because growing pending may invalidate its storage.
        const std::string current = pending[index];
        for (const Direction direction : {Direction::Up, Direction::Down,
                                          Direction::Left, Direction::Right}) {
          menu.FocusTile(current);
          if (!menu.Move(direction)) continue;
          const auto next = menu.focused_tile_id();
          assert(next);
          if (reached.insert(*next).second) pending.push_back(*next);
        }
      }
      assert(reached == focusable);
    }
  }
  assert(used_actions == binding_ids);
  menu.SelectPage("cars");
  assert(!menu.FocusTile("current_car"));
  const auto& car_card = pages[1].tiles[0];
  assert(car_card.id == "current_car" && car_card.action.empty());
  assert(!Focusable(car_card));
  menu.FocusTile("car_tuning");
  assert(menu.focused_tile_id()=="car_tuning");
  assert(menu.Activate() == "car_tuning");
  menu.Suspend();
  menu.SetPages(MakePausePages(all));
  menu.Resume();
  assert(menu.page_id() == "cars" && menu.focused_tile_id() == "car_tuning");

  // Individual destination flags cannot override the free-roam gate.
  const PauseCapabilities no_free_roam{false, true, true, true, true, true};
  const auto gated_pages = MakePausePages(no_free_roam);
  menu.SetPages(gated_pages);
  menu.SelectPage("home");
  assert(!menu.FocusTile("my_cars") && !menu.FocusTile("world_map"));
  assert(menu.SelectPage("cars"));
  assert(!menu.focused_tile_id());
  for (const auto& page : gated_pages) {
    if (page.id == "cars") {
      assert(Available(page));
      for (const auto& tile : page.tiles) assert(!Focusable(tile));
    }
    if (page.id == "settings") {
      assert(Available(page));
      for (const auto& tile : page.tiles) assert(Focusable(tile));
    }
  }
  menu.SelectPage("home");
  assert(menu.ChangePage(1) && menu.page_id() == "cars");
  assert(menu.ChangePage(1) && menu.page_id() == "social");
  assert(menu.ChangePage(1) && menu.page_id() == "settings");
  assert(menu.ChangePage(-1) && menu.page_id() == "social");
  assert(menu.ChangePage(-1) && menu.page_id() == "cars");
  assert(menu.ChangePage(-1) && menu.page_id() == "home");
  // Map remains a stock online action; it must never unlock car changes.
  auto online = all;
  online.change_car = false;
  menu.SetPages(MakePausePages(online));
  assert(menu.FocusTile("world_map"));
  assert(!menu.FocusTile("my_cars"));
  assert(menu.SelectPage("cars"));
  assert(!menu.FocusTile("change_car"));
  assert(menu.FocusTile("garage") && menu.Activate() == "garage");
  assert(menu.FocusTile("autoshow") && menu.Activate() == "autoshow");
  menu.SetPages(gated_pages);
  menu.SelectPage("home");
  menu.FocusTile("settings");
  assert(menu.Activate() == "settings_page");
  assert(FindPauseAction(*menu.Activate()) == PauseAction::SettingsPage);
  menu.SetPages(MakePausePages());  // Unknown action capabilities still fail closed.
  assert(menu.SelectPage("cars"));
  assert(!menu.focused_tile_id() && !menu.Activate());
  assert(menu.SelectPage("settings"));

  auto destinations_locked = all;
  destinations_locked.garage = destinations_locked.autoshow = destinations_locked.car_tuning = false;
  menu.SetPages(MakePausePages(destinations_locked));
  assert(menu.SelectPage("cars"));
  assert(menu.focused_tile_id() == "change_car");
  assert(!menu.FocusTile("garage") && !menu.FocusTile("autoshow"));
  assert(!menu.FocusTile("car_tuning"));
  assert(!menu.Move(Direction::Down));

  menu.SetPages(MakePausePages(all));
  menu.SelectPage("settings");
  menu.FocusTile("controls");
  menu.Update(0, 0);
  assert(menu.Update(Mask(Button::Confirm), 0).action == "controls");
  menu.Suspend();
  menu.SetPages(MakePausePages(all));
  assert(!menu.Update(Mask(Button::Back), 1).back);
  menu.Resume();
  for (double elapsed : {0.0, .35, 10.0}) {
    menu.SetPages(MakePausePages(all));
    const auto result = menu.Update(Button::Confirm | Button::Back, elapsed);
    assert(!result.action && !result.back && !result.changed);
    assert(menu.page_id() == "settings" && menu.focused_tile_id() == "controls");
  }
  menu.SetPages(MakePausePages(all));
  menu.Update(0, 0);
  menu.SetPages(MakePausePages(all));
  assert(menu.Update(Mask(Button::Confirm), 0).action == "controls");
}

void NativeContextPages() {
  // Native model snapshots, not host guesses about DLC installation.
  PauseCapabilities caps;
  caps.world_map=true;
  assert(DesktopQuitContext(caps,true,1,0));
  assert(!DesktopQuitContext(caps,false,1,0));
  assert(!DesktopQuitContext(caps,true,2,0));
  assert(!DesktopQuitContext(caps,true,1,1));
  for (const uint32_t action : {0u,1u,14u,16u,18u}) {
    caps.native[action].present=true;
    assert(!DesktopQuitContext(caps,true,1,0)); // Even stale free-roam presence is insufficient.
    caps.native[action]={};
  }
  caps={};
  caps.native[14]={true,true,"QUIT RACE"};
  caps.native[10]={true,true,"RESTART"};
  assert(PauseEntryPage(caps)=="activity");
  Controller menu;
  menu.SetPages(MakePausePages(caps));
  assert(menu.SelectPage("activity"));
  menu.FocusTile("quit_race");assert(menu.Activate()=="quit_race");
  assert(NativePauseAction(*menu.Activate())==14);
  caps.native[14].label="RETIRE";
  caps.native[10].enabled=false;
  const auto changed=MakePausePages(caps);
  assert(changed[4].tiles[0].label=="RETIRE");
  menu.SetPages(changed);
  assert(menu.focused_tile_id()=="quit_race" && !menu.FocusTile("restart_race"));
  caps.native[14]={};caps.native[10]={};
  assert(PauseEntryPage(caps)=="home");
  menu.SetPages(MakePausePages(caps));
  assert(!menu.SelectPage("activity"));
  assert(!NativePauseAction("unknown") && !NativePauseAction("native_999"));

  caps.native[6]={true,false,"HORIZON RALLY"};
  caps.native[19]={true,true,"1000 CLUB"};
  menu.SetPages(MakePausePages(caps));
  assert(menu.SelectPage("horizon"));
  assert(!menu.FocusTile("horizon_rally"));
  menu.FocusTile("thousand_club");assert(NativePauseAction(*menu.Activate())==19);
  caps.native[19]={};
  menu.SetPages(MakePausePages(caps));
  assert(!menu.FocusTile("thousand_club"));
  // Rally-only model: Resume, Difficulty and Restart. No profile dependency.
  caps={};caps.native[3]={true,true,"RESUME"};
  caps.native[18]={true,true,"DIFFICULTY"};caps.native[10]={true,true,"RESTART"};
  menu.SetPages(MakePausePages(caps));menu.SelectPage("settings");
  menu.FocusTile("difficulty");assert(menu.focused_tile_id()=="difficulty");
  caps.native[18].enabled=false;
  menu.SetPages(MakePausePages(caps));assert(!menu.FocusTile("difficulty"));

  using O=PauseDialogOutcome;
  for (uint32_t action : {0u,1u,6u,7u,10u,14u,15u,16u,17u}) {
    const auto dialog=NativePauseDialog(action);assert(dialog);
    assert(NativePauseDialogOutcome(*dialog,0)==(*dialog==5?O::Restart:O::Leave));
    assert(NativePauseDialogOutcome(*dialog,*dialog==5?3:1)==O::Cancel);
  }
  assert(NativePauseDialog(0)==0); // Zero is a real confirmation ID.
  assert(!NativePauseDialog(19));
  assert(NativePauseDialogOutcome(5,6)==O::Child);
  assert(NativePauseDialogOutcome(2,6)==O::Leave);
  for (uint32_t dialog : {16u,20u,21u,22u,100005u,100007u,100011u,100012u,100013u,100014u})
    for (uint32_t choice : {0u,1u,3u,6u})
      assert(NativePauseDialogOutcome(dialog,choice)==O::Cancel);
  assert(NativePauseDialogOutcome(999,0)==O::Child);
}

void NativeLayoutContract() {
  namespace layout = forza::native_pause_layout;
  const auto defaults = MakePausePages({true, true, true, true, true});
  assert(layout::Supports(defaults));
  assert(!layout::Supports({}));
  for (size_t count = 1; count <= layout::kTabCapacity; ++count) {
    auto pages = defaults;
    pages.resize(count);
    assert(layout::Supports(pages));
    // Availability changes do not alter backend capacity or geometry.
    pages.back().visible = false;
    pages.back().enabled = false;
    assert(layout::Supports(pages));
  }
  auto too_many_pages = defaults;
  for(size_t i=too_many_pages.size();i<=layout::kPageCapacity;++i)
    too_many_pages.push_back(MakePage("extension"+std::to_string(i)));
  assert(!layout::Supports(too_many_pages));
  too_many_pages.pop_back();
  assert(layout::Supports(too_many_pages));
  auto too_many_tiles = defaults;
  too_many_tiles.front().tiles.push_back(MakeTile("extension"));
  assert(!layout::Supports(too_many_tiles));
  // Empty pages can still be shown; explicit focuslessness belongs to the
  // controller, rather than being treated as invalid renderer geometry.
  assert(layout::Supports({Page{"empty", "Empty", {}}}));

  for (const Rect& valid : {Rect{0, 0, 1280, 720}, Rect{1279, 719, 1, 1},
                            Rect{.25, .5, 1279.75, 719.5}})
    assert(layout::Valid(valid));
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double infinity = std::numeric_limits<double>::infinity();
  const double huge = std::numeric_limits<double>::max();
  std::vector<Rect> invalid{
      {-1, 0, 10, 10}, {0, -1, 10, 10}, {0, 0, 0, 10}, {0, 0, 10, 0},
      {0, 0, -1, 10}, {0, 0, 10, -1}, {0, 0, 1281, 720}, {0, 0, 1280, 721},
      {1279, 0, 2, 1}, {0, 719, 1, 2}, {huge, 0, huge, 1},
      {std::nextafter(1279.0, infinity), 719, 1, 1},
      {1279, std::nextafter(719.0, infinity), 1, 1}};
  for (double value : {nan, infinity, -infinity}) {
    invalid.push_back({value, 0, 10, 10});
    invalid.push_back({0, value, 10, 10});
    invalid.push_back({0, 0, value, 10});
    invalid.push_back({0, 0, 10, value});
  }
  for (const auto& bounds : invalid) {
    assert(!layout::Valid(bounds));
    auto pages = defaults;
    pages.front().tiles.front().bounds = bounds;
    assert(!layout::Supports(pages));
    // A disabled or hidden tile must not bypass geometric validation and
    // become unsafe when its availability changes on a later refresh.
    pages.front().tiles.front().visible = false;
    pages.front().tiles.front().enabled = false;
    assert(!layout::Supports(pages));
  }

  const auto contained = [](const Rect& inner, const Rect& outer) {
    return layout::Valid(inner) && inner.x >= outer.x && inner.y >= outer.y &&
           inner.x + inner.width <= outer.x + outer.width &&
           inner.y + inner.height <= outer.y + outer.height;
  };
  unsigned car_panels = 0, map_panels = 0;
  for (const auto& page : defaults) {
    for (const auto& tile : page.tiles) {
      if (tile.id == "current_car") {
        const auto car = layout::Car(tile.bounds);
        assert(contained(car.name, tile.bounds));
        assert(contained(car.photo, tile.bounds));
        assert(contained(car.specifications, tile.bounds));
        assert(car.name.y + car.name.height <= car.photo.y);
        assert(car.photo.y + car.photo.height <= car.specifications.y);
        ++car_panels;
      } else if (tile.id == "world_map") {
        const auto map = layout::Map(tile.bounds);
        assert(contained(map, tile.bounds));
        assert(map.x > tile.bounds.x && map.y > tile.bounds.y);
        using namespace forza::fh1_menu_components;
        for(const double y : {map.y,map.y+map.height}) {
          const auto project=[&](double x){return 640+kMenuTranslateX+kMenuScaleX*(x-640)+kMenuShear*(360-y);};
          const auto left=map.x+kMenuShear*(map.y+map.height/2-y);
          assert(left>=project(tile.bounds.x)+8);
          assert(left+map.width<=project(tile.bounds.x+tile.bounds.width)-8);
        }
        ++map_panels;
      }
    }
  }
  assert(car_panels > 0 && map_panels > 0);
  for (const auto& tab : layout::kTabs) assert(layout::Valid(tab));
}
}  // namespace

int main() {
  TabAvailability();
  GeometryAndUnavailableTiles();
  StableIdsAndOwnedData();
  GateTiming();
  ChildOwnershipAndInputPolicy();
  DefaultPausePages();
  NativeContextPages();
  NativeLayoutContract();
  std::puts("Native menu model: availability, spatial focus, stable IDs, input timing, child ownership, default pages and layout contracts passed");
}
