#!/bin/bash
# Read new iMessage/SMS messages for a given handle, newer than a cursor.
#
# Usage:
#   imessage_read.sh <handle> [since_ns]
#
#   handle    — same handle (phone/email) you used to send. Matches against both
#               chat.chat_identifier (for 1:1 conversations the two are equal)
#               and handle.id so this works whether or not the chat has been
#               normalised.
#   since_ns  — cursor in message.date's native format: nanoseconds since
#               2001-01-01 UTC (Apple epoch). Defaults to 0 (return everything).
#
# Output: JSONL (one JSON object per line), oldest-first:
#   {"rowid":…, "date":…, "is_from_me":0|1, "text":"…", "handle":"…"}
#
# Notes:
#   - Opens chat.db read-only (?mode=ro) to avoid lock contention with
#     Messages.app's live writes.
#   - Retries once on SQLITE_BUSY.
#   - Ignores rows with NULL text (attributedBody-only messages are not
#     decoded here — known gap, plain text replies round-trip fine).
#
# Exit codes:
#   0   — success (zero or more lines emitted).
#   2   — bad arguments.
#   66  — chat.db unreadable (likely missing Full Disk Access).
#   1   — any other sqlite3 failure.

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 <handle> [since_ns=0]" >&2
  exit 2
fi

HANDLE="$1"
SINCE="${2:-0}"
DB="$HOME/Library/Messages/chat.db"

# Escape single quotes for safe inline use in the SQL literal.
HANDLE_ESC="${HANDLE//\'/\'\'}"

# SQL literal integers don't need escaping; reject non-numeric since_ns to be
# safe.
if ! [[ "$SINCE" =~ ^-?[0-9]+$ ]]; then
  echo "since_ns must be an integer (nanoseconds since 2001-01-01 UTC)" >&2
  exit 2
fi

read -r -d '' SQL <<SQL || true
SELECT
  json_object(
    'rowid',      message.ROWID,
    'date',       message.date,
    'is_from_me', message.is_from_me,
    'text',       message.text,
    'handle',     handle.id
  )
FROM message
LEFT JOIN handle
  ON message.handle_id = handle.rowid
LEFT JOIN chat_message_join
  ON chat_message_join.message_id = message.ROWID
LEFT JOIN chat
  ON chat.ROWID = chat_message_join.chat_id
WHERE (chat.chat_identifier = '$HANDLE_ESC' OR handle.id = '$HANDLE_ESC')
  AND message.date > $SINCE
  AND message.text IS NOT NULL
ORDER BY message.date ASC;
SQL

try_read() {
  sqlite3 "file:$DB?mode=ro" "$SQL" 2>&1
}

if [[ ! -r "$DB" ]]; then
  echo "chat.db not readable at $DB — likely missing Full Disk Access" >&2
  exit 66
fi

OUT=$(try_read) || RC=$?
RC="${RC:-0}"

if [[ "$RC" -ne 0 ]]; then
  if echo "$OUT" | grep -qi "database is locked"; then
    sleep 0.5
    OUT=$(try_read) || RC=$?
    RC="${RC:-0}"
  fi
fi

if [[ "$RC" -ne 0 ]]; then
  echo "$OUT" >&2
  if echo "$OUT" | grep -qi "unable to open database"; then
    exit 66
  fi
  exit "$RC"
fi

# sqlite3 emits one JSON object per row, one per line. Pass through as-is.
printf '%s' "$OUT"
# Ensure trailing newline so the last object is a complete JSONL record.
[[ -n "$OUT" && "${OUT: -1}" != $'\n' ]] && echo
exit 0
