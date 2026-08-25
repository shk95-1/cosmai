# AGENTS.md — 부팅과 절대 규칙 (본문 25줄 이하, tests/test_agents_md.py 가 지킨다)

## 부팅 (이 순서)
1. `tool/issue audit` → `tool/issue ready` (두 레포 한 그래프). 도구가 죽으면 `gh issue list -l ch:<채널>` 로 퇴화 부팅.
2. 핀 이슈: `[규약]`(규칙이 어디서 강제되는지 색인 + 변경 원장) · 현행 `[목표]` · 진행 중인 `[repo]` 이슈.
3. `STATE.md` §2(계산 불가 사실)·§3(승인 경계). 계산 가능한 사실은 `tool/status` 가 찍는다.
4. `contracts/README.md` → 자기 채널 에픽(`channel` 라벨) → 착수할 이슈 본문. 본문이 현재 진실, 코멘트는 이력.

## 절대 규칙
- 계획은 이슈에만. 메모리·문서·인계 파일·플랜 파일에 계획을 두지 않는다. 인계는 이슈 코멘트로.
- 이슈 1 = PR 1 = 채널 1(`ch:` 라벨 하나). 브랜치 `<채널>/<이슈#>-<slug>`, 워크트리 `../cosmai-wt/<채널, 슬래시는 하이픈>`, 착수 = assignee + 착수 코멘트(자원·워크트리·포트). 워커는 동시에 둘까지.
- 논리 의존은 `blockedBy`, 대기열은 sub-issue 위치. 파일 겹침은 적지 않는다. 메모(`memo`)는 실행하지 않는다 — 승격이 먼저.
- secret 은 키 이름만(`contracts/secrets.md`). 머신 경로 금지(레포 상대 또는 `~`). `--no-verify`·force push 금지.
- 운영 DB·컨테이너 조치는 코디네이터 세션이 직접, 한 명령씩. 매번 승인이 필요한 것은 `STATE.md` §3.
- 계약(`contracts/`)이 정본, DDL 은 추가만. 완료 커밋 본문에 `Closes #n`(교차 레포는 `owner/repo#n`).

## 규칙이 강제되는 자리
훅(`.githooks/`, `tool/checks/`) · 테스트(`tests/`) · 에이전트 정의(`.claude/agents/`) · `tool/issue lint` · 이 파일. 규칙 본문은 그 자리에 있고, 바뀌면 `[규약]` 에 코멘트 한 줄.
