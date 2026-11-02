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
  plus `fetch` / `export` / `nop` fields. `missing`: not in the game files.

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
   needed: the key holds the guest's format). **Open**: Windows NVIDIA and the Steam Deck
   captures (merge them with `pipeline_list.py merge`; the fields mean the same on every
   compiler), and places only "Fast Travel Anywhere" reaches offline.
3. Launcher: the "Prebuild shaders" step in Setup, offered again after a driver,
   launcher or graphics-setting change. Progress and cancel, using the existing warm-up
   progress lines. **Done.**
4. Measure: hitches per session (`FORZA_STALL`) on a fresh cache, before and after, on
   AMD and NVIDIA. Also prebuild time and warm start time with the full list. **Done on
   Linux AMD; open on NVIDIA** (and Windows AMD, where the warm-up was slow in #167).
5. Lists uploaded by players through Forza Live. The format holds no game content.
   **Done**: [phase 3](#phase-3-lists-from-players-276).

## Phase 2: the prebuild (Linux, AMD)

Done on Linux: AMD Radeon RX 9060 XT (RADV, Mesa driver 0x6802001), 8 cores. Windows and
NVIDIA captures and measurements, and the Steam Deck, are still to do (plan above).

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

`launcher/forza_launcher/data/pipelines-4D5309C9.json`: 1190 pipelines and 971 shader
recipes, storage version `fbo:20260228/20261018`. 430 KB of text, one pipeline or shader
per line (43 KB gzip): small enough for git, and a new capture changes only the lines of
new pipelines. `check` passes: hashes, decoded render state, vertex layout and export
fields only.

Captured on Linux (RADV) with scripted runs (`live_session.sh` and the autopress FIFO,
scratch copies of a free-roam save, offline), each into its own cache root:

| Run | Store | Pipelines |
| --- | --- | --- |
| Free roam, every camera (5 views and look back), Dak's garage with four car changes (the cockpit view of each), photo mode with every effect slider and a photo, the pause menu pages, into the night (with the exploration that found the menus) | main | 780 |
| From the spawn to the Autoshow showroom: several cars, each from three views | main | 610 |
| The GoPro Millennia Masters festival race at the spawn: sign-up, intro, 60 s of racing | main | 504 |
| A new profile's opening drive | main | 411 |
| Free roam near the spawn (a failed fast-travel run) | main | 278 |
| SMAA at 1440p: the first run above, and the race | ui, scene | 415 + 643, 316 + 473 |
| SMAA at 1080p, the race: the HUD raster is scaled (FH1 fractional raster, modification bit 63) | ui, scene | 315 + 480 |

Partitions in the merged list: 445 pipelines in all three, 247 main and scene, 154 main
only, 289 ui only (281 of them the fractional raster variant), 25 scene and ui, 22 main and
ui, 8 scene only. A pipeline seen only without split rendering is also imported by both
split caches (`split_partitions`): which of the two would draw it is unknown. With SMAA at 1440p
the HUD cache imports 634 pipelines and the scene cache 850 (the 269 fractional variants
are dropped there, the raster is not scaled).

Shaders: 524 `file`, 443 `bound`, 4 `missing`; 1148 of the 1190 pipelines (96.5%) can be
rebuilt from the player's files. The rest compile in play, as before.

Coverage of earlier play (phase 1 stores, older versions, so compared by shader hashes and
render state without the modification bits): 759 of the 940 pipelines of the long-played
main cache (80.7%), 903 of the 1612 of all 347 stores (56%, many of them other split
settings and older SDK versions).

Not reachable offline: "Fast Travel Anywhere" is a Marketplace unlock (the map offers it,
then says Xbox LIVE is needed), so the runs reach the spawn area, the festival (fast travel
to Dak's garage and the Autoshow is free) and the race track. Other places compile in play
until a longer capture (or player lists, plan item 5) covers them.

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
   cache (twice with split rendering). Cancel terminates the game.
4. Write a stamp (`launcher.json` beside the files): launcher version, list SHA-1, GPU
   plugin size and time, the arguments that choose the stores (`--gpu_backend`,
   `--render_target_path_vulkan`, `--fh1_upscaler`, `--fh1_scene_720p_native_hud`,
   `--fh1_native_scene_scale`, `--fh1_output_width/height`, `--resolution_scale`) and the
   driver cache files (`shaders/local/*.vkpc`, named after the GPU and driver).

The card shows Optional, Done (pipelines and time), or Recommended with the reason: the
graphics settings changed, the launcher or the game (plugin) was updated, or the driver
changed (the SDK deletes the driver cache file of an earlier driver of the same GPU, so the
stamp's files are gone). Every start imports the list anyway; the step only moves the
compile out of play and the first start.

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
   Settings > Online > **Share shader list** is on (default), and the online mode is the public
   server or the player's own server: never offline, never "This PC". Failures are silent and logged
   to the launcher log; entries that were not accepted are tried again after the next session; it
   never blocks Play.
3. **Merge** (`server/forza_shader_lists.py`): every entry is validated against the list format
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

Tests: `launcher/test_shader_share.py`, `server/test_forza_shader_lists.py`, and an end-to-end run
against a local server with three players (not part of CI).
