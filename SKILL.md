---
name: afk
description: AFK ("away from keyboard") protocol - use when the user says they're stepping away (dinner, beach, school pickup, errands, sleep, "going afk", "ping me on slack when...", "text me when...") and wants Claude to keep working and interact with them over a remote channel instead of the terminal. Intended invocation is "/loop /afk [TASK]" — the /loop harness powers the polling via ScheduleWakeup, and this skill owns the thread protocol. Task is optional: when omitted the skill inherits from the current conversation context (recent goals, diffs, plans). Supports Slack (default) and iMessage/SMS via "--transport=imessage". Also triggers on "/afk", "run this in the background and DM me", "I'll be on my phone", "sms me", or any request to command Claude from Slack/SMS/iMessage/Discord while away. Establishes a persistent thread per session so the user can run 10+ parallel cmux sessions from one phone and route each reply to the right session. You MUST invoke this skill whenever the user hands off control to remote messaging, even if they don't name the skill directly.
---

## Correct invocation

```
/loop /afk [task]
/loop /afk                               # no task — inherit from current conversation
/loop /afk --transport=imessage [task]   # drive over iMessage/SMS instead of Slack
```

`/loop` (without an interval) puts the conversation in dynamic self-pacing mode. Each tick re-fires `/afk`, which re-enters this skill with state loaded from `.afk/session.json`. You call `ScheduleWakeup` at the end of each tick to set the next wake delay.

### Resolving the task (task is optional)

- **`/afk <task>` — explicit task.** Use the string verbatim as the session task.
- **`/afk` — no task.** Resume-or-inherit:
  1. If `.afk/session.json` exists with `status: active`, this is a resume — continue that session, do not ask for a new task.
  2. Otherwise, **derive the task from the current conversation context**: the user's most recent goals/work-in-progress in this transcript, uncommitted diffs on disk, an active plan/todo list if one exists, the repo + recent commits. Summarize in one sentence and use that as the task. Derive the session label from the worktree name plus a short slug of the summary.
  3. Post the derived task in the session header (`inherited from terminal context`) so the user can see what you picked up. If they disagree, they can reply `switch to <real task>` in the thread — no need to ask in the terminal; they're leaving.
  4. Only block to ask the user what to work on if there is genuinely no context to infer from (fresh terminal, no messages, no repo state). Print `what should I work on while you're afk?` in the terminal and exit without starting a session.
- **`--transport=<name>` — override transport.** Today: `slack` (default), `imessage`. Unknown values → stop and ask. Everything else on the command line after the flag is the (optional) task.

### Invocation without `/loop`

If the user runs `/afk [task]` WITHOUT `/loop` (first thing you should check: are you in /loop mode?), you have two options:

1. **Preferred**: tell the user in the terminal: `AFK needs /loop to poll — rerun as: /loop /afk [task]`. Do not post to Slack/iMessage. Exit.
2. **One-shot fallback**: if the task is small and doesn't need a reply (e.g., "kick off this job and post when done"), do the work, post a completion summary to a fresh thread, and exit. Warn the user once that no polling will happen.

Never pretend polling is active when it isn't.

# AFK protocol

## When this fires

The user is leaving the keyboard and wants to keep working with you over a messaging channel — Slack DM today, potentially SMS/Discord/etc. later. They may be running many sessions at once (via cmux worktrees), so each session needs its own isolated conversation thread.

## Core idea

One **session** = one **thread** on the remote channel.

- The thread root is the session header.
- Every status update, question, and user reply lives inside that thread.
- Local state file (`$PWD/.afk/session.json`) pins this session to its thread, so re-entering `/afk` from the same worktree resumes the same conversation instead of starting a new one.
- You distinguish your own messages from the user's with a **sentinel prefix** (`🤖 ` by default), because the Slack MCP posts as the user — so Slack's built-in sender attribution does not help here.

## How the polling happens — requires `/loop`

The wait-for-reply loop relies on `ScheduleWakeup`, which ONLY fires in `/loop` dynamic mode. The intended invocation is:

```
/loop /afk <task description>
```

The `/loop` harness re-enters this skill on each wake. You (the skill) just do the current tick: check thread, act, post, decide on next delay via `ScheduleWakeup`.

If the user runs `/afk <task>` *without* `/loop`, you can still do one-shot work (post header, do the task, post summary) but you CANNOT block waiting for replies mid-task. In that case, finish the task, post a summary with "reply in this thread to continue — next session should be started via `/loop /afk`", and exit. Tell the user this once in the terminal so they can relaunch correctly.

### Sub-60s cycles via a Monitor ticker

`ScheduleWakeup` is clamped to `[60, 3600]` by the runtime, so 60s is the hard floor for timer-based wakes. When the user wants faster reply detection (`faster` command, or an explicit request for "30s cycles" etc.), arm a **persistent Monitor ticker** whose stdout emits one line per desired wake:

```
while true; do echo "tick $(date -u +%H:%M:%SZ)"; sleep 30; done
```

Each tick arrives as a `<task-notification>` and wakes the loop immediately, bypassing the `ScheduleWakeup` floor. Treat the notification as a cue to read the Slack thread via MCP — the Monitor script is just a ticker; it does not itself read Slack (MCP tools are only callable from the model's tool loop, not from bash).

Rules:

- Record the ticker task id in `.afk/session.json` under `monitor_task_id` so resume and close can find it.
- Always keep a fallback `ScheduleWakeup` (≥60s) armed too — if the Monitor dies silently the session must still wake on the long safety net.
- On `slower`, or on 3+ consecutive empty ticks, `TaskStop` the ticker and fall back to pure `ScheduleWakeup` backoff (60→90→270→900→1800). Rapid ticking while the user is genuinely away burns compute for no benefit.
- On `close`, `TaskStop` the ticker before exiting.

The `references/protocol.md` backoff table and the `faster`/`slower` commands still apply; the Monitor is an accelerator over the top of the existing schedule, not a replacement.

## Picking the transport

Default transport is Slack. The user may override via `--transport=<name>` or free-form text ("use iMessage", "SMS me", "post to #foo"). Read the matching reference BEFORE doing anything transport-specific:

- `references/slack.md` — Slack DM (default).
- `references/imessage.md` — iMessage or SMS via local bridge (osascript + chat.db). **Single-session only** (iMessage has no threads; a second concurrent session would be untriageable). Best when the user is off-wifi or doesn't want to open Slack. For parallel sessions, use Slack.
- `references/transports.md` — how to add a new transport and what capabilities it must implement.

If the user names a transport that has no reference file, stop and ask them to either point you at the tool, or fall back to Slack.

## The protocol (transport-agnostic)

See `references/protocol.md` for the full message-format + user-command reference. Short version:

- **Your posts** to the thread always start with the sentinel (`🤖 `). This is the ONLY reliable way to tell your messages apart from the user's later — do not rely on sender metadata.
- **User replies** are anything in the thread without that sentinel.
- **Tool output / code / long blobs** go inside triple-backtick code blocks. Keep natural-language prose outside code blocks so it renders normally on mobile.
- **One post per milestone**, not one post per tool call. Noise kills this protocol fast — imagine the user watching on their phone during dinner.
- **Questions end with `?`** and clearly state what you need. Offer a default when reasonable ("going with X unless you say otherwise").
- **User commands** (`pause`, `resume`, `status`, `end`, `switch to <task>`) are parsed from the plain text of their reply. Anything not a recognized command is a free-form instruction.

## Session lifecycle

### 1. Start or resume

On entry:

1. Check `$PWD/.afk/session.json`.
   - **Exists and `status: active`** → resume. If the user passed a task on this invocation, treat it as a `switch to <task>` in the existing thread. Otherwise post a `🤖 resumed` note with a one-line status and continue.
   - **Missing or `status: closed`** → new session. Resolve the task:
     - If a task was provided on the invocation, use it.
     - If no task was provided, derive one from the current conversation context (recent user messages, active plan/todos, uncommitted diffs, recent commits). Summarize in one sentence. If you genuinely can't infer anything, print `what should I work on while you're afk?` in the terminal and exit without posting.
2. Pick the transport: default `slack` unless `--transport=<name>` or explicit natural-language override. Read the matching `references/<name>.md` for tools, sentinel, formatting, and (for iMessage) permissions preflight. For iMessage specifically, run the FDA + Automation checks from `references/imessage.md` and bail with a clear terminal message if either fails.
3. For a new session:
   - Ensure `.afk/` is ignored by git: if `.gitignore` exists in the repo root and doesn't already include `.afk/`, append it. If no repo, skip.
   - Post a **session header** as a new top-level message in the channel (NOT in a thread — for iMessage, see that reference for how "header" maps). Include: task summary (+ `inherited from terminal context` if you derived it), cwd, start time, session label.
   - Capture the returned `ts`/`rowid` — this becomes your `thread_ts` for the rest of the session.
   - Write `.afk/session.json` (see `references/protocol.md` for schema; include `transport` + any transport-specific fields like `handle`).
   - Post a first thread reply confirming you're active and what you'll do first.
4. In the terminal, print one line:
   - Slack: `AFK mode active · thread: <url>`
   - iMessage: `AFK mode active · iMessage ↔ <handle> · tag: #sess-<slug>` (drop the tag in single-session mode)

   That's it. Do not narrate in the terminal after this — the user is gone.

### 2. Working

While making progress:

- Post to the thread at genuine milestones: plan decided, first attempt failing, fix applied, tests passing, PR opened.
- If a decision point arrives that you'd normally ask the user about, post a **question** and enter the wait loop (below). Do not silently pick a direction on anything irreversible or cross-cutting.
- For reversible local choices (variable naming, minor refactor shape), decide and mention the choice in your next status update — don't block the user for trivia.

### 3. Wait loop

When you need input:

1. Post the question. Update `last_seen_ts` in the state file to the ts of your question (so your own post is not mistaken for a reply).
2. Call `ScheduleWakeup` with the `prompt` being a short self-briefing like: `Resume AFK session — read thread for replies since last_seen_ts and act on them. Cwd: <pwd>.` Pass `<<autonomous-loop-dynamic>>` ONLY if this session was launched autonomously with no user prompt; otherwise use the self-briefing string.
3. Delay choice (cache-aware — see `ScheduleWakeup` docs):
   - Actively expecting a reply in the next few minutes: **60–270s**.
   - Idle standby (user is at dinner, no rush): **1200–1800s**.
   - Never pick 300s — worst of both cache windows.
4. On wake: read the thread with `oldest = last_seen_ts`. Strip messages whose text starts with the sentinel (those are yours). Anything remaining is user input.
5. **No new input** → update `last_seen_ts` to `now` is NOT correct (you'd miss past messages); keep `last_seen_ts` unchanged and `ScheduleWakeup` again. If this is the Nth consecutive empty wake, increase the delay (backoff): 90s → 270s → 900s → 1800s.
6. **New input** → set `last_seen_ts` to the ts of the newest user message consumed, then act. Parse for commands first, then treat the rest as instruction.

### 4. Close

Session ends when:

- User replies `end`, `stop`, `done afk`, or similar — close explicitly.
- Task completes and user has acknowledged the result (`ok`, `cool`, `thanks`, 👍, etc.).
- Catastrophic failure — post the failure, ask for guidance, and go into long idle (1800s) instead of closing unilaterally.

On close:

- Post a final summary: what got done, what did not, any PRs/commits/links.
- Set `status: closed` in the state file (keep the file around — it's a receipt).
- Terminate any pending `ScheduleWakeup` by not calling it again.
- If a Monitor ticker is armed (see `monitor_task_id` in state), `TaskStop` it.

## Multi-session discipline

The user may have 10 cmux worktrees each running AFK. To keep threads distinguishable on a phone screen:

- The session header includes a **short human label** (e.g., `myapp · bug-fix`), derived from the worktree name + a short task tag the user gave you (or that you inferred). Keep it under ~40 chars.
- Every thread reply is short; the phone screen is small.
- Never post to another session's thread — `thread_ts` from `.afk/session.json` is authoritative for THIS worktree. If you find yourself without a session file, do not guess the thread; start a new one.

## What not to do

- **Do not spam the thread.** One post per tool call will drown the user in notifications.
- **Do not rely on Slack's sender metadata** to distinguish your posts from the user's. The sentinel prefix is load-bearing.
- **Do not take irreversible actions** (force-push, delete, deploy, send external messages, spend money) without an explicit green-light in the thread. AFK mode raises the bar for "ask first," it does not lower it.
- **Do not narrate in the terminal after the initial activation line.** The user is not watching the terminal.
- **Do not edit past Slack posts to "update" them.** Append a new post — users rely on the chronological scroll to see progress.
- **Do not start a new thread per update.** One thread = one session, for the session's whole life.

## Quick recipe

1. Read `references/slack.md` (or the transport the user named).
2. Read `references/protocol.md` for the sentinel, state schema, and user command list.
3. Do the session-start steps above.
4. Work. Post milestones. Block on `ScheduleWakeup` when you need a human.
5. Close cleanly.
