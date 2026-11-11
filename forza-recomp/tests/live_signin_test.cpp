// Sign-in to the Forza Live server (src/kernel/live_signin.h): LIST only once welcomed, error 1
// as a request to sign in again, per-code log limits, refusal texts.
#include "kernel/live_signin.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace {
int checks = 0;
void Check(bool condition, const char* description) {
  ++checks;
  if (!condition) {
    std::cerr << "FAILED: " << description << '\n';
    std::exit(1);
  }
}
bool Has(const std::string& text, const char* part) { return text.find(part) != std::string::npos; }
}  // namespace

int main() {
  using namespace std::chrono;
  using namespace forza::live_signin;
  const Clock::time_point t(seconds(100));

  // The startup race: the first pass ran before any WELCOME.
  Check(!MaySendSignedIn(0, false, {}, t), "No LIST before the first WELCOME");
  Check(!MaySendSignedIn(7, false, {}, t), "An id without a received WELCOME is not signed in");
  Check(MaySendSignedIn(7, false, t, t), "LIST right after a WELCOME");
  Check(MaySendSignedIn(7, false, t, t + seconds(12)), "Two lost keep-alive WELCOMEs still pass");
  Check(!MaySendSignedIn(7, false, t, t + seconds(13)),
        "Long silence: the server may have dropped us (it does after 30 s)");
  Check(!MaySendSignedIn(7, true, t, t), "Held after a pause, a sleep or an error 1");
  Check(!MaySendSignedIn(7, false, t, t - seconds(1)), "A WELCOME from the future is not fresh");

  Resign r;
  Check(!r.ShouldWarn(t), "No warning without an error 1");
  r.Asked(t);
  Check(r.Pending(), "Error 1 is pending until a WELCOME");
  Check(!r.ShouldWarn(t + seconds(4)), "No warning within the grace");
  r.Asked(t + seconds(4));  // a second error 1 does not move the start
  Check(r.ShouldWarn(t + seconds(5)), "Warns when no WELCOME follows the error 1");
  Check(!r.ShouldWarn(t + seconds(6)), "Warns once");
  r.Welcomed();
  Check(!r.Pending() && !r.ShouldWarn(t + seconds(60)), "A WELCOME clears it");
  r.Asked(t + seconds(60));
  r.Welcomed();
  Check(!r.ShouldWarn(t + seconds(70)), "Answered at once: no warning (the usual case)");
  r.Asked(t + seconds(80));
  Check(r.ShouldWarn(t + seconds(86)), "A later error 1 can warn again");

  ErrorLog log;
  bool last = false;
  int ones = 0;
  for (int i = 0; i < 100; ++i) {
    if (log.Take(1, last)) ++ones;
  }
  Check(ones == kLinesPerCode, "Error 1 lines are limited");
  Check(log.Take(4, last) && !last, "A flood of error 1 does not hide a ban (error 4)");
  for (int i = 1; i < kLinesPerCode - 1; ++i) log.Take(4, last);
  Check(log.Take(4, last) && last, "The last logged line of a code is marked");
  Check(!log.Take(4, last), "Then that code is quiet");
  Check(log.Take(3, last), "Other codes keep their own lines");

  Check(!IsRefusal(1) && IsRefusal(2) && IsRefusal(3) && IsRefusal(4) && IsRefusal(5) &&
            !IsRefusal(6),
        "Refusals are codes 2-5");
  const std::string one = ErrorText(1, "srv:21200", "not registered");
  Check(Has(one, "sign in again") && !Has(one, "refused"), "Error 1 reads as a sign-in request");
  Check(Has(ErrorText(2, "srv:21200", "x"), "version"), "Error 2 names the protocol version");
  Check(Has(ErrorText(3, "srv:21200", "server full"), "full"), "Error 3 says full");
  Check(Has(ErrorText(4, "srv:21200", "banned from this server"), "banned"),
        "Error 4 says kicked or banned");
  Check(Has(ErrorText(4, "srv:21200", "x"), "srv:21200"), "The server is named");
  Check(Has(ErrorText(5, "srv:21200", "x"), "event-unlock"), "Error 5 names event unlocks");
  Check(Has(ErrorText(9, "srv:21200", "odd"), "error 9: odd"), "Unknown codes keep their text");
  Check(NoticeText(1).empty() && NoticeText(6).empty(), "Only refusals go on screen");
  for (uint8_t c = 2; c <= 5; ++c) {
    Check(Has(NoticeText(c), "Online > Servers"), "Each refusal notice says where to pick a server");
  }
  std::cout << "Live sign-in: " << checks << " checks passed\n";
}
