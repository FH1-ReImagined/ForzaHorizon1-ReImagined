// FH1 TU4 pause composition: original native donor widgets, repository-owned
// page geometry and controller. No second game's assets or translated code.
// tu_address_map: TU4 addresses
#include "../platform/native_pause_scene.h"
#include "../platform/native_quit.h"
#include "../platform/guest_addr.h"
#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include "../platform/fh1_menu_widgets.h"
#include "../platform/fh1_menu_components.h"
#include "../platform/native_car_tuning_menu.h"
#include "../platform/native_pause_layout.h"
#include "../platform/native_pause_pages.h"
#include "../platform/native_pause_dialog.h"
#include "../platform/native_pause_assets.h"
#include "../platform/fh1_pause_actions.h"
#include "../platform/fh1_current_car.h"
#include "../platform/fh1_map_preview.h"
#include "../platform/native_pause_edges.h"
#include "../platform/fh1_festival_travel.h"
#include "../platform/fh1_car_recovery.h"
#include "../platform/offline_cars.h"
#include "../kernel/live.h"
#include "../kernel/identity.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/func.h>
#include <rex/runtime.h>
#include <array>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <optional>
#include <unordered_map>

REXCVAR_DEFINE_BOOL(forza_native_pause, false, "Interface",
                    "Use the experimental FH1-native tabbed pause menu");
extern "C" REX_FUNC(__imp__sub_827DFB18);
extern "C" REX_FUNC(__imp__sub_827DAB28);
extern "C" REX_FUNC(__imp__sub_827DE0D0);
extern "C" REX_FUNC(__imp__sub_827618F8);
extern "C" REX_FUNC(__imp__sub_827540F8);
extern "C" REX_FUNC(__imp__XamInputGetState);
extern "C" REX_FUNC(__imp__XamUserGetName);

namespace forza::native_pause_scene {
namespace {
using namespace fh1_menu_widgets;
using Clock = std::chrono::steady_clock;
constexpr uint32_t kPanelColor = 0x211e20;
constexpr uint32_t kTextColor = 0xffffff;
constexpr uint32_t kSecondaryTextColor = 0xe2dce2;
constexpr uint32_t kDisabledTextColor = 0xaaaaaa;
namespace layout = native_pause_layout;
struct Card {
  Donor donor;
  uint32_t title = 0, paper = 0, material = 0, caption = 0, grain = 0, grain_material = 0;
  uint32_t progress_fill = 0, progress_track = 0;
  uint32_t icon=0,icon_material=0;std::string icon_action;
};
struct Car {
  uint32_t root = 0, name = 0, make = 0, image = 0, material = 0;
  uint32_t stats = 0, controller = 0, logo = 0, logo_material = 0;
  bool logo_ready = false;
  fh1_logo_layout::Fit logo_fit;
  uint32_t badge=0,badge_controller=0;uint64_t badge_value=0;bool badge_ready=false;
  std::array<uint32_t, 5> rows{}, labels{}, values{};
  uint32_t owned = 0, model = 0;
  bool bound = false;
  float photo_x=1,photo_y=1;
};
struct State {
  uint32_t screen = 0, engine = 0, root = 0;
  uint32_t stock_render_root = 0;
  uint8_t stock_suppression = 0, stock_visibility = 0;
  bool stock_captured = false;
  bool ready = false, visible = true;
  menu::Controller controller{};
  fh1_menu_components::TabBar tab_bar;
  std::array<Card, layout::kTileCapacity> cards;
  std::array<Card, layout::kTabCapacity> tabs;
  Donor header;
  Donor stock_footer;
  std::vector<std::pair<uint32_t,bool>> stock_nodes;
  bool footer_bound=false, footer_recovery=false, footer_quit=false;
  Car car;
  uint32_t player_bar = 0, credits = 0, username = 0, username_paper = 0;
  uint32_t header_paper=0,header_material=0,credits_text=0,credits_display=0,username_material=0;
  uint64_t credits_hash=0;
  uint32_t status = 0, footer = 0;
  std::array<uint32_t, 2> bumpers{};
  std::string gamertag, connection;
  std::string friends_summary;
  std::string requests_summary,chat_summary,connection_summary,account_summary,session_summary;
  bool social_help=false,footer_social_help=false;
  bool social_session=false,help_from_session=false;
  size_t session_offset=0;
  bool online_entry_available=false;
  bool end_free_roam=false;
  std::optional<fh1_pause_actions::DialogRequest> exit_dialog;
  std::string social_return="social_connection";
  std::optional<float> completion;
  bool recovery = false;
  std::unordered_map<uint32_t, std::string> text;
  Clock::time_point started{}, previous{}, refreshed{};
  uint16_t previous_pad = 0xffff;
};
State state;
struct ReturnBookmark {
  uint32_t context = 0;
  std::string page, tile;
  bool social_help=false;
  std::string social_return="social_connection";
  bool social_session=false,help_from_session=false;
};
std::optional<ReturnBookmark> return_bookmark;
struct OnlineIntent {
  menu::PauseAction action;
  Clock::time_point expires;
  Clock::time_point ready_since{};
};
std::optional<OnlineIntent> online_intent;

bool Require(const char* group, const char* name, uint32_t node) {
  if (!node) REXLOG_WARN("Native pause binding: {} missing {}", group, name);
  return node != 0;
}
void DumpNodes(Native& ui, const Donor& d, const char* name) {
  std::vector<uint32_t> pending{d.root};
  unsigned count = 0;
  while (!pending.empty() && count++ < 512) {
    const auto node = pending.back(); pending.pop_back();
    const auto wrapper = Read(ui.base, node + 4);
    REXLOG_INFO("Native pause donor {}: node={:08X} hash={:08X} parent={:08X} kind={}",
        name, node, Read(ui.base, node), Read(ui.base, node + 12),
        wrapper ? unsigned(GuestPtr(ui.base, wrapper + 0xa)[0]) : 0);
    unsigned siblings = 0;
    for (auto child = Read(ui.base, node + 20); child && siblings++ < 1024;
         child = Read(ui.base, child + 16)) pending.push_back(child);
  }
}

uint32_t Named(Native& ui, uint32_t root, const char* name) {
  const auto nodes = Descendants(ui.base, root, Hash(name));
  return nodes.empty() ? 0u : nodes.front();
}
bool ActionAvailable(uint8_t* base, uint32_t action) {
  return fh1_pause_actions::Available(base, state.screen, action);
}
std::string NativeRowLabel(Native& ui, uint32_t text);
std::optional<uint32_t> LeaveAction(uint8_t* base) {
  // End Free Roam is a host round action. Quit (17) uses the separate native
  // quit/quit_to_new_party delegates. Retire (14) leaves a guest in the round.
  if (ActionAvailable(base,15)) return 15;
  if (ActionAvailable(base,17)&&Read(base,state.screen+0x1f8)) return 17;
  return {};
}
menu::PauseCapabilities Capabilities(Native& ui) {
  using D = fh1_festival_travel::Destination;
  uint32_t presence=0,region=0;
  const bool online_entry=LiveConnected()&&ActionAvailable(ui.base,2)&&
      Read(ui.base,state.screen+0x1e4)&&LiveLocalActivity(presence,region)&&
      presence==1&&region==0;
  state.online_entry_available=online_entry;
  const auto session=LiveCurrentSession();
  state.end_free_roam=session.has_value()&&ActionAvailable(ui.base,15);
  menu::PauseCapabilities caps{ActionAvailable(ui.base, 2), ActionAvailable(ui.base, 11),
          ActionAvailable(ui.base, 12),
          fh1_festival_travel::Available(ui.ctx, ui.base, D::Garage),
          fh1_festival_travel::Available(ui.ctx, ui.base, D::Autoshow),
          car_tuning_menu::Available(ui.ctx, ui.base, state.screen),
          LiveConnected(), online_entry, online_entry,
          LiveConnected() && session.has_value() && !(session->flags & 0x400),
          LiveConnected() && session.has_value() && LeaveAction(ui.base).has_value(),
          session&&session->members.size()>5,state.end_free_roam,
          ActionAvailable(ui.base,2)&&offline_cars::Available(ui.base,state.screen)};
  for (uint32_t action = 0; action < caps.native.size(); ++action) {
    if (const auto row = fh1_pause_actions::FindRow(ui.base,state.screen,action))
      caps.native[action] = {true,row->enabled,NativeRowLabel(ui,row->text)};
  }
  return caps;
}
void Depth(Native& ui, const Donor& d, uint32_t node, float value) {
  if (!node || ui.Scalar(d.presentation, node, "position.z", value)) return;
  const auto wrapper = Read(ui.base, node + 4);
  if (wrapper && Read(ui.base, wrapper + 4) == node) {
    Write(ui.base, wrapper + 0x14, std::bit_cast<uint32_t>(value));
    GuestPtr(ui.base, wrapper + 0xfb)[0] |= 0x20;
    ui.Dirty(d.presentation, node);
  }
}
// Normalize retained ancestry once, preserving original native ownership and
// sibling lists. Coordinates below then use FH1's centered, upward-positive
// 1280x720 canvas. No node is reparented or cloned.
void Normalize(Native& ui, const Donor& donor, uint32_t node) {
  unsigned depth = 0;
  for (auto parent = Read(ui.base, node + 12);
       parent && parent != donor.root && depth++ < 100;
       parent = Read(ui.base, parent + 12)) {
    ui.Position(donor.presentation, parent, 0, 0);
    Depth(ui, donor, parent, 0);
    ui.Scale(donor.presentation, parent, 1, 1);
    ui.RotationZ(donor.presentation, parent, 0);
    ui.Opacity(donor.presentation, parent, 100);
  }
}
void Point(Native& ui, const Donor& d, uint32_t node, double x, double y) {
  ui.Position(d.presentation, node, float(x - 640), float(360 - y));
}
void Label(Native& ui, const Donor& d, uint32_t node,
           const menu::Rect& bounds, float size, const std::string& text,
           uint32_t color = 0xffffff, uint32_t align = 0) {
  Normalize(ui, d, node);
  ui.Scale(d.presentation, node, 1, 1);
  ui.RotationZ(d.presentation, node, 0);
  Depth(ui, d, node, -3);
  Point(ui, d, node, align == 1 ? bounds.x + bounds.width / 2 :
        align == 2 ? bounds.x + bounds.width : bounds.x,
        bounds.y + bounds.height / 2);
  ui.TextStyle(d.access, d.presentation, node, size,
               float(bounds.width / 1200), float(bounds.height / 720), align);
  ui.Color(d.presentation, node, color, true);
  ui.Opacity(d.presentation, node, 100);
  auto [cached, inserted] = state.text.try_emplace(node, text);
  if (inserted || cached->second != text) {
    ui.Text(d.presentation, node, text);
    cached->second = text;
  }
}
void PaperUV(Native& ui, const Donor& d, uint32_t paper) {
  // These are the image records in the verified pristine garage papers.
  // Their first U offsets crop narrow strips for the original tape/timer.
  for (const uint32_t hash : {0xde7bb086u, 0xebf2ce08u, 0x2b443d36u,
                             0x660a8466u, 0xa0110228u, 0xe0dc09b7u, 0x54741897u}) {
    for (const auto image : Descendants(ui.base, paper, hash)) {
      const auto wrapper = Read(ui.base, image + 4);
      if (!wrapper || Read(ui.base, wrapper + 4) != image ||
          GuestPtr(ui.base, wrapper + 0xa)[0] != 7) continue;
      const auto object = Read(ui.base, wrapper);
      if (object) {
        const bool u = ui.Scalar(d.presentation, image, "positionu", 0);
        const bool v = ui.Scalar(d.presentation, image, "positionv", 0);
        if (!u) Write(ui.base, object + 0xb4, std::bit_cast<uint32_t>(0.f));
        if (!v) Write(ui.base, object + 0xb8, std::bit_cast<uint32_t>(0.f));
        if (!u || !v) ui.Call(0x82f1d8f0, {0, wrapper, image});
      }
    }
  }
}
bool BindCard(Native& ui, Card& card, bool tab = false) {
  // Resolved against the pristine garage triplet; exact names are validated
  // before activation so format drift always returns to the stock pause.
  const auto tape = Named(ui, card.donor.root, "TitleTape");
  card.title = Named(ui, tape, "TitleTapeText");
  if(tab)card.title=Named(ui,Named(ui,card.donor.root,"CAR_STATS"),"TEXT_CAR_NAME");
  // BGF renames these FBF meshes. Match their serialized runtime identities,
  // scoped under the verified tape component, rather than FBF display names.
  const auto highlight = Named(ui, Named(ui, card.donor.root, "CAR_LIST"), "HIGHLIGHT");
  const auto papers = Descendants(ui.base, highlight, 0xe0dc09b7);
  card.paper = papers.empty() ? 0u : papers.front();
  card.caption = Named(ui, card.donor.root, "TEXT_STATUS");
  const auto fills = Descendants(ui.base, tape, 0x747ffda5);
  card.progress_fill = fills.empty() ? 0u : fills.front();
  const auto button = Named(ui, card.donor.root, "HelpButton0");
  card.progress_track = Named(ui, button, "Pill");
  bool valid = Require("card", "TitleTape", tape);
  valid = Require("card", "TitleTapeText", card.title) && valid;
  valid = Require("card", "HIGHLIGHT.BigPane01", card.paper) && valid;
  if (!valid) return false;
  card.material = Named(ui, card.paper, "Material866");
  if(!tab){
    // Slot zero keeps its manufacturer logo exclusively for the current car.
    // Its otherwise unused progress quad supplies Social artwork instead.
    const bool first=&card==&state.cards[0];
    card.icon=first?card.progress_fill:Named(ui,Named(ui,card.donor.root,"CAR_STATS"),"LOGO");
    card.icon_material=Named(ui,card.icon,first?"Material90":"Material_Car_Logo");
  }
  if(tab){const auto grain=Descendants(ui.base,tape,0x43a6b655);
    card.grain=grain.empty()?0:grain.front();card.grain_material=Named(ui,card.grain,"Material90");
    PaperUV(ui,card.donor,card.grain_material);}
  if(!tab){
    const auto binding=ui.scratch+832;ui.Call(0x82664A38,{binding});
    Write(ui.base,binding+4,card.material);Write(ui.base,binding+8,card.donor.presentation);
    ui.Call(0x82F38E80,{binding,ui.String("GAME:\\MEDIA\\UI\\TEXTURES\\HORIZON\\DIRTMASKS\\PANEL01.TGA"),0,8});
    ui.CropImage(card.material,0,0,1,1);
  }
  PaperUV(ui, card.donor, card.material);
  if (card.progress_fill) PaperUV(ui, card.donor, card.progress_fill);
  if (card.progress_track) PaperUV(ui, card.donor, card.progress_track);
  return Require("card", "BigPane01.Material866", card.material);
}
std::string Description(std::string_view id, bool enabled) {
  if (menu::NativePauseAction(id)) {
    if (!enabled) return "Unavailable in this activity";
    if (id=="horizon_rally") return "Open Horizon Rally";
    if (id=="thousand_club") return "Car challenges and medals";
    if (id=="coop_challenges") return "Free roam co-op challenges";
    if (id=="sponsor_challenges") return "View sponsor challenges";
    if (id=="message_center") return "Open the message center";
    if (id=="native_multiplayer") return "Enter multiplayer";
    if (id=="head_tracking") return "Native head tracking options";
    return "Confirmation required";
  }
  if (id=="difficulty" && !enabled) return "Unavailable in this activity";
  if (!enabled && (id == "my_cars" || id == "change_car" || id == "car_tuning"))
    return NetworkOn() ? "Offline free roam only" : "Unavailable here";
  if (!enabled && (id == "garage" || id == "autoshow")) return "Unavailable here";
  if (id == "world_map") return "Explore Colorado";
  if (id == "friends") return state.friends_summary;
  if (id == "chat") return state.chat_summary;
  if (id == "friend_requests") return state.requests_summary;
  if (id == "social_connection") return state.connection_summary;
  if (id == "session_players") return state.session_summary;
  if (id == "session_join") return "Choose a friend\nSelect Join game\nConfirm before joining";
  if (id == "session_invite") return LiveCurrentSession()?"Choose who to invite\nInvites need acceptance":"Start or join a session first";
  if (id == "session_leave") return state.end_free_roam?
      "End free roam for everyone\nReturn to the native lobby\nConfirmation required":
      "Return through FH1's\nexit confirmation";
  if (id == "social_account") return state.account_summary;
  if (id == "social_help") return "1. Close the game and open the launcher.\n2. Online: choose Public server, This PC\n   or My own server.\n3. Launch the game again.\n\nNo answer? Check the server address,\navailability and your firewall.";
  if (id == "social_back") return "Return to friends, requests and online play";
  if (id == "social_stats") return "Single-player career and festival progress";
  if (id == "free_roam" || id == "create_game") {
    if(!LiveConnected())return "Requires Forza Live\nSee Connection & Help";
    if(!state.online_entry_available)return "Start from single-player\nColorado free roam";
    return id=="free_roam"?"Public free roam\nChoose a playlist":"Custom lobby\nSet rules and invite friends";
  }
  if (id == "my_cars" || id == "change_car") return "Choose an owned car";
  if (id == "garage") return "Fast travel for car upgrades";
  if (id == "autoshow") return "Fast travel to buy cars";
  if (id == "settings") return "Audio, graphics, controls and difficulty";
  if (id == "photo_mode") return "Capture your Horizon";
  if (id == "car_tuning") return "Adjust your current car setup";
  if (id == "my_profile") return "Your Horizon career";
  if (id == "difficulty") return "Driving assists and difficulty";
  if (id == "graphics") return "Display and graphics settings";
  if (id == "audio") return "Music, voice and sound";
  if (id == "controls") return "Controller layout";
  if (id == "hud") return "Driving display and notifications";
  if (id == "video") return "Brightness and subtitles";
  return "";
}
void ActionArtwork(Native& ui,Card& card,const menu::Tile& tile) {
  struct Art {const char* id;const char* path;float w,h,l,t,bw,bh;};
  static constexpr Art art[]{
    {"change_car","UI4\\Native_Menu\\change_car.tga",256,256,0,0,256,256},
    {"car_tuning","UI4\\Native_Menu\\tune_car.tga",256,256,0,0,256,256},
    {"garage","UI4\\Native_Menu\\garage.tga",256,256,0,0,256,256},
    {"autoshow","UI4\\Native_Menu\\autoshow.tga",256,256,0,0,256,256},
    {"friends","UI4\\Native_Menu\\social_friends.tga",256,256,0,0,256,256},
    {"free_roam","UI4\\Native_Menu\\social_world.tga",256,256,0,0,256,256},
    {"create_game","UI4\\Native_Menu\\social_game.tga",256,256,0,0,256,256},
    {"friend_requests","UI4\\Native_Menu\\social_requests.tga",256,256,0,0,256,256},
    {"chat","UI4\\Native_Menu\\social_chat.tga",256,256,0,0,256,256},
    {"social_connection","UI4\\Native_Menu\\social_connection.tga",256,256,0,0,256,256},
    {"social_account","UI4\\Native_Menu\\social_connection.tga",256,256,0,0,256,256},
    {"social_help","UI4\\Native_Menu\\social_connection.tga",256,256,0,0,256,256},
    {"session_join","UI4\\Native_Menu\\social_friends.tga",256,256,0,0,256,256},
    {"session_invite","UI4\\Native_Menu\\social_requests.tga",256,256,0,0,256,256},
  };
  const Art* image=nullptr;for(const auto& a:art)if(tile.id==a.id){image=&a;break;}
  if(!card.icon)return;
  const auto& d=card.donor;ui.Show(d.presentation,card.icon,image!=nullptr);
  if(!image)return;
  if(card.icon_action!=tile.id){
    const auto binding=ui.scratch+832;ui.Call(0x82664A38,{binding});
    Write(ui.base,binding+4,card.icon_material);Write(ui.base,binding+8,d.presentation);
    ui.Call(0x82F38E80,{binding,ui.String(std::string("GAME:\\Media\\UI\\Textures\\")+image->path),0,8});
    card.icon_action=tile.id;
  }
  // Clip the final quarter in UV space; the quad stays completely inside the
  // tile, so artwork cannot bleed into the neighboring action or footer.
  const float fit=208/std::max(image->bw,image->bh);
  const float w=image->bw*.75f*fit,h=image->bh*.75f*fit;
  Normalize(ui,d,card.icon);Point(ui,d,card.icon,tile.bounds.x+tile.bounds.width-w/2,tile.bounds.y+tile.bounds.height-h/2);
  ui.Scale(d.presentation,card.icon,w/100,h/100);Depth(ui,d,card.icon,1);
  const auto focus=state.controller.focused_tile_id();
  const bool selected=focus&&*focus==tile.id;
  ui.Color(d.presentation,card.icon_material,kTextColor);ui.Opacity(d.presentation,card.icon,selected?18:12);
  ui.CropImage(card.icon_material,image->l/image->w,image->t/image->h,image->bw*.75f/image->w,image->bh*.75f/image->h);
  if(tile.id=="social_connection"){
    // A complete, foreground plug is an actual connection indicator, rather
    // than a cropped decoration or an invented signal-strength meter.
    Point(ui,d,card.icon,tile.bounds.x+tile.bounds.width-48,tile.bounds.y+tile.bounds.height/2);
    ui.Scale(d.presentation,card.icon,.48f,.48f);
    ui.CropImage(card.icon_material,0,0,1,1);
    ui.Color(d.presentation,card.icon_material,LiveConnected()&&!selected?0xff007a:kTextColor);
    ui.Opacity(d.presentation,card.icon,LiveConnected()?100:45);
  }else if(state.controller.page_id()=="social"){
    ui.Opacity(d.presentation,card.icon,selected?14:9);
  }
  Label(ui,d,card.title,{tile.bounds.x+20,tile.bounds.y+12,tile.bounds.width-40,40},21,tile.label,tile.enabled?kTextColor:kDisabledTextColor);
}
bool BindHeader(Native& ui) {
  const auto& d = state.header;
  const auto panes=Descendants(ui.base,Named(ui,Named(ui,d.root,"CAR_LIST"),"HIGHLIGHT"),0xe0dc09b7);
  state.header_paper=panes.empty()?0:panes.front();
  state.header_material=Named(ui,state.header_paper,"Material866");
  if(!state.header_paper||!state.header_material)return false;
  PaperUV(ui,d,state.header_material);
  state.player_bar = Named(ui, d.root, "PlayerBar");
  if (!state.player_bar) state.player_bar = Named(ui, d.root, "PLAYER_BAR");
  state.credits = Named(ui, state.player_bar, "BottomRow");
  state.credits_text=Named(ui,state.credits,"TEXT_CR");
  state.credits_display=Named(ui,Named(ui,d.root,"CAR_STATS"),"TEXT_CAR_NAME");
  state.username = Named(ui, d.root, "TEXT_TIME");
  const auto papers=Descendants(ui.base,Named(ui,d.root,"TitleTape"),0x43a6b655);
  state.username_paper = papers.empty() ? 0u : papers.front();
  state.username_material=Named(ui,state.username_paper,"Material90");
  if (state.username_material) PaperUV(ui, d, state.username_material);
  state.status = Named(ui, d.root, "TEXT_STATUS");
  for (unsigned i = 0; i < 3; ++i) {
    const auto button = Named(ui, d.root, ("HelpButton" + std::to_string(i)).c_str());
    const auto label = Named(ui, button, "Text_BUTTON");
    if (i == 0) state.footer = label;
    else state.bumpers[i - 1] = label;
  }
  bool valid = true;
  for (const auto& [name, node] : std::initializer_list<std::pair<const char*, uint32_t>>{
      {"PlayerBar", state.player_bar}, {"PlayerBar.BottomRow", state.credits}, {"TEXT_CR",state.credits_text},
      {"Credits display",state.credits_display},
      {"TEXT_TIME", state.username}, {"Time.Pill_white", state.username_paper},
      {"Username.Material90",state.username_material},
      {"TEXT_STATUS", state.status}, {"HelpButton0.Text_BUTTON", state.footer},
      {"HelpButton1.Text_BUTTON", state.bumpers[0]}, {"HelpButton2.Text_BUTTON", state.bumpers[1]}})
    valid = Require("header", name, node) && valid;
  return valid;
}
std::string SocialLine(std::string text,size_t limit=48) {
  for(auto& c:text)if(static_cast<unsigned char>(c)<32)c=' ';
  size_t count=0,end=0;
  while(end<text.size()&&count<limit){
    ++end;
    while(end<text.size()&&(static_cast<unsigned char>(text[end])&0xc0)==0x80)++end;
    ++count;
  }
  if(end<text.size())text=text.substr(0,end)+"...";
  return text;
}
std::string RenderedText(Native& ui,uint32_t node) {
  const auto wrapper=node?Read(ui.base,node+4):0;
  if(!wrapper||GuestPtr(ui.base,wrapper+0xA)[0]!=5)return {};
  const auto object=Read(ui.base,wrapper);
  if(!object)return {};
  const auto length=Read(ui.base,object+0x94),capacity=Read(ui.base,object+0x98);
  if(length>240||length>capacity)return {};
  const auto chars=capacity<8?object+0x84:Read(ui.base,object+0x84);
  if(!chars)return {};
  std::string text;
  for(unsigned i=0;i<length&&text.size()<116;++i){
    uint32_t c=uint32_t(GuestPtr(ui.base,chars+2*i)[0])<<8|GuestPtr(ui.base,chars+2*i+1)[0];
    if(c>=0xD800&&c<=0xDBFF&&i+1<length){
      const uint32_t low=uint32_t(GuestPtr(ui.base,chars+2*i+2)[0])<<8|GuestPtr(ui.base,chars+2*i+3)[0];
      if(low>=0xDC00&&low<=0xDFFF){c=0x10000+((c-0xD800)<<10)+(low-0xDC00);++i;}
    }
    if(c<32||c==127)c=' ';
    if(c>=0xD800&&c<=0xDFFF)c=0xFFFD;
    if(c<0x80)text+=char(c);
    else if(c<0x800){text+=char(0xC0|(c>>6));text+=char(0x80|(c&63));}
    else if(c<0x10000){text+=char(0xE0|(c>>12));text+=char(0x80|((c>>6)&63));text+=char(0x80|(c&63));}
    else{ text+=char(0xF0|(c>>18));text+=char(0x80|((c>>12)&63));text+=char(0x80|((c>>6)&63));text+=char(0x80|(c&63));}
  }
  return text;
}
std::string NativeRowLabel(Native& ui,uint32_t text) {
  // The original row owns this IString reference. 82F3A728 calls its +0x1C
  // accessor on the UI thread, then copies the returned UTF-16 string.
  if (!text) return {};
  const auto chars = ui.Virtual(text,0x1C,{text});
  if (!chars) return {};
  std::string result;
  for (unsigned i=0;i<240;++i) {
    const auto* p = GuestPtr(ui.base,chars+2*i);
    uint32_t c = uint32_t(p[0])<<8|p[1];
    if (!c) return result;
    if (c>=0xD800 && c<=0xDBFF && i+1<240) {
      const auto* next=GuestPtr(ui.base,chars+2*i+2);
      const uint32_t low=uint32_t(next[0])<<8|next[1];
      if (low>=0xDC00 && low<=0xDFFF) {c=0x10000+((c-0xD800)<<10)+low-0xDC00;++i;}
    }
    if (c<32 || c==127) c=' ';
    if (c>=0xD800 && c<=0xDFFF) c=0xFFFD;
    if (c<0x80) result+=char(c);
    else if (c<0x800) {result+=char(0xC0|(c>>6));result+=char(0x80|(c&63));}
    else if (c<0x10000) {result+=char(0xE0|(c>>12));result+=char(0x80|((c>>6)&63));result+=char(0x80|(c&63));}
    else {result+=char(0xF0|(c>>18));result+=char(0x80|((c>>12)&63));result+=char(0x80|((c>>6)&63));result+=char(0x80|(c&63));}
  }
  return {}; // Reject an unterminated/oversized native label.
}
void RefreshData(Native& ui) {
  ui.ctx.r3.u64 = 0; ui.ctx.r4.u64 = ui.scratch + 1800; ui.ctx.r5.u64 = 16;
  __imp__XamUserGetName(ui.ctx, ui.base);
  if (!ui.ctx.r3.u32) {
    state.gamertag.clear();
    for (unsigned i = 0; i < 15 && GuestPtr(ui.base, ui.scratch + 1800)[i]; ++i)
      state.gamertag += char(GuestPtr(ui.base, ui.scratch + 1800)[i]);
  }
  state.connection = LiveConnected() ? "ONLINE" : "";
  {
    // PlayerBar follows the loaded online car; the garage photo donor can
    // still describe the single-player owned car after a multiplayer transition.
    const auto text=RenderedText(ui,Named(ui,state.player_bar,"TEXT_PI"));
    unsigned pi=0;bool valid=!text.empty();
    for(const auto c:text)if(c>='0'&&c<='9')pi=pi*10+unsigned(c-'0');else if(c!=' ')valid=false;
    const auto player=ui.Call(0x82915BE0,{});
    const auto table=player?Read(ui.base,player):0;
    const auto getter=table?Read(ui.base,table+0x78):0;
    const auto car=getter?ui.Call(getter,{player}):0;
    const auto model=car?ui.Call(0x826BDCB0,{car}):0;
    const auto name=RenderedText(ui,Named(ui,state.player_bar,"TEXT_CAR"));
    if(valid&&pi&&pi<=9999&&model&&!name.empty())
      LiveSetCarDetails({Read(ui.base,model),uint16_t(pi),uint8_t(ui.Call(0x825E6518,{car})),name});
    else LiveSetCarDetails({});
  }
  state.session_summary="No active session.\nStart Horizon World or create a game.\n\nThis list shows session members,\nnot every player on the server.";
  if(LiveConnected())if(const auto session=LiveCurrentSession()){
    const auto roster=LiveRoster();
    const int host=OnlineGameIsHost()==1||session->hosted?1:0;
    state.session_summary=std::string(host==1?"YOU ARE HOST":host==0?"CONNECTED AS GUEST":
        session->hosted?"HOSTING SESSION":"JOINED SESSION")+" / "+std::to_string(session->players)+
        "/"+std::to_string(session->capacity)+" PLAYERS\n";
    std::vector<std::string> rows;
    const auto self=IdentityXuid();
    for(const auto xuid:session->members){
      if(xuid==self){
        auto line=state.gamertag+(host==1?" (YOU / HOST)":" (YOU)");
        if(session->local_car.model&&session->local_car.pi)
          line+=" / "+SocialLine(session->local_car.name,22)+" / "+social_presence::ClassName(session->local_car.car_class)+std::to_string(session->local_car.pi);
        else line+=" / car details pending";
        rows.push_back(line);continue;
      }
      const auto player=std::find_if(roster.begin(),roster.end(),[&](const auto& p){return p.xuid==xuid;});
      if(player==roster.end()){rows.push_back("Player information pending");continue;}
      std::string line=SocialLine(player->gamertag,15);
      uint64_t advertised=0;for(unsigned i=0;i<8;++i)advertised=(advertised<<8)|player->session[i];
      if(advertised==session->id&&(player->state&0x10))line+=" (HOST)";
      if(const auto car=social_presence::Read(player->extra))line+=" / "+SocialLine(car->name,22)+
          " / "+social_presence::ClassName(car->car_class)+std::to_string(car->pi);
      else line+=" / car not published";
      rows.push_back(line);
    }
    if(rows.empty())state.session_summary+="Waiting for session member details.\n";
    else{
      state.session_offset=(state.session_offset/5*5)%((rows.size()+4)/5*5);
      for(size_t i=state.session_offset;i<std::min(rows.size(),state.session_offset+5);++i)
        state.session_summary+=rows[i]+"\n";
      if(rows.size()>5)state.session_summary+="Select for next players";
    }
  }
  if(LiveConnected()){
    unsigned friends=0,online=0,incoming=0,sent=0;
    const bool active=LiveFriendsActive();
    if(active){
      for(const auto& relation:LiveRelations())switch(relation.kind){
        case LiveRelationKind::kFriend:++friends;if(relation.state&1)++online;break;
        case LiveRelationKind::kAsksUs:++incoming;break;
        case LiveRelationKind::kWeAsked:++sent;break;
        default:break;
      }
    }else for(const auto& player:LiveFriendsList()){
      ++friends;if(player.state&1)++online;
    }
    state.friends_summary=std::to_string(online)+" ONLINE / "+std::to_string(friends)+" FRIENDS\n";
    unsigned shown=0;
    for(const auto& player:LiveFriendsList()){
      if(!(player.state&1)||shown==3)continue;
      state.friends_summary+=SocialLine(player.gamertag+" - "+(player.status.empty()?"Online":player.status))+"\n";
      ++shown;
    }
    if(!shown)state.friends_summary+="No friends online right now.\n";
    if(online>shown)state.friends_summary+="+ "+std::to_string(online-shown)+" more online\n";
    state.friends_summary+="Select to add, message or manage friends.";
    state.requests_summary=active?std::to_string(incoming)+" INCOMING\n"+std::to_string(sent)+" SENT\nAccept, decline or cancel":"List not received yet.\nSelect to refresh requests.";
    const auto chat=LiveGetChatPreview();
    state.chat_summary=std::to_string(chat.unread)+" UNREAD\n";
    state.chat_summary+=chat.latest.empty()?"No messages yet.\nSelect to start chatting.":SocialLine(chat.latest,24)+"\nSelect to open chat.";
    const auto room_players=LiveRoster().size()+1;
    state.connection_summary=(LiveSessionIds().empty()?"ONLINE / ":"SESSION ACTIVE / ")+std::to_string(room_players)+
        (room_players==1?" PLAYER IN ROOM":" PLAYERS IN ROOM");
  }else{
    const bool configured=NetworkOn();
    state.friends_summary=configured?"Waiting for the Forza Live server.\nYour friends list is unavailable.\nSelect for connection help.":"Play Horizon World or host a custom game.\nAdd friends and use world or private chat.\n\nForza Live is off for this game session.\nSelect to see how to connect.";
    state.requests_summary="Connect to Forza Live.\nSelect for setup help.";
    state.chat_summary="Connect to Forza Live.\nSelect for setup help.";
    state.connection_summary=configured?"WAITING FOR SERVER / SELECT FOR HELP":"OFFLINE / SELECT FOR SETUP HELP";
  }
  state.account_summary="PLAYER: "+SocialLine(state.gamertag,15)+"\n";
  state.account_summary+=LiveConnected()?"SERVER CONNECTED\n":"NO LIVE SERVER CONNECTION\n";
  uint32_t ip=0;uint16_t port=0;
  if(LiveServerHttp(ip,port)){
    char endpoint[80];std::snprintf(endpoint,sizeof(endpoint),"Title service: %u.%u.%u.%u:%u\n",ip>>24,(ip>>16)&255,(ip>>8)&255,ip&255,unsigned(port));
    state.account_summary+=endpoint;
  }
  state.account_summary+="\nFriends and chat use your launcher identity.\nPublic games and custom lobbies use FH1's\noriginal multiplayer menus.";
  state.recovery = Read(ui.base, state.screen + 0x1dc) && ActionAvailable(ui.base, 2) &&
      fh1_car_recovery::Available(ui.ctx, ui.base);
  // Same completion getter used by the original Statistics General page.
  const auto services = Read(ui.base, 0x833caae4);
  if (!services || !ui.Call(0x82f12410, {services}) ||
      !Read(ui.base, services + 0x10) || !Read(ui.base, services + 0x6c)) return;
  const auto facade = ui.Call(0x825ec700, {});
  const auto player = ui.Virtual(facade, 0x28, {facade});
  const auto profile = ui.Virtual(player, 0x58, {player});
  if (!profile) return;
  ui.Call(0x8269e150, {profile, 1});
  const double percentage = ui.ctx.f1.f64;
  if (std::isfinite(percentage)) state.completion = float(std::clamp(percentage / 100, 0., 1.));
}
void HeaderLayout(Native& ui) {
  const auto& d = state.header;
  const fh1_menu_components::Rect r{1034,65,182,27};
  Normalize(ui,d,state.header_paper);
  Point(ui,d,state.header_paper,r.x+r.w/2,r.y+r.h/2);
  ui.Scale(d.presentation,state.header_paper,r.w/100,r.h/100);
  ui.RotationZ(d.presentation,state.header_paper,0);Depth(ui,d,state.header_paper,3);
  ui.Opacity(d.presentation,state.header_paper,100);
  ui.Color(d.presentation,state.header_material,0x171517);
  // Preserve PlayerBar's native wristband/class/car bindings on the left.
  // Keep the native localized currency binding active, then mirror its text
  // into an independent right-aligned label. Native credits auto-layout must
  // not reposition our display when the balance changes.
  ui.Show(d.presentation,state.credits,false);
  ui.Show(d.presentation,state.credits_display,ui.CopyText(d.presentation,state.credits_text,state.credits_display,state.credits_hash));
  Normalize(ui,d,state.credits_display);Point(ui,d,state.credits_display,1204,78);
  ui.Scale(d.presentation,state.credits_display,1,1);
  ui.TextStyle(d.access,d.presentation,state.credits_display,18,158.f/1200,27.f/720,2);
  ui.Color(d.presentation,state.credits_display,0xffffff,true);Depth(ui,d,state.credits_display,-5);
  Normalize(ui, d, state.username_paper);
  Point(ui, d, state.username_paper, 1086, 50);
  ui.Scale(d.presentation, state.username_paper, 2.6f, .36f);
  ui.Color(d.presentation,state.username_material,0x171517);
  Label(ui, d, state.username, {976, 33, 228, 34}, 18, state.gamertag, 0xffffff, 2);
  Label(ui, d, state.status, {996, 652, 220, 32}, 14, state.connection, 0xffffff, 2);
  for (auto node : {state.footer,state.bumpers[0],state.bumpers[1]})
    ui.Show(d.presentation,node,false);
  const bool subpage=state.social_help||state.social_session;
  const bool quit=ActionAvailable(ui.base,17);
  if (!state.footer_bound || state.footer_recovery!=state.recovery || state.footer_social_help!=subpage || state.footer_quit!=quit) {
    state.footer_bound=FooterButtons(ui,state.screen,
        {{0,"SELECT"},{1,subpage?"BACK":"RESUME"},{8,"PREV TAB"},{9,"NEXT TAB"},
         {14,state.recovery?"RESET CAR POSITION":""},{3,quit?"QUIT":""}});
    state.footer_quit=quit;
    state.footer_recovery=state.recovery;
    state.footer_social_help=subpage;
  }
}
void StyleCard(Native& ui, Card& card, const menu::Rect& bounds,
               const std::string& title, bool selected, bool enabled,
               bool tab = false) {
  auto& d = card.donor;
  Normalize(ui, d, card.paper);
  Point(ui, d, card.paper, bounds.x + bounds.width / 2,
        bounds.y + bounds.height / 2);
  ui.RotationZ(d.presentation, card.paper, 0);
  Depth(ui, d, card.paper, 3);
  // Native garage paper is a fixed mesh. Text is a separate branch and
  // never inherits the panel's nonuniform scaling.
  ui.Scale(d.presentation, card.paper, float(bounds.width / 100),
           float(bounds.height / 100));
  if (card.material)
    ui.Color(d.presentation, card.material, selected ? 0xff007a : tab?0x302b30:kPanelColor);
  ui.Opacity(d.presentation, card.paper, enabled ? 100.f : 48.f);
  Label(ui, d, card.title,
        {bounds.x + 20, bounds.y + (tab ? 0 : 12), bounds.width - 40,
         tab ? bounds.height : 40}, tab ? 23.f : 27.f, title,
        enabled ? kTextColor : kDisabledTextColor, tab ? 1 : 0);
}
void Progress(Native& ui, Card& card, const menu::Rect& bounds) {
  const auto& d = card.donor;
  const float fraction = state.completion.value_or(0);
  const menu::Rect track{bounds.x + 24, bounds.y + bounds.height - 24,
                         bounds.width - 48, 8};
  for (const auto node : {card.progress_track, card.progress_fill}) Normalize(ui, d, node);
  Depth(ui, d, card.progress_track, -1);
  Depth(ui, d, card.progress_fill, -2);
  Point(ui, d, card.progress_track, track.x + track.width / 2, track.y + track.height / 2);
  Point(ui, d, card.progress_fill, track.x + track.width * fraction / 2, track.y + track.height / 2);
  ui.Scale(d.presentation, card.progress_track, float(track.width / 100), .08f);
  ui.Scale(d.presentation, card.progress_fill, float(track.width * fraction / 100), .08f);
  ui.Show(d.presentation, card.progress_track, true);
  ui.Show(d.presentation, card.progress_fill, state.completion.has_value() && fraction > 0);
  ui.Color(d.presentation, Named(ui, card.progress_track, "Material100"), 0x777177);
  ui.Color(d.presentation, Named(ui, card.progress_fill, "Material90"), 0xff007a);
  char label[48];
  if (state.completion) std::snprintf(label, sizeof(label), "%.1f%% COMPLETE", fraction * 100);
  else std::snprintf(label, sizeof(label), "PROGRESS UNAVAILABLE");
  Label(ui, d, card.caption, {bounds.x + bounds.width - 228, bounds.y + 12, 204, 38},
        20, label, kTextColor, 2);
  Label(ui, d, card.title, {bounds.x + 20, bounds.y + 12, bounds.width - 264, 40},
        23, "FESTIVAL PROGRESS",kTextColor);
}
bool BindCar(Native& ui) {
  auto& d = state.cards[0].donor;
  auto& car = state.car;
  car.root = Named(ui, d.root, "CAR_STATS");
  car.name = Named(ui, car.root, "TEXT_CAR_NAME");
  car.make = Named(ui, car.root, "TEXT_MANUFACTURER");
  car.logo = Named(ui, car.root, "LOGO");
  car.logo_material = Named(ui, car.logo, "Material_Car_Logo");
  car.image=Named(ui,car.root,"Car_Thumb_");
  car.material=Named(ui,car.root,"Material_Car_Ima");
  if(car.image){
    const auto instance=Read(ui.base,car.image+4);
    car.photo_x=Float(ui.base,instance+0x24);car.photo_y=Float(ui.base,instance+0x28);
    float sx=car.photo_x,sy=car.photo_y;
    unsigned depth=0;
    for(auto parent=Read(ui.base,car.image+12);parent&&parent!=d.root&&depth++<100;parent=Read(ui.base,parent+12)){
      const auto wrapper=Read(ui.base,parent+4);
      if(GuestPtr(ui.base,wrapper+0xA)[0]==8)break;
      sx*=Float(ui.base,wrapper+0x24);sy*=Float(ui.base,wrapper+0x28);
    }
    car.photo_x=sx;car.photo_y=sy;
    if(!std::isfinite(sx)||!std::isfinite(sy)||std::abs(sx)<.001f||std::abs(sy)<.001f)return false;
    REXLOG_INFO("Native pause photo: authored scale {} {} effective {} {}",car.photo_x,car.photo_y,sx,sy);
  }
  car.stats = Named(ui, car.root, "STATS");
  car.controller = d.ControllerByVtable(ui, 0x82074f4c);
  car.badge=Named(ui,car.root,"CLASS_SM");
  const auto pi=Named(ui,car.badge,"TEXT_PI");
  const auto manager=d.Components(ui),begin=manager?Read(ui.base,manager+0x28):0,end=manager?Read(ui.base,manager+0x2C):0;
  if(begin&&end>=begin&&end-begin<=8192&&(end-begin)%8==0)
    for(auto item=begin;item<end;item+=8){const auto controller=Read(ui.base,item+4);
      if(controller&&Read(ui.base,controller)==0x8207513C&&Read(ui.base,controller+0xA8)==pi)car.badge_controller=controller;}
  for (size_t i = 0; i < car.rows.size(); ++i) {
    const auto suffix = i ? std::to_string(i + 1) : std::string{};
    car.rows[i] = Named(ui, car.stats, ("CAR_STATS_PERFORMANCE_BARS" + suffix).c_str());
    car.labels[i] = Named(ui, car.rows[i], "TEXT_NAME");
    car.values[i] = Named(ui, car.rows[i], "TEXT_VALUE");
    if (!car.labels[i] || !car.values[i]) {
      REXLOG_WARN("Native pause binding: car row {} node={:08X} name={:08X} value={:08X}",
                  i, car.rows[i], car.labels[i], car.values[i]);
      return false;
    }
  }
  bool valid = true;
  for (const auto& [name, node] : std::initializer_list<std::pair<const char*, uint32_t>>{
      {"CAR_STATS", car.root}, {"TEXT_CAR_NAME", car.name}, {"TEXT_MANUFACTURER", car.make},
      {"Car_Thumb_", car.image}, {"Material_Car_Ima", car.material}, {"STATS", car.stats},
      {"CAR_STATS controller", car.controller},{"CLASS_SM",car.badge},{"Class badge controller",car.badge_controller}})
    valid = Require("car", name, node) && valid;
  return valid;
}
void CarData(Native& ui) {
  auto& car = state.car;
  auto& d = state.cards[0].donor;
  const auto current = fh1_current_car::ReadCurrentCar(ui);
  car.badge_ready=current&&fh1_current_car::BindCurrentClass(ui,car.badge_controller,current.model_id,&car.badge_value);
  if (!current || (car.bound && current.owned_id == car.owned && current.model_id == car.model))
    return;
  const bool names = fh1_current_car::BindNames(ui, d.presentation, car.name, car.make, current.model_id, car.logo_material, &car.logo_ready,true,&car.logo_fit);
  const bool specs = fh1_current_car::BindPerformance(ui, car.controller, current.owned_id);
  if (!current.thumbnail.empty()) {
    // This operation is synchronous. Retain no host-allocated binding across
    // donor lifetime; native resource ownership stays with the presentation.
    const auto binding = ui.scratch + 832;
    ui.Call(0x82664a38, {binding});
    Write(ui.base, binding + 4, car.material);
    Write(ui.base, binding + 8, d.presentation);
    ui.Call(0x82f38e80, {binding, ui.String(current.thumbnail), 0, 8});
  }
  car.owned = current.owned_id;
  car.model = current.model_id;
  car.bound = names && specs;
}
void CarLayout(Native& ui, const menu::Rect& bounds) {
  auto& car = state.car;
  auto& d = state.cards[0].donor;
  const auto content = layout::Car(bounds);
  // Native names/data are retained; only their anchors and fitting boxes move.
  Normalize(ui, d, car.name);
  Normalize(ui, d, car.make);
  Point(ui, d, car.name, content.name.x+78, content.name.y + 12);
  Point(ui, d, car.make, content.name.x+78, content.name.y + 35);
  ui.TextStyle(d.access, d.presentation, car.name, 23,
               float((content.name.width-168) / 1200), .045f, 0);
  ui.TextStyle(d.access, d.presentation, car.make, 18,
               float((content.name.width-78) / 1200), .035f, 0);
  ui.Color(d.presentation, car.name, kTextColor, true);
  ui.Color(d.presentation, car.make, kSecondaryTextColor, true);
  ui.Show(d.presentation,car.badge,car.badge_ready);
  if(car.badge_ready){Normalize(ui,d,car.badge);Depth(ui,d,car.badge,-3);
    Point(ui,d,car.badge,bounds.x+bounds.width-51,content.name.y+12);
    ui.Scale(d.presentation,car.badge,.9f,.9f);ui.RotationZ(d.presentation,car.badge,0);}
  Normalize(ui, d, car.image);
  Depth(ui, d, car.name, -3); Depth(ui, d, car.make, -3);
  Depth(ui, d, car.image, -1);
  // Preserve the authored thumbnail's X/Y ratio. Its animated Y scale is
  // owned by FH1, so resizing the child stretches it; zoom its parent equally
  // on both axes and counter-scale only the center position.
  const float zoom=float(std::min(content.photo.width/320,content.photo.height/128));
  ui.Position(d.presentation,car.image,
      float(content.photo.x+content.photo.width/2-640)/zoom,
      float(360-content.photo.y-content.photo.height/2+14)/zoom);
  ui.Show(d.presentation,car.logo,car.logo_ready);
  if(car.logo_ready){Normalize(ui,d,car.logo);Depth(ui,d,car.logo,-3);
    Point(ui,d,car.logo,content.name.x+32+car.logo_fit.dx,content.name.y+23+car.logo_fit.dy);
    ui.Scale(d.presentation,car.logo,car.logo_fit.sx,car.logo_fit.sy);ui.RotationZ(d.presentation,car.logo,0);
    ui.Opacity(d.presentation,car.logo,100);ui.Color(d.presentation,car.logo_material,0xffffff);}
  ui.Scale(d.presentation,car.image,car.photo_x,car.photo_y);
  for (size_t i = 0; i < car.rows.size(); ++i) {
    const double width = content.specifications.width / car.rows.size();
    const double x = content.specifications.x + width * i;
    const double y = content.specifications.y;
    for (auto node : {car.labels[i], car.values[i]}) Normalize(ui, d, node);
    Depth(ui, d, car.labels[i], -3); Depth(ui, d, car.values[i], -3);
    Point(ui, d, car.labels[i], x + width / 2, y + 10);
    Point(ui, d, car.values[i], x + width / 2, y + 32);
    ui.TextStyle(d.access, d.presentation, car.labels[i], 14,
                 float((width - 8) / 1200), .032f, 1);
    ui.TextStyle(d.access, d.presentation, car.values[i], 16,
                 float((width - 8) / 1200), .035f, 1);
    ui.Color(d.presentation,car.labels[i],kSecondaryTextColor,true);
    ui.Color(d.presentation,car.values[i],kTextColor,true);
  }
  ui.Scale(d.presentation,Read(ui.base,car.image+12),zoom,zoom);
  // The dedicated photo branch is excluded from the paper shear. Translate
  // its center into the composition without compressing or skewing the car.
  const float x=float(content.photo.x+content.photo.width/2-640);
  const float y=float(360-content.photo.y-content.photo.height/2+14);
  using namespace fh1_menu_components;
  ui.Position(d.presentation,Read(ui.base,car.image+12),
      (kMenuScaleX-1)*x+kMenuShear*y+kMenuTranslateX,0);
}
void StockVisibility(Native& ui, bool restore) {
  if (!state.stock_captured) return;
  // +370 suppresses drawing only. The neighboring +371 flag would stop the
  // original presentation's event/update work and must remain untouched.
  const bool footer=state.stock_footer.Ready() && state.visible;
  ui.Call(0x82feb480, {state.engine, restore ? uint32_t(state.stock_suppression) : footer?0u:1u});
  GuestPtr(ui.base, state.stock_render_root + 0xfc)[0] = restore ? state.stock_visibility : uint8_t(footer);
  if (!restore && footer) state.stock_footer.ApplyVisibility(ui);
  const auto manager = ui.Virtual(state.engine, 0x54, {state.engine});
  const auto adapter = ui.Virtual(manager, 0x3c, {manager});
  if (adapter) GuestPtr(ui.base, adapter + 0x110)[0] = 1;
}
void Visibility(Native& ui, bool visible) {
  for (auto& card : state.cards) card.donor.SetVisible(ui, visible);
  for (auto& tab : state.tabs) tab.donor.SetVisible(ui, visible);
  state.header.SetVisible(ui, visible);
  if (state.ready) StockVisibility(ui, false);
}
void Release(Native& ui, bool restore) {
  native_pause_edges::ClearTabs();
  fh1_map_preview::Release(ui.ctx, ui.base);
  if (restore) {
    for (const auto& [node,visible]:state.stock_nodes) ui.Show(state.engine,node,visible);
    ui.Call(0x827d6348,{state.screen});
    StockVisibility(ui, true);
  }
  for (auto& card : state.cards) card.donor.Release(ui);
  for (auto& tab : state.tabs) tab.donor.Release(ui);
  state.header.Release(ui);
  state = {};
}
void Resume(Native& ui) {
  return_bookmark.reset();
  const auto screen = state.screen;
  ui.Call(0x827DABD0, {screen});
  if (!Read(ui.base, screen + 0x1DC)) return;
  Page(ui.ctx, ui.base, screen, false);
  // The native done callback can start loading the gameplay interface before
  // the pause's deferred destructor runs. Release our extra presentations now
  // so their textures do not overlap that load in the front-end GPU heap.
  Release(ui, true);
  offline_cars::PrepareResume(ui.base, screen);
  if (!fh1_pause_actions::Resume(ui.ctx, ui.base, screen)) {
    offline_cars::ClearReload(ui.base);
    AfterBind(ui.ctx, ui.base, screen);
  }
}
void SocialSession(Native& ui,bool show) {
  state.social_session=show;state.social_help=false;
  state.controller.SetPages(menu::MakePausePages(Capabilities(ui),false,show));
  state.controller.SelectPage("social");
  state.controller.FocusTile(show?"social_back":"social_connection");
  state.controller.Resume();state.previous_pad=0xffff;
  state.refreshed={};
}
void SocialHelp(Native& ui,bool show) {
  if(!show&&state.help_from_session){SocialSession(ui,true);return;}
  if(show)state.help_from_session=state.social_session;
  if(show)state.social_return=state.controller.focused_tile_id().value_or("social_connection");
  state.social_help=show;state.social_session=false;
  state.controller.SetPages(menu::MakePausePages(Capabilities(ui),show));
  state.controller.SelectPage("social");
  state.controller.FocusTile(show?"social_back":state.social_return);
  state.controller.Resume();state.previous_pad=0xffff;
}
bool DispatchNative(Native& ui, uint32_t action) {
  const auto screen=state.screen;
  const bool dispatched=fh1_pause_actions::Dispatch(ui.ctx,ui.base,screen,action);
  if (!Owns(screen)) return dispatched;
  state.exit_dialog=fh1_pause_actions::QueuedDialog(ui.ctx,ui.base,screen);
  // These branches always open a confirmation/difficulty child, including
  // Rally/Multiplayer eligibility prompts. No accepted child means no handoff.
  if (menu::NativePauseDialog(action) || action==18)
    return dispatched && state.exit_dialog.has_value();
  // Message Center and Club otherwise invoke their original bound callbacks.
  if ((action==5 || action==19) && !state.exit_dialog &&
      !Read(ui.base,screen+(action==5?500:516))) return false;
  return dispatched;
}
void Activate(Native& ui, const std::string& id) {
  const auto action = menu::FindPauseAction(id);
  if (!action) return;
  if (*action == menu::PauseAction::SettingsPage) {
    state.controller.SelectPage("settings");
    return;
  }
  if(*action==menu::PauseAction::ConnectionGuide||*action==menu::PauseAction::SocialRoot){
    if(*action==menu::PauseAction::ConnectionGuide)SocialHelp(ui,true);
    else if(state.social_help)SocialHelp(ui,false);
    else SocialSession(ui,false);
    return;
  }
  if(*action==menu::PauseAction::SessionPage){SocialSession(ui,true);return;}
  if(*action==menu::PauseAction::SessionNext){state.session_offset+=5;state.refreshed={};return;}
  if(*action==menu::PauseAction::InviteFriends){
    if(LiveConnected())if(const auto session=LiveCurrentSession())
      if(!(session->flags&0x400))LiveOpenInvitePicker(session->info.data(),"pause Social");
    return;
  }
  if (*action == menu::PauseAction::Friends || *action == menu::PauseAction::Chat || *action==menu::PauseAction::FriendRequests) {
    if (!LiveConnected())SocialHelp(ui,true);
    else LiveOpenFriendsPage(*action==menu::PauseAction::Chat?LiveFriendsPage::Chat:
        *action==menu::PauseAction::FriendRequests?LiveFriendsPage::Requests:LiveFriendsPage::Friends);
    return;
  }
  // Revalidate gates immediately before transitions, not just on pause entry.
  const auto caps = Capabilities(ui);
  const auto native_action = menu::NativePauseAction(id);
  if (*action == menu::PauseAction::NativeActivity &&
      (!native_action || !ActionAvailable(ui.base,*native_action))) return;
  if (*action == menu::PauseAction::Difficulty &&
      !caps.my_profile && !ActionAvailable(ui.base,18)) return;
  if ((*action == menu::PauseAction::FreeRoam && !caps.free_roam) ||
      (*action == menu::PauseAction::CreateGame && !caps.create_game)) return;
  if(*action==menu::PauseAction::LeaveSession&&!caps.leave_session)return;
  if (*action == menu::PauseAction::TuneCar && !caps.car_tuning) return;
  if (*action == menu::PauseAction::ChangeCar && !caps.change_car) return;
  if ((*action == menu::PauseAction::WorldMap || *action == menu::PauseAction::ChangeCar ||
       *action == menu::PauseAction::Garage || *action == menu::PauseAction::Autoshow) && !caps.world_map)
    return;
  const auto screen = state.screen;
  offline_cars::BeforeTransition(ui.base, *action);
  return_bookmark = ReturnBookmark{Read(ui.base, screen + 0x10c),
      state.controller.page_id().value_or("home"),
      state.controller.focused_tile_id().value_or(""),state.social_help,state.social_return,
      state.social_session,state.help_from_session};
  if (*action == menu::PauseAction::Graphics) {
    // The native graphics adapter hides its parent only after creating the
    // child. Failed allocation must leave this page interactive.
    ui.Call(0x827ebee0, {screen, 2, 0, 0x7000});
    if (state.screen == screen && state.visible) return_bookmark.reset();
    return;
  }
  Page(ui.ctx, ui.base, screen, false);
  bool requested = false;
  using A = menu::PauseAction;
  switch (*action) {
    case A::NativeActivity:
      requested=DispatchNative(ui,*native_action);
      break;
    case A::WorldMap: requested = fh1_pause_actions::Dispatch(ui.ctx, ui.base, screen, 2); break;
    case A::ChangeCar: requested = offline_cars::Open(ui.ctx, ui.base, screen); break;
    case A::TuneCar: requested = car_tuning_menu::Open(ui.ctx, ui.base, screen); break;
    case A::Garage: case A::Autoshow:
      requested = fh1_festival_travel::Open(ui.ctx, ui.base, screen,
          *action == A::Garage ? fh1_festival_travel::Destination::Garage : fh1_festival_travel::Destination::Autoshow); break;
    case A::FestivalProgress: requested = fh1_pause_actions::OpenProgress(ui.ctx, ui.base, screen); break;
    case A::SocialStats: requested = fh1_pause_actions::OpenProgress(ui.ctx, ui.base, screen); break;
    case A::LeaveSession:
      if(const auto exit=LeaveAction(ui.base)){
        requested=DispatchNative(ui,*exit);
      }
      break;
    case A::FreeRoam: case A::CreateGame: {
      online_intent=OnlineIntent{*action,Clock::now()+std::chrono::seconds(30)};
      requested=fh1_pause_actions::OpenMultiplayer(ui.ctx,ui.base,screen);
      if(!requested)online_intent.reset();
      break;
    }
    case A::PhotoMode: requested = caps.photo_mode && fh1_pause_actions::Dispatch(ui.ctx, ui.base, screen, 11); break;
    case A::MyProfile: requested = caps.my_profile && fh1_pause_actions::Dispatch(ui.ctx, ui.base, screen, 12); break;
    case A::Audio: requested = fh1_pause_actions::OpenAudio(ui.ctx, ui.base, screen); break;
    case A::Controls: requested = fh1_pause_actions::OpenControls(ui.ctx, ui.base, screen); break;
    case A::Hud: requested = fh1_pause_actions::OpenHud(ui.ctx, ui.base, screen); break;
    case A::Video: requested = fh1_pause_actions::OpenVideo(ui.ctx, ui.base, screen); break;
    case A::Difficulty: {
      if (ActionAvailable(ui.base,18)) {
        requested=DispatchNative(ui,18);
        break;
      }
      const auto params = ui.Call(0x824265c8, {0x88});
      if (params) {
        ui.Call(0x825a5640, {params, 0});
        Write(ui.base, params + 4, 0); // Original COptions general-assists context.
        if (const auto title = ui.TextRef("DIFFICULTY")) {
          ui.Call(0x82471820, {params + 0xc, title});
          ui.Call(0x824ceab0, {title});
        }
        Write(ui.base, ui.scratch, params);
        ui.Call(0x82675b00, {screen, 5, ui.scratch, 0});
        requested = true;
      }
      break;
    }
    default: break;
  }
  if (!requested && state.screen == screen) {
    return_bookmark.reset();
    Page(ui.ctx, ui.base, screen, true);
  }
}
void OpenQuit(Native& ui) {
  const auto screen=state.screen;
  if(!ActionAvailable(ui.base,17))return;
  return_bookmark=ReturnBookmark{Read(ui.base,screen+0x10c),
      state.controller.page_id().value_or("home"),state.controller.focused_tile_id().value_or(""),
      state.social_help,state.social_return,state.social_session,state.help_from_session};
  Page(ui.ctx,ui.base,screen,false);
  uint32_t presence=0,region=0;
  const bool activity_known=LiveLocalActivity(presence,region);
  const bool desktop_context=menu::DesktopQuitContext(Capabilities(ui),activity_known,presence,region);
  const bool requested=desktop_context && native_quit::Available(ui.base,screen) ?
      native_quit::Open(ui.ctx,ui.base,screen,Read(ui.base,screen+0x10c)) :
      DispatchNative(ui,17);
  if (Owns(screen)) state.exit_dialog=fh1_pause_actions::QueuedDialog(ui.ctx,ui.base,screen);
  if(!requested || (Owns(screen) && !state.exit_dialog)){
    native_quit::Cancel();state.exit_dialog.reset();return_bookmark.reset();Page(ui.ctx,ui.base,screen,true);
  }
}
std::optional<uint16_t> Pad(Native& ui) {
  ui.ctx.r3.u64 = 0; ui.ctx.r4.u64 = 0; ui.ctx.r5.u64 = ui.scratch;
  __imp__XamInputGetState(ui.ctx, ui.base);
  if (ui.ctx.r3.u32) return std::nullopt;
  auto held = uint16_t(Read(ui.base, ui.scratch + 4) >> 16);
  const auto axes = Read(ui.base, ui.scratch + 8);
  const auto x = int16_t(axes >> 16), y = int16_t(axes);
  if (x < -16000) held |= 4;
  if (x > 16000) held |= 8;
  if (y > 16000) held |= 1;
  if (y < -16000) held |= 2;
  return held;
}
void PollInput(Native& ui, double elapsed) {
  const auto pad = Pad(ui);
  if (!pad) return;
  const auto held = *pad;
  menu::ButtonMask mapped = held & 15;
  if (held & 0x100) mapped |= menu::Mask(menu::Button::PreviousPage);
  if (held & 0x200) mapped |= menu::Mask(menu::Button::NextPage);
  if (held & 0x1000) mapped |= menu::Mask(menu::Button::Confirm);
  if (held & (0x2000 | 0x10)) mapped |= menu::Mask(menu::Button::Back);
  const auto result = state.controller.Update(mapped, elapsed);
  const bool quit = (held & 0x8000) && !(state.previous_pad & 0x8000);
  const bool reset = (held & 0x40) && !(state.previous_pad & 0x40);
  state.previous_pad = held;
  if(quit){OpenQuit(ui);return;}
  if (result.back) {
    if(state.social_help)SocialHelp(ui,false);
    else if(state.social_session)SocialSession(ui,false);
    else Resume(ui);
    return;
  }
  if((state.social_help||state.social_session)&&state.controller.page_id()!="social"){
    state.social_help=state.social_session=state.help_from_session=false;
    state.controller.SetPages(menu::MakePausePages(Capabilities(ui)));
  }
  if (reset && Read(ui.base, state.screen + 0x1dc) && ActionAvailable(ui.base, 2) &&
      fh1_car_recovery::Request(ui.ctx, ui.base)) { Resume(ui); return; }
  if (result.action) Activate(ui, *result.action);
}
} // namespace

bool Owns(uint32_t screen) { return screen && state.screen == screen && state.ready; }
bool Input(PPCContext& ctx, uint8_t* base) {
  if (state.screen != ctx.r3.u32) return false;
  if (state.ready) return true;
  // Opening Start and its release can be delivered while donors load. Cancel
  // only a fresh activation/resume, not these inherited or navigation events.
  Native ui(ctx, base);
  if (ui.scratch) {
    const auto held = Pad(ui);
    if (!held || (*held & ~state.previous_pad & (0x1000 | 0x2000 | 0x10)))
      Release(ui, false);
  }
  return false;
}
bool Page(PPCContext& ctx, uint8_t* base, uint32_t screen, bool show) {
  if (screen != state.screen) return false;
  state.visible = show;
  if (show) { state.exit_dialog.reset(); state.controller.Resume(); state.previous_pad = 0xffff; }
  else { state.controller.Suspend(); fh1_map_preview::Update(ctx, base, screen, false); }
  Native ui(ctx, base);
  if (ui.scratch && state.ready) Visibility(ui, show);
  return state.ready;
}
menu::PauseDialogOutcome BeforeExitDialog(PPCContext& ctx,uint8_t* base) {
  using Outcome=menu::PauseDialogOutcome;
  if(!Owns(ctx.r3.u32))return Outcome::Ignored;
  const auto outcome=native_pause_dialog::Before(ctx,base,state.screen,state.exit_dialog);
  if(outcome!=Outcome::Leave)return outcome;
  // The original result handler ends the online activity and invokes done.
  // Release the composition before that transition can load gameplay UI.
  return_bookmark.reset();
  offline_cars::ClearReload(base);
  Native ui(ctx,base);if(ui.scratch)Release(ui,true);
  return outcome;
}
void AfterExitDialog(PPCContext& ctx,uint8_t* base,uint32_t screen,
                     menu::PauseDialogOutcome outcome,bool close_child) {
  if (!Owns(screen) || outcome==menu::PauseDialogOutcome::Ignored) return;
  const auto completion=native_pause_dialog::After(ctx,base,screen,outcome,close_child);
  if (completion.next) state.exit_dialog=completion.next;
  if (completion.effect==native_pause_dialog::Effect::Release) {
    return_bookmark.reset();offline_cars::ClearReload(base);
    Native ui(ctx,base);if(ui.scratch)Release(ui,true);
  } else if (completion.effect==native_pause_dialog::Effect::Restore) {
    Page(ctx,base,screen,true);
  }
}
void Destroy(PPCContext& ctx, uint8_t* base, uint32_t screen) {
  if (screen != state.screen) return;
  offline_cars::ClearReload(base);
  Native ui(ctx, base);
  if (ui.scratch) Release(ui, false);
}
void AfterBind(PPCContext& ctx, uint8_t* base, uint32_t screen) {
  native_quit::Cancel();
  if (rex::cvar::GetFlagByName("forza_native_pause") != "true") return;
  const auto* kernel = REX_KERNEL_STATE();
  const auto* runtime = kernel ? kernel->emulator() : nullptr;
  if (!runtime || runtime->update_data_root().empty() ||
      !native_pause_assets::DonorsAvailable(runtime->update_data_root() / "media.zip")) {
    REXLOG_WARN("Native pause: staged donor triplets unavailable; keeping stock pause");
    return;
  }
  Native ui(ctx, base);
  if (!ui.scratch || !screen) return;
  offline_cars::reload_intent.Bind(Read(base, screen + 0x10C));
  if (state.screen) Release(ui, true);
  state.screen = screen;
  state.engine = Read(base, screen + 8);
  const auto access = ui.Virtual(state.engine, 0x1c, {state.engine});
  state.root = ui.Virtual(access, 0x48, {access});
  if (!state.engine || !state.root) { state = {}; return; }
  state.controller.SetPages(menu::MakePausePages(Capabilities(ui)));
  if (!layout::Supports(state.controller.pages())) { state = {}; return; }
  state.started = state.previous = Clock::now();
  state.previous_pad = Pad(ui).value_or(0xffff);
  for (size_t i = 0; i < state.cards.size(); ++i)
    state.cards[i].donor.Start(ui, "game:\\media\\ui\\scenes\\ui4\\NATIVE_MENU_TILE_" + std::to_string(i) + ".bgf");
  for (size_t i = 0; i < state.tabs.size(); ++i)
    state.tabs[i].donor.Start(ui, "game:\\media\\ui\\scenes\\ui4\\NATIVE_MENU_TAB_" + std::to_string(i) + ".bgf");
  state.header.Start(ui, "game:\\media\\ui\\scenes\\ui4\\NATIVE_MENU_HEADER.bgf");
}
void PartyInput(PPCContext& ctx,uint8_t* base,uint32_t screen,uint32_t event) {
  if(!online_intent||!event||Read(base,screen)!=0x820634b4)return;
  Native ui(ctx,base);
  if(ui.Call(0x82cbc110,{event,0xa,0x60,0})&0xffu)online_intent.reset();
}
void PartyTick(PPCContext& ctx,uint8_t* base,uint32_t screen) {
  if(!online_intent)return;
  if(Clock::now()>=online_intent->expires||!LiveConnected()){
    online_intent.reset();return;
  }
  // CPartyLobby's normal tick populates its choice list and host state.
  // The original builder omits Find/Create while the local party isn't ready.
  if(Read(base,screen)!=0x820634b4||!Read(base,screen+0x17c))return;
  const auto party=Read(base,screen+0x170);
  if(!party||!GuestPtr(base,party+0x71)[0]||
      !GuestPtr(base,screen+0x12a70)[0]||GuestPtr(base,screen+0x12a73)[0]||
      LiveSessionIds().empty()){
    online_intent->ready_since={};return;
  }
  // A lobby can report its host role before async XSession creation completes.
  // Let its published session and controls settle before requesting a child.
  const auto now=Clock::now();
  if(online_intent->ready_since==Clock::time_point{})online_intent->ready_since=now;
  if(now-online_intent->ready_since<std::chrono::milliseconds(300))return;
  const auto action=online_intent->action;
  online_intent.reset(); // Back to the party must never repeat this shortcut.
  Native ui(ctx,base);
  ui.Call(0x827d1280,{screen,action==menu::PauseAction::CreateGame?2u:0u});
  REXLOG_INFO("Native Social: entered {} through initialized FH1 party lobby",
      action==menu::PauseAction::CreateGame?"Create Custom Game":"Find Game");
}
void Tick(PPCContext& ctx, uint8_t* base, uint32_t screen) {
  if (!screen || screen != state.screen) return;
  Native ui(ctx, base);
  if (!ui.scratch) return;
  const auto now = Clock::now();
  if (!state.ready) {
    if (const auto held = Pad(ui)) state.previous_pad &= *held;
    bool ready = true;
    for (size_t i = 0; i < state.cards.size(); ++i) {
      const bool loaded = state.cards[i].donor.Poll(ui, screen,
          [i, &ui](uint32_t wrapper, uint32_t) {
            if (i != 0 || !wrapper) return false;
            const auto stats = Named(ui, state.cards[i].donor.root, "CAR_STATS");
            return IsDescendant(ui.base, Read(ui.base, wrapper + 0x10), stats);
          });
      ready = loaded && ready;
    }
    for (auto& tab : state.tabs) ready = tab.donor.Poll(ui, screen) && ready;
    ready = state.header.Poll(ui, screen, [&ui](uint32_t wrapper, uint32_t) {
      auto bar = Named(ui, state.header.root, "PlayerBar");
      if (!bar) bar = Named(ui, state.header.root, "PLAYER_BAR");
      return wrapper && IsDescendant(ui.base, Read(ui.base, wrapper + 0x10), bar);
    }) && ready;
    if (!ready) {
      bool failed = state.header.status == Donor::Status::Failed;
      for (const auto& card : state.cards) failed |= card.donor.status == Donor::Status::Failed;
      for (const auto& tab : state.tabs) failed |= tab.donor.status == Donor::Status::Failed;
      if (failed || std::chrono::duration<double>(now - state.started).count() > 12) {
        REXLOG_WARN("Native pause: donor setup failed or timed out; keeping stock pause");
        Release(ui, true);
      }
      return;
    }
    bool valid = true;
    for (auto& card : state.cards) valid = BindCard(ui, card) && valid;
    for (auto& tab : state.tabs) valid = BindCard(ui, tab, true) && valid;
    valid = BindCar(ui) && valid;
    valid = BindHeader(ui) && valid;
    const auto& progress = state.cards[4];
    valid = progress.caption && progress.progress_fill && progress.progress_track && valid;
    if (!valid) {
      REXLOG_WARN("Native pause: donor capacity/type validation failed; keeping stock pause");
      DumpNodes(ui, state.cards[0].donor, "tile0");
      DumpNodes(ui, state.header, "header");
      Release(ui, true); return;
    }
    valid = state.header.Isolate(ui, {state.header_paper,state.player_bar,state.credits_display, state.username, state.username_paper,
        state.status, state.footer, state.bumpers[0], state.bumpers[1]}) && valid;
    for (auto& tab : state.tabs) valid = tab.donor.Isolate(ui, {tab.paper, tab.title,tab.grain,tab.caption}) && valid;
    for (size_t i = 0; i < state.cards.size(); ++i) {
      auto& card = state.cards[i];
      std::vector<uint32_t> retained{card.paper, card.title};
      if (card.caption) retained.push_back(card.caption);
      if (i == 0) {
        retained.insert(retained.end(), {state.car.name, state.car.make, state.car.image, state.car.logo,state.car.badge});
        for (unsigned row = 0; row < 5; ++row) {
          retained.push_back(state.car.labels[row]); retained.push_back(state.car.values[row]);
        }
      }
      if(card.icon)retained.push_back(card.icon);
      if (i == 4) {
        retained.push_back(card.progress_fill); retained.push_back(card.progress_track);
      }
      valid = card.donor.Isolate(ui, retained) && valid;
    }
    if (!valid) {
      REXLOG_WARN("Native pause: visual isolation failed; keeping stock pause");
      Release(ui, true); return;
    }
    state.ready = true;
    const auto runtime = ui.Virtual(state.engine, 8, {state.engine});
    const auto metadata = ui.Virtual(runtime, 0x1c, {runtime});
    state.stock_render_root = metadata ? Read(base, metadata + 4) : 0;
    if (!state.stock_render_root) {
      REXLOG_WARN("Native pause: stock render root missing; keeping stock pause");
      Release(ui, true); return;
    }
    state.stock_suppression = GuestPtr(base, state.engine + 0x370)[0];
    state.stock_visibility = GuestPtr(base, state.stock_render_root + 0xfc)[0];
    state.stock_captured = true;
    // Keep the initialized FH1 footer (and its real controller glyphs) while
    // isolating the stock page artwork. Capture visibility for stock fallback.
    auto& footer=state.stock_footer;
    footer.presentation=state.engine;
    footer.access=ui.Virtual(state.engine,0x1c,{state.engine});
    footer.root=state.root; footer.status=Donor::Status::Ready;
    const auto button_bar=Named(ui,state.root,"ButtonBar");
    std::vector<uint32_t> pending{state.root};
    while (!pending.empty() && state.stock_nodes.size()<8192) {
      const auto node=pending.back();pending.pop_back();
      const auto wrapper=Read(base,node+4);
      if (wrapper && Read(base,wrapper+4)==node)
        state.stock_nodes.push_back({node,GuestPtr(base,wrapper+0xfc)[0]!=0});
      unsigned siblings=0;
      for (auto child=Read(base,node+20);child && siblings++<1024;child=Read(base,child+16))
        pending.push_back(child);
    }
    if (!pending.empty() || !button_bar || !footer.Isolate(ui,{button_bar})) {
      REXLOG_WARN("Native pause: footer isolation unavailable; keeping stock pause");
      Release(ui,true);return;
    }
    if (const auto player = state.header.ControllerByVtable(ui, 0x8207eb54))
      ui.Call(0x82f36ab8, {player, Read(base, 0x833f44e8)});
    ui.Call(0x82f1ec20, {state.header.owner, Hash("SHOW_PLAYER_BAR") & 0x7fffffff});
    state.controller.Enter();
    state.controller.SelectPage(menu::PauseEntryPage(Capabilities(ui)));
    if (return_bookmark && return_bookmark->context == Read(base, screen + 0x10c)) {
      state.social_help=return_bookmark->social_help;
      state.social_session=return_bookmark->social_session;
      state.help_from_session=return_bookmark->help_from_session;
      state.social_return=return_bookmark->social_return;
      state.controller.SetPages(menu::MakePausePages(Capabilities(ui),state.social_help,state.social_session));
      state.controller.SelectPage(return_bookmark->page);
      state.controller.FocusTile(return_bookmark->tile);
    }
    return_bookmark.reset();
    state.refreshed = {};
    Visibility(ui, state.visible);
    REXLOG_INFO("Native pause: validated six tile slots, four tabs and FH1 header");
  }
  const double elapsed = std::chrono::duration<double>(now - state.previous).count();
  state.previous = now;
  StockVisibility(ui, false);
  if (!state.visible) return;
  if (std::chrono::duration<double>(now - state.refreshed).count() >= 1) {
    state.controller.SetPages(menu::MakePausePages(Capabilities(ui),state.social_help,state.social_session));
    CarData(ui);
    RefreshData(ui);
    state.refreshed = now;
  }
  PollInput(ui, elapsed);
  if (state.screen != screen || !state.visible) return;
  const auto page_id = state.controller.page_id();
  const auto focus = state.controller.focused_tile_id();
  const menu::Page* page = nullptr;
  for (const auto& candidate : state.controller.pages())
    if (page_id && candidate.id == *page_id) page = &candidate;
  if (!page) { Release(ui, true); return; }
  for (size_t i = 0; i < state.cards.size(); ++i) {
    auto& card = state.cards[i];
    const bool visible = i < page->tiles.size() && page->tiles[i].visible;
    card.donor.SetVisible(ui, visible);
    if (!visible) continue;
    const auto& tile = page->tiles[i];
    card.donor.Tick(ui, [&] {
      StyleCard(ui, card, tile.bounds, tile.label, focus && tile.id == *focus, tile.enabled);
      ActionArtwork(ui,card,tile);
      const bool social=page->id=="social";
      const bool context_page=page->id.starts_with("activity")||page->id.starts_with("horizon");
      if(social||context_page){
        if(!tile.enabled)ui.Opacity(card.donor.presentation,card.paper,85);
        Label(ui,card.donor,card.title,{tile.bounds.x+20,tile.bounds.y+12,tile.bounds.width-40,40},
            tile.bounds.width<300?21:23,tile.label,tile.enabled?kTextColor:kDisabledTextColor);
      }
      if (card.caption) {
        const bool compact = tile.bounds.height <= 106;
        ui.Show(card.donor.presentation, card.caption, tile.id != "current_car");
        Label(ui, card.donor, card.caption,
              {tile.bounds.x + 24, tile.bounds.y + (compact ? 54 : 62),
               tile.bounds.width - (tile.id=="social_connection"?104:48), social?(compact?36.:tile.bounds.height-80):compact ? 26. : 32.},
              social?(state.social_help||tile.id=="session_players"?14:16):compact ? 16 : 18, Description(tile.id, tile.enabled), tile.enabled ? (focus&&*focus==tile.id?kTextColor:kSecondaryTextColor) : kDisabledTextColor);
      }
      if (i == 4) {
        const bool progress = tile.id == "festival_progress";
        ui.Show(card.donor.presentation, card.progress_track, progress);
        ui.Show(card.donor.presentation, card.progress_fill, progress);
        if (progress) Progress(ui, card, tile.bounds);

      }
      const bool car = tile.id == "current_car";
      if (tile.id == "world_map" && fh1_map_preview::Ready()) {
        ui.Color(card.donor.presentation, card.material, kPanelColor);
        ui.Show(card.donor.presentation, card.caption, false);
        ui.Color(card.donor.presentation, card.title,
                 focus && tile.id == *focus ? 0xff007a : kTextColor, true);
      }
      if (i == 0) {
        for (auto node : {state.car.name, state.car.make, state.car.image, state.car.stats, state.car.logo,state.car.badge})
          ui.Show(card.donor.presentation, node, car);
        if (car) CarLayout(ui, tile.bounds);
      }
    });
  }
  std::vector<fh1_menu_components::Tab> tabs;
  for (const auto& p : state.controller.pages())
    if (p.visible) tabs.push_back({p.id, p.label, !p.enabled});
  if (!state.tab_bar.SetTabs(std::move(tabs)) || !state.tab_bar.Select(page->id)) {
    Release(ui, true); return;
  }
  state.tab_bar.Advance(elapsed);
  const fh1_menu_components::Rect tab_bounds{64, 112, 768, 42};
  const auto cells = state.tab_bar.Layout(tab_bounds, layout::kTabCapacity);
  for (size_t i = 0; i < state.tabs.size(); ++i)
    state.tabs[i].donor.SetVisible(ui, i < cells.size());
  state.tab_bar.Paint(tab_bounds, layout::kTabCapacity,
      [&](size_t slot, fh1_menu_components::Rect r, uint32_t color) {
        r=fh1_menu_components::MenuQuad(r);
        auto& tab = state.tabs[slot];
        native_pause_edges::TabMeshes(slot,Read(ui.base,tab.paper+4),Read(ui.base,tab.grain+4));
        tab.donor.Layer(ui,Read(ui.base,state.screen+0x110)+(color==0xff007a?3:2));
        tab.donor.Tick(ui, [&] {
          StyleCard(ui, tab, {r.x,r.y,r.w,r.h}, "", false, true, true);
          ui.Color(tab.donor.presentation, tab.material, color);
          Normalize(ui,tab.donor,tab.grain);Point(ui,tab.donor,tab.grain,r.x+r.w/2,r.y+r.h/2);
          Depth(ui,tab.donor,tab.grain,2);ui.Scale(tab.donor.presentation,tab.grain,r.w/100,r.h/100);
          ui.Color(tab.donor.presentation,tab.grain_material,0xffffff);ui.Opacity(tab.donor.presentation,tab.grain,18);
        });
      },
      [&](size_t slot, unsigned index, fh1_menu_components::Rect r, float size,
          const std::string& text, uint32_t color) {
        if (index) return; // Pause capabilities disable a page rather than lock it.
        r=fh1_menu_components::MenuQuad(r);
        auto& tab = state.tabs[slot];
        Label(ui, tab.donor, tab.title, {r.x,r.y,r.w,r.h}, size, text, color, 1);
      }, 20.f);
  state.header.Tick(ui, [&] { HeaderLayout(ui); });
  for(size_t slot=0;slot<cells.size();++slot){
    auto& tab=state.tabs[slot];
    const bool before=slot==0&&state.tab_bar.MoreBefore();
    const bool after=slot+1==cells.size()&&state.tab_bar.MoreAfter();
    ui.Show(tab.donor.presentation,tab.caption,before||after);
    if(before||after){
      const auto r=fh1_menu_components::MenuQuad({after?838.f:38.f,114,22,38});
      Label(ui,tab.donor,tab.caption,{r.x,r.y,r.w,r.h},20,after?">":"<",0xffffff,1);
    }
  }
  for(auto& card:state.cards)if(card.donor.visible)
    ui.MenuComposition(card.donor.presentation,card.donor.root,
        &card==&state.cards[0]?Read(ui.base,state.car.image+12):0);
  // Tab papers/grain have explicit slanted geometry above. Their labels use
  // projected anchors only, preserving the native font rather than shearing it.
  ui.MenuComposition(state.header.presentation,state.header.root);
  bool map_visible = false, map_selected = false;
  if (page->id == "home") {
    const auto map = std::find_if(page->tiles.begin(), page->tiles.end(),
        [](const auto& tile) { return tile.id == "world_map"; });
    if (map != page->tiles.end() && map->visible && map->enabled &&
        fh1_map_preview::SetBounds(layout::Map(map->bounds), false)) {
      map_visible = true;
      map_selected = focus && *focus == map->id;
    }
  }
  fh1_map_preview::Update(ctx, base, screen, map_visible, map_selected);
}
} // namespace forza::native_pause_scene

extern "C" REX_FUNC(sub_827DFB18) {
  const auto screen = ctx.r3.u32;
  __imp__sub_827DFB18(ctx, base);
  forza::native_pause_scene::AfterBind(ctx, base, screen);
}
extern "C" REX_FUNC(sub_827DAB28) {
  const auto screen = ctx.r3.u32;
  __imp__sub_827DAB28(ctx, base);
  forza::native_pause_scene::Page(ctx, base, screen, true);
}
extern "C" REX_FUNC(sub_827DE0D0) {
  const auto screen = ctx.r3.u32;
  __imp__sub_827DE0D0(ctx, base);
  forza::native_pause_scene::Tick(ctx, base, screen);
}
extern "C" REX_FUNC(sub_827618F8) {
  const auto screen=ctx.r3.u32;
  if (!forza::native_pause_scene::Owns(screen)) {
    __imp__sub_827618F8(ctx,base);
    return;
  }
  const auto close_flag=ctx.r7.u32;
  auto outcome=forza::menu::PauseDialogOutcome::Ignored;
  forza::native_quit::WithChoice(ctx,base,[&]{
    outcome=forza::native_pause_scene::BeforeExitDialog(ctx,base);
    __imp__sub_827618F8(ctx,base);
  });
  // Modal cancellation does not rebind the parent or run its setup callback.
  forza::native_pause_scene::AfterExitDialog(ctx,base,screen,outcome,
      !close_flag || forza::GuestPtr(base,close_flag)[0]!=0);
}
extern "C" REX_FUNC(sub_827540F8) {
  const auto screen=ctx.r3.u32;
  __imp__sub_827540F8(ctx,base);
  forza::fh1_pause_actions::ObserveClubGuard(base,screen,(ctx.r3.u32&0xff)!=0);
}
#else
namespace forza::native_pause_scene {
bool Owns(uint32_t) { return false; }
bool Input(PPCContext&, uint8_t*) { return false; }
bool Page(PPCContext&, uint8_t*, uint32_t, bool) { return false; }
void Destroy(PPCContext&, uint8_t*, uint32_t) {}
void AfterBind(PPCContext&, uint8_t*, uint32_t) {}
void Tick(PPCContext&, uint8_t*, uint32_t) {}
void PartyTick(PPCContext&, uint8_t*, uint32_t) {}
void PartyInput(PPCContext&, uint8_t*, uint32_t, uint32_t) {}
}
#endif
