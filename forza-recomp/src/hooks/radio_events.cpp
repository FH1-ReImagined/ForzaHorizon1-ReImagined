// Opt-in radio preservation for career/Rally race-opening music cues.
//
// CPrepareCutscene's race-specific path resolves exact pre-race canonical names.
// The ordered native track/variant lookup chain may bind its selected full key;
// numbered fallbacks and unknown/story/post-race names confer no authority.
// Publication waits for native preparation to finish. Per-record revisions and
// unique binding tickets revoke authority across stop/reprepare and nested reuse. An accepted current load invalidates every old binding before its
// native copy runs; publication after the copy makes reentrant observations fail
// closed. The request type must also agree with the native race settings.
//
// A bound record must be in the native running state (actual/desired phase 3),
// with its own live timeline. Only that record's timed music callback and exact
// helper enqueue/marker callsites may redirect. Nested ineligible calls mask TLS
// authority. No lock spans a native call; host binding state is synchronized.
//
// The original helper still owns dialogue, DJ/lock cleanup and its own string.
// Replace only its by-value deferred event-music command with the native Free
// Roam command, including its normal one-second fade and TU4 context flag. Off
// uses the native TU4 restore flag or disc deferred radio_off selection. Once
// queued, the matching marker clears this request's forced-station attribution
// even if a new load starts; normal native queue teardown remains authoritative.
// Default off calls each original unchanged. CPU contract fixtures do not prove
// native output or Off behavior; those require bounded game captures.
#include "radio_event_bindings.h"
#include "../platform/guest_memory.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <optional>
#include <string>
#include <vector>
#include <unordered_map>

REXCVAR_DEFINE_BOOL(radio_keep_in_events, false, "Audio", "Keep selected radio during career/Rally race-opening music cues, including Off");
extern "C" REX_FUNC(OriginalPrepareCutscene);
extern "C" REX_FUNC(OriginalGenericFallback);
extern "C" REX_FUNC(OriginalStopCutscene);
extern "C" REX_FUNC(OriginalEventRequestCopy);
extern "C" REX_FUNC(OriginalRaceCutsceneLookup);
extern "C" REX_FUNC(OriginalCutsceneLookup);
extern "C" REX_FUNC(OriginalCutsceneMusicTrigger);
extern "C" REX_FUNC(OriginalStartEventMusic);
extern "C" REX_FUNC(OriginalQueueEventMusic);
extern "C" REX_FUNC(OriginalMarkScriptedStation);
extern "C" REX_FUNC(QueueFreeRoam);
#if !defined(FORZA_TITLE_UPDATE) || FORZA_TITLE_UPDATE != 4
extern "C" REX_FUNC(QueueSelectStation);
#endif
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(FreeRoamFlag);
#endif
extern "C" REX_FUNC(DestroyOwnedString);

namespace {
using namespace forza::radio_event_native;
bool Enabled() { static const bool enabled = REXCVAR_GET(radio_keep_in_events); return enabled; }
bool Trace() {
  static const bool trace = [] { const char* value = std::getenv("FORZA_RADIO_EVENTLOG"); return value && value[0] == '1'; }();
  return trace;
}
uint32_t Read(uint8_t* base, uint32_t a) {
  const auto* p = forza::GuestPtr(base, a);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
std::optional<std::string> String(uint8_t* base, uint32_t a) {
  if (!a) return {};
  const auto size = Read(base, a + 16), capacity = Read(base, a + 20);
  if (!size || size > 255 || size > capacity) return {};
  const auto data = capacity >= 16 ? Read(base, a) : a;
  if (!data) return {};
  const auto* p = forza::GuestPtr(base, data);
  std::string result(reinterpret_cast<const char*>(p), size);
  if (std::any_of(result.begin(), result.end(), [](unsigned char c) { return c < 32 || c > 126; })) return {};
  return result;
}
bool Canonical(const std::string& name) {
  return name == "prerace_intro" || name == "prerace_countdown" || name == "prerace_looping_01";
}
struct Token { uint8_t* base; uint32_t manager; uint64_t epoch; };
struct Binding { Token token; uint32_t record; std::string full_key; uint64_t ticket; };
struct State {
  std::mutex mutex;
  uint8_t* base = nullptr;
  uint32_t manager = 0;
  uint64_t epoch = 0;
  uint64_t revision = 0, next_ticket = 0;
  std::unordered_map<uint32_t, uint64_t> record_revisions;
  unsigned transitions = 0;
  std::vector<Binding> bindings;
} state;
void ResetLocked(uint8_t* base, uint32_t manager) {
  if (state.base == base && state.manager == manager) return;
  state.base = base; state.manager = manager; ++state.epoch; state.bindings.clear();
}
std::optional<Token> Snapshot(uint8_t* base) {
  const auto manager = Read(base, kManager), settings = Read(base, kSettings);
  if (!manager || !settings) return {};
  const auto type = Read(base, settings + 4);
  if ((type != 3 && !(kTu4 && type == 20)) || Read(base, manager + 8 + 44) != type) return {};
  std::lock_guard lock(state.mutex);
  ResetLocked(base, manager);
  if (state.transitions) return {};
  return Token{base, manager, state.epoch};
}
bool Same(const Token& a, const Token& b) { return a.base == b.base && a.manager == b.manager && a.epoch == b.epoch; }
bool Current(const Token& token) { const auto now = Snapshot(token.base); return now && Same(*now, token); }
struct Transition {
  bool active = false;
  Transition(uint8_t* base, uint32_t destination) {
    const auto manager = Read(base, kManager);
    if (!manager || destination != manager + 8) return;
    std::lock_guard lock(state.mutex); ResetLocked(base, manager);
    ++state.transitions; ++state.epoch; state.bindings.clear(); active = true;
  }
  ~Transition() {
    if (!active) return;
    std::lock_guard lock(state.mutex); --state.transitions; ++state.epoch; state.bindings.clear();
  }
};
template<class T> struct Scope {
  T*& slot; T* previous;
  Scope(T*& target, T* value) : slot(target), previous(target) { slot = value; }
  Scope(T*& target, T& value) : Scope(target, &value) {}
  ~Scope() { slot = previous; }
};
uint64_t Revision() { std::lock_guard lock(state.mutex); return state.revision; }
uint64_t Invalidate(uint32_t record) {
  std::lock_guard lock(state.mutex);
  std::erase_if(state.bindings, [&](const Binding& b) { return b.record == record; });
  ++state.record_revisions[record];
  return ++state.revision;
}
struct Query {
  Token token; uint32_t receiver; std::string canonical;
  uint64_t revision; unsigned next = 0; bool valid = true;
  uint32_t record = 0; std::string full_key; uint64_t record_revision = 0;
};
struct Preparation { uint8_t* base; std::optional<Query> candidate; uint32_t record = 0; };
thread_local Preparation* preparation = nullptr;
thread_local Query* fallback = nullptr;
thread_local Query* query = nullptr;
bool Bind(const Query& q) {
  if (!q.valid || !q.record || q.full_key.empty() || !Current(q.token)) return false;
  const auto actual = String(q.token.base, q.record + 4);
  if (!actual || *actual != q.full_key) return false;
  std::lock_guard lock(state.mutex);
  if (state.record_revisions[q.record] != q.record_revision || state.transitions || state.epoch != q.token.epoch || state.base != q.token.base || state.manager != q.token.manager) return false;
  std::erase_if(state.bindings, [&](const Binding& b) { return b.record == q.record; });
  if (state.bindings.size() >= 32) return false;
  state.bindings.push_back({q.token, q.record, q.full_key, ++state.next_ticket});
  return true;
}
std::optional<Binding> Active(uint8_t* base, uint32_t record) {
  const auto token = Snapshot(base);
  if (!token || !record || Read(base, record + 80) != 3 || Read(base, record + 84) != 3) return {};
  const auto timeline = Read(base, record + 192);
  if (!timeline || Read(base, timeline + 4) != record) return {};
  const auto key = String(base, record + 4);
  if (!key) return {};
  std::lock_guard lock(state.mutex);
  if (state.transitions || state.epoch != token->epoch) return {};
  for (const auto& binding : state.bindings)
    if (Same(binding.token, *token) && binding.record == record && binding.full_key == *key) return binding;
  return {};
}
struct Request { Binding binding; uint32_t player; uint32_t audio_manager; bool redirected = false; };
thread_local Request* music = nullptr;
thread_local Request* request = nullptr;
bool Valid(const Request& r) {
  const auto active = Active(r.binding.token.base, r.binding.record);
  return active && Same(active->token, r.binding.token) && active->full_key == r.binding.full_key && active->ticket == r.binding.ticket &&
         Read(r.binding.token.base, kPlayer) == r.player;
}
std::optional<bool> SelectedOff(uint8_t* base, uint32_t manager) {
  const auto worker = Read(base, manager + 12);
  const auto radio = worker ? Read(base, worker + 52) : 0;
  const auto table = radio ? Read(base, radio) : 0;
  if (!table || Read(base, table + kRadioFreeRoamSlot) != kRadioFreeRoam) return {};
  const auto normal = Read(base, radio + 168), total = Read(base, radio + 176);
  const auto selected = Read(base, radio + 172);
  if (normal != 3 || total <= normal || total > 64 || selected > normal) return {};
  return selected == normal;
}

}

extern "C" REX_FUNC(EventRequestCopy) {
  if (!Enabled()) { OriginalEventRequestCopy(ctx, base); return; }
  if (Trace()) REXLOG_INFO("RADIOEVENT request-copy receiver={:08X} caller={:08X}", ctx.r3.u32, uint32_t(ctx.lr));
  Transition transition(base, ctx.r3.u32);
  OriginalEventRequestCopy(ctx, base);
}
extern "C" REX_FUNC(PrepareCutscene) {
  if (!Enabled()) { OriginalPrepareCutscene(ctx, base); return; }
  Preparation p{base, {}};
  Scope scope(preparation, p);
  Scope mask(query, static_cast<Query*>(nullptr));
  OriginalPrepareCutscene(ctx, base);
  // A stale outer prepare may overwrite a nested prepare's music name on return.
  // Revoke that record instead of leaving the nested binding alive.
  if (!p.candidate || p.record != p.candidate->record || !Bind(*p.candidate)) Invalidate(p.record);
}
extern "C" REX_FUNC(StopCutscene) {
  if (Enabled()) Invalidate(ctx.r3.u32); // Invalidate before native stop drains callbacks.
  OriginalStopCutscene(ctx, base);
}
extern "C" REX_FUNC(RaceCutsceneLookup) {
  const auto caller = ctx.lr;
  const auto receiver = ctx.r3.u32;
  const auto token = Enabled() && preparation && preparation->base == base && caller == kPrepareReturn ? Snapshot(base) : std::nullopt;
  const auto name = token ? String(base, ctx.r4.u32) : std::nullopt;
  if (Enabled() && Trace()) {
    const auto diagnostic_name = String(base, ctx.r4.u32);
    REXLOG_INFO("RADIOEVENT race-lookup caller={:08X} token={} canonical={}", uint32_t(caller), bool(token), diagnostic_name.value_or("<empty>"));
  }
  if (!token || !name || !Canonical(*name)) { Scope mask(query, static_cast<Query*>(nullptr)); OriginalRaceCutsceneLookup(ctx, base); return; }
  Query q{*token, receiver, *name, Revision(), 0, true, 0, {}};
  Scope scope(query, q);
  OriginalRaceCutsceneLookup(ctx, base);
  if (Trace()) REXLOG_INFO("RADIOEVENT selected key={} record={:08X} final={:08X} valid={}", q.full_key, q.record, ctx.r3.u32, q.valid);
  if (q.valid && receiver && q.record && ctx.r3.u32 == q.record) preparation->candidate = q;
}
extern "C" REX_FUNC(GenericFallback) {
  auto* q = query;
  const auto name = q ? String(base, ctx.r4.u32) : std::nullopt;
  const bool qualified = Enabled() && q && q->valid && q->next == 2 && !q->record &&
      q->token.base == base && ctx.lr == kFallbackReturn && ctx.r3.u32 == q->receiver &&
      name && *name == q->canonical && Current(q->token) && Revision() == q->revision;
  Scope scope(fallback, qualified ? q : nullptr);
  if (!qualified) { Scope mask(query, static_cast<Query*>(nullptr)); OriginalGenericFallback(ctx, base); return; }
  OriginalGenericFallback(ctx, base);
}
extern "C" REX_FUNC(CutsceneLookup) {
  const auto caller = ctx.lr;
  auto* q = query;
  const bool qualified = Enabled() && q && q->valid && !q->record && q->next < 3 &&
      caller == (q->next == 0 ? kQualifiedReturn : q->next == 1 ? kTrackReturn : kVariantReturn) &&
      (q->next != 2 || fallback == q) && q->token.base == base && ctx.r3.u32 == q->receiver &&
      Current(q->token) && Revision() == q->revision;
  const auto key = Enabled() && (qualified || Trace()) ? String(base, ctx.r4.u32) : std::nullopt;
  if (q && !qualified) q->valid = false;
  OriginalCutsceneLookup(ctx, base);
  if (Enabled() && Trace() && key) REXLOG_INFO("RADIOEVENT lookup caller={:08X} key={} result={:08X}", uint32_t(caller), *key, ctx.r3.u32);
  // A native prepare selecting this shared record revokes its previous authority.
  // Revision checks prevent an in-flight query publishing across stop/reprepare.
  if (Enabled() && preparation && preparation->base == base) {
    preparation->record = ctx.r3.u32;
    std::lock_guard lock(state.mutex);
    const bool unchanged = !q || state.revision == q->revision;
    std::erase_if(state.bindings, [&](const Binding& b) { return b.record == ctx.r3.u32; });
    ++state.revision;
    const auto record_revision = ++state.record_revisions[ctx.r3.u32];
    if (q) {
      q->valid = q->valid && qualified && unchanged && bool(key);
      q->revision = state.revision;
      if (q->valid) { ++q->next; q->record = ctx.r3.u32; q->full_key = *key; q->record_revision = record_revision; }
    }
  }
}
extern "C" REX_FUNC(CutsceneMusicTrigger) {
  const auto binding = Enabled() ? Active(base, ctx.r3.u32) : std::nullopt;
  if (Enabled() && Trace()) {
    const auto record = ctx.r3.u32;
    REXLOG_INFO("RADIOEVENT music record={:08X} key={} phase={}/{} bound={}", record, record ? String(base, record + 4).value_or("<empty>") : "<null>", record ? Read(base, record + 80) : 0, record ? Read(base, record + 84) : 0, bool(binding));
  }
  Scope mask_request(request, static_cast<Request*>(nullptr));
  if (!binding) { Scope mask(music, static_cast<Request*>(nullptr)); OriginalCutsceneMusicTrigger(ctx, base); return; }
  Request r{*binding, Read(base, kPlayer), Read(base, kAudioManager)};
  Scope scope(music, r);
  OriginalCutsceneMusicTrigger(ctx, base);
}
extern "C" REX_FUNC(StartEventMusic) {
  auto* r = music;
  if (!Enabled() || !r || r->binding.token.base != base || ctx.lr != kMusicReturn ||
      ctx.r3.u32 != r->player || !Valid(*r)) { Scope mask(request, static_cast<Request*>(nullptr)); OriginalStartEventMusic(ctx, base); return; }
  Scope scope(request, *r);
  OriginalStartEventMusic(ctx, base);
}
extern "C" REX_FUNC(QueueEventMusic) {
  auto* r = request;
  if (!Enabled() || !r || r->binding.token.base != base || r->redirected || ctx.lr != kQueueReturn || !Valid(*r)) {
    OriginalQueueEventMusic(ctx, base); return;
  }
  const auto manager = ctx.r3.u32, name = ctx.r4.u32;
  const auto table = manager && manager == r->audio_manager && manager == Read(base, kAudioManager) ? Read(base, manager) : 0;
  if (!table || Read(base, table + kFreeRoamSlot) != kFreeRoamQueue) { OriginalQueueEventMusic(ctx, base); return; }
  const auto off = SelectedOff(base, manager);
  if (!off || (*off && !kTu4 && Read(base, table + kSelectStationSlot) != kSelectStationQueue)) { OriginalQueueEventMusic(ctx, base); return; }
  PPCContext call = ctx;
  call.f1.f64 = 1.0; // Native play_freeroam_music_with_fades; avoids cached zero-fade skip.
  call.r5.u64 = 0; call.r6.u64 = 0;
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  PPCContext flag = ctx;
  FreeRoamFlag(flag, base);
  call.r6.u64 = (flag.r3.u32 & 0xFF) != 0 || *off; // Native force-restore path is required when selected radio is Off.
#endif
  // The native predicate must not let a reentrant load commit stale authority.
  if (!Valid(*r) || Read(base, kAudioManager) != manager || Read(base, manager) != table ||
      Read(base, table + kFreeRoamSlot) != kFreeRoamQueue) { OriginalQueueEventMusic(ctx, base); return; }
  r->redirected = true; // Queue commit: matching marker must remain coherent across later load transitions.
  QueueFreeRoam(call, base);
#if !defined(FORZA_TITLE_UPDATE) || FORZA_TITLE_UPDATE != 4
  // Disc's fading Free Roam command cannot restore Off. Reaffirm native radio_off
  // through CSetRadioStation's own deferred API after the Free Roam mode cleanup.
  // This avoids the immediate-Free-Roam command's cached-fade skip entirely.
  if (*off && Valid(*r) && Read(base, kAudioManager) == manager && Read(base, manager) == table &&
      Read(base, table + kSelectStationSlot) == kSelectStationQueue) {
    PPCContext select = ctx;
    select.r3.u64 = manager; select.r4.u64 = 3; select.r5.u64 = 0; select.r6.u64 = 1;
    QueueSelectStation(select, base);
  }
#endif
  ctx.r3.u64 = name; ctx.r4.u64 = 1; ctx.r5.u64 = 0;
  DestroyOwnedString(ctx, base); // Replaced by-value queue argument, exactly once.
  REXLOG_INFO("Radio: retained selected station for a qualified career event cue");
}
extern "C" REX_FUNC(MarkScriptedStation) {
  auto* r = request;
  if (Enabled() && r && r->redirected && r->binding.token.base == base && ctx.r3.u32 == r->player && ctx.lr == kMarkerReturn) {
    ctx.r4.u64 = 0; ctx.r5.u64 = uint32_t(-1);
  }
  OriginalMarkScriptedStation(ctx, base);
}
