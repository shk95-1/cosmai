// 구조 지도의 판단 부분 — 순수 함수만 (#142). DOM 도 fetch 도 없어 portal/test 가 그대로 잰다.
//
// 그림은 선언에서 나온다. needs.pipeline_edge 가 노드와 엣지를 진다(#141) — 좌표를 손으로 잡지
// 않는 이유는 엣지를 하나 더했을 때 그림이 저절로 따라와야 하기 때문이다.
//
// 이 그래프는 DAG 가 아니다. analyze:all 이 need_mention 을 *쓰고 또 읽는다* — 추출이 쓰고
// 집계가 같은 run 안에서 다시 읽는다. 사실이므로 지우지 않는다: 읽기 엣지를 빼면 계보가 한쪽으로만
// 흘러 지표에서 수집분으로 거꾸로 못 탄다(#144). 대신 레이아웃이 그것을 감당한다 — 계층 계산에서
// 되먹임 엣지를 빼고, 그림에서는 다른 모양으로 되돌아오게 그린다.

/** 노드 키 전부. 엣지에서만 뽑는다 — 노드 표가 없다는 것이 #141 의 설계다. */
export function nodesOf(edges) {
  const kinds = new Map();
  for (const e of edges || []) {
    kinds.set(e.from_key, e.from_kind);
    kinds.set(e.to_key, e.to_kind);
  }
  return [...kinds].map(([key, kind]) => ({ key, kind })).sort((a, b) => (a.key < b.key ? -1 : 1));
}

/** DFS 로 되먹임(back) 엣지를 고른다. 계층 계산에서 뺄 것들이다.
 *
 * 방문 순서를 키 정렬로 고정한다 — 순서가 응답에 좌우되면 같은 데이터가 매번 다른 엣지를
 * 되먹임으로 고르고, 그림이 새로고침마다 바뀐다.
 */
export function feedbackEdges(edges) {
  const out = new Map();
  for (const e of edges || []) {
    if (!out.has(e.from_key)) out.set(e.from_key, []);
    out.get(e.from_key).push(e);
  }
  for (const list of out.values()) list.sort((a, b) => (a.to_key < b.to_key ? -1 : 1));

  const nodes = nodesOf(edges);
  const back = [];

  // 먼저 2-사이클을 읽기 쪽으로 끊는다. 단계가 같은 표를 쓰고 또 읽으면(analyze:all 과
  // need_mention) 어느 쪽을 끊든 사이클은 풀리지만 뜻이 다르다: 쓰기를 끊으면 그 단계가 제
  // 산출보다 뒤 열에 서서 "지표 다음에 도는 것" 처럼 보인다(실측에서 analyze:polarity_missing 이
  // 그렇게 섰다). 흐름의 등뼈는 쓰기이므로 읽기를 되먹임으로 돌린다.
  const pairs = new Set((edges || []).map((e) => `${e.from_key}|${e.to_key}`));
  const cut = new Set();
  for (const e of edges || []) {
    if (e.from_kind === 'store' && pairs.has(`${e.to_key}|${e.from_key}`)) {
      back.push(e);
      cut.add(`${e.from_key}|${e.to_key}`);
    }
  }

  const WHITE = 0, GREY = 1, BLACK = 2;
  const color = new Map(nodes.map((n) => [n.key, WHITE]));
  const hasIncoming = new Set((edges || []).filter((e) => !cut.has(`${e.from_key}|${e.to_key}`)).map((e) => e.to_key));
  const roots = nodes.filter((n) => !hasIncoming.has(n.key));

  const visit = (key) => {
    color.set(key, GREY);
    for (const e of out.get(key) || []) {
      if (cut.has(`${e.from_key}|${e.to_key}`)) continue;
      const c = color.get(e.to_key);
      if (c === GREY) back.push(e); // 회색으로 돌아왔다 = 남은 사이클을 닫는 엣지
      else if (c === WHITE) visit(e.to_key);
    }
    color.set(key, BLACK);
  };
  // 뿌리(아무것도 안 먹는 노드)부터 돌고, 거기서 닿지 않는 덩어리는 키 순서로 잇는다.
  for (const n of [...roots, ...nodes]) if (color.get(n.key) === WHITE) visit(n.key);
  return back;
}

/** 노드마다 계층(열 번호). 되먹임 엣지를 뺀 그래프에서 가장 긴 경로의 길이다.
 *
 * 가장 긴 경로를 쓰는 이유: 짧은 쪽으로 배정하면 엣지가 여러 열을 건너뛰어 선이 그림을 가로지른다.
 */
export function layers(edges) {
  const back = new Set(feedbackEdges(edges).map((e) => `${e.from_key} ${e.to_key}`));
  const forward = (edges || []).filter((e) => !back.has(`${e.from_key} ${e.to_key}`));
  const incoming = new Map(nodesOf(edges).map((n) => [n.key, []]));
  for (const e of forward) incoming.get(e.to_key).push(e.from_key);

  const depth = new Map();
  const resolve = (key, seen = new Set()) => {
    if (depth.has(key)) return depth.get(key);
    if (seen.has(key)) return 0; // 방어: 되먹임을 뺐으므로 여기 오지 않는다
    seen.add(key);
    const parents = incoming.get(key) || [];
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => resolve(p, seen) + 1));
    depth.set(key, value);
    return value;
  };
  for (const n of nodesOf(edges)) resolve(n.key);

  // 들어오는 엣지가 없는 노드는 제 산출 바로 앞으로 당긴다. 2-사이클의 읽기를 끊고 나면
  // analyze:polarity_missing 처럼 "쓰기만 남은" 단계가 뿌리가 되어 수집기 옆(0열)에 서는데,
  // 그것은 뜻이 아니다 -- 그 단계는 제가 채우는 표 바로 앞에 있어야 읽힌다. 오른쪽으로만
  // 옮기고 자식보다 앞을 지키므로 다른 노드의 계층을 흔들지 않는다.
  const children = new Map(nodesOf(edges).map((n) => [n.key, []]));
  for (const e of forward) children.get(e.from_key).push(e.to_key);
  for (const n of nodesOf(edges)) {
    if ((incoming.get(n.key) || []).length > 0) continue;
    const kids = children.get(n.key) || [];
    if (kids.length === 0) continue;
    const pulled = Math.min(...kids.map((k) => depth.get(k))) - 1;
    if (pulled > depth.get(n.key)) depth.set(n.key, pulled);
  }
  return depth;
}

/** 열마다 노드를 담아 돌려준다. 열 안 순서는 키 정렬 — 안정성이 먼저다. */
export function columns(edges) {
  const depth = layers(edges);
  const width = Math.max(0, ...depth.values()) + 1;
  const out = Array.from({ length: width }, () => []);
  for (const n of nodesOf(edges)) out[depth.get(n.key)].push(n);
  return out;
}

export const NODE_W = 190, NODE_H = 34, COL_GAP = 90, ROW_GAP = 14, PAD = 20;

/** 노드 키에서 {x, y, w, h}. 좌표를 순수 함수가 내므로 테스트가 겹침을 물을 수 있다. */
export function positions(edges) {
  const out = new Map();
  columns(edges).forEach((column, index) => {
    column.forEach((node, row) => {
      out.set(node.key, {
        x: PAD + index * (NODE_W + COL_GAP),
        y: PAD + row * (NODE_H + ROW_GAP),
        w: NODE_W,
        h: NODE_H,
      });
    });
  });
  return out;
}

/** 판의 크기. 가장 긴 열이 높이를 정한다. */
export function canvasSize(edges) {
  const cols = columns(edges);
  const rows = Math.max(1, ...cols.map((c) => c.length));
  return {
    width: PAD * 2 + cols.length * NODE_W + Math.max(0, cols.length - 1) * COL_GAP,
    height: PAD * 2 + rows * NODE_H + Math.max(0, rows - 1) * ROW_GAP,
  };
}

/** 사람이 읽는 라벨. 저장소는 스키마를 남긴다 — 같은 이름이 두 스키마에 있을 수 있다. */
export function labelOf(node) {
  return node.kind === 'stage' ? node.key.replace(':', ' / ') : node.key;
}
