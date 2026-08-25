"""BM25 어휘 검색 (slices/ydc/bm25.py). 벡터가 넘어야 하는 기준선이다.

정확 일치가 정답인 말이 많다 -- `에칠헥실트리아존` · `SPF50+` · 브랜드명. 벡터에 넣으면
성분이 다른 것을 비슷하다고 하므로 그건 순위 문제가 아니라 오답이다. 그쪽을 이 파일이 맡는다.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from analysis.retrieval import topics
from analysis.retrieval.topics import TOPICS

if TYPE_CHECKING:  # 타입 전용: 런타임에 kiwipiepy 를 끌어오면 --help 한 번에 모델이 뜬다.
    from kiwipiepy import Kiwi

K1 = 1.2  # tf 포화 계수. 표준값
B = 0.75  # 길이 보정 강도. 표준값
# SPF50+ 를 한 토큰으로. `spf` + `50` 으로 갈리면 차단지수 검색이 전부 어긋난다.
LATIN_RE = re.compile(r"[a-z0-9]+(?:[+\-][a-z0-9]+)*\+?")
HANGUL_RE = re.compile(r"[가-힣]")

# 내용어만. VA·VV 를 넣는 것은 `끈적이다`·`시리다` 처럼 서술어가 주제인 경우가 많아서다.
# SL(외국어)·SN(숫자)은 일부러 뺐다 -- 라틴 토큰은 정규식이 담당하고, 둘 다 넣으면 tf 가 부푼다.
KIWI_TAGS = frozenset({"NNG", "NNP", "VA", "VV", "XR", "MAG"})
# 한 글자 명사는 잡음이지만(거·것·수) 서술어 어간은 한 글자가 정상이다(`하얗`·`싫`).
NOUN_TAGS = frozenset({"NNG", "NNP"})
# 0 으로 두면 등록해도 기존 분석을 못 이겨 `신제품` 이 신(XPN) + 제품(NNG) 으로 갈린다.
USER_WORD_SCORE = 3.0

# 정본은 이 디렉터리다. analysis/slices/ydc/seeds/ 에 md5 가 같은 사본이 있지만 그쪽은 이식 전
# 원본(읽기 전용 참조, #9 가 지운다)이라 고쳐도 색인에 아무 일도 일어나지 않는다(#18 M15).
DICT_DIR = Path(__file__).resolve().parent / "dict"
DICTIONARIES = (DICT_DIR / "user_dictionary.tsv", DICT_DIR / "ingredient_dictionary.tsv")
# 토큰을 정하는 입력 전부. topics.py 가 여기 드는 이유는 그 별칭이 Kiwi 사용자 단어로 등록되고
# (kiwi()) 부분문자열 확장 목록도 되기 때문이다(expand()) -- 색인 캐시 서명이 이 전부를 걸어야 한다.
TOKENIZER_INPUTS = (*DICTIONARIES, Path(topics.__file__).resolve())


_kiwi = None
_topic_words: list[str] | None = None
_expand_words: list[str] | None = None
# 상한을 두지 않는다: 토큰 하나에 항목 하나라 코퍼스 어휘 수에서 멎고(실측 3,000청크 -> 항목
# 3,013개 · 약 92B, 이어 돈 질의 150번이 더한 것은 2개), 그 어휘로 세운 postings 를 같은
# 프로세스가 이미 훨씬 크게 물고 있다 -- 프로세스는 CLI 한 번이다(#18 M16).
_expanded: dict[str, tuple[str, ...]] = {}


def topic_words() -> list[str]:
    """Kiwi 에 통째로 등록할 별칭. 등록 전에 Kiwi 에게 물어보고 고른다 -- 이미 한 낱말인 것과
    활용형(VA·VV)은 빼야 한다. 활용형을 명사로 박으면 형태소 통합이 깨진다."""
    global _topic_words
    if _topic_words is not None:
        return _topic_words

    from kiwipiepy import Kiwi

    bare = Kiwi()  # 사용자 사전을 얹지 않은 판정용
    words = set()
    for entry in TOPICS:
        for alias in entry["ko"]:
            # 공백이 든 별칭·조사가 붙은 별칭은 낱말이 아니라 Kiwi 에 넣을 수 없다.
            if " " in alias or len(alias) < 2 or alias.endswith("에"):
                continue
            # kiwipiepy 의 오버로드는 한 문장 입력도 배치 반환과 함께 묶어 놓아 좁혀지지 않는다.
            tokens: Any = bare.tokenize(alias)
            if len(tokens) == 1 and tokens[0].form == alias:
                continue  # 이미 한 낱말이다
            if any(t.tag.split("-")[0] in {"VA", "VV"} for t in tokens):
                continue  # 활용형이다. 명사로 박으면 `하얗게` 질의가 `하얘` 문서를 놓친다
            words.add(alias)
    _topic_words = sorted(words)
    return _topic_words


def kiwi(dictionaries: tuple[Path, ...] = DICTIONARIES) -> Kiwi:
    """Kiwi 를 한 번만 만든다. 사전 없이 쓰면 백탁이 백 + 탁 으로 쪼개진다."""
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi

        _kiwi = Kiwi()
        for dictionary in dictionaries:
            # 없으면 조용히 지나가지 않는다: 사전 없는 색인은 오류 없이 순위만 틀린다.
            if not dictionary.exists():
                raise FileNotFoundError(f"Kiwi 사전이 없다: {dictionary}")
            _kiwi.load_user_dictionary(str(dictionary))
        # 주제 별칭은 topics.py 가 정본이므로 TSV 에 복사하지 않고 여기서 넣는다.
        for word in topic_words():
            _kiwi.add_user_word(word, "NNG", USER_WORD_SCORE)  # pyright: ignore[reportArgumentType]
    return _kiwi


def is_korean(text: str) -> bool:
    """한글이 5% 넘으면 한국어로 본다."""
    if not text:
        return False
    return len(HANGUL_RE.findall(text)) / len(text) > 0.05


def expand_words() -> list[str]:
    """부분문자열 확장에 쓸 별칭 전부. 등록 목록과 다른 집합이다."""
    global _expand_words
    if _expand_words is None:
        _expand_words = sorted({a for e in TOPICS for a in e["ko"] if " " not in a and len(a) >= 2})
    return _expand_words


def expand(token: str) -> tuple[str, ...]:
    """토큰이 주제 별칭을 품고 있으면 별칭도 같이 낸다. 질의와 문서에 대칭으로 적용된다."""
    hit = _expanded.get(token)
    if hit is None:
        extra = [w for w in expand_words() if w != token and w in token]
        hit = (token, *extra)
        _expanded[token] = hit
    return hit


def tokenize(text: str) -> list[str]:
    """언어로 갈린다. 두 갈래 모두 소문자 NFKC 를 거쳐 같은 표면형을 만든다."""
    text = unicodedata.normalize("NFKC", text or "")
    if not is_korean(text):
        return LATIN_RE.findall(text.lower())
    out = []
    tokens: Any = kiwi().tokenize(text)  # 위와 같은 이유
    for token in tokens:
        tag = token.tag.split("-")[0]  # VA-I -> VA; 이걸 놓치면 서술어 질의가 토큰 0개가 된다
        if tag not in KIWI_TAGS:
            continue
        if tag in NOUN_TAGS and len(token.form) < 2:
            continue
        out.append(token.form.lower())
    # Kiwi 는 기호가 붙으면 쪼개므로 `SPF50+` 를 한 덩어리로 못 준다. 정규식이 담당한다.
    out.extend(t for t in LATIN_RE.findall(text.lower()) if len(t) >= 2)
    return [t for token in out for t in expand(token)]


class Index:
    """역색인 하나. 문서 수가 30만 규모라 메모리에 그냥 둔다."""

    def __init__(self, doc_ids: list[str], texts: list[str]):
        if len(doc_ids) != len(texts):
            raise ValueError("doc_ids 와 texts 길이가 다르다")
        self.doc_ids = doc_ids
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths: list[int] = []
        for i, text in enumerate(texts):
            tokens = tokenize(text)
            self.lengths.append(len(tokens))
            for term, tf in Counter(tokens).items():
                self.postings[term].append((i, tf))
        self.n = len(doc_ids)
        self.avg_len = (sum(self.lengths) / self.n) if self.n else 0.0
        self.position = {doc_id: i for i, doc_id in enumerate(doc_ids)}

    def state(self) -> dict:
        """캐시에 담을 알맹이. 클래스가 아니라 dict 로 오간다 -- 클래스를 피클하면
        모듈 경로가 바뀌는 날 캐시 전체를 못 읽는다."""
        return {
            "doc_ids": self.doc_ids,
            "postings": dict(self.postings),
            "lengths": self.lengths,
        }

    @classmethod
    def from_state(cls, state: dict) -> Index:
        index = cls.__new__(cls)
        index.doc_ids = state["doc_ids"]
        index.postings = state["postings"]
        index.lengths = state["lengths"]
        index.n = len(index.doc_ids)
        index.avg_len = (sum(index.lengths) / index.n) if index.n else 0.0
        index.position = {d: i for i, d in enumerate(index.doc_ids)}
        return index

    def idf(self, term: str) -> float:
        """Robertson-Sparck Jones. 절반 넘는 문서에 나오면 음수가 되므로 0 에서 막는다."""
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        return max(0.0, math.log((self.n - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, query: str, k: int | None = 10, skip: set[str] | None = None) -> list[tuple[str, float]]:
        """상위 k 개 (doc_id, 점수). skip 은 후보에서 뺄 doc_id -- heldout 평가가 이 위에 선다:
        질의 글자가 든 문서를 빼고도 같은 주제를 찾아내는가."""
        banned = {self.position[d] for d in (skip or ()) if d in self.position}
        scores: dict[int, float] = defaultdict(float)
        for term in set(tokenize(query)):
            weight = self.idf(term)
            if weight == 0.0:
                continue
            for i, tf in self.postings.get(term, ()):
                if i in banned:
                    continue
                norm = tf + K1 * (1 - B + B * self.lengths[i] / (self.avg_len or 1))
                scores[i] += weight * tf * (K1 + 1) / norm
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], self.doc_ids[kv[0]]))
        if k is not None:
            ranked = ranked[:k]
        return [(self.doc_ids[i], round(s, 4)) for i, s in ranked]
