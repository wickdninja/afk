#!/usr/bin/env python3
"""Read new iMessage/SMS messages for a given handle, newer than a cursor.

Usage:
    imessage_read.py <handle>[,<handle2>,...] [since_ns]

Multiple handles (comma-separated) catch the same user replying from any of
their registered aliases -- e.g. one Apple ID with both an email
(nross63@gmail.com) and a phone number (+12607103877). iMessage records the
reply under whichever alias the phone happened to be using; polling only the
pinned handle silently drops replies sent from the other one. Always pass
every alias the user might reply from.

Outputs JSONL (one JSON object per line), oldest-first:
    {"rowid":..., "date":..., "is_from_me":0|1, "text":"...", "handle":"..."}

Two things this script gets right that a naive reader does not:

1. attributedBody decode. On current macOS (Sonoma / Sequoia / Tahoe) the plain
   `message.text` column is frequently NULL and the body lives ONLY in
   `message.attributedBody`, a binary NSAttributedString typedstream blob. This
   script decodes that blob to plain UTF-8 (see decode_attributed_body) for BOTH
   inbound and outbound rows whenever `text` is NULL/empty. Without it, real
   text replies come back empty and the reader emits nothing.

2. No is_from_me filter. This script does NOT filter by is_from_me. When both
   ends share an Apple ID, chat.db marks every row is_from_me=1, so an
   is_from_me=0 filter drops every user reply. The sentinel prefix (see
   references/imessage.md) is the sole load-bearing "is this mine?" filter, and
   that check belongs to the AFK loop, not here. This reader's contract is:
   emit every message body in the conversation; let the consumer filter on the
   sentinel.

Exit codes:
    0  -- success
    2  -- bad args
    66 -- chat.db unreadable (likely missing Full Disk Access)
    1  -- other sqlite error
"""

import json
import os
import re
import sqlite3
import sys
import time

# Unicode object-replacement character. iMessage uses it as the inline
# placeholder for an attachment (image/file/sticker) inside the text run. On its
# own it carries no textual content; stripped so a pure-attachment message
# yields no phantom body and a captioned attachment keeps only the caption.
OBJECT_REPLACEMENT = "￼"


def _read_varint(blob, pos):
    """Read a typedstream length varint at blob[pos].

    typedstream encodes a small non-negative length as a single byte < 0x80.
    Larger values use a marker byte followed by a little-endian integer:
        0x81  -> next 2 bytes  (uint16 LE)
        0x82  -> next 4 bytes  (uint32 LE)
        0x83  -> next 8 bytes  (uint64 LE)
    Returns (length, next_pos) or (None, pos) if it cannot be read.
    """
    if pos >= len(blob):
        return None, pos
    marker = blob[pos]
    pos += 1
    if marker < 0x80:
        return marker, pos
    width = {0x81: 2, 0x82: 4, 0x83: 8}.get(marker)
    if width is None:
        return None, pos
    if pos + width > len(blob):
        return None, pos
    return int.from_bytes(blob[pos : pos + width], "little"), pos + width


def decode_attributed_body(blob):
    """Extract the plain string body from an NSAttributedString typedstream blob.

    Layout, observed live on macOS chat.db rows:

        ... NSString \\x01 \\x94 \\x84 \\x01 + <len-varint> <utf8 bytes> \\x86 ...

    The string backing is written after the NSString class marker as the '+'
    (0x2B) char-type token, then a length varint, then that many UTF-8 bytes.
    We locate the NSString marker, find the '+' token just after the class
    header, read the varint, and decode the byte slice. This is a real decode of
    the documented typedstream shape, not a regex over the blob -- it handles
    multi-byte emoji and embedded newlines correctly because it slices an exact,
    length-prefixed byte range.

    Returns the decoded body (object-replacement chars stripped), or None if the
    blob has no decodable NSString backing (e.g. a pure tapback/reaction, which
    stores its payload as NSNumber/NSDictionary attributes with no string).
    """
    if not blob:
        return None

    idx = blob.find(b"NSString")
    if idx < 0:
        return None

    # The '+' char-type token sits a few bytes past the class name (after the
    # version/inheritance bytes). Search a generous window; bail if it is not
    # close, which means this is not the length-prefixed-NSString shape.
    plus = blob.find(b"+", idx)
    if plus < 0 or plus - idx > 64:
        return None

    length, cursor = _read_varint(blob, plus + 1)
    if length is None or length < 0 or cursor + length > len(blob):
        return None

    raw = blob[cursor : cursor + length]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # Length is exact for valid rows; replace only as a last resort so a
        # single bad byte never sinks the whole reply.
        text = raw.decode("utf-8", errors="replace")

    text = text.replace(OBJECT_REPLACEMENT, "")
    if text == "":
        return None
    return text


def resolve_body(text, attributed_body):
    """Return the best plain-text body for a row, or None to skip it."""
    if text is not None and text != "":
        cleaned = text.replace(OBJECT_REPLACEMENT, "")
        if cleaned != "":
            return cleaned
    if attributed_body is not None:
        return decode_attributed_body(bytes(attributed_body))
    return None


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("usage: imessage_read.py <handle>[,<handle2>,...] [since_ns=0]", file=sys.stderr)
        return 2

    handles = [h.strip() for h in sys.argv[1].split(",") if h.strip()]
    if not handles:
        print("at least one handle required", file=sys.stderr)
        return 2
    since = sys.argv[2] if len(sys.argv) == 3 else "0"
    if not re.fullmatch(r"-?\d+", since):
        print(
            "since_ns must be an integer (nanoseconds since 2001-01-01 UTC)",
            file=sys.stderr,
        )
        return 2
    since_int = int(since)

    db_path = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.access(db_path, os.R_OK):
        print(
            f"chat.db not readable at {db_path} -- likely missing Full Disk Access",
            file=sys.stderr,
        )
        return 66

    placeholders = ",".join(["?"] * len(handles))
    sql = f"""
        SELECT
            message.ROWID,
            message.date,
            message.is_from_me,
            message.text,
            message.attributedBody,
            handle.id
        FROM message
        LEFT JOIN handle
            ON message.handle_id = handle.rowid
        LEFT JOIN chat_message_join
            ON chat_message_join.message_id = message.ROWID
        LEFT JOIN chat
            ON chat.ROWID = chat_message_join.chat_id
        WHERE (chat.chat_identifier IN ({placeholders}) OR handle.id IN ({placeholders}))
            AND message.date > ?
        ORDER BY message.date ASC
    """
    params = (*handles, *handles, since_int)

    def query():
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    try:
        rows = query()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower():
            time.sleep(0.5)
            try:
                rows = query()
            except sqlite3.OperationalError as e2:
                print(str(e2), file=sys.stderr)
                return 1
        elif "unable to open" in str(e).lower():
            print(str(e), file=sys.stderr)
            return 66
        else:
            print(str(e), file=sys.stderr)
            return 1

    # De-dup: the same message can join to multiple chats/handles (aliases +
    # group rows), producing duplicate ROWIDs. Emit each ROWID once.
    seen = set()
    out = []
    for rowid, date, is_from_me, text, attributed_body, handle_id in rows:
        if rowid in seen:
            continue
        body = resolve_body(text, attributed_body)
        if body is None:
            continue
        seen.add(rowid)
        out.append(
            json.dumps(
                {
                    "rowid": rowid,
                    "date": date,
                    "is_from_me": is_from_me,
                    "text": body,
                    "handle": handle_id,
                },
                ensure_ascii=False,
            )
        )

    if out:
        print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
