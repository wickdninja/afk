#!/bin/bash
# Read new iMessage/SMS messages for a given handle, newer than a cursor.
#
# This is a thin wrapper around imessage_read.py — kept for backwards
# compatibility with callers that hard-code the .sh extension.
#
# The Python implementation:
#   - decodes message.attributedBody for messages where message.text is NULL
#     (required on newer macOS — many replies live only in attributedBody)
#   - does NOT filter by is_from_me; the sentinel-prefix filter is the loop's
#     responsibility (when both ends share an Apple ID, every row is
#     is_from_me=1)
#
# Usage:
#   imessage_read.sh <handle> [since_ns]

set -euo pipefail
exec "$(dirname "$0")/imessage_read.py" "$@"
