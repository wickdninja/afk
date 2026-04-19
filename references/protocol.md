# AFK protocol — message format, commands, state

Transport-agnostic. Read this alongside the transport-specific reference (e.g., `slack.md`).

## Sentinel prefix

Every message you post starts with:

```
🤖 
```

(Emoji U+1F916 followed by an ASCII space.) This is the ONLY mark that identifies a post as yours. Never omit it. If a transport strips emoji, swap to a text sentinel like `[afk] ` and update `references/<transport>.md` to document the substitution.

On read, treat any message whose `text` (after trimming leading whitespace) starts with the sentinel as yours and ignore it. Everything else in the thread is user input.

**Why this matters:** on Slack the MCP posts as the authenticated user, so both you and the human appear as the same Slack user. Filtering by sender is not reliable. The sentinel is.

## Message shape

### Status update (preferred default)

```
🤖 ▸ applied fix to experts.service.ts, running tests
```

Single line, leading `▸` for scannability, present-tense verb. Mobile-friendly.

### Question

```
🤖 the rules file has two `TX` entries with different thresholds — prefer the newer one (2026-03) or the older one (2025-11)?
```

End with `?`. State what you need. Offer a default when reasonable:
`🤖 … going with the newer one unless you say otherwise in the next 5 min.`

### Tool output / code / diff

Prose line first, then fenced block:

````
🤖 tests are failing on pipeline.spec.ts:

```
FAIL  pipeline.spec.ts
  × orchestrator retry on partial consensus (124 ms)
    Expected 3 experts, received 2
```

🤖 ▸ investigating the 2/3 path
````

### Milestone / completion

```
🤖 ✅ done. PR up: https://github.com/your-org/your-repo/pull/1234 — 4 files changed, tests green.
```

### Warning / blocked

```
🤖 ⚠️ blocked: migration needs prod DB access. Want me to open the SSM tunnel request or wait until you're back?
```

## User commands

Parse the user's reply text (post-trim, lowercase) against these commands BEFORE treating it as free-form instruction. If the reply is exactly one of these (or starts with the command followed by whitespace + args), treat it as a command.

| Command | Effect |
|---|---|
| `pause` | Stop polling. Enter long idle — `ScheduleWakeup` 1800s, and on wake only check for `resume` or `end`. Post `🤖 paused.` and nothing else until resumed. |
| `resume` | Exit pause. Post `🤖 resumed, continuing…` and pick up where you left off. |
| `status` | Post a fresh one-line status (what you're currently doing, how long since last progress). Do NOT dump history. |
| `end` / `stop` / `done afk` | Close the session (see SKILL.md §4). |
| `switch to <new task>` / `instead, <new task>` | Drop current work, start the new task in the same thread. Post an acknowledgement and a one-line plan. |
| `faster` | Shrink ScheduleWakeup delay floor to 60s AND arm a 30s Monitor ticker (see SKILL.md "Sub-60s cycles"). Record `monitor_task_id` + `monitor_tick_s` in state. |
| `slower` | Raise ScheduleWakeup delay floor to 1800s. `TaskStop` any armed Monitor ticker and clear `monitor_task_id`. |
| `log` | Post the last 5 status updates as a recap (read from thread, not local). Useful if the user lost context. |

Anything else → treat as a free-form instruction. The user writes fast on a phone — lowercase, short, often no punctuation. Do not demand well-formed prompts.

## Session state file

Location: `$PWD/.afk/session.json` (relative to the cwd Claude was invoked in — cmux worktrees isolate naturally).

Schema (Slack example):

```json
{
  "transport": "slack",
  "channel_id": "D01234ABCDE",
  "thread_ts": "1713372000.123456",
  "sentinel": "🤖 ",
  "session_label": "myapp · bug-fix",
  "task": "fix the routing break at state boundaries",
  "cwd": "/Users/you/src/myapp",
  "started_at": "2026-04-17T18:42:00Z",
  "last_seen_ts": "1713372000.123456",
  "status": "active",
  "empty_wake_streak": 0,
  "delay_floor_s": 60
}
```

Schema (iMessage example — note the transport-specific cursor + addressing):

```json
{
  "transport": "imessage",
  "handle": "+15551234567",
  "service": "iMessage",
  "sentinel": "🤖 ",
  "session_label": "myapp · bug-fix",
  "task": "fix the routing break at state boundaries",
  "cwd": "/Users/you/src/myapp",
  "started_at": "2026-04-17T18:42:00Z",
  "last_seen_rowid": 69827,
  "status": "active",
  "multi_session": false,
  "empty_wake_streak": 0,
  "delay_floor_s": 60,
  "notes": "same Apple ID on both ends — is_from_me always 1, sentinel is the only filter"
}
```

Fields:

- `transport` — key into `references/<transport>.md`.
- `channel_id` / `thread_ts` (Slack) — channel + thread pair.
- `handle` / `service` / `last_seen_rowid` (iMessage) — single-conversation addressing + chat.db rowid cursor (use **rowid**, not `date`, for `since` queries — rowid is monotonic and 64-bit, `date` can collide on rapid sends).
- `sentinel` — serialized so you survive a sentinel change without breaking resume.
- `session_label` — what the human sees to distinguish sessions on their phone.
- `last_seen_ts` / `last_seen_rowid` — newest consumed message cursor. Pick the field name that matches the transport's native cursor type.
- `status` — `active` | `paused` | `closed`. On resume, refuse to reuse `closed`.
- `multi_session` — bool. Some transports (iMessage) are single-session per machine; this asserts the constraint at write time.
- `empty_wake_streak` — for backoff. Reset to 0 on any user reply.
- `delay_floor_s` — baseline ScheduleWakeup delay, tunable via `faster`/`slower`.
- `notes` — free-form runtime-discovered quirks for THIS session (e.g., same-Apple-ID warning). Append-only across resumes; never autogenerated content the user might confuse for a status.
- `monitor_task_id` (optional) — when a sub-60s Monitor ticker is armed, store its task id here so resume + close can find it.

Write atomically: write to `.afk/session.json.tmp`, then `mv` over. Never leave a half-written file; a crash here breaks resume.

Also write an append-only `.afk/log.md` for local debugging (one line per post/receive event, timestamped). This is a debug aid for YOU, not the user — it is not authoritative. The Slack thread is authoritative.

## Backoff schedule

On each consecutive empty wake (no user message arrived):

| streak | delay |
|---|---|
| 1 | `delay_floor_s` (default 60) |
| 2 | 90 |
| 3 | 270 |
| 4 | 900 |
| 5+ | 1800 |

Reset the streak to 0 on any user reply. If `status = paused`, ignore the schedule and always use 1800.

## Cache-window hygiene

`ScheduleWakeup` sleeps past 300s invalidate the prompt cache. Relevant rules:

- Under 270s: cache stays warm, cheap waking.
- 300–1200s: paying the cache miss for little benefit. Avoid.
- 1200s+: one cache miss amortized over a long wait.

Map this to the backoff schedule above (60 → 90 → 270 → 900 → 1800). Notice we skip the 300–900s dead zone.

## Idempotence and duplicate delivery

If you wake and can't tell whether your previous post succeeded (tool error, network), read the last 3 messages in the thread first. If your intended post is already there (by text match on the sentinel + first ~40 chars), do NOT repost. Duplicate DMs at 2am are the fastest way to break trust in this protocol.

## Ending without acknowledgment

If the task completes cleanly and the user hasn't replied in 30+ min:

1. Post the completion summary.
2. `ScheduleWakeup` at 1800s, once.
3. On wake with still no reply: post `🤖 idle-closing session. reply in this thread anytime to reopen.`, set `status: closed`, and stop.

Do not hold a polling session open indefinitely after the task is done — it burns cache budget for nothing.
