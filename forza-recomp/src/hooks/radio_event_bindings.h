// Manual verified disc/TU4 bindings; tu_address_map: TU4 addresses
#pragma once
#include <cstdint>
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
#define PrepareCutscene sub_829E5D38
#define OriginalPrepareCutscene __imp__sub_829E5D38
#define GenericFallback sub_8298CBD0
#define OriginalGenericFallback __imp__sub_8298CBD0
#define StopCutscene sub_8298C960
#define OriginalStopCutscene __imp__sub_8298C960
#define EventRequestCopy sub_825C50E8
#define OriginalEventRequestCopy __imp__sub_825C50E8
#define RaceCutsceneLookup sub_8298E3F8
#define OriginalRaceCutsceneLookup __imp__sub_8298E3F8
#define CutsceneLookup sub_82987470
#define OriginalCutsceneLookup __imp__sub_82987470
#define CutsceneMusicTrigger sub_82987358
#define OriginalCutsceneMusicTrigger __imp__sub_82987358
#define StartEventMusic sub_826157D8
#define OriginalStartEventMusic __imp__sub_826157D8
#define QueueEventMusic sub_8252A8D0
#define OriginalQueueEventMusic __imp__sub_8252A8D0
#define MarkScriptedStation sub_825FF7A0
#define OriginalMarkScriptedStation __imp__sub_825FF7A0
#define QueueSelectStation sub_82509AD8
#define QueueFreeRoam sub_825092A8
#define FreeRoamFlag sub_82593190
#define DestroyOwnedString sub_82441428
namespace forza::radio_event_native {
inline constexpr bool kTu4 = true;
inline constexpr uint32_t kRadioFreeRoam = 0x82C97F40;
inline constexpr uint32_t kRadioFreeRoamSlot = 0x30;
inline constexpr uint32_t kSelectStationSlot = 0x118, kSelectStationQueue = 0x82509AD8;
inline constexpr uint32_t kManager = 0x833D3F34;
inline constexpr uint32_t kSettings = 0x833ED710;
inline constexpr uint32_t kPlayer = 0x833D5BAC;
inline constexpr uint32_t kAudioManager = 0x833CFF68;
inline constexpr uint32_t kPrepareReturn = 0x829E5D94;
inline constexpr uint32_t kQualifiedReturn = 0x8298E4AC;
inline constexpr uint32_t kTrackReturn = 0x8298E524;
inline constexpr uint32_t kFallbackReturn = 0x8298E538;
inline constexpr uint32_t kVariantReturn = 0x8298CC48;
inline constexpr uint32_t kMusicReturn = 0x8298745C;
inline constexpr uint32_t kQueueReturn = 0x82615A10;
inline constexpr uint32_t kMarkerReturn = 0x82615AA0;
inline constexpr uint32_t kFreeRoamQueue = 0x825092A8;
inline constexpr uint32_t kFreeRoamSlot = 0x9C;
}
#else
#define PrepareCutscene sub_82932DA8
#define OriginalPrepareCutscene __imp__sub_82932DA8
#define GenericFallback sub_828DAE80
#define OriginalGenericFallback __imp__sub_828DAE80
#define StopCutscene sub_828DAC10
#define OriginalStopCutscene __imp__sub_828DAC10
#define EventRequestCopy sub_82553B30
#define OriginalEventRequestCopy __imp__sub_82553B30
#define RaceCutsceneLookup sub_828DC630
#define OriginalRaceCutsceneLookup __imp__sub_828DC630
#define CutsceneLookup sub_828D56F8
#define OriginalCutsceneLookup __imp__sub_828D56F8
#define CutsceneMusicTrigger sub_828D55E0
#define OriginalCutsceneMusicTrigger __imp__sub_828D55E0
#define StartEventMusic sub_8259E830
#define OriginalStartEventMusic __imp__sub_8259E830
#define QueueEventMusic sub_824BCE00
#define OriginalQueueEventMusic __imp__sub_824BCE00
#define MarkScriptedStation sub_82589580
#define OriginalMarkScriptedStation __imp__sub_82589580
#define QueueSelectStation sub_8249D140
#define QueueFreeRoam sub_8249C8D8
#define DestroyOwnedString sub_82411478
namespace forza::radio_event_native {
inline constexpr bool kTu4 = false;
inline constexpr uint32_t kRadioFreeRoam = 0x82BD8C28;
inline constexpr uint32_t kRadioFreeRoamSlot = 0x2C;
inline constexpr uint32_t kSelectStationSlot = 0x114, kSelectStationQueue = 0x8249D140;
inline constexpr uint32_t kManager = 0x832E6364;
inline constexpr uint32_t kSettings = 0x832FEBA0;
inline constexpr uint32_t kPlayer = 0x832E7F7C;
inline constexpr uint32_t kAudioManager = 0x832E2648;
inline constexpr uint32_t kPrepareReturn = 0x82932E04;
inline constexpr uint32_t kQualifiedReturn = 0x828DC6E4;
inline constexpr uint32_t kTrackReturn = 0x828DC75C;
inline constexpr uint32_t kFallbackReturn = 0x828DC770;
inline constexpr uint32_t kVariantReturn = 0x828DAEF8;
inline constexpr uint32_t kMusicReturn = 0x828D56E4;
inline constexpr uint32_t kQueueReturn = 0x8259EA68;
inline constexpr uint32_t kMarkerReturn = 0x8259EAF8;
inline constexpr uint32_t kFreeRoamQueue = 0x8249C8D8;
inline constexpr uint32_t kFreeRoamSlot = 0x98;
}
#endif
