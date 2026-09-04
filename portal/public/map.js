// The judgement half of the structure map — pure functions only (#142). No DOM, no fetch, so portal/test can measure it directly.
//
// The picture comes from a declaration. needs.pipeline_edge carries the nodes and edges (#141) — coordinates
// are not set by hand because adding one more edge must make the picture follow along on its own.
//
// This graph is not a DAG. analyze:all both *writes and reads* need_mention — extraction writes it and
// aggregation reads it again within the same run. Since that is a fact, it is not erased: dropping the read
// edge would make lineage flow only one way, unable to trace back from metrics to the collection (#144).
// The layout absorbs it instead — the feedback edge is excluded from the layer calculation and drawn coming back in a different shape.

/** All node keys. Extracted from edges only — having no node table is #141's design. */
export function nodesOf(edges) {
  const kinds = new Map();
  for (const e of edges || []) {
    kinds.set(e.from_key, e.from_kind);
    kinds.set(e.to_key, e.to_kind);
  }
  return [...kinds].map(([key, kind]) => ({ key, kind })).sort((a, b) => (a.key < b.key ? -1 : 1));
}

/** Picks the feedback (back) edges via DFS. These are what the layer calculation excludes.
 *
 * The visit order is fixed by key sort — if the order depended on the response, the same data would pick a
 * different edge as feedback each time, and the picture would change on every refresh.
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

  // First, break any 2-cycle on the read side. When a stage both writes and reads the same table (analyze:all and
  // need_mention), the cycle resolves either way it is cut, but the meaning differs: cutting the write puts that stage
  // behind its own output column, reading as "something that runs after the metrics" (in practice analyze:polarity_missing
  // landed there that way). Since the write is the backbone of the flow, the read is turned into the feedback edge instead.
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
      if (c === GREY) back.push(e); // coming back to grey = an edge closing a remaining cycle
      else if (c === WHITE) visit(e.to_key);
    }
    color.set(key, BLACK);
  };
  // Starts from the roots (nodes nothing feeds into), and any cluster it does not reach from there is joined in key order.
  for (const n of [...roots, ...nodes]) if (color.get(n.key) === WHITE) visit(n.key);
  return back;
}

/** Each node's layer (column number). The length of the longest path in the graph with feedback edges removed.
 *
 * Why the longest path is used: assigning the shorter one would have edges skip several columns, cutting lines across the picture.
 */
export function layers(edges) {
  const back = new Set(feedbackEdges(edges).map((e) => `${e.from_key} ${e.to_key}`));
  const forward = (edges || []).filter((e) => !back.has(`${e.from_key} ${e.to_key}`));
  const incoming = new Map(nodesOf(edges).map((n) => [n.key, []]));
  for (const e of forward) incoming.get(e.to_key).push(e.from_key);

  const depth = new Map();
  const resolve = (key, seen = new Set()) => {
    if (depth.has(key)) return depth.get(key);
    if (seen.has(key)) return 0; // guard: feedback edges were removed, so this should never be reached
    seen.add(key);
    const parents = incoming.get(key) || [];
    const value = parents.length === 0 ? 0 : Math.max(...parents.map((p) => resolve(p, seen) + 1));
    depth.set(key, value);
    return value;
  };
  for (const n of nodesOf(edges)) resolve(n.key);

  // A node with no incoming edge is pulled to right before its own output. Once a 2-cycle's read edge is cut,
  // a "write-only-left" stage like analyze:polarity_missing becomes a root and lands next to the collector (column 0),
  // which is not the meaning -- that stage must read as standing right before the table it fills. It is only moved
  // rightward and kept ahead of its children, so it never disturbs another node's layer.
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

/** Returns nodes bucketed by column. Order within a column is key sort — stability comes first. */
export function columns(edges) {
  const depth = layers(edges);
  const width = Math.max(0, ...depth.values()) + 1;
  const out = Array.from({ length: width }, () => []);
  for (const n of nodesOf(edges)) out[depth.get(n.key)].push(n);
  return out;
}

export const NODE_W = 190, NODE_H = 34, COL_GAP = 90, ROW_GAP = 14, PAD = 20;

/** {x, y, w, h} from a node key. Since a pure function produces the coordinates, a test can ask about overlap. */
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

/** The canvas size. The longest column decides the height. */
export function canvasSize(edges) {
  const cols = columns(edges);
  const rows = Math.max(1, ...cols.map((c) => c.length));
  return {
    width: PAD * 2 + cols.length * NODE_W + Math.max(0, cols.length - 1) * COL_GAP,
    height: PAD * 2 + rows * NODE_H + Math.max(0, rows - 1) * ROW_GAP,
  };
}

/** The human-readable label. A store keeps its schema — the same name can exist in two schemas. */
export function labelOf(node) {
  return node.kind === 'stage' ? node.key.replace(':', ' / ') : node.key;
}
