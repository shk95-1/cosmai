"""The disposal of the 30 particles of ydc `lexicon.json` (fork #37).

ydc cut the particles **as strings** and had validated that list of 30 by measuring the corpus (v1.1.1:
462,414 unique tokens counted, 0 removed). cosmai has no such stage -- Kiwi splits the morphemes and
`bm25.tokenize` drops any tag outside `KIWI_TAGS`. So the list was disposed of by **replacement** rather than
promoted, and what is kept is not the list but the invariant that list was holding.

Widen `KIWI_TAGS` or delete the one-character-noun rule and particles leak into the tokens, which raises no
exception and only changes the ranking quietly -- the ydc list lives here only as the test vector that
catches that regression.

One test running all 30 is not a matter of taste: the autouse fixture that sets the active dictionary up
throws Kiwi away (`bm25._forget_topics`), so one parameter is one whole morphological analyzer.
"""

from __future__ import annotations

from pathlib import Path

from analysis.retrieval.bm25 import tokenize

ENTRYPOINTS = Path(__file__).resolve().parents[2] / "contracts" / "entrypoints.md"

# The `particles` of ydc lexicon.json v1.1.1 as they are. Not a dictionary but a test vector, so it lives in
# the code.
PARTICLES = tuple(
    "이랑 에게서 에서 으로 에게 한테 부터 까지 보다 처럼 마다 조차 밖에 라는 이나"
    " 은 는 이 가 을 를 의 에 도 만 과 와 랑 나 께".split()
)

# The allomorphs that stand only after a word ending in a vowel. An ungrammatical pair makes Kiwi split it
# differently and the test meaningless.
AFTER_VOWEL = frozenset({"는", "가", "를", "와", "랑", "나"})
FRAME = "이 제품은 {word} 정말 좋았어요"


def _stem(particle: str) -> str:
    return "재도포" if particle in AFTER_VOWEL else "선크림"


def test_no_particle_changes_a_single_token():
    off = {p: tokenize(FRAME.format(word=_stem(p) + p)) for p in PARTICLES}
    want = {p: tokenize(FRAME.format(word=_stem(p))) for p in PARTICLES}
    assert {p: t for p, t in off.items() if t != want[p]} == {}


def test_every_stem_survives_whole_and_no_particle_becomes_a_token():
    """A split stem still loses the particle, so the test above is only half of it."""
    leaked = {}
    for particle in PARTICLES:
        tokens = tokenize(FRAME.format(word=_stem(particle) + particle))
        if _stem(particle) not in tokens or particle in tokens:
            leaked[particle] = tokens
    assert leaked == {}


def test_the_contract_says_the_tags_do_the_cutting():
    """With this disposal living only in the code, the next person cannot tell it from "it was left out".

    Only the particle side is picked up -- the stopword sentence of the same section may be reopened by the
    query axis (fork #46), and there is no reason for the particle-disposal test to hold that sentence
    hostage."""
    assert "조사는 Kiwi 의 태그가 가른다" in ENTRYPOINTS.read_text(encoding="utf-8")
