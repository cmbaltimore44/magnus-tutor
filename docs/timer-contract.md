# Focus timer contract (Magnus ⇄ Magnus Tutor)

There is exactly one focus timer, and Magnus owns it. The tutor displays it and
sends commands through `magnus timer …`. The tutor never writes the timer file,
never writes `focus_sessions`, and never talks to Supabase, so focus minutes are
logged once, by Magnus.

## Files (`~/.config/magnus/`, or `$XDG_CONFIG_HOME/magnus/`)

### `timer.json`: the timer (schema 1)

Written only by Magnus (the TUI or `magnus timer`). Every write takes a lock
(`timer.lock/`, a directory created atomically, stale after 5 s), writes
`timer.json.tmp`, then renames it over `timer.json`, so readers never see a
partial file. `version` goes up by one on every write.

```jsonc
{
  "schema": 1,
  "version": 42,                  // +1 on every write; readers ignore older versions
  "updated_at": 1791250000000,    // ms since epoch
  "updated_by": "tui" | "cli",
  "alerts": "auto",               // auto | terminal | web | both (see Alerts)
  "settings": { "focus": 25, "short": 5, "long": 15, "every": 4 },  // minutes; long break after `every` rounds
  "timer": null | {
    "phase": "focus" | "short" | "long",
    "status": "running" | "ended",      // ended = time is up, waiting for "next" or "+5"
    "startedAt": 1791249000000,         // ms: when this phase started (phase_started_at)
    "minutes": 25,                      // phase length in minutes, grows with +5 (phase_duration)
    "pausedAt": null | 1791249600000,   // ms: set while paused
    "pausedMs": 0,                      // total paused time in this phase
    "round": 1,                         // focus rounds finished in this cycle (0..every)
    "taskId": null | "uuid",            // Magnus task (shared with Magnus Web), or
    "label": null | "office hours: E&M PSet 3",  // a label instead of a task
    "title": null | "…",                // display name (task title or label)
    "loggedMs": 0, "segmentAt": null    // focus already logged this round (task switches)
  }
}
```

Remaining time is computed from timestamps by every client, so nothing ticks
on disk and laptop sleep behaves the same everywhere:

```
elapsed   = min(minutes·60000, max(0, (pausedAt ?? now) − startedAt − pausedMs))
remaining = minutes·60000 − elapsed
ended     = status == "ended" or (status == "running" and pausedAt == null and remaining == 0)
```

A missing file means no timer. A corrupted file is treated as no timer, and
moved aside to `timer.json.corrupt` on the next write. On first run, an older
Magnus timer kept in `prefs.json` (`focusTimer`) is migrated into `timer.json`.

### `tui.json`: is the Magnus TUI open?

`{ "pid": 12345, "heartbeat_at": 1791250000000 }`, rewritten by the TUI every
15 s and removed when it quits. The TUI counts as alive if the pid exists and
the heartbeat is under 45 s old.

## Commands (`magnus timer …`)

All commands share one code path with the TUI keys (`t`, `T`), including focus
logging, and work whether the TUI is running or not.

| Command | Same as | Notes |
|---|---|---|
| `status [--json]` | status bar | `--json` prints the snapshot below |
| `start [--task ID \| --label "…"]` | `t` / `T` → start | if a timer runs, `--task/--label` switches instead (like `t` on another task) |
| `pause`, `resume` | `T` → Pause/Resume | |
| `skip` | `T` → Skip / Start next phase | logs the focus time so far when leaving a focus round |
| `add5` | `T` → Add 5 minutes | |
| `switch [--task ID \| --label "…" \| --none]` | `T` → Switch task | keeps the clock and round; logs time so far to the old target |
| `stop` | `T` → Stop | logs focus time |
| `discard` | `T` → Discard | logs nothing |
| `alerts auto\|terminal\|web\|both` | (tutor setting) | stored in `timer.json` |
| `minutes [--label PREFIX] [--days N] [--json]` | Insights | focus minutes by label, read from `focus_sessions` by Magnus |

Exit code 0 on success, 1 on error (message on stderr). With `--json`, every
command prints the snapshot after the change:

```jsonc
{ "active": true, "phase": "focus", "status": "running", "paused": false,
  "remaining_ms": 812000, "ends_at": 1791250812000, "phase_label": "Focus 2/4",
  "round": 1, "every": 4, "title": "…", "label": "…", "task_id": null,
  "version": 43, "tui_alive": true, "alerts": "auto", "logged": { "minutes": 12 } | null }
```

## Alerts (no double ringing)

`alerts` in `timer.json`: `terminal` → only the TUI rings; `web` → only the
tutor web app (chime + notification); `both`; `auto` → the TUI rings if it is
alive, otherwise the web app does. The web app always shows an in-page banner.

## Conflicts

Two clients commanding at once: each write takes the lock and re-reads the
latest file first, so commands apply in order (last write wins). Clients
compare `version` to drop stale snapshots.
