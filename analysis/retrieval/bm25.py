"""BM25 lexical search (ydc bm25.py, v0.1.0 02440ab; changed later in v0.3.0). The baseline the vectors
have to beat.

정확 일치가 정답인 말이 많다 -- `에칠헥실트리아존` · `SPF50+` · 브랜드명. 벡터에 넣으면
성분이 다른 것을 비슷하다고 하므로 그건 순위 문제가 아니라 오답이다. 그쪽을 이 파일이 맡는다.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
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
# The locative particle (U+C5D0). An alias ending in it is a phrase, not a word, and cannot go into Kiwi.
PARTICLE = "\uc5d0"

# This directory is canonical -- a copy with the same md5 sat in analysis/slices/ydc/seeds/ and made the
# canonical copy look like two; #9 deleted that copy (#18 M15).
DICT_DIR = Path(__file__).resolve().parent / "dict"
DICTIONARIES = (DICT_DIR / "user_dictionary.tsv", DICT_DIR / "ingredient_dictionary.tsv")
# Every input that decides the tokens and **is a file**. Topic aliases decide tokens as well (they are Kiwi
# user words and an expansion list), but that side is now an active dictionary version rather than a file, so
# the index cache signature bites that dictionary's fingerprint separately on top of this hash
# (pipeline.index_signature, #8).
TOKENIZER_INPUTS = DICTIONARIES


@dataclass
class _Derived:
    """Everything this module derives from one topic dictionary: the tokenizer with the aliases registered
    on it, the registration and expansion lists, and the expansion memo. It survives `topics.forget()` and a
    `topics.use()` of the same content -- the retrieval tests install the same dictionary before every test
    and a CLI run installs one per process, so the Kiwi build (measured 1.8 s and 266 MB) happens once per
    dictionary rather than once per test (#81).

    The expansion memo has no cap: one entry per token, so it stops at the vocabulary size of the corpus
    (measured, 3,000 chunks -> 3,013 entries · about 92B, and 150 queries run after that added 2), and the
    postings built from that vocabulary are already held far larger by the same process -- a process is one
    CLI run (#18 M16)."""

    source: topics.Topics
    kiwi: Kiwi | None = None
    topic_words: list[str] | None = None
    expand_words: list[str] | None = None
    expanded: dict[str, tuple[str, ...]] = field(default_factory=dict)


# By fingerprint, least recently used first, at most KEPT of them. Two rather than one: the suite alternates
# between the repo dictionary and a wider one around a few tests, and one slot rebuilt Kiwi on every return;
# a CLI run installs one dictionary and fills one slot. The bound is the memory: one Kiwi is 266 MB.
_derived: OrderedDict[str, _Derived] = OrderedDict()
KEPT = 2
# alias -> whether Kiwi has to be told about it. The verdict comes from a Kiwi with no user dictionary on it
# and depends on no dictionary, so it is kept for the process: the bare analyzer is built only for an alias no
# dictionary of this process has carried yet -- once, in a CLI run (#81).
_registrable: dict[str, bool] = {}


def _for_active() -> _Derived:
    """The cache for the active dictionary. Its key is the fingerprint, which `from_rows` makes from the
    whole content -- so a dictionary object carrying the same fingerprint is the same dictionary, unless it
    was built by hand around a borrowed fingerprint (tests do). Then its content decides, not the label."""
    dictionary = topics.active()
    cache = _derived.get(dictionary.fingerprint)
    if cache is not None and cache.source is not dictionary:
        if cache.source.entries == dictionary.entries:
            cache.source = dictionary  # the next call answers on identity alone
        else:
            cache = None
    if cache is None:
        if dictionary.fingerprint not in _derived and len(_derived) >= KEPT:
            _derived.popitem(last=False)
        cache = _derived[dictionary.fingerprint] = _Derived(source=dictionary)
    _derived.move_to_end(dictionary.fingerprint)
    return cache


def topic_words() -> list[str]:
    """The aliases to register wholesale with Kiwi. Kiwi is asked before registering -- what is already one
    word, and inflected forms (VA · VV), have to be left out. Nailing an inflected form down as a noun breaks
    morpheme integration."""
    return _topic_words_of(_for_active())


def _topic_words_of(cache: _Derived) -> list[str]:
    if cache.topic_words is not None:
        return cache.topic_words
    bare: Any = None  # for the decision, with no user dictionary on it; built only when an alias needs it
    words = set()
    for entry in cache.source.entries:
        for alias in entry["ko"]:
            # An alias with a space in it, or with a particle attached, is not a word and cannot go into Kiwi.
            if " " in alias or len(alias) < 2 or alias.endswith(PARTICLE):
                continue
            verdict = _registrable.get(alias)
            if verdict is None:
                if bare is None:
                    from kiwipiepy import Kiwi

                    bare = Kiwi()
                # kiwipiepy's overloads tie a single-sentence input together with the batch return, so it
                # cannot be narrowed.
                tokens: Any = bare.tokenize(alias)
                # Already one word: nothing to register. An inflected form (VA · VV): nailed down as a noun,
                # a query in one inflection misses the document that carries another.
                verdict = _registrable[alias] = not (
                    (len(tokens) == 1 and tokens[0].form == alias)
                    or any(t.tag.split("-")[0] in {"VA", "VV"} for t in tokens)
                )
            if verdict:
                words.add(alias)
    cache.topic_words = sorted(words)
    return cache.topic_words


def kiwi() -> Kiwi:
    """The tokenizer for the active dictionary: the two packaged dictionaries and the active dictionary's
    aliases registered on one Kiwi. Without the dictionaries a compound ingredient name splits into its
    morphemes. Kiwi cannot remove a registered user word, so a dictionary with other aliases means building a
    new one (#81)."""
    return _kiwi_of(_for_active())


def _kiwi_of(cache: _Derived) -> Kiwi:
    if cache.kiwi is not None:
        return cache.kiwi
    from kiwipiepy import Kiwi

    built = Kiwi()
    for dictionary in DICTIONARIES:
        # A missing one is not passed over quietly: an index without the dictionary gets the ranking wrong
        # with no error.
        if not dictionary.exists():
            raise FileNotFoundError(f"the Kiwi dictionary is missing: {dictionary}")
        built.load_user_dictionary(str(dictionary))
    # The canonical topic aliases are the active dictionary (needs.aspect_lexicon), so they are not copied
    # into the TSV but put in here.
    for word in _topic_words_of(cache):
        built.add_user_word(word, "NNG", USER_WORD_SCORE)  # pyright: ignore[reportArgumentType]
    cache.kiwi = built
    return built


def is_korean(text: str) -> bool:
    """Over 5% Hangul counts as Korean."""
    if not text:
        return False
    return len(HANGUL_RE.findall(text)) / len(text) > 0.05


def expand_words() -> list[str]:
    """Every alias used for substring expansion. A different set from the registration list."""
    return _expand_words_of(_for_active())


def _expand_words_of(cache: _Derived) -> list[str]:
    if cache.expand_words is None:
        aliases = (a for e in cache.source.entries for a in e["ko"])
        cache.expand_words = sorted({a for a in aliases if " " not in a and len(a) >= 2})
    return cache.expand_words


def expand(token: str) -> tuple[str, ...]:
    """When a token holds a topic alias, the alias is emitted too. Applied symmetrically to query and
    document."""
    return _expand(_for_active(), token)


def _expand(cache: _Derived, token: str) -> tuple[str, ...]:
    hit = cache.expanded.get(token)
    if hit is None:
        extra = [w for w in _expand_words_of(cache) if w != token and w in token]
        hit = (token, *extra)
        cache.expanded[token] = hit
    return hit


def tokenize(text: str) -> list[str]:
    """Split by language. Both branches go through lowercase NFKC and produce the same surface form."""
    text = unicodedata.normalize("NFKC", text or "")
    if not is_korean(text):
        return LATIN_RE.findall(text.lower())
    cache = _for_active()  # resolved once: this runs per chunk while an index is built
    out = []
    tokens: Any = _kiwi_of(cache).tokenize(text)  # the same reason as above
    for token in tokens:
        tag = token.tag.split("-")[0]  # VA-I -> VA; miss this and a predicate query gets 0 tokens
        if tag not in KIWI_TAGS:
            continue
        if tag in NOUN_TAGS and len(token.form) < 2:
            continue
        out.append(token.form.lower())
    # Kiwi splits on an attached symbol, so it cannot give `SPF50+` as one lump. The regex takes that.
    out.extend(t for t in LATIN_RE.findall(text.lower()) if len(t) >= 2)
    return [t for token in out for t in _expand(cache, token)]


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
