"""brand_link · product_match 평가 구현체. `analysis.registry.IMPLEMENTATIONS` 가 이 모듈을 import 한다."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from analysis.lexicon import load_lexicon
from analysis.linker import LINKER_VERSION, RuleLinker, accepts, normalized
from analysis.registry import LabeledRow, register
from analysis.types import Lexicon, TextUnit

# labeled_set.ref 가 '<sample>:<src>/<ref_id>/<brand>' 이라 brand 는 마지막 조각이다 (formats.md).
BRAND_FROM_REF = 2
# labeled_set.text 가 '<src_a>:<name_a> | <src_b>:<name_b>' 이다 (db/seed/labeled.py). extra 에는 없다 (T8).
PAIR_SEPARATOR = " | "


@dataclass(frozen=True, eq=False)
class BrandLinkPredictor:
    """행마다 '그 브랜드를 그 문맥에서 우리 회로가 링크하는가'를 답한다 — OK 는 채택, FP 는 비채택."""

    url: str | None = None
    lexicon: Lexicon | None = None
    linker: RuleLinker = field(default_factory=RuleLinker)

    def _lexicon(self) -> Lexicon:
        if self.lexicon is not None:
            return self.lexicon
        # Predictor 프로토콜은 연결을 넘겨주지 않는다 — 사전을 읽을 접속은 구현체가 연다.
        from db.runtime import runtime_url
        from db.seed._common import connect

        with connect(self.url or runtime_url()) as conn:
            return load_lexicon(conn)

    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]:
        lexicon = self._lexicon()
        out: list[str] = []
        for row in rows:
            brand = row.ref.rsplit("/", BRAND_FROM_REF)[-1]
            unit = TextUnit(
                src="yt_comment",
                site="youtube",
                ref=row.ref,
                text=row.text,
                observed_at=date(1970, 1, 1),
                observed_at_resolution="day",
            )
            # (d) surface_re 는 ingredient 표면도 문다. 브랜드 회로의 정밀도를 재려면 kind 로 거른다.
            linked = {h.canonical for h in self.linker.link(unit, lexicon) if h.kind == "brand"}
            out.append("OK" if brand in linked else "FP")
        return out


@dataclass(frozen=True, eq=False)
class ProductMatchPredictor:
    """쌍마다 채택(Y)·비채택(N)을 답한다. 점수는 채택 집합에 대한 정밀도다 (interfaces.md)."""

    linker: RuleLinker = field(default_factory=RuleLinker)

    def __call__(self, rows: Sequence[LabeledRow]) -> Sequence[str]:
        out: list[str] = []
        for row in rows:
            left, _, right = row.text.partition(PAIR_SEPARATOR)
            src_a, _, name_a = left.partition(":")
            src_b, _, name_b = right.partition(":")
            # 평가 행에는 브랜드 컬럼이 없다 — 후보 생성이 이미 브랜드로 묶은 쌍이라 이름만으로 판정한다.
            a = normalized(name_a, "", src_a)
            b = normalized(name_b, "", src_b)
            out.append("Y" if accepts(a, b).ok else "N")
        return out


register("brand_link", LINKER_VERSION, BrandLinkPredictor())
register("product_match", LINKER_VERSION, ProductMatchPredictor())
