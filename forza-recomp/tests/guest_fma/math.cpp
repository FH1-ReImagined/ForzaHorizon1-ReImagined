#include <cmath>
#include <cstddef>

#ifdef REFERENCE_MATH
#define NAME(n) reference_##n
#else
#define NAME(n) guest_##n
#endif

extern "C" double NAME(fma64)(double a, double b, double c) { return std::fma(a, b, c); }
extern "C" float NAME(fma32)(float a, float b, float c) { return std::fma(a, b, c); }
extern "C" double NAME(separate64)(double a, double b, double c) { return a * b + c; }
extern "C" float NAME(separate32)(float a, float b, float c) { return a * b + c; }

// Serial recurrence exposes call overhead; this is not a game/FPS benchmark.
extern "C" double NAME(bench)(std::size_t iterations) {
  double value = 0.25;
  for (std::size_t i = 0; i < iterations; ++i)
    value = std::fma(value, 0.9999999997, 0.0000000001);
  return value;
}

// Several independent DSP accumulators can overlap only when calls don't spill
// their live values across the math library's ABI boundary.
extern "C" double NAME(bench4)(std::size_t iterations) {
  double a = 0.25, b = 0.5, c = 0.75, d = 1.0;
  for (std::size_t i = 0; i < iterations; ++i) {
    a = std::fma(a, 0.9999999997, 0.0000000001);
    b = std::fma(b, 0.9999999997, 0.0000000001);
    c = std::fma(c, 0.9999999997, 0.0000000001);
    d = std::fma(d, 0.9999999997, 0.0000000001);
  }
  return a + b + c + d;
}
