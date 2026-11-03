// The GPU plugin's inline rex::bit_scan_forward, tzcnt and lzcnt (SDK patch 0361, #45:
// include/rex/math.h with REX_INLINE_BIT_SCANS) against the runtime library's exported
// functions: every 32-bit value, every 8- and 16-bit value, the 64-bit edge cases and 2^28
// random 64-bit values (about 25 s). Build against the patched SDK source and an overlay's
// runtime:
//   c++ -std=c++20 -O2 -I ~/rexglue-sdk-src-forza/sdk/include tools/test_inline_bit_scans.cpp \
//       -L ~/rexglue-sdk-patched-forza/lib -lrexruntime \
//       -Wl,-rpath,$HOME/rexglue-sdk-patched-forza/lib -o test_inline_bit_scans
// (Linux: the reference functions are bound by their mangled names.)
#define REX_INLINE_BIT_SCANS 1
#include <rex/math.h>
#include <cstdio>
#include <cstdlib>
#include <random>
namespace ref {
bool bsf32(uint32_t v, uint32_t* o) __asm__("_ZN3rex16bit_scan_forwardEjPj");
bool bsf64(uint64_t v, uint32_t* o) __asm__("_ZN3rex16bit_scan_forwardEmPj");
uint8_t tz8(uint8_t) __asm__("_ZN3rex5tzcntEh");
uint8_t tz16(uint16_t) __asm__("_ZN3rex5tzcntEt");
uint8_t tz32(uint32_t) __asm__("_ZN3rex5tzcntEj");
uint8_t tz64(uint64_t) __asm__("_ZN3rex5tzcntEm");
uint8_t lz8(uint8_t) __asm__("_ZN3rex5lzcntEh");
uint8_t lz16(uint16_t) __asm__("_ZN3rex5lzcntEt");
uint8_t lz32(uint32_t) __asm__("_ZN3rex5lzcntEj");
uint8_t lz64(uint64_t) __asm__("_ZN3rex5lzcntEm");
}
static void fail(const char* what, unsigned long long v) {
  std::fprintf(stderr, "MISMATCH %s at %llx\n", what, v);
  std::exit(1);
}
static void check64(uint64_t v) {
  uint32_t a = 7, b = 7;
  bool ra = rex::fh1_inline_bit_scans::bit_scan_forward(v, &a), rb = ref::bsf64(v, &b);
  if (ra != rb || a != b) fail("bsf64", v);
  if (rex::fh1_inline_bit_scans::tzcnt(v) != ref::tz64(v)) fail("tz64", v);
  if (rex::fh1_inline_bit_scans::lzcnt(v) != ref::lz64(v)) fail("lz64", v);
}
int main() {
  for (uint32_t v = 0; v < 256; ++v) {
    if (rex::fh1_inline_bit_scans::tzcnt(uint8_t(v)) != ref::tz8(uint8_t(v))) fail("tz8", v);
    if (rex::fh1_inline_bit_scans::lzcnt(uint8_t(v)) != ref::lz8(uint8_t(v))) fail("lz8", v);
  }
  for (uint32_t v = 0; v < 65536; ++v) {
    if (rex::fh1_inline_bit_scans::tzcnt(uint16_t(v)) != ref::tz16(uint16_t(v))) fail("tz16", v);
    if (rex::fh1_inline_bit_scans::lzcnt(uint16_t(v)) != ref::lz16(uint16_t(v))) fail("lz16", v);
  }
  uint64_t n = 0;
  uint32_t v = 0;
  do {
    uint32_t a = 7, b = 7;
    bool ra = rex::fh1_inline_bit_scans::bit_scan_forward(v, &a), rb = ref::bsf32(v, &b);
    if (ra != rb || a != b) fail("bsf32", v);
    if (rex::fh1_inline_bit_scans::tzcnt(v) != ref::tz32(v)) fail("tz32", v);
    if (rex::fh1_inline_bit_scans::lzcnt(v) != ref::lz32(v)) fail("lz32", v);
    ++n;
  } while (++v != 0);
  check64(0);
  for (int i = 0; i < 64; ++i) {
    check64(uint64_t(1) << i);
    check64(~uint64_t(0) << i);
    check64(~uint64_t(0) >> i);
  }
  std::mt19937_64 rng(45);
  for (uint64_t i = 0; i < (uint64_t(1) << 28); ++i) {
    uint64_t x = rng();
    check64(x >> (i & 63));
  }
  std::printf("identical: %llu 32-bit values, all 8/16-bit, %llu 64-bit values\n",
              (unsigned long long)n, (unsigned long long)((uint64_t(1) << 28) + 193));
  return 0;
}
