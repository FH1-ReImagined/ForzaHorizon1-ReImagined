// live_identity.txt is checked after writing (src/kernel/identity_file.h).
#include "kernel/identity_file.h"

#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <random>
#include <string>

#ifndef _WIN32
#include <unistd.h>
#endif

namespace {
int checks = 0;
void Check(bool condition, const char* description) {
  ++checks;
  if (!condition) {
    std::cerr << "FAILED: " << description << '\n';
    std::exit(1);
  }
}
}  // namespace

int main() {
  namespace fs = std::filesystem;
  using forza::identity_file::WarningText;
  using forza::identity_file::WriteChecked;
  const fs::path root =
      fs::temp_directory_path() / ("forza-identity-test-" + std::to_string(std::random_device{}()));
  fs::create_directories(root);
  const std::string text = "xuid=0009000000000001\ngamertag=Driver1234\n";
  std::string why;

  const fs::path good = root / "forza" / "4D5309C9" / "live_identity.txt";
  Check(WriteChecked(good, text, why), "Writes into a new folder");
  Check(why.empty(), "No reason on success");
  Check(WriteChecked(good, text + "friends_key=00\n", why), "Overwrites an existing file");

  const fs::path blocker = root / "not-a-folder";
  WriteChecked(blocker, "x", why);
  why.clear();
  Check(!WriteChecked(blocker / "live_identity.txt", text, why), "Fails when the folder is a file");
  Check(!why.empty(), "Says why");

#ifndef _WIN32
  if (geteuid() != 0) {  // root writes anyway
    const fs::path locked = root / "locked";
    fs::create_directories(locked);
    fs::permissions(locked, fs::perms::owner_read | fs::perms::owner_exec);
    why.clear();
    Check(!WriteChecked(locked / "live_identity.txt", text, why),
          "Fails in a read-only folder (as with Controlled folder access)");
    Check(why.find("opened for writing") != std::string::npos, "Says the file could not be opened");
    fs::permissions(locked, fs::perms::owner_all);
  }
#endif

  const std::string warning = WarningText("C:\\Users\\a\\Documents\\forza\\live_identity.txt", "x");
  Check(warning.find("Documents\\forza\\live_identity.txt") != std::string::npos,
        "The warning names the path");
  Check(warning.find("Controlled folder access") != std::string::npos &&
            warning.find("OneDrive") != std::string::npos &&
            warning.find("read-only") != std::string::npos,
        "The warning names the usual causes");

  fs::remove_all(root);
  std::cout << "Identity file: " << checks << " checks passed\n";
}
