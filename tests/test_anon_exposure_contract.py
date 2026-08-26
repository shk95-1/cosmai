"""#168: 계약이 적은 anon 노출 목록과 이 레포가 실제로 여는 것이 어긋나면 실패한다.

`db/grants/postgrest_anon_needs.sql:3` 은 "화이트리스트"라고 적지만 그 파일은 `needs` 하나만
다스린다 -- `trend_radar` 13개는 `trend_radar_reader` 멤버십으로, `tubedepth` 12개는 구 스택
init 의 직접 GRANT 로 열려 있고 둘 다 이 레포 밖이다. 그래서 이 테스트가 잠글 수 있는 것은
`needs` 절뿐이고, 나머지 두 스키마는 계약이 사실로 적되 이 스위트가 대조하지 못한다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "anon_exposure.md"
GRANT_FILES = (
    ROOT / "db" / "grants" / "postgrest_anon_needs.sql",
    ROOT / "db" / "views" / "pipeline_health.sql",
)
# 계약의 needs 절: 백틱 안의 needs.<관계> 만 목록으로 읽는다.
LISTED = re.compile(r"`needs\.([a-z_]+)`")
# GRANT SELECT ON a, b, c TO postgrest_anon; -- 여러 줄에 걸쳐 있다.
GRANTED = re.compile(r"GRANT\s+SELECT\s+ON\s+(.*?)\s+TO\s+postgrest_anon", re.DOTALL | re.IGNORECASE)


def _granted_in_repo() -> set[str]:
    names: set[str] = set()
    for path in GRANT_FILES:
        # 주석을 지우고 읽는다: 'postgrest_anon_needs.sql' 이라는 파일 이름이 needs.sql 로 잡혔다.
        body = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        for target in GRANTED.findall(body):
            names.update(re.findall(r"needs\.([a-z_]+)", target))
    return names


def _section(name: str) -> str:
    text = CONTRACT.read_text(encoding="utf-8")
    body = text.split(f"## {name}", 1)[1]
    return body.split("\n## ", 1)[0]


def test_the_needs_section_lists_exactly_what_this_repo_grants():
    listed = set(LISTED.findall(_section("needs")))
    granted = _granted_in_repo()
    assert granted, "GRANT 를 하나도 못 읽었다 -- 정규식이 파일 모양을 놓쳤다"
    assert listed == granted, f"계약에만: {sorted(listed - granted)} / GRANT 에만: {sorted(granted - listed)}"


def test_the_contract_names_the_two_paths_that_open_the_old_stack():
    # 계약이 "화이트리스트"만 적고 멤버십·직접 GRANT 를 빠뜨리면 #168 이 다시 생긴다.
    text = CONTRACT.read_text(encoding="utf-8")
    assert "trend_radar_reader" in text
    assert "40-postgrest-tubedepth-grants.sh" in text
