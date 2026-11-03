#include <algorithm>
#include <array>
#include <bit>
#include <cfenv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <immintrin.h>
#include <limits>
#include <random>

extern "C" double reference_fma64(double, double, double);
extern "C" double guest_fma64(double, double, double);
extern "C" float reference_fma32(float, float, float);
extern "C" float guest_fma32(float, float, float);
extern "C" double reference_separate64(double, double, double);
extern "C" double guest_separate64(double, double, double);
extern "C" float reference_separate32(float, float, float);
extern "C" float guest_separate32(float, float, float);
extern "C" double reference_bench(std::size_t);
extern "C" double guest_bench(std::size_t);
extern "C" double reference_bench4(std::size_t);
extern "C" double guest_bench4(std::size_t);

// The baseline CPU guard must run before this default-priority generated-code
// initializer as well as before main. Inspect/fault-inject with gdb if needed.
volatile double startup_result = guest_fma64(1.0, 2.0, 3.0);

template <typename T> void Same(T a, T b) {
  // NaN payload selection isn't specified by std::fma. Everything else,
  // including the sign of zero, must match bit for bit.
  if (std::isnan(a) && std::isnan(b)) return;
  if (std::bit_cast<std::array<unsigned char, sizeof(T)>>(a) !=
      std::bit_cast<std::array<unsigned char, sizeof(T)>>(b)) {
    std::fprintf(stderr, "Math mismatch: %a vs %a\n", double(a), double(b));
    std::exit(1);
  }
}

template <typename T, typename U> void Check(auto reference, auto guest, auto separate_ref,
                                           auto separate_guest, std::mt19937_64& rng) {
  using L = std::numeric_limits<T>;
  const std::array<T, 13> edge{T(0), -T(0), T(1), T(-1), L::min(), -L::min(),
      L::max(), -L::max(), L::denorm_min(), -L::denorm_min(),
      L::infinity(), -L::infinity(), L::quiet_NaN()};
  for (auto a : edge) for (auto b : edge) for (auto c : edge) {
    Same(reference(a, b, c), guest(a, b, c));
    Same(separate_ref(a, b, c), separate_guest(a, b, c));
  }
  for (int i = 0; i < 65536; ++i) {
    T a = std::bit_cast<T>(static_cast<U>(rng()));
    T b = std::bit_cast<T>(static_cast<U>(rng()));
    T c = std::bit_cast<T>(static_cast<U>(rng()));
    Same(reference(a, b, c), guest(a, b, c));
    Same(separate_ref(a, b, c), separate_guest(a, b, c));
  }
}

int main() {
  Same(double(startup_result), 5.0);
  const unsigned original_mxcsr = _mm_getcsr();
  const int original_rounding = std::fegetround();
  std::mt19937_64 rng(0xBC250);
  for (int rounding : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
    std::fesetround(rounding);
    for (unsigned flush : {0u, 0x40u, 0x8000u, 0x8040u}) {
      _mm_setcsr((_mm_getcsr() & ~0x8040u) | flush);
      Check<double, uint64_t>(reference_fma64, guest_fma64, reference_separate64,
                              guest_separate64, rng);
      Check<float, uint32_t>(reference_fma32, guest_fma32, reference_separate32,
                             guest_separate32, rng);
    }
  }
  std::fesetround(original_rounding);
  _mm_setcsr(original_mxcsr);
  std::puts("Math matches: float/double, 4 rounding modes, all FTZ/DAZ combinations; NaN payloads excluded.");

  auto benchmark = [](const char* label, auto reference, auto guest) {
    constexpr std::size_t iterations = 10000000;
    std::array<double, 7> baseline{}, optimized{};
    const double expected = reference(iterations);
    Same(guest(iterations), expected);
    auto measure = [&](auto fn) {
      auto start = std::chrono::steady_clock::now();
      double result = fn(iterations);
      auto elapsed = std::chrono::steady_clock::now() - start;
      Same(result, expected);
      return std::chrono::duration<double, std::milli>(elapsed).count();
    };
    for (std::size_t i = 0; i < baseline.size(); ++i) {
      // Alternate order to reduce warm-up/temperature bias.
      if (i % 2) {
        optimized[i] = measure(guest);
        baseline[i] = measure(reference);
      } else {
        baseline[i] = measure(reference);
        optimized[i] = measure(guest);
      }
    }
    std::sort(baseline.begin(), baseline.end());
    std::sort(optimized.begin(), optimized.end());
    std::printf("%s FMA microbenchmark, 7 runs, median: baseline %.2f ms; guest %.2f ms\n",
                label, baseline[3], optimized[3]);
  };
  benchmark("Serial", reference_bench, guest_bench);
  benchmark("4 accumulators", reference_bench4, guest_bench4);
}
