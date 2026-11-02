// Which joysticks are wheels and which is the wheel (SDK patch 0134, src/input/wheel/wheel_primary.h,
// #306): wheel_device first, then force feedback, then SDL's wheels, then SDL's order; pedal,
// shifter and handbrake devices are no wheels unless wheel_device names them. Build against the
// patched SDK source (tools/build_rexglue_sdk.sh --source-only), from the repository root:
//   SDK=~/rexglue-sdk-src-forza/sdk; g++ -std=c++17 -Wall -Wextra -Werror -I $SDK/include
//       -I $SDK/src/input/wheel forza-recomp/tests/wheel_primary_test.cpp -o wheel-primary-test
//   ./wheel-primary-test
#include <wheel_primary.h>

#include <cstdio>
#include <cstdlib>

using namespace rex::input::wheel;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

JoyInfo Joy(const char* name, uint16_t vid, uint16_t pid, bool sdl_wheel = false) {
  JoyInfo j;
  j.name = name;
  j.guid = "0300000000000000000000000000000" + std::to_string(vid % 10);
  j.vid = vid;
  j.pid = pid;
  j.sdl_wheel = sdl_wheel;
  return j;
}

using V = std::vector<int>;
}  // namespace

int main() {
  // #306: with wheel_mode = on every joystick without a gamepad mapping is wheel-like; vJoy (SimHub's
  // pedals, handbrake and shifter) and a button box come before the Fanatec base in SDL's order.
  const std::vector<JoyInfo> joys = {
      Joy("Shaul Eizikovich vJoy - Virtual Joystick", 0x1234, 0xBEAD),
      Joy("LeafLabs Maple", 0x1EAF, 0x0024),
      Joy("FANATEC Wheel", 0x0EB7, 0x0020, true),   // an interface without force feedback
      Joy("FANATEC Wheel", 0x0EB7, 0x0020, true)};  // the one with force feedback
  const std::vector<bool> like = {true, true, true, true};
  const std::vector<bool> ffb = {false, false, false, true};
  const std::vector<bool> none = {false, false, false, false};
  Check(WheelOrder(joys, like, ffb, none, "") == V{3, 2, 0, 1},
        "auto: force feedback first, then SDL's wheels, then SDL's order");
  Check(WheelOrder(joys, like, none, none, "") == V{2, 3, 0, 1}, "without force feedback: SDL's wheels");
  Check(WheelOrder({joys[0], joys[1]}, {true, true}, {false, false}, {false, false}, "") == V{0, 1},
        "otherwise SDL's order");
  // The pedal, shifter or handbrake device is no wheel.
  const std::vector<bool> vjoy_aux = {true, false, false, false};
  Check(WheelOrder(joys, like, ffb, vjoy_aux, "") == V{3, 2, 1}, "the pedal device is no wheel");
  // wheel_device: exact name, vid:pid, GUID or part of a name; it makes the device a wheel.
  Check(WheelOrder(joys, like, ffb, none, "LeafLabs Maple")[0] == 1, "named exactly");
  Check(WheelOrder(joys, like, ffb, none, "1eaf:0024")[0] == 1, "by vid:pid");
  Check(WheelOrder(joys, like, ffb, none, "fanatec") == V{3, 2, 0, 1},
        "part of a name: of two matches the one with force feedback");
  Check(WheelOrder(joys, {false, false, true, true}, ffb, none, "vjoy")[0] == 0,
        "a named device is a wheel even when it is not wheel-like");
  Check(WheelOrder(joys, like, ffb, vjoy_aux, "vjoy")[0] == 0, "and even when it is an aux device");
  Check(WheelOrder(joys, like, ffb, none, "LeafLabs Maple") == V{1, 3, 2, 0},
        "the others keep their order after it");
  // Exact before part of a name.
  const std::vector<JoyInfo> two = {Joy("Wheel Pro X", 1, 2), Joy("Wheel Pro", 3, 4)};
  Check(WheelOrder(two, {true, true}, {false, false}, {false, false}, "wheel pro")[0] == 1,
        "an exact name before part of one");
  // Not named: auto; none of these settings names a device.
  Check(WheelOrder(joys, like, ffb, none, "none") == WheelOrder(joys, like, ffb, none, ""),
        "none is auto");
  Check(WheelOrder(joys, like, ffb, none, "missing") == WheelOrder(joys, like, ffb, none, ""),
        "a device that is not there: auto");
  Check(WheelOrder({}, {}, {}, {}, "").empty(), "no joysticks");
  Check(WheelOrder(joys, none, ffb, none, "").empty(), "nothing wheel-like");
  // #562: wheel_device saved under the launcher's name ("Thrustmaster TX Racing Wheel") while the
  // game's SDL has no name for the TX ("44F B669"). A name that matches nothing stands for a
  // wheel-like joystick without a name of its own; a vid:pid or a name that does match wins.
  const std::vector<JoyInfo> tx_joys = {
      Joy("Shaul Eizikovich vJoy - Virtual Joystick", 0x1234, 0xBEAD),
      Joy("44F B669", 0x044F, 0xB669, true),
      Joy("44F B371", 0x044F, 0xB371)};  // the T-LCM, read as the pedal device
  const std::vector<bool> tx_like = {true, true, true};
  const std::vector<bool> tx_aux = {false, false, true};
  const std::vector<bool> tx_ffb = {false, false, false};
  Check(WheelOrder(tx_joys, tx_like, tx_ffb, tx_aux, "Thrustmaster TX Racing Wheel") == V{1, 0},
        "a name no joystick has: the nameless wheel is the wheel, before the vJoy");
  Check(WheelOrder(tx_joys, tx_like, tx_ffb, tx_aux, "044f:b669") == V{1, 0}, "by vid:pid");
  Check(WheelOrder(tx_joys, tx_like, tx_ffb, tx_aux, "") == V{1, 0}, "auto: SDL's wheel first");
  Check(WheelOrder(tx_joys, tx_like, tx_ffb, tx_aux, "vJoy - Virtual")[0] == 0,
        "a name that matches names the one it matches");
  Check(WheelOrder(tx_joys, tx_like, tx_ffb, tx_aux, "none") == V{1, 0}, "none is auto");
  const std::vector<bool> only_vjoy = {true, false, true};
  Check(WheelOrder(tx_joys, only_vjoy, tx_ffb, tx_aux, "Thrustmaster TX Racing Wheel") == V{0},
        "a nameless joystick that is no wheel does not stand in");
  Check(WheelOrder({Joy("Wheel A", 1, 2, true), Joy("Wheel B", 3, 4, true)}, {true, true},
                   {false, false}, {false, false}, "Wheel C") == V{0, 1},
        "a name that matches nothing, no nameless wheel: auto");
  const std::vector<int> scores = WheelDeviceScores(tx_joys, tx_like, tx_aux, "TX Racing Wheel");
  Check(scores == std::vector<int>{0, 1, 0}, "scores: the nameless wheel, not the pedals");
  std::puts("wheel primary: ok");
  return 0;
}
