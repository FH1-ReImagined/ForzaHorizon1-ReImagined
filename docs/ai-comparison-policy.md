# AI comparison isolation, draft

The former rubber-banding option rewrote a driver's configuration fields using a
policy credited to Pinyon Shift. The replacement removes that rewrite entirely.
It leaves database/configuration values, difficulty ranges and temperament data
untouched, and changes the comparison inputs at five verified call sites instead.
The research citation and existing license notices remain during the overall audit.

The setting is still fixed when the process starts. `game` uses the original
behavior. In `off`, single-player relative-performance code receives no human
comparison target. Original query functions still execute first, preserving their
side effects. Other query callers, online race type 6, online free-roam type 18,
and an unavailable race-settings pointer receive the original result.

| Caller use | Base return address | TU4 return address | Query |
| --- | --- | --- | --- |
| Rubber-band update | `82B93A84` | `82C520CC` | Leading human |
| Rubber-band eligibility | `82B84384` | `82C4297C` | Leading human |
| Catch-up update | `82B84654` | `82C42C4C` | Leading human |
| Relative torque selection | `82B90FA4` | `82C4F5EC` | Trailing human |
| Relative maximum-skill selection | `82B910CC` | `82C4F714` | Trailing human |

The query entry points are `82B73600`/`82C31D60` and
`82B736A0`/`82C31E00`, respectively. These are interface facts read directly from
the local base and TU4 XEX branch instructions. The four caller functions have
matching instruction counts, matching conditional branches and matching
controller-relative memory operands across the two builds; relocation-normalized
instruction streams also match. This comparison is implementation evidence, not
a license-clearance statement.

The game's rubber-band eligibility function returns false when there is no
comparison target, allowing its ordinary exit/reset path to run. Its relative
skill function skips maximum-performance selection and follows the normal skill
selection path. The catch-up updater's no-reference branch returns immediately,
so it would preserve stale live blend state. At that one verified call site the
replacement clears controller byte 168 and skill-state float 140 before supplying
the no-reference result. These are live state fields, not the old configuration
thresholds or factors. The controller is in the caller's preserved `r31` register.

Scope matters: these queries are also used by other game systems. Filtering by
query identity and exact return address preserves every other caller. The code
does not hide players from steering, collision avoidance, ordinary race ordering
or unrelated difficulty decisions. Direct grid/rival boost code and post-race
skill adjustment are not intercepted.

Contracts cover both executable variants, every authorized call site, wrong query
identity, adjacent/unknown return addresses, stock mode, online exclusions,
unavailable race settings, and precise/idempotent stale-state cleanup. The game
hook passes production syntax checks for both title-update settings.

Runtime qualification remains outstanding: career races and free roam must show
normal driving without relative speed/skill changes, while online mode and stock
mode remain unchanged. Pausing, race restart, controller reuse and replay flows
must not preserve stale catch-up state. Earlier performance measurements in
`playing.md` exercised the old config-rewrite mechanism and do not qualify this
replacement. In off mode, the trace's gap field now reflects the isolated
comparison state rather than a measurement of actual player distance.
