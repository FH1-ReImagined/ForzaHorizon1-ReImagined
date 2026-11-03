// Original FH1 tuning workspace; stock screen retains dispatcher ownership only.
// tu_address_map: TU4 addresses
#include "../platform/native_car_tuning_menu.h"
#include "../platform/native_car_tuning.h"
#include "../platform/native_pause_scene.h"
#include "../platform/guest_addr.h"

#if FORZA_TU4
#include "../platform/guest_memory.h"
#include "../platform/offline_cars.h"
#include "../platform/native_tuning_components.h"
#include "../platform/native_tuning_assets.h"
#include "../platform/car_tuning_library.h"
#include "../kernel/live.h"
#include <rex/logging.h>
#include <rex/runtime.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <chrono>
#include <cstring>
#include <initializer_list>
#include <string>
#include <vector>

extern "C" REX_FUNC(__imp__XamInputGetState);

namespace forza::car_tuning_menu {
namespace {
using namespace car_tuning;
uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Write(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = GuestPtr(base, address);
  for (unsigned i = 0; i < 4; ++i) p[i] = uint8_t(value >> (24 - 8 * i));
}
class UI {
 public:
  UI(const PPCContext& context, uint8_t* memory)
      : ctx(context), base(memory), scratch(REX_KERNEL_MEMORY()->SystemHeapAlloc(4096)) {}
  ~UI() { if (scratch) REX_KERNEL_MEMORY()->SystemHeapFree(scratch); }
  uint32_t Call(uint32_t function, std::initializer_list<uint32_t> args) {
    unsigned index = 0;
    for (auto value : args) switch (index++) {
      case 0: ctx.r3.u64 = value; break;
      case 1: ctx.r4.u64 = value; break;
      case 2: ctx.r5.u64 = value; break;
      case 3: ctx.r6.u64 = value; break;
      case 4: ctx.r7.u64 = value; break;
      case 5: ctx.r8.u64 = value; break;
      case 6: ctx.r9.u64 = value; break;
    }
    rex::runtime::ResolveIndirectFunction(function)(ctx, base);
    return ctx.r3.u32;
  }
  uint32_t Text(const std::string& text) {
    return Text(std::u16string(text.begin(), text.end()));
  }
  uint32_t Text(const std::u16string& text) {
    if (!scratch || text.size() > 1400) return 0;
    auto* bytes = GuestPtr(base, scratch + 32);
    for (size_t i = 0; i < text.size(); ++i) {
      bytes[i * 2] = uint8_t(text[i] >> 8); bytes[i * 2 + 1] = uint8_t(text[i]);
    }
    bytes[text.size() * 2] = bytes[text.size() * 2 + 1] = 0;
    const auto object = Call(0x824265C8, {16});
    if (!object) return 0;
    std::memset(GuestPtr(base, object), 0, 16);
    Write(base, object, 0x82001674);
    Call(0x82D6DE20, {object, scratch + 32, uint32_t(text.size())});
    Call(0x82555B90, {scratch, object});
    return scratch;
  }
  uint32_t Hash(const char* name) {
    const auto size = std::strlen(name);
    if (!scratch || size >= 256) return 0;
    std::memcpy(GuestPtr(base, scratch + 3072), name, size + 1);
    return Call(0x82F354E0, {scratch + 3072});
  }
  uint32_t Event(uint32_t screen, uint32_t event) {
    Write(base, scratch, 0x5D);
    Write(base, scratch + 4, 0x60);
    const auto table = Read(base, screen);
    const auto found = Call(Read(base, table + 0xA8), {screen, event, scratch, 2});
    return found ? Read(base, found) : 0;
  }
  void Destroy(uint32_t object) {
    if (object) Call(Read(base, Read(base, object)), {object, 1});
  }
  PPCContext ctx;
  uint8_t* base;
  uint32_t scratch;
};

Access AccessFor(uint8_t* base, uint32_t parent) {
  const bool paused = base && parent && Read(base, parent) == 0x82061F94;
  const bool free_roam = offline_cars::Available(base, parent);
  // BeginNative independently matches current owned garage ID, profile car ID,
  // live-car model ID, and actual live-car definition before granting access.
  return {paused, free_roam, true};
}

struct Form {
  uint32_t parent = 0, screen = 0, parameters = 0, source = 0;
  uint32_t parent_component = 0, empty_parent_source = 0;
  std::array<uint32_t, kFieldCount> rows{};
  std::array<uint32_t, kFieldCount> selections{};
  std::array<std::vector<float>, kFieldCount> choices;
  EditSession edit;
  NativePreview preview;
  NativePreview saved_preview;
  std::array<PerformanceMetric, 7> saved_metrics{};
  std::vector<unsigned> visible;
  unsigned category = 0, selected = 0;
  fh1_menu_components::TabBar tabs;
  bool saved_metrics_ready = false, preview_is_saved = false;
  bool opening = false, armed = false, applying = false, bound = false;
  bool apply_running = false;
  uint64_t generation = 0;
  uint16_t held = 0;
  int direction = 0;
  std::chrono::steady_clock::time_point repeat_at{};
  std::chrono::steady_clock::time_point animation_at{};
  std::string message;
  enum class Page { Tune, Library, Name, Delete } page=Page::Tune;
  unsigned action=0,library_selected=0,key=0;
  std::string name;
  std::vector<setups::Entry> entries;
  fh1_menu_components::TabBar library_tabs;
};
Form form;
native_tuning_components::Workspace workspace;
uint64_t next_generation = 0;

void ParentRows(UI& ui, bool show) {
  if (native_pause_scene::Page(ui.ctx, ui.base, form.parent, show)) return;
  const bool valid = form.parent && Read(ui.base, form.parent) == 0x82061F94;
  const auto component = valid ? Read(ui.base, form.parent + 0x170) : 0;
  if (!show) {
    if (!component) return; // An authored parent scene may not use stock list rows.
    form.empty_parent_source = ui.Call(0x824265C8, {0x38});
    if (!form.empty_parent_source) return;
    ui.Call(0x828EED20, {form.empty_parent_source});
    form.parent_component = component;
  }
  if (component && component == form.parent_component) {
    ui.Call(0x828B82C8, {component, show ? form.parent + 0x174 : form.empty_parent_source});
    ui.Call(Read(ui.base, Read(ui.base, component + 0x6C) + 0x28), {component + 0x6C});
    if (show) ui.Call(0x827D6348, {form.parent});
  }
  if (show) {
    ui.Destroy(form.empty_parent_source);
    form.empty_parent_source = form.parent_component = 0;
  }
}

struct Display { uint32_t family, decimals; };
Display DisplayFor(Field field) {
  switch (kFields[size_t(field)].category) {
    case Category::Tires: return {18, 1};
    case Category::Gearing: case Category::AntiRoll: return {0, 2};
    case Category::Alignment: return {20, 1};
    case Category::Springs:
      return {field == Field::FrontHeight || field == Field::RearHeight ? 8u : 25u, 1};
    case Category::Damping: return {0, 1};
    case Category::Aero: return {28, 0};
    case Category::Brakes: case Category::Differential: return {1, 0};
  }
  return {0, 2};
}
std::u16string FormatUnit(UI& ui, Display display, float value, bool units = true) {
  Write(ui.base, ui.scratch, 0);
  ui.ctx.f1.f64 = value;
  ui.Call(0x82D6FBD0, {ui.scratch, 0, display.family, 1, display.decimals, units ? 1u : 0u, 0});
  const auto object = Read(ui.base, ui.scratch);
  std::u16string text;
  if (object) {
    const auto chars = ui.Call(Read(ui.base, Read(ui.base, object) + 0x1C), {object});
    if (chars) for (unsigned i = 0; i < 128; ++i) {
      const auto* p = GuestPtr(ui.base, chars + i * 2);
      const auto c = char16_t(uint16_t(p[0]) << 8 | p[1]);
      if (!c) break;
      text.push_back(c);
    }
  }
  ui.Call(0x824CEAB0, {ui.scratch});
  return text;
}
std::u16string FormatValue(UI& ui, Field field, float value) {
  return FormatUnit(ui, DisplayFor(field), value);
}
struct UnitGrid {double zero=0,quantum=0,step=0;};
UnitGrid NativeGrid(UI& ui,Field field,const FieldRange& range) {
  const auto display=DisplayFor(field);const auto service=Read(ui.base,0x8357CFBC);
  if(!service||!Read(ui.base,0x833CAADC))return {};
  const auto record=ui.Call(0x82D6DD00,{service,display.family});if(!record)return {};
  const auto unit=Read(ui.base,record+4);ui.ctx.f1.f64=0;ui.Call(0x825D4AC8,{unit});const double zero=ui.ctx.f1.f64;
  ui.ctx.f1.f64=std::pow(10.0,-double(display.decimals));ui.Call(0x825D4AC8,{unit});
  const double quantum=std::fabs(ui.ctx.f1.f64-zero);
  if(!std::isfinite(zero)||!std::isfinite(quantum)||quantum<=0)return {};
  const double span=double(range.Maximum())-range.Minimum();
  return {zero,quantum,quantum*std::fmax(1.0,std::ceil(span/(quantum*1000.0)))};
}
double NativeStep(UI& ui,Field field,const FieldRange& range){return NativeGrid(ui,field,range).step;}
constexpr std::array<const char*, 9> kCategories{
  "TIRES", "GEARING", "ALIGNMENT", "ANTI-ROLL BARS", "SPRINGS", "DAMPING", "AERO", "BRAKES", "DIFFERENTIAL"};
constexpr std::array<const char*, 9> kCategoryHelp{
  "Cold pressure changes response and grip.",
  "Keep forward gear ratios in descending order.",
  "Alignment changes tire contact and steering.",
  "Roll stiffness changes cornering balance.",
  "Balance support, compliance and clearance.",
  "Rebound: extension. Bump: compression.",
  "Downforce adds high-speed grip and drag.",
  "Balance: front/rear. Pressure: force.",
  "Sport rear differentials link accel/decel."};
void RestartPreview(UI& ui) {
  // A quick edit must not discard the still-running saved-setup baseline.
  if (form.preview_is_saved && !form.saved_metrics_ready && form.preview.model && !form.saved_preview.model) {
    form.saved_preview = form.preview;
    form.preview = {};
  }
  form.preview_is_saved = !form.edit.dirty();
  if (!BeginPreviewNative(ui.ctx, ui.base, AccessFor(ui.base, form.parent), form.edit, form.preview))
    form.preview.failed = true;
}
void Status(UI& ui, const std::string& message = {}) {
  (void)ui; form.message=message;
}
setups::Library SetupLibrary(bool inbox=false,bool exports=false) {
  const auto* runtime=rex::Runtime::instance();
  return setups::Library(runtime->user_data_root()/"tunes"/(inbox?"inbox":exports?"exports":"library"));
}
void Footer(UI& ui) {
  const bool tune=form.page==Form::Page::Tune,name=form.page==Form::Page::Name;
  fh1_menu_widgets::Native native(ui.ctx,ui.base);
  if (native) fh1_menu_widgets::FooterButtons(native,form.screen,
      {{0,tune?"APPLY SETUP":setups::LibraryActionEnabled(form.action)?"SELECT":""},
       {1,tune?"DISCARD":"BACK"},{2,tune?"SETUPS":""},
       {3,name?"BACKSPACE":tune?"RESET ALL":""},
       {8,name?"":tune?"PREV CATEGORY":"PREV ACTION"},
       {9,name?"":tune?"NEXT CATEGORY":"NEXT ACTION"}});
}
void RefreshLibrary() {
  if(!setups::LibraryActionEnabled(form.action)){form.entries.clear();return;}
  form.entries=SetupLibrary(form.action==5).List();
  form.library_selected=std::min<unsigned>(form.library_selected,form.entries.empty()?0:unsigned(form.entries.size()-1));
}
void LibraryPage(UI& ui) {
  form.page=Form::Page::Library;form.library_tabs.SetTabs({{"save","SAVE"},{"load","LOAD"},{"rename","RENAME"},{"delete","DELETE"},{"export","EXPORT"},{"import","IMPORT"},{"community","COMMUNITY",true,"Coming soon"}});
  form.library_tabs.Select(form.library_tabs.tabs()[form.action].id);RefreshLibrary();Footer(ui);
}
const std::vector<std::string>& NameKeys() {
  static const auto keys=[] {std::vector<std::string> v;for(char c:std::string("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -_'"))v.emplace_back(1,c);v.push_back("DEL");v.push_back("DONE");v.push_back("CANCEL");return v;}();return keys;
}
void LibrarySelect(UI& ui) {
  if(!setups::LibraryActionEnabled(form.action)){Status(ui,"Community browser - Coming soon. Export and Import are available now.");return;}
  if(form.page==Form::Page::Name){
    const auto& key=NameKeys()[form.key];
    if(key=="CANCEL"){LibraryPage(ui);return;}
    if(key=="DEL"){if(!form.name.empty())form.name.pop_back();return;}
    if(key!="DONE"){if(form.name.size()<setups::kMaxName)form.name+=key;return;}
    if(!setups::ValidName(form.name)){Status(ui,"Use 1-32 letters/numbers; names must start and end with a letter or number.");return;}
    bool success=false;
    if(form.action==0)success=SetupLibrary().Save(setups::Capture(form.name,form.edit)).has_value();
    else if(form.library_selected<form.entries.size())success=SetupLibrary().Rename(form.entries[form.library_selected].id,form.name);
    LibraryPage(ui);Status(ui,success?"Named setup saved locally. The car changes only with Apply.":"Could not save this setup. Check storage or the 256-setup limit.");return;
  }
  if(form.page==Form::Page::Delete){
    bool success=form.library_selected<form.entries.size()&&SetupLibrary().Remove(form.entries[form.library_selected].id);
    LibraryPage(ui);Status(ui,success?"Setup deleted from this local library.":"Setup could not be deleted.");return;
  }
  if(form.action==0||form.action==2){
    if(form.action==2&&form.entries.empty()){Status(ui,"Select a saved setup first.");return;}
    form.name=form.action==0?"Setup "+std::to_string(form.entries.size()+1):form.entries[form.library_selected].setup.name;
    form.key=0;form.page=Form::Page::Name;Footer(ui);Status(ui,"Choose characters, then DONE. Y removes the last character.");return;
  }
  if(form.entries.empty()){Status(ui,"No setup is selected.");return;}
  auto entry=form.entries[form.library_selected];
  auto current=SetupLibrary(form.action==5).Get(entry.id);
  if(!current){RefreshLibrary();Status(ui,"The selected file changed or is invalid. Select it again.");return;}
  if(form.action==3){form.page=Form::Page::Delete;Footer(ui);Status(ui,"Select A to delete this local setup, or B to keep it.");return;}
  if(form.action==4){Status(ui,SetupLibrary(false,true).Save(*current)?"Parameter-only file exported to tunes/exports. Share that .fh1tune file.":"Export failed or the export folder is full.");return;}
  auto draft=form.edit;
  if(!setups::LoadDraft(*current,draft)){Status(ui,"Incompatible tune: car model, all installed parts, schema and legal fields must match.");return;}
  if(form.action==5&&!SetupLibrary().Save(*current)){Status(ui,"Import could not be saved locally. Your draft was not changed.");return;}
  form.edit=std::move(draft);RestartPreview(ui);form.page=Form::Page::Tune;Footer(ui);
  Status(ui,"Loaded "+current->name+" as an unsaved draft. Review it, then Apply or Discard.");
}

bool BuildRow(UI& ui, unsigned index) {
  const auto field = Field(index);
  const auto& range = *form.edit.schema()[index];
  auto& values = form.choices[index];
  values = {kStock,range.Minimum()};
  // Keep an existing value exactly even between choices. Units and conversion
  // come from FH1's selected native unit profile, including mixed profiles.
  const auto current = *form.edit.Value(field);
  const auto grid=NativeGrid(ui,field,range);
  if(!grid.step)return false;
  // Align to visible native unit precision. A raw minimum such as188.55lb/in
  // otherwise puts every choice on a rounding midpoint and can change its
  // displayed digit after the title's float save/reload round trip.
  const double first=fh1_menu_components::SliderGridStart(range.Minimum(),grid.zero,grid.quantum);
  for (unsigned i = 0; i <= 1000; ++i) {
    const double value = first + i * grid.step;
    if (value >= range.Maximum()) break;
    if(value>=range.Minimum())values.push_back(float(value));
  }
  values.push_back(range.Maximum());
  if (!form.edit.IsStock(field)) values.push_back(current);
  std::sort(values.begin() + 1, values.end());
  values.erase(std::unique(values.begin() + 1, values.end()), values.end());
  const auto choices = ui.Call(0x824265C8, {0x28});
  if (!choices) return false;
  ui.Call(0x828DD2A8, {choices, uint32_t(values.size())});
  for (size_t i = 0; i < values.size(); ++i) {
    auto formatted = FormatValue(ui, field, i == 0 ? range.stock : values[i]);
    if (formatted.empty()) { ui.Destroy(choices); return false; }
    if (i == 0) formatted = u"STOCK (" + formatted + u")";
    const auto text = ui.Text(formatted);
    if (!text) { ui.Destroy(choices); return false; }
    ui.ctx.f1.f64 = 0;
    ui.Call(0x828DDCA8, {choices, uint32_t(i), text, 0, 0, 0, 1});
    ui.Call(0x824CEAB0, {text});
  }
  const auto row = ui.Call(0x824265C8, {0x1C});
  if (!row) { ui.Destroy(choices); return false; }
  std::memset(GuestPtr(ui.base, row), 0, 0x1C);
  Write(ui.base, row, 0x82049434);
  Write(ui.base, row + 4, choices);
  Write(ui.base, row + 0xC, index);
  const auto label = ui.Text(kFields[index].label);
  if (!label) { ui.Destroy(choices); ui.Destroy(row); return false; }
  ui.Call(0x82471820, {row + 0x10, label});
  ui.Call(0x824CEAB0, {label});
  if (!form.edit.IsStock(field))
    Write(ui.base, row + 8, uint32_t(std::find(values.begin() + 1, values.end(), current) - values.begin()));
  form.selections[index] = Read(ui.base, row + 8);
  form.rows[index] = row;
  Write(ui.base, ui.scratch, row);
  ui.Call(0x826D9C80, {form.source + 0x18, ui.scratch});
  return true;
}
bool Sync(UI& ui) {
  bool accepted = true;
  const auto before = form.edit.pending();
  for (unsigned i = 0; i < form.rows.size(); ++i) {
    if (!form.rows[i]) continue;
    const auto selected = Read(ui.base, form.rows[i] + 8);
    if (selected == form.selections[i]) continue;
    bool changed = false;
    const auto field = Field(i);
    if (selected == 0) changed = form.edit.Reset(field);
    else if (selected < form.choices[i].size()) {
      const auto value = form.choices[i][selected];
      // Preserve the current normalized bit pattern when its display choice
      // did not change; round-trip floating arithmetic is not a user edit.
      changed = (!form.edit.IsStock(field) && form.edit.Value(field) == value) ||
                form.edit.SetValue(field, value);
    }
    if (changed) form.selections[i] = selected;
    else { Write(ui.base, form.rows[i] + 8, form.selections[i]); accepted = false; }
  }
  if (form.edit.pending() != before) RestartPreview(ui);
  return accepted;
}
void Reset(UI& ui) {
  form.edit.ResetAll();
  for (unsigned i = 0; i < form.rows.size(); ++i) {
    if (!form.rows[i]) continue;
    form.selections[i] = 0;
    Write(ui.base, form.rows[i] + 8, 0);
  }
  const auto component = Read(ui.base, form.screen + 0x16C);
  if (component) ui.Call(Read(ui.base, Read(ui.base, component + 0x6C) + 0x28), {component + 0x6C});
  RestartPreview(ui);
  Status(ui);
}
void ShowCategory(UI& ui, unsigned category) {
  form.category = category % kCategories.size();
  constexpr std::array<const char*,9> ids{"tires","gearing","alignment","anti_roll","springs","damping","aero","brakes","differential"};
  constexpr std::array<const char*,9> labels{"TIRES","GEARING","ALIGNMENT","ANTI-ROLL","SPRINGS","DAMPING","AERO","BRAKES","DIFF"};
  std::vector<fh1_menu_components::Tab> tabs;
  for(unsigned i=0;i<ids.size();++i){bool available=false;for(unsigned f=0;f<kFieldCount;++f)if(unsigned(kFields[f].category)==i&&form.edit.Editable(Field(f)))available=true;
    tabs.push_back({ids[i],labels[i],!available});}
  form.tabs.SetTabs(std::move(tabs));form.tabs.Select(ids[form.category]);
  form.selected = 0;
  form.visible.clear();
  const auto begin = Read(ui.base, form.source + 0x18);
  for (size_t i = 0; i < form.rows.size(); ++i) {
    if (!form.rows[i] || unsigned(kFields[i].category) != form.category) continue;
    Write(ui.base, begin + uint32_t(form.visible.size() * 4), form.rows[i]);
    form.visible.push_back(unsigned(i));
  }
  Write(ui.base, form.source + 0x1C, begin + uint32_t(form.visible.size() * 4));
  const auto component = Read(ui.base, form.screen + 0x16C);
  if (component) {
    ui.Call(Read(ui.base, Read(ui.base, component + 0x6C)), {component + 0x6C, form.source});
    ui.Call(Read(ui.base, Read(ui.base, component + 0x6C) + 0x28), {component + 0x6C});
  }
  Status(ui);
}
}

bool Available(const PPCContext& context, uint8_t* base, uint32_t parent) {
  if (form.opening || form.screen || !NativeEnabled()) return false;
  EditSession probe;
  return BeginNative(context, base, AccessFor(base, parent), probe) == NativeResult::Ready;
}

bool Open(const PPCContext& context, uint8_t* base, uint32_t parent) {
  if (form.opening || form.screen) return false;
  const auto* kernel=REX_KERNEL_STATE();
  const auto* runtime=kernel?kernel->emulator():nullptr;
  if(!runtime || !native_tuning_assets::DonorsAvailable(runtime->update_data_root()/"media.zip")) {
    REXLOG_WARN("Native car tuning: dedicated workspace donor triplets unavailable");return false;
  }
  EditSession edit;
  if (BeginCatalogNative(context, base, AccessFor(base, parent), edit) != NativeResult::Ready) return false;
  UI ui(context, base);
  if (!ui.scratch) return false;
  const auto parameters = ui.Call(0x824265C8, {0x88});
  if (!parameters) return false;
  ui.Call(0x825A5640, {parameters, 0});
  const auto title = ui.Text("CAR TUNING");
  if (!title) { ui.Destroy(parameters); return false; }
  ui.Call(0x82471820, {parameters + 0xC, title});
  ui.Call(0x824CEAB0, {title});
  form = {};
  form.generation = ++next_generation;
  form.parent = parent;
  form.parameters = parameters;
  form.edit = edit;
  form.opening = true;
  Write(base, ui.scratch, parameters);
  ui.Call(0x82675B00, {parent, 5, ui.scratch, 0});
  // 82F240C8 transfers accepted parameters into parent+A0. On rejection it
  // destroys them without a child callback; never keep a dangling pending form
  // or free them a second time. A synchronous bind already owns the screen.
  if (form.opening && Read(base, parent + 0xA0) != parameters) {
    form = {};
    REXLOG_INFO("Native car tuning: options request was declined");
    return false;
  }
  REXLOG_INFO("Native car tuning: requested options page for owned car {}", edit.identity().owned_id);
  return true;
}

bool TryBind(PPCContext& context, uint8_t* base) {
  const auto screen = context.r3.u32;
  if (!form.opening || Read(base, screen + 0xA4) != form.parameters) return false;
  form.opening = false;
  form.screen = screen;
  UI ui(context, base);
  if (!ui.scratch) { ui.Call(0x827F20F0, {screen}); return true; }
  form.source = ui.Call(0x824265C8, {0x28});
  if (!form.source) { ui.Call(0x827F20F0, {screen}); return true; }
  ui.Call(0x8267A4B0, {form.source});
  Write(base, screen + 0x1A0, form.source);
  Write(base, form.parameters + 0x1C, form.source);
  bool rows_ok = true;
  for (size_t i = 0; i < kFieldCount; ++i)
    if (form.edit.Editable(Field(i)) && !BuildRow(ui, unsigned(i))) { rows_ok = false; break; }
  ui.Call(0x827FBDD0, {screen});
  ui.Call(0x82F1EC20, {screen, ui.Hash("DIFFICULTY")});
  const auto footer = ui.Call(0x82746638, {screen});
  if (footer) {
    ui.Call(0x8289A908, {footer});
    for (const auto& item : {std::pair{0u, "APPLY SETUP"}, std::pair{1u, "DISCARD"}, std::pair{3u, "RESET ALL"},
                             std::pair{8u, "PREV CATEGORY"}, std::pair{9u, "NEXT CATEGORY"}}) {
      if (const auto text = ui.Text(item.second)) {
        ui.Call(0x8289E2B0, {footer, item.first, text});
        ui.Call(0x824CEAB0, {text});
      }
    }
    ui.Call(Read(base, Read(base, footer + 0x6C) + 0x28), {footer + 0x6C});
  }
  form.bound = rows_ok;
  if(rows_ok)Footer(ui);
  if (rows_ok) {
    ParentRows(ui, false);
    RestartPreview(ui);
    ShowCategory(ui, 0);
    fh1_menu_widgets::Native native(context,base);
    workspace.Start(native,screen,form.parent);
  }
  if (!rows_ok) ui.Call(0x827F20F0, {screen});
  return true;
}

bool TryNotify(PPCContext& context, uint8_t* base) {
  if (!form.screen || context.r3.u32 != form.screen) return false;
  // Dedicated components own selection/value input. The hidden stock list
  // remains a lifetime-owned datasource, not an interactive controller.
  (void)base;
  return true;
}
bool TryDestroy(PPCContext& context, uint8_t* base) {
  if (!form.screen || context.r3.u32 != form.screen) return false;
  UI ui(context, base);
  ReleasePreviewNative(context, base, form.preview);
  ReleasePreviewNative(context, base, form.saved_preview);
  fh1_menu_widgets::Native native(context,base);
  workspace.Release(native);
  ParentRows(ui, true);
  for (const auto row : form.rows) if (row) {
    ui.Destroy(Read(base, row + 4));
    ui.Destroy(row);
  }
  ui.Destroy(form.source);
  Write(base, form.screen + 0x1A0, 0);
  // The outer FH1 screen destructor retains ownership of its parameters and
  // component bindings. This callback owns only the datasource/rows it replaced.
  form = {};
  return true;
}
void ApplyInDispatch(const PPCContext& context, uint8_t* base, UI& ui) {
  // Native save/wait may pump callbacks. Keep input locked and commit a detached
  // session so destruction or a replacement form cannot invalidate its storage.
  const auto screen = form.screen;
  const auto generation = form.generation;
  auto pending = form.edit;
  form.apply_running = true;
  // Let native saving/error modals own the foreground while Apply pumps UI.
  fh1_menu_widgets::Native presentation(context,base);workspace.SetVisible(presentation,false);
  const auto result = ApplyNative(context, base, AccessFor(base, form.parent), pending);
  if (form.screen != screen || form.generation != generation) return;
  form.apply_running = form.applying = false;
  form.edit = pending;
  if (result == NativeResult::Applied || result == NativeResult::Unchanged) {
    ui.Call(0x827F20F0, {form.screen});
  } else {
    workspace.SetVisible(presentation,true);
    form.edit.Close();
    form.armed = false;
    Status(ui, result == NativeResult::ApplyUnconfirmed
        ? "The game did not confirm the save. Close this page and check the car before retrying."
        : "The car or game state changed. Close and reopen tuning before applying.");
  }
}

bool TryInput(PPCContext& context, uint8_t* base) {
  if (!form.screen || context.r3.u32 != form.screen) return false;
  UI ui(context, base);
  if (!ui.scratch) return true;
  if (workspace.Failed()) {
    form.edit.Cancel();
    ui.Call(0x827F20F0, {form.screen});
    return true;
  }
  if (!form.bound || !form.armed || form.applying) return true;
  const auto event = ui.Event(form.screen, context.r4.u32);
  if(form.page!=Form::Page::Tune){
    if(event==0x60){
      if(form.page==Form::Page::Library){form.page=Form::Page::Tune;Footer(ui);Status(ui);}
      else{LibraryPage(ui);Status(ui);}
    }else if(event==0x5D)LibrarySelect(ui);
    return true;
  }
  if (event == 0x60) {
    form.edit.Cancel();
    ui.Call(0x827F20F0, {form.screen});
  } else if (!form.edit.open()) {
    return true;
  } else if (event == 0x5D) {
    if (!Sync(ui)) {
      Status(ui, "Keep gear ratios in descending order. Adjust the neighboring gear first.");
      return true;
    }
    form.applying = true;
    ReleasePreviewNative(context, base, form.preview);
    ReleasePreviewNative(context, base, form.saved_preview);
    Status(ui, "Applying and saving car tuning...");
    // Screen transitions require the active native UI dispatcher. A frame tick
    // has no transition job context, even on the same guest thread.
    ApplyInDispatch(context, base, ui);
  } else {
    // Directional input is read by the dedicated component controller in Tick.
    // Forwarding it to hidden native spinners depends on their scene visibility.
    return true;
  }
  return true;
}
std::string Utf8(const std::u16string& value) {
  std::string out;
  for(auto c:value) {
    if(c<128)out.push_back(char(c));
    else if(c<2048){out.push_back(char(0xc0|(c>>6)));out.push_back(char(0x80|(c&63)));}
    else{out.push_back(char(0xe0|(c>>12)));out.push_back(char(0x80|((c>>6)&63)));out.push_back(char(0x80|(c&63)));}
  }return out;
}
void DrawWorkspace(UI& ui) {
  native_tuning_components::View view;view.category=form.category;view.dirty=form.edit.dirty();view.tabs=form.tabs;view.model_id=form.edit.identity().model_id;
  EditSession saved;saved.BeginSchema(form.edit.identity(),form.edit.original(),form.edit.schema());
  for(unsigned position=0;position<form.visible.size();++position){
    auto index=form.visible[position];auto field=Field(index);
    if(!form.edit.Editable(field))continue;
    const auto& range=*form.edit.schema()[index];
    auto value=*form.edit.Value(field),previous=*saved.Value(field);
    native_tuning_components::Row row;
    row.label=kFields[index].label;row.saved=(saved.IsStock(field)?"STOCK ":"")+Utf8(FormatValue(ui,field,previous));row.draft=(form.edit.IsStock(field)?"STOCK ":"")+Utf8(FormatValue(ui,field,value));
    row.value=std::clamp((value-range.Minimum())/(range.Maximum()-range.Minimum()),0.f,1.f);
    row.saved_value=std::clamp((previous-range.Minimum())/(range.Maximum()-range.Minimum()),0.f,1.f);
    row.selected=position==form.selected;view.rows.push_back(row);
  }
  auto speed60=FormatUnit(ui,{13,0},26.8224f,false),speed100=FormatUnit(ui,{13,0},44.704f,false),speed120=FormatUnit(ui,{13,0},53.6448f,false);
  const std::array<std::u16string,7> labels{u"0-"+speed60,u"0-"+speed100,u"Top speed",speed60+u"-0",speed100+u"-0",u"G @ "+speed60,u"G @ "+speed120};
  constexpr std::array<Display,7> units{{{3,2},{3,2},{13,1},{9,1},{9,1},{0,2},{0,2}}};
  for(unsigned i=0;i<7;++i){
    auto format=[&](const PerformanceMetric& metric,bool failed){return failed?std::string("N/A"):metric.status==2?Utf8(FormatUnit(ui,units[i],metric.value)):metric.status==1?std::string("N/A"):std::string("...");};
    view.metrics[i]={Utf8(labels[i]),format(form.saved_metrics_ready?form.saved_metrics[i]:form.preview_is_saved?form.preview.metrics[i]:form.saved_preview.metrics[i],!form.saved_metrics_ready && (form.preview_is_saved?form.preview.failed:form.saved_preview.failed)),format(form.preview.metrics[i],form.preview.failed)};
  }
  auto units_label=Utf8(FormatUnit(ui,{13,0},26.8224f));auto space=units_label.find(' ');
  view.calculating=(!form.preview.complete&&!form.preview.failed)||
      (!form.saved_metrics_ready&&form.saved_preview.model&&!form.saved_preview.complete&&!form.saved_preview.failed);
  view.preview_failed=form.preview.failed||!form.edit.open();
  view.status=std::string(form.preview.failed?"UNAVAILABLE":view.calculating?"CALCULATING":"READY")+"  |  Tests in "+(space==std::string::npos?units_label:units_label.substr(space+1));
  view.help=form.visible.empty()?"Install adjustable parts to unlock this category.":std::string(kFields[form.visible[std::min<size_t>(form.selected,form.visible.size()-1)]].label)+": "+kCategoryHelp[form.category];
  if(!form.visible.empty() && form.edit.open()){
    auto field=Field(form.visible[std::min<size_t>(form.selected,form.visible.size()-1)]);const auto& range=*form.edit.schema()[size_t(field)];
    view.range="Range: "+Utf8(FormatValue(ui,field,range.Minimum()))+" - "+Utf8(FormatValue(ui,field,range.Maximum()))+".";
  }
  if(!form.edit.open()){view.status="REOPEN REQUIRED";for(auto& metric:view.metrics)metric.draft="N/A";}
  if(!form.message.empty())view.help=form.message;
  if(form.page!=Form::Page::Tune){
    view.library=true;view.tabs=form.library_tabs;view.rows.clear();
    view.range="Local parameter files only. Loading never applies or saves the car.";
    if(form.page==Form::Page::Name){view.keyboard=true;view.entry=form.name;view.keys=NameKeys();view.key=form.key;}
    else if(form.page==Form::Page::Delete){
      native_tuning_components::Row row;row.label="DELETE "+form.entries[form.library_selected].setup.name+"?";row.selected=true;view.rows.push_back(row);
    }else if(!setups::LibraryActionEnabled(form.action)){
      native_tuning_components::Row row;row.label="Community browser - Coming soon";row.draft="UNAVAILABLE";view.rows.push_back(row);
      view.help="Community browser - Coming soon. Use Export and Import to share tune files.";
      view.range="This page has no online connection or action.";
    }else if(form.action==0){
      native_tuning_components::Row row;row.label="Save current draft with a name";row.selected=true;view.rows.push_back(row);
      if(form.message.empty())view.help="Save a reusable local setup without changing the car.";
    }else{
      unsigned begin=form.library_selected/9*9,end=std::min<unsigned>(begin+9,unsigned(form.entries.size()));
      for(unsigned i=begin;i<end;++i){auto& entry=form.entries[i];auto probe=form.edit;
        native_tuning_components::Row row;row.label=entry.setup.name;row.saved=std::to_string(entry.setup.model);
        row.draft=setups::LoadDraft(entry.setup,probe)?"READY":"INCOMPATIBLE";row.selected=i==form.library_selected;view.rows.push_back(std::move(row));}
      if(form.message.empty())view.help=form.action==5?"Add shared .fh1tune files to tunes/inbox, then select Import.":"Choose a setup. Its car model and installed parts must match.";
      if(!form.entries.empty())view.range="Setup "+std::to_string(form.library_selected+1)+" / "+std::to_string(form.entries.size())+". "+view.range;
    }
  }
  fh1_menu_widgets::Native native(ui.ctx,ui.base);workspace.Tick(native,view);
  // Preserve the shared component's displayed animation position across the
  // value snapshot, so interrupted tuning/library transitions stay continuous.
  if(form.page==Form::Page::Tune)form.tabs=view.tabs;else form.library_tabs=view.tabs;
  if(workspace.Failed())REXLOG_WARN("Native car tuning: dedicated workspace rejected; next input closes without applying");
}

void Direction(UI& ui,int direction) {
  if(form.page!=Form::Page::Tune){
    if(form.page==Form::Page::Name){int delta=direction==1?-9:direction==2?9:direction==3?-1:1;
      form.key=unsigned((int(form.key)+int(NameKeys().size())+delta)%int(NameKeys().size()));
    }else if(form.page==Form::Page::Library&&!form.entries.empty()&&form.action!=0){
      form.library_selected=unsigned((form.library_selected+form.entries.size()+(direction==1||direction==3?-1:1))%form.entries.size());
    }return;
  }
  if(form.visible.empty())return;
  if(direction==1||direction==2){
    form.selected=unsigned((form.selected+form.visible.size()+(direction==1?-1:1))%form.visible.size());
    Status(ui);return;
  }
  const auto index=form.visible[std::min<size_t>(form.selected,form.visible.size()-1)];
  const auto field=Field(index);const auto& range=*form.edit.schema()[index];
  const auto step=NativeStep(ui,field,range);
  const auto current=*form.edit.Value(field);auto choices=form.choices[index];
  auto at=std::lower_bound(choices.begin()+1,choices.end(),current);
  if(at==choices.end()||*at!=current)at=choices.insert(at,current);
  auto selected=fh1_menu_components::SliderNeighbor(choices,size_t(at-choices.begin()),range.stock,float(step*.25),direction==4?1:-1);
  if(!selected||choices[*selected]==current)return;
  if(!form.edit.SetValue(field,choices[*selected])){
    Status(ui,"Keep gear ratios in descending order. Adjust the neighboring gear first.");return;
  }
  auto original=std::find(form.choices[index].begin()+1,form.choices[index].end(),choices[*selected]);
  form.selections[index]=uint32_t(original-form.choices[index].begin());Write(ui.base,form.rows[index]+8,form.selections[index]);
  RestartPreview(ui);Status(ui);
}
void Tick(const PPCContext& context, uint8_t* base) {
  if (!form.screen || !form.bound || form.apply_running) return;
  const auto animation_now=std::chrono::steady_clock::now();
  const auto animation_delta=form.animation_at.time_since_epoch().count()?std::chrono::duration<double>(animation_now-form.animation_at).count():1.;
  form.animation_at=animation_now;
  form.tabs.Advance(animation_delta);form.library_tabs.Advance(animation_delta);
  UI ui(context, base);
  if (!ui.scratch) return;
  if (workspace.Failed()) {
    ReleasePreviewNative(context, base, form.preview);
    ReleasePreviewNative(context, base, form.saved_preview);
    form.edit.Cancel();
    return; // The next native input dispatcher safely closes the failed page.
  }
  auto pad = context;
  pad.r3.u64 = 0; pad.r4.u64 = 0; pad.r5.u64 = ui.scratch;
  __imp__XamInputGetState(pad, base);
  if (pad.r3.u32 == 0) {
    const auto* bytes = GuestPtr(base, ui.scratch + 4);
    const uint16_t held = uint16_t(bytes[0]) << 8 | bytes[1];
    const auto* stick=GuestPtr(base,ui.scratch+8);
    const auto x=int16_t(uint16_t(stick[0])<<8|stick[1]),y=int16_t(uint16_t(stick[2])<<8|stick[3]);
    if (!held && std::abs(int(x))<10000 && std::abs(int(y))<10000) form.armed = true;
    int direction=(held&1)?1:(held&2)?2:(held&4)?3:(held&8)?4:0;
    if(!direction && (std::abs(int(x))>10000||std::abs(int(y))>10000))
      direction=std::abs(int(x))>std::abs(int(y))?(x<0?3:4):(y>0?1:2);
    const auto now=std::chrono::steady_clock::now();
    if(form.armed && !form.applying && form.edit.open() && direction &&
        (direction!=form.direction || now>=form.repeat_at)){
      Direction(ui,direction);
      form.repeat_at=now+std::chrono::milliseconds(direction!=form.direction?350:100);
    }
    form.direction=direction;
    if (form.armed && !form.applying && form.edit.open() &&
        (held & 0x8000) && !(form.held & 0x8000)) {
      if(form.page==Form::Page::Name){if(!form.name.empty())form.name.pop_back();}
      else if(form.page==Form::Page::Tune)Reset(ui);
    }
    if(form.armed&&!form.applying&&form.edit.open()&&form.page==Form::Page::Tune&&(held&0x4000)&&!(form.held&0x4000))LibraryPage(ui);
    if (form.armed && !form.applying && form.edit.open()) {
      if ((held & 0x0100) && !(form.held & 0x0100))
        {if(form.page==Form::Page::Tune){form.tabs.SelectNeighbor(-1);ShowCategory(ui,unsigned(form.tabs.selected_index()));}
         else if(form.page==Form::Page::Library){form.library_tabs.SelectNeighbor(-1);form.action=unsigned(form.library_tabs.selected_index());RefreshLibrary();Footer(ui);Status(ui);}}
      if ((held & 0x0200) && !(form.held & 0x0200))
        {if(form.page==Form::Page::Tune){form.tabs.SelectNeighbor(1);ShowCategory(ui,unsigned(form.tabs.selected_index()));}
         else if(form.page==Form::Page::Library){form.library_tabs.SelectNeighbor(1);form.action=unsigned(form.library_tabs.selected_index());RefreshLibrary();Footer(ui);Status(ui);}}
    }
    form.held = held;
  }
  if (AdvancePreviewNative(context, base, AccessFor(base, form.parent), form.preview)) {
    if (form.preview.complete && !form.preview.failed && !form.edit.dirty()) {
      form.saved_metrics = form.preview.metrics;
      form.saved_metrics_ready = true;
    }
  }
  if (AdvancePreviewNative(context, base, AccessFor(base, form.parent), form.saved_preview)) {
    if (form.saved_preview.complete && !form.saved_preview.failed) {
      form.saved_metrics = form.saved_preview.metrics;
      form.saved_metrics_ready = true;
    }
  }
  DrawWorkspace(ui);
}
} // namespace forza::car_tuning_menu
#else
namespace forza::car_tuning_menu {
bool Available(const PPCContext&, uint8_t*, uint32_t) { return false; }
bool Open(const PPCContext&, uint8_t*, uint32_t) { return false; }
bool TryBind(PPCContext&, uint8_t*) { return false; }
bool TryNotify(PPCContext&, uint8_t*) { return false; }
bool TryDestroy(PPCContext&, uint8_t*) { return false; }
bool TryInput(PPCContext&, uint8_t*) { return false; }
void Tick(const PPCContext&, uint8_t*) {}
}
#endif
