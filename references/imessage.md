# AFK transport: iMessage / SMS

Read when session state has `transport: "imessage"`, or when the user asks AFK to run over iMessage/SMS.

This transport does NOT use an MCP server. It uses a local bridge:

- **Send** via AppleScript driving `Messages.app`.
- **Read** via direct SQLite query against `~/Library/Messages/chat.db`.

Both halves require OS permissions to be pre-granted (see "Permissions" below). If permissions are missing, **surface the exact fix in the terminal before entering AFK** — the user cannot be prompted for permission once they've left.

## Identity + addressing

iMessage has no threads. Each conversation is a long scroll bound to one handle (phone or email). For AFK:

- **One handle = one conversation = one session.** iMessage is **single-session only**.
- If a second `/afk --transport=imessage` is started while another is already active on this machine (see "Active-session registry" below), refuse. Print in the terminal: `another AFK iMessage session is already active at <cwd> — end it first or use --transport=slack`. Exit without posting.
- Subject-tag multiplexing was considered and rejected: iMessage's flat scroll makes multi-session triage worse than useless on a phone, and the tag discipline adds error surface for no real win. If you want parallel sessions, use Slack — each session is its own thread there.

### Handle config

Read the iMessage handle + service from `~/.claude/afk/imessage.json`:

```json
{
  "handle": "+15551234567",
  "service": "iMessage"
}
```

- `handle` — phone number (E.164: `+15551234567`) or email address registered with iMessage. For SMS-only recipients use `service: "SMS"` and a phone number.
- `service` — `"iMessage"` (default) or `"SMS"`. Pick SMS only if the recipient isn't on iMessage and you're OK paying per-message + losing delivery receipts.

If the config file is missing, stop and tell the user in the terminal:

```
iMessage transport needs a handle. Create ~/.claude/afk/imessage.json with
{"handle": "+15551234567", "service": "iMessage"} and rerun.
```

Do NOT guess a handle from email or other memory.

## Tools (bash helpers)

Two shell scripts ship with the skill at `~/.claude/skills/afk/scripts/`. Call them with `Bash`.

### Send

```
~/.claude/skills/afk/scripts/imessage_send.sh <handle> <body_file> [service]
```

- `handle` — E.164 phone or email, matching the config.
- `body_file` — path to a file whose contents are the literal message body. Using a file (not a shell arg) sidesteps AppleScript quote/newline escaping hell. Write your message to a temp file first, then pass the path.
- `service` — `iMessage` (default) or `SMS`.

Exit code 0 on success. Any non-zero exit = send failed; read stderr and surface.

### Read

```
~/.claude/skills/afk/scripts/imessage_read.sh <handle> [since_ns]
```

- `handle` — same handle used to send.
- `since_ns` — cursor, matching `message.date` format (nanoseconds since Apple epoch 2001-01-01 UTC). Default `0` returns all messages in that conversation (use only on cold-start; normally pass `last_seen_ts`).

Outputs **one JSON object per line** (JSONL), oldest-first:

```
{"rowid":12345,"date":761328543000000000,"is_from_me":1,"text":"🤖 ▸ applied fix","handle":"+15551234567"}
{"rowid":12346,"date":761328601000000000,"is_from_me":0,"text":"nice — what's next?","handle":"+15551234567"}
```

`date` is in `message.date`'s native format (nanoseconds since 2001-01-01 UTC). **Use this value directly as `last_seen_ts`** — do not convert. Pass it back as `since_ns` on the next read.

To convert to Unix epoch for display: `unix_seconds = date / 1_000_000_000 + 978_307_200`.

## Sender distinguishability

iMessage gives you `is_from_me` per message, but **the sentinel is the load-bearing filter**, not `is_from_me`. Reason: when the user has the same Apple ID signed in on both their Mac (where you're running) and their phone (where they're replying), iMessage marks BOTH sides as `is_from_me = 1` in `chat.db`. Filtering on it alone will silently drop every user reply.

Apply filters in this order:

1. **Sentinel first.** Skip any message whose text (after trimming leading whitespace) starts with the sentinel (`🤖 `). This is your authoritative "I sent this" check.
2. **`is_from_me = 0` as a sanity check** when the user is on a *different* Apple ID. If you observe at least one `is_from_me = 0` message in the conversation history, it's safe to use `is_from_me = 0` as a secondary filter. If every message in history is `is_from_me = 1`, you're in the same-Apple-ID case — the secondary filter is useless and the sentinel is the ONLY filter.

Detect the same-Apple-ID case once on session start: read the last ~20 messages, count `is_from_me` values. If all are 1, write `"same_apple_id": true` to session.json `notes` so future ticks skip the secondary filter.

Everything that survives the sentinel filter (and the `is_from_me = 0` filter, when applicable) is real user input.

## Sentinel

`🤖 ` (U+1F916 + space). iMessage renders emoji fine; no substitution needed. Keep it — it preserves protocol consistency across transports and guards against the `is_from_me` filter ever being wrong.

## Active-session registry

To enforce the single-session rule across worktrees, maintain `~/.claude/afk/imessage_sessions.json`:

```json
{
  "active": {
    "+15551234567": {
      "cwd": "/Users/you/src/myapp",
      "session_label": "myapp · bug-fix",
      "started_at": "2026-04-17T18:42:00Z"
    }
  }
}
```

On `/afk --transport=imessage` entry:

1. Read the registry.
2. If `active[handle]` exists AND `cwd` != current `$PWD`: another session owns this handle. Refuse (see terminal message above) and exit.
3. If `active[handle]` exists AND `cwd` == current `$PWD`: this is a resume of our own session; proceed.
4. Otherwise: claim the slot — write the entry — and start the session.

On close: remove `active[handle]` from the registry. Do this in the same step as flipping `session.json.status = "closed"`. If the registry ever gets out of sync (crash, force-quit), the user can fix it by deleting the file; document that.

Write atomically (`.tmp` + `mv`).

## Session header

Since there's no thread URL, the header is just the first message in the conversation for this session. Format:

```
🤖 AFK session start — myapp · bug-fix
cwd: /Users/you/src/myapp
started: 2026-04-17 18:42 ET
task: fix routing polyline break at state lines

reply to drive. commands: pause resume status end "switch to <task>" faster slower
```

The `thread_ts` field in `session.json` for iMessage stores the `rowid` of this header message (from the send round-trip — see below). It's not a "thread" in Apple's sense, but it anchors the session to a specific starting point for reads and for idempotence checks.

All reads use `last_seen_ts` as the cursor — because iMessage is single-session, every non-`is_from_me` message in this conversation since the cursor is a reply to this session. No tag-filtering needed.

### Getting the header's rowid

`imessage_send.sh` doesn't return a rowid (AppleScript doesn't expose it). To capture the header rowid:

1. Send the header.
2. Immediately read with `since_ns = 0` limited to the last 1 message, OR re-read with a `since_ns` of (current time in ns) minus 10 seconds.
3. Take the most recent `is_from_me=1` row whose text matches your header's first 40 chars.

Store that rowid as `thread_ts` in state.

## Formatting quirks

iMessage is plain text. Markdown is NOT rendered — asterisks, underscores, backticks all appear literally. Adapt the protocol:

- **Status updates** — keep the `▸` leader, skip bold/italic:
  ```
  🤖 ▸ applied fix, running tests
  ```
- **Tool output / diffs** — no code fences. Just two newlines, then the block, then two newlines. Long blocks will wrap awkwardly on a phone. Truncate aggressively — show the failing test line, not the whole transcript.
- **Links** — iMessage auto-linkifies bare URLs. Do NOT wrap them in `<>` or `[label](url)` syntax; that will render literally. Use the bare URL and precede it with context:
  ```
  🤖 ✅ done. PR: https://github.com/your-org/your-repo/pull/1234
  ```
- **Newlines** — `\n` in AppleScript sends a literal newline; safe to use. Blank lines are fine.
- **Emoji** — render natively. Status/✅/⚠️/▸ all work.
- **Message length** — there's no hard limit for iMessage, but aim for ≤ 5 lines per post. SMS fragments at 160 chars and long messages split; if `service: SMS`, keep each post under 300 chars.

After the first post of a new session, fetch it back once to confirm it rendered sensibly (especially the session header). Do not re-verify every post.

## Rate limits + failure modes

### osascript / Messages.app
- First run per session will prompt for **Automation** permission for whatever process runs `osascript` (Terminal, iTerm, cmux, etc.). If it fails, stderr will say `Not authorized to send Apple events to Messages`. Fix: System Settings → Privacy & Security → Automation → [your terminal] → Messages (toggle on). Surface this in the terminal before entering AFK.
- Messages.app must be running (or launchable) for sends to succeed. The AppleScript `tell application "Messages"` will auto-launch it if closed. If the user has iMessage disabled entirely (not signed in), sends will fail with `Can't get buddy`. Surface and exit.
- Sends are synchronous from AppleScript's POV but may queue if iMessage is reconnecting. A send that returns exit 0 has been handed to Messages.app; actual delivery depends on Apple's servers. No delivery receipts are exposed via AppleScript.

### sqlite3 / chat.db
- Reading chat.db requires **Full Disk Access** for whatever process runs `sqlite3`. Without it: `Error: unable to open database file`. Fix: System Settings → Privacy & Security → Full Disk Access → [your terminal]. Surface and exit if this fails.
- chat.db is written-to live by Messages.app. The read script uses a read-only connection (`file:…?mode=ro`) to avoid lock contention. Transient `database is locked` is possible under heavy write; retry once after 500ms before giving up.
- Newer macOS versions may store message bodies in `attributedBody` (binary NSKeyedArchiver) instead of `text`. The read script returns only messages with non-null `text`. Messages with rich formatting, tapbacks, reactions, and some stickers may be invisible to this path. Document this as a known gap; tell users to reply with plain text.

### Graceful degradation

If send fails: write the pending body to `.afk/pending.md` (append), log to `.afk/log.md`, `ScheduleWakeup` 60s, and retry on next wake. Do not drop messages silently.

If read fails: log and skip this tick — treat it as an empty wake so backoff still progresses. Do not mark messages as consumed if the read failed.

## "Thread URL" for the terminal activation line

iMessage has no URL for a conversation. The terminal line is:

```
AFK mode active · iMessage ↔ <handle> · <session_label>
```

## Permissions preflight (required before first AFK tick)

On session start, BEFORE posting the header, run these two checks and surface any failure in the terminal:

1. **FDA check:**
   ```
   sqlite3 "file:$HOME/Library/Messages/chat.db?mode=ro" "SELECT 1 LIMIT 1;"
   ```
   Exit 0 = FDA is granted. Non-zero = print the FDA fix instructions and exit.

2. **Automation check:**
   ```
   osascript -e 'tell application "Messages" to get name' 2>&1
   ```
   Exit 0 with output = Automation is granted. Non-zero (especially `-1743` "Not authorized") = print the Automation fix instructions and exit.

3. **Same-Apple-ID detection** (after sending the header so there's at least one row to inspect): read the last 20 messages from this conversation, count `is_from_me` values. If every row is `is_from_me = 1`, both ends share an Apple ID — write `"same_apple_id": true` (or a notes string) to `session.json` so subsequent ticks skip the `is_from_me` secondary filter. See "Sender distinguishability" above for why this matters.

The first two are one-shot; cache the success for the session's lifetime. The third should re-check on resume since the user could have signed out/in between sessions.

## Testing (one-time per machine)

Before trusting iMessage with a real AFK session, dry-run:

1. Put handle + service into `~/.claude/afk/imessage.json`.
2. Run the send script with a tiny body. Confirm it lands on your phone.
3. Reply from your phone.
4. Run the read script with `since_ns=0`. Confirm both messages round-trip, with correct `is_from_me` values.
5. Send a message containing a newline and an emoji. Confirm they render as expected.

If any of those fail, fix before using `/loop /afk`.
