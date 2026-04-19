#!/usr/bin/env python3
"""Read new iMessage/SMS messages for a given handle, newer than a cursor.

Usage:
    imessage_read.py <handle> [since_ns]

Outputs JSONL (one JSON object per line), oldest-first:
    {"rowid":..., "date":..., "is_from_me":0|1, "text":"...", "handle":"..."}

Improvements over the bash version:
- Decodes message.attributedBody (NSAttributedString typedstream) when
  message.text is NULL. This is required on newer macOS — many replies have
  text=NULL and the body is only in attributedBody.
- Does NOT filter by is_from_me. The sentinel-prefix filter is the responsibility
  of the AFK loop, because when both ends share an Apple ID every row is
  is_from_me=1 in chat.db.

Exit codes:
    0  — success
    2  — bad args
    66 — chat.db unreadable (likely missing Full Disk Access)
    1  — other sqlite error
"""

import json
import os
import re
import sqlite3
import sys
import time


def decode_attributed_body(blob):
    """Extract the NSString text from an NSAttributedString typedstream blob.

    The typedstream format wraps an NSString whose backing is encoded as a
    C-string ('+') after the NSString class marker. The length is either a
    single byte (< 0x81) or 0x81 followed by a 2-byte big-endian length, or
    0x82 followed by a 4-byte big-endian length.

    This implementation finds the first '+' marker following 'NSString' and
    reads the length-prefixed UTF-8 bytes. Robust enough for plain replies and
    common rich-text/attachment messages.
    """
    if not blob:
        return None

    idx = blob.find(b"NSString")
    if idx < 0:
        return None

    # Skip ahead past the NSString class header to find the type marker '+'.
    # In practice it appears within the next 16 bytes.
    plus = blob.find(b"+", idx)
    if plus < 0 or plus - idx > 32:
        return None

    cursor = plus + 1
    if cursor >= len(blob):
        return None

    length_byte = blob[cursor]
    cursor += 1

    if length_byte == 0x81:
        if cursor + 2 > len(blob):
            return None
        length = int.from_bytes(blob[cursor : cursor + 2], "little")
        cursor += 2
    elif length_byte == 0x82:
        if cursor + 4 > len(blob):
            return None
        length = int.from_bytes(blob[cursor : cursor + 4], "little")
        cursor += 4
    else:
        length = length_byte

    if cursor + length > len(blob):
        return None

    try:
        return blob[cursor : cursor + length].decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("usage: imessage_read.py <handle> [since_ns=0]", file=sys.stderr)
        return 2

    handle = sys.argv[1]
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
            f"chat.db not readable at {db_path} — likely missing Full Disk Access",
            file=sys.stderr,
        )
        return 66

    sql = """
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
        WHERE (chat.chat_identifier = ? OR handle.id = ?)
            AND message.date > ?
        ORDER BY message.date ASC
    """

    def query():
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            cur = conn.cursor()
            cur.execute(sql, (handle, handle, since_int))
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

    out = []
    for rowid, date, is_from_me, text, attributed_body, handle_id in rows:
        body = text
        if body is None and attributed_body is not None:
            body = decode_attributed_body(bytes(attributed_body))
        if body is None:
            continue
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
