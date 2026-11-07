#include <rex/graphics/vulkan/binding_reuse.h>
#include <cstdio>
#include <cstdlib>
using rex::graphics::vulkan::TextureRequestReuse;
static void check(bool ok) { if (!ok) std::abort(); }
int main() {
  TextureRequestReuse state;
  check(!state.Matches(1, 0, 1, ~0u, false));
  for (uint64_t submission = 1; submission < 5000; ++submission) {
    for (unsigned bit = 0; bit < 32; ++bit) {
      uint32_t mask = uint32_t(1) << bit;
      state.Remember(submission, 7, mask);
      check(state.Matches(submission, 7, mask, mask, false));
      check(state.Matches(submission, 7, 0, mask, false));
      check(!state.Matches(submission + 1, 7, mask, mask, false));
      check(!state.Matches(submission, 8, mask, mask, false));
      check(!state.Matches(submission, 7, mask, 0, false));
      check(!state.Matches(submission, 7, mask, mask, true));
      check(!state.Matches(submission, 7, ~mask, ~0u, false));
    }
  }
  state.Remember(5001, 9, ~0u);
  check(state.Matches(5001, 9, 0x80000001u, ~0u, false));
  check(!state.Matches(5001, 9, 0x80000001u, 0x7fffffffu, false));
  using rex::graphics::vulkan::BindingCompatiblePrefixMask;
  check(BindingCompatiblePrefixMask(0) == 0);
  check(BindingCompatiblePrefixMask(2) == 3);
  check(BindingCompatiblePrefixMask(3) == 7);
  check(BindingCompatiblePrefixMask(4) == 15);
  check(BindingCompatiblePrefixMask(31) == 0x7fffffffu);
  check(BindingCompatiblePrefixMask(32) == ~0u);
  // A changed vertex layout invalidates both texture stages; a changed pixel
  // layout preserves vertex bindings. Reuse must not resurrect those bits.
  check((15u & BindingCompatiblePrefixMask(2) & 12u) == 0);
  check((15u & BindingCompatiblePrefixMask(3) & 12u) == 4);
  using rex::graphics::vulkan::SamplerBindingReuse;
  int shader, other_shader, cache, other_cache;
  SamplerBindingReuse sampler;
  check(!sampler.Matches(&shader, &cache, 1, -1, 1));
  sampler.shader = &shader; sampler.cache = &cache;
  sampler.submission = 1; sampler.anisotropy = -1; sampler.dirty = 0;
  check(sampler.Matches(&shader, &cache, 1, -1, 1));
  check(!sampler.Matches(&other_shader, &cache, 1, -1, 1));
  check(!sampler.Matches(&shader, &other_cache, 1, -1, 1));
  check(!sampler.Matches(&shader, &cache, 2, -1, 1));
  check(!sampler.Matches(&shader, &cache, 1, 4, 1));
  for (unsigned bit = 0; bit < 32; ++bit) {
    sampler.dirty = uint32_t(1) << bit;
    check(!sampler.Matches(&shader, &cache, 1, -1, sampler.dirty));
    check(sampler.Matches(&shader, &cache, 1, -1, ~sampler.dirty));
  }
  std::puts("PASS: descriptor layout compatibility; sampler shader/cache/submission/policy/fetch guards; texture request masks, invalidation and usage transitions");
}
