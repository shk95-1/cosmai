"""RuleLinker: 브랜드·엔티티 링크(p3 규칙)와 사이트 간 제품 식별(p2 규칙).

규칙은 analysis/slices/p3-youtube-brand-link/link_brands.py 와
analysis/slices/p2-ranking-dynamics/build_product_ref.py 를 옮긴 것이다(슬라이스는 import 하지 않는다).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass

from analysis.types import (
    EntityHit,
    Lexicon,
    ProductCandidateRow,
    ProductMatch,
    ProductMemberRow,
    ProductRefRow,
    ProductRow,
    TextUnit,
)

LINKER_VERSION = "rule-v1.0"

# 판정 3: 계약 예시가 'oy:' 다. p2 의 source[:2] 규칙에 올리브영만 예외를 둔다.
SOURCE_PREFIX = {"oliveyoung": "oy"}
# 앵커 우선순위이자 사이트 쌍의 순회 순서 — 올리브영이 가장 넓은 카탈로그라 앵커다.
SITES = ("oliveyoung", "glowpick", "hwahae", "daisomall")

BRAND_ALIAS = {
    "vtcosmetics": "vt", "cnp": "차앤박", "프릴루드딘토": "딘토", "어퓨더퓨어": "어퓨",
    "바이리얼베리어": "리얼베리어", "애경바세린": "바세린", "본셉스킨케어": "본셉", "본셉메이크업": "본셉",
    "리더스코스메틱": "리더스", "미모바이마몽드": "마몽드", "줌바이정샘물": "정샘물",
    "플레이101by에뛰드": "에뛰드", "네이처리퍼블릭바이플라워": "네이처리퍼블릭",
    "네이처리퍼블릭식물원": "네이처리퍼블릭", "제이엠솔루션": "jm솔루션", "드롭비컬러즈": "드롭비",
    "밀크터치디어씽": "밀크터치", "3m넥스케어": "넥스케어", "글린트바이비디보브": "글린트",
    "입생로랑뷰티": "입생로랑",
}  # fmt: skip

NOISE = re.compile(
    r"\[.*?\]|\(.*?\)|【.*?】|SPF\s*\d+\+*|PA\++"
    r"|\d+(\.\d+)?\s*(ml|mL|g|kg|매입|매|입|개입|개|ea|EA|p|P|호|종|장|정|포|pcs|fl\.?\s*oz\.?|oz)"
    r"(\b|(?=[^A-Za-z0-9]))"
    r"|\b\d+\s*\+\s*\d+\b|\d+\s*colors?|\d+\s*색|\d+\s*종|x\s*\d+|X\s*\d+|\*\s*\d+"
    r"|/\s*\d[\d\.]*\s*(fl\.?\s*oz|oz)",
    re.I,
)
MARKETING = (
    "기획", "본품", "리필", "단품", "세트", "더블", "증정", "한정", "올영픽", "올영", "픽", "특가", "신상",
    "NEW", "대용량", "업그레이드", "리뉴얼", "1+1", "2입", "3입", "2개입", "더블기획", "기획세트", "듀오",
    "구성", "랜덤", "택1", "택", "선택", "추가", "온라인", "단독", "공식", "정품", "국내", "수입", "용량",
    "사은품", "무료", "배송", "pick", "PICK", "한정판", "에디션", "리미티드", "패키지", "세일", "할인",
    "할인가",
)  # fmt: skip
GLUED = re.compile(r"(?<=[가-힣A-Za-z])(기획|단품|세트|리필|더블|증정)(?![가-힣])")
MARKETING_RE = tuple(
    re.compile(r"(?<![A-Za-z가-힣])" + re.escape(w) + r"(?![A-Za-z가-힣])") for w in MARKETING
)
SYNONYMS = (
    (re.compile(r"썬"), "선"),
    (re.compile(r"쿠션팩트"), "쿠션"),
    (re.compile(r"폼클렌저|폼 클렌저|클렌징폼|클렌징 폼|포밍클렌저|폼 클렌징"), "클렌징폼"),
    (re.compile(r"크림\s*패드"), "패드"),
    (re.compile(r"수분크림"), "수분 크림"),
    (re.compile(r"오드퍼퓸|오 드 퍼퓸|오드 퍼퓸|EDP"), "오드퍼퓸"),
    (re.compile(r"오드뚜왈렛|오 드 뚜왈렛|오드 뚜왈렛|EDT"), "오드뚜왈렛"),
    (re.compile(r"마스크팩|마스크 팩"), "마스크"),
    (re.compile(r"시트마스크|시트 마스크"), "마스크"),
    (re.compile(r"선 크림"), "선크림"),
    (re.compile(r"썬크림"), "선크림"),
    (re.compile(r"토너패드|토너 패드"), "패드"),
    (re.compile(r"샴푸바"), "샴푸"),
    (re.compile(r"립 틴트"), "립틴트"),
    (re.compile(r"([가-힣])(\d)"), r"\1 \2"),
)
BRACKET_SPEC = re.compile(r"\[(SPF[^\]]*|PA[^\]]*)\]", re.I)
NON_WORD = re.compile(r"[^\w가-힣\.]+")
BARE_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")
NUMBER = re.compile(r"[\d\.]+")
SPACES = re.compile(r"\s+")
# 제형·재질 일반명사. 이것만 겹치는 두 이름은 같은 제품의 증거가 아니다.
STOP_TOK = frozenset({
    "크림", "세럼", "토너", "로션", "앰플", "에센스", "마스크", "패드", "샴푸", "선크림", "수분", "진정",
    "미스트", "클렌저", "젤", "오일", "밤", "팩", "워시", "바디", "헤어", "립", "페이셜", "스킨", "케어",
    "the", "더", "데일리", "모이스처", "모이스쳐", "리페어", "트리트먼트", "에디션",
})  # fmt: skip
FORMS = (
    "크림미스트", "클렌징폼", "클렌징오일", "클렌징밀크", "클렌징워터", "클렌징밤", "클렌징젤", "클렌징티슈",
    "톤업선크림", "선크림", "선스틱", "선세럼", "선쿠션", "선스프레이", "선밤", "선젤", "선로션", "선에센스",
    "바디워시", "바디로션", "바디크림", "바디미스트", "바디오일", "바디스크럽", "바디버터", "핸드크림",
    "풋샴푸", "샴푸", "트리트먼트", "컨디셔너", "헤어오일", "헤어에센스", "헤어퍼퓸", "헤어미스트",
    "오드퍼퓸", "오드뚜왈렛", "퍼퓸", "쿠션", "파운데이션", "립틴트", "틴트", "립밤", "립스틱", "립글로스",
    "립라이너", "섀도우", "쉐도우", "팔레트", "블러셔", "마스카라", "아이라이너", "브로우", "컨실러",
    "프라이머", "픽서", "파우더", "팩트", "하이라이터", "세럼", "앰플", "토너", "로션", "에멀전", "에센스",
    "수분크림", "크림", "마스크", "패드", "미스트", "올인원", "젤", "밤", "오일", "필링", "스크럽", "클렌저",
    "폼", "워시", "페이퍼", "리무버", "스프레이", "스틱", "티슈", "패치", "마스크시트", "팩", "아이크림",
    "넥크림",
)  # fmt: skip
FORM_RE = re.compile("|".join(sorted(FORMS, key=len, reverse=True)))
FORM_MAP = {
    "크림미스트": "미스트", "쉐도우": "섀도우", "폼": "클렌징폼", "클렌저": "클렌징폼", "수분크림": "크림",
    "마스크시트": "마스크", "팩": "마스크", "톤업선크림": "선크림",
}  # fmt: skip
# 한쪽에만 있으면 다른 제품인 토큰(라인 변형·색·부위). 숫자 토큰도 같은 역할을 한다.
DISCRIM = frozenset({
    "톤업", "포맨", "맨", "미니", "미니어처", "대용량", "리필", "키즈", "베이비", "프로", "플러스", "라이트",
    "딥", "오일프리", "젤", "쿨링", "워터프루프", "더마", "클리어", "수딩", "모공", "탄력", "흔적", "미백",
    "주름", "레드", "그린", "블루", "화이트", "블랙", "핑크", "골드", "바디", "헤어", "립", "아이", "넥",
    "핸드", "풋", "스칼프", "두피", "선", "마일드", "센서티브", "인텐시브", "리치", "프레쉬", "매트",
    "글로우", "글로시", "벨벳", "샤인", "멜팅", "젤리", "워터", "밀크", "오일", "엠디", "md", "패드",
})  # fmt: skip
# 대괄호 안에 규격을 적는 사이트는 대괄호째 지우면 이름이 비어 버린다.
KEEP_BRACKET_SOURCES = frozenset({"glowpick", "hwahae"})


@dataclass(frozen=True)
class Normalized:
    source: str
    product_key: str
    name: str
    brand: str
    name_norm: str
    tokens: frozenset[str]
    significant: frozenset[str]
    bigrams: frozenset[str]
    numbers: frozenset[str]
    form: str


@dataclass(frozen=True)
class Match:
    ok: bool
    shared_tok: int
    shared_sig: int
    dice: float


def normalize_brand(brand: str | None) -> str:
    stripped = re.sub(r"[\s™®\(\)\.\-_/]", "", (brand or "").lower())
    return BRAND_ALIAS.get(stripped, stripped)


def normalize_name(name: str, brand: str | None, keep_bracket: bool = False) -> str:
    n = name or ""
    if keep_bracket:
        n = BRACKET_SPEC.sub(" ", n).replace("[", " ").replace("]", " ")
    n = NOISE.sub(" ", n)
    n = GLUED.sub(" ", n)
    for word in MARKETING_RE:
        n = word.sub(" ", n)
    for pattern, replacement in SYNONYMS:
        n = pattern.sub(replacement, n)
    n = n.lower()
    lowered = (brand or "").lower()
    if lowered:
        n = n.replace(lowered, " ").replace(lowered.replace(" ", ""), " ")
    n = BARE_DOT.sub(" ", NON_WORD.sub(" ", n))
    return SPACES.sub(" ", n).strip()


def _bigrams(name_norm: str) -> frozenset[str]:
    glued = name_norm.replace(" ", "")
    return frozenset(glued[i : i + 2] for i in range(len(glued) - 1))


def _form(name_norm: str) -> str:
    found = [FORM_MAP.get(m.group(), m.group()) for m in FORM_RE.finditer(name_norm.replace(" ", ""))]
    return found[-1] if found else ""  # 마지막 제형 토큰이 주 제형이다


def normalized(name: str, brand: str | None, source: str, product_key: str = "") -> Normalized:
    name_norm = normalize_name(name, brand, keep_bracket=source in KEEP_BRACKET_SOURCES)
    tokens = frozenset(t for t in name_norm.split() if len(t) >= 2 or t.isdigit())
    return Normalized(
        source=source,
        product_key=product_key,
        name=name,
        brand=brand or "",
        name_norm=name_norm,
        tokens=tokens,
        significant=frozenset(t for t in tokens if t not in STOP_TOK),
        bigrams=_bigrams(name_norm),
        numbers=frozenset(t for t in name_norm.split() if NUMBER.fullmatch(t)),
        form=_form(name_norm),
    )


def _dice(a: frozenset[str], b: frozenset[str]) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def accepts(a: Normalized, b: Normalized) -> Match:
    """p2 v2 규칙: 주 제형 일치 + 변별·숫자 토큰 불일치 없음 + (자카드 | Dice) 문턱."""
    shared = a.tokens & b.tokens
    sig = a.significant & b.significant
    dice = round(_dice(a.bigrams, b.bigrams), 3)
    union = a.significant | b.significant
    jaccard = len(sig) / len(union) if union else 0.0
    glued_a, glued_b = a.name_norm.replace(" ", ""), b.name_norm.replace(" ", "")
    # 토큰으로는 어긋나도 글자로는 양쪽에 다 있는 변별어는 어긋난 것이 아니다.
    differing = {t for t in (a.significant ^ b.significant) & DISCRIM if (t in glued_a) != (t in glued_b)} | (
        a.numbers ^ b.numbers
    )
    ok = (
        a.form == b.form
        and not differing
        and ((jaccard >= 0.5 and len(sig) >= 1) or dice >= 0.7 or (len(sig) >= 2 and dice >= 0.6))
    )
    return Match(ok=ok, shared_tok=len(shared), shared_sig=len(sig), dice=dice)


class _Union:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)


class RuleLinker:
    """`Linker` 프로토콜의 규칙 구현."""

    def __init__(self, version: str = LINKER_VERSION) -> None:
        self.version = version
        self._kinds: dict[int, dict[str, str]] = {}

    def _kind_of(self, lexicon: Lexicon) -> dict[str, str]:
        """표면 → kind. surface_re 는 ingredient 표면까지 물기 때문에 히트마다 되짚어야 한다."""
        cached = self._kinds.get(lexicon.version)
        if cached is None:
            cached = {}
            for surface in lexicon.surfaces:
                cached.setdefault(surface.surface, surface.kind)
                cached.setdefault(surface.surface.lower(), surface.kind)
            self._kinds[lexicon.version] = cached
        return cached

    def link(self, unit: TextUnit, lexicon: Lexicon) -> list[EntityHit]:
        kinds = self._kind_of(lexicon)
        text = unit.text
        hits: list[EntityHit] = []
        for m in lexicon.surface_re.finditer(text):
            surface = m.group(1)
            canonical = lexicon.surface_to_canonical[surface.lower()]
            window = text[max(0, m.start(1) - lexicon.cooc_window) : m.end(1) + lexicon.cooc_window]
            cooc = bool(lexicon.product_word_re.search(window))
            if canonical in lexicon.cooc_required and not cooc:
                continue
            hits.append(
                EntityHit(
                    kind=kinds[surface.lower()],
                    canonical=canonical,
                    surface=surface,
                    start=m.start(1),
                    end=m.end(1),
                    cooc=cooc,
                )
            )
        return hits

    def match_products(self, products: Iterable[ProductRow]) -> ProductMatch:
        rows = [p for p in products if p.name and not p.name.isdigit()]
        brands = self._brands(rows)
        norms = [
            normalized(p.name, brands[i] or p.brand, p.source, p.product_key) for i, p in enumerate(rows)
        ]
        groups = _Union(len(rows))
        seen: dict[tuple[str, str, str], int] = {}
        for i, norm in enumerate(norms):
            # 사이트 안의 기획·용량 변형과 중복 키는 정규화 이름이 같다 — 먼저 한 덩어리로 접는다.
            key = (norm.source, normalize_brand(brands[i]), norm.name_norm)
            if key in seen:
                groups.union(i, seen[key])
            else:
                seen[key] = i
        candidates = self._candidates(rows, norms, brands, groups)
        return ProductMatch(
            refs=tuple(self._refs(rows, norms, brands, groups)),
            members=tuple(self._members(rows, norms, groups, candidates)),
            variants=(),  # B3: 산출 알고리즘이 없어 이 유닛의 범위 밖이다
            candidates=tuple(candidates),
        )

    def _brands(self, rows: list[ProductRow]) -> list[str]:
        """다이소몰은 브랜드 컬럼이 없다 — 이름 앞토막이 올리브영 브랜드면 그것으로 본다."""
        catalogue = sorted(
            {p.brand for p in rows if p.source == "oliveyoung" and p.brand}, key=len, reverse=True
        )
        out: list[str] = []
        for p in rows:
            if p.brand:
                out.append(p.brand)
                continue
            head = re.sub(r"^\[.*?\]\s*", "", p.name)
            out.append(next((b for b in catalogue if len(b) >= 2 and head.startswith(b)), ""))
        return out

    def _candidates(
        self, rows: list[ProductRow], norms: list[Normalized], brands: list[str], groups: _Union
    ) -> list[ProductCandidateRow]:
        by_brand: dict[str, dict[str, list[int]]] = {}
        for i, brand in enumerate(brands):
            key = normalize_brand(brand)
            if key:
                by_brand.setdefault(key, {}).setdefault(rows[i].source, []).append(i)
        out: list[ProductCandidateRow] = []
        for brand, sites in sorted(by_brand.items()):
            for src_a, src_b in itertools.combinations(SITES, 2):
                if src_a not in sites or src_b not in sites:
                    continue
                forward = {i: _best(norms, i, sites[src_b]) for i in sites[src_a]}
                backward = {j: _best(norms, j, sites[src_a]) for j in sites[src_b]}
                for i, best in forward.items():
                    if best is None:
                        continue
                    j, match = best
                    back = backward[j]
                    mutual = back is not None and groups.find(back[0]) == groups.find(i)
                    out.append(
                        ProductCandidateRow(
                            src_a=src_a,
                            key_a=rows[i].product_key,
                            src_b=src_b,
                            key_b=rows[j].product_key,
                            brand=brand,
                            shared_tok=match.shared_tok,
                            shared_sig=match.shared_sig,
                            dice=match.dice,
                            mutual=mutual,
                        )
                    )
                    if mutual:
                        groups.union(i, j)
        return out

    def _refs(
        self, rows: list[ProductRow], norms: list[Normalized], brands: list[str], groups: _Union
    ) -> list[ProductRefRow]:
        out: list[ProductRefRow] = []
        for members in _clusters(rows, groups):
            sources = {rows[i].source for i in members}
            if len(sources) < 2:  # 한 사이트 안의 변형만 모인 덩어리는 사이트 간 식별이 아니다
                continue
            anchor = next((i for i in members if rows[i].source == "oliveyoung"), members[0])
            out.append(
                ProductRefRow(
                    product_ref=_ref_id(rows[anchor]),
                    brand=brands[anchor] or None,
                    name_norm=norms[anchor].name_norm,
                    name=rows[anchor].name,
                    n_sites=len(sources),
                    first_seen=rows[anchor].first_ranked,
                    linker_version=self.version,
                )
            )
        return out

    def _members(
        self,
        rows: list[ProductRow],
        norms: list[Normalized],
        groups: _Union,
        candidates: list[ProductCandidateRow],
    ) -> list[ProductMemberRow]:
        # A13: match_score 는 같은 쌍의 후보 dice 다. 한 제품이 여러 쌍에 걸리면 가장 높은 것을 쓴다.
        best: dict[tuple[str, str], float] = {}
        for c in candidates:
            if not c.mutual:
                continue
            for source, key in ((c.src_a, c.key_a), (c.src_b, c.key_b)):
                best[(source, key)] = max(best.get((source, key), 0.0), c.dice)
        out: list[ProductMemberRow] = []
        for members in _clusters(rows, groups):
            sources = {rows[i].source for i in members}
            if len(sources) < 2:
                continue
            anchor = next((i for i in members if rows[i].source == "oliveyoung"), members[0])
            ref = _ref_id(rows[anchor])
            for i in members:
                out.append(
                    ProductMemberRow(
                        source=rows[i].source,
                        product_key=rows[i].product_key,
                        product_ref=ref,
                        role="primary" if i == anchor else "member",
                        match_score=best.get((rows[i].source, rows[i].product_key)),
                    )
                )
        _ = norms
        return out


def _ref_id(row: ProductRow) -> str:
    return f"{SOURCE_PREFIX.get(row.source, row.source[:2])}:{row.product_key}"


def _clusters(rows: list[ProductRow], groups: _Union) -> list[list[int]]:
    out: dict[int, list[int]] = {}
    for i in range(len(rows)):
        out.setdefault(groups.find(i), []).append(i)
    return list(out.values())


def _best(norms: list[Normalized], i: int, pool: list[int]) -> tuple[int, Match] | None:
    best: tuple[int, Match] | None = None
    for j in pool:
        match = accepts(norms[i], norms[j])
        if match.ok and (best is None or (match.shared_sig, match.dice) > (best[1].shared_sig, best[1].dice)):
            best = (j, match)
    return best
