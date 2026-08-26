// 한 단계가 얼마나 나쁜가 — 관제 표(#139)와 구조 지도(#143)가 **같은 함수**를 부른다.
//
// 두 화면이 같은 단계를 다른 색으로 보이면 둘 다 못 믿게 된다. 그래서 여기 하나만 둔다.
// 판정 자체는 여기 없다: needs.pipeline_health 가 freshness 와 last_run_status 를 이미 정해서
// 주고(#138), 이 파일은 그 둘을 색 하나로 접는 규칙만 갖는다.

// 심각도는 **하나의 척도**다: 0 위급 · 1 주의 · 2 아직 안 돎 · 3 정상 · 4 선언상 꺼짐.
// 두 컬럼이 각자의 등수를 갖고 그 등수를 섞으면 비교가 뜻을 잃는다 — 같은 자로 재야
// "둘 중 나쁜 쪽" 이 성립한다.
const FRESHNESS_SEVERITY = { stalled: 0, late: 1, never: 2, ok: 3, disabled: 4 };
// yielded 는 소스 락에 밀려 물러난 run 이라 고장이 아니다(#78). blocked 는 403·429 — 주의다.
const STATUS_SEVERITY = { failed: 0, partial: 1, blocked: 1, yielded: 3, ok: 3 };

export const SEVERITY_CLASS = ['sev-critical', 'sev-warn', 'sev-idle', 'sev-ok', 'sev-muted'];

export function severityOf(row) {
  // 모르는 값과 없는 값(아직 안 돈 단계의 last_run_status)은 판단에서 빠진다 — 정상으로도
  // 고장으로도 세지 않는다. 그러면 남은 한쪽이 그 행의 심각도를 결정한다.
  const f = FRESHNESS_SEVERITY[row.freshness];
  const s = STATUS_SEVERITY[row.last_run_status];
  return Math.min(f === undefined ? 99 : f, s === undefined ? 99 : s);
}

/** 심각도에 붙는 CSS 클래스. 아는 값이 하나도 없으면 '아직 안 돎' 으로 둔다. */
export function severityClass(row) {
  return SEVERITY_CLASS[severityOf(row)] || 'sev-idle';
}
