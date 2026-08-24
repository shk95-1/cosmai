#!/usr/bin/env python3
"""trend.py가 낸 분기 시계열을 자기완결 HTML 한 장으로 렌더한다.

v0.2에서 바뀐 것: **지표를 직접 계산하지 않는다.** `trend.py --out`이 만든 CSV만 읽는다.

이전 버전은 정규화 run에서 `해당 주제 언급 영상 / 분기 전체 영상`(문서 기준 share)을
직접 계산했다. 그 지표는 무효다 — 유튜버 설명란 길이 중앙값이 3년간 1,253자에서 709자로
줄어서, 분자(언급 수)만 줄고 분모(영상 수)는 그대로여서 13개 주제 중 10개가 동반
하락한다(합계 -28.6%p). 자세한 근거는 reports/TEAM_DECISIONS_v0.2.md 2.2절에 있다.

지표 구현이 두 곳에 있으면 한쪽만 고쳐지고 두 결과가 갈린다. 그래서 계산은 trend.py,
렌더는 이 파일로 나눈다.

외부 리소스를 참조하지 않는다. 사설망에서도 그대로 뜨고, 열어본 시각이 제3자에게 새지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import html
from pathlib import Path
from typing import Any

from topics import TOPICS

SOURCE_LABEL = {"youtube_video": "영상 설명", "youtube_comment": "댓글"}


def read_series(path: Path) -> tuple[list[str], dict[tuple[str, str], list[dict[str, Any]]]]:
    """(분기 목록, (source, topic) -> 분기순 행 목록)."""
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    if not rows:
        raise SystemExit(f"{path}가 비었습니다. trend.py를 먼저 실행하세요.")
    quarters = sorted({r["quarter"] for r in rows})
    index = {q: i for i, q in enumerate(quarters)}
    series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["source"], row["topic_id"])
        series.setdefault(key, [None] * len(quarters))[index[row["quarter"]]] = row
    return quarters, series


def sparkline(values: list[float], ceiling: float, width: int = 132, height: int = 30) -> str:
    """모든 행이 같은 y축(ceiling)을 쓴다. 행끼리 높이를 비교할 수 있어야 한다."""
    if len(values) < 2 or ceiling <= 0:
        return ""
    step = width / (len(values) - 1)
    points = " ".join(
        f"{i * step:.1f},{height - (v / ceiling) * (height - 4) - 2:.1f}"
        for i, v in enumerate(values)
    )
    last_x, last_y = points.split()[-1].split(",")
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-hidden="true">'
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="1.6"/>'
        f'<circle cx="{last_x}" cy="{last_y}" r="2.6" fill="currentColor"/></svg>'
    )


def block(source: str, quarters: list[str], series, note: dict[str, str]) -> str:
    """한 소스(영상 설명 / 댓글)의 표 하나."""
    keys = [k for k in series if k[0] == source]
    if not keys:
        return ""
    comp = {k[1]: [float(r["composition"]) * 100 for r in series[k]] for k in keys}
    ceiling = max((v for row in comp.values() for v in row), default=0.0)
    # 최근 4분기 평균 - 그 앞 4분기 평균. 같은 계절 구간끼리 비교해야 계절성이 상쇄된다.
    def shift(topic: str) -> float:
        v = comp[topic]
        if len(v) < 8:
            return 0.0
        late, early = v[-5:-1], v[-9:-5]
        return sum(late) / len(late) - sum(early) / len(early)

    body = []
    for topic in sorted(comp, key=shift, reverse=True):
        rows = series[(source, topic)]
        vals = comp[topic]
        delta = shift(topic)
        ok = sum(1 for r in rows if r["sample_ok"] == "True")
        cells = "".join(
            f"<td class='num{"" if r["sample_ok"] == "True" else " thin"}'>{v:.1f}</td>"
            for r, v in zip(rows, vals)
        )
        sign = "up" if delta > 0.3 else "down" if delta < -0.3 else "flat"
        warn = "" if ok >= 10 else f" · 표본 충족 {ok}/{len(rows)}분기"
        body.append(
            f"<tr><th>{html.escape(topic)}"
            f"<small>{html.escape(note.get(topic, ''))}{html.escape(warn)}</small></th>"
            f"<td class=spark>{sparkline(vals, ceiling)}</td>{cells}"
            f"<td class='num {sign}'>{delta:+.1f}</td></tr>"
        )

    docs = series[keys[0]]
    heads = "".join(f"<th class=num>{q[2:]}</th>" for q in quarters)
    denom = "".join(f"<td class=num>{r['quarter_documents']}</td>" for r in docs)
    return f"""<h2>{html.escape(SOURCE_LABEL.get(source, source))}</h2>
<table>
<thead><tr><th>주제</th><th>추이</th>{heads}<th class=num>변화</th></tr></thead>
<tbody>{"".join(body)}</tbody>
<tfoot><tr><th>분모 (분기 문서 수)</th><td></td>{denom}<td></td></tr></tfoot>
</table>
"""


def render(source_path: Path, quarters: list[str], series) -> str:
    note = {t["topic"]: t["note"] for t in TOPICS}
    blocks = "".join(block(s, quarters, series, note) for s in ("youtube_video", "youtube_comment"))
    version = next(iter(series.values()))[0]["metric_version"]
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>선크림 주제 트렌드 {html.escape(version)}</title>
<style>
:root {{ color-scheme: light dark;
  --bg:#fbfbfa; --panel:#fff; --line:#e4e4e1; --ink:#1a1a18; --dim:#6b6b66;
  --up:#a03434; --down:#2f6f4f; --flat:#8a8a84; }}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#17181a; --panel:#1f2124; --line:#2e3135; --ink:#e8e8e4; --dim:#9a9a93;
  --up:#e08585; --down:#7fc9a0; --flat:#757570; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; padding:24px; background:var(--bg); color:var(--ink);
  font:14px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif }}
h1 {{ font-size:17px; margin:0 0 4px }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.07em; color:var(--dim);
  margin:26px 0 8px }}
.src {{ color:var(--dim); font-size:12px; margin-bottom:18px }}
.warn {{ border-left:3px solid var(--up); background:var(--panel); padding:10px 14px;
  margin:0 0 18px; font-size:13px; max-width:940px }}
table {{ border-collapse:collapse; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; overflow:hidden; font-variant-numeric:tabular-nums }}
th,td {{ padding:8px 12px; border-bottom:1px solid var(--line); text-align:left }}
thead th {{ font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--dim) }}
tbody th {{ font-weight:600; max-width:250px }}
tbody th small {{ display:block; font-weight:400; color:var(--dim); font-size:11px; line-height:1.35 }}
.num {{ text-align:right; font-variant-numeric:tabular-nums }}
.thin {{ color:var(--dim); font-style:italic }}
.spark {{ color:var(--dim); padding:4px 12px; width:150px }}
.up {{ color:var(--up); font-weight:600 }}
.down {{ color:var(--down); font-weight:600 }}
.flat {{ color:var(--flat) }}
tfoot td,tfoot th {{ color:var(--dim); font-size:12px; border-top:2px solid var(--line) }}
tr:last-child td, tr:last-child th {{ border-bottom:none }}
</style>
<h1>선크림 주제 트렌드 — 분기별 주제 간 구성비 (%)</h1>
<p class=src>출처: {html.escape(str(source_path))} · 지표 {html.escape(version)} ·
값은 <code>해당 주제 mention / 그 분기 전체 주제 mention</code> ·
모집단은 선크림 언급 장문(&gt;60초) 영상 · <b>변화</b>는 최근 4분기 평균 − 그 앞 4분기 평균</p>
<div class=warn><b>읽는 법 세 가지.</b>
① 구성비다. 절대 언급량이 아니다 — 설명란 길이가 3년간 44% 줄어 절대량은 쓸 수 없다.
② <i>기울임</i>으로 표시된 칸은 <code>document_count &lt; 5</code>로 표본이 부족하다. 그 행의 변화값은 믿지 말 것.
③ 영상 설명과 댓글을 합치지 않았다. 영상은 스펙·포뮬러, 댓글은 사용감·불만을 담는다.
두 표에서 방향이 갈리는 주제가 제품 공백 후보다.</div>
{blocks}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="trend.py 출력 CSV를 HTML 리포트로 렌더")
    ap.add_argument(
        "csv_path", type=Path, nargs="?", default=Path("reports/trend_sunscreen_v0.2.csv"),
        help="trend.py --out 으로 만든 CSV",
    )
    ap.add_argument("-o", "--output", type=Path, default=Path("reports/trend_report.html"))
    args = ap.parse_args()

    quarters, series = read_series(args.csv_path)
    assert quarters, "분기가 하나도 없습니다"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.csv_path, quarters, series), encoding="utf-8")
    topics = len({k[1] for k in series})
    print(f"[report] {args.output} - 주제 {topics}개 x 분기 {len(quarters)}개 x 소스 {len({k[0] for k in series})}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
