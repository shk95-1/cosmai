"""A **frozen copy** of the topic dictionary (2026-08-26, `analysis/retrieval/topics.py` as it stood just
before the move).

The literals here and `match_topics` are not touched. That there are **the same 15 topics, the same aliases
and the same match results** after the source of topic expansion moved from constants to
`needs.aspect_lexicon` is proved by `test_topics.py` against this copy -- because the measured search table
of `contracts/interfaces.md` (six mode x engine lines) stands on that equivalence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

TOPICS: list[dict] = [
    {
        "topic": "백탁",
        "topic_type": "attribute",
        "ko": ["백탁", "하얗게", "하얘"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "백태·창백·유령·회끄무레는 실측 0건이라 제외",
    },
    {
        "topic": "자극_눈시림",
        "topic_type": "attribute",
        "ko": ["눈시림", "눈 시림", "눈따가", "자극", "따갑", "붉어", "뒤집", "예민", "트러블", "알러지"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "눈시려·눈아파·눈매움 0건 제외. '눈물'은 감동 댓글과 섞여 제외",
    },
    {
        "topic": "발림성",
        "topic_type": "attribute",
        "ko": ["발림성", "발림", "제형", "텍스처"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'발림'은 '발림성'보다 17편 더 잡는다(발림이 좋다 등). 둘 다 유지",
    },
    {
        "topic": "촉촉함_건조함",
        "topic_type": "attribute",
        "ko": ["촉촉", "수분", "보습", "건조"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'건조'는 댓글(89)이 영상(38)보다 많다. 불만은 댓글에 쌓인다",
    },
    {
        "topic": "끈적임_유분감",
        "topic_type": "attribute",
        "ko": ["끈적", "유분", "번들", "기름"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "미끌 0건 제외",
    },
    {
        "topic": "밀림_들뜸",
        "topic_type": "attribute",
        "ko": ["밀림", "밀려", "들뜸", "뭉침"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'화장 궁합' 0건 제외",
    },
    {
        "topic": "지속력_워터프루프",
        "topic_type": "attribute",
        "ko": ["지속력", "워터프루프", "무너짐", "재도포", "땀에", "방수"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'지속'만으로는 '지속적으로' 등 일반어에 걸려 제외",
    },
    {
        "topic": "톤업_메이크업베이스",
        "topic_type": "attribute",
        "ko": ["톤업", "톤 업", "메이크업베이스", "피부톤", "화이트닝"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'베이스'(83)는 단독으로 화장품 일반어라 제외",
    },
    {
        "topic": "무기자차",
        "topic_type": "formula",
        "ko": ["무기자차", "징크", "티타늄", "미네랄"],
        "latin": ["ZnO", "TiO2"],
        "mfds_inci": [
            "징크옥사이드",
            "티타늄디옥사이드",
            "산화아연",
            "이산화티타늄",
            "Zinc Oxide",
            "Titanium Dioxide",
        ],
        "trend_use": True,
        "note": "유튜브는 '무기자차', 식약처는 '산화아연'. 표기 겹침 0건 -> 매핑 필수",
    },
    {
        "topic": "유기자차",
        "topic_type": "formula",
        "ko": ["유기자차", "아보벤존", "옥토크릴렌", "화학적"],
        "latin": [],
        "mfds_inci": [
            "에칠헥실트리아존",
            "비스-에칠헥실옥시페놀메톡시페닐트리아진",
            "메칠렌비스-벤조트리아졸릴테트라메칠부틸페놀",
            "에칠헥실살리실레이트",
            "에칠헥실메톡시신나메이트",
            "부틸메톡시디벤조일메탄",
            "옥토크릴렌",
            "디에칠아미노하이드록시벤조일헥실벤조에이트",
            "드로메트리졸트리실록산",
            "테레프탈릴리덴디캠퍼설포닉애씨드",
            "페닐벤즈이미다졸설포닉애씨드",
            "벤조페논-3",
            "4-메칠벤질리덴캠퍼",
            "아보벤존",
            "에틸헥실메톡시신나메이트",
            "Avobenzone",
            "Octocrylene",
        ],
        "trend_use": True,
        "note": "케미컬·유비놀 0건 제외",
    },
    {
        "topic": "혼합자차",
        "topic_type": "formula",
        "ko": ["혼합자차"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "19편뿐. 분기당 표본 부족 가능성 높음 -> 판정 시 확인",
    },
    {
        "topic": "SPF_PA",
        "topic_type": "spec",
        "ko": ["차단지수"],
        "latin": ["SPF", "PA", "UVA", "UVB"],
        "mfds_inci": [],
        "trend_use": True,
        "note": "경계 매칭 필수. 부분문자열은 coupang에 걸려 오탐 16%",
    },
    {
        "topic": "성분_신제품",
        "topic_type": "event",
        "ko": ["성분", "신제품", "출시", "리뉴얼", "신상"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": True,
        "note": "'신제품'만은 3편뿐. 전용 검색어 추가 대상(UnitA 4.1)",
    },
    {
        "topic": "추천_재구매",
        "topic_type": "genre",
        "ko": ["재구매", "품절", "인생템", "추천"],
        "latin": [],
        "mfds_inci": [],
        "trend_use": False,
        "note": "'추천' 396/518=76%. 장르 표시일 뿐 주제가 아니다. 트렌드 판정 제외",
    },
    {
        "topic": "선크림",
        "topic_type": "product_category",
        "ko": ["선크림", "썬크림", "자외선차단제", "선블록", "선스틱", "선쿠션", "선세럼", "선젤"],
        "latin": [],
        "mfds_inci": ["자외선차단제"],
        "trend_use": False,
        "note": "481/518=93%. 판별력 없음. 다른 소스에서 문서 필터로만 사용",
    },
]


def _latin_pattern(terms: Sequence[str]) -> re.Pattern[str] | None:
    """A latin token matches only when neither side is a letter (blocks the coupang -> PA 16% false hits)."""
    if not terms:
        return None
    alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z]){alts}(?![A-Za-z])", re.IGNORECASE)


_LATIN = {t["topic"]: _latin_pattern(t["latin"]) for t in TOPICS}


def match_topics(text: str, *, include_excluded: bool = False) -> list[str]:
    """The topics that appear in the text. One document can hit several topics."""
    if not text:
        return []
    lowered = text.lower()
    hits = []
    for entry in TOPICS:
        if not entry["trend_use"] and not include_excluded:
            continue
        if any(term.lower() in lowered for term in entry["ko"]):
            hits.append(entry["topic"])
            continue
        pattern = _LATIN[entry["topic"]]
        if pattern and pattern.search(text):
            hits.append(entry["topic"])
    return hits
