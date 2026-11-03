# Notes for coding agents

These apply to anyone working in this repository with an AI coding agent (Claude Code, Cursor,
Codex and others), as well as to people. [CONTRIBUTING.md](CONTRIBUTING.md) has the full
workflow.

## What to work on first

Issues are the backlog, and their `priority: N` label (1 is highest) sets the order.

1. **Bugs before features.** Crashes, regressions, and anything that stops a player from
   playing come first.
2. **Your own assigned bugs.** Before starting a feature, check the open `priority: 1` and
   `priority: 2` bugs assigned to you:
   `gh issue list --assignee @me --label "priority: 2" --label bug`. Take those first,
   unless an issue or the maintainer says otherwise.
3. **Players waiting on you.** Where a player answered last on one of your issues (a log,
   a test result, a question), reply before starting new work.
4. **Features** need an issue with a priority, and the higher-priority bugs in the same area
   done or in hand. GPU-vendor bugs carry an `nvidia` or `amd` label; they go to whoever has
   that hardware.

## How to work

- Comment on the issue and mark it `WIP` (label, a `[WIP] ` title prefix and an assignee)
  before starting, so work isn't done twice.
- Keep fixes and features in separate PRs, so the small, safe part can merge first.
- Every fix says how it was verified: the test, the measurement, or the before/after in the
  game.
- Never commit game files, extracted data, or screenshots of the game.
