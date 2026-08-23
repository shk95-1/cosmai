"""needs.entity_lexicon / needs.aspect_lexicon 한 버전을 읽어 컴파일된 사전으로 돌려준다.

정규식 어휘(조사·제품어·담화 표지·바람 표지)는 slice-p3 link_brands.py 와 slice-p1 aspects_generic.py 의
규칙 v2.2 를 그대로 옮긴 것이다. 읽기는 needs_runtime 롤(search_path=needs)이라 테이블명을 한정하지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, LiteralString

import psycopg

from analysis.types import AspectLexicon, AspectPattern, EntitySurface, Lexicon

# 조사 허용 꼬리와 제품어. surface_re 의 경계 규칙은 slice-p3 의 RX 와 같은 모양이다.
PARTICLES = (
    r"(?:이에요|예요|이고|이랑|이라고|이라는|이라서|이니까|인데|입니다|이야|이죠|이네|으로|에서|처럼|보다|밖에|부터|까지"
    r"|는|은|이|가|을|를|도|의|로|와|과|에|랑|만|나|든|요|야|죠|네|거|꺼|건|껀|게|께)?"
)
PRODUCT_WORDS = (
    r"(?:크림|세럼|팩|선크림|립|쿠션|토너|앰플|에센스|클렌징|마스크|패드|틴트|로션|파데|파운데이션|스킨|폼|샴푸|미스트|밤"
    r"|오일|컨실러|섀도우|블러셔|마스카라|아이라이너|선스틱|젤|스틱|바디워시|핸드크림|클렌저|펜슬|브로우|립스틱|글로스"
    r"|팔레트|선쿠션|톤업|기획|세트)"
)
COOC_WINDOW = 25

# 후보 문장을 고르는 표지. aspect 패턴과 달리 사전 테이블에 없고 규칙 버전에 붙어 있다 (slice-p1).
DISCOURSE_MARKERS = (
    r"아쉬|단점|별로|불편|근데|다만|빼고는|하지만|그런데|재구매 ?(안|는 ?안|하지)|실망|별루|기대 ?이하"
    r"|그닥|비추|후회|최악|환불|돈 ?아깝|안 ?맞|못 ?쓰|중단"
)
WISH_MARKERS = r"좋겠|좋았으면|었으면|았으면"

# 표면이 하나도 없을 때의 대체 패턴: 빈 교대는 모든 위치에 맞아 사전 없는 실행이 전부 히트가 된다.
NEVER = re.compile(r"(?!)")
# 링크 대상이 아닌 kind: format/attribute 는 자기 패턴 필드로 나가고 stopword 는 애초에 세지 않는다.
NOT_LINKABLE = frozenset({"format", "attribute", "stopword"})

# active 는 행마다 붙고 activate 는 kind 별로 켠다(001) — "현재 사전" = active 행 전부이고
# version 은 그중 최고 버전을 이름표로 단다. 명시된 version 은 kind 를 가리지 않고 그 버전만 읽는다.
ENTITY_ACTIVE: LiteralString = """
SELECT kind, canonical, surface, tier, source, version FROM entity_lexicon WHERE active ORDER BY id
"""
ENTITY_ROWS: LiteralString = """
SELECT kind, canonical, surface, tier, source, version
FROM entity_lexicon WHERE version = %s ORDER BY id
"""
ASPECT_ACTIVE: LiteralString = """
SELECT aspect, scope, category, pattern, is_neutral_noun, priority, ruleset, version
FROM aspect_lexicon WHERE active AND ruleset IN (%s, 'shared') ORDER BY priority, id
"""
ASPECT_ROWS: LiteralString = """
SELECT aspect, scope, category, pattern, is_neutral_noun, priority, ruleset, version
FROM aspect_lexicon WHERE version = %s AND ruleset IN (%s, 'shared') ORDER BY priority, id
"""


def _label(rows: Sequence[Sequence[Any]], version: int | None, table: str) -> int:
    if version is not None:
        return version
    if not rows:
        raise LookupError(f"{table} 에 active 행이 없다 — cosmai lexicon activate 로 한 버전을 켜라")
    return max(int(row[-1]) for row in rows)


def _alternation(surfaces: list[str]) -> str:
    """긴 표면이 먼저 맞아야 '라네즈'가 '라네'에 잘리지 않는다; 같은 길이는 이름 순으로 고정한다."""
    return "|".join(re.escape(s) for s in sorted(set(surfaces), key=lambda s: (-len(s), s)))


def _kind_patterns(rows: Sequence[EntitySurface], kind: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    by_canonical: dict[str, list[str]] = {}
    for row in rows:
        if row.kind == kind:
            by_canonical.setdefault(row.canonical, []).append(row.surface)
    return tuple((c, re.compile(_alternation(s))) for c, s in by_canonical.items())


def compile_lexicon(surfaces: Sequence[EntitySurface], version: int) -> Lexicon:
    """행 → 컴파일된 사전. DB 없이 사전을 만들 수 있어야 링커·추출기의 규칙을 순수 함수로 검사한다."""
    surfaces = tuple(surfaces)
    stop = frozenset(s.canonical for s in surfaces if s.tier == "stop")
    linkable = [s.surface for s in surfaces if s.kind not in NOT_LINKABLE and s.canonical not in stop]
    alt = _alternation(linkable)
    surface_re = (
        re.compile(rf"(?<![가-힣A-Za-z0-9])({alt}){PARTICLES}(?=$|[^가-힣A-Za-z0-9]|{PRODUCT_WORDS})", re.I)
        if alt
        else NEVER
    )
    to_canonical: dict[str, str] = {}
    for s in surfaces:
        to_canonical.setdefault(s.surface, s.canonical)
        to_canonical.setdefault(s.surface.lower(), s.canonical)
    return Lexicon(
        version=version,
        surfaces=surfaces,
        surface_to_canonical=to_canonical,
        surface_re=surface_re,
        stop=stop,
        cooc_required=frozenset(s.canonical for s in surfaces if s.tier == "cooc_required"),
        product_word_re=re.compile(PRODUCT_WORDS),
        cooc_window=COOC_WINDOW,
        format_patterns=_kind_patterns(surfaces, "format"),
        attribute_patterns=_kind_patterns(surfaces, "attribute"),
    )


def load_lexicon(conn: psycopg.Connection[Any], version: int | None = None) -> Lexicon:
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ENTITY_ACTIVE)
        else:
            cur.execute(ENTITY_ROWS, (version,))
        rows = cur.fetchall()
    return compile_lexicon([EntitySurface(*row[:5]) for row in rows], _label(rows, version, "entity_lexicon"))


def load_aspects(conn: psycopg.Connection[Any], ruleset: str, version: int | None = None) -> AspectLexicon:
    with conn.cursor() as cur:
        if version is None:
            cur.execute(ASPECT_ACTIVE, (ruleset,))
        else:
            cur.execute(ASPECT_ROWS, (version, ruleset))
        rows = cur.fetchall()
    resolved = _label(rows, version, "aspect_lexicon")
    patterns = tuple(
        AspectPattern(
            aspect=row[0],
            scope=row[1],
            category=row[2],
            pattern=re.compile(row[3]),
            is_neutral_noun=row[4],
            priority=row[5],
            ruleset=row[6],
        )
        for row in rows
    )
    return AspectLexicon(
        version=resolved,
        ruleset=ruleset,
        patterns=patterns,
        discourse_marker_re=re.compile(DISCOURSE_MARKERS),
        wish_marker_re=re.compile(WISH_MARKERS),
    )
