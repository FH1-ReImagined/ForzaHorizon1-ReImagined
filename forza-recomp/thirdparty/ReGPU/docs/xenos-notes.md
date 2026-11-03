# Xenos notes

What ReGPU's renderer had to get right, learned while rendering a full retail game (Forza Motorsport 2)
and checked against Xenia and a real console. Read this before changing `src/gpu/`.

## The frame

- **Present cadence.** Titles present on vblank: every vblank in gameplay, often every second one in
  menus. ReGPU's vblank runs faster than its frame cap, so both reach the cap (`Config::maxFps`).
- **Predicated tiling.** A frame bigger than EDRAM is drawn in tiles: D3D replays the same command stream
  per tile with `binSelect`/`binMask` (PM4 `SET_BIN_*`) choosing which draws run, and a resolve per tile.
  The host draws all tiles into one tall surface; each tile's draws are offset by the tile's origin.
- **Resolves are tiles of one image.** Each tile resolves to the destination row it starts at; ReGPU keys
  the resolved texture by the image's own base so they land together. Some titles give the full image's
  rows on every tile's copy: the texture keeps its size instead of being recreated (which dropped the
  earlier tiles).
- **Clears after a resolve** prepare EDRAM for the next tile, which the host draws lower on the same
  surface: a full-width clear has to cover every row from the tile down.

## EDRAM surfaces

- **Render targets alias.** Titles draw into the same EDRAM through different views: another MSAA mode,
  another pitch, a 1x view of a 4x surface. A clear or stencil write through one view must reach what the
  other view draws (the paint-style composites in the first port only worked once those views mapped to
  the same host surface).
- **Taller and wider than the screen.** Targets of 1024x1024 or 2048-wide composites are drawn as passes
  of up to 720 rows, or in strips. They're scaled as a whole pass, and their resolves keep all their rows
  (clamping to 720 stretched them 1.4x when sampled).
- **1280x720.** ReGPU assumes the title renders at 1280x720 (`kWidth`/`kHeight` in `renderer.cpp`). Most
  360 games do; some use 1024x600 or 1152x640 and will need this made a setting.
- **Formats.** `8_8_8_8` stays 8-bit (clamped, like the hardware); other colour formats keep their HDR
  range. The 360's `7_7_7_3` (7e3) is approximated with half floats, clamped to its 31.875 ceiling.
  Depth copies are D24FS8: 20e4 float depth in the top 24 bits of each word.

## Resolved images as textures

- **Swizzle and swap.** A copy with the swap bit (`RB_COPY_DEST_INFO` bit 24) stores red and blue
  exchanged; the title's fetch swizzle swaps them back. ReGPU keeps its copies unswapped and corrects the
  swizzle when they're sampled. A copy the **CPU** reads (a screenshot, a thumbnail it compresses) wants
  the words as they are: red in bits 16-23 when the copy swaps.
- **Written back only on request.** Resolves stay on the GPU. An image the title reads on the CPU must be
  written to memory too (`Host::resolveWriteback`): at once (a stall), or a frame late without one for
  something read every frame.
- **Stale resolves.** Titles reuse a resolve's memory for their own textures later. ReGPU notices by
  hashing the guest memory under the image, **at the image's own bytes per pixel**: hashing an 8-bit image
  as 32-bit took in unrelated memory and dropped live resolves (noise where a shadow should be).
- **Reflection cubes** are six resolved faces; the cube is sized from its faces, not from the pass that
  first samples it (that rebuilt it mid-level).

## Textures

- **Tiling.** 2D textures are stored tiled in 32x32 blocks (`TextureTiledOffset`); DXT formats tile by
  4x4 block. Small mips are packed into the tail of a larger level: getting the packed-mip offsets wrong
  shows as black patches on far geometry.
- **Endian.** The fetch constant's endian field (8in16, 8in32, 16in32) applies before the format is read.
- **DXT5A / DXN** map to BC4/BC5. DXT5A's one channel is broadcast to all four.
- **Change detection.** Textures are re-hashed when used; small ones (icons) densely, big ones sampled,
  and the whole base level counts (a guessed size missed changes in the second half).
- **Filtering.** Anisotropic filtering only on wrapping samplers (surface textures). Clamped samplers also
  read shadow maps and render targets, where averaging depth breaks shadows.

## Shaders

- **Identity.** Hash only the used microcode dwords (`ucode::UsedDwords`): loaded shaders carry a per-load
  stamp after the last instruction, which otherwise makes every load look new.
- **Patched copies.** Titles patch vertex-fetch fields (format, stride, offset) into copies of a shader at
  load. ReGPU reads those fields from a per-draw table, so every copy shares one HLSL and one compile.
- **Vertex fetch.** Signed normalised formats clamp (`x / (2^(n-1)-1)`, the two lowest codes both -1).
  16-bit components: X in the low half of each endian-swapped word.
- **DX9 ALU rules.** Multiplies treat 0 x anything as 0 (`Mul9`, `Dot9` in the preamble).
- **Alpha test in depth-only passes** must stay on: shadow maps have cut-out geometry.
- **Compile time.** fxc with skip-optimisation is the fastest option; a large title has ~5,000 shaders.
  `Host::staticShaders` lets the first boot compile them all behind a progress screen instead of
  hitching in play. Finding shaders in memory as levels load (`shader_scan.cpp`) also finds stale copies
  in freed memory: never treat those as real.

## Display

- **Gamma.** The title uploads a 256-entry display table (`DC_LUT_30_COLOR`); apply it. It is often not
  the identity (one title's made the picture ~20% darker than without it).
- **Widescreen.** Every 360 title targets 16:9. At wider resolutions ReGPU widens the 3D view (Hor+) and
  keeps 2D content at 16:9; a title with its own widescreen patch calls `SetGameWidescreen(true)`.

## Console-accurate oddities (don't fix)

- Dark ambient areas can carry a small colour residue (about 1%) where spherical-harmonic ambient
  terms nearly cancel: red and green clamp at zero, blue doesn't. A real console and Xenia show it too;
  the title's photo-mode contrast slider makes it visible.

## Platform

- **Wine/Proton:** Wine's `d3dcompiler_47` can't compile the translated shaders (black screen, game
  running). Ship Microsoft's redistributable beside the executable; ReGPU loads it from there.
