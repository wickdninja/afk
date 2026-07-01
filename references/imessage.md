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
  "service": "iMessage",
  "aliases": ["+15551234567", "you@example.com"]
}
```

- `handle` — phone number (E.164: `+15551234567`) or email address registered with iMessage. This is the address you SEND to. For SMS-only recipients use `service: "SMS"` and a phone number.
- `service` — `"iMessage"` (default) or `"SMS"`. Pick SMS only if the recipient isn't on iMessage and you're OK paying per-message + losing delivery receipts.
- `aliases` — **optional but strongly recommended.** Every address the same person might reply FROM. One Apple ID commonly registers both a phone number and one or more emails; iMessage records a reply under whichever alias the phone was using, which is not always the one you sent to. **Read polls all of them** (pass them comma-joined; see "Read"). If omitted, only `handle` is polled and a reply from a non-pinned alias is silently dropped. Include `handle` itself in the list. Observed live: a user reply landed under the email alias while the reader polled only the phone number, and the session went deaf.

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
~/.claude/skills/afk/scripts/imessage_read.sh <handle>[,<alias2>,...] [since_ns]
```

- `handle` — one or more comma-separated addresses. **Always pass every entry from `aliases`** (comma-joined, no spaces), not just the pinned send handle. Polling a single handle drops replies the user sent from a different alias.
- `since_ns` — cursor, matching `message.date` format (nanoseconds since Apple epoch 2001-01-01 UTC). Default `0` returns all messages in that conversation (use only on cold-start; normally pass `last_seen_ts`).

The reader populates `text` from `message.text` when present and otherwise decodes `message.attributedBody` (the NSAttributedString typedstream where the body now lives on current macOS). It does this for BOTH inbound and outbound rows, and it does **not** filter by `is_from_me` — that is the consumer's job via the sentinel (see "Sender distinguishability"). Attachment placeholder characters (object-replacement, `U+FFFC`) are stripped; a message that is nothing but an attachment/tapback is omitted (no phantom empty row).

Outputs **one JSON object per line** (JSONL), oldest-first:

```
{"rowid":12345,"date":761328543000000000,"is_from_me":1,"text":"🤖 ▸ applied fix","handle":"+15551234567"}
{"rowid":12346,"date":761328601000000000,"is_from_me":0,"text":"nice — what's next?","handle":"+15551234567"}
```

`date` is in `message.date`'s native format (nanoseconds since 2001-01-01 UTC). **Use this value directly as `last_seen_ts`** — do not convert. Pass it back as `since_ns` on the next read.

To convert to Unix epoch for display: `unix_seconds = date / 1_000_000_000 + 978_307_200`.

## Sender distinguishability

**The sentinel is the sole required filter. Never drop a message because of `is_from_me`.**

Reason: when the user has the same Apple ID signed in on both their Mac (where you're running) and their phone (where they're replying), iMessage marks BOTH sides as `is_from_me = 1` in `chat.db`. An `is_from_me = 0` filter then matches zero rows and every user reply is silently dropped. This exact bug made an AFK session go deaf. The read helper deliberately emits every row and does NOT filter on `is_from_me`; the consumer must not re-introduce that filter.

The filter, in full:

- **Sentinel only.** A message is YOURS if and only if its `text` (after trimming leading whitespace) starts with the sentinel (`🤖 `). Skip those. **Everything else is user input** — regardless of `is_from_me`, regardless of which alias it came from.

`is_from_me` is informational (it's in the JSONL for logging/debugging), not a gate. Do not condition on it, and do not maintain a "same_apple_id" flag to decide whether to trust it — the sentinel makes that decision unnecessary in every topology:

- **Same Apple ID** (all rows `is_from_me = 1`): the sentinel is the only thing that can tell your posts from the user's. Correct by construction.
- **Different Apple ID** (user replies are `is_from_me = 0`): the sentinel still cleanly excludes your posts; the `is_from_me = 0` rows also happen to be user input, so the outcome is identical. There is no case where adding an `is_from_me` gate helps, and one common case where it silently breaks — so don't add it.

Corollary: if you ever see a recent inbound-looking message that lacks the sentinel, treat it as user input immediately. Do not wait to "confirm" the Apple-ID topology first.

Everything that survives the sentinel filter is real user input.

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

All reads use `last_seen_ts` as the cursor — because iMessage is single-session, every non-sentinel message in this conversation since the cursor is a reply to this session. No tag-filtering needed.

### Getting the header's rowid

`imessage_send.sh` doesn't return a rowid (AppleScript doesn't expose it). To capture the header rowid:

1. Send the header.
2. Immediately read with `since_ns = 0` limited to the last 1 message, OR re-read with a `since_ns` of (current time in ns) minus 10 seconds.
3. Take the most recent row whose text starts with the sentinel and matches your header's first 40 chars. (Match on the sentinel, not `is_from_me` — same reasoning as the reply filter.)

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
- **attributedBody is the default source on current macOS.** On Sonoma / Sequoia / Tahoe the plain `message.text` column is frequently NULL and the body lives ONLY in `message.attributedBody` (a binary NSAttributedString typedstream). The Python read script (`imessage_read.py`, also exposed as `imessage_read.sh`) decodes the length-prefixed NSString payload from `attributedBody` whenever `text` is NULL/empty, for both inbound and outbound rows. Emoji and embedded newlines round-trip correctly because the decoder slices an exact, length-prefixed byte range (not a regex). Verify with `scripts/imessage_selftest.py`. Tapbacks, reactions, and some stickers (which embed via NSNumber/NSDictionary attributes rather than a length-prefixed NSString backing) have no string body and are intentionally omitted; tell users to reply with plain text or short emoji.

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

3. **Reader round-trip** (optional but recommended, especially on a new machine): run `scripts/imessage_selftest.py <handle>[,<alias>...]`. It proves the attributedBody decoder round-trips plain text, emoji, newlines, and long bodies, and (if chat.db is readable) that real replies recover from `attributedBody`. If it fails, fix before entering AFK.

There is deliberately no "same Apple ID" detection step and no `is_from_me` flag to maintain: the sentinel is the sole filter in every topology (see "Sender distinguishability").

Both checks are one-shot; cache success for the session's lifetime.

## Testing (one-time per machine)

Before trusting iMessage with a real AFK session, dry-run:

1. Put handle + service + `aliases` into `~/.claude/afk/imessage.json`. List every address you might reply from.
2. Run `scripts/imessage_selftest.py <handle>[,<alias>...]`. Confirm the synthetic decoder tests PASS and, if chat.db is readable, that real bodies recover from `attributedBody`.
3. Run the send script with a tiny body. Confirm it lands on your phone.
4. Reply from your phone — once with plain text, once with an emoji, once with a newline in the body. If you have multiple aliases, reply from more than one.
5. Run the read script with all aliases comma-joined and `since_ns=0`. Confirm every reply round-trips with its exact text. (Note: replies may show `is_from_me=1` on a shared Apple ID — that is expected and must not cause them to be dropped.)
6. Send a message containing a newline and an emoji. Confirm they render as expected.

If any of those fail, fix before using `/loop /afk`.
