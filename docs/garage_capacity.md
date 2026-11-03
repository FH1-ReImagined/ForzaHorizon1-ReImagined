# Garage capacity investigation

Status: 1,000-car implementation validated at the native TU4 purchase/refusal
boundary and across normal save/restart, 2026-10-26.
The audit started at main `0e1717ba898bc4fc086b2cb51dcd7721a9445e8a`.
The existing native pause-menu work is separate.

## Confirmed stock behavior

The stock garage limit is **550**, including additions waiting in the native
queue. TU4's full predicate is `8259DC10`; the matching disc/TU0 predicate is
`8252FCD0`. Both read the queue's begin/end pointers at object offsets 68/72,
divide the byte difference by eight, add the database row count, and report full
at 550 or more. The TU4 count helper `8259DB60` executes a count query against
the garage object's table name. A higher limit must retain pending additions in
the calculation: checking only rows already committed permits overbooking.

All nine direct TU4 predicate call sites have been enumerated:

| Caller | Call site | Observed use |
| --- | --- | --- |
| `825C8478` | `825C84B4` | Central native car-add routine; rejects a full garage before allocating a car and ID. |
| `825D1918` | `825D193C` | Full-garage replacement path: selects an existing car, removes it, then adds car 309. Its higher-level trigger still needs classification. |
| `82682E30` | `82682F48` | CarFlow screen with the localized buy-car/full-garage message. |
| `8273D808` | `8273D878` | Barn-find/workshop flow with a full-garage message. |
| `827A20A8` | `827A28A4` | Message Center/gift preparation flow with a full-garage message. |
| `8282F3E0` | `8282F468` | Tombola flow: stores full status and controls notifications. |
| `82852790` | `828527C0` | Car transition that selects a cash-prize/full response; exact screen identity remains open. |
| `828E4710` | `828E4750` | Active-profile wrapper returning the full predicate. |
| `82918B68` | `82918BDC` | Acquisition eligibility gate that also checks storage-device free space. |

The last caller's separate 2 MiB device-free-space requirement is not a garage
row limit. Its chain reaches `XamContentGetDeviceData`, via volume method
`82D208B0` and import thunk `82B2AFF8` / `832B863C`. Preserve this check.

A second literal 550 occurs in `82E2C780`, which calls `82E3E4E8` to allocate
550 elements of 240 bytes and 100 elements of 60 bytes. RTTI identifies the
owner as `CTrackPresentation`, with Default, Null and Unified presentation
subclasses. This is a track presentation pool, not evidence of garage backing
storage, and must not be changed as part of a garage limit patch.

## Storage, IDs and thumbnails

The garage object constructor `825B4970` allocates no fixed 550-car array. It
initializes a dynamic pending vector at offsets 68/72/76. Its destructor
`825B49E8` destroys that vector. The drain routine `825C9318` pops an eight-byte
entry before forwarding it through the native add path, so a drained entry is
not counted twice by the full predicate. Queue insertion/growth still needs a
complete audit.

Native IDs are database-backed. `825B18F8` considers both `LastUsedGarageId` and
the maximum saved garage ID, advances the counter, and updates the database.
`825BFE60` constructs the native row and inserts it. This does not establish
safe behavior at integer exhaustion; the launcher already checks its supported
signed ID range before adding cars.

The audited car-list loader `8253D7B8` sizes an 80-byte-per-row vector from the
query's row count, then iterates results. Its resize/growth helpers
`8253CAC8` / `825372C8` use dynamic allocation with overflow checks. This path
has no observed 550-element allocation; other list/sort/filter paths remain to
be checked and measured.

Thumbnail names are constructed through `825B60C0`. Cleanup `825C1630` queries
saved IDs in order, builds a dynamic set, enumerates thumbnail files, and removes
unreferenced files. Native removal `825B1290` deletes the car row and its
purchased-parts rows and handles its thumbnail. Rendering, cache pressure and
delete/re-add behavior above 550 still require runtime verification.

The profile-volume constructor `825EFD28` consumes filename/size pairs from
`83305074`. Initializer `83221270` assigns `PlayerDatabase` to the second pair,
whose size is `0x600000` (6 MiB). Profile loading `82583178` and volume creation
`82565888` use this configuration. This agrees with the copied baseline's
6,291,464-byte outer save file (6 MiB plus its eight-byte header).

The authenticated baseline contains three garage rows in a 29,696-byte SQLite
database. This is not a worst-case headroom measurement. Purchased upgrades,
career progress, strings, and compression affect the eventual save size.
The existing launcher encoder preserves the allocated container size and
refuses an edit that will not fit. Its 32 MiB decode safety ceiling is not the
native save allocation and must not be used to justify a larger garage.

The original authenticated unicorn editor had no logical row check. The
implementation below adds the 1,000-row guard while preserving its
game-data-derived stock builder, authenticated decode/encode, ID guard, full
backup, concurrency checks and atomic replacement. Native boundary profiles
must use supported native additions rather than arbitrary garage-field edits.

## 1,000-car implementation and evidence gates

The requested limit is **1,000** by default. `src/hooks/garage_capacity.cpp`
replaces the central full predicate for TU0 and TU4 and calls the executable's
original database-count helper. It snapshots the native pending vector before
that call, matching the original order. `src/platform/garage_capacity.h` counts
stored and pending cars with wide arithmetic and rejects malformed vector
descriptors. There is no unbounded setting.

The authenticated launcher editor uses the same 1,000-car limit. It counts
rows, including repeated models, and refuses the entire missing-unicorn batch
before modifying the database if that batch would exceed the limit. A full
garage already owning all seven cars remains a no-op. Existing ID, container
size, authentication, backup and atomic replacement checks are retained.
Native storage-space checks, add/remove functions and save functions remain
in place. Replacing the central predicate covers all nine observed callers.

Native TU4 validation used disposable copied profiles and ordinary controller
input. Starting with 999 cars and 37,780 credits, an Autoshow purchase of the
15,000-credit Volkswagen Beetle created exactly garage ID 1,000 (car 1523),
advanced the stored ID counter to 1,000, and left 22,780 credits. The next
purchase displayed the native full-garage message before a payment choice;
credits remained unchanged. Normal Quit returned to the main menu before the
host window was closed. Read-only authenticated profile/database inspection
confirmed all previous 999 ID/model pairs and the one new car.

A fresh process loaded that saved profile into free roam with the Beetle.
The native Stats screen displayed **NUMBER OF CARS IN GARAGE: 1,000**, and the
HUD showed 22,780 credits. A second normal Quit and host closure were followed
by authenticated inspection: all 1,000 ID/model pairs, the ID counter, credits,
and the original 6,291,464-byte container allocation were preserved. Both hosts
exited with code zero and empty owned jobs. The observer reported a partial
memory-read error during host teardown; raw records retain that error, while
closure and save correctness were checked independently. Original source
profiles remained unchanged.

This runtime check used a private capture/input derivative of the production
build with the same capacity implementation, immutable SDK121 and verified
unchanged generated code. It qualifies the TU4 native save/acquisition flow
and ABI, without making a current-renderer performance claim. TU0 hook behavior
is covered by the compiled fixture, not a native TU0 play session.

Practical headroom samples at 1,000 cars used a 543,744-byte SQLite database
and about 44 KiB of the compressed container for repeated stock models; a
seven-model sample used about 65 KiB compressed. A legitimate purchased-parts
checkpoint and the schema were also inspected. These are bounded samples,
not a worst-case guarantee for arbitrary purchased parts, career history or
strings. External thumbnails and liveries are separate files. The encoder's
allocation guard and native device-space check remain intact.

Further coverage can exercise gift/reward paths, sustained pending queues,
list sorting/filtering, thumbnail pressure, remove/re-add and extensively
modified careers. Those paths share the central predicate, but the native
purchase/save/restart result does not establish every UI or performance case.

Private decoded binaries, generated function extracts, profile copies, hashes
and audit metadata stay outside Git under `build/garage-capacity-20261025`.
Local validation on 2026-10-26: all **19** `launcher/test_garage.py` tests passed
with no skips. The SDK-free capacity fixture compiled and passed, including
stored/pending boundaries, integer overflow and invalid vector descriptors.
The production hook also compiled and executed against the installed runtime
SDK for **TU0 and TU4** with a stubbed SQL count helper. Those checks verify
big-endian descriptor reads, pending snapshots and caller context preservation.
Authenticated synthetic saves cover additions through slot 1,000 and refusal
without writes above the limit. The native TU4 purchase/refusal/save/restart evidence is recorded above; the
synthetic and stubbed checks cover additional policy and ABI boundaries.
