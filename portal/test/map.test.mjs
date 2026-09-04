import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  nodesOf, feedbackEdges, layers, columns, positions, canvasSize, labelOf,
  NODE_W, NODE_H, COL_GAP, ROW_GAP, PAD,
} from '../public/map.js';
import { MAP_QUERIES } from '../public/query.js';
import { severityClass } from '../public/severity.js';
import { severityOf as opsSeverityOf } from '../public/ops.js';

const w = (stage, store) => ({ from_key: stage, from_kind: 'stage', to_key: store, to_kind: 'store' });
const r = (store, stage) => ({ from_key: store, from_kind: 'store', to_key: stage, to_kind: 'stage' });

// A scaled-down copy of the ops shape: one collector → table → analyze → metrics, and analyze reads its own output back.
const SAMPLE = [
  w('commerce:ranking', 'trend_radar.rank_snapshot'),
  r('trend_radar.rank_snapshot', 'analyze:all'),
  w('analyze:all', 'needs.need_mention'),
  r('needs.need_mention', 'analyze:all'), // feedback — a fact, not erased
  w('analyze:all', 'needs.metrics_need'),
];

test('노드는 엣지에서만 나온다 — 노드 표가 없다는 것이 설계다', () => {
  const keys = nodesOf(SAMPLE).map((n) => n.key);
  assert.deepEqual(keys, [
    'analyze:all',
    'commerce:ranking',
    'needs.metrics_need',
    'needs.need_mention',
    'trend_radar.rank_snapshot',
  ]);
  assert.equal(nodesOf(SAMPLE).find((n) => n.key === 'analyze:all').kind, 'stage');
  assert.equal(nodesOf(SAMPLE).find((n) => n.key === 'needs.need_mention').kind, 'store');
});

test('되먹임 엣지 하나를 찾아낸다 — 그것이 사이클을 닫는 엣지다', () => {
  const back = feedbackEdges(SAMPLE);
  assert.equal(back.length, 1);
  assert.equal(back[0].from_key, 'needs.need_mention');
  assert.equal(back[0].to_key, 'analyze:all');
});

test('되먹임 선택이 입력 순서에 흔들리지 않는다 — 그림이 새로고침마다 바뀌면 안 된다', () => {
  const a = feedbackEdges(SAMPLE).map((e) => `${e.from_key} ${e.to_key}`);
  const b = feedbackEdges([...SAMPLE].reverse()).map((e) => `${e.from_key} ${e.to_key}`);
  assert.deepEqual(a, b);
});

test('계층은 되먹임을 뺀 그래프의 가장 긴 경로다', () => {
  const depth = layers(SAMPLE);
  assert.equal(depth.get('commerce:ranking'), 0);
  assert.equal(depth.get('trend_radar.rank_snapshot'), 1);
  assert.equal(depth.get('analyze:all'), 2);
  assert.equal(depth.get('needs.need_mention'), 3);
  assert.equal(depth.get('needs.metrics_need'), 3);
});

test('사이클이 있어도 계층 계산이 끝난다', () => {
  // Without removing the feedback edge this loops forever. That is this function's reason to exist.
  const cyclic = [w('a:x', 's.one'), r('s.one', 'a:x')];
  const depth = layers(cyclic);
  assert.equal(depth.get('a:x'), 0);
  assert.equal(depth.get('s.one'), 1);
});

test('2-사이클은 읽기 쪽을 끊는다 — 쓰는 단계가 제 산출보다 뒤에 서면 안 된다', () => {
  // The cycle resolves either way it is cut, but the meaning differs. Cutting the write would make that stage look
  // like "something that runs after the metrics" — in the real ops graph analyze:polarity_missing actually landed there that way.
  const back = feedbackEdges(SAMPLE);
  assert.equal(back.length, 1);
  assert.equal(back[0].from_kind, 'store'); // the read was cut
  const depth = layers(SAMPLE);
  assert.ok(depth.get('analyze:all') < depth.get('needs.need_mention'));
});

test('쓰기만 남은 단계는 제 산출 바로 앞으로 당겨진다', () => {
  // Once the 2-cycle's read is cut, this stage has no incoming edge and lands in column 0 — but standing next
  // to the collector is not the meaning. It must read as standing right before the table it fills.
  const withIncremental = [
    ...SAMPLE,
    w('analyze:polarity_missing', 'needs.need_mention'),
    r('needs.need_mention', 'analyze:polarity_missing'),
  ];
  const depth = layers(withIncremental);
  assert.equal(depth.get('analyze:polarity_missing'), depth.get('needs.need_mention') - 1);
  assert.equal(depth.get('analyze:polarity_missing'), depth.get('analyze:all'));
});

test('열은 계층별로 갈리고 열 안 순서는 고정이다', () => {
  const cols = columns(SAMPLE);
  assert.equal(cols.length, 4);
  assert.deepEqual(cols[0].map((n) => n.key), ['commerce:ranking']);
  assert.deepEqual(cols[3].map((n) => n.key), ['needs.metrics_need', 'needs.need_mention']);
});

test('좌표가 겹치지 않는다', () => {
  const boxes = [...positions(SAMPLE).values()];
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i], b = boxes[j];
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      assert.ok(apart, `노드 둘이 겹친다: ${JSON.stringify(a)} ${JSON.stringify(b)}`);
    }
  }
});

test('같은 열의 노드는 x 가 같고 다른 열은 다르다', () => {
  const pos = positions(SAMPLE);
  assert.equal(pos.get('needs.metrics_need').x, pos.get('needs.need_mention').x);
  assert.notEqual(pos.get('commerce:ranking').x, pos.get('analyze:all').x);
});

test('판의 크기가 가장 긴 열을 담는다', () => {
  const { width, height } = canvasSize(SAMPLE);
  assert.equal(width, PAD * 2 + 4 * NODE_W + 3 * COL_GAP);
  assert.equal(height, PAD * 2 + 2 * NODE_H + 1 * ROW_GAP);
  for (const box of positions(SAMPLE).values()) {
    assert.ok(box.x + box.w <= width && box.y + box.h <= height, '노드가 판 밖으로 나간다');
  }
});

test('라벨은 단계와 저장소를 다르게 읽는다', () => {
  assert.equal(labelOf({ key: 'commerce:ranking', kind: 'stage' }), 'commerce / ranking');
  assert.equal(labelOf({ key: 'trend_radar.review', kind: 'store' }), 'trend_radar.review');
});

test('빈 그래프에서도 죽지 않는다', () => {
  assert.deepEqual(nodesOf([]), []);
  assert.deepEqual(feedbackEdges([]), []);
  assert.deepEqual(columns([]), [[]]);
  assert.ok(canvasSize([]).height > 0);
});

test('select 가 소비 함수들이 거르는 컬럼을 빠짐없이 담는다 (#130 이 데인 자리)', () => {
  for (const key of ['from_key', 'from_kind', 'to_key', 'to_kind']) {
    assert.ok(MAP_QUERIES.edge.select.includes(key), `edge select 에 ${key} 가 없다`);
  }
  for (const key of ['stage_key', 'arm', 'enabled']) {
    assert.ok(MAP_QUERIES.stage.select.includes(key), `stage select 에 ${key} 가 없다`);
  }
});

test('지도와 관제 표가 같은 행에 같은 색을 낸다 (#143)', () => {
  // If two screens showed the same stage in different colors, neither could be trusted. Calling the same module
  // is that guarantee, and this is what measures it.
  for (const row of [
    { freshness: 'stalled', last_run_status: 'failed' },
    { freshness: 'ok', last_run_status: 'partial' },
    { freshness: 'never', last_run_status: null },
    { freshness: 'disabled', last_run_status: null },
    { freshness: 'ok', last_run_status: 'ok' },
  ]) {
    assert.equal(severityClass(row), ['sev-critical', 'sev-warn', 'sev-idle', 'sev-ok', 'sev-muted'][opsSeverityOf(row)]);
  }
});

test('상태 질의가 색을 고르는 데 필요한 컬럼을 담는다', () => {
  for (const key of ['stage_key', 'freshness', 'last_run_status']) {
    assert.ok(MAP_QUERIES.health.select.includes(key), `health select 에 ${key} 가 없다`);
  }
});
