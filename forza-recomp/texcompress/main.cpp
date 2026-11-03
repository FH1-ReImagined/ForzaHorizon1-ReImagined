// forza-texcompress (#413): compresses texture pack pictures to BC1/BC7 DDS files with mips, for
// the launcher's Make HD pack and Add pack (launcher/forza_launcher/texture_compress.py).
//
//   forza-texcompress [--threads N] [--low-priority] JOBS
//     JOBS is a UTF-8 text file with a job per line, tab-separated:
//       <bc1|bc7|auto> <width> <height> <input.rgba> <output.dds>
//     input.rgba holds width * height RGBA8 texels, rows packed. auto: BC1 for an opaque texture
//     BC1 keeps well, else BC7. Each output is written to <output>.tmp and renamed. A line per job
//     as it finishes, on stdout:
//       ok<TAB><output><TAB><BC1|BC7><TAB><levels><TAB><RGB PSNR><TAB><alpha flips>
//       fail<TAB><output><TAB><why>
//     Exit status 0 when every job worked, 1 when one failed, 2 when the jobs could not be read.
//   forza-texcompress --decode <input.dds> <output.rgba>
//     The largest level decoded to RGBA8; prints <width><TAB><height><TAB><format><TAB><levels>.
//   forza-texcompress --version
#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#include "texcompress.h"

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <sys/resource.h>
#endif

namespace fs = std::filesystem;

namespace {

// Bumped when the job format or the output changes; the launcher checks it.
constexpr int kVersion = 1;

fs::path Utf8Path(const std::string& text) {
  return fs::path(std::u8string(reinterpret_cast<const char8_t*>(text.data()), text.size()));
}

std::string PathText(const fs::path& path) {
  const std::u8string text = path.u8string();
  return std::string(reinterpret_cast<const char*>(text.data()), text.size());
}

bool ReadFile(const fs::path& path, std::vector<uint8_t>& out, std::string& error) {
  std::ifstream file(path, std::ios::binary);
  if (!file) {
    error = "cannot open " + PathText(path);
    return false;
  }
  file.seekg(0, std::ios::end);
  const std::streamoff size = file.tellg();
  file.seekg(0, std::ios::beg);
  if (size < 0) {
    error = "cannot read " + PathText(path);
    return false;
  }
  out.resize(size_t(size));
  if (size && !file.read(reinterpret_cast<char*>(out.data()), size)) {
    error = "cannot read " + PathText(path);
    return false;
  }
  return true;
}

// Through <path>.tmp, renamed over the target, so a stopped run never leaves half a file.
bool WriteFile(const fs::path& path, const std::vector<uint8_t>& data, std::string& error) {
  fs::path temp = path;
  temp += ".tmp";
  bool written = false;
  {
    std::ofstream file(temp, std::ios::binary | std::ios::trunc);
    if (file && file.write(reinterpret_cast<const char*>(data.data()), std::streamsize(data.size()))) {
      file.flush();
      if (file) {
        file.close();
        written = !file.fail();
      }
    }
  }
  // Buffered writes can fail only at flush/close. Never publish one as a successful job:
  // the launcher removes its source PNG after success. Close before cleanup on Windows.
  if (!written) {
    error = "cannot write " + PathText(temp);
    std::error_code ignored;
    fs::remove(temp, ignored);
    return false;
  }
  std::error_code ec;
  fs::rename(temp, path, ec);
  if (ec) {
    error = "cannot rename " + PathText(temp) + ": " + ec.message();
    fs::remove(temp, ec);
    return false;
  }
  return true;
}

struct Job {
  texcompress::Choice choice = texcompress::Choice::kAuto;
  uint32_t width = 0, height = 0;
  fs::path input, output;
  std::string output_text;
};

std::vector<std::string> Split(const std::string& line, char separator) {
  std::vector<std::string> out;
  size_t start = 0;
  while (true) {
    const size_t end = line.find(separator, start);
    out.push_back(line.substr(start, end == std::string::npos ? std::string::npos : end - start));
    if (end == std::string::npos) return out;
    start = end + 1;
  }
}

bool ParseJobs(const fs::path& path, std::vector<Job>& jobs, std::string& error) {
  std::vector<uint8_t> data;
  if (!ReadFile(path, data, error)) return false;
  std::string text(data.begin(), data.end());
  size_t number = 0;
  for (std::string line : Split(text, '\n')) {
    ++number;
    if (!line.empty() && line.back() == '\r') line.pop_back();
    if (line.empty()) continue;
    const std::vector<std::string> fields = Split(line, '\t');
    Job job;
    if (fields.size() != 5) {
      error = "line " + std::to_string(number) + ": not 5 fields";
      return false;
    }
    if (fields[0] == "bc1") {
      job.choice = texcompress::Choice::kBC1;
    } else if (fields[0] == "bc7") {
      job.choice = texcompress::Choice::kBC7;
    } else if (fields[0] == "auto") {
      job.choice = texcompress::Choice::kAuto;
    } else {
      error = "line " + std::to_string(number) + ": unknown format " + fields[0];
      return false;
    }
    char* end = nullptr;
    const unsigned long width = std::strtoul(fields[1].c_str(), &end, 10);
    const bool width_ok = end && !*end;
    const unsigned long height = std::strtoul(fields[2].c_str(), &end, 10);
    if (!width_ok || !end || *end || !width || !height || width > 16384 || height > 16384) {
      error = "line " + std::to_string(number) + ": bad size";
      return false;
    }
    job.width = uint32_t(width);
    job.height = uint32_t(height);
    job.input = Utf8Path(fields[3]);
    job.output = Utf8Path(fields[4]);
    job.output_text = fields[4];
    jobs.push_back(std::move(job));
  }
  return true;
}

void LowerPriority() {
#ifdef _WIN32
  SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS);
#else
  // Linux: the calling thread's nice value; the workers started after it inherit it.
  setpriority(PRIO_PROCESS, 0, 10);
#endif
}

int RunJobs(const fs::path& jobs_path, unsigned threads) {
  std::vector<Job> jobs;
  std::string error;
  if (!ParseJobs(jobs_path, jobs, error)) {
    std::fprintf(stderr, "forza-texcompress: %s\n", error.c_str());
    return 2;
  }
  // The largest first, so the workers finish together.
  std::stable_sort(jobs.begin(), jobs.end(), [](const Job& a, const Job& b) {
    return uint64_t(a.width) * a.height > uint64_t(b.width) * b.height;
  });
  std::atomic<size_t> next{0};
  std::atomic<bool> failed{false};
  std::mutex output_mutex;
  const texcompress::Settings settings;
  auto worker = [&] {
    for (size_t index; (index = next.fetch_add(1)) < jobs.size();) {
      const Job& job = jobs[index];
      std::string why;
      texcompress::Image image;
      image.width = job.width;
      image.height = job.height;
      texcompress::Result result;
      bool ok = ReadFile(job.input, image.rgba, why);
      if (ok && image.rgba.size() != size_t(job.width) * job.height * 4) {
        why = "the input holds " + std::to_string(image.rgba.size()) + " bytes, not " +
              std::to_string(size_t(job.width) * job.height * 4);
        ok = false;
      }
      if (ok) {
        result = texcompress::Compress(image, job.choice, settings);
        ok = WriteFile(job.output, result.dds, why);
      }
      std::lock_guard<std::mutex> lock(output_mutex);
      if (ok) {
        std::printf("ok\t%s\t%s\t%u\t%.2f\t%llu\n", job.output_text.c_str(),
                    texcompress::FormatName(result.format), result.levels, result.error.rgb_psnr,
                    static_cast<unsigned long long>(result.error.alpha_flips));
      } else {
        failed = true;
        std::printf("fail\t%s\t%s\n", job.output_text.c_str(), why.c_str());
      }
      std::fflush(stdout);
    }
  };
  threads = std::max(1u, std::min<unsigned>(threads, unsigned(jobs.size())));
  std::vector<std::thread> pool;
  for (unsigned i = 1; i < threads; ++i) pool.emplace_back(worker);
  worker();
  for (std::thread& thread : pool) thread.join();
  return failed ? 1 : 0;
}

int DecodeFile(const fs::path& input, const fs::path& output) {
  std::vector<uint8_t> data;
  std::string error;
  texcompress::DdsFile dds;
  if (!ReadFile(input, data, error) || !texcompress::ReadDds(data.data(), data.size(), dds, error)) {
    std::fprintf(stderr, "forza-texcompress: %s\n", error.c_str());
    return 1;
  }
  const texcompress::Image image = texcompress::Decode(
      dds.format, data.data() + dds.levels[0].first, dds.levels[0].second, dds.width, dds.height);
  if (!WriteFile(output, image.rgba, error)) {
    std::fprintf(stderr, "forza-texcompress: %s\n", error.c_str());
    return 1;
  }
  std::printf("%u\t%u\t%s\t%zu\n", dds.width, dds.height, texcompress::FormatName(dds.format),
              dds.levels.size());
  return 0;
}

int Usage() {
  std::fprintf(stderr,
               "usage: forza-texcompress [--threads N] [--low-priority] JOBS\n"
               "       forza-texcompress --decode INPUT.dds OUTPUT.rgba\n"
               "       forza-texcompress --version\n");
  return 2;
}

int Main(const std::vector<std::string>& args) {
  unsigned threads = std::max(1u, std::thread::hardware_concurrency());
  for (size_t i = 0; i < args.size(); ++i) {
    const std::string& arg = args[i];
    if (arg == "--version") {
      std::printf("forza-texcompress %d (bc7enc_rdo b9438627)\n", kVersion);
      return 0;
    }
    if (arg == "--decode") {
      if (i + 2 >= args.size()) return Usage();
      return DecodeFile(Utf8Path(args[i + 1]), Utf8Path(args[i + 2]));
    }
    if (arg == "--threads" && i + 1 < args.size()) {
      threads = unsigned(std::max(1L, std::strtol(args[++i].c_str(), nullptr, 10)));
    } else if (arg == "--low-priority") {
      LowerPriority();
    } else if (arg.rfind("--", 0) == 0 || i + 1 != args.size()) {
      return Usage();
    } else {
      return RunJobs(Utf8Path(arg), threads);
    }
  }
  return Usage();
}

}  // namespace

#ifdef _WIN32
// The arguments as UTF-16, turned into UTF-8 (paths with any characters).
int wmain(int argc, wchar_t** argv) {
  std::vector<std::string> args;
  for (int i = 1; i < argc; ++i) args.push_back(PathText(fs::path(argv[i])));
  return Main(args);
}
#else
int main(int argc, char** argv) { return Main(std::vector<std::string>(argv + 1, argv + argc)); }
#endif
