# Stock pause contexts and custom menu coverage

The custom pause menu reads FH1 TU4's current action model, including native
localized labels and enabled flags. The normal model builder (`827DE1B0`) and
the Rally model builder (`827D65A0`) retain responsibility for event, party,
progression, hardware and DLC availability. A row that the stock builder does
not publish is not added by the custom menu. Disabled rows remain disabled.
Activation reads the model again; cached tile availability cannot authorize an
action after the context changes.

Home, Cars, Social and Settings retain their IDs and order. Activity and Horizon
pages reuse the existing six tile donors and scrolling tab strip; additional
Horizon rows use a second page. Opening pause with an available Quit Race,
Quit PR Stunt or Finish action selects Activity. Returning from a child retains
the originating page and tile. Native labels preserve distinctions such as
Quit Race versus Retire rather than interpreting both as Leave Session.

## Action checklist

These IDs are from the original TU4 pause builders and dispatcher `827EBEE0`.
The dispatcher takes the action in **r6**, with event **r4=2**; r5 is not the ID.

| Native ID | Stock action/context | Custom destination and routing |
|---|---|---|
| 0 | Quit PR Stunt, when the current mission supplies it | Activity; original confirmation 0 and result handler |
| 1 | Restart PR Stunt, when that mission supports restart | Activity; original confirmation 1 and result handler |
| 2 | Map, when allowed by the current activity | Existing Home map |
| 3 | Resume; also present in the special Rally model | Existing Back/Start resume callback |
| 4 | Co-op Challenges in the applicable multiplayer modes | Horizon; original child route |
| 5 | Message Center when the native gate permits it | Horizon; original message/profile checks and child route |
| 6 | Horizon Rally when offered by the current event/DLC state | Horizon; original Rally eligibility checks and flow |
| 7 | Multiplayer in supported contexts | Horizon; original entry/confirmation, alongside the custom Social entry points |
| 8 | Sponsor Challenges when unlocked | Horizon; original child route |
| 9 | Dispatcher branch exists, but neither pause model builder emits it | Not synthesized or exposed |
| 10 | Restart; native confirmation varies for race, Rivals and Rally | Activity; original confirmation 5, including its native difficulty choice |
| 11 | Photo Mode when allowed | Existing Home tile |
| 12 | My Profile | Existing Home tile and general Settings difficulty availability |
| 13 | Head Tracking when the native hardware/state gate offers it | Horizon; original child route |
| 14 | Quit Race or Retire, according to the current native event/party state | Activity; original label, confirmation 17 and result handler |
| 15 | End Free Roam for an eligible host | Activity and existing Social session page; original confirmation 18 |
| 16 | Finish current event/challenge when offered | Activity; original confirmation 19 and result handler |
| 17 | Quit, with the stock single-player/party context | Y footer; original confirmation 2 outside the desktop-exit context below |
| 18 | Difficulty in the special Rally pause model | Settings; original dispatcher allocates context **22**, preserving its apply/return behavior |
| 19 | 1000 Club when the native expansion gate offers it | Horizon; original content/license checks and challenge flow |

The special Rally builder emits only 3, 18 and 10. The custom menu does not add
a race quit/retire action to that model unless the original builder supplies
one. Normal event, Rivals, PR Stunt, co-op and online host/guest differences come
from their actual rows, not from a host-side recreation of every game mode.
Expansion package detection alone never enables Rally or 1000 Club.

The existing desktop-exit extension is selected only with an available Map row,
known single-player Colorado free-roam presence/region, no race/stunt/Rally-only
rows, and its existing offline,
save-manager and callback gates. Other Quit contexts use the original handler.
This preserves party choices and race-specific confirmations. The existing
save/flush/title completion checks still control desktop closure.

## Confirmation and return contracts

The custom adapter forwards the common native result handler `827618F8` exactly
once. That handler owns the high-numbered content/marketplace results and
delegates the pause-specific results to `827E5CA8`. The adapter records accepted
native child requests (`82F240C8`: pending flag +264, owned parameters +160),
reads the ID through the same virtual getter as `82F28AD0`, and matches both the
returning child's parent and retained parameters. Confirmation choices require
the native 16-byte Result type. Information/difficulty returns do not read a
choice. **0 is a valid PR Stunt confirmation ID.**

No accepted child on a confirmation-only route restores the custom page. A
scoped observer forwards the original Club guard `827540F8` once and detects
its no-child rejection paths. The adapter does not rerun content checks or
infer success from dispatch alone. Follow-up native children retain the hidden
parent, and the native result handler's keep-open flag is respected.
Confirmations that only invoke a bound exit callback recheck that reference
before releasing the composition; if the native handler would no-op, the page
is restored. Stunt/finish handlers retain their separate native activity work.

| Confirmation | Choice | Custom composition handling |
|---|---|---|
| 0, 1, 3, 17, 18, 19, 23 | 0 | Release composition before original event transition |
| 0, 1, 3, 17, 18, 19, 23 | Cancel | Restore page after forwarding original handler |
| 2 (Quit) | 0 or 6 (leave with party) | Release before original quit delegate |
| 2 (Quit) | Cancel | Restore page |
| 5 (Restart) | 0 | Release only after the original restart gate accepts (+468); otherwise restore page |
| 5 (Restart) | 3 (B) | Restore page |
| 5 (Restart) | 6 (difficulty) | Keep parent hidden; preserve the native delegate's full-screen 170 route and return bookmark |
| 16, 20, 21 | Any | Restore after native information/eligibility prompt dismissal |
| 22 (Rally difficulty) | Return | Preserve native apply handler, then restore page |
| 100005, 100007, 100011–100014 | Return | Preserve common native content/marketplace handling; follow any replacement child before restoring |

No custom action directly resets an event, changes rewards, writes a save or
calls a quit/restart delegate in place of the native confirmation.

## Validation and outstanding runtime checks

Focused source tests exercise the actual native dispatch adapter for all 20
native IDs; rejected missing, disabled, duplicate and malformed models; preserved
guest bytes and PPC caller registers; denied Club guard with/without a prompt;
accepted child request IDs/parameter identity and wrong-parent/stale-result
rejection; the production Before/After result lifecycle with keep-open flags,
replacement content prompts, stale callbacks, accepted/rejected restarts and
the delayed Difficulty delegate; contextual labels; absent/disabled DLC rows; Rally difficulty;
modal choice distinctions; spatial navigation and tab
overflow. Existing car access/reload tests remain covered. Source-disabled TU0
and TU4 builds and the enabled TU4 scene translation unit compile locally.

### Partial in-game qualification (2026-10-29)

The tested source was `793171eb97bf340c9fa66650f4d2dbaf19a3982c`, linked
against SDK `ff1db6f6ff64f237ec2dbd28f06680205a6b8b1a`. The Windows host
SHA-256 was `08f82cddf09f8e94cf08d4635d5290b48e857a5245e7c73c317894c852196966`.
Tests used isolated, authenticated profile copies, verified Rally/Club content,
and normal UI input, starting outside Dak's Garage in Colorado free roam.

- Home, Cars, Social, Settings and Horizon tabs rendered and switched normally.
- Rally opened the native single-player exit/entry confirmation. Cancel
  restored the originating Horizon/Rally tile and subsequent navigation worked.
- Native World Map opened and Back restored Home/World Map.
- Native Quit displayed title/desktop/cancel choices. Cancel restored Home;
  accepted title and desktop exits were not exercised.
- With explicit digital D-pad bindings (`U` up, `J` down, `H` left, `N` right),
  `N` moved Rally to 1000 Club. The other three directions were not exercised
  in that tab-switching run.
  Short default stick/chord inputs had not moved focus in the previous run;
  that input-delivery limitation is not classified as a menu defect.
- Selecting Club invoked the native unavailable message requiring a green
  wristband. OK restored the identical Horizon/Club selection; a new `N`
  moved to Sponsor Challenges. This qualifies the eligibility prompt and return,
  not an eligible Club challenge child or a no-prompt rejection path.
- Back resumed the native free-roam HUD. The earlier run also accepted the
  native camera-cycle button after resume; driving motion was not tested.

The two qualification runs ended after 237.750 and 207.922 seconds with no
native fault, no remaining owned processes, and unchanged source saves, assets
and runtime binaries. Their copied profiles remained authenticated; native
autosave changed their file hashes while the checked progression fields stayed
unchanged. Supervisor shutdown is not evidence of native desktop-exit success.

A subsequent 303.422-second race-entry attempt on the same host reached Race
Central through its offered 0-credit map travel. Rivals > Festival Events >
Yellow Wristband Events returned the native offline-service error. Normal Back
returned to free roam. Digital `J` navigated the native map filter, but mouse
click/drag and short stick-key input did not move the native selector to the
nearby Festival event. Its identity, class and sign-up remained unverified.
No race countdown/HUD occurred, so that attempt did not qualify Activity race
selection, Restart or Quit Race. Owned cleanup and all source/runtime guards
passed; the copied profile remained authenticated with checked progression
fields unchanged after native travel, filter changes and autosave.

A further 319.656-second run applied the Festival Events-only filter and
confirmed that normal digital D-pad input moves the native map cursor. `U`
and `N` selected local facilities; `U+N` moved the cursor out of the facility
cluster, and individual `N` inputs advanced it toward a visible event. The
small increments and observation time exhausted the event-entry window before
the marker was reached. Its identity, class, eligibility and travel offer remain
unverified. No event entry or race pause checks ran. Back restored Home/World
Map, then resumed Garage free roam. Cleanup, frozen configuration and source
guards passed; the copied profile authenticated with checked progression fields
unchanged. This qualifies map movement and return only.

A 591.032-second run used the authenticated native save from that map
attempt, with its complete file inventory and all 229 decoded profile fields
frozen before launch. Digital input reached Oakley Blitz. Details identified
Gladstone Dirt Sprint, a two-lap mixed-surface circuit race displaying 100 points
and 3,000 credits as rewards; restrictions required class B or below and AWD.
The current car's drivetrain eligibility was not established. Fast Travel
Anywhere displayed an unlock requirement, so the offer was canceled without
acceptance. Back restored Home/World Map and then the free-roam HUD outside
Dak's Garage. No driving, event sign-up, countdown or race HUD occurred. Five
native still frames were inspected; no video was recorded. The run stopped
early with no native fault or remaining game process. The original seed, prior
run's continuation source, frozen evidence, assets and runtime stayed unchanged;
the new native copy authenticated with checked progression fields unchanged.
That map attempt did not qualify race Activity, Restart or Quit Race.

On 2026-10-29, a 293.407-second run of the same host qualified the targeted
race paths in the **offline introductory Horizon Heats** event. A preserved
ten-file checkpoint matched its original inventory exactly and both profile
and player database authenticated. It loaded beside the native sign-up prompt;
the event card identified Horizon Heats, asphalt point-to-point, C open, with
the current Corrado VR6 C400 eligible. Ordinary X/A entry and Start Race led to
an actual race HUD showing 0% progress and place 8/8. No driving, fast travel,
benchmark control, AI driver, private input or progression edit was used.

- Start opened Activity with Quit Race selected and Restart available.
- Restart's native confirmation canceled with B and restored the selected
  Restart tile. A fresh left input selected Quit Race, proving live navigation.
- Quit Race's native Retire From Event confirmation canceled with B and
  restored the selected Quit Race tile. Back resumed the race HUD and its timer.
- Reopening pause and confirming Quit Race returned to the Horizon Heats
  sign-up area. Pause then opened Home/World Map visibly; right selected My
  Cars, and Back resumed the free-roam HUD. No hidden parent was observed.

Thirteen saved native still frames were visually inspected; no video was
recorded. The owned process closed early with no native fault or remaining game
process. Source checkpoint, prior evidence, assets and runtime hashes stayed
unchanged. The native copy authenticated afterward; checked progression fields
remained 500 credits, zero XP/races and wristband level 0, with no career result
rows before or after retirement. This qualifies the observed introductory
event only. Restart confirmation acceptance, difficulty, later race contexts,
completion/rewards, eligible Club, party behavior and camera/fade remain untested
by this run.

A targeted Restart attempt used a fresh copy of the same ten-file
checkpoint and unchanged host. Window activation failed, and one documented
fresh-window retry returned `GetCursorPos failed: Access is denied. (0x80070005)`.
No test input was sent. The owned process closed after 55.594 seconds with no
remaining game process; source saves, prior evidence, assets and runtime guards
passed, and the copied profile was byte-identical. This is a UI access blocker
of unknown cause, not a Restart result or a game defect.

After the user unlocked Windows, documented window activation and capture
succeeded. A fresh-copy retry of the unchanged host then qualified **confirmed
Restart** in Horizon Heats. Restart > Yes returned to the native Start Race
menu. Starting again reset the race timer from a saved 00:10.833 to 00:00.833,
then it advanced to 00:08.317. Pause reopened Activity visibly, left moved the
selection from Restart to Quit Race, and Back resumed the HUD at 00:20.983.
Confirmed retirement then returned to the Horizon Heats sign-up area.

The retry ended early at 287.297 seconds with no native fault or remaining game
process. Thirteen saved native frames were inspected. Source checkpoint, prior
evidence, assets and runtime stayed unchanged; the native copied profile and
player database authenticated, checked progression remained unchanged, and
career event-result rows remained empty. This qualifies confirmed Restart and
subsequent menu/navigation/resume in the observed introductory event only.

### Remaining runtime coverage

The following contexts remain unqualified and should be tested against isolated
profile copies with source and runtime revisions recorded. The partial results
above do not qualify another event, progression, party or hardware context:

- Career race: extend the Horizon Heats cancel/restart/quit/resume evidence to
  later career contexts; test difficulty child and Back, and
  completion/reward/progression behavior.
- PR Stunt and Rivals: context-specific labels, quit/restart/finish/retire
  confirmations and native destination on each accepted result.
- Rally: its three-row pause model, native difficulty apply/cancel and restart;
  Colorado-to-Rally entry only when the native flow offers it.
- 1000 Club and Sponsor Challenges: available and unavailable content/activity
  states, child Back, native challenge data, and no-child eligibility rejection.
- Message Center, native Multiplayer and Rally entry: Cancel/OK on eligibility
  errors, confirmation cancellation, and native marketplace follow-up returns.
- Multiplayer host/guest: co-op entry, retire versus leave, End Free Roam and
  party-leader choice 6; no offline desktop extension in these contexts.
- Free roam: remaining overflow/held-input cases, native title quit, and
  existing save-confirmed desktop exit.

Camera movement and studio fade remain separate work. No Bucket List action or
second game's resources are included.
