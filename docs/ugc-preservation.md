# Preserving design and vinyl catalogs

`tools/ugc_catalog.py` reads an FH1, FM3 or FM4 `LivCatalog` / `LivGroups` saved-content
package, or an already-extracted catalog. It exports original encrypted payloads, creator
markers, storefront records, previews, and a manifest with SHA-256 hashes. It does not
write to the source save, change ownership/locks, install content, or contact a server.
Save packages, artwork, previews and game key material must remain outside the repository.

```sh
python3 tools/ugc_catalog.py /private/saves/LivCatalog
python3 tools/ugc_catalog.py /private/saves/LivCatalog --export /private/exports/catalog-1
python3 tools/ugc_catalog.py /private/saves/LivGroups --export /private/exports/vinyls-1
```

The destination must be a new directory. The bundle contains one `item-NNNN` folder per
design or group, preserving original member filenames and bytes, plus `manifest.json`.
`source_path` preserves each item's location in the source catalog. A nonempty
`StorefrontID` is recorded as a storefront marker; the exporter does not decrypt it.
Layer counts come from filenames, not a decoded layer stream. A directory input has no
verified game title ID; only a supported STFS saved-content header identifies the source
game. FM3/FM4 content is labelled with its own game and is not advertised as FH1-compatible.

The reader checks STFS data-block SHA-1 hashes, bounds reads and rejects unsafe paths,
links, duplicate paths, oversized content and inconsistent model identifiers. Those block
hashes detect archive damage; they do not verify the author's identity or the Xbox package
signature. An unreadable thumbnail is reported in the manifest while its original bytes
and the livery remain exportable. Cancellation or a failed export removes its partial
output; existing destinations are never overwritten.

## Launcher integration

Open **Designs & vinyls** in the launcher's sidebar. Choose **Open save package** for
a `LivCatalog` or `LivGroups` STFS save, or **Open catalog folder** for an extracted
catalog. The page shows thumbnails, creator markers, car IDs, declared layer counts
and whether storefront records were preserved. Search by title, creator or car ID,
and filter designs or vinyl groups. A missing or unsupported preview does not prevent
preserving the original files.

**Export catalog** saves the entire inspected catalog (including items hidden by a
filter) to a new, timestamped folder under the location you choose. It includes original
payloads and ownership/storefront records, a manifest with hashes, and decoded PNG
previews where supported. **Open export folder** reveals the result. Loading/export
run in a cancellable worker; closing waits for that worker, and a cancelled export
removes its incomplete output. Close the game before opening a catalog.

To import, open the original FH1 **LivCatalog save package**, select a design, and choose
**Import design**. The confirmation names the destination save folder and offline profile
identity. **Back up and import** authenticates the source index, livery and storefront
record using your game executable, verifies the destination career identity, preserves
creator/lock metadata, and merges the selected design into the destination catalog.
Your career and garage are not edited. In-game, use the matching car's saved-designs menu
to apply it. Duplicate designs are refused rather than replaced.

Import requires a previously saved local career and a supported authenticated FH1 package
with its original index/profile ID. Extracted folders, archival bundles, FM3/FM4 designs
and vinyl groups remain export-only. The game and Setup must be stopped. A shared OS
save lock prevents importing while another launcher game session or save operation is
active. Standalone game launches outside the launcher must be closed manually.

The importer snapshots only `LivCatalog`, the only save directory it changes. Verified
backups and SHA-256 manifests live under `<launcher data>/save-backups/catalog-imports`;
**Open catalog backup** reveals the exact backup used. There is no automatic retention
limit. To restore manually with the game closed, move the current `LivCatalog` aside and
copy the backup's `LivCatalog` to the manifest's destination; if `existed` is false, there
was no previous catalog. The importer stages a complete catalog, rechecks the original,
then publishes with a rollback journal. Cancellation before publication leaves the
original catalog untouched; publication finishes without cancellation. An interrupted
publication is rolled back before the next launcher Play/import operation. If the
journal is invalid or recovery cannot verify its backup, Play continues with one
visible warning and a launcher-log entry. The warning names the latest backup whose
manifest matches this destination/profile and whose archived files pass SHA-256
verification, or says that none could be verified. Check the manifest before manual
restoration; the latest verified backup may predate the interrupted import. Recovery
does not discard an unverified journal or backup, and further imports remain blocked
until recovery succeeds. An active import/save lock still blocks Play; filesystem I/O
failures during recovery also stop launch. The lock stays held throughout the game
session, including relaunches, even when catalog recovery only produces a warning.

No ownership is unlocked and no designs are downloaded by this page.

`launcher/forza_launcher/ugc.py` is independent of Qt. `read_catalog` and `export_catalog`
accept cancellation callbacks; export also accepts progress updates. Run them in a
launcher worker. The reader retains source bytes in memory so
an export uses the same snapshot the user inspected. `tools/xdc_decode.py` now exposes
bounded `decode` and `png_bytes` functions using the standard library, avoiding a Pillow
dependency in a packaged launcher. The original command-line decoder still works but
requires a new output filename and reads dimensions from the texture descriptor.

Related PRs inspected on 2026-10-18:

- [#139, save manager and profile editor](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/pull/139):
  full backups, game-session locks and an authenticated career-profile codec. This is the
  foundation for any future importer that changes a save. Its profile codec does not
  directly decode the recovered livery's different encrypted wrapper.
- [#130, photo gallery and launcher layout](https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/pull/130):
  worker-based discovery, preview/export and UI lifecycle patterns; now merged into main.
  Gallery keeps page ID 4, Controls 5 and Saves 6; Designs uses page ID 7.
  The Saves PR's original ID 4 will need reconciliation when it is integrated.

## Recovery findings and remaining work

A privately held, pre-shutdown FH1 catalog was checked with the tool: two paints, including
one with a different creator's `.owner` marker and a `StorefrontID`. A companion vinyl
catalog contains one group. A separate FM3 catalog contains a 22-layer design by filename
with creator and storefront markers; its thumbnails decode too. The FM3 catalog in an
older backup has the same listed entries. The additional FM4 catalog contains one
owner-created paint. No payloads or private source paths are included here.

The recovered FH1 career profile authenticated and decrypted using the codec from #139
and the player's own executable, with the package's profile identity. Further tracing
identified the livery-specific key derivation. `tools/livery_codec.py` authenticates the
outer livery stream, checks the `crlv` header, and verifies a byte-identical no-edit round
trip. It reads key material from the user's own executable and prints only audit metadata.
It does not install, rebind, unlock, export plaintext, or edit a save.

```sh
python3 tools/livery_codec.py /private/saves/design.liv \
  --xex /private/game/default.xex --profile-id 0123456789ABCDEF
```

The identity is the eight-byte save profile ID (the STFS header's profile ID), not
necessarily the creator's online XUID. The stream consists of a 32-byte HMAC-SHA256,
a 20-byte length/IV seed, and AES-128-CBC ciphertext. The plaintext length is the XOR
of the seed's five big-endian words. The MAC covers the seed and ciphertext. Padding
is retained exactly; it is not interpreted as PKCS#7. Disc and TU4 key-table addresses
are supported, and a matching MAC is required before accepting plaintext. Career,
catalog-index and livery streams use different derivations; a career codec alone is
insufficient. The catalog index additionally wraps a deflated SQLite database.

An offline TU4 test used an isolated copy of the recovered save and catalog under the
original save identity. The game listed **Recaro Cossie**, creator **HeavyDarka**, date
**2017-10-26**, two layers, for the **1987 Ford Sierra Cosworth RS500**. After buying
that car with the disposable save's credits, applying the design succeeded and the
RECARO lettering rendered on the car in free roam. The newly saved garage livery
authenticated and decoded to exactly the same plaintext as the recovered catalog
livery. Both disc and TU4 executables produced the same authenticated plaintext and
byte-identical encrypted round trip. The original creator and lock markers were retained.
After saving and exiting single player, the runtime was rebuilt without the temporary
crypto probe and launched as a new process. The Sierra and RECARO lettering persisted
in free roam. No authentication checks were bypassed to load the design.

The launcher importer was subsequently exercised with the original FH1 package and a
separate offline identity on a disposable, progressed profile with the Sierra already
owned. The private fixture was prepared for that identity before running the game;
this is not a career migration feature. Import authenticated both identities, created
a verified catalog backup, and left the career file byte-identical. In-game, selecting
**Load Recaro Cossie** from My Paint Jobs reported a successful application. The game
wrote a new garage livery whose authenticated 524-byte plaintext exactly matched the
source design, while preserving the creator and locked status. After saving and
exiting single player, a new game process loaded the Sierra and its artwork from disk.

This establishes that design's cross-identity import on the tested offline TU4 runtime.
Vinyl groups, tunes, other Forza games and archival-bundle import remain unsupported.
Preservation bundles remain explicitly `installable: false`; importing requires the
original FH1 package and its authenticated index. Keep original creator attribution
and distinguish historical content from new uploads; never count a successful protocol
response alone as an in-game restoration.

Validation: synthetic tests in `launcher/test_ugc.py` cover a constructed STFS package,
damaged data blocks, wrong game IDs, traversal/links, export rollback, source preservation,
thumbnail dimensions/pixels and invalid compression. Private real packages were exported
and their original member hashes compared with the earlier read-only extraction.
`tools/test_livery_codec.py` adds synthetic authentication, tamper, format, length,
identity-derivation and no-edit round-trip checks, plus a published NIST AES-CBC vector.
No game keys, save bytes or recovered artwork are test fixtures.

### Windows package follow-up (2026-10-22)

Integrated main `8c20da9` without conflicts, retaining catalog recovery warnings and
the shared game/import lease. The full Windows launcher suite passed: 590 tests,
37 skips; all nine synthetic livery-codec tests passed. A fresh PyInstaller 6.22.3
build and Windows release-folder assembly completed successfully.

The assembled frozen launcher ran a private, entirely synthetic catalog fixture
through the actual Qt import worker. The destination payload authenticated for its
new identity, creator attribution and the lock remained, a catalog backup manifest
was created, and the disposable career remained byte-identical. A malformed journal
produced the verified-backup warning while allowing a harmless frozen `--version`
process to start; the session lease still blocked another import until cleanup.
The Designs page and import/backup controls were inspected at 900 by 620 using Qt's
offscreen platform with the Windows Segoe UI font explicitly registered for capture.

This checks the packaged modules, crypto/SQLite dependencies, worker and Windows
file-lock behavior. It does not repeat real-package import or in-game application,
which retain the earlier macOS/TU4 provenance above. No game/GPU session, real profile
or shared SDK installation was used in this follow-up.
