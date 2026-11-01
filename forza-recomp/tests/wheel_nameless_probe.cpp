// A pedal set, a shifter and a wheel base that Windows gives no name (#562): a Thrustmaster TX
// (044f:b669), T-LCM pedals (044f:b371) and a TH8 RS shifter (044f:b660). The game's SDL 3 on
// Windows calls the first two "44F B669" and "44F B371" while the launcher (SDL 2, DirectInput)
// lists them as "Thrustmaster TX Racing Wheel" and "T-LCM Pedals", and saved the settings under
// those names. The pedals are found by the name the launcher saved, by their vid:pid, and a name
// that matches nothing with no nameless pedal set around leaves them out; the log lists every
// joystick when a configured device is missing.
//
// Linux only (needs write access to /dev/uinput). Build against the ReIGlue source and link the
// runtime built from it, from the repository root, as wheel_devices_probe.cpp does:
//   SDK=~/reiglue-src-forza/sdk; clang++ -std=c++23 -msse4.1 -I$SDK/include
//       -I$SDK/thirdparty/sdl3/include -I$SDK/thirdparty/fmt/include -I$SDK/thirdparty/spdlog/include
//       -I$SDK/thirdparty/simde -I$SDK/src/input/wheel
//       -DSPDLOG_COMPILED_LIB -DSPDLOG_FMT_EXTERNAL forza-recomp/tests/wheel_nameless_probe.cpp
//       -L ~/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib -lrexruntime -pthread
//       -Wl,-rpath,$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib -o wheel-nameless-probe
//   ./wheel-nameless-probe
#include <rex/cvar.h>
#include <rex/input/wheel/wheel_input_driver.h>

#include <fcntl.h>
#include <linux/uinput.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <thread>
#include <vector>

using namespace rex::input::wheel;
using rex::X_STATUS;

namespace {

void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

// A uinput device: axes (code, min, max, rest) and buttons BTN_TRIGGER + n (SDL's button n).
struct Device {
  int fd = -1;

  struct Axis {
    int code, min, max, rest;
  };
  bool Create(const char* name, uint16_t vid, uint16_t pid, std::vector<Axis> axes, int buttons) {
    fd = open("/dev/uinput", O_RDWR | O_NONBLOCK);
    if (fd < 0) {
      return false;
    }
    if (buttons) {
      ioctl(fd, UI_SET_EVBIT, EV_KEY);
      for (int b = 0; b < buttons; b++) {
        ioctl(fd, UI_SET_KEYBIT, BTN_TRIGGER + b);
      }
    }
    ioctl(fd, UI_SET_EVBIT, EV_ABS);
    for (const auto& a : axes) {
      uinput_abs_setup abs{};
      abs.code = static_cast<__u16>(a.code);
      abs.absinfo.minimum = a.min;
      abs.absinfo.maximum = a.max;
      abs.absinfo.value = a.rest;
      ioctl(fd, UI_SET_ABSBIT, a.code);
      ioctl(fd, UI_ABS_SETUP, &abs);
    }
    uinput_setup setup{};
    setup.id = {BUS_USB, vid, pid, 0x111};
    std::snprintf(setup.name, sizeof(setup.name), "%s", name);
    return ioctl(fd, UI_DEV_SETUP, &setup) >= 0 && ioctl(fd, UI_DEV_CREATE) >= 0;
  }

  void Emit(int type, int code, int value) {
    input_event ev{};
    ev.type = static_cast<__u16>(type);
    ev.code = static_cast<__u16>(code);
    ev.value = value;
    write(fd, &ev, sizeof(ev));
    input_event syn{};
    syn.type = EV_SYN;
    syn.code = SYN_REPORT;
    write(fd, &syn, sizeof(syn));
  }
  void SetAxis(int code, int value) { Emit(EV_ABS, code, value); }
  void SetButton(int n, bool down) { Emit(EV_KEY, BTN_TRIGGER + n, down ? 1 : 0); }

  void Destroy() {
    if (fd >= 0) {
      ioctl(fd, UI_DEV_DESTROY);
      close(fd);
      fd = -1;
    }
  }
  ~Device() { Destroy(); }
};

void SetCvar(const char* name, const char* value) {
  for (rex::cvar::FlagEntry& entry : rex::cvar::GetRegistry()) {
    if (entry.name == name) {
      entry.setter(value);
      return;
    }
  }
  Check(false, "cvar not found");
}

void Sleep(int ms) {
  std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

// The wheel's state as the game reads it.
rex::input::X_INPUT_GAMEPAD State(WheelInputDriver& d, rex::input::DeviceId id) {
  rex::input::X_INPUT_STATE s{};
  Check(d.GetDeviceState(id, &s) == 0, "state");
  return s.gamepad;
}

// Enumerates until `want` wheels are there (the driver rescans every 500 ms).
std::vector<rex::input::DeviceInfo> Wheels(WheelInputDriver& d, size_t want, int ms = 3000) {
  std::vector<rex::input::DeviceInfo> found;
  for (int t = 0; t < ms; t += 100) {
    found.clear();
    d.EnumerateDevices(found);
    if (found.size() == want) {
      break;
    }
    Sleep(100);
  }
  return found;
}

// Polls the state until `ok` holds (a rescan or an SDL update away).
template <typename Ok>
bool Until(WheelInputDriver& d, rex::input::DeviceId id, Ok ok, int ms = 3000) {
  for (int t = 0; t < ms; t += 50) {
    std::vector<rex::input::DeviceInfo> found;
    d.EnumerateDevices(found);
    if (ok(State(d, id))) {
      return true;
    }
    Sleep(50);
  }
  return false;
}

// Polls for `ms`: rescans happen, pedals at rest arm.
void Poll(WheelInputDriver& d, rex::input::DeviceId id, int ms) {
  for (int t = 0; t < ms; t += 50) {
    std::vector<rex::input::DeviceInfo> found;
    d.EnumerateDevices(found);
    State(d, id);
    Sleep(50);
  }
}

}  // namespace

int main() {
  // Only the virtual devices; SDL finds a plugged-in device by watching /dev/input.
  setenv("SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT", "0x044f/0xb669,0x044f/0xb371,0x044f/0xb660", 1);
  setenv("SDL_JOYSTICK_DISABLE_UDEV", "1", 1);
  Device tx, tlcm, th8;
  // The TX: steering X, brake Y, throttle Rz, clutch on the slider, named as Windows names it.
  Check(tx.Create("44F B669", 0x044F, 0xB669,
                  {{ABS_X, -32768, 32767, 0}, {ABS_Y, 0, 65535, 0}, {ABS_RZ, 0, 65535, 0},
                   {ABS_THROTTLE, 0, 65535, 0}},
                  13),
        "create the TX (needs /dev/uinput)");
  // T-LCM: X throttle, Y clutch, Z brake, all resting at the minimum. (Linux SDL lists a device
  // with X and Y axes only with a button; the real pedals have none.)
  Check(tlcm.Create("44F B371", 0x044F, 0xB371,
                    {{ABS_X, 0, 65535, 0}, {ABS_Y, 0, 65535, 0}, {ABS_Z, 0, 65535, 0}}, 2),
        "create the T-LCM");
  Check(th8.Create("Thustmaster T500 RS Gear Shift", 0x044F, 0xB660,
                   {{ABS_X, -32768, 32767, 0}, {ABS_Y, -32768, 32767, 0}}, 10),
        "create the TH8");
  Sleep(500);

  struct Case {
    const char* what;
    const char* pedal;    // wheel_pedal_device
    const char* shifter;  // wheel_shifter_device
    bool pedals_read;     // the T-LCM drives the triggers
  };
  const Case cases[] = {
      // The names the launcher saved before: the TX and T-LCM have none in the game.
      {"the launcher's names (the T-LCM has none in the game)", "T-LCM Pedals",
       "Thustmaster T500 RS Gear Shift", true},
      // What the launcher saves now.
      {"vid:pid", "044f:b371", "044f:b660", true},
      // A name that is nobody's while a nameless pedal set of a known model is there: it is taken.
      {"another name", "Heusinkveld Sprint", "none", true},
      {"none reads the wheel's own", "none", "none", false},
  };
  for (const Case& c : cases) {
    std::printf("%s:\n", c.what);
    SetCvar("wheel_device", "Thrustmaster TX Racing Wheel");
    SetCvar("wheel_pedal_device", c.pedal);
    SetCvar("wheel_shifter_device", c.shifter);
    WheelInputDriver driver(nullptr, 0);
    Check(driver.Setup() == X_STATUS_SUCCESS, "driver setup");
    auto found = Wheels(driver, 1);
    Check(found.size() == 1, "one wheel: the TX; the pedals and the shifter are not wheels");
    const auto id = found[0].id;
    Poll(driver, id, 800);
    tlcm.SetAxis(ABS_X, 65535);  // throttle
    const bool throttle =
        Until(driver, id, [](const auto& g) { return g.right_trigger == 255; }, c.pedals_read ? 3000 : 700);
    Check(throttle == c.pedals_read, c.pedals_read ? "the T-LCM's throttle drives the car"
                                                   : "the T-LCM is not read");
    tlcm.SetAxis(ABS_X, 0);
    if (c.pedals_read) {
      tlcm.SetAxis(ABS_Z, 32768);  // brake, half way
      Check(Until(driver, id, [](const auto& g) { return g.left_trigger > 120 && g.left_trigger < 135; }),
            "the T-LCM's brake");
      tlcm.SetAxis(ABS_Z, 0);
    }
    std::printf("  %s\n", c.pedals_read ? "T-LCM read with the TX" : "T-LCM left alone");
  }
  // A device that is not there: the log lists the joysticks SDL does see.
  {
    std::printf("a pedal device that is not connected (the log lists every joystick):\n");
    tlcm.Destroy();
    Sleep(300);
    SetCvar("wheel_pedal_device", "T-LCM Pedals");
    SetCvar("wheel_shifter_device", "none");
    WheelInputDriver driver(nullptr, 0);
    Check(driver.Setup() == X_STATUS_SUCCESS, "driver setup");
    auto found = Wheels(driver, 1);
    Check(found.size() == 1, "still the TX");
    Poll(driver, found[0].id, 800);
  }
  std::puts("wheel nameless probe: all checks passed");
  return 0;
}
