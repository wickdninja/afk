# Adding a new AFK transport

Read this when the user wants AFK over a channel that doesn't yet have a `references/<transport>.md` (e.g., SMS, Discord, iMessage, Telegram, email).

## What a transport must provide

The AFK protocol assumes four primitives. A transport is adequate if it can supply all four:

| Primitive | What it means | Example (Slack) |
|---|---|---|
| **send(thread, text)** | Append a message to an addressable conversation | `slack_send_message` with `thread_ts` |
| **read_since(thread, ts)** | List messages in the conversation newer than a cursor | `slack_read_thread` with `oldest` |
| **open_thread() → id** | Start a new session-scoped conversation and get a stable ID | Post a top-level message; use its `ts` as `thread_ts` |
| **sender_distinguishable** | Can you reliably tell your posts from the user's? | No in Slack (both are the same user), so we use the sentinel prefix |

If a transport lacks one of these, document the workaround in its reference file.

## Sentinel fallback

The sentinel prefix (`🤖 `, U+1F916 + space) is how you tell your own posts from the user's. If the transport strips emoji or mangles Unicode:

- Fall back to an ASCII sentinel: `[afk] ` with the brackets included.
- Serialize your choice in `session.json.sentinel` so resume works.
- Document the choice in the transport reference.

Never pick a sentinel that a human might type naturally (e.g., `> ` alone is a bad choice — the user could send a literal quote). The sentinel should be visually distinct and vanishingly unlikely as a user reply.

## Thread emulation for non-threaded channels

Some channels (SMS, email-over-time) do not have native threading. Two options:

1. **Single-session channel.** One session per conversation; no multiplexing. Simpler. Works for SMS if the user isn't running many sessions.
2. **Subject-line threading.** Each session tags messages with a short tag (e.g., `#sess-myapp-routing`) at the start of every post (after the sentinel). Read-since filters by tag. Less ergonomic; only worth it if the user insists on multi-session over a non-threaded transport.

Document which strategy you're using in the transport reference.

## Required reference file contents

When adding `references/<transport>.md`, cover:

1. **Identity** — authenticated user / bot, default addressing (channel, phone number, DM).
2. **Send tool** — exact MCP tool name, required params, where `thread_ts`/equivalent goes.
3. **Read tool** — exact MCP tool name, how to filter for new messages.
4. **Thread open** — how to create a new session-scoped thread.
5. **Sentinel** — confirm `🤖 ` works; note any substitution.
6. **Formatting quirks** — what markdown renders, what gets stripped, link syntax, emoji behavior.
7. **Rate limits + failure modes** — what errors look like and how to back off.
8. **Thread URL template** — so you can link the thread in the terminal activation line.

## Testing a new transport

Before trusting a new transport with a real AFK session:

1. Dry-run: post a test session header, a thread reply, read it back, confirm the sentinel round-trips.
2. Send a user-reply yourself (from the actual client, not via the MCP) and confirm your read path sees it without the sentinel.
3. Back-to-back posts: confirm rate-limit behavior is gentle enough for a streaming use case.

If any of these fail, surface the problem in the terminal before entering AFK mode — do NOT enter AFK with an untested transport, because the whole point is the user can't watch the terminal to notice breakage.

## Implemented

- `slack.md` — Slack DM via MCP. Native threading, sentinel required for sender disambiguation.
- `imessage.md` — iMessage/SMS via local bridge (osascript + `~/Library/Messages/chat.db`). **Single-session only** (no native threading; a global registry enforces one active session per handle). Requires Full Disk Access + Automation permissions on the Mac. `is_from_me` augments the sentinel for sender disambiguation.

## Not yet

- Signal — E2E, no official local DB. Would need `signal-cli` + its own socket. Revisit if requested.
- IRC / Matrix — possible, no user demand yet.
- Discord — MCP exists; add when requested.
