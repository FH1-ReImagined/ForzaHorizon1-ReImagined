#pragma once

#include "menu_model.h"
#include <array>
#include <optional>
#include <string_view>

// Original menu definitions. Coordinates use a 1280 x 720 design canvas;
// the renderer maps them into its viewport. IDs are persistent selection keys,
// not indices into a serialized game scene or translated instruction stream.
namespace forza::menu {

inline constexpr double kPauseCanvasWidth = 1280;
inline constexpr double kPauseCanvasHeight = 720;

enum class PauseAction {
  WorldMap, SettingsPage, ChangeCar, FestivalProgress, PhotoMode, MyProfile,
  Garage, Autoshow, TuneCar, Difficulty, Graphics, Audio, Controls, Hud, Video,
  Friends, Chat, SocialStats, FreeRoam, CreateGame, FriendRequests, ConnectionGuide, SocialRoot,
  SessionPage, InviteFriends, LeaveSession, SessionNext,
  NativeActivity,
};

struct PauseActionBinding {
  std::string_view id;
  PauseAction action;
};

inline constexpr std::array kPauseActions{
    PauseActionBinding{"world_map", PauseAction::WorldMap},
    PauseActionBinding{"settings_page", PauseAction::SettingsPage},
    PauseActionBinding{"change_car", PauseAction::ChangeCar},
    PauseActionBinding{"festival_progress", PauseAction::FestivalProgress},
    PauseActionBinding{"photo_mode", PauseAction::PhotoMode},
    PauseActionBinding{"my_profile", PauseAction::MyProfile},
    PauseActionBinding{"garage", PauseAction::Garage},
    PauseActionBinding{"autoshow", PauseAction::Autoshow},
    PauseActionBinding{"car_tuning", PauseAction::TuneCar},
    PauseActionBinding{"difficulty", PauseAction::Difficulty},
    PauseActionBinding{"graphics", PauseAction::Graphics},
    PauseActionBinding{"audio", PauseAction::Audio},
    PauseActionBinding{"controls", PauseAction::Controls},
    PauseActionBinding{"hud", PauseAction::Hud},
    PauseActionBinding{"video", PauseAction::Video},
    PauseActionBinding{"friends", PauseAction::Friends},
    PauseActionBinding{"chat", PauseAction::Chat},
    PauseActionBinding{"social_stats", PauseAction::SocialStats},
    PauseActionBinding{"free_roam", PauseAction::FreeRoam},
    PauseActionBinding{"create_game", PauseAction::CreateGame},
    PauseActionBinding{"friend_requests", PauseAction::FriendRequests},
    PauseActionBinding{"social_connection", PauseAction::ConnectionGuide},
    PauseActionBinding{"social_back", PauseAction::SocialRoot},
    PauseActionBinding{"social_session", PauseAction::SessionPage},
    PauseActionBinding{"session_invite", PauseAction::InviteFriends},
    PauseActionBinding{"session_leave", PauseAction::LeaveSession},
    PauseActionBinding{"session_next", PauseAction::SessionNext},
    PauseActionBinding{"quit_stunt", PauseAction::NativeActivity},
    PauseActionBinding{"restart_stunt", PauseAction::NativeActivity},
    PauseActionBinding{"restart_race", PauseAction::NativeActivity},
    PauseActionBinding{"quit_race", PauseAction::NativeActivity},
    PauseActionBinding{"end_free_roam", PauseAction::NativeActivity},
    PauseActionBinding{"finish_race", PauseAction::NativeActivity},
    PauseActionBinding{"horizon_rally", PauseAction::NativeActivity},
    PauseActionBinding{"thousand_club", PauseAction::NativeActivity},
    PauseActionBinding{"coop_challenges", PauseAction::NativeActivity},
    PauseActionBinding{"sponsor_challenges", PauseAction::NativeActivity},
    PauseActionBinding{"message_center", PauseAction::NativeActivity},
    PauseActionBinding{"native_multiplayer", PauseAction::NativeActivity},
    PauseActionBinding{"head_tracking", PauseAction::NativeActivity},
};

struct NativePauseBinding {
  std::string_view id;
  uint32_t action;
  bool activity;
};
inline constexpr std::array<NativePauseBinding,13> kNativePauseBindings{{
    NativePauseBinding{"quit_race",14,true}, {"restart_race",10,true},
    {"quit_stunt",0,true}, {"restart_stunt",1,true},
    {"finish_race",16,true}, {"end_free_roam",15,true},
    {"horizon_rally",6,false}, {"thousand_club",19,false},
    {"coop_challenges",4,false}, {"sponsor_challenges",8,false},
    {"message_center",5,false}, {"native_multiplayer",7,false},
    {"head_tracking",13,false},
}};

inline std::optional<uint32_t> NativePauseDialog(uint32_t action) {
  switch (action) {
    case 0: case 1: return action;
    case 6: return 23;
    case 7: return 3;
    case 10: return 5;
    case 14: return 17;
    case 15: return 18;
    case 16: return 19;
    case 17: return 2;
    default: return {};
  }
}
enum class PauseDialogOutcome { Ignored, Child, Cancel, Leave, Restart };
inline bool NativePauseDialogReturns(uint32_t dialog) {
  // Information/error prompts return without an activity transition.
  // Marketplace choices first run the common native 827618F8 handler; the
  // adapter must respect its keep-open flag and any follow-up child request.
  return dialog == 16 || dialog == 20 || dialog == 21 || dialog == 22 ||
      dialog == 100005 || dialog == 100007 || dialog == 100011 || dialog == 100012 ||
      dialog == 100013 || dialog == 100014;
}
inline PauseDialogOutcome NativePauseDialogOutcome(uint32_t dialog, uint32_t choice) {
  if (NativePauseDialogReturns(dialog)) return PauseDialogOutcome::Cancel;
  // Restart's B result is 3; X=6 opens native difficulty. Quit's X=6
  // is the party-leader leave-with-party choice, not cancellation.
  if (dialog == 5)
    return choice == 0 ? PauseDialogOutcome::Restart : choice == 3 ?
        PauseDialogOutcome::Cancel : PauseDialogOutcome::Child;
  switch (dialog) {
    case 0: case 1: case 2: case 3: case 17: case 18: case 19: case 23:
      return choice == 0 || (dialog == 2 && choice == 6) ?
          PauseDialogOutcome::Leave : PauseDialogOutcome::Cancel;
    default: return PauseDialogOutcome::Child;
  }
}
inline std::optional<uint32_t> NativePauseAction(std::string_view id) {
  for (const auto& binding : kNativePauseBindings)
    if (binding.id == id) return binding.action;
  return {};
}

struct NativePauseRow {
  bool present = false, enabled = false;
  std::string label;
};

inline std::optional<PauseAction> FindPauseAction(std::string_view id) {
  for (const auto& binding : kPauseActions)
    if (binding.id == id) return binding.action;
  return std::nullopt;
}

// The native adapter derives these from FH1's live action model. Unknown
// availability fails closed; changing layout cannot unlock a game action.
struct PauseCapabilities {
  bool world_map = false;
  bool photo_mode = false;
  bool my_profile = false;
  bool garage = false;
  bool autoshow = false;
  bool car_tuning = false;
  bool friends = false;
  bool free_roam = false;
  bool create_game = false;
  bool invite_friends = false;
  bool leave_session = false;
  bool session_more = false;
  bool end_free_roam = false;
  bool change_car = false;
  std::array<NativePauseRow,20> native{};
};

inline std::string_view PauseEntryPage(const PauseCapabilities& caps) {
  for (const uint32_t action : {0u,14u,16u})
    if (caps.native[action].present && caps.native[action].enabled &&
        !caps.native[action].label.empty()) return "activity";
  return "home";
}

inline bool DesktopQuitContext(const PauseCapabilities& caps, bool activity_known,
                               uint32_t presence, uint32_t region) {
  if (!caps.world_map || !activity_known || presence != 1 || region != 0) return false;
  for (const uint32_t action : {0u,1u,14u,16u,18u})
    if (caps.native[action].present) return false;
  return true;
}

inline std::vector<Page> MakePausePages(const PauseCapabilities& caps = {}, bool social_help=false,
                                      bool social_session=false) {
  std::vector<Page> pages{
      {"home", "HOME", {
          {"world_map", "WORLD MAP", {64, 166, 560, 282}, "world_map", true, caps.world_map},
          {"my_cars", "MY CARS", {640, 166, 280, 282}, "change_car", true, caps.change_car},
          {"photo_mode", "PHOTO MODE", {936, 166, 280, 133}, "photo_mode", true, caps.photo_mode},
          {"my_profile", "MY PROFILE", {936, 315, 280, 133}, "my_profile", true, caps.my_profile},
          {"festival_progress", "FESTIVAL PROGRESS", {64, 464, 560, 106}, "festival_progress"},
          {"settings", "SETTINGS", {640, 464, 576, 106}, "settings_page"},
      }},
      // Viewing car information must not depend on the offline Change Car
      // action. Keep each action's capability check on its own tile.
      {"cars", "CARS", {
          {"current_car", "CURRENT CAR", {64, 166, 560, 404}, ""},
          {"change_car", "CHANGE CAR", {640, 166, 280, 194}, "change_car", true, caps.change_car},
          {"car_tuning", "TUNE CAR", {936, 166, 280, 194}, "car_tuning", true, caps.world_map && caps.car_tuning},
          {"garage", "GO TO GARAGE", {640, 376, 280, 194}, "garage", true, caps.world_map && caps.garage},
          {"autoshow", "GO TO AUTOSHOW", {936, 376, 280, 194}, "autoshow", true, caps.world_map && caps.autoshow},
      }},
      {"social", "SOCIAL", {
          {"friends", "FRIENDS", {64, 166, 560, 282}, "friends"},
          {"free_roam", "HORIZON WORLD", {640, 166, 280, 194}, "free_roam", true, caps.free_roam},
          {"create_game", "CREATE GAME", {936, 166, 280, 194}, "create_game", true, caps.create_game},
          {"friend_requests", "REQUESTS", {640, 376, 280, 194}, "friend_requests"},
          {"chat", "CHAT", {936, 376, 280, 194}, "chat"},
          {"social_connection", "SESSION & CONNECTION", {64, 464, 560, 106}, "social_session"},
      }},
      {"settings", "SETTINGS", {
          {"difficulty", "DIFFICULTY", {64, 166, 374, 194}, "difficulty", true,
              caps.my_profile || caps.native[18].enabled},
          {"graphics", "GRAPHICS", {454, 166, 372, 194}, "graphics"},
          {"audio", "AUDIO", {842, 166, 374, 194}, "audio"},
          {"controls", "CONTROLS", {64, 376, 374, 194}, "controls"},
          {"hud", "HUD", {454, 376, 372, 194}, "hud"},
          {"video", "VIDEO", {842, 376, 374, 194}, "video"},
      }},

  };
  if(social_help)pages[2].tiles={
      {"social_account","CONNECTION",{64,166,560,282},""},
      {"social_help","FORZA LIVE SETUP",{640,166,576,282},""},
      {"social_stats","CAREER STATISTICS",{64,464,560,106},"social_stats"},
      {"social_back","BACK TO SOCIAL",{640,464,576,106},"social_back"},
  };
  else if(social_session)pages[2].tiles={
      {"session_players","SESSION PLAYERS",{64,166,560,282},caps.session_more?"session_next":""},
      {"session_join","JOIN A FRIEND",{640,166,280,194},"friends",true,caps.friends},
      {"session_invite","INVITE FRIENDS",{936,166,280,194},"session_invite",true,caps.invite_friends},
      {"session_leave",caps.end_free_roam?"END FREE ROAM":"LEAVE SESSION",{640,376,280,194},"session_leave",true,caps.leave_session},
      {"social_connection","CONNECTION & HELP",{936,376,280,194},"social_connection"},
      {"social_back","BACK TO SOCIAL",{64,464,560,106},"social_back"},
  };
  // The native rows supply the context-specific/localized title (for example
  // Quit Race versus Retire). Never turn an absent DLC/event row into a tile.
  // Six donors are reused per page; additional actions get a navigable page.
  for (const bool activity : {true,false}) {
    unsigned count = 0;
    for (const auto& binding : kNativePauseBindings) {
      const auto& row = caps.native[binding.action];
      if (binding.activity != activity || !row.present || row.label.empty()) continue;
      const unsigned index = count++ % 6;
      if (!index) {
        const std::string name = activity ? "activity" : "horizon";
        const unsigned number = (count - 1) / 6;
        pages.push_back({name + (number ? "_" + std::to_string(number + 1) : ""),
                         std::string(activity ? "ACTIVITY" : "HORIZON") +
                             (number ? " " + std::to_string(number + 1) : ""), {}});
      }
      const double x = 64 + (index % 3) * 390;
      const double y = 166 + (index / 3) * 210;
      pages.back().tiles.push_back({std::string(binding.id),row.label,
          {x,y,374,194},std::string(binding.id),true,row.enabled});
    }
  }
  return pages;
}

} // namespace forza::menu
