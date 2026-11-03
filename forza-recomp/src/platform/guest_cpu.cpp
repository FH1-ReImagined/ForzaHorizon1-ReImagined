#include <cstdio>
#include <cstdlib>

#if defined(FORZA_GUEST_FMA) && FORZA_GUEST_FMA
namespace {
// Run before shared-library initializers as well as generated static
// initializers: SDK constructors may call inline code exported by the guest.
// The host and its SDK dependencies retain their portable compiler settings.
void CheckGuestCpu(int, char**, char**) {
  // Clang/GCC's feature detection includes the OS's AVX register-state support.
  // This translation unit deliberately does not inherit the guest's -mfma flag.
  __builtin_cpu_init();
  // Clang's -mfma also permits SSE4.2 and POPCNT; check those rather than
  // assuming a VM exposes every feature of the underlying physical CPU.
  if (!__builtin_cpu_supports("avx") || !__builtin_cpu_supports("fma") ||
      !__builtin_cpu_supports("sse4.2") || !__builtin_cpu_supports("popcnt")) {
    std::fputs("This experimental Forza build requires CPU/OS AVX/FMA, SSE4.2 and POPCNT support.\n"
               "Rebuild with -DFORZA_GUEST_FMA=OFF for the portable version.\n", stderr);
    std::_Exit(EXIT_FAILURE);
  }
}
__attribute__((section(".preinit_array"), used)) void (*const kCheckGuestCpu)(int, char**,
                                                                          char**) = CheckGuestCpu;
}  // namespace
#endif
