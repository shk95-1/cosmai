<!-- origin: service/yt-scrapper/decisions/README.md:1-13 + decisions/001 (shape)
     reuse: ONE file docs/decisions.md; append an entry only after the failure happened and was measured; ≤10 lines each; delete the entry when its condition stops holding. -->
## <rule in one line>

- **규칙**: 무엇을 한다 / 하지 않는다.
- **겪은 비용**: 날짜, 수치 (예: "40잡 스윕에서 처리량 8× 저하, 단일 잡·전체 스위트·10잡 시험에서는 안 보였다").
- **그만둘 조건**: 어떤 사실이 바뀌면 이 규칙이 틀리게 되는가 (예: "Postgres로 이관하면 `readonly` 구분은 아무것도 벌지 못한다" → 이관일에 항목 삭제).
- **강제**: 훅 | 테스트 경로 | 없음(사람이 grep).
