"""근거 없는 질의를 막는다 (포크 #48, ydc `vector_threshold.py` 의 `df_gate`).

**코사인 하한선 대신이다.** 계약 §벡터 하한선 이 재기 전에 정한 판정으로 하한선을 버렸다 -- 진짜
질의(주제 별칭 61개)와 코퍼스에 없는 성분명의 최고 코사인 분포가 갈리지 않아서, 문턱은 무관한 결과를
통과시키면서 맞는 결과를 자르는 쪽으로만 작동한다. 코사인은 안 갈리지만 **df 는 갈린다.**

규칙 하나다. **길이 `ZERO_DF_MINLEN` 이상인 질의 토큰 중 문서빈도가 0 인 것이 있으면 막는다.**
코퍼스가 그 이름을 한 번도 말한 적이 없다는 뜻이라, 검색 결과가 나와도 그 이름과 무관한 문서다.

버린 갈래가 둘이고 둘 다 실측으로 **이득 0 · 손해 2** 였다(계약 §벡터 하한선 의 표).

  길이 3 으로 내리기   `재도포` 와 `ZnO`(-> `zno`) 두 별칭이 막히는데 가짜 차단은 그대로다. 짧은 토큰의
                       df 0 은 "없는 이름" 의 근거로 약하다 -- 부분문자열로도 0 이 될 수 있다.
  "전부 0" 갈래        ydc 초판이 쓴 규칙. 더 막는 가짜가 0개인데(문장에 섞인 이름은 `제품`·`함유` 의
                       df 때문에 애초에 전부-0 이 아니다) 같은 별칭 둘을 막는다.

**토큰이 0개인 질의는 판정하지 않는다.** `톤 업`·`땀에`·키릴 표기가 그 갈래이고, 막으면 **벡터가
유일하게 답하는 자리**를 막는다 -- 그 질의들에서 bm25 는 검색 결과가 0건이다. 키릴 표기 하나를 못 막는
것이 그 대가이고, 고칠 자리는 이 게이트가 아니라 토큰화다.

df 는 **색인 축**에서 읽는다(`bm25.tokenize` · `Index.postings`). 질의 불용어(포크 #46)를 태우면 그
목록이 바뀌는 날 이 판정이 함께 흔들린다 -- `eval.docs_with_tokens` 가 같은 이유로 같은 축이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from analysis.retrieval import bm25

ZERO_DF_MINLEN = 4  # df 0 을 "코퍼스에 없는 이름" 의 근거로 볼 최소 토큰 길이. 실측이 정했다


@dataclass(frozen=True)
class Grounding:
    """질의가 코퍼스에 근거를 갖는가. `note` 는 통과든 차단이든 사람에게 그 이유를 말한다."""

    ok: bool
    note: str
    missing: tuple[str, ...] = ()


def check(query: str, index: bm25.Index) -> Grounding:
    """이 질의를 검색해도 되는가. 막으면 부르는 쪽이 결과 0건으로 답한다."""
    frequency = {term: len(index.postings.get(term, ())) for term in set(bm25.tokenize(query))}
    if not frequency:
        return Grounding(True, "질의에 토큰이 없다 -- df 로 판정할 수 없어 벡터에 맡긴다")
    missing = tuple(sorted(t for t, df in frequency.items() if not df and len(t) >= ZERO_DF_MINLEN))
    if missing:
        return Grounding(
            False,
            f"코퍼스에 없는 이름이 질의에 있다 {list(missing)} -- 검색 결과가 나와도 그 이름과 무관한 문서다",
            missing,
        )
    return Grounding(True, f"df 최대 {max(frequency.values()):,}")
