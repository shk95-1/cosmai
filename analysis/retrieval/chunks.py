"""The chunks that go into embedding and indexing, and the validator of their contract
(ydc chunks.py, v0.1.0 02440ab; changed later in v0.2.0).

The contract has five columns: chunk_id (`{doc_id}#{ordinal}`) · doc_id · source · ordinal (from 0, with no
gaps) · text (put through normalize_text). doc_id is common to all sources, so merging sources is a
concatenation rather than a join.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from analysis.retrieval.normalize import normalize_text

MAX_CHARS = 500  # the 512-token cap of multilingual-e5; Korean is ~1.5 chars/token -> about 330 tokens
FIELDS = ("chunk_id", "doc_id", "source", "ordinal", "text")
SAMPLES_PER_KIND = 3  # one kind can come up tens of thousands of times -- only samples keep the report read

# Priority of the places to cut at. Cutting mid-sentence breaks the meaning of that chunk alone.
BREAKS = (". ", "! ", "? ", "다. ", "요. ", "\n", " ")


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Into pieces of at most limit characters. No overlap is kept -- with the same sentence in several
    chunks the top of the search fills up with one document, and evidence has to come from several."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []

    out: list[str] = []
    while len(text) > limit:
        window = text[:limit]
        cut = -1
        for mark in BREAKS:
            found = window.rfind(mark)
            # Cutting too early shatters the piece. It has to be past the halfway mark.
            if found > limit // 2:
                cut = found + len(mark)
                break
        if cut <= 0:
            cut = limit  # with no place to cut at, cut anyway
        piece = text[:cut].strip()
        if piece:
            out.append(piece)
        text = text[cut:].strip()
    if text:
        out.append(text)
    return out


def problem_kind(message: str) -> str:
    """The kind of a violation message. It is the unit the cap counts and the unit the "N kinds" of the report
    counts -- with different rules in the two places only one cap applies and the other number counts
    something else (#18 M12)."""
    return message.split(":")[0]


def row_ref(row: Mapping[str, object], line: int) -> str:
    """The coordinate a violation message points at. The message exists so a person can find the original, so
    the table's primary key, chunk_id, comes first -- one `WHERE chunk_id = ...` produces that row. Failing
    that, the (doc_id, ordinal) index; failing that, only the order it was scanned in (that number is unique
    only if the caller keeps counting across calls)."""
    chunk_id = str(row.get("chunk_id") or "").strip()
    if chunk_id:
        return chunk_id
    doc_id = str(row.get("doc_id") or "").strip()
    if doc_id:
        return f"doc_id={doc_id} ordinal={row.get('ordinal')!r}"
    source = str(row.get("source") or "").strip()
    return f"{line}행 (source={source})" if source else f"{line}행"


def check_rows(
    rows: Iterable[Mapping[str, object]], *, first_line: int = 2
) -> tuple[list[str], Counter, list[int], int]:
    """(violations, count per source, list of lengths, document count). No file is read, so this checks
    anywhere.

    `first_line` is where the numbering starts for rows that have no coordinate at all. Unless the caller
    that splits into batches (pipeline.run) shifts it by the number of rows already checked, the same numbers
    come up again in every batch (#27).
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    ordinals: dict[str, list[int]] = defaultdict(list)
    per_source: Counter = Counter()
    lengths: list[int] = []

    def note(message: str) -> None:
        # This cap applies **only inside one check_rows**. The cap for a whole run has to be counted by the
        # caller (pipeline.run) -- called again per batch, this starts from 0 every time.
        kind = problem_kind(message)
        if sum(1 for p in problems if problem_kind(p) == kind) < SAMPLES_PER_KIND:
            problems.append(message)

    for line, row in enumerate(rows, first_line):
        chunk_id = str(row.get("chunk_id") or "").strip()
        doc_id = str(row.get("doc_id") or "").strip()
        source = str(row.get("source") or "").strip()
        text = str(row.get("text") or "")
        ref = row_ref(row, line)

        if not chunk_id:
            note(f"chunk_id 없음: {ref}")
        elif chunk_id in seen_ids:
            note(f"chunk_id 중복: {ref}")
        seen_ids.add(chunk_id)

        if not doc_id:
            note(f"doc_id 없음: {ref}")
        if not source:
            note(f"source 없음: {ref}")
        if not text.strip():
            note(f"text 비어 있음: {ref}")
        # If the normalization rule differs per source, comparing scores across sources means nothing.
        if text != normalize_text(text):
            note(f"정규화 안 됨: {ref}")
        if len(text) > MAX_CHARS * 2:
            note(f"너무 긺: {ref} — {len(text)}자")

        try:
            ordinals[doc_id].append(int(row["ordinal"]))  # pyright: ignore[reportArgumentType]
        except (KeyError, TypeError, ValueError):
            note(f"ordinal 이 정수가 아님: {ref} — {row.get('ordinal')!r}")

        per_source[source] += 1
        lengths.append(len(text))

    for doc_id, values in ordinals.items():
        if sorted(values) != list(range(len(values))):
            note(f"ordinal 이 0 부터 연속이 아님: {doc_id} -> {sorted(values)[:6]}")
    return problems, per_source, lengths, len(ordinals)
