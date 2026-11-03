// Original FH1 native tuning adapter. No FH2 routines, resources or build inputs.
// tu_address_map: TU4 addresses
#include "../platform/native_car_tuning.h"
#include "../platform/guest_addr.h"

#if FORZA_TU4
#include "../platform/guest_memory.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <initializer_list>

REXCVAR_DEFINE_BOOL(forza_car_tuning, false, "Game",
                   "Experimental native owned-car tuning");

namespace forza::car_tuning {
namespace {
uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Write(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = GuestPtr(base, address);
  for (unsigned i = 0; i < 4; ++i) p[i] = uint8_t(value >> (24 - 8 * i));
}
float ReadFloat(uint8_t* base, uint32_t address) {
  return std::bit_cast<float>(Read(base, address));
}
class Native {
 public:
  Native(const PPCContext& context, uint8_t* memory) : ctx(context), base(memory) {}
  uint32_t Call(uint32_t function, std::initializer_list<uint32_t> args) {
    unsigned index = 0;
    for (auto value : args) switch (index++) {
      case 0: ctx.r3.u64 = value; break;
      case 1: ctx.r4.u64 = value; break;
      case 2: ctx.r5.u64 = value; break;
      case 3: ctx.r6.u64 = value; break;
    }
    rex::runtime::ResolveIndirectFunction(function)(ctx, base);
    return ctx.r3.u32;
  }
  uint32_t Virtual(uint32_t object, uint32_t offset) {
    if (!object) return 0;
    const auto table = Read(base, object);
    const auto function = table ? Read(base, table + offset) : 0;
    return function ? Call(function, {object}) : 0;
  }
  float Scalar(uint32_t function, std::initializer_list<uint32_t> args) {
    Call(function, args);
    return float(ctx.f1.f64);
  }
  PPCContext ctx;
  uint8_t* base;
};
struct Snapshot {
  Identity identity;
  Payload payload{};
  PressureLimits limits;
};

struct Model {
  Native& native;
  uint32_t address = 0;
  explicit Model(Native& calls) : native(calls) {
    address = native.Call(0x824265C8, {0x940});
    if (address) native.Call(0x8271D6E8, {address});
  }
  ~Model() {
    if (!address) return;
    native.Call(0x82658570, {address});
    native.Call(0x8242D1A0, {address});
  }
};

// Native interfaces, not reimplementations of the game's tuning calculations.
// A field's endpoints are sampled through its own scalar getter so spring,
// height and aero ranges use the installed car's physics definition.
struct FieldBinding { uint32_t scalar, gate; uint32_t argument = 0; };
constexpr std::array<FieldBinding, kFieldCount> kBindings{{
  {0x82656EF0, 0}, {0x82656F10, 0}, {0x82656F68, 0x826564B8},
  {0x82656F88, 0x826564D8, 1}, {0x82656F88, 0x826564D8, 2},
  {0x82656F88, 0x826564D8, 3}, {0x82656F88, 0x826564D8, 4},
  {0x82656F88, 0x826564D8, 5}, {0x82656F88, 0x826564D8, 6},
  {0x82656F88, 0x826564D8, 7}, {0x82656F88, 0x826564D8, 8},
  {0x82657040, 0x826564F8}, {0x82657068, 0x826564F8},
  {0x82657090, 0x826564F8}, {0x826570B8, 0x826564F8}, {0x826570E0, 0x826564F8},
  {0x82656080, 0x82656518}, {0x826560A0, 0x82656538},
  {0x82656150, 0x826564F8}, {0x82656178, 0x826564F8},
  {0x826561A0, 0x826564F8}, {0x826561C0, 0x826564F8},
  {0x82656240, 0x826564F8}, {0x82656260, 0x826564F8},
  {0x82656280, 0x826564F8}, {0x826562A0, 0x826564F8},
  {0x826562F8, 0x82656558}, {0x82656318, 0x82656578},
  {0x82656378, 0x82656598}, {0x82656390, 0x82656598},
  {0x82656440, 0x826565B8}, {0x82656470, 0x826565D8},
  {0x82656458, 0x826565B8}, {0x82656488, 0x826565D8}, {0x826564A0, 0x826565D8},
}};

bool InitializeModel(Native& native, uint32_t model, const Identity& id) {
  auto* base = native.base;
  native.Call(0x8265CA18, {model});
  if (Read(base, model + 4) != id.definition || Read(base, model + 0x46C) != id.model_id ||
      Read(base, model + 0x934) != 2 || !Read(base, model + 0x938)) return false;
  for (size_t i = 0; i < id.parts.size(); ++i)
    if (Read(base, model + 0xC + uint32_t(i * 4)) != id.parts[i]) return false;
  return true;
}

FieldSchema Catalog(Native& native, uint32_t model) {
  FieldSchema schema{};
  auto* base = native.base;
  const auto gears = Read(base, model + 0x458); // Includes reverse; final drive occupies UI row zero.
  const auto drive = Read(base, model + 0x454); // Native 0=front, 1=rear, 2=all wheels.
  // Native reset resolves defaults into the temporary model while restoring
  // the live definition's original normalized payload before returning.
  native.Call(0x82656010, {model});
  for (size_t i = 0; i < kFieldCount; ++i) {
    const auto field = Field(i);
    const auto& binding = kBindings[i];
    if (binding.gate && !native.Call(binding.gate, {model})) continue;
    if (field >= Field::Gear1 && field <= Field::Gear8 && (gears < 2 || gears > 9 || binding.argument >= gears)) continue;
    if ((field == Field::FrontAcceleration || field == Field::FrontDeceleration) && drive != 0 && drive != 2) continue;
    if ((field == Field::RearAcceleration || field == Field::RearDeceleration) && drive != 1 && drive != 2) continue;
    if (field == Field::CenterBalance && drive != 2) continue;
    const auto offset = model + 0xC0 + kFields[i].offset;
    const auto stock_bits = Read(base, offset);
    const auto scalar = [&] {
      return native.Scalar(binding.scalar, {model, binding.argument});
    };
    const auto stock = scalar();
    Write(base, offset, std::bit_cast<uint32_t>(0.0f));
    const auto zero = scalar();
    Write(base, offset, std::bit_cast<uint32_t>(1.0f));
    const auto one = scalar();
    Write(base, offset, stock_bits);
    FieldRange range{zero, one, stock};
    // FH1's category Apply handler links rear coast to rear power for sport
    // differentials (82846474 / 82846570). Race exposes both independently.
    if (field == Field::RearAcceleration && native.Call(0x82656438, {model}) == 2)
      range.linked_normalized = Field::RearDeceleration;
    if (range.Valid()) schema[i] = range;
  }
  return schema;
}

bool Inspect(Native& native, Snapshot& snapshot) {
  auto* base = native.base;
  if (!base) return false;
  const auto services = Read(base, 0x833CAAE4);
  if (!services) return false;
  const auto player = native.Call(0x82915BE0, {});
  const auto garage = native.Virtual(player, 0x64);
  const auto profile = native.Virtual(player, 0x78);
  if (!garage || !profile) return false;

  const auto scratch = REX_KERNEL_MEMORY()->SystemHeapAlloc(4);
  if (!scratch) return false;
  Write(base, scratch, 0);
  struct Reference {
    Native& native;
    uint32_t address;
    ~Reference() {
      native.Call(0x824CEAB0, {address});
      REX_KERNEL_MEMORY()->SystemHeapFree(address);
    }
  } reference{native, scratch};
  native.Call(0x825A3250, {scratch, garage});
  const auto car = Read(base, scratch);
  if (!car || !Read(base, car + 0x898)) return false;
  snapshot.identity.owned_id = Read(base, car + 0x28C);
  snapshot.identity.model_id = Read(base, car + 0x38);
  if (!snapshot.identity.owned_id || !snapshot.identity.model_id ||
      Read(base, profile + 0x250) != snapshot.identity.owned_id) return false;

  // The live-car reference is owned by the current game context. No raw pointer
  // is retained across callbacks: Apply inspects it again before constructing.
  const auto local_ref = native.Call(0x824AF1E8, {services, 0});
  const auto local = local_ref ? Read(base, local_ref) : 0;
  if (!local) return false;
  const auto live_ref = native.Call(0x824BB8F8, {local});
  const auto live = live_ref ? Read(base, live_ref) : 0;
  if (!live || Read(base, live + 0x2E50) != snapshot.identity.model_id) return false;
  snapshot.identity.live_car = live;
  snapshot.identity.definition = native.Call(0x8250B250, {live});
  if (!snapshot.identity.definition) return false;
  // 82C9E6D8 initializes these 45 IDs; 82C9E700 compares their entire 0xB4
  // bytes. This is the stable parts block, not live physics or reference data.
  for (size_t i = 0; i < snapshot.identity.parts.size(); ++i)
    snapshot.identity.parts[i] = Read(base, live + 0x2E58 + uint32_t(i * 4));
  for (size_t i = 0; i < snapshot.payload.size(); ++i)
    snapshot.payload[i] = Read(base, car + 0xF4 + uint32_t(i * 4));
  // CarTuning settings registration: 82CA386C / 82CA38B0. Stock pressure is
  // the native default passed by 82DF67C0. Read all three from the loaded title.
  snapshot.limits = {ReadFloat(base, 0x8359FD18), ReadFloat(base, 0x8359FD1C),
                     ReadFloat(base, 0x82145560)};
  return snapshot.limits.Valid();
}

bool MatchesLivePressure(uint8_t* base, uint32_t model, const EditSession& edit) {
  const auto& id = edit.identity();
  for (size_t i = 0; i < edit.pending().size(); ++i)
    if (Read(base, id.live_car + 0x2F0C + uint32_t(i * 4)) != edit.pending()[i]) return false;
  const auto physics = Read(base, model + 0x938);
  if (!physics) return false;
  // 82DF6510 writes each wheel's cold pressure at +0x1DC0, stride 0x410.
  // Check physics as well as the garage payload: storage alone is insufficient.
  for (unsigned wheel = 0; wheel < 4; ++wheel) {
    const auto expected = edit.Pressure(wheel < 2 ? Axle::Front : Axle::Rear);
    const auto actual = ReadFloat(base, physics + 0x1DC0 + wheel * 0x410);
    if (!expected || !std::isfinite(actual) || std::fabs(actual - *expected) > 0.001f) return false;
  }
  return true;
}
}

bool NativeEnabled() { return REXCVAR_GET(forza_car_tuning); }

NativeResult BeginNative(const PPCContext& context, uint8_t* base, Access access,
                         EditSession& edit) {
  edit.Close();
  if (!NativeEnabled()) return NativeResult::Disabled;
  if (!access.Allowed()) return NativeResult::Unavailable;
  Native native(context, base);
  Snapshot current;
  if (!Inspect(native, current)) return NativeResult::Unavailable;
  return edit.Begin(current.identity, current.payload, current.limits)
             ? NativeResult::Ready : NativeResult::InvalidData;
}

NativeResult BeginCatalogNative(const PPCContext& context, uint8_t* base, Access access,
                                EditSession& edit) {
  edit.Close();
  if (!NativeEnabled()) return NativeResult::Disabled;
  if (!access.Allowed()) return NativeResult::Unavailable;
  Native native(context, base);
  Snapshot current;
  if (!Inspect(native, current)) return NativeResult::Unavailable;
  Model model(native);
  if (!model.address) return NativeResult::AllocationFailed;
  if (!InitializeModel(native, model.address, current.identity)) return NativeResult::Unavailable;
  const auto schema = Catalog(native, model.address);
  Snapshot after;
  if (!Inspect(native, after) || after.identity != current.identity || after.payload != current.payload)
    return NativeResult::StaleSession;
  return edit.BeginSchema(current.identity, current.payload, schema)
             ? NativeResult::Ready : NativeResult::InvalidData;
}

NativeResult ApplyNative(const PPCContext& context, uint8_t* base, Access access,
                         EditSession& edit) {
  if (!NativeEnabled()) return NativeResult::Disabled;
  if (!access.Allowed()) return NativeResult::Unavailable;
  Native native(context, base);
  Snapshot current;
  if (!Inspect(native, current)) return NativeResult::Unavailable;
  const auto check = edit.CheckCommitSchema(current.identity, current.payload, edit.schema());
  if (check == CommitCheck::Unchanged) return NativeResult::Unchanged;
  if (check != CommitCheck::Ready) return NativeResult::StaleSession;

  Model owned(native);
  const auto model = owned.address;
  if (!model) return NativeResult::AllocationFailed;
  if (!InitializeModel(native, model, current.identity))
    return NativeResult::Unavailable;

  // The pressure-only entry point keeps its original minimal read path; the
  // full catalog additionally rechecks every native upgrade gate and endpoint.
  const bool pressure_only = edit.schema() == PressureSchema(edit.limits());
  const auto schema = pressure_only ? PressureSchema(current.limits) : Catalog(native, model);

  Snapshot rechecked;
  if (!Inspect(native, rechecked) ||
      edit.CheckCommitSchema(rechecked.identity, rechecked.payload,
          pressure_only ? PressureSchema(rechecked.limits) : schema) != CommitCheck::Ready)
    return NativeResult::StaleSession;

  // Initializing the native model resolves stock sentinels. Restore the exact
  // original payload plus only the player's edits before asking FH1 to commit.
  for (size_t i = 0; i < edit.pending().size(); ++i)
    Write(base, model + 0xC0 + uint32_t(i * 4), edit.pending()[i]);
  native.Call(0x82659028, {model});
  Snapshot after;
  if (!Inspect(native, after) || after.identity != edit.identity() ||
      !MatchesLivePressure(base, model, edit) || !edit.AcknowledgeApplied(after.identity, after.payload)) {
    // The native call may have already applied. Do not roll back, retry, or
    // report success without matching the native garage's readback.
    edit.Close();
    REXLOG_WARN("Native car tuning: apply returned without matching garage and live physics readback");
    return NativeResult::ApplyUnconfirmed;
  }
  REXLOG_INFO("Native car tuning: garage, live setup and four cold tire pressures match for owned car {}",
              after.identity.owned_id);
  return NativeResult::Applied;
}

void ReleasePreviewNative(const PPCContext& context, uint8_t* base, NativePreview& preview) {
  const auto model = preview.model;
  preview.model = 0;
  if (!model) return;
  Native native(context, base);
  native.Call(0x82658570, {model});
  native.Call(0x8242D1A0, {model});
  // One for each preview (every slider step in the tuning menu): thousands in a session's log
  // (#609). The first ones stay at info, the rest are debug.
  static uint32_t released = 0;
  if (++released <= 8) {
    REXLOG_INFO("Native car tuning: released standalone preview model {:08X}{}", model,
                released == 8 ? " (later ones at debug level)" : "");
  } else {
    REXLOG_DEBUG("Native car tuning: released standalone preview model {:08X}", model);
  }
}

namespace {
Payload ReadPayload(uint8_t* base, uint32_t address) {
  Payload result{};
  for (size_t i = 0; i < result.size(); ++i) result[i] = Read(base, address + uint32_t(i * 4));
  return result;
}
bool PreviewUnchanged(Native& native, const NativePreview& preview) {
  Snapshot current;
  return Inspect(native, current) && current.identity == preview.identity &&
      current.payload == preview.original &&
      ReadPayload(native.base, preview.identity.live_car + 0x2F0C) == preview.live_original &&
      ReadPayload(native.base, preview.identity.definition + 0x2C08) == preview.definition_original;
}
}

bool BeginPreviewNative(const PPCContext& context, uint8_t* base, Access access,
                        const EditSession& edit, NativePreview& preview) {
  ReleasePreviewNative(context, base, preview);
  preview = {};
  if (!NativeEnabled() || !access.Allowed() || !edit.open()) return false;
  Native native(context, base);
  Snapshot current;
  if (!Inspect(native, current) || current.identity != edit.identity() ||
      current.payload != edit.original()) return false;
  preview.identity = current.identity;
  preview.draft = edit.dirty();
  preview.original = current.payload;
  preview.live_original = ReadPayload(base, current.identity.live_car + 0x2F0C);
  preview.definition_original = ReadPayload(base, current.identity.definition + 0x2C08);
  Model owned(native);
  if (!owned.address) return false;
  const auto model = owned.address;
  // Retained performance-screen initializer: state 3 owns a fresh definition,
  // unlike state 2 used by the live Apply adapter. The runtime probe confirms
  // that evaluation and destruction leave garage, live tune and parts intact.
  native.Call(0x8265CE80, {model});
  const auto definition = Read(base, model + 4);
  const auto simulator = Read(base, model + 0x930);
  if (!definition || definition == current.identity.definition || !simulator ||
      Read(base, model + 0x934) != 3 || Read(base, model + 0x46C) != current.identity.model_id)
    return false;
  for (size_t i = 0; i < current.identity.parts.size(); ++i)
    if (Read(base, model + 0xC + uint32_t(i * 4)) != current.identity.parts[i]) return false;
  for (size_t i = 0; i < edit.pending().size(); ++i)
    Write(base, model + 0xC0 + uint32_t(i * 4), edit.pending()[i]);
  native.Call(0x82DF6968, {definition, model + 0xC0});
  const auto parameters = REX_KERNEL_MEMORY()->SystemHeapAlloc(0x24);
  if (!parameters) return false;
  for (uint32_t i = 0; i < 0x24; i += 4) Write(base, parameters + i, 0);
  // Native evaluator configuration used by 8265CE80. Its definition argument
  // points exclusively at the clone; no live physics refresh or save is called.
  Write(base, parameters, Read(base, 0x835A03E4));
  Write(base, parameters + 4, definition);
  GuestPtr(base, parameters)[0x14] = 1;
  Write(base, parameters + 0x18, std::bit_cast<uint32_t>(1.0f));
  Write(base, parameters + 0x1C, std::bit_cast<uint32_t>(-1.0f));
  Write(base, parameters + 0x20, std::bit_cast<uint32_t>(-1.0f));
  native.Call(Read(base, Read(base, simulator) + 0x1C), {simulator, parameters});
  REX_KERNEL_MEMORY()->SystemHeapFree(parameters);
  if (!PreviewUnchanged(native, preview)) return false;
  preview.model = owned.address;
  owned.address = 0;
  return true;
}

bool AdvancePreviewNative(const PPCContext& context, uint8_t* base, Access access,
                          NativePreview& preview) {
  if (!preview.model || preview.complete || preview.failed) return false;
  Native native(context, base);
  if (!NativeEnabled() || !access.Allowed() || !PreviewUnchanged(native, preview) || ++preview.ticks > 1200) {
    preview.failed = true;
    ReleasePreviewNative(context, base, preview);
    return true;
  }
  // Native 1/120-second host-time budget; the evaluator steps its own physics,
  // not the paused live car. Result pairs follow the retained performance UI.
  const bool complete = native.Call(0x82656E58, {preview.model}) != 0;
  constexpr std::array<uint32_t, 7> offsets{0x474, 0x47C, 0x494, 0x4A4, 0x49C, 0x4BC, 0x4C4};
  bool changed = false;
  for (size_t i = 0; i < offsets.size(); ++i) {
    auto status = Read(base, preview.model + offsets[i] + 4);
    const auto value = ReadFloat(base, preview.model + offsets[i]);
    if (status > 2 || (status == 2 && (!std::isfinite(value) || value <= 0))) status = 1;
    changed |= preview.metrics[i].status != status || preview.metrics[i].value != value;
    preview.metrics[i] = {value, status};
  }
  if (!PreviewUnchanged(native, preview)) preview.failed = true;
  preview.complete = complete;
  if (complete && !preview.failed)
    REXLOG_INFO("Native car tuning: {} preview complete; garage/live/definition unchanged; results [{}, {}, {}, {}, {}, {}, {}]",
        preview.draft ? "draft" : "saved", preview.metrics[0].value, preview.metrics[1].value,
        preview.metrics[2].value, preview.metrics[3].value, preview.metrics[4].value,
        preview.metrics[5].value, preview.metrics[6].value);
  if (complete || preview.failed) ReleasePreviewNative(context, base, preview);
  return changed || complete || preview.failed;
}

} // namespace forza::car_tuning
#else
namespace forza::car_tuning {
bool NativeEnabled() { return false; }
NativeResult BeginNative(const PPCContext&, uint8_t*, Access, EditSession& edit) {
  edit.Close();
  return NativeResult::UnsupportedBuild;
}
NativeResult BeginCatalogNative(const PPCContext&, uint8_t*, Access, EditSession& edit) {
  edit.Close();
  return NativeResult::UnsupportedBuild;
}
NativeResult ApplyNative(const PPCContext&, uint8_t*, Access, EditSession&) {
  return NativeResult::UnsupportedBuild;
}
bool BeginPreviewNative(const PPCContext&, uint8_t*, Access, const EditSession&, NativePreview&) { return false; }
bool AdvancePreviewNative(const PPCContext&, uint8_t*, Access, NativePreview&) { return false; }
void ReleasePreviewNative(const PPCContext&, uint8_t*, NativePreview& preview) { preview = {}; }
} // namespace forza::car_tuning
#endif
