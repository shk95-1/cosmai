import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { latestRuns, scopesForRun, needRowsForScope, wishRowsForScope, productRows, runCaption } from '../public/screens.js';

const here = dirname(fileURLToPath(import.meta.url));
const needFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_need.sample.json'), 'utf8'));
const wishFixture = JSON.parse(readFileSync(join(here, 'fixtures/metrics_wish.sample.json'), 'utf8'));

// 시드는 슬라이스별로 다른 run(need=2, wish=3, 에픽 #16 §1단계 판정 4) — 표마다
// 자기 run을 써야 한다. 하나의 runId를 공유하면 wish가 항상 빈다(수정 라운드 1 결함 1).
test('latestRuns: need와 wish는 각자의 최신 run_id를 갖는다', () => {
  const { needRunId, wishRunId } = latestRuns(needFixture, wishFixture);
  assert.equal(needRunId, 2);
  assert.equal(wishRunId, 3);
});

test('wishRowsForScope: wish 자신의 run(3)으로 걸러야 행이 나온다', () => {
  const { wishRunId } = latestRuns(needFixture, wishFixture);
  const rows = wishRowsForScope(wishFixture, wishRunId, 'wish:a');
  assert.equal(rows.length, 4);
});

// 결함 2 재현: wish scope 셀렉트를 need scope(카테고리명)로 채우면 wish:* 값이
// 하나도 없다 — 화면마다 자기 표의 scope 목록을 써야 한다.
test('scopesForRun: need scope와 wish scope는 서로 다른 값 집합이다', () => {
  const { needRunId, wishRunId } = latestRuns(needFixture, wishFixture);
  const needScopes = scopesForRun(needFixture, needRunId);
  const wishScopes = scopesForRun(wishFixture, wishRunId);
  assert.deepEqual(needScopes, ['선블록', '쿠션']);
  assert.deepEqual(wishScopes, ['wish:a', 'wish:b']);
  assert.equal(needScopes.some((s) => wishScopes.includes(s)), false);
});

test('needRowsForScope: 카테고리 합(product_ref/month 빈 값)만 남는다', () => {
  const { needRunId } = latestRuns(needFixture, wishFixture);
  const rows = needRowsForScope(needFixture, needRunId, '선블록');
  assert.deepEqual(rows.map((r) => r.need_key), ['밀림', '끈적유분']);
});

test('productRows: run 2의 product_ref 비어있지 않은 행이 나온다', () => {
  const { needRunId } = latestRuns(needFixture, wishFixture);
  const rows = productRows(needFixture, needRunId);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].product_ref, 'oy:A1');
});

test('runCaption: need·wish run을 함께 보인다', () => {
  assert.equal(runCaption(2, 3), 'need run #2 · wish run #3');
  assert.equal(runCaption(null, null), '데이터 없음');
});
