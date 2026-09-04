"""Blocks a query with no grounding (fork #48, `df_gate` in ydc `vector_threshold.py`).

**The gate applies to `vector` and `hybrid` only** (`pipeline.search`). `bm25` ignores a word with df 0 by
giving it idf 0 and answers with the words that are left, so the gate would turn a partial answer into 0
results, and that loss is not worth it. `ask` applies it to every engine (#76): the LLM call is paid, and
a df-0 name makes the model refuse anyway.

**코사인 하한선 대신이다.** 계약 §벡터 하한선 이 재기 전에 정한 판정으로 하한선을 버렸다 -- 진짜
질의(주제 별칭 61개)와 코퍼스에 없는 성분명의 최고 코사인 분포가 갈리지 않아서, 문턱은 무관한 결과를
통과시키면서 맞는 결과를 자르는 쪽으로만 작동한다. 코사인은 안 갈리지만 **df 는 갈린다.**

One rule. **If any query token of length `ZERO_DF_MINLEN` or more has a chunk frequency of 0, it is blocked.**
That means the corpus has never once said that name, so even when search results come back they have nothing
to do with that name.

**It is called "df" but the unit counted is a chunk** -- `Index` is built per chunk (`pipeline.load_index`),
so `len(postings[term])` is a chunk count rather than a document count. Only 0-or-not is looked at so the
decision is the same, but it is a different word from what `eval.docs_with_tokens` does (folding into
documents), and it is written apart here.

버린 갈래가 둘이고 둘 다 실측으로 **이득 0 · 손해 2** 였다(계약 §벡터 하한선 의 표).

  길이 3 으로 내리기   `재도포` 와 `ZnO`(-> `zno`) 두 별칭이 막히는데 가짜 차단은 그대로다. 짧은 토큰의
                       df 0 은 "없는 이름" 의 근거로 약하다 -- 부분문자열로도 0 이 될 수 있다.
  "전부 0" 갈래        ydc 규칙의 초판(`v0.3.0` 의 `df_gate` 는 두 갈래를 **함께** 쓴다). 우리 코퍼스에서
                       더 막는 가짜가 0개인데(문장에 섞인 이름은 `제품`·`함유` 때문에 애초에 전부-0 이
                       아니다) 진짜 별칭 둘을 막는다.

**토큰이 0개인 질의는 판정하지 않는다.** `톤 업`·`땀에`·키릴 표기가 그 갈래이고, 막으면 **벡터가
유일하게 답하는 자리**를 막는다 -- 그 질의들에서 bm25 는 검색 결과가 0건이다. 키릴 표기 하나를 못 막는
것이 그 대가이고, 고칠 자리는 이 게이트가 아니라 토큰화다.

The frequency is read on the **index axis** (`bm25.tokenize` · `Index.postings`). Riding the query stopwords
(fork #46) would make this decision shake along with that list the day it changes -- `eval.docs_with_tokens`
takes the same shape for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from analysis.retrieval import bm25

# The smallest token length at which frequency 0 counts as evidence of "a name not in the corpus". The name
# and the value are the same as ydc `v0.3.0` `vector_threshold.py:33`, and that the value also holds for our
# corpus was settled by measurement
ZERO_DF_MINLEN = 4


@dataclass(frozen=True)
class Grounding:
    """Does the query have grounding in the corpus. `note` tells a person the reason either way."""

    ok: bool
    note: str
    missing: tuple[str, ...] = ()


def check(query: str, index: bm25.Index) -> Grounding:
    """May this query be searched. Blocked, the caller answers with 0 results."""
    # It is a chunk count (docstring above). Which is why the name is frequency rather than df.
    frequency = {term: len(index.postings.get(term, ())) for term in set(bm25.tokenize(query))}
    if not frequency:
        return Grounding(True, "질의에 토큰이 없다 -- 빈도로 판정할 수 없어 벡터에 맡긴다")
    missing = tuple(sorted(t for t, n in frequency.items() if not n and len(t) >= ZERO_DF_MINLEN))
    if missing:
        return Grounding(
            False,
            f"코퍼스에 없는 이름이 질의에 있다 {list(missing)} -- 검색 결과가 나와도 그 이름과 무관한 문서다",
            missing,
        )
    return Grounding(True, f"청크빈도 최대 {max(frequency.values()):,}")
