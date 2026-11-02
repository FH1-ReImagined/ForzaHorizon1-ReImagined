# FH1 telemetry and adaptive triggers

## Physics-driven Xbox triggers

Updated launchers request `FORZA_TELEMETRY_PHYSICS=1`. Updated games then send
FH1T v2 (140 bytes): the existing 96-byte prefix, version 2 and size 140, followed
by little-endian `<10fI>`: RPM, redline RPM, speed (m/s), processed brake, throttle,
handbrake, normalized FL/FR/RL/RR longitudinal slips, and the game's ABS-active
word reduced to 0/1. Flag 16 says the physics fields are valid. Without it the
receiver retains pressure-based effects. Older launchers still receive v1, and
updated launchers accept v1 from older games. The session nonce, sequence,
endpoint checks, output acknowledgements and expiry rules still apply.

The brake trigger pulses at about 12 Hz while the game's ABS is active and the
car moves; tire lock beyond peak grip also produces feedback. The throttle
trigger combines RPM and tire spin beyond peak grip. Both are multiplied by
the game's processed pedal input and the existing independent user strengths.
Airborne wheels contribute no slip. Slip is corrected for reverse gear.
V2 driving state uses the existing world-state gate (pause, rewind, loading,
menu and simulation), rather than treating a retained car as proof of driving.
Normal controller body rumble and DualSense resistance remain separate.

`car_physics.h` reads ICarDynamics through checked guest ranges and rejects
unknown vtables, non-finite/out-of-range samples and inaccessible fields. The
common offsets are engine angular velocity +324, redline +452, maximum +456,
idle +1228, gear byte +2240, ABS +3020, speed +3128, controls +3904. Wheels start
at +3936: disc stride **1024**, TU4 stride **1040**. Each wheel has rotation
speed +12, normalized longitudinal slip +496, normalized slip angle +520 and
contact byte +796 on disc, +800 on TU4; suspension displacement is +0 minus spring rest +860 on disc, +876 on TU4.

A Windows TU4 copied-save run on 2026-10-23 exposed and corrected both moved
wheel fields. An independent UDP receiver captured 2,699 Horizon packets,
including 2,084 active samples, engine speed to 8,004 RPM, speed to 24.7 m/s,
full processed throttle/brake input and inactive packets after pause. The same
sampler feeds FH1T v2. Disc/TU4 native UDP probes and strict reader tests pass;
physical Xbox trigger output still requires controller hardware validation.
Angular velocity becomes RPM by 60/(2*pi). Gear 0 is reverse, 9 neutral.

Research reference: [Pinyon Shift's car dynamics findings](https://github.com/arcanite24/pinyon-shift/blob/main/src/pinyon_shift_runtime_hooks.cpp).
The disc and TU4 HUD getters were independently inspected for this implementation;
notably TU4 getters multiply wheel indices by 1040 (`mulli ... 1040`), while the
disc getters shift by 10. The known interface vtables are disc 82003EEC/8223759C,
TU4 82003F0C/8224A6E4. No generated game code or game data is included here.

Validation: 640 Windows launcher tests pass (37 skips); the checked reader's
synthetic disc/TU4 tests, native compilation of both telemetry adapters for both
builds, and actual native UDP v1/v2 probes pass. V2 probes verify RPM, all four
wheel strides, invalid input, remote-car exclusion and unmapped memory. Physical
Xbox controller feel/transport and real-game samples still require validation;
synthetic probes do not establish hardware or live offset qualification.

The following sections describe the original v1 bridge and its baseline effects.

The launcher Controls page offers optional DualSense / DualSense Edge throttle
and brake resistance. It uses the HorizonHaptics MIT-licensed trigger encoders
and USB/Bluetooth transport; attribution and local changes are recorded in
`launcher/forza_launcher/haptics/vendor/`. It does not run the upstream FH6
application or imitate its 324-byte packets.

This version uses raw player-one LT and RT after overlay input filtering.
Use the default in-game driving layout (LT brake, RT throttle). The controller
diagram selector changes the reference only. Zero resistance disables that
trigger; overall intensity scales both. Xbox and DualShock 4 have no adaptive
triggers. Body rumble, lightbar and player LEDs are not claimed by this writer.

Effects default off. Background workers handle HID and UDP I/O. Effects switch on above a raw trigger value of 12 and off below 4, and follow the
trigger in steps of 8, so a trigger held half way does not flicker. Effects release
after 350 ms without valid active telemetry, when disabled, on game stop/exit
and before launcher shutdown. Each game process gets a fresh nonce, so old
packets cannot reactivate effects. USB/Bluetooth coexistence with SDL, Steam
Input and controller remappers still requires real hardware testing. Linux
users need access to the Sony `/dev/hidraw` device; the launcher does not change
device permissions.

## Native producer

Both builds hook the physics substep `sub_82D4DE30` (TU4 `sub_82E100B0`) after
calling the original. This is the inner loop of `sub_82D4E2E0` / `sub_82E10560`,
rather than a render callback. The TU4 address was matched with
`tools/tu_address_map.py` against locally supplied executables.

The verified car path matches the existing world challenge reader:
`FORZA_ADDR(0x832DF024)` → root+4 → holder+4 → list vector at +0x80/+0x84.
It accepts at most 64 vehicles, selects kind 0 at vehicle+0x2DD0, reads car at
+0x2D90, position at car-0xA20 and forward at car-0xA40 (Y up). Reads check
mapped readable pages, bounds and finite vectors.

Velocity and acceleration are **derived from position and host monotonic time**,
not native wheel/chassis measurements. Car changes, gaps over 250 ms and jumps
over 200 m/s reset derivative validity. The initial resistance effect does not
use these derivatives. RPM, gear, tire slip, ABS state, road surface and actual
handbrake action remain unavailable; no effects are synthesized for them.
Derived acceleration uses the car's horizontal heading for X/Z and world-up Y.

`FORZA_TELEMETRY_PORT` (decimal 1–65535) and `FORZA_TELEMETRY_TOKEN` (16 hex digits,
nonzero) enable UDP output to **127.0.0.1 only**. Without both the producer does
no guest-memory reads or socket writes. The launcher chooses an ephemeral port
and random token for its child. `forza-telemetry.json` beside the executable
advertises protocol support. Older games launch normally and Controls explains
that a rebuild is needed.

## FH1T v1 wire format

96 bytes, little endian, Python struct `<4sHHQIIQII12fBBBbHH`:

| Offset | Value |
| --- | --- |
| 0 | `FH1T` magic, 4 bytes |
| 4 | version u16 = 1; size u16 = 96 |
| 8 | session nonce u64 |
| 16 | sequence u32; validity flags u32 |
| 24 | producer monotonic milliseconds u64 |
| 32 | car identity u32; active u32 (0 or 1) |
| 40 | position XYZ, world velocity XYZ, local acceleration XYZ, forward XYZ: 12 f32 |
| 88 | raw LT u8, RT u8, unavailable handbrake u8 = 0, raw steer i8 |
| 92 | raw Xbox button mask u16; reserved u16 = 0 |

Validity bits: position/forward 1, fresh raw input 2, derived velocity 4,
derived acceleration 8. Active requires a local car, a physics substep and
player-one input read within 250 ms, and a non-zero rumble
request from the game within 3 s: the car stays in the vehicle list in menus, but the
game only rumbles while it runs (#224). Sequence comparison accepts u32 wrap and
rejects duplicates/reordered packets. The receiver checks length, version,
token, fields and finite floats. Its watchdog uses receive time, without
comparing clock epochs across processes. This is local IPC, not an authenticated
network service.

## Validation

`python -m unittest discover -s launcher -p 'test_*.py'` covers the receiver,
watchdog, session rotation, disable/exit reset, report flags/CRC, preferences and
Controls UI. `launcher/packaging/smoke_gui.py PATH/forza-launcher.exe` checks the
frozen backend import, artwork, page rendering and settings persistence without
opening a HID device.

`forza-recomp/tests/telemetry_probe.cpp` is a Windows-only native producer probe.
Build it with `src/platform/telemetry.cpp`, C++23, the SDK include paths and
`rexruntime.lib` / `ws2_32.lib`, using the same `FORZA_TITLE_UPDATE` definition
for both sources. Put the SDK runtime DLLs beside the probe. Run
`python tools/test_telemetry_bridge.py PATH/telemetry_probe.exe` for each build.
It creates sparse synthetic guest memory and verifies the actual native UDP
wire format, raw input mapping, derivatives, teleport invalidation, remote-car
exclusion and unreadable pointers, without game data or a controller.

Local Windows validation: native touched sources compiled for base and TU4;
both native probes passed; TU4 hook linked against existing local generated
objects and produced 3,919 valid packets during an isolated offline run using
a copied save. The run reached free roam and supplied real car positions.
The validation host also contains existing local diagnostic hooks; it was not
a clean full rebuild of both game versions. Physical DualSense output, Bluetooth,
Linux permissions and Steam/remapper coexistence have not been hardware tested.

## Xbox impulse triggers

The Controls page has a separate opt-in Xbox trigger vibration card. Defaults:
overall intensity 50%, RT throttle 35%, LT brake 70%. These multiply raw RT/LT
input values; they do not simulate ABS or traction from unavailable data.
Zero disables a channel. DualSense preferences remain independent.

The launcher sends output commands back to the native FH1 bridge. SDK patch 0090
uses the game's existing SDL controller driver and gamepad handles, rather than
opening another controller connection from Python. This lets SDL preserve normal
body rumble when a backend builds a combined device report. Only Xbox One/Series
devices that advertise SDL's trigger-rumble capability receive effects. Xbox 360
and unsupported connections are skipped. Connection and driver support must be
checked on real hardware; no particular USB/Bluetooth/Wireless transport has
been hardware-validated here.

The native route uses the same guest-user assignments as input. In the default
merged-controller mode it follows the game's existing rumble behavior across all
four guest slots; per-user mode selects only player one. SDK master vibration,
UI input blocking and focus also suppress trigger output. Each effect lasts at
most 150 ms and is renewed by fresh driving telemetry; SDL's event/joystick pump
expires it even when physics pauses. Disable, session rotation and launcher/game
shutdown also stop renewals and send zero output when the game can receive it.

The game metadata adds `xbox_trigger_rumble: 1`; older FH1T bridges remain usable
for DualSense. The optional runtime export is resolved at run time. An old SDK
does not prevent game startup; its acknowledgement asks the player to update the
launcher and rebuild. No extra Python library or controller DLL is bundled.

### Output wire format

`FH1X` commands: 32 bytes, little endian `<4sHHQIIHHI`: magic, version u16 = 1,
size u16 = 32, session nonce u64, command sequence u32, echoed FH1T sequence u32,
LT amplitude u16, RT amplitude u16, reserved u32 = 0. Amplitudes range 0–65535.
Only the configured loopback receiver IP/port may send commands. The echoed
sample must be among the last four transmitted and at most 150 ms old; command
sequences must advance (u32 wrap supported). The physics thread drains at most
64 messages per sample and applies only the latest valid command. Inactive
telemetry forces both amplitudes to zero.

`FH1A` acknowledgements: 24 bytes `<4sHHQIi`: magic, version/size, nonce,
command sequence, signed result. Positive = number of supported controllers
written, 0 = no supported device, -1 = output failure, -2 = runtime missing the
optional export. Acknowledgements must come from the current producer endpoint
and match an advancing, already-sent command. They do not refresh telemetry's
watchdog or count as driving activity.

### Xbox validation

`launcher/test_xbox_haptics.py` covers preferences, amplitudes, protocol guards,
acknowledgements, capability gating, live disable and stale telemetry. The
launcher suite passes 228 tests (23 skipped). `tools/test_xbox_bridge.py` drives
`telemetry_probe.exe --xbox` and checks native commands, acknowledgements, nonce,
sender port, reserved fields, ordering and a queued command expiring across a
220 ms pause; it passed for base and TU4. Existing native FH1T probes still pass.

`forza-recomp/tests/xbox_rumble_probe.cpp` tests the real patched SDK input code
with SDL virtual gamepads, without game files or a physical controller. Build
it into a standalone console executable using the patched SDK runtime object
files and their normal link libraries, so the SDK code and virtual devices share
one SDL instance. Compile the probe against the patched SDK headers. Its fixture
opens private SDL driver state only to register virtual devices; assertions use
public output methods and real virtual-device callbacks. It verifies amplitudes,
guest-user routing, capability rejection, body rumble preservation, UI/focus/
vibration gating and the 150 ms expiry. It passed locally on Windows. Native
modified game sources also compile for both base and TU4; the TU4 host and the
modified runtime link. Physical trigger feel, transport compatibility and Linux
remain to be tested.
