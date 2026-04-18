#!/bin/bash
# Send an iMessage (or SMS) via AppleScript.
#
# Usage:
#   imessage_send.sh <handle> <body_file> [service]
#
#   handle     — E.164 phone (e.g. +15551234567) or email registered with iMessage.
#   body_file  — path to a file whose contents are the literal message body.
#                A file (not a shell arg) is used to avoid AppleScript quote/newline
#                escaping issues with arbitrary content.
#   service    — "iMessage" (default) or "SMS".
#
# Exit codes:
#   0   — send dispatched to Messages.app.
#   2   — bad arguments.
#   3   — body file missing or unreadable.
#   64  — Automation permission not granted (user must fix in System Settings).
#   65  — Messages.app / iMessage not available.
#   1   — any other osascript failure (stderr is the AppleScript error).

set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "usage: $0 <handle> <body_file> [service=iMessage]" >&2
  exit 2
fi

HANDLE="$1"
BODY_FILE="$2"
SERVICE="${3:-iMessage}"

if [[ ! -r "$BODY_FILE" ]]; then
  echo "body file not readable: $BODY_FILE" >&2
  exit 3
fi

# Re-encode the body as an AppleScript string literal via Python so that
# newlines, quotes, and backslashes survive correctly. json.dumps happens to
# produce a valid AppleScript string: "…" with \n, \", \\ escapes, all of which
# AppleScript accepts.
BODY_LITERAL=$(python3 -c 'import sys,json; sys.stdout.write(json.dumps(sys.stdin.read()))' < "$BODY_FILE")

# Build the AppleScript. Using -e per line keeps things readable and avoids
# heredoc-vs-shell-quoting confusion.
OUT=$(osascript \
  -e "tell application \"Messages\"" \
  -e "  set targetService to 1st service whose service type = $SERVICE" \
  -e "  set targetBuddy to buddy \"$HANDLE\" of targetService" \
  -e "  send $BODY_LITERAL to targetBuddy" \
  -e "end tell" 2>&1) || {
    RC=$?
    echo "$OUT" >&2
    # Map common Apple event errors to dedicated exit codes.
    if echo "$OUT" | grep -qE '(-1743|Not authorized to send Apple events)'; then
      exit 64
    fi
    if echo "$OUT" | grep -qiE "(Can't get buddy|service type|Messages got an error)"; then
      exit 65
    fi
    exit "$RC"
  }

exit 0
