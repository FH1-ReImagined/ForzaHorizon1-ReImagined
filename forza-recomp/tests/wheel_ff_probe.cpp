// The racing wheel driver's game force feedback on a real SDL haptic device (SDK patch 0101): a
// Linux uinput wheel with only constant force and autocentre, like the hid-lg4ff driver of a G29,
// so the game's spring becomes the wheel's autocentre. The probe drives the actual WheelInputDriver
// through the kernel-facing calls (GameFf*) and reads back what reaches the device: the autocentre
// must follow the spring and drop to 0 when it stops, is reset or becomes another type (#219 review:
// a reset left the hardware autocentre at its last value). Then (#256) the forces fade out while
// the game says the player is not driving (ForzaWheelPlayerDriving) and come back when they are,
// and wheel_ffb_spring_strength scales the spring; quick toggles leave the spring whole, and back
// at full strength the steering force is started again (#304, patch 0130), and wheel_ffb_keep_playing
// can start it again every few seconds (#304, patch 0146). Then (#273, patch 0129)
// the game's rumble on a wheel with periodic effects (a uinput Fanatec CSR Elite): which effects
// each wheel_rumble_style sends (fine: a 40 Hz road sine and a ~71 Hz engine sine; classic: one
// 25 Hz sine, as before), and fine falling back to classic on a wheel with room for one effect.
//
// Linux only (needs write access to /dev/uinput). Build against the patched SDK source
// (tools/build_rexglue_sdk.sh --source-only) and link the patched runtime, from the repository root:
//   SDK=~/rexglue-sdk-src-forza/sdk; clang++ -std=c++23 -msse4.1 -I$SDK/include
//       -I$SDK/thirdparty/sdl3/include -I$SDK/thirdparty/fmt/include -I$SDK/thirdparty/spdlog/include
//       -I$SDK/thirdparty/simde
//       -DSPDLOG_COMPILED_LIB -DSPDLOG_FMT_EXTERNAL forza-recomp/tests/wheel_ff_probe.cpp
//       -L ~/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib -lrexruntime -pthread
//       -Wl,-rpath,$HOME/rexglue-sdk-patched-forza/0.10.0.24/linux-amd64/lib -o wheel-ff-probe
//   ./wheel-ff-probe
#include <rex/cvar.h>
#include <rex/input/wheel/wheel_ff.h>
#include <rex/input/wheel/wheel_input_driver.h>

#include <fcntl.h>
#include <linux/uinput.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

using namespace rex::input::wheel;
using rex::X_STATUS;

extern "C" void ForzaWheelPlayerDriving(int driving);  // the runtime's export (#256)

namespace {

void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

struct Sine {
  int id, period, magnitude;
};

// A uinput G29 (046d:c24f) with constant force, gain and autocentre. It accepts every effect
// upload and records the last autocentre the driver set (0-0xFFFF). With `periodic`, a Fanatec CSR
// Elite (0eb7:0011) with constant force, sine and gain instead, room for `ff_max` effects, and the
// sines uploaded and played are recorded.
struct VirtualWheel {
  int fd = -1;
  std::atomic<bool> stop{false};
  std::atomic<int> autocenter{-1};
  std::atomic<int> autocenter_writes{0};
  std::atomic<int> autocenter_between{0};  // writes strictly between 0 and full (a fade's steps)
  std::thread reader;
  std::mutex m;
  std::vector<Sine> sines;
  std::vector<std::pair<int, int>> plays;  // (effect id, 1 play / 0 stop)

  std::vector<Sine> Sines() {
    std::lock_guard lock(m);
    return sines;
  }
  int Starts() {  // play requests (EV_FF value 1) so far
    std::lock_guard lock(m);
    int n = 0;
    for (const auto& [fx, v] : plays) {
      n += v != 0;
    }
    return n;
  }
  int LastPlayed() {  // the effect of the last play or stop request
    std::lock_guard lock(m);
    return plays.empty() ? -1 : plays.back().first;
  }
  bool Playing(int id) {
    std::lock_guard lock(m);
    int state = 0;
    for (const auto& [fx, v] : plays) {
      if (fx == id) {
        state = v;
      }
    }
    return state != 0;
  }

  bool Create(bool periodic = false, int ff_max = 16) {
    fd = open("/dev/uinput", O_RDWR | O_NONBLOCK);
    if (fd < 0) {
      return false;
    }
    ioctl(fd, UI_SET_EVBIT, EV_KEY);
    for (int b = BTN_TRIGGER; b <= BTN_DEAD; b++) {
      ioctl(fd, UI_SET_KEYBIT, b);
    }
    ioctl(fd, UI_SET_EVBIT, EV_ABS);
    const struct {
      int code, min, max, value;
    } axes[] = {{ABS_X, -32768, 32767, 0}, {ABS_Y, 0, 255, 255}, {ABS_Z, 0, 255, 255},
                {ABS_RZ, 0, 255, 255}};
    for (const auto& a : axes) {
      uinput_abs_setup abs{};
      abs.code = static_cast<__u16>(a.code);
      abs.absinfo.minimum = a.min;
      abs.absinfo.maximum = a.max;
      abs.absinfo.value = a.value;
      ioctl(fd, UI_SET_ABSBIT, a.code);
      ioctl(fd, UI_ABS_SETUP, &abs);
    }
    ioctl(fd, UI_SET_EVBIT, EV_FF);
    for (int f : periodic ? std::vector<int>{FF_CONSTANT, FF_PERIODIC, FF_SINE, FF_GAIN}
                          : std::vector<int>{FF_CONSTANT, FF_GAIN, FF_AUTOCENTER}) {
      ioctl(fd, UI_SET_FFBIT, f);
    }
    uinput_setup setup{};
    setup.id = periodic ? input_id{BUS_USB, 0x0eb7, 0x0011, 0x111}
                        : input_id{BUS_USB, 0x046d, 0xc24f, 0x111};
    std::snprintf(setup.name, sizeof(setup.name), "%s",
                  periodic ? "FANATEC CSR Elite Wheel" : "Logitech G29 Driving Force Racing Wheel");
    setup.ff_effects_max = static_cast<__u32>(ff_max);
    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
      return false;
    }
    reader = std::thread([this] { Read(); });
    return true;
  }

  void Read() {
    while (!stop) {
      pollfd p{fd, POLLIN, 0};
      if (poll(&p, 1, 20) <= 0) {
        continue;
      }
      input_event ev{};
      while (read(fd, &ev, sizeof(ev)) == static_cast<ssize_t>(sizeof(ev))) {
        if (ev.type == EV_UINPUT && ev.code == UI_FF_UPLOAD) {
          uinput_ff_upload up{};
          up.request_id = static_cast<__u32>(ev.value);
          ioctl(fd, UI_BEGIN_FF_UPLOAD, &up);
          if (up.effect.type == FF_PERIODIC) {
            std::lock_guard lock(m);
            sines.push_back({up.effect.id, up.effect.u.periodic.period, up.effect.u.periodic.magnitude});
          }
          up.retval = 0;
          ioctl(fd, UI_END_FF_UPLOAD, &up);
        } else if (ev.type == EV_UINPUT && ev.code == UI_FF_ERASE) {
          uinput_ff_erase er{};
          er.request_id = static_cast<__u32>(ev.value);
          ioctl(fd, UI_BEGIN_FF_ERASE, &er);
          er.retval = 0;
          ioctl(fd, UI_END_FF_ERASE, &er);
        } else if (ev.type == EV_FF && ev.code < FF_GAIN) {
          std::lock_guard lock(m);
          plays.push_back({ev.code, ev.value});
        } else if (ev.type == EV_FF && ev.code == FF_AUTOCENTER) {
          autocenter = ev.value;
          autocenter_writes++;
          if (ev.value > 0 && ev.value < 0xFFFF * 30 / 100) {
            autocenter_between++;
          }
        }
      }
    }
  }

  // Waits for the device to hold `percent` autocentre (SDL writes 0xFFFF * percent / 100).
  bool AutocenterIs(int percent) {
    const int want = static_cast<int>(0xFFFFUL * static_cast<unsigned>(percent) / 100);
    for (int i = 0; i < 100; i++) {
      if (autocenter == want) {
        return true;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    std::fprintf(stderr, "autocentre is %d, expected %d\n", autocenter.load(), want);
    return false;
  }

  void Destroy() {
    stop = true;
    if (reader.joinable()) {
      reader.join();
    }
    if (fd >= 0) {
      ioctl(fd, UI_DEV_DESTROY);
      close(fd);
      fd = -1;
    }
  }
  ~VirtualWheel() { Destroy(); }
};

// FH1's spring (sub_82CB76B8 with ControllerFFB.ini): block 4, coefficients 63, saturations 127.
FfEffect Spring(uint32_t type = kFfSpring) {
  FfEffect e;
  e.block = 4;
  e.type = type;
  e.gain = 255;
  e.pos_coeff = e.neg_coeff = 63;
  e.pos_sat = e.neg_sat = 127;
  return e;
}

// The game polls the pad every frame; the driver steps the fade there.
void Poll(WheelInputDriver& driver, rex::input::DeviceId id, int ms) {
  for (int t = 0; t < ms; t += 10) {
    rex::input::X_INPUT_STATE state{};
    driver.GetDeviceState(id, &state);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
  }
}

void SetCvar(const char* name, const char* value) {
  for (rex::cvar::FlagEntry& entry : rex::cvar::GetRegistry()) {
    if (entry.name == name) {
      entry.setter(value);
      return;
    }
  }
  Check(false, "cvar not found");
}

// #273: the game's rumble (left motor full, right motor half) on a wheel with periodic effects,
// in each wheel_rumble_style, and fine on a wheel with room for one effect. Prints what is sent.
void PrintSines(const char* style, const std::vector<Sine>& sines) {
  for (const auto& u : sines) {
    std::printf("  %-8s sine effect %d: period %d ms (%d Hz), magnitude %d\n", style, u.id, u.period,
                u.period ? 1000 / u.period : 0, u.magnitude);
  }
}

void RumbleStyles() {
  ForzaWheelPlayerDriving(1);
  std::printf("the game's rumble (left 65535, right 32768; strength 60, road 80, engine 50):\n");
  for (const char* style : {"fine", "classic", "one slot"}) {
    const bool one_slot = std::string(style) == "one slot";
    SetCvar("wheel_rumble_style", one_slot ? "fine" : style);
    VirtualWheel wheel;
    Check(wheel.Create(true, one_slot ? 1 : 16), "create the uinput CSR Elite");
    std::this_thread::sleep_for(std::chrono::milliseconds(500));  // udev
    {
      WheelInputDriver driver(nullptr, 0);
      Check(driver.Setup() == X_STATUS_SUCCESS, "driver setup (rumble)");
      std::vector<rex::input::DeviceInfo> found;
      for (int i = 0; i < 50 && found.empty(); i++) {
        driver.EnumerateDevices(found);
        if (found.empty()) {
          std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
      }
      Check(found.size() == 1, "the driver opens the CSR Elite");
      Check(GameFfSetRumble(65535, 32768), "the game's rumble");
      std::this_thread::sleep_for(std::chrono::milliseconds(200));
      const auto sines = wheel.Sines();
      PrintSines(style, sines);
      Check(!sines.empty(), "a vibration is sent");
      if (std::string(style) == "fine") {
        // Created at 40 Hz and ~71 Hz, then set (the default strengths): road 1 * 0.8 * 0.6 * 0.25,
        // engine 0.5 * 0.5 * 0.6 * 0.25.
        bool road = false, engine = false;
        for (const auto& u : sines) {
          road |= u.period == 25 && std::abs(u.magnitude - 3932) <= 1 && wheel.Playing(u.id);
          engine |= u.period == 14 && std::abs(u.magnitude - 1228) <= 1 && wheel.Playing(u.id);
        }
        Check(road && engine, "fine: a 40 Hz road sine and a 71 Hz engine sine play at their levels");
      } else {
        // Classic, and fine without room for its two effects: one 25 Hz sine at 0.6 * 0.6 of the
        // stronger motor by its strength (road: 0.8), as before #273.
        bool ok = true;
        for (const auto& u : sines) {
          ok &= u.period == 40 || (one_slot && u.period == 25);
        }
        Check(ok && sines.back().period == 40 && std::abs(sines.back().magnitude - 9437) <= 1 &&
                  wheel.Playing(sines.back().id),
              "classic: one 25 Hz sine plays");
      }
      // The vibration is updated about 30 times a second (#467, ReIGlue 0403): the game's state
      // polls, several a frame, send what comes due after its last rumble.
      Check(GameFfSetRumble(0, 0), "rumble off");
      Poll(driver, found[0].id, 150);
      for (const auto& u : wheel.Sines()) {
        Check(!wheel.Playing(u.id), "no rumble: every sine stops");
      }
    }
  }
}

// #219 and #256: the game's spring on a wheel without a spring effect, and the fade.
void SpringAndFade() {
  // Only the virtual devices: SDL3 leaves out every other joystick with this hint.
  setenv("SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT", "0x046d/0xc24f,0x0eb7/0x0011", 1);
  // The rumble wheels below are plugged in later: SDL finds them by watching /dev/input, also where
  // no udev monitor reaches the process (a sandbox, a container).
  setenv("SDL_JOYSTICK_DISABLE_UDEV", "1", 1);
  VirtualWheel dev;
  Check(dev.Create(), "create the uinput wheel (needs /dev/uinput)");
  std::this_thread::sleep_for(std::chrono::milliseconds(500));  // udev
  WheelInputDriver driver(nullptr, 0);
  Check(driver.Setup() == X_STATUS_SUCCESS, "driver setup");
  std::vector<rex::input::DeviceInfo> found;
  for (int i = 0; i < 50 && found.empty(); i++) {
    driver.EnumerateDevices(found);
    if (found.empty()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
  }
  Check(found.size() == 1, "the driver opens the virtual wheel");
  Check(dev.AutocenterIs(0), "the wheel's own autocentre is off at first");

  // Default strength 60: the spring pulls with 63/127 * 0.6, autocentre 30.
  const int spring = 30;
  Check(GameFfDeviceInfo(), "device info");
  Check(GameFfControl(kFfStopAll), "stop all before the effects");
  Check(GameFfSetEffect(Spring()), "define the spring");
  Check(dev.AutocenterIs(0), "a defined spring waits for its start");
  Check(GameFfOperate(4, 2), "start the spring");
  Check(dev.AutocenterIs(spring), "running spring: autocentre");
  Check(GameFfControl(kFfStopAll), "stop all");
  Check(dev.AutocenterIs(0), "stop all: autocentre 0");
  Check(GameFfOperate(4, 2), "restart the spring");
  Check(dev.AutocenterIs(spring), "restarted spring: autocentre");
  Check(GameFfControl(kFfReset), "reset");
  Check(dev.AutocenterIs(0), "reset: autocentre 0");
  Check(!GameFfOperate(4, 2), "a reset block cannot start");
  Check(GameFfSetEffect(Spring()) && GameFfOperate(4, 2), "define and start again");
  Check(dev.AutocenterIs(spring), "spring again: autocentre");
  Check(GameFfSetEffect(Spring(kFfDamper)), "redefine the block as a damper");
  Check(dev.AutocenterIs(0), "the block is no spring any more: autocentre 0");
  Check(GameFfSetEffect(Spring()), "back to a spring (still started)");
  Check(dev.AutocenterIs(spring), "spring once more: autocentre");
  FfEffect quiet = Spring();
  quiet.gain = 0;
  Check(GameFfUpdateEffect(quiet, 0x100), "gain 0 (a car at a standstill)");
  Check(dev.AutocenterIs(0), "gain 0: autocentre 0");

  // #256: not driving (the pause menu, a rewind, a replay): the forces fade out, then back in.
  const rex::input::DeviceId id = found[0].id;
  Check(GameFfUpdateEffect(Spring(), 0x100), "the spring at full gain again");
  Check(dev.AutocenterIs(spring), "full gain: autocentre");
  Poll(driver, id, 100);
  Check(dev.AutocenterIs(spring), "the game never said: no fade");
  ForzaWheelPlayerDriving(0);
  Poll(driver, id, 100);
  Check(dev.autocenter > 0 && dev.autocenter < 0xFFFF * spring / 100, "fading out");
  Poll(driver, id, 300);
  Check(dev.AutocenterIs(0), "not driving: the spring has faded out");
  Check(GameFfUpdateEffect(Spring(), 0x100), "the game updates its spring while not driving");
  Poll(driver, id, 50);
  Check(dev.AutocenterIs(0), "an update while not driving stays faded out");
  const int between = dev.autocenter_between;
  ForzaWheelPlayerDriving(1);
  Poll(driver, id, 700);
  Check(dev.AutocenterIs(spring), "driving again: the spring is back");
  Check(dev.autocenter_between - between >= 3, "it came back in steps");
  // #304: quick toggles (the old physics gate flipped on every hitch: out, then back 0.5 s later)
  // leave nothing behind: once driving lasts, the spring is back whole.
  for (int i = 0; i < 12; i++) {
    ForzaWheelPlayerDriving(i % 2);
    Poll(driver, id, 20 + 30 * (i % 3));
  }
  ForzaWheelPlayerDriving(1);
  Poll(driver, id, 700);
  Check(dev.AutocenterIs(spring), "quick toggles: the spring is back whole");
  // #304: back at full strength every effect that should play is started again, even one the
  // driver believes is running, so a wheel that dropped it meanwhile (DirectInput stops a device's
  // effects when it acquires it again) gets its steering force back.
  FfEffect steer;
  steer.block = 5;
  steer.type = kFfConstant;
  steer.gain = 255;
  steer.magnitude = 40;
  Check(GameFfSetEffect(steer) && GameFfOperate(5, 2), "the steering force plays");
  Poll(driver, id, 100);
  const int starts = dev.Starts();
  Check(starts > 0, "the steering force was started");
  ForzaWheelPlayerDriving(0);
  Poll(driver, id, 400);
  Check(dev.Starts() == starts, "fading out starts nothing");
  ForzaWheelPlayerDriving(1);
  Poll(driver, id, 700);
  Check(dev.Starts() > starts, "back at full strength: the steering force is started again");
  // #304 (patch 0146): wheel_ffb_keep_playing starts every playing effect again every N seconds,
  // for a wheel that ends effects by itself (never by default: SDL patch 0147 makes the endless
  // length endless on Windows and macOS too).
  const int playing = dev.Starts();
  Poll(driver, id, 1500);
  Check(dev.Starts() == playing, "by default: no restarts");
  SetCvar("wheel_ffb_keep_playing", "1");
  Poll(driver, id, 2600);
  const int kept = dev.Starts() - playing;
  Check(kept >= 2 && kept <= 3, "keep playing every second: the steering force restarted");
  Check(dev.Playing(dev.LastPlayed()), "still playing after the restarts");
  SetCvar("wheel_ffb_keep_playing", "0");
  Poll(driver, id, 1500);
  Check(dev.Starts() - playing == kept, "0: no restarts");
  std::printf("wheel ff probe: the steering force kept playing (%d restarts in 2.6 s)\n", kept);
  Check(GameFfOperate(5, kFfOpStop), "stop the steering force");
  // wheel_ffb_spring_strength 50: half the centring (autocentre 15).
  SetCvar("wheel_ffb_spring_strength", "50");
  Check(GameFfUpdateEffect(Spring(), 0x100), "the spring again");
  Check(dev.AutocenterIs(spring / 2), "spring strength 50: half the autocentre");
  std::printf("wheel ff probe: autocentre follows the spring (%d writes), fades while not driving, "
              "spring strength\n",
              dev.autocenter_writes.load());
}

}  // namespace

int main() {
  SpringAndFade();  // its wheel and driver are gone after it: the rumble wheels are the only ones
  RumbleStyles();
  std::printf("wheel ff probe: all checks passed\n");
  return 0;
}
