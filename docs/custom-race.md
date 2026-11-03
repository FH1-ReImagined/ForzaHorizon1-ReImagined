# Custom Race

Replay a festival or street race you have already won with your own settings: car
class, laps, number of opponents and time of day. Rewards are paid as for a normal
run of the event.

Title Update 4 only. Off by default.

## Playing

1. In the launcher, enable **Settings > Gameplay > Custom Race** and set
   **Settings > Online > Sign in to Xbox LIVE: off**. Custom Race is offered only
   offline, the same gate as [car tuning](car-tuning.md).
2. Drive to a festival race (point-to-point or circuit) or a street race you have
   already won and open its title card.
   Map details only offers **Set Route**; drive to the venue to use Custom Race.
3. The footer shows **Custom Race** on Select. Press Select.
4. Choose:
   - **Car Class**: E, D, C, B, A, S, R3, R2 or R1 (defaults to the event's class).
   - **Laps**: 1 to 10, circuits only (defaults to the event's laps).
   - **Opponents**: 1 to 7 (defaults to the event's grid).
   - **Time of Day**: Event default, Dawn, Morning, Midday, Afternoon, Sunset or
     Midnight.
5. Press **A**. Car registration opens with only your cars of the chosen class;
   **Buy New Car** lists the dealer's cars of that class. Pick a car and the race
   loads.
6. Press **B** on the Custom Race page instead to go back to the title card with
   the event's normal settings.

The settings last for one race. Returning to free roam, cancelling, or leaving the
card restores the event as shipped.

Not offered for events you have not won, for other event types (showcases,
rivals, PR stunts, online and Co-op Festival), or while signed in to Xbox LIVE.

## How it works

Source: `forza-recomp/src/hooks/custom_race.cpp` (hooks) and
`forza-recomp/src/platform/custom_race.h` (option lists, clamping and the SQL, with no
SDK dependency).

- **Title card** (`CCareerEventDescription`): the bind hook (`sub_82733A68`) reads
  the event id and checks the native career-entry transition (absent on map details),
  the event type, the win (`sub_82522108`), the offline gate
  (`pause_car_access::Allowed`) and the cvar. If all pass, it adds a Select prompt in
  footer slot 13 when that slot is free. The input hook (`sub_82738948`) opens the
  page on Select. The destructor (`sub_82727530`) ends any pending hand-over.
- **Page**: built on the difficulty-style screen that the in-game Graphics menu also
  uses. `graphics_menu.cpp` owns its four hooks and offers each call to
  `custom_race::Try*` first.
- **Shadowing**: on A, the event's rows are shadowed by `TEMP` tables, which SQLite
  searches before the main database. The tables are `Events`, `Races`,
  `EventParticipants`, `EventRestrictions` and `EventRecommendedCars`. As a result,
  the title card, car registration's class filter, the dealer, the race setup and
  the CPU grid all see the custom values; other events are copied unchanged.
  - The event's restriction row is kept with an empty field mask, because car
    registration crashes without it.
  - Laps change only for circuits.
  - Opponents reuse the event's named drivers in order, in shuffled installed cars of
    the class.
  - `DisarmStatements()` drops every table again.
- **Hand-over**: once the page closes, the card is active again, and one A press is
  added to player 1's pad (`CustomRacePad`, from `kernel/input_autopress.cpp`). That
  press runs the card's own path into car registration and the race.
  - The press is added only while the career title card is the active screen. The
    window (150 ms) opens when the page hands input back to the card. It closes as
    soon as the card's input stub (`sub_82738948`) takes the A, or when the card's
    destructor runs.
  - It is not added while another overlay blocks the pad (`block_all`).
  - If the card never enters the event, the Custom Race is cancelled after a few
    seconds.
- **Cleanup**: the shadow is dropped on B, when the card binds again without a
  pending launch, when the hand-over fails, and once the player is back in free
  roam.

`FORZA_CUSTOM_RACE_TRACE=1` logs the gate inputs, the SQL and the hand-over.

## Tests

`forza-recomp/tests/custom_race_test.cpp` covers:

- Statement order: the drops first, the drivers before `EventParticipants` is
  shadowed, and each copy before its update.
- Clamped values: class, laps, opponents and an unlisted time of day.
- The laps update for circuits only.

CI runs the test on Linux:

```sh
g++ -std=c++20 -O2 -Wall -Wextra -Werror -I forza-recomp/src \
    forza-recomp/tests/custom_race_test.cpp -o custom-race-test && ./custom-race-test
```

`forza-recomp/tests/custom_race_context_test.cpp` compiles the TU4 hooks against
the SDK with synthetic guest memory and checked native-call boundaries. It checks
that remote map details adds no Custom Race footer, cannot arm shadow tables,
and cancels a stale pending hand-over with all seven drops while preserving the
native input call. It does not qualify venue entry or an actual race.

CI runs both tests through `forza-recomp/tests/custom-race` in the Linux SDK job,
after its SDK setup and even on a cache hit. The context fixture includes the
actual hook's SDK headers, so it cannot use the earlier SDK-free g++ step.
