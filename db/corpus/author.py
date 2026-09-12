"""The one place a comment author's channel identifier becomes a hash (fork #92).

#91 decided a comment row keeps the author's channel identifier only as a hash and stores no display
name. The rule is worth nothing if two implementations of the hash exist: the archive's stored
`author_channel_hash` values were made with this exact form, and the evidence and sensitivity stages
detect a creator comment by an **exact match** against them (`contracts/interfaces.md` §Ad and
sponsorship marking). A second implementation that drifts by one character does not raise -- it
quietly matches nothing, and the evidence stops being consumer speech while the output stays just as
plausible. So the function lives here, above both the corpus loader (`db/corpus`) and the analysis
packages that rebuild it, and every caller imports this one.
"""

from __future__ import annotations

import hashlib
import re

# ydc's collector hashed "youtube:" + the channel id, and the 247,338 comment documents of the
# 2026-08-19 handover carry that form -- it is a stored fact now, not a choice.
HASH_PREFIX = "youtube:"
HASH_LENGTH = 24

# What a raw YouTube channel id looks like: "UC" plus 22 characters of base64url. It is here rather
# than in the checks that use it because both the loader's refusal and the invariant view have to
# recognise the same shape (`db/views/author_identifier_violation.sql`).
RAW_CHANNEL_ID = re.compile(r"^UC[0-9A-Za-z_-]{22}$")

# What a stored `author_channel_hash` must look like. The negative check above only catches an identifier
# pasted in whole; a 64-character digest, an uppercase one or one hashed without the prefix is the right
# kind of thing with the wrong value, and it fails silently -- creator-comment marking matches zero rows
# and the cards that come out are the creator's own speech sold as consumer reaction.
AUTHOR_HASH = re.compile(r"^[0-9a-f]{24}$")


def author_hash(channel_id: str) -> str:
    """`sha256("youtube:" + channel_id)[:24]` -- the rule the collector used, so the value is
    rebuildable from a channel id and nothing else about the person is kept."""
    return hashlib.sha256(f"{HASH_PREFIX}{channel_id}".encode()).hexdigest()[:HASH_LENGTH]
