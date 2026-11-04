# Vendored dependencies

Only the source files and headers the build needs are included. CMake uses these
local copies without downloading dependencies.

| Directory | Used by | Upstream revision | Source archive SHA-256 |
| --- | --- | --- | --- |
| `discord-rpc` | Discord Rich Presence | [discord/discord-rpc@963aa9f3e5ce81a4682c6ca3d136cddda614db33](https://github.com/discord/discord-rpc/tree/963aa9f3e5ce81a4682c6ca3d136cddda614db33) | `8f4115999391f95f443615fc5b13cb2398926ac4c418d3d34ff5735f71c6faea` |
| `rapidjson` | Discord Rich Presence | [Tencent/rapidjson@24b5e7a8b27f42fa16b96fc70aade9106cf7102f](https://github.com/Tencent/rapidjson/tree/24b5e7a8b27f42fa16b96fc70aade9106cf7102f) | `2d2601a82d2d3b7e143a3c8d43ef616671391034bc46891a9816b79cf2d3e7a8` |
| `bc7enc_rdo` | `forza-texcompress` (texture packs, #413) | [richgel999/bc7enc_rdo@b9438627eef73a1157e84201b6fa6eb2ffd6d9f0](https://github.com/richgel999/bc7enc_rdo/tree/b9438627eef73a1157e84201b6fa6eb2ffd6d9f0) | `bbb33d1dbb6178a3a2c044956c3cc0b8ea9fc2f2903824379dd86f87d53b64c6` |
| `openxr` (`include/openxr/openxr.h`, `openxr_platform.h`, `openxr_platform_defines.h`) | VR setting (`src/platform/xr.cpp`; the loader is opened at run time, not linked) | [KhronosGroup/OpenXR-SDK release-1.1.62](https://github.com/KhronosGroup/OpenXR-SDK/tree/release-1.1.62) | `dcc7c89b6af5d0364447debfe1b0437713bcedd0e8c0c2819c75ec6f546b393d` |

`ReGPU` is not vendored: it is a git submodule (`git submodule update --init forza-recomp/thirdparty/ReGPU`),
pinned to the `fh1` branch of the project's fork of Scott's (zer0ducksgiven) ReGPU:

| Directory | Used by | Pinned revision | License |
| --- | --- | --- | --- |
| `ReGPU` | the optional Windows GPU plugin `rexgpu-regpu.dll` (`regpu-plugin/`, `FORZA_REGPU_PLUGIN`, `--gpu_plugin=regpu`) | [FH1-ReImagined/ReGPU@4ead77404f4eaf8dedf761d9e83170372bee96dd](https://github.com/FH1-ReImagined/ReGPU/tree/4ead77404f4eaf8dedf761d9e83170372bee96dd) (`fh1`: upstream [zer0ducksgiven/ReGPU](https://github.com/zer0ducksgiven/ReGPU)@561d0ae plus FH1's changes) | MIT (`ReGPU/LICENSE`, copied to `licenses/ReGPU.txt`) |

It is built only into the plugin. The launcher's source bundle carries it on Windows only, and only the
files its CMake needs (`builder.source_files`), not its notes or developer tools.

`fidelityfx-fsr1` holds only the FSR 1 shader headers ReGPU compiles into its upscaler (with fxc, when
`FORZA_REGPU_FSR1_FROM_SOURCE` is on; otherwise `regpu-plugin/shaders/fsr1_cs.h` is used), unchanged, from [GPUOpen-LibrariesAndSDKs/FidelityFX-SDK v2.0.0](https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/tree/v2.0.0)
(`f4c1da8e`): `api/internal/gpu/ffx_*.h` without `ffx_core_cpu.h`, and
`upscalers/fsr3/include/gpu/fsr1/ffx_fsr1.h`. MIT; `LICENSE.txt` is the notice from their headers.
Source archive SHA-256 (`codeload.github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/tar.gz/f4c1da8e92f3fe563b5c28c44e6267ce6b6b8eb2`):
`021313c076baf922982db0166de94024e82f9daf9eb578b4a34c75c8ec650a5f`.

Not vendored: `src/kernel/ed25519.h` (Ed25519 signing and SHA-512 for signed requests to the
Forza Live server, #515) is this project's code in the formulation of TweetNaCl (Bernstein et
al., public domain), checked against the RFC 8032 vectors by `tools/test_live_signing.cpp`. The
launcher's `forza_launcher/ed25519.py` and the server's `forza_live/core/ed25519.py` derive from RFC 8032's
reference code (Simplified BSD License, `launcher/forza_launcher/RFC8032-LICENSE.txt`).

The hashes refer to the upstream `codeload.github.com/<owner>/<repo>/tar.gz/<revision>`
archives. Verify them before updating the vendored files. Keep upstream license
notices: `discord-rpc/LICENSE`, `rapidjson/license.txt`, `bc7enc_rdo/LICENSE` and `openxr/LICENSE-*.txt`
(the OpenXR headers are Apache-2.0 or MIT). The
license files are copied beside the built game (`licenses/`) and included in launcher
runtime staging.

`bc7enc_rdo` holds only `bc7enc.{h,cpp}` (BC7), `rgbcx.{h,cpp}` with
`rgbcx_table4_small.h` (BC1) and `bc7decomp.{h,cpp}` (the BC7 decoder the tool's checks
and tests use), unchanged: MIT or public domain (Unlicense), whichever the user prefers. Its
`LICENSE` also names `bc7e.ispc` (Apache-2.0) and LodePNG (zlib), which are not
included.
