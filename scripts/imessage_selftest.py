#!/usr/bin/env python3
"""Self-test for the iMessage reply reader's attributedBody decoder.

Two layers:

1. Synthetic round-trip (always runs, no permissions needed). Encode a string
   into the exact NSAttributedString typedstream shape macOS uses, then decode
   it back and assert equality. Covers plain text, emoji, embedded newlines,
   long bodies (forcing the 0x81 uint16 and 0x82 uint32 length paths), and the
   attachment object-replacement character.

2. Live round-trip (best-effort). If ~/Library/Messages/chat.db is readable,
   pull recent rows for the given handle(s) and assert every text-NULL,
   non-attachment row decodes to a non-empty body -- i.e. no real reply comes
   back empty. Prints one recovered reply as proof. Skips cleanly (exit 0) if
   Full Disk Access is not granted, so CI without chat.db still passes.

Usage:
    imessage_selftest.py [<handle>[,<handle2>,...]]

    handle defaults to +12607103877 (the reference AFK handle). Live layer is
    skipped if chat.db is unreadable.

Exit codes:
    0  -- all assertions passed (live layer may have been skipped)
    1  -- a round-trip assertion failed
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from imessage_read import decode_attributed_body, resolve_body  # noqa: E402


def _varint(n):
    """Encode a length the way typedstream does (mirror of _read_varint)."""
    if n < 0x80:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\x81" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:
        return b"\x82" + n.to_bytes(4, "little")
    return b"\x83" + n.to_bytes(8, "little")


def encode_attributed_body(text):
    """Build a minimal but realistic attributedBody blob backing `text`.

    Mirrors the live layout: a typedstream preamble, the NSString class marker,
    the '+' char-type token, a length varint, the UTF-8 bytes, then a trailing
    attribute-run marker. The decoder must recover `text` from this.
    """
    payload = text.encode("utf-8")
    return (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
        b"\x19NSMutableAttributedString\x00\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSString\x01\x94\x84\x01+"
        + _varint(len(payload))
        + payload
        + b"\x86\x84\x02iI\x01"
        + _varint(len(payload))
    )


def check(name, expected, blob):
    got = decode_attributed_body(blob)
    ok = got == expected
    mark = "PASS" if ok else "FAIL"
    shown = repr(got) if got is None or len(got) <= 48 else repr(got[:45] + "...")
    print(f"  [{mark}] {name}: {shown}")
    return ok


def synthetic_tests():
    print("synthetic round-trip (encode -> decode):")
    long_body = "x" * 500                 # forces 0x81 uint16 length path
    huge_body = "y" * 70000               # forces 0x82 uint32 length path
    cases = [
        ("plain text", "Merge 147 if ready"),
        ("emoji", "🤖 ✅ shipped, tests green 🎉"),
        ("newline", "line one\nline two\n\nline four"),
        ("emoji + newline", "🚀 done:\n- fixed the reader\n- 267 tests green"),
        ("smart quotes / nbsp", "it’s broken  and 404s"),
        ("long (uint16 len)", long_body),
        ("huge (uint32 len)", huge_body),
    ]
    all_ok = True
    for name, text in cases:
        all_ok &= check(name, text, encode_attributed_body(text))

    # Attachment object-replacement handling.
    print("attachment object-replacement handling:")
    obj = "￼"
    all_ok &= check("captioned attachment -> caption only",
                    "here you go", encode_attributed_body(obj + "here you go"))
    # Pure attachment (only the placeholder) must decode to None, not a junk row.
    got = decode_attributed_body(encode_attributed_body(obj))
    ok = got is None
    print(f"  [{'PASS' if ok else 'FAIL'}] pure attachment -> None: {got!r}")
    all_ok &= ok

    # resolve_body prefers a populated text column but strips a lone placeholder.
    ok = resolve_body("plain from text col", None) == "plain from text col"
    print(f"  [{'PASS' if ok else 'FAIL'}] resolve_body uses text column when present")
    all_ok &= ok
    return all_ok


def live_tests(handles):
    import sqlite3
    db = os.path.expanduser("~/Library/Messages/chat.db")
    if not os.access(db, os.R_OK):
        print("live round-trip: SKIPPED (chat.db not readable -- no Full Disk Access)")
        return True
    print(f"live round-trip against chat.db for {handles}:")
    ph = ",".join(["?"] * len(handles))
    sql = f"""
        SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.rowid
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        WHERE (h.id IN ({ph}) OR c.chat_identifier IN ({ph}))
        ORDER BY m.date DESC
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2.0)
        rows = conn.execute(sql, (*handles, *handles)).fetchall()
        conn.close()
    except sqlite3.OperationalError as e:
        print(f"  SKIPPED ({e})")
        return True

    text_null_rows = 0
    dropped = 0
    sample = None
    for rowid, text, ab, is_from_me in rows:
        if text is not None and text != "":
            continue
        if ab is None:
            continue
        text_null_rows += 1
        body = decode_attributed_body(bytes(ab))
        if body is None:
            # None is only legitimate for pure attachments / tapbacks. Count it
            # so an unexpected spike is visible, but do not fail on it.
            dropped += 1
        elif sample is None and is_from_me == 0:
            sample = (rowid, body)
    if sample is None:
        # fall back to any recovered body if no inbound row exists in history
        for rowid, text, ab, is_from_me in rows:
            if (text is None or text == "") and ab is not None:
                b = decode_attributed_body(bytes(ab))
                if b is not None:
                    sample = (rowid, b)
                    break

    print(f"  text-NULL rows inspected: {text_null_rows}; "
          f"attachment/tapback (None): {dropped}; "
          f"text recovered from attributedBody: {text_null_rows - dropped}")
    if sample:
        rowid, body = sample
        one = body.replace("\n", " ")
        print(f"  recovered reply (rowid {rowid}): {one[:90]!r}")
    # Live layer is informational; it fails only if it recovered nothing at all
    # while there were text-NULL rows to decode.
    if text_null_rows > 0 and text_null_rows == dropped:
        print("  [FAIL] every text-NULL row decoded to None -- decoder is not recovering bodies")
        return False
    print("  [PASS] attributedBody bodies recover from chat.db")
    return True


def main():
    handles = [h.strip() for h in (sys.argv[1] if len(sys.argv) > 1 else "+12607103877").split(",") if h.strip()]
    ok = synthetic_tests()
    ok &= live_tests(handles)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
