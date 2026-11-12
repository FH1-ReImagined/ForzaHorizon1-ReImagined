# Startup shader warm-up

The game already waits for the SDK to rebuild previously stored shaders and
pipeline descriptions before resuming the guest. The launcher now shows this
stage as **Preparing cached shaders**, with a completed/total pipeline count on
Vulkan. Direct3D 12 reports the beginning and end of its existing preload stage.
The normal Stop button remains available. Older SDKs that do not report progress
keep the existing launcher behavior.

This prepares **previously encountered pipelines**. An empty cache has nothing to
warm, and a new race, car, effect or graphics configuration can still introduce
new shader combinations during gameplay. No game shaders or driver binaries are
bundled or downloaded by this feature. On Vulkan, Setup's optional **Prebuild
shaders** step adds the pipelines of a shipped list (hashes and render state; the
shaders are rebuilt from the player's own game files) to every warm-up:
[shader prebuild](shader_prebuild.md).

## Local Vulkan driver cache

In addition to the existing shader/pipeline descriptions, Vulkan retains the
driver's pipeline data in `cache_root/shaders/local/*.vkpc`, one entry per
stored pipeline description. Each pipeline is created with a `VkPipelineCache`
of its own, seeded with its entry. Then:

- the driver found the pipeline in the entry (Vulkan 1.3 pipeline creation
  feedback, or without it, the cache's data is unchanged): the entry is kept
  ("created from stored driver data" in the log);
- the driver reports it did not use the entry, or without a report, the data
  grew past 1.5 times the size of a fresh entry: the cache now holds the unused
  entry and the new pipeline. The pipeline is created once more with an empty
  cache (the driver finds it in its own caches by then) and only that data is
  kept ("stale entries rebuilt");
- otherwise the new data replaces the entry.

After warm-up, entries of descriptions that are no longer in the pipeline store
are dropped, and the log says how many pipelines came from stored driver data
and how many were compiled. Files of an earlier driver or game version on the
same GPU are deleted. The file follows the store: the same pipelines give the
same size.

The first version seeded a single cache with the whole previous file and saved
that cache. Drivers never drop data from a cache, so every pipeline the driver
did not find in it again (after a translator change, or with a driver that keys
it differently) was stored a second time at every start, and dead data was
never dropped. In #167 an RX 6700 XT (AMD, Windows) went from 39.6 MB to
55.9 MB during the warm-up of the same 867 pipelines, and saves stopped without
a word past 64 MiB (#247).

The cache is saved after warm-up, and periodically when new pipelines have been
created. Shutdown also attempts to save pending data. Disk writes use a temporary
file and replace the previous file only after the write succeeds. Files stay within
512 MiB. If all entries do not fit, whole entries are retained in description-key
order, skipping those that exceed the remaining space. The log reports the saved
count and warns once per session about omitted pipelines, which may compile again
next time. Individual entries over 16 MiB remain unsupported.

This bounded save avoids losing all persistence when NVIDIA driver data exceeds
the limit (#406). It does not explain the driver's large entries or guarantee that
all pipelines can be retained; live NVIDIA restart testing is still required.

The **Keep compiled Vulkan pipelines** advanced setting is on by default and
requires **Keep a shader cache** and the Vulkan backend. Changing it requires a
restart. Turning it off leaves the original shader/pipeline description preload
in place. It does not delete existing cache files.

Files are local to the GPU vendor/device, driver version, pipeline cache UUID and
SDK pipeline/translator format versions. Incompatible and oversized files are
ignored; in a damaged one, the entries before the damage are kept (each entry
has its own checksum). Driver rejection of an entry retries with an empty cache;
failure to create or write the cache falls back to normal compilation. The data
is opaque and should not be distributed between players.

Each pipeline's cache is used by one creation thread only. A short lock guards
the entries; file encoding and writes run outside it, on the storage worker
during play. No extra GPU idle wait is introduced. This is not a claim that
runtime shader stalls disappear.

## Pipeline store

The pipeline descriptions (`shaders/shareable/<title>.fbo.vk.xpso`) are filtered
at start: records this device cannot create are left out, and a record that
fails its hash ends the store. When anything was left out, the store is
rewritten with exactly the kept records, in their order, and the log names what
was dropped and why (patch 0102). Before, the file was truncated to the number
of kept records: the rejected ones stayed, and as many valid records were cut
from the end. Those are the newest pipelines, so they compiled again in the same
places every session, until the cache was deleted. A GPU switch, a driver
exposing fewer features or an imported pipeline list triggers it. The header
holds the description and shader modification versions separately, so raising
either one starts a new store; a store with the original header and the same
versions is kept and converted.

## Validation

- Launcher unit/Qt tests cover bounded progress parsing, old and rotated logs,
  partial lines, worker ordering, progress visibility and setting dependencies.
- `tools/test_driver_pipeline_cache.cpp` tests identity rejection, every single
  byte corruption and truncation (only whole entries before the damage are
  read), size limits (an oversized file is not read, a bounded save reloads a
  stable subset over repeated saves, with exact-fit and one-byte-short boundaries),
  failed writes and concurrent atomic replacement. It also
  models a driver that finds a third of the pipelines under a new key at every
  start: one seeded cache grows from 13.8 to 36.8 MB over six starts, while the
  per-pipeline entries stay at 13.8 MB.
- Windows RTX 5060 Ti, 1440p/2x scene, three prebuild-only starts on an isolated
  private host fixture: the first compiled 330 scene pipelines and saved 252 in
  535,444,993 bytes (510.6 MiB), omitting 78 at the 512 MiB cap. Both closed-process
  restarts loaded and reused all 252 retained pipelines; the omitted 78 compiled
  again. Cache identity and file bytes stayed identical across all three starts.
  This validates bounded persistence for that fixture; gameplay performance and
  the cause of large driver payloads were not assessed.
- `tools/test_pipeline_store.cpp` writes stores with valid records and records
  the device rejects (the last ones among them), damaged and partial records,
  the original header and other versions. Every valid record survives, in
  order, and the file holds exactly those; each version is checked on its own;
  a failed rewrite leaves the store as it was. CI runs both tests against the
  patched SDK on Windows and Linux.
- The Windows SDK builds with both Vulkan and Direct3D 12 enabled.
- Game runs for #247 on an RX 9060 XT (RADV, Mesa 26.2.1), scripted free roam
  (`tools/perf_scenario.sh freeroam`), three starts on a new cache each, before
  (one seeded cache, truncating store) and after:

  | | Start 1 | Start 2 | Start 3 |
  | --- | --- | --- | --- |
  | Before: `.vkpc` after the session (bytes) | 2,461,912 | 2,507,400 | 2,507,576 |
  | Before: warmed / created in play (free-roam scene) | 0 / 673 (162) | 359 / 6 (2) | 362 / 8 (6) |
  | After: `.vkpc` (bytes, pipelines) | 3,510,468 (361) | 3,838,616 (392) | 3,922,504 (400) |
  | After: warmed (from stored driver data) / created in play (free-roam scene) | 0 / 686 (185) | 367 (361) / 51 (48) | 393 (391) / 13 (11) |

  RADV kept the single cache stable while the pipelines did not change. The
  per-pipeline file is about 40% larger there: shaders that pipelines share are
  stored with each. The drive is not fully repeatable: the second "after" start
  met 24 pipeline pairs the first had not (22 of them met by the first "before"
  start); every stored pipeline was warmed.
- Every SPIR-V module changed by a test-only build, then changed again, then
  restored: before, the file grew from 2,507,576 to 2,542,128 and 2,575,252
  bytes and stayed at 2,575,368 after the restore (the dead data stays). After,
  it stayed at 3,922,504 to 3,922,560 bytes, with 400 stale entries rebuilt at
  each change, and the next start took all 400 pipelines from stored driver
  data. RADV adds only a small pipeline object per new key; the AMD Windows
  driver in #167 stores about 46 KB per pipeline.
- Rejected records: 12 copies of stored pipelines turned into triangle fans
  (which `ArePipelineRequirementsMet` refuses on every device), spread over a
  store of 367 from the first record to the last. Before: after warm-up the
  store held 11 of the rejected records and had lost the 11 newest valid ones.
  After: the log listed the 12, the store was rewritten with all 367 in order
  (and its header converted), all 367 were warmed, and the next start warmed
  them again from stored driver data, dropping nothing.
- The first version (one seeded cache), on a local RTX 5060 Ti at native
  2560x1440, loaded 410 stored Vulkan pipeline descriptions. The first start supplied no application cache and saved a
  15,980,775-byte driver payload. The next start supplied that payload and
  completed the preload again. Warm-up logged 801 ms and 296 ms respectively.
  Another game was running, and the driver already had its own cache: these
  timings are a smoke test, not an isolated speedup benchmark.
- A third start with a deliberately truncated local cache ignored it, rebuilt
  the known pipelines and replaced the damaged file successfully.
- Khronos synchronization validation reported no errors during warm-up. After
  warm-up it reported `VUID-vkQueueSubmit-pSignalSemaphores-00067` in presentation.
  The presenter is unchanged here; its semaphore ownership fix is carried
  separately in patch 0076 on the renderer experiment branch. This PR does not
  claim a validation-clean gameplay session or include that separate fix.

No first-encounter stutter, unseen-pipeline coverage, or all-driver performance
improvement is claimed by these checks.
