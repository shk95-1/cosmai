"""BM25 lexical search (ydc bm25.py, v0.1.0 02440ab; changed later in v0.3.0). The baseline the vectors
have to beat.

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

from analysis.retrieval import stopwords, topics

if TYPE_CHECKING:  # types only: pulling kiwipiepy in at runtime loads the model on a single --help.
    from kiwipiepy import Kiwi

K1 = 1.2  # tf saturation coefficient. The standard value
B = 0.75  # strength of the length correction. The standard value
# SPF50+ as one token. Split into `spf` + `50`, every search for a protection factor goes wrong.
LATIN_RE = re.compile(r"[a-z0-9]+(?:[+\-][a-z0-9]+)*\+?")
HANGUL_RE = re.compile(r"[가-힣]")

# 내용어만. VA·VV 를 넣는 것은 `끈적이다`·`시리다` 처럼 서술어가 주제인 경우가 많아서다.
# SL (foreign word) · SN (number) are left out on purpose -- latin tokens are the regex's job, and with both
# in, tf inflates.
KIWI_TAGS = frozenset({"NNG", "NNP", "VA", "VV", "XR", "MAG"})
# 한 글자 명사는 잡음이지만(거·것·수) 서술어 어간은 한 글자가 정상이다(`하얗`·`싫`).
NOUN_TAGS = frozenset({"NNG", "NNP"})
# 0 으로 두면 등록해도 기존 분석을 못 이겨 `신제품` 이 신(XPN) + 제품(NNG) 으로 갈린다.
USER_WORD_SCORE = 3.0

# This directory is canonical -- a copy with the same md5 sat in analysis/slices/ydc/seeds/ and made the
# canonical copy look like two; #9 deleted that copy (#18 M15).
DICT_DIR = Path(__file__).resolve().parent / "dict"
DICTIONARIES = (DICT_DIR / "user_dictionary.tsv", DICT_DIR / "ingredient_dictionary.tsv")
# Every input that decides the tokens and **is a file**. Topic aliases decide tokens as well (they are Kiwi
# user words and an expansion list), but that side is now an active dictionary version rather than a file, so
# the index cache signature bites that dictionary's fingerprint separately on top of this hash
# (pipeline.index_signature, #8).
TOKENIZER_INPUTS = DICTIONARIES


_kiwi = None
_topic_words: list[str] | None = None
_expand_words: list[str] | None = None
# No cap: one entry per token, so it stops at the vocabulary size of the corpus (measured, 3,000 chunks ->
# 3,013 entries · about 92B, and 150 queries run after that added 2), and the postings built from that
# vocabulary are already held far larger by the same process -- a process is one CLI run (#18 M16).
_expanded: dict[str, tuple[str, ...]] = {}


def _forget_topics(_dictionary: topics.Topics | None) -> None:
    """When the active topic dictionary changes, everything derived from it here goes stale -- one survivor
    is enough to produce tokens of two mixed dictionaries. Kiwi cannot remove a registered user word, so
    building a new one is the only way."""
    global _kiwi, _topic_words, _expand_words
    _kiwi = None
    _topic_words = None
    _expand_words = None
    _expanded.clear()


topics.on_change(_forget_topics)


def topic_words() -> list[str]:
    """The aliases to register wholesale with Kiwi. Kiwi is asked before registering -- what is already one
    word, and inflected forms (VA · VV), have to be left out. Nailing an inflected form down as a noun breaks
    morpheme integration."""
    global _topic_words
    if _topic_words is not None:
        return _topic_words

    from kiwipiepy import Kiwi

    bare = Kiwi()  # for the decision, with no user dictionary on it
    words = set()
    for entry in topics.active().entries:
        for alias in entry["ko"]:
            # An alias with a space in it, or with a particle attached, is not a word and cannot go into Kiwi.
            if " " in alias or len(alias) < 2 or alias.endswith("에"):
                continue
            # kiwipiepy's overloads tie a single-sentence input together with the batch return, so it cannot
            # be narrowed.
            tokens: Any = bare.tokenize(alias)
            if len(tokens) == 1 and tokens[0].form == alias:
                continue  # already one word
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
            # A missing one is not passed over quietly: an index without the dictionary gets the ranking
            # wrong with no error.
            if not dictionary.exists():
                raise FileNotFoundError(f"Kiwi 사전이 없다: {dictionary}")
            _kiwi.load_user_dictionary(str(dictionary))
        # The canonical topic aliases are the active dictionary (needs.aspect_lexicon), so they are not
        # copied into the TSV but put in here.
        for word in topic_words():
            _kiwi.add_user_word(word, "NNG", USER_WORD_SCORE)  # pyright: ignore[reportArgumentType]
    return _kiwi


def is_korean(text: str) -> bool:
    """Over 5% Hangul counts as Korean."""
    if not text:
        return False
    return len(HANGUL_RE.findall(text)) / len(text) > 0.05


def expand_words() -> list[str]:
    """Every alias used for substring expansion. A different set from the registration list."""
    global _expand_words
    if _expand_words is None:
        aliases = (a for e in topics.active().entries for a in e["ko"])
        _expand_words = sorted({a for a in aliases if " " not in a and len(a) >= 2})
    return _expand_words


def expand(token: str) -> tuple[str, ...]:
    """When a token holds a topic alias, the alias is emitted too. Applied symmetrically to query and
    document."""
    hit = _expanded.get(token)
    if hit is None:
        extra = [w for w in expand_words() if w != token and w in token]
        hit = (token, *extra)
        _expanded[token] = hit
    return hit


def tokenize(text: str) -> list[str]:
    """Split by language. Both branches go through lowercase NFKC and produce the same surface form."""
    text = unicodedata.normalize("NFKC", text or "")
    if not is_korean(text):
        return LATIN_RE.findall(text.lower())
    out = []
    tokens: Any = kiwi().tokenize(text)  # the same reason as above
    for token in tokens:
        tag = token.tag.split("-")[0]  # VA-I -> VA; miss this and a predicate query gets 0 tokens
        if tag not in KIWI_TAGS:
            continue
        if tag in NOUN_TAGS and len(token.form) < 2:
            continue
        out.append(token.form.lower())
    # Kiwi splits on an attached symbol, so it cannot give `SPF50+` as one lump. The regex takes that.
    out.extend(t for t in LATIN_RE.findall(text.lower()) if len(t) >= 2)
    return [t for token in out for t in expand(token)]


def tokenize_query(text: str) -> list[str]:
    """Query tokenization. It puts **only the query stopword removal** on top of the index tokenization
    (fork #46).

    색인(`tokenize`)에서 빼지 않는 이유는 그러면 `소비자` 를 직접 찾는 질의를 못 하게 되기 때문이고,
    전부 불용어일 때 지우지 않는 이유는 토큰 0개가 결과 0건이라 필러가 낀 순위보다 나빠서다.
    """
    return stopwords.active().keep(tokenize(text))


class Index:
    """One inverted index. The document count is on the order of 300k, so it just stays in memory."""

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
        """The payload to put in the cache. It travels as a dict rather than a class -- pickling a class
        makes the whole cache unreadable the day the module path changes."""
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
        """Robertson-Sparck Jones. It goes negative once a term is in over half the documents, so it is
        clamped at 0."""
        df = len(self.postings.get(term, ()))
        if df == 0:
            return 0.0
        return max(0.0, math.log((self.n - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, query: str, k: int | None = 10, skip: set[str] | None = None) -> list[tuple[str, float]]:
        """The top k (doc_id, score). skip is the doc_ids to drop from the candidates -- the heldout
        evaluation stands on this: does it still find the same topic with the documents carrying the query's
        own characters taken out."""
        banned = {self.position[d] for d in (skip or ()) if d in self.position}
        scores: dict[int, float] = defaultdict(float)
        # The query goes through `tokenize_query` and the index (__init__ above) through `tokenize` -- this
        # is where the two axes part (#46).
        for term in set(tokenize_query(query)):
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
