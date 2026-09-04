"""ydc `lexicon.json` 의 조사 30개 처분 (포크 #37).

ydc 는 조사를 **문자열로 잘라** 냈고 그 목록 30개를 코퍼스 실측으로 검증해 두었다(v1.1.1: 고유 토큰
462,414개를 세어 제거 0개). cosmai 에는 그 단계가 없다 -- 형태소는 Kiwi 가 가르고 `bm25.tokenize` 는
`KIWI_TAGS` 밖의 태그를 버린다. 그래서 목록은 승격하지 않고 **대체**로 처분했고, 남기는 것은 목록이
아니라 그 목록이 지키던 불변식이다.

`KIWI_TAGS` 를 넓히거나 한 글자 명사 규칙을 지우면 조사가 토큰으로 새는데, 그것은 예외를 내지 않고
순위만 조용히 바꾼다 -- ydc 목록이 여기서는 그 회귀를 잡는 시험 벡터로만 산다.

한 시험이 30개를 다 도는 것은 취향이 아니다: 활성 사전을 세우는 autouse 픽스처가 Kiwi 를 버리므로
(`bm25._forget_topics`) 매개변수 하나가 곧 형태소 분석기 한 벌이다.
"""

from __future__ import annotations

from pathlib import Path

from analysis.retrieval.bm25 import tokenize

ENTRYPOINTS = Path(__file__).resolve().parents[2] / "contracts" / "entrypoints.md"

# ydc lexicon.json v1.1.1 의 `particles` 그대로. 사전이 아니라 시험 벡터라 코드에 산다.
PARTICLES = tuple(
    "이랑 에게서 에서 으로 에게 한테 부터 까지 보다 처럼 마다 조차 밖에 라는 이나"
    " 은 는 이 가 을 를 의 에 도 만 과 와 랑 나 께".split()
)

# 받침 없는 말 뒤에만 서는 이형태. 문법에 안 맞는 짝을 넣으면 Kiwi 가 다르게 갈라 시험이 무의미해진다.
AFTER_VOWEL = frozenset({"는", "가", "를", "와", "랑", "나"})
FRAME = "이 제품은 {word} 정말 좋았어요"


def _stem(particle: str) -> str:
    return "재도포" if particle in AFTER_VOWEL else "선크림"


def test_no_particle_changes_a_single_token():
    off = {p: tokenize(FRAME.format(word=_stem(p) + p)) for p in PARTICLES}
    want = {p: tokenize(FRAME.format(word=_stem(p))) for p in PARTICLES}
    assert {p: t for p, t in off.items() if t != want[p]} == {}


def test_every_stem_survives_whole_and_no_particle_becomes_a_token():
    """어간이 쪼개져도 조사는 사라지므로 앞 시험만으로는 절반이다."""
    leaked = {}
    for particle in PARTICLES:
        tokens = tokenize(FRAME.format(word=_stem(particle) + particle))
        if _stem(particle) not in tokens or particle in tokens:
            leaked[particle] = tokens
    assert leaked == {}


def test_the_contract_says_the_tags_do_the_cutting():
    """이 처분이 코드에만 있으면 다음 사람은 "빠뜨린 것" 과 구분하지 못한다.

    조사 쪽만 집는다 -- 같은 절의 불용어 문장은 질의 축(포크 #46)이 다시 열 수 있고, 조사 처분 시험이
    그 문장을 인질로 잡을 이유가 없다."""
    assert "조사는 Kiwi 의 태그가 가른다" in ENTRYPOINTS.read_text(encoding="utf-8")
