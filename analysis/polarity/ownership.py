"""어느 극성 구현이 어느 `lexicon_category` 를 **어느 달부터** 소유하는가 — 이 파일이 그 답이다 (#31, #97).

005 의 자연키 `(src, ref, need_key, extractor_version, md5(sentence))` 에는 `polarity_version` 이
없다. 그래서 같은 추출기 버전으로 두 구현이 같은 문장을 처리하면 **행 단위 소유권이 성립하지 않는다** —
나중에 도는 쪽이 제자리 upsert 로 앞의 라벨을 덮고, 스코프 없는 실행의 삭제문은 남은 것마저 지운다.
소유는 그래서 행이 아니라 `(scope, 기간)` 단위다: 주인만 그 scope 의 `since` 이후 달을 쓰고 지우며,
그 앞의 달과 주인 없는 scope 는 지금처럼 규칙이 갱신한다. `lexicon_category IS NULL` 인 행(댓글·
카테고리 없는 리뷰)은 어느 달에도 주인이 없다.

기간이 있어야 등록과 패스가 분리된다. scope 전체를 넘기던 때는 등록만 하고 패스를 미루면 그 카테고리의
새 리뷰에 행이 아예 안 생겼고(규칙이 후보 추출 전에 건너뛴다), 그래서 26개 카테고리는 6~7시간짜리
전량 패스를 기다려야 했다. `since` 를 다음 달로 적어 등록하면 과거분은 규칙이 계속 갱신하고, 주인의
패스는 자기 기간만 채우면 된다.

값은 그 구현이 산출 행에 찍는 `polarity_version` 그대로다 — 실행이 자기 것인지 아는 유일한 단서가
그 문자열이다. 판본이 오르면(few-shot·프롬프트 날짜) 이 표도 같이 옮겨야 하고,
tests/test_analyze_polarity.py 의 소유 표 검사가 그 순간을 잡는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from analysis.polarity import VERSION as RULE_VERSION

__all__ = [
    "ALWAYS",
    "CRON_SINCE",
    "NO_OWNERS",
    "OWNERS",
    "Owner",
    "Scopes",
    "may_write",
    "owner_of",
    "scopes_of",
    "unready",
]


@dataclass(frozen=True)
class Owner:
    """한 scope 의 주인: 판본과, 그 판본이 책임지는 첫 달(`need_mention.month` 와 같은 YYYY-MM)."""

    version: str
    since: str


Scopes = tuple[tuple[str, str], ...]  # (scope, since) 쌍 — SQL 로 넘어갈 때도 이 모양이다

ALWAYS = "0000-00"  # 어떤 YYYY-MM 보다도 작다 — 전량 패스가 끝난 scope 는 모든 달이 주인 몫이다.

# 2026-08-24 홀드아웃에서 gemma4 가 규칙을 넘었다 (interfaces.md §LLM 실측). 선블록은 전량 패스(run 16,
# 6h44m)가 끝나 ALWAYS 다. 나머지 26개는 그 뒤로 오는 달만 주인의 몫이다 — 등록과 패스가 같은 순간일
# 필요가 없어졌으므로(#97) 26개를 한꺼번에 꺼내도 그 앞의 달은 05:00 규칙 줄이 계속 갱신한다.
_GEMMA4_2026_08_24 = "llm-ollama-gemma4:latest-fs2-20260824"
# 크론 줄(`stack/crontab.d/analyze` 의 `0 8`)이 처음 도는 달. **배포가 이 달을 넘기면 이 값도 같이
# 옮긴다** — 한 달을 늦게 적으면 그 사이의 달은 규칙이 계속 쓰고, 일찍 적으면 첫 밤이 그만큼의 밀린
# 달을 통째로 진다.
CRON_SINCE = "2026-08"
# #33 의 대상 표 그대로 — 언급 행 수 내림차순 26개(합계 21,123행). 키는 `category_map` 이 산출하는
# `lexicon_category` 다: 표에 오른 leaf 는 그 매핑값이고 표에 없는 leaf 는 항등이다 (analysis/units.py).
_CRON_SCOPES = (
    "에센스",
    "쿠션",
    "시트팩",
    "클렌징폼",
    "크림",
    "헤어토닉/앰플",
    "헤어트리트먼트",
    "애프터선",
    # 선스틱은 #33 의 26개 목록이 쓰인 뒤에 생긴 카테고리다 — 운영 need_mention 에 588행(2026-08-26
    # 조정자 실측). 빠뜨리면 규칙이 계속 갱신해 조용히 남으므로 크론 대상에 넣는다.
    "선스틱",
    "립틴트",
    "뷰티/위생",
    "샴푸",
    "염모제",
    "클렌징워터",
    "스킨/토너",
    "페이셜미스트",
    "클렌징밀크",
    "패드",
    "프로틴음료(고형)",
    "올인원",
    "BB/CC",
    "블러셔",
    "립틴트/라커",
    "아이섀도우",
    "바디로션/크림",
    "스킨케어기기",
    "향수",
)
OWNERS: Mapping[str, Owner] = MappingProxyType(
    {"선블록": Owner(_GEMMA4_2026_08_24, ALWAYS)}
    | {scope: Owner(_GEMMA4_2026_08_24, CRON_SINCE) for scope in _CRON_SCOPES}
)
# 소유가 없던 시절의 동작 그대로 — 이 표를 비우면 규칙 실행이 다시 전량을 쓰고 지운다.
NO_OWNERS: Mapping[str, Owner] = MappingProxyType({})


def owner_of(owners: Mapping[str, Owner], lexicon_category: str | None, month: str) -> str | None:
    """이 (scope, 달)의 주인 판본 — 없으면 None(= 규칙과 표에 없는 구현의 몫)."""
    owner = owners.get(lexicon_category) if lexicon_category is not None else None
    return owner.version if owner is not None and month >= owner.since else None


def scopes_of(owners: Mapping[str, Owner], polarity_version: str, *, mine: bool) -> Scopes:
    """이 판정자가 주인인 (scope, since) 쌍, 또는 남이 주인인 쌍 — 세 술어가 이 배열을 SQL 로 받는다."""
    return tuple(
        sorted(
            (scope, owner.since)
            for scope, owner in owners.items()
            if (owner.version == polarity_version) == mine
        )
    )


def may_write(owners: Mapping[str, Owner], version: str, lexicon_category: str | None, month: str) -> bool:
    """이 판본이 이 행을 쓰고 지워도 되는가 — 소유 술어 한 자리다.

    `analysis/polarity/pipeline.py` 의 읽기 건너뛰기가 이것을 그대로 부르고, 삭제문과 `DO UPDATE` 는
    같은 뜻을 SQL 로 옮긴 `OWNED` 술어를 쓴다(같은 (scope, since) 배열 둘을 받는다).
    """
    owner = owner_of(owners, lexicon_category, month)
    if owner is not None:
        return owner == version
    # 주인 없는 자리는 규칙의 몫이다. 표에 오른 구현은 자기 (scope, 기간) 밖으로 나가지 않는다 —
    # 그러지 않으면 주인의 스코프 없는 한 줄이 곧 전량 재라벨이다.
    return not any(registered.version == version for registered in owners.values())


def unready(owners: Mapping[str, Owner], version: str, scope: str | None) -> str | None:
    """규칙이 아닌 구현을 손으로 풀어도 되는 자리인가 — 아니면 그 이유 한 줄 (cosmai/cli.py 가 부른다).

    스코프를 안 붙인 한 줄은 표에 자기 자리가 있을 때만 자기 몫으로 좁혀진다(#97) — 표에 없는 구현의
    그 한 줄은 여전히 규칙 모집단 전량 재라벨이고, 값은 시간이거나 GPU 다(유료 여부가 기준이 아닌
    이유다). 이름 붙인 scope 에 주인이 아직 없으면 그것도 거절한다: 주인 없는 scope 는 규칙이 매일
    05:00 에 다시 라벨하므로 등록 없이 도는 패스는 성공하고도 다음 새벽에 사라진다.
    남의 scope 를 지정한 실행은 여기서 걸러내지 않는다 — 그 거절은 단계의 몫이고 계약은 그것을
    failed run + 종료 코드 1 로 약속한다 (contracts/entrypoints.md §분석).
    """
    if version == RULE_VERSION:
        return None
    if scope is not None:
        if scope not in owners:
            return (
                f"{scope} has no owner, so the 05:00 rule run relabels it tonight; register it to "
                f"{version} in analysis/polarity/ownership.py before this pass"
            )
        return None
    if not scopes_of(owners, version, mine=True):
        return (
            f"--impl {version} owns no scope, so this would relabel every scope; register it with a "
            "since month in analysis/polarity/ownership.py, or name one with --scope <category>"
        )
    return None
