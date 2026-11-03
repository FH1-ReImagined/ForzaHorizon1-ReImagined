// Route native pause input and release composed resources before FH1 teardown.
// tu_address_map: TU4 addresses
#include "../platform/native_pause_scene.h"
#include "../platform/guest_addr.h"

#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
extern "C" REX_FUNC(__imp__sub_827DADC8);
extern "C" REX_FUNC(__imp__sub_827D6248);
extern "C" REX_FUNC(__imp__sub_827DDC50);
extern "C" REX_FUNC(__imp__sub_82790EF8);

extern "C" REX_FUNC(sub_827DADC8) {
  if (!forza::native_pause_scene::Input(ctx, base))
    __imp__sub_827DADC8(ctx, base);
}
extern "C" REX_FUNC(sub_827D6248) {
  forza::native_pause_scene::Destroy(ctx, base, ctx.r3.u32);
  __imp__sub_827D6248(ctx, base);
}
extern "C" REX_FUNC(sub_827DDC50) {
  const auto screen=ctx.r3.u32;
  __imp__sub_827DDC50(ctx,base);
  forza::native_pause_scene::PartyTick(ctx,base,screen);
}
extern "C" REX_FUNC(sub_82790EF8) {
  forza::native_pause_scene::PartyInput(ctx,base,ctx.r3.u32,ctx.r4.u32);
  __imp__sub_82790EF8(ctx,base);
}
#endif
