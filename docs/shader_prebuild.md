# Shader prebuild (#226)

Goal: like Steam's "Processing Vulkan shaders", build every pipeline the game uses before
playing, so that new places, cars and cameras no longer compile shaders mid-game (#167,
#49, #45). Nothing from the game is distributed: we ship a list of pipelines (hashes and
render state), and the shader code is read from the player's own game files.

Phase 1, the feasibility study, added three tools and changed no SDK or launcher code.
Phase 2 ([below](#phase-2-the-prebuild-linux-amd)) builds the feature: SDK patch 0113
imports a shipped list, the launcher has a "Prebuild shaders" step in Setup, and the list
in `launcher/forza_launcher/data/` is captured from scripted runs. The phase 1 tools:

- `tools/xxh3.py`: XXH3-64 in pure Python (the SDK's shader and pipeline hash).
- `tools/fxobj_shaders.py`: finds the shader microcode in the game files and measures how
  much of a played shader cache it covers.
- `tools/pipeline_list.py`: turns stored pipeline descriptions into a shippable list,
  merges and checks lists, writes the prebuild files for the SDK from a list and the
  player's files (phase 2), and (experiment) writes SDK storage files.

## What the SDK stores today

`VulkanPipelineCache::InitializeShaderStorage` (`sdk/src/graphics/vulkan/pipeline_cache.cpp`)
keeps two files per title in `<cache_root>/shaders/shareable/`, and patch 0086 adds a third
in `shaders/local/`. `<cache_root>` is `<user data>/cache`. With split scene/HUD rendering
(native scene, DLSS, FSR 3, SMAA) it is
`cache/fh1-live-v1-<output W>x<H>-<scale X>x<Y>/ui` and `.../scene`, one store per
setting.

**`4D5309C9.xsh`: shader microcode.** Header: `XESH`, then version `0x20201219`
(big-endian). Then one record per shader:
- u64 hash;
- u32 with the dword count (31 bits) and the type (top bit: 0 vertex, 1 pixel);
- the microcode as it was in guest memory (big-endian words).

The key is `XXH3_64bits()` of exactly those bytes: what `IM_LOAD` pointed at. A record that
fails its hash ends the file.

**`4D5309C9.fbo.vk.xpso` (`.fsi.` for the interlock path): pipeline descriptions.** The
original 12-byte header holds `XEPS`, the API (0 fbo, 1 fsi), and a version (big-endian):
`max(PipelineDescription::kVersion, SpirvShaderTranslator::Modification::kVersion)`.
Patch 0102 writes format 2: 20 bytes, `XEPS`, the API, then big-endian 2 and both
versions apart (today `0x20260228` and `0x20261018`); a store with the original header and
the same larger version is converted. If the header does not match, the whole file is
dropped. Then one record per pipeline: the u64 XXH3 of the description, then the
`PipelineDescription`:

| Field | Meaning |
| --- | --- |
| VS hash, PS hash (u64) | `.xsh` keys; PS 0 means no pixel shader |
| VS / PS modification (u64 each) | translation variant: interpolator masks, dynamic register count, clip planes, host vertex shader type (vertex, domain, point or rectangle expansion), point parameters, depth/stencil output mode, contraction |
| RenderPassKey (u32) | MSAA samples, used attachments, depth format (D24S8 / D24FS8: the guest's, whatever host format stands for it), 4 colour view formats, transfer formats |
| state (45 bits) | geometry shader type, topology, primitive restart, tessellation mode, depth clamp, polygon mode, culling, front face, rasterizer discard, depth write/compare, stencil test and ops |
| 4 render targets (26 bits each) | blend factors and ops, colour write mask |

The bit fields are packed differently per compiler. Clang/GCC (Linux, macOS) write
58-byte descriptions (66-byte records). MSVC (Windows) starts the last 13 state bits in a
new u32: 60 bytes, 68-byte records. The bit positions are otherwise the same, but the
files and the description hashes are not portable between Windows and the others. So the
shipped list uses decoded fields, never raw records.

**`shaders/local/<title>-<vendor>-<device>-<driver>-<versions>.vkpc`** (patch 0086): the
driver's `VkPipelineCache` data, one entry per stored description since #247; a save past
512 MiB is skipped with a warning.

**Warm-up.** At start the SDK reads the `.xpso` and drops records that fail their hash or
`ArePipelineRequirementsMet()`. It loads every `.xsh` shader and translates each (hash,
modification) pair the descriptions need to SPIR-V, with the current translator. It then
creates the pipelines on 3/4 of the cores, blocking, reporting `SHADER_WARMUP` progress.
No translated code is stored: only guest microcode, descriptions and the driver's own
cache. Pipelines created during play are appended to both files and flushed at the end
of every submission.

## Where the microcode is in the game files

A `.fxobj` is Forza's effect container. Each compiled shader in it is an Xbox 360 D3D
shader blob, preceded by its byte length (big-endian u32). The blob, all big-endian:

- **Header, 0x24 bytes.** Magic `0x102A1101` (vertex) or `0x102A1100` (pixel), virtual
  size, physical size, debug info offset (0x24, a `c:\Temp\UPDB\...` path, or 0), constant
  table offset, definitions offset, shader description offset, 0, 0.
- **Virtual part** (`virtual size` bytes from the start). Header, debug info, constant
  table (the D3D constant table with `vs_3_0`/`ps_3_0`), definitions, shader description.
  The description starts with u32 *physical offset* and u32 *byte size* of the microcode,
  followed for vertex shaders by the vertex fetch table.
- **Physical part** (follows the virtual part). Literal constants, then the microcode at
  `physical offset`. The microcode ends with a 12-byte compiler tag: `NJ`, u16, and an
  8-byte build id. The tag is uploaded too, so it is part of the hashed bytes.

`fxobj_shaders.py` scans for the magic and validates each header, so the effect format
around the blobs need not be parsed. The same blobs, with or without the length prefix,
are in:

| Source | Files | Blobs (VS / PS) |
| --- | --- | --- |
| `media/shaders/**/*.fxobj` | 174 | 1514 / 1407 (2918 unique) |
| title update `media.zip`, `shaders/cars/*.fxobj` (replace the base car shaders) | 6 | 1309 / 1221 |
| `media/tracks/*/bin.zip`, `shaders/track/*.fxobj` (zip method 21, LZX, no local headers) | 197 | 766 / 606 |
| executable image (engine shaders: post, sky, UI, particles) | 1 | 192 / 290 |

No other media zip holds shaders: `aiopenworld`, `animatedobjects`, `camera`,
`dynamicpost`, `effects`, `gamemodes`, `gametunablesettings`, `Livery`, `physics`,
`realtimesky`, `renderscenarios`, `Spectators` and `UI` contain XML, textures (`xds`, `dds`),
geometry and UI data. The `.sh` files in the Colorado zips are lighting data. The
launcher can already decode the executable image (`xexcheck.py` through
`tools/apply_title_update.py`, pure Python where needed). LZX members decode with
`tools/lzx_delta.py`.

### Vertex shaders are bound at draw time

Pixel shaders are uploaded exactly as stored, so their hash is the blob's. Vertex shaders
never are: when the game draws, the console's D3D runtime binds the vertex shader to the
vertex declaration and the pixel shader, and uploads the patched copy. Comparing the
stored shaders with the blobs shows three kinds of change, and nothing else:

1. **Vertex fetches** take the vertex layout. Fetch constant and slot, prefetch count,
   destination swizzle, format and its flags, exponent adjust, mini-fetch, stride and
   offset (`FETCH_LAYOUT_MASK`).
2. **Exports** are renumbered to the pixel shader's inputs. The export destination
   registers and the clamp bit change (`EXPORT_MASK`, ALU word 0 of exports only).
3. **Exports the pixel shader does not read** become a fixed no-op ALU instruction
   (`ALU_NOP`).

The list stores, for each played vertex shader, the blob's hash and those fields per
changed instruction: instruction indices and vertex layout values, no instruction code.
`apply_binding()` rebuilds the exact shader, and its XXH3 must equal the listed hash.

## Coverage

These are the shaders stored by every play session on the test machine: the main cache
from long play, and 347 stores from scripted runs. Sources: the base game files, the TU4
`media.zip` and the TU4 executable image (`fxobj_shaders.py coverage`).

| Shaders found in | VS (806) | PS (909) | All (1715) |
| --- | --- | --- | --- |
| `media/shaders` only | 37.5% | 31.6% | 34.3% |
| + TU4 `media.zip` | 73.0% | 61.3% | 66.8% |
| + track zips | 85.7% | 86.2% | 86.0% |
| + executable image | **99.4%** | **99.7%** | **99.5%** |

How the 801 vertex shaders are bound: vertex layout only 691, layout and no-ops 41,
layout, renumbered exports and no-ops 68, layout and renumbered exports 1. The 8 not found
are 5 vertex shaders that differ in other ways and 3 pixel shaders with no blob.

At the pipeline level, both shaders are rebuildable for 1568 of the 1612 distinct stored
descriptions (97.3%; all three storage versions), and for 1008 of the 1047 from the main
cache and the current version (96.3%). The rest stay as today: compiled the first time
they are met.

Without the binding step no vertex shader would match at all: a stored vertex shader's
hash never equals its blob's.

## The pipeline list

```
tools/pipeline_list.py make <cache>/shaders/shareable/4D5309C9.fbo.vk.xpso \
    --xsh <cache>/shaders/shareable/4D5309C9.xsh \
    --sources <game>/media/shaders --sources <update>/media.zip \
    --sources <game>/media/tracks --sources <image>.bin -o run.json
tools/pipeline_list.py merge a.json b.json -o list.json   # refuses mixed storage versions
tools/pipeline_list.py check list.json
tools/pipeline_list.py prebuild list.json --sources ... --out <cache>/shaders/prebuild
```

A list is JSON with:
- `format` (2), `title` and `storage_versions` (`fbo:<description version>/<modification
  version>` from format-2 stores; phase 1 lists, format 1, held the larger version only);
- `pipelines`: `vs`, `vs_mod`, `ps`, `ps_mod`, `fields` (the 58 decoded bit fields of the
  render pass key, the state and the 4 render targets, in `pipeline_list.FIELDS` order),
  and `partitions` (`main`, `ui`, `scene`). Format 1 had the fields as named dicts, 1.5 KB
  per pipeline; `merge` still reads it;
- `shaders`: one recipe per hash. `file`: the blob with this hash. `bound`: `base` hash
  plus `fetch` / `export` / `nop` fields. `image` (#614): microcode with no blob header in the
  executable image (the D3D library's own shaders), `size` bytes at one of `offsets`, one
  offset per known executable (Title Update 4's, the disc's); the build takes the one whose
  XXH3 is the hash. `missing`: not in the game files. `make` writes `image` recipes when an
  executable image is among its sources; the recipes uploaded from players' sessions (phase 3)
  keep the other kinds.

`check` refuses anything else. Every value must be a valid value of its field (an enum
may have fewer values than its bits hold: a blend factor has 4 bits and 15 values), every
`fetch` and `export` value must fit its mask, and the only 64-bit strings are hashes and
modification bits. So microcode cannot hide in a list. With
`make`, the `.xsh` microcode is only read to find each shader's source and never written
to the list.

Sizes (format 1): the main cache's 940 pipelines and 951 shaders gave 1.7 MB of JSON, all
347 stores 3.0 MB, 84 KB gzip. Format 2 writes one pipeline or shader per line, compact:
see [the shipped list](#the-shipped-list).

## Building from the list

`pipeline_list.py write-storage list.json --sources ... --out <cache>/shaders/shareable`
rebuilds every listed shader from the given game files and verifies its hash. It writes a
local `.xsh` and an `.xpso` in the SDK's own format and layout, which today's warm-up
loads as is. From the 1047-pipeline list it wrote 1021 shaders, each byte-identical to the
one stored in play, and 1008 pipelines, all valid records whose shaders are present.

Phase 1 did not boot the game on these files. Phase 2 did, through the SDK's own import
instead: `Created N graphics pipelines from Vulkan storage (N requested)` for every list,
and a list made from a store imports as exactly that store's descriptions (293 of 293
"already stored").

For the shipped feature, the SDK should read the list itself rather than the launcher
writing its private formats. The estimate (phase 2 needed a patch of about 400 lines with
its header, and a launcher module of 300 lines plus the page):

| Change | Size |
| --- | --- |
| SDK: read a second, imported store (`shaders/prebuild/`: the launcher's `.xsh` of rebuilt shaders, plus the list in a small fixed binary of the decoded fields), filter it like the own store, merge it before translation; the existing loop translates, creates and reports progress | ~150 lines in `InitializeShaderStorage` (split the reader into a helper) |
| SDK: fix the rejected-record truncation (below); without it, imported records the device rejects would erase the player's newest pipelines at every start | ~15 lines |
| SDK: device-dependent fields. Drop or remap records the device cannot build (D24S8 depth when `VK_FORMAT_D24_UNORM_S8_UINT` is missing, `fsi` vs `fbo`, the portability-subset limits `ArePipelineRequirementsMet` checks) | ~30 lines |
| SDK: a prebuild-only run (cvar: warm up, save the driver cache, exit) | ~20 lines |
| SDK: `.vkpc` limit | raise 64 MiB, or warn when exceeded (now silent) |
| Launcher: "Prebuild shaders" step. Read the player's `.fxobj`, zips and executable image, rebuild the listed shaders (`fxobj_shaders.py`, `pipeline_list.py` logic), write `shaders/prebuild/`, run the prebuild-only game with the existing warm-up progress, offer again after a driver or launcher update | ~300 lines plus tests |

About one SDK patch of 200 to 250 lines, plus the launcher step. No change to translation
or pipeline creation.

Partitions: with split rendering the scene and HUD caches are separate stores, and
scene pipelines translate with the scene's resolution scale. The prebuild run must use
the player's current graphics settings, and a settings change that selects a new
partition needs a new prebuild (offer it, as for driver updates).

## Stale caches (#167)

Findings:

- **Stored data carries format versions, not a translator or SDK version.** `.xsh`
  carries `0x20201219` (record format) and `.xpso` the `max()` of the description and
  modification versions. `.vkpc` is keyed by device, driver, pipeline cache UUID and both
  versions.
- **A translation change cannot leave stale translated code.** Only microcode and
  descriptions are stored. Every start re-translates with the current translator and
  re-creates the pipelines, and the driver's caches are keyed by the SPIR-V. After an
  update that changes translation, the warm-up is slower once while the driver recompiles;
  the cached pipelines are still correct.
- **An update that changes descriptions without a version bump** leaves dead records:
  warmed at every start, never used. The changed pipelines are compiled once in game and
  stored, and do not come back next session. A bump can also be hidden by the `max()`:
  raising the lower of the two versions changes nothing.
- **Bug: rejected records make the newest pipelines disappear at every start.** Upstream
  `pipeline_cache.cpp` (bd833a2: lines 461-491 and 695) skips records that
  `ArePipelineRequirementsMet()` rejects and compacts the kept ones in memory. It then
  truncates the file to *kept count x record size*, but the file is not compacted. The
  rejected records stay in place, and the last N valid records are cut off, N being the
  number of rejected ones. Those are the pipelines met last session. They compile again in
  the same places, are appended, and are cut again at the next start. This happens every
  session until the cache is deleted. That is the #167 symptom, but whether that
  player's store held rejected records is unknown. It needs a store with
  records this device rejects: a GPU switch (patch 0054's discrete preference, an eGPU, a
  copied cache), a driver exposing fewer features, or any imported list. The 347 local
  stores have none, so it was not triggered here.
- **The `.vkpc` stops being saved past 64 MiB, silently** (`pipeline_cache.cpp`:486), and
  on AMD's Windows driver it grows at every start. In the RX 6700 XT log in #167, the
  start supplied 39.6 MB for 867 stored pipelines. The warm-up still took 7.2 s and the
  first save afterwards was 55.9 MB: about a third of the stored pipelines missed the
  supplied data and were compiled again. The session ended at 58.4 MB, so the next start
  or two cross 64 MiB. From then on nothing new is saved. Every start recompiles what is
  missing (slow starts), and deleting the cache resets it. At about 46 KB per pipeline on
  that driver, a full prebuild (1500+ pipelines) is over the limit by itself. Why the
  warm-up misses AMD's cache for the same pipelines is open: on NVIDIA the second start
  reused it (801 ms, then 296 ms, [warm-up](shader_warmup.md)).

Proposed fix:
1. Rewrite the `.xpso` compacted when anything was rejected, instead of truncating.
2. Store the two versions separately, so that any bump invalidates.
3. Add a translation revision that our SDK patches bump when they change SPIR-V output or
   description semantics. Put it in the `.vkpc` identity, so that stale driver data is
   dropped instead of growing toward the limit. The launcher compares it to offer
   "Prebuild shaders" again.
4. Raise the `.vkpc` limit, or split the file, and log when a save is skipped.

## Phase 2 plan

1. SDK patch: the store fixes above (1-4), and the import of `shaders/prebuild/` with
   device filtering, plus the prebuild-only run. **Done**: 0102 and 0086 (#253) cover
   fixes 1, 2 and 4, 0113 the import and the prebuild-only run; fix 3 is replaced (see
   [below](#sdk-patch-0113)).
2. Capture lists from scripted runs that cover the map, every car class, the cameras,
   photo mode, Autoshow and the UI scenes. `tools/perf_scenario.sh` and `live_session.sh`
   on Linux AMD, Windows NVIDIA and the Steam Deck. Merge per storage version. Keep D24S8
   and D24FS8 variants, or let the import remap them. **Done on Linux AMD** (no remap
   needed: the key holds the guest's format) and **on Windows NVIDIA**
   ([below](#windows-nvidia-276)). **Open**: the Steam Deck capture (merge it with
   `pipeline_list.py merge`; the fields mean the same on every compiler). #276 added the rest of the map, more cars, the races, the paint shop and the
   Horizon World ([below](#wider-coverage-276)).
3. Launcher: the "Prebuild shaders" step in Setup, offered again after a driver,
   launcher or graphics-setting change. Progress and cancel, using the existing warm-up
   progress lines. **Done.**
4. Measure: hitches per session (`FORZA_STALL`) on a fresh cache, before and after, on
   AMD and NVIDIA. Also prebuild time and warm start time with the full list. **Done on
   Linux AMD and Windows NVIDIA**; open on Windows AMD, where the warm-up was slow in #167.
5. Lists uploaded by players through Forza Live. The format holds no game content.
   **Done**: [phase 3](#phase-3-lists-from-players-276).

## Phase 2: the prebuild (Linux, AMD)

Done on Linux: AMD Radeon RX 9060 XT (RADV, Mesa driver 0x6802001), 8 cores. Windows with
NVIDIA: [below](#windows-nvidia-276). The Steam Deck is still to do (plan above).

### SDK: patch 0113

Each Vulkan pipeline cache reads `<cache root>/shaders/prebuild/` at every start (the
top cache root, also with split rendering):

- `<title>.<fbo|fsi>.vk.xpl`, the list for the SDK
  (`include/rex/graphics/pipeline_prebuild_file.h`). Little-endian: a 40-byte header
  (`XEPL`, format 1, the render target path, both description versions, the field count,
  the record count, the record size, FNV-1a 64 of the records), then 96-byte records:
  VS hash, VS modification, PS hash, PS modification (u64), partition flags (u32: main 1,
  ui 2, scene 4) and the 58 decoded fields, one byte each.
- `<title>.xsh`: the shaders the launcher rebuilt, in the SDK's own shader storage format.

A list for the other render target path, for other versions, damaged, or with a value
outside an enum is not used (a warning, or the record skipped). The rest is filtered like
the own store: the partition this cache serves (`main`, or `ui` / `scene` with split
rendering), the FH1 fractional raster variant (modification bit 63, set per draw when the
raster is scaled) dropped where the raster is not scaled, `ArePipelineRequirementsMet`,
and pipelines whose shaders are missing. The description is assembled field by field
(`ToDescription`), so the compiler places the bits: the same list serves MSVC and Clang
builds. The listed pipelines are added to the stored ones before translation; the
warm-up creates them with the usual progress, and their driver data (0086) is kept. They
are never written to the store, which keeps what the player met in play.

`--shader_prebuild_only=true` exits after the warm-up, with the driver data saved and
before the guest starts (`SHADER_PREBUILD: complete, exiting`).

Tests: `tools/test_pipeline_prebuild.cpp` (CI, Linux and Windows SDK jobs) checks the
format, every refusal, the partition and raster filters, and that `ToDescription` on a
copy of the description's declarations gives exactly the bytes the game stored, 58 with
Clang/GCC and 60 with MSVC layout (also checked here with `clang++ -mms-bitfields`). The
same list bytes are pinned in `launcher/test_shader_prebuild.py` for the Python encoder.
In the game, a list made from a store and imported over that store finds all 293 of its
pipelines already stored.

Device filtering: the render pass key holds the *guest's* depth format on every device
(`RenderPassKey::depth_format` comes from the render target's key; the comment saying it
must be D24FS8 without a host D24S8 format is from Xenia's older design). RADV has no
`VK_FORMAT_D24_UNORM_S8_UINT`, and its stores still have D24S8 keys (88 of 1612 in phase
1). So nothing is remapped; a first version that did turned 22 of 293 imported pipelines
into ones the game never draws.

Store fixes (#167, #247), as of PR #253:
1. Compacted rewrite instead of the truncation: done (0102).
2. Both versions in the header: done (0102, format 2).
3. A translation revision in the `.vkpc` identity: not done. 0086 now keeps one driver
   entry per pipeline and rebuilds an entry the driver did not use from an empty cache,
   so stale driver data no longer piles up; the launcher offers the prebuild again after
   a launcher or GPU plugin update instead.
4. The `.vkpc` limit: done (0086: 512 MiB, a skipped save is logged).

### The shipped list

`launcher/forza_launcher/data/pipelines-4D5309C9.json`: 2831 pipelines and 1102 shader
recipes, storage version `fbo:20260228/20261018`. 865 KB of text, one pipeline or shader
per line (60 KB gzip): small enough for git, and a new capture changes only the lines of
new pipelines. `check` passes: hashes, decoded render state, vertex layout and export
fields, and image offsets.

Captured on Linux (RADV) with scripted runs (`live_session.sh` and the autopress FIFO,
scratch copies of a free-roam save, offline), each into its own cache root, and the same
route on Windows with NVIDIA ([below](#windows-nvidia-276)):

| Run | Store | Pipelines |
| --- | --- | --- |
| Free roam, every camera (5 views and look back), Dak's garage with four car changes (the cockpit view of each), photo mode with every effect slider and a photo, the pause menu pages, into the night (with the exploration that found the menus) | main | 780 |
| From the spawn to the Autoshow showroom: several cars, each from three views | main | 610 |
| The GoPro Millennia Masters festival race at the spawn: sign-up, intro, 60 s of racing | main | 504 |
| A new profile's opening drive | main | 411 |
| Free roam near the spawn (a failed fast-travel run) | main | 278 |
| SMAA at 1440p: the first run above, and the race | ui, scene | 415 + 643, 316 + 473 |
| SMAA at 1080p, the race: the HUD raster is scaled (FH1 fractional raster, modification bit 63) | ui, scene | 315 + 480 |
| #614: the race (60 s with autodrive following the AI) and a free-roam drive (70 s on the County Route 203 loop), each from an empty cache and after a prebuild; 7 pipelines not listed before | main | 444 |
| #276, one cache root over four sessions (below): 27 places in every region but the two held out, by day (12:00) and night (23:00); photo mode; an outpost's Change Car, Paint Shop (paint, a vinyl group, decals) and Garage upgrades; the Autoshow (the first cars of its first manufacturers, Abarth to Aston Martin, from two views, and a purchase); 12 festival races and two Showcases with the first eligible car (D to S: MINI, Nissan, Mitsubishi, Lotus, Toyota, Plymouth, Ferrari, Ford and others); the main menu's Benchmark; 325 pipelines not listed before | main | 1208 |
| #276, a Horizon World on a local server: two games, a co-op challenge, both cars driving together | main | 513 |
| #276, Windows, GTX 1650 Ti, the launcher's settings for that PC: the map tour (24 places, day and night), 12 festival races and a Showcase, two Autoshow purchases, photo mode with every effect slider and a photo; 44 pipelines not listed before | main | 1046 |
| #276, the same laptop at five render settings players use ([below](#render-settings-276)): 540p scene, 720p without scene MSAA, 720p with 2x MSAA, the lowest settings, FSR 3 at render scale 2; 1265 pipelines not listed before | main, ui, scene | 939, 950, 944, 946, 463 + 687 |

Partitions in the merged list: 482 pipelines in all three, 331 main and scene, 1482 main
only, 504 ui only (most of them the fractional raster variant), 24 main and ui, 6 scene only,
2 scene and ui. A pipeline seen only without split rendering is also imported by both
split caches (`split_partitions`): which of the two would draw it is unknown. With SMAA at 1440p
the HUD cache imports 634 pipelines and the scene cache 850 (the 269 fractional variants
are dropped there, the raster is not scaled).

Shaders: 610 `file`, 488 `bound`, 2 `image`, 2 `missing`; 2824 of the 2831 pipelines (99.8%)
can be rebuilt from the player's files. The rest compile in play, as before. Before #614 the two
`image` shaders were `missing` (1148 of 1190): a vertex shader of 96 bytes and a pixel shader of
36 that the D3D library keeps in the executable without a blob header (Title Update 4 image
offsets 882440 and 882536, the disc's 810200 and 810296). 39 of the 42 listed pipelines that
could not be built used them, 27 of them in the `main` partition, and those were most of what
a first session after the prebuild still compiled in play (see docs/perf.md, #614).

Coverage of earlier play (phase 1 stores, older versions, so compared by shader hashes and
render state without the modification bits): 759 of the 940 pipelines of the long-played
main cache (80.7%), 903 of the 1612 of all 347 stores (56%, many of them other split
settings and older SDK versions).

Not reachable offline by the menus: "Fast Travel Anywhere" is a Marketplace unlock (the map
offers it, then says Xbox LIVE is needed). The #276 captures move the car instead: a test
script writes the position of the player car's physics state (the `car - 0xA20` vector that
`tools/autodrive.py` reads) onto a road node of the sat-nav graph (`colorado.nav`, which has
the heights) and zeroes its velocity for 2 s; the world streams in around it as after a fast
travel. The same works on an event's or an outpost's own position, where X then signs up or
enters as usual.

### Wider coverage (#276)

TU4 game of #614's branch (no game or SDK change from main 77a7c33), the standard SDK overlay,
RX 9060 XT (RADV), Ryzen 7 7800X3D, Linux; the golden TU4 save in a scratch `--user_data_root`,
scratch XDG dirs, 60 fps, FSR 1, 1920x1080 window, audio muted. Scripted input through the
autopress FIFO, `tools/autodrive.py` on `tools/nav_route.py` loops in free roam and following
the AI in races.

The capture (one cache root, so one store): a map tour that moves the car to the 10 outposts,
the towns and a 2 km grid of road nodes (27 places; every region except Red Rock Heights and
the area east of x = 5400, Finley Dam and the canyon, held out for the measurement) and
drives 25 s from each, by day with the camera changed at every third place, then again
backwards at 23:00 (the clock held as `tools/render_test.py` does); then the rest listed in the
table above. FH1 has no weather, so there is no rain to capture. 1208 pipelines were stored,
325 of them not in the list before: 92 from the tour, 53 more from photo mode and the outpost,
94 from the Autoshow, 77 from the races, 9 from the Horizon World, none from the Benchmark. Merged
with `pipeline_list.py merge`: 1197 to 1522 pipelines, 973 to 1088 shaders, 432 to 527 KB
(43 to 49 KB gzip).

The measurement, places and events the capture did not visit: an empty cache root and Mesa
cache; the launcher's prebuild with the list alone (offline); then from the boot: free roam
at Red Rock Heights at 12:00 (45 s of driving), Goodyear Mountain Blast (sign-up, the first
eligible car, 60 s of the race from the start), free roam at the Finley Dam outpost at 23:00
(45 s), Rockstar Cliff Run (as the first race). Benchmark lock held, no other game, two runs
per list, alternating. "Built in play" is the session's own store at the end: the pipelines
it met that the prebuild had not imported (it never stores imported ones).

| | #614's list (2 runs) | This list (2 runs) |
| --- | --- | --- |
| Pipelines built in play, whole session (boot, menus, sign-ups, loads included) | 57 / 57 | **6 / 6** |
| Pipelines the command processor created in the four scenes (`--fh1_frame_cost_csv`) | 12 / 12 | **1 / 1** |
| Frames over 25 ms in the four scenes (4 min 1 s) | 3 / 1, worst 32 ms | 0 / 5, worst 48 ms |
| Prebuild: shaders from the game files, then the game (`--shader_prebuild_only`) | 1.8 s + 2.7-2.9 s, 876 pipelines in a 1.7-1.8 s warm-up | 1.8 s + 3.3-3.4 s, 1230 pipelines in a 2.4 s warm-up |
| Warm-up at the first start after the prebuild | 395 / 391 ms | 479 ms |

53 of the 57 pipelines #614's list missed are in this one; of the 6 left, 3 use shaders the
list has no recipe for and 2 are listed but were not imported. None of the frames over 25 ms
in either column was a pipeline: the command processor waited for the game's commands or
uploaded data (one 9.5 ms pipeline wait in a 26 ms frame in the first column). The 25-28
frames over 25 ms during each load, sign-up and quit are the game's own, as in #614.

### Windows, NVIDIA (#276)

Windows 11, Core i5-10300H (4 cores), GeForce GTX 1650 Ti (4 GB, driver 0x8AD84000), 8 GB RAM.
TU4 game built by the launcher from main of 2026-11-04, with the launch plan the launcher
makes for that PC (60 fps, FSR, the 720p scene with the game's MSAA, 1920x1080 fullscreen,
`--gpu_vendor=nvidia`); the golden TU4 save in a scratch `--user_data_root` and cache root,
audio muted, offline, `--vulkan_store_pipeline_cache=false` (the NVIDIA preset of #667), and
`__GL_SHADER_DISK_CACHE_PATH` on an empty folder per run, so the driver's own shader cache
starts empty too. The autopress FIFO is Linux only, so a script in the signed-in session sent the
inputs as keys of the SDK's keyboard pad (`--mnk_mode`), and moved the car, held the clock and ran
`tools/autodrive.py` (unchanged; its `/proc` reads went through `ReadProcessMemory`) as on Linux.
The race sign-ups and menus were checked on screenshots.

The capture (one cache root, one session): the map tour of 24 places (the outposts, towns, the
festival, the airport and a 2 km grid of road nodes outside the two held-out areas), 25 s of
driving at each by day with the camera changed at every third place, then backwards at 23:00;
12 festival races (GoPro Millennia Masters, G-SHOCK Super Sprint, Ferrari Campione, Lotus
Championship, BBS Japanese Trailblazers, ADIDAS Lakeside Landslide, Rockstar Dirt Raid, Brembo
Midnight Blast, MINI Mayhem, Audi Elite, Oakley British Elite, Old Spice Mega Muscle) and the
Bi-Plane Beatdown Showcase, each with the first eligible car; two Autoshow purchases (Lotus
Evora S, MINI Cooper S, the second from both views); photo mode with every effect slider and a
photo. Not repeated here: the outpost menus and the Horizon World (one game at a time on 8 GB).

1046 pipelines were stored, 44 of them (4%) not in the list:
- 13: the dirt-map seam variant (#164, `fh1_dirt_seam` in the vertex and pixel shader
  modifications) of listed shader pairs;
- 19: listed shaders and modifications with other render state: another primitive type (12),
  blend factors (4), depth or stencil tests (2), MSAA (1);
- 2: without the early depth hint (`depth_stencil_mode`, which follows the draw's alpha test);
- 10: a shader no list had (9: cars and places the Linux captures did not meet) or a new pair of
  listed shaders (1).

None is specific to the vendor or to Windows: NVIDIA's descriptions have the same render pass
formats, MSAA, view formats and modifications as RADV's, and the fields mean the same with MSVC.
Coverage is what adds pipelines, and so are render settings: the #640 runs on the same laptop
(the benchmark and free roam with MSAA off, 2x MSAA and the 540p scene, in one store) stored 997
pipelines, 508 of them not listed: 299 differ only in the MSAA sample count (with the view format)
and 208 in modification bits that follow it (centroid interpolation). The list covers the
settings it was captured with.

Merged with `pipeline_list.py merge`: 1522 to 1566 pipelines, 1088 to 1096 shaders, 527 to
540 KB (49 to 51 KB gzip). Two `image` recipes differed only in the offsets: the shipped ones
also hold the disc executable's, which a TU4 capture cannot see, so they were kept.

The measurement: the held-out route of [Wider coverage](#wider-coverage-276) (Red Rock Heights
at 12:00, Goodyear Mountain Blast, Finley Dam at 23:00, Rockstar Cliff Run; the same times and
lengths), each run from an empty cache root and driver cache: the prebuild as the launcher does it
(the files, then the game with `--shader_prebuild_only`, offline), then the game from the boot with
the same driver cache. Two runs per list, alternating; frames from `FORZA_FRAMETIME_CSV`,
pipelines from `--fh1_frame_cost_csv`.

| | Shipped list (2 runs) | With the Windows capture (2 runs) |
| --- | --- | --- |
| Pipelines built in play, whole session | 3 / 3 | **2 / 2** |
| Pipelines created in the four scenes (on the command processor / real ones on any thread) | 1 / 2, 1 / 2 | **0 / 1, 0 / 1** |
| Frames over 50 ms in the four scenes (3 min 45 s) | 3 / 4, worst 59 ms | 2 / 3, worst 61 ms |
| Longest command processor frame | 117 / 124 ms (a 59-79 ms pipeline wait) | 69 / 67 ms |
| Prebuild: shaders from the game files, then the game | 3.6-3.8 s + 30.6-31.3 s, 1230 pipelines in a 26.7-27.6 s warm-up | 3.7-3.9 s + 31.3-31.6 s, 1261 pipelines in a 27.4-27.8 s warm-up |
| Warm-up at the first start after the prebuild | 1192 / 1117 ms | 1233 / 1215 ms |

- The shipped list already covers the held-out route on NVIDIA: 3 pipelines met in the whole
  session, 2 of them in Rockstar Cliff Run. The Windows capture adds the one the command
  processor waited for at that race's start (the 117-124 ms frame, held rather than shown). The
  other two are in neither list (one pixel shader has no recipe); one of them compiles for
  187-210 ms in the background, and the two to three frames over 50 ms in the race wait for it
  (about 33 ms each). The rest of the route had none (one 51 ms frame in one run).
- With an empty driver cache NVIDIA builds about 45 pipelines a second here (the warm-up runs
  on 6 threads), against about 500 on the RX 9060 XT, so the prebuild takes 35 s, almost all of
  it the driver. The bigger list costs nothing measurable. The first start after it takes
  1.1-1.2 s: the driver's cache (45 MB for the list) answers for every pipeline.

### Render settings (#276)

The #640 runs showed that render settings, not the vendor, add the most pipelines: the scene's
MSAA sample count is part of every scene pipeline, and the pixel shaders' modification bits for
centroid interpolation follow it. The captures above were all made with the game's 4x MSAA scene
(or split rendering), while 0.2.7 gives entry cards the 540p scene by default.

Same laptop and setup as [Windows, NVIDIA](#windows-nvidia-276), main of 2026-11-05 (09a1d6a),
one cache root per setting, the scripted route of that capture (the same road nodes): the map
tour by day and at 23:00, 8 festival races with the first eligible car (GoPro Millennia Masters,
Ferrari Campione, BBS Japanese Trailblazers, ADIDAS Lakeside Landslide, Rockstar Dirt Raid, Brembo
Midnight Blast, Oakley British Elite, Old Spice Mega Muscle) and the Bi-Plane Beatdown Showcase,
photo mode with every effect slider and a photo, and the Autoshow's Horizon Recommends for the
Lotus Championship (no purchase). Lotus Championship, MINI Mayhem and Audi Elite need a car the
save doesn't have and were left out. The settings are the launcher's (`game.build_plan` with the
setting changed):

| Setting | Pipelines stored | New against the list | New against the list and the settings above |
| --- | --- | --- | --- |
| The launcher's plan for this PC: 540p scene, game MSAA (one pass with 2x MSAA), FSR 1 | 939 | 504 | 504 |
| 720p scene, Scene anti-aliasing Off | 950 | 512 | 511 |
| 720p scene, Scene anti-aliasing 2x (native 2x MSAA) | 944 | 507 | 22 |
| The lowest settings (540p, Scene anti-aliasing Off, anisotropic filtering and motion blur off) | 946 | 516 | 5 |
| AMD FSR 3 Quality at 1080p (render scale 2, split rendering) | 463 ui + 687 scene | 223 | 223 |

Why they are new:
- 540p and 720p with 2x: 397 and 408 are listed pipelines drawn with 2 samples instead of 4, the
  rest of the state the same; 92-93 more differ in the render target's view format only. The two
  settings draw the same 2x scene, so the second adds only 22.
- Scene anti-aliasing Off and the lowest settings: about 500 drawn with 1 sample instead of 4,
  436 of them also with other pixel shader modification bits (the interpolation that follows the
  sample count). The lowest settings add 5 to Off.
- FSR 3: 206 differ only in modification bit 63 (the HUD's fractional raster, as with SMAA at
  1080p), 216 of the 223 in the HUD store.
- In every setting fewer than 10 were a shader no list had or other render state (blend, depth,
  stencil, culling).

Merged with `pipeline_list.py merge` (the two `image` recipes kept as shipped, with the disc
executable's offsets): 1566 to 2831 pipelines, 1096 to 1102 shaders, 540 to 865 KB (51 to 60 KB
gzip).

The measurement: the held-out route of [Wider coverage](#wider-coverage-276) as in
[Windows, NVIDIA](#windows-nvidia-276) (each run from an empty cache root and driver cache, the
launcher's prebuild, then the game from the boot), now with the launcher's plan for this PC, the
540p scene. Two runs per list, alternating.

| | List before (2 runs) | This list (2 runs) |
| --- | --- | --- |
| Pipelines built in play, whole session | 275 / 283 | **9 / 6** |
| Pipelines created in the four scenes (on the command processor / in the background) | 21 / 43, 20 / 49 | **0 / 1, 1 / 2** |
| Frames over 50 ms in the four scenes (3 min 40 s) | 2 / 2, worst 59 ms | 2 / 2, worst 55 ms |
| Longest command processor frame | 143 / 157 ms | 65 / 67 ms |
| Prebuild: shaders from the game files, then the game | 3.2 s + 27.5-27.9 s, 1261 pipelines in a 24.1-24.6 s warm-up | 3.4 s + 39.6-40.0 s, 2298 pipelines in a 36.3-36.6 s warm-up |
| Warm-up at the first start after the prebuild | 1080 / 1087 ms | 1569 / 1488 ms |

- At 540p the list before missed most of the route: 275-283 pipelines were built in play, 14 on
  the command processor and 31-36 in the background in the first scene alone (Red Rock
  Heights), and the command processor held frames for up to 143-157 ms. With this list the
  session builds 6-9, the scenes 0-1 on the command processor, and the longest frame is 65-67 ms.
- The 2 frames over 50 ms per run are in Rockstar Cliff Run with both lists, the pipeline of
  #670 that no list has (its pixel shader has no recipe) compiling in the background.
- The prebuild takes 12 s longer (52-63 pipelines a second from an empty driver cache) and the
  first start after it 0.4-0.5 s longer: 2298 of the 2831 are imported by the main store, the
  rest are the split-rendering HUD and scene variants.

### Launcher: Prebuild shaders

Setup > Build, step 5 (`forza_launcher/shader_prebuild.py`, `ui/pages.py`). Optional; the
game must be built and use Vulkan.

1. Rebuild the listed shaders from the player's files: `media/shaders`, the tracks'
   `bin.zip` (LZX), the update's `media.zip` and the executable image (decoded from
   `default.xex` in memory, `apply_title_update.Xex`). 1.7 s here, pure Python.
2. Write `<title>.xsh` and `<title>.fbo.vk.xpl` to `<cache root>/shaders/prebuild/` (the
   cache root of the launch plan: `--cache_root`, else `<user data root>/cache`).
3. Start the game with the launch plan plus `--shader_prebuild_only=true` and follow its
   `SHADER_WARMUP` lines (`shader_warmup.Monitor`): the progress bar fills once per pipeline
   cache (twice with split rendering). Cancel terminates the game. This run also writes the
   **shader pack** (ReIGlue 0600, the native renderer's M1, [native-renderer.md](native-renderer.md#m1-shaders-translated-at-build-time)):
   the SPIR-V of every stored and listed shader it translated, for this GPU and these settings,
   in `shaders/translated/<title>.<key>.vk.xspv` of each pipeline cache (about 22 MB for the
   list). Later starts take their shaders from it instead of translating them; the game uses a
   pack only when its own build wrote it for its exact configuration. Advanced > *Keep
   translated shaders* (`vulkan_shader_pack`) off: no pack.
4. Write a stamp (`launcher.json` beside the files): launcher version, list SHA-1, GPU
   plugin size and time, the arguments that choose the stores (`--gpu_backend`,
   `--render_target_path_vulkan`, `--fh1_upscaler`, `--fh1_scene_720p_native_hud`,
   `--fh1_native_scene_scale`, `--fh1_output_width/height`, `--resolution_scale`), the
   driver cache files (`shaders/local/*.vkpc`, named after the GPU and driver), whether the
   pack was on and the packs this run wrote.

The card shows Optional, Done (pipelines, "their shaders translated" when the pack is there,
and time), or Recommended with the reason: the graphics settings changed, the launcher or the
game (plugin) was updated, the driver changed (the SDK deletes the driver cache file of an
earlier driver of the same GPU, so the stamp's files are gone), the translated shaders were
deleted, or the pack setting was off at the last prebuild and is on now. Every start imports
the list anyway; the step only moves the compile out of play and the first start, and the
translation out of every start.

The step refuses to run when `--user_data_root` or `--cache_root` is given twice (the
launcher's own plus one in Advanced > extra arguments): the game then ignores its path
options and uses its default folders, so the files would be written where it does not look.

### Current build required (#295)

A game built by an older launcher does not know `--shader_prebuild_only` or patch 0113 and
exits at once. The step therefore asks `builder.build_status` (the Setup page's Build test:
complete build whose id matches the project, title update and CPU target) and, unless it says
"built", does not start the game: the card shows "Rebuild first" and "Rebuild the game first
(Setup > Build): Prebuild shaders needs the current build." When the game still stops early,
the launcher quotes error lines only from a `forza.log` written during that run (modification
time); with no fresh log it says the game stopped right away without writing a log and that
rebuilding usually fixes it, and it reports an unknown-option message from the game's console
output the same way.

### Measurements

RX 9060 XT, RADV (Mesa driver 0x6802001), 8 cores; benchmark lock held, no other game
running, load under 1. Every run starts from an empty cache root and an empty Mesa shader
cache (`MESA_SHADER_CACHE_DIR`: RADV keeps its own disk cache, which otherwise answers
for pipelines compiled by any earlier run). The prebuild run uses the same Mesa cache as
the drive after it, as on a player's machine.

The drive (`live_session.sh` with scripted input, frame statistics `RECOMP_PERF`, the
scenes labelled through the input FIFO as in `perf_scenario.sh`): free roam at the spawn, fast
travel to the Horizon Festival (loading), a car change in Dak's garage to a car the
captures did not use with its five camera views, a drive at the festival with every
camera, photo mode with effects and a photo, a drive. About 4 min 10 s of play after the
title screen. "Pipelines" counts every graphics and compute pipeline the game created in
that time (`OnPipelinesCreated`: real pipelines and the placeholders drawn while one
compiles); hitches are presented frames over 100 ms, stutters over twice the median.

Two batches, each without and with the prebuild. Per scene, the first batch:

| Scene | Without prebuild: pipelines / hitches / stutters / longest frame | With prebuild |
| --- | --- | --- |
| Spawn, 10 s | 4 / 0 / 0 / 66 ms | 0 / 0 / 0 / 35 ms |
| Fast travel to the festival, 35 s | 234 / 4 / 7 / 435 ms | 3 / 0 / 0 / 64 ms |
| Dak's garage, car change, cameras, 76 s | 190 / 1 / 4 / 200 ms | 28 / 0 / 0 / 66 ms |
| Festival drive, every camera, 55 s | 102 / 1 / 5 / 116 ms | 20 / 0 / 0 / 67 ms |
| Photo mode, 51 s | 63 / 0 / 3 / 68 ms | 25 / 0 / 2 / 68 ms |
| Drive, 24 s | 0 / 0 / 0 / 35 ms | 0 / 0 / 0 / 37 ms |
| **Session** | **593 / 6 / 19**, worst 1 % 8.4 fps (travel) | **76 / 0 / 2**, worst 1 % 23.2 fps |

The second batch, session totals: without 608 pipelines, 9 hitches, 29 stutters (longest
frame 233 ms, in the garage); with 83, 0 and 4 (longest 84 ms).

Pipelines the session stored (the player's own store at the end): 566 and 575 without the
prebuild, 48 and 53 with it: the uncaptured car (its cockpit and body shaders) and some
photo effect combinations. `FORZA_STALL` (a frame of the main loop over 200 ms) logged
nothing in any run: on RADV, compiles are short and async compilation draws placeholders
meanwhile, so the cost shows as the hitches and dropped frames above, the 435 ms frame
while the festival loaded included.

| | Time |
| --- | --- |
| Prebuild (the launcher's step, `shader_prebuild.prebuild`), empty caches | 4.3 s both times: shaders rebuilt from the game files 1.7 s, then the game: 838 pipelines in a 1.6 s warm-up |
| First start after the prebuild (838 listed, nothing stored) | warm-up 392 / 388 ms |
| Warm start, own store only (566 / 575 pipelines, after the drive without prebuild) | warm-up 243 / 248 ms |
| Warm start, own store + list (886 / 891 pipelines) | warm-up 410 / 417 ms |

The list costs about 170 ms per start here. On the Windows AMD driver of #167 the warm-up
took 7.2 s for 867 pipelines: that is where Windows and NVIDIA measurements are needed.

## Phase 3: lists from players (#276)

Like Steam's shader pre-caching, the list grows from play, through Forza Live. Hard rule:
nothing from the game is uploaded or distributed, only pipeline descriptions (shader hashes and
decoded render state, the list format, plus for each shader how to rebuild it from the player's
own files: a `file` or `bound` recipe of hashes and vertex layout fields, no microcode). Shader
code always comes from each player's own files. Descriptions do not depend on the GPU, so uploads
from any vendor help everyone.

Data flow:
1. **After a session** (`shader_share.py`, started when the launcher sees the game exit, in a
   background thread): the player's pipeline stores (`<cache root>/shaders/shareable/*.xpso` and the
   split `fh1-live-*/{ui,scene}` ones) are read and decoded with `pipeline_list.stores_to_pipelines`,
   the same code as `pipeline_list.py make`. Only stores of the shipped list's storage version are
   used. Entries already in the shipped list, or already sent to this server (a small record,
   `shader_share.json` in the data folder), are skipped. Recipes are made from the player's own
   `.xsh` and game files (`make_shader_recipes`); pipelines whose shaders cannot be rebuilt from
   them are not worth sending and are noted as done. Up to 3000 pipelines per session, 400 per
   request.
2. **Upload**: `POST /shader-lists/upload` on the server's HTTP port (UDP port plus one), signed
   with the player's XUID and signing key from `live_identity.txt` (an Ed25519 signature,
   `launcher/forza_launcher/live_auth.py`: the key itself is not sent), 8 s timeout. Only when
   Online > Online > **Share shader list** is on (default), and the online mode is the public
   server or the player's own server: never offline, never "This PC". Failures are silent and logged
   to the launcher log; entries that were not accepted are tried again after the next session; it
   never blocks Play.
3. **Merge** (`server/forza_live/content/shader_lists.py`): every entry is validated against the list format
   and its enum ranges, and counted by **distinct players**; an entry is published once at least 3
   players reported it. Caps: 1 MiB and 500 pipelines per request, 5000 pipelines per player and day.
   The recipe most players reported is published for each shader.
4. **Download**: before *Prebuild shaders* writes its files, the launcher fetches
   `GET /shader-lists?version=...` (6 s timeout), checks it with `check_list` (any problem: ignored),
   merges it with the shipped list (`pipeline_list.merge_lists`; the shipped recipe wins a
   conflict) and builds that. Offline, on any failure, or for another storage version it uses the
   shipped list alone. The existing validation still drops entries whose shaders are not in the
   player's files, and a `bound` shader must reproduce its listed hash.

Abuse: a fake player can add entries, never code. An entry costs only prebuild time, and only when
the player's own files contain its shaders. Three accounts are needed to publish one.

Tests: `launcher/test_shader_share.py`, `server/tests/content/test_shader_lists.py`, and an end-to-end run
against a local server with three players (not part of CI).
