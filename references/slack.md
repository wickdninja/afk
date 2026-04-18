# AFK transport: Slack

Default transport. Read this when the session state has `transport: "slack"` or when defaulting.

## Identity + channel

Slack addressing is user-specific, so it lives in a config file — not hardcoded. Read `~/.claude/afk/slack.json`:

```json
{
  "user_id":        "U01234ABCDE",
  "channel_id":     "D01234ABCDE",
  "workspace_host": "yourworkspace.slack.com"
}
```

- `user_id` — Slack user ID of the AFK operator. Used for the thread URL template and as a "don't self-@-mention in the thread" reminder. (The MCP already posts as whoever is authenticated; this field is informational.)
- `channel_id` — the channel or DM where session headers + threads go. Most people use their own command-center DM (`D…`) so sessions don't spam teammates, but any channel you can post to works.
- `workspace_host` — bare hostname, used to build thread URLs.

**If the config file is missing**, stop before posting and print in the terminal:

```
Slack transport needs ~/.claude/afk/slack.json with {user_id, channel_id, workspace_host}. See references/slack.md.
```

Do NOT guess IDs from memory or from the Slack MCP's `whoami`. Silently picking the wrong channel will leak work context into the wrong DM.

The MCP posts AS the authenticated user. This means **your posts and the user's replies share the same sender in the thread**. The sentinel prefix (`🤖 `) is how you tell them apart. Do not skip it — if you do, on the next wake you will loop forever, mistaking your own question for a new user reply and re-acting on it.

## Tools

### Sending

Use `mcp__plugin_slack_slack__slack_send_message`:

```
channel_id: <channel_id from ~/.claude/afk/slack.json>
thread_ts:  <from session.json>     # omit for session-header post
message:    "🤖 <your message>"
```

**Do NOT use `slack_send_message_draft`** in AFK mode. The draft flow is for user-reviewed messages; AFK posts are autonomous by design — the user pre-authorized autonomous posting by invoking `/afk`.

`reply_broadcast: true` — set sparingly, only for attention-critical events (catastrophic failure, session-close summary) if you want the post to also appear in the channel's main feed. Default: false.

### Reading for replies

Use `mcp__plugin_slack_slack__slack_read_thread`:

```
channel_id:      <channel_id from ~/.claude/afk/slack.json>
message_ts:      <thread_ts from session.json>
oldest:          <last_seen_ts from session.json>
response_format: "concise"
```

The response is an array of messages with `ts`, `user`, `text`. Iterate; for each message:

1. If `text.lstrip().startswith(sentinel)` → yours, skip.
2. Otherwise → user input. Capture `ts` and `text`.

Update `last_seen_ts` to the newest ts you consumed (including skipped ones — you don't want to re-read your own messages either).

### Finding the thread URL

To link the thread in the terminal activation line and in summaries:

```
https://<workspace_host>/archives/<channel_id>/p<thread_ts_without_dot>
```

Example: `thread_ts = "1713372000.123456"` → `p1713372000123456`.

## Slack formatting quirks

MCP sends use standard markdown (`**bold**`, `_italic_`, `` `code` ``, `<url|label>` for links with labels). This is NOT the same as Slack's classic mrkdwn (`*bold*`). The MCP layer translates.

Notable:

- Links: `<https://example.com|clickable label>` renders as a hyperlink.
- Code fences: triple-backtick blocks render correctly.
- Emoji: `:white_check_mark:` and literal Unicode both work. Prefer Unicode for the sentinel.
- Line breaks: `\n` works. Blank lines render as blank lines.
- Mentions: `<@U…>` pings. **Don't @-mention the AFK operator in their own DM** — pings are redundant and push an extra notification per post. Use plain text to refer to them.

After posting something non-trivial, consider a read-back check (fetch the thread, confirm your post rendered sensibly). Do this once per session, not per post, unless you suspect formatting trouble.

## Session header (top-level post)

The ONE non-threaded post per session. Format:

```
🤖 **AFK session** · `<session_label>`
cwd: `<cwd>`
started: <YYYY-MM-DD HH:MM TZ>
task: <task summary>

reply in this thread to drive this session. commands: `pause` `resume` `status` `end` `switch to <task>` `faster` `slower`
```

Example:

```
🤖 **AFK session** · `myapp · bug-fix`
cwd: `/Users/you/src/myapp`
started: 2026-04-17 18:42 ET
task: fix the routing break at state boundaries

reply in this thread to drive this session. commands: `pause` `resume` `status` `end` `switch to <task>` `faster` `slower`
```

Keep the header dense — it's what the user scans when triaging multiple parallel sessions on their phone. The label + cwd let them jump straight to the right worktree when back at their desk.

## Rate limits + resilience

Slack's API is rate-limited but generous enough for this use case (a few posts per minute per session). If you hit a rate-limit error:

1. Back off: sleep 30s and retry once.
2. If still failing, write the intended post to `.afk/pending.md` and `ScheduleWakeup` 60s. On wake, try again — do not drop messages silently.

If the MCP server itself is down, log to `.afk/log.md` locally and surface on next wake. Do not crash the session — the point of AFK is that transient channel issues don't strand the user.

## One DM, many threads

All AFK sessions for one user target the same configured channel. They're distinguished by `thread_ts`. N cmux worktrees = N threads in the same channel. The user scrolls the channel to triage; taps into a thread to drive that session.

This is why the session header is a **top-level post** (not a thread reply). It makes each session visible in the channel's main timeline for triage.
