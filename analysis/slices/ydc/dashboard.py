#!/usr/bin/env python3
"""판정 격자에서 셀을 누르면 근거 원문이 펼쳐지는 정적 화면을 만든다.

왜. 지표와 판정은 CSV 로 나와 있지만 "이 판정의 근거가 무엇인가"를 보려면 세 파일을
손으로 조인해야 한다. 검토자가 그렇게 쓰지 않는다. 누르면 근거가 나와야 한다.

무엇을 읽나. 전부 이미 있는 산출물이다. 새로 계산하지 않는다.
  reports/trend_judgement_v0.2.csv   판정 격자 (주제 x 분기 x 소스)
  reports/evidence_comments.csv      근거 댓글 (주제 x 분기, 좋아요 상위)
  reports/transcript_gain.csv        자막 회수량 (검증)
  reports/commerce_crosscheck.csv    커머스 속성 평가 대조 (검증)

소스는 CSV 의 `source` 열에서 읽어 탭으로 만든다. 하드코딩하지 않는다.
NAVER·커머스가 같은 형식으로 들어오면 탭이 저절로 생긴다.

의존성 없음. 표준 라이브러리로 만들고 브라우저에서 파일을 바로 연다.

사용법:
    python dashboard.py
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

csv.field_size_limit(10 ** 8)

# 판정 유형별 색. 상승 계열은 따뜻하게, 유지·확산은 차갑게, 판정 못 한 것은 회색.
TYPE_COLOR = {
    "급상승": "#D94F45",
    "단기 피크": "#E8913C",
    "신규 등장": "#8E6FD8",
    "채널 확산": "#3E9C9C",
    "지속 인기": "#4A7FC1",
    "사라짐": "#7A8B99",
    "근거 부족": "#D8DEE4",
    "판정 보류": "#E8ECEF",
    "미확정(진행 중)": "#F3F5F7",
}


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as h:
        return list(csv.DictReader(h))


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build(judgement: Path, evidence: Path, gain: Path, commerce: Path,
          sources_csv: Path, ingredient_csv: Path, out: Path,
          adfilter: Path | None = None, backtest: Path | None = None,
          unmatched: Path | None = None, ranking: Path | None = None) -> dict:
    rows = read(judgement)
    if not rows:
        raise SystemExit(f"{judgement} 가 비었다. trend_judgement 를 먼저 만들어야 한다.")

    sources = sorted({r["source"] for r in rows})
    quarters = sorted({r["quarter"] for r in rows})
    topics = sorted({r["topic_id"] for r in rows})

    cells: dict = defaultdict(dict)
    for r in rows:
        cells[r["source"]][f'{r["topic_id"]}|{r["quarter"]}'] = {
            "t": r.get("trend_type") or "",
            "score": num(r.get("opportunity_score")),
            "comp": num(r.get("composition")),
            "vel": num(r.get("velocity_yoy")),
            "pers": num(r.get("persistence")),
            "diff": num(r.get("channel_diffusion")),
            "ev": num(r.get("evidence_strength")),
            "docs": num(r.get("document_count")),
            "gap": num(r.get("gap_pp")),
            "hold": r.get("hold_reason") or "",
            "single": r.get("single_source") or "",
        }

    ev: dict = defaultdict(list)
    for r in read(evidence):
        ev[f'{r["topic_id"]}|{r["quarter"]}'].append({
            "like": int(num(r.get("like_count"), 0)),
            "text": r.get("text") or "",
            "term": r.get("matched_term") or "",
            "url": r.get("url") or "",
        })

    meta = {
        "tau": (rows[0].get("tau") or ""),
        "version": (rows[0].get("metric_version") or ""),
        "cells": len(rows),
        "judged": sum(1 for r in rows if (r.get("trend_type") or "") not in
                      ("", "근거 부족", "판정 보류", "미확정(진행 중)")),
    }

    data = {
        "sources": sources, "quarters": quarters, "topics": topics,
        "cells": cells, "evidence": ev, "colors": TYPE_COLOR, "meta": meta,
        "gain": read(gain), "commerce": read(commerce), "xsrc": read(sources_csv), "ingr": read(ingredient_csv),
        # 08.24 추가분. 없으면 그 절만 빠진다 — 화면이 깨지지 않게 빈 목록으로 둔다
        "adf": read(adfilter) if adfilter else [],
        "back": read(backtest) if backtest else [],
        # 성분표에 있는데 우리 사전에 없는 말만 남긴다. 2,365종 전부는 화면에 못 담는다
        "unmatched": [r for r in (read(unmatched) if unmatched else [])
                      if int(r.get("inci_products") or 0) > 0],
        "ranking": read(ranking) if ranking else [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    check_script(out)
    return meta


def check_script(page: Path) -> None:
    """생성한 JS 가 파싱되는지 확인한다.

    이 화면은 전부 자바스크립트가 그린다. 문법이 깨지면 페이지가 조용히 빈 화면이 되고,
    HTML 안에 문자열이 들어 있는지 grep 으로 확인해도 통과한다. 실제로 한 번 그렇게
    깨진 걸 모르고 배포했다. 그래서 만들 때마다 검사한다.
    """
    node = shutil.which("node")
    if not node:
        print("[경고] node 가 없어 JS 문법을 검사하지 못했다. 브라우저에서 직접 확인할 것")
        return
    body = re.search(r"<script>(.*?)</script>", page.read_text(encoding="utf-8"), re.S)
    if not body:
        raise SystemExit("생성한 페이지에 <script> 가 없다")
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as fh:
        fh.write(body.group(1))
        tmp = fh.name
    result = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
    Path(tmp).unlink(missing_ok=True)
    if result.returncode != 0:
        raise SystemExit("생성한 JS 가 문법 오류다. 화면이 빈 채로 나온다:\n"
                         + (result.stderr or result.stdout))
    print("JS 문법 검사 통과")


def render(d: dict) -> str:
    payload = json.dumps(d, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>선크림 트렌드 판정 · 근거 추적</title>
<style>
:root {{ --line:#DDE3E8; --ink:#1B2733; --dim:#6B7A88; --bg:#F7F9FB; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:14px/1.55 "맑은 고딕","Malgun Gothic",system-ui,sans-serif;
  color:var(--ink); background:var(--bg); }}
header {{ padding:20px 24px 14px; background:#fff; border-bottom:1px solid var(--line); }}
h1 {{ margin:0 0 6px; font-size:19px; }}
.sub {{ color:var(--dim); font-size:12.5px; }}
.warn {{ margin-top:10px; padding:8px 11px; background:#FFF6E5; border:1px solid #F0D9A8;
  border-radius:5px; font-size:12.5px; }}
main {{ padding:18px 24px 60px; }}
.tabs {{ display:flex; gap:6px; margin-bottom:12px; }}
.tabs button {{ padding:6px 13px; border:1px solid var(--line); background:#fff; cursor:pointer;
  border-radius:5px; font:inherit; font-size:13px; }}
.tabs button.on {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
.wrap {{ overflow-x:auto; background:#fff; border:1px solid var(--line); border-radius:6px; }}
table {{ border-collapse:collapse; font-size:12px; }}
th,td {{ border:1px solid var(--line); padding:0; }}
thead th {{ background:#F0F3F6; padding:6px 7px; font-weight:600; white-space:nowrap;
  position:sticky; top:0; z-index:2; }}
tbody th {{ background:#FAFBFC; padding:6px 10px; text-align:left; white-space:nowrap;
  position:sticky; left:0; z-index:1; font-weight:600; }}
.cell {{ width:100%; height:34px; border:0; cursor:pointer; font:inherit; font-size:11px;
  color:#fff; text-shadow:0 1px 1px rgba(0,0,0,.18); }}
.cell.pale {{ color:#7A8B99; text-shadow:none; }}
.cell:focus {{ outline:2px solid #1B2733; outline-offset:-2px; }}
.legend {{ display:flex; flex-wrap:wrap; gap:12px; margin:11px 0 0; font-size:12px;
  color:var(--dim); }}
.legend i {{ display:inline-block; width:11px; height:11px; border-radius:2px;
  margin-right:5px; vertical-align:-1px; }}
#panel {{ margin-top:18px; background:#fff; border:1px solid var(--line); border-radius:6px;
  padding:16px 18px; }}
#panel .empty {{ color:var(--dim); }}
.ptitle {{ font-size:16px; font-weight:600; margin:0 0 3px; }}
.pmeta {{ color:var(--dim); font-size:12.5px; margin-bottom:12px; }}
.metrics {{ display:flex; flex-wrap:wrap; gap:9px; margin-bottom:15px; }}
.m {{ border:1px solid var(--line); border-radius:5px; padding:6px 11px; min-width:96px; }}
.m b {{ display:block; font-size:11px; color:var(--dim); font-weight:600; }}
.m span {{ font-size:15px; }}
.hold {{ background:#FFF6E5; border:1px solid #F0D9A8; border-radius:5px;
  padding:8px 11px; margin-bottom:14px; font-size:12.5px; }}
h3 {{ font-size:13px; margin:0 0 8px; }}
.q {{ border-left:3px solid #4A7FC1; padding:8px 12px; margin-bottom:9px; background:#FAFBFC; }}
.q .top {{ font-size:11.5px; color:var(--dim); margin-bottom:4px; }}
.q mark {{ background:#FFE9A8; padding:0 1px; }}
.q a {{ color:#2A6099; }}
section.verify {{ margin-top:26px; }}
section.verify h2 {{ font-size:15px; margin:0 0 4px; }}
section.verify p {{ color:var(--dim); font-size:12.5px; margin:0 0 10px; }}
.vt {{ background:#fff; border:1px solid var(--line); border-radius:6px; font-size:12.5px; }}
.vt th,.vt td {{ padding:6px 11px; }}
.vt td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
.gain {{ color:#B3352C; font-weight:600; }}
</style></head><body>
<header>
  <h1>선크림 주제 트렌드 — 판정과 근거</h1>
  <div class="sub" id="sub"></div>
  <div class="warn" id="warn"></div>
</header>
<main>
  <div class="tabs" id="tabs"></div>
  <div class="wrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
  <div class="legend" id="legend"></div>
  <div id="panel"><div class="empty">격자에서 셀을 누르면 그 분기의 지표와 근거 원문이 여기에 나옵니다.</div></div>
  <section class="verify" id="verify"></section>
</main>
<script>
const D = {payload};
const $ = (id) => document.getElementById(id);
let src = D.sources[0];
let sel = null;   // 선택된 셀. 소스를 바꿔도 패널이 격자와 같은 소스를 보게 유지한다.

const SRC_LABEL = {{ youtube_video:'YouTube 영상 설명', youtube_comment:'YouTube 댓글' }};
const label = (s) => SRC_LABEL[s] || s;
const pct = (v) => v == null ? '—' : (v * 100).toFixed(2) + '%';
const fx = (v, n) => v == null ? '—' : v.toFixed(n);

$('sub').textContent =
  `${{D.topics.length}}개 주제 x ${{D.quarters.length}}개 분기 x ${{D.sources.length}}개 소스 = `
  + `${{D.meta.cells}}셀 · 판정된 셀 ${{D.meta.judged}}개 · τ=${{D.meta.tau}} · 지표 ${{D.meta.version}}`;
$('warn').innerHTML =
  '<b>격자(시계열 판정)에 있는 소스는 YouTube 뿐입니다.</b> 커머스 리뷰는 시계열이 '
  + '아니라 단일 시점이라 격자에 넣을 수 없고, 아래 <b>검증 1</b> 에서 같은 사전·같은 '
  + '정의로 나란히 비교합니다. NAVER·식약처·논문은 아직 데이터를 받지 못했습니다.';

D.sources.forEach(s => {{
  const b = document.createElement('button');
  b.textContent = label(s);
  b.onclick = () => {{ src = s; draw(); repanel(); }};
  b.dataset.src = s;
  $('tabs').append(b);
}});

Object.entries(D.colors).forEach(([k, v]) => {{
  const s = document.createElement('span');
  s.innerHTML = `<i style="background:${{v}}"></i>${{k}}`;
  $('legend').append(s);
}});

function draw() {{
  [...$('tabs').children].forEach(b => b.className = b.dataset.src === src ? 'on' : '');
  $('thead').innerHTML = '<tr><th>주제</th>' +
    D.quarters.map(q => `<th>${{q}}</th>`).join('') + '</tr>';
  $('tbody').innerHTML = D.topics.map(t => '<tr><th>' + t + '</th>' + D.quarters.map(q => {{
    const c = D.cells[src][t + '|' + q];
    if (!c) return '<td></td>';
    const col = D.colors[c.t] || '#EEE';
    const pale = ['근거 부족', '판정 보류', '미확정(진행 중)'].includes(c.t);
    const txt = c.score != null ? Math.round(c.score) : '';
    return `<td><button class="cell${{pale ? ' pale' : ''}}" style="background:${{col}}"`
      + ` data-t="${{t}}" data-q="${{q}}" title="${{c.t}}">${{txt}}</button></td>`;
  }}).join('') + '</tr>').join('');
  $('tbody').querySelectorAll('.cell').forEach(b =>
    b.onclick = () => {{ sel = {{ t: b.dataset.t, q: b.dataset.q }}; repanel(); }});
}}

// 패널은 항상 현재 소스를 따라간다. 탭을 바꿨을 때 옛 소스의 상세가 남아 있으면
// 격자와 패널이 서로 다른 소스를 말하게 되고, 그게 이 화면이 경고하는 바로 그 혼동이다.
function repanel() {{
  if (!sel) return;
  if (!D.cells[src][sel.t + '|' + sel.q]) {{
    $('panel').innerHTML = '<div class="empty">이 소스에는 '
      + sel.t + ' · ' + sel.q + ' 셀이 없습니다.</div>';
    return;
  }}
  detail(sel.t, sel.q);
}}

function mark(text, term) {{
  if (!term) return text;
  const i = text.toLowerCase().indexOf(term.toLowerCase());
  if (i < 0) return text;
  return text.slice(0, i) + '<mark>' + text.slice(i, i + term.length)
       + '</mark>' + text.slice(i + term.length);
}}

function detail(t, q) {{
  const c = D.cells[src][t + '|' + q];
  const quotes = D.evidence[t + '|' + q] || [];
  const M = [
    ['판정', c.t || '—'], ['기회 점수', c.score == null ? '—' : Math.round(c.score)],
    ['구성비', pct(c.comp)], ['velocity(YoY)', fx(c.vel, 3)],
    ['persistence', fx(c.pers, 2)], ['채널 확산', fx(c.diff, 3)],
    ['근거 강도', fx(c.ev, 1)], ['언급 문서', c.docs == null ? '—' : c.docs],
    ['갭(댓글−영상)', c.gap == null ? '—' : c.gap.toFixed(2) + '%p'],
  ];
  $('panel').innerHTML =
    `<p class="ptitle">${{t}} · ${{q}}</p>`
    + `<div class="pmeta">${{label(src)}}${{c.single === 'true' ? ' · 단일 소스 판정' : ''}}</div>`
    + '<div class="metrics">' + M.map(([k, v]) =>
        `<div class="m"><b>${{k}}</b><span>${{v}}</span></div>`).join('') + '</div>'
    + (c.hold ? `<div class="hold"><b>판정하지 않은 이유</b> — ${{c.hold}}</div>` : '')
    + `<h3>근거 · 그 분기 선크림 영상의 댓글 ${{quotes.length}}건 (좋아요 상위)</h3>`
    + (quotes.length ? quotes.map(x =>
        `<div class="q"><div class="top">좋아요 ${{x.like}}`
        + (x.term ? ` · 걸린 표현 <b>${{x.term}}</b>` : '')
        + (x.url ? ` · <a href="${{x.url}}" target="_blank" rel="noopener">원문 영상</a>` : '')
        + `</div>${{mark(x.text, x.term)}}</div>`).join('')
      : '<div class="empty">이 분기에 저장된 근거 댓글이 없습니다.</div>');
  $('panel').scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
}}

function verify() {{
  let h = '';
  if (D.xsrc.length) {{
    h += '<h2>검증 1 · 소스가 다르면 같은 주제를 다르게 말한다</h2>'
      + '<p>같은 사전, 같은 구성비 정의로 소스별로 따로 계산했습니다. 소스 간 문서 수는'
      + ' 합산하지 않습니다. 커머스 리뷰는 시계열이 아니라 단일 시점이므로 유튜브의 최근'
      + ' 확정 분기와만 나란히 놓았습니다.</p>'
      + '<table class="vt"><tr><th>주제</th><th>유튜브 댓글</th><th>유튜브 영상 설명</th>'
      + '<th>커머스 리뷰</th><th>커머스−영상</th><th>해석</th></tr>'
      + D.xsrc.map(r => {{
          const d = +r.commerce_minus_video_pp;
          const cls = Math.abs(d) >= 5 ? ' class="gain"' : '';
          return `<tr><td>${{r.topic_id}}</td>`
            + `<td class="n">${{r.youtube_comment_pct}}%</td>`
            + `<td class="n">${{r.youtube_video_pct}}%</td>`
            + `<td class="n">${{r.commerce_review_pct}}%</td>`
            + `<td class="n"${{cls}}>${{d > 0 ? '+' : ''}}${{d}}%p</td>`
            + `<td>${{r.reading}}</td></tr>`;
        }}).join('')
      + '</table>'
      + '<p style="margin-top:8px"><b>유튜브는 스펙·성분을, 커머스 리뷰는 실사용 감각을'
      + ' 담습니다.</b> 백탁이 그 대비의 극단입니다 — 영상 설명 0.31% 인데 리뷰에서는'
      + ' 12.09% 로 다섯째입니다. 영상 설명으로 13분기 전부 표본 부족이던 주제가'
      + ' 실재하는 주요 불만이라는 것을, 언급량이 아닌 다른 소스가 확인해 줍니다.</p>';
  }}
  if (D.gain.length) {{
    const rows = D.gain.filter(r => +r.gained > 0);
    h += '<h2 style="margin-top:22px">검증 2 · 자막을 넣으면 얼마나 더 보이나</h2>'
      + '<p>표본 측정입니다. 판정에는 반영하지 않았습니다. 자막은 공식 API 가 주지 않아'
      + ' 자료원 성격이 달라, 한계를 재는 용도로만 씁니다.</p>'
      + '<table class="vt"><tr><th>구분</th><th>주제</th><th>설명란만</th>'
      + '<th>자막 포함</th><th>회수</th></tr>'
      + rows.map(r => `<tr><td>${{r.bucket}}</td><td>${{r.topic_id}}</td>`
        + `<td class="n">${{r.videos_matched_baseline}}</td>`
        + `<td class="n">${{r.videos_matched_with_transcript}}</td>`
        + `<td class="n gain">+${{r.gained}}</td></tr>`).join('')
      + '</table>';
  }}
  h += '<h2 style="margin-top:22px">검증 3 · 커머스 플랫폼 자체 설문과 대조</h2>';
  if (D.commerce.length) {{
    h += '<p>언급량으로 만든 판정을 언급량이 아닌 데이터로 확인하는 자리입니다.'
      + ' 커머스는 시계열이 아니라 현재 스냅샷이라 최근 확정 분기와만 대조합니다.</p>'
      + '<table class="vt"><tr><th>주제</th><th>평가 제품</th><th>커머스 긍정률</th>'
      + '<th>우리 구성비</th><th>갭</th><th>해석</th></tr>'
      + D.commerce.map(r => `<tr><td>${{r.topic_id}}</td>`
        + `<td class="n">${{r.products_rated}}</td>`
        + `<td class="n">${{r.positive_rate_mean}}%</td>`
        + `<td class="n">${{r.youtube_composition_pct}}%</td>`
        + `<td class="n">${{r.youtube_gap_pp}}%p</td><td>${{r.reading}}</td></tr>`).join('')
      + '</table>'
      + '<p style="margin-top:8px"><b>현재는 근거 부족입니다.</b> 선크림 제품 163개 중'
      + ' 속성 평가가 있는 것이 2개뿐입니다. 우리 판정에 문서 5건을 요구하면서 이 대조에만'
      + ' 예외를 두면 이중 기준이라, 커버리지가 올라갈 때까지 해석하지 않습니다.</p>';
  }} else {{
    h += '<p>아직 대조할 데이터가 없습니다.</p>';
  }}
  if (D.ingr.length) {{
    h += '<h2 style="margin-top:22px">검증 4 · 실제 제품 구성과 담론 비교</h2>'
      + '<p>전성분표로 선크림 368개를 무기·유기·혼합자차로 분류했습니다. 분모가 다르므로'
      + ' (담론은 13개 주제 전체, 제품은 선크림 제품 수) 크기가 아니라 <b>순위</b>를 봅니다.</p>'
      + '<table class="vt"><tr><th>유형</th><th>제품</th><th>제품 비중</th><th>순위</th>'
      + '<th>댓글 비중</th><th>순위</th><th>배수</th><th>해석</th></tr>'
      + D.ingr.map(r => `<tr><td>${{r.filter_type}}</td>`
        + `<td class="n">${{r.products}}</td><td class="n">${{r.product_pct}}%</td>`
        + `<td class="n">${{r.product_rank}}</td>`
        + `<td class="n">${{r.youtube_comment_pct}}%</td>`
        + `<td class="n">${{r.comment_rank}}</td>`
        + `<td class="n">${{r.product_over_comment ? r.product_over_comment + 'x' : '—'}}</td>`
        + `<td>${{r.reading}}</td></tr>`).join('')
      + '</table>'
      + '<p style="margin-top:8px"><b>제품이 가장 많은 두 유형이 담론에서는 뒤에 있습니다.</b>'
      + ' 혼합자차는 제품의 32.9% 인데 댓글 구성비는 1.21% 로, 우리가 표본 부족으로 판정'
      + ' 보류한 주제입니다. 제품이 없어서가 아니라 <b>아무도 그 이름으로 말하지 않기'
      + ' 때문</b>이었습니다. 반대로 무기자차는 제품 비중이 가장 낮은데 담론은 1위입니다.</p>';
  }}
  if (D.adf.length) {{
    const v = '광고·협찬 영상 제외';
    const rows = D.adf.filter(r => r.variant === v && Math.abs(+r.diff_pp) >= 0.5)
                      .sort((a, b) => Math.abs(+b.diff_pp) - Math.abs(+a.diff_pp));
    const flips = D.adf.filter(r => r.variant === v)
                       .reduce((n, r) => n + (+r.flipped_cells || 0), 0);
    h += '<h2 style="margin-top:22px">검증 5 · 광고·협찬을 빼면 결론이 바뀐다</h2>'
      + '<p>선크림 장문 964편 중 <b>465편(48.2%)이 광고·협찬</b>입니다. 유튜버 자체 신고'
      + ' (<code>has_paid_product_placement</code>)는 254편뿐이고 설명란 문구로 407편이'
      + ' 걸립니다 &mdash; 신고 필드만 믿으면 절반을 놓칩니다. 아래는 465편을 빼고 같은'
      + ' 파이프라인을 다시 돌린 결과입니다.</p>'
      + `<p><b>판정 64셀 중 ${{flips}}셀의 유형이 뒤집히고 24셀이 표본 미달로 사라집니다.</b>`
      + ' 그래서 빼지 않고 <b>필터 민감</b>으로 표시합니다 &mdash; 빼면 모집단이 절반이 되고,'
      + ' 남기면 결론이 광고에 실립니다. 공짜인 선택지가 없으므로 숫자를 공개합니다.</p>'
      + '<table class="vt"><tr><th>소스</th><th>주제</th><th>전체 기준</th>'
      + '<th>광고 제외</th><th>차이</th><th>판정 변화</th></tr>'
      + rows.map(r => `<tr><td>${{r.source.replace('youtube_', '')}}</td>`
        + `<td>${{r.topic_id}}</td>`
        + `<td class="n">${{r.composition_base_pp}}%</td>`
        + `<td class="n">${{r.composition_kept_pp}}%</td>`
        + `<td class="n gain">${{+r.diff_pp > 0 ? '+' : ''}}${{r.diff_pp}}%p</td>`
        + `<td class="n">${{+r.flipped_cells ? r.flipped_cells + '셀' : '—'}}</td></tr>`).join('')
      + '</table>';
  }}
  if (D.back.length) {{
    const a = D.back.filter(r => r.hit === 'true').length;
    const b = D.back.filter(r => r.hit_level === 'true').length;
    h += '<h2 style="margin-top:22px">검증 6 · 과거로 돌려 판정을 채점했다</h2>'
      + '<p>과거 분기까지만 알고 있었던 것처럼 지표를 다시 계산해 판정하고, 그 뒤 4분기에'
      + ' 실제로 그 방향이 유지됐는지 봤습니다. <code>persistence</code> 의 기준선이 전체'
      + ' 기간 중앙값이라, 자르지 않으면 아직 오지 않은 분기를 보고 판정하게 됩니다.</p>'
      + `<p><b>기준 A(계속 상승) ${{a}}/${{D.back.length}}건 `
      + `${{Math.round(100 * a / D.back.length)}}% · `
      + `기준 B(수준 유지) ${{b}}/${{D.back.length}}건 `
      + `${{Math.round(100 * b / D.back.length)}}%</b> 이고 <b>기저율은 47%</b> 입니다`
      + ' (판정과 무관하게 오른 셀의 비율). 기준 A 의 직전 구간에는 급상승한 분기 자체가'
      + ' 들어 있어 평균 회귀만으로 실패가 납니다. 그래서 두 기준을 다 냅니다.</p>'
      + '<table class="vt"><tr><th>시점</th><th>소스</th><th>주제</th><th>판정</th>'
      + '<th>직전 4분기</th><th>이후 4분기</th><th>A</th><th>B</th></tr>'
      + D.back.map(r => `<tr><td>${{r.cutoff}}</td>`
        + `<td>${{r.source.replace('youtube_', '')}}</td><td>${{r.topic_id}}</td>`
        + `<td>${{r.trend_type}}</td>`
        + `<td class="n">${{r.before_pp}}%</td><td class="n">${{r.after_pp}}%</td>`
        + `<td class="n">${{r.hit === 'true' ? '적중' : '실패'}}</td>`
        + `<td class="n">${{r.hit_level === 'true' ? '적중' : '실패'}}</td></tr>`).join('')
      + '</table>'
      + '<p style="margin-top:8px"><b>이 도구는 변화를 서술하고 미래를 맞히지 않습니다.</b>'
      + ' 상승 계열 9건만 보면 기준 A 22% 로 기저율 47% 의 절반입니다. 13분기 · 계절 상품 ·'
      + ' 964편으로는 예측 모델을 세울 수 없습니다. 그래서 카드는 &ldquo;뜰 것이다&rdquo;가'
      + ' 아니라 <b>&ldquo;지금 이런 비대칭이 있다&rdquo;</b>로 씁니다.</p>';
  }}
  if (D.unmatched.length) {{
    const rows = D.unmatched.sort((x, y) => +y.inci_products - +x.inci_products);
    h += '<h2 style="margin-top:22px">검증 7 · 우리 사전이 못 보는 성분</h2>'
      + '<p>기획안에 적어 둔 최대 한계가 &ldquo;사전에 없는 성분은 관측되지 않는다&rdquo;'
      + ' 였습니다. 그 한계를 숫자로 재봤습니다. 판정 모집단을 형태소 분석해 명사를 뽑고,'
      + ' 선크림 아닌 영상 4,993편을 대조군으로 둬서 <b>선크림 문맥에 특이한 말</b>만'
      + ' 남긴 뒤(lift 2.0 이상), 그중 성분표에 실제로 있는 것을 골랐습니다.</p>'
      + `<p><b>사전 밖 명사 2,365종 중 성분표에 실제로 있는 말이 ${{rows.length}}종입니다.</b>`
      + ' 이게 사전 추가 최우선 후보이고, <b>자동으로 넣지 않았습니다</b> &mdash; 넣으면'
      + ' 임계값·판정·카드가 전부 다시 계산되고 팀에 공유한 숫자와 어긋납니다.</p>'
      + '<table class="vt"><tr><th>성분</th><th>성분표</th><th>lift</th>'
      + '<th>선크림 영상</th><th>댓글</th><th>최다 분기</th></tr>'
      + rows.map(r => `<tr><td>${{r.noun}}</td>`
        + `<td class="n gain">${{r.inci_products}}제품</td>`
        + `<td class="n">${{r.lift}}x</td>`
        + `<td class="n">${{r.sun_video_docs}}</td>`
        + `<td class="n">${{r.comment_docs}}</td>`
        + `<td class="n">${{r.peak_quarter}}</td></tr>`).join('')
      + '</table>';
  }}
  if (D.ranking.length) {{
    const rows = D.ranking.filter(r => +r.snapshots >= 10)
                          .sort((x, y) => +x.moved - +y.moved);
    const pick = rows.slice(0, 6).concat(rows.slice(-6).reverse());
    h += '<h2 style="margin-top:22px">검증 8 · 커머스 주간 순위 변동</h2>'
      + '<p>랭킹 스냅샷이 6일치가 됐고 수집기가 계속 돌고 있습니다. 데이터를 받는'
      + ' 방법부터 고쳐야 했습니다 &mdash; <b>정지 시각 고정 + 전순서 정렬 + 행수 대조</b>'
      + ' 입니다. 처음에 정렬 없이 <code>limit/offset</code> 으로 받았더니 수집기가 쓰는'
      + ' 동안 같은 offset 이 다른 행을 가리켜, <b>중복 37% 와 관측 깊이 변동이 보였습니다.</b>'
      + ' 둘 다 서버에 없는 현상이었고 <b>우리 추출 문제였습니다</b> &mdash; 정지·정렬로'
      + ' 다시 받으니 중복 0건, 보드 22개 중 21개 깊이 고정입니다.</p>'
      + '<p>그래서 아래는 <b>아티팩트를 걷어낸 것이 아니라 관심 구간을 고른 것</b>입니다.'
      + ' 올리브영 선케어 100위권은 실제로 크게 움직입니다(중앙 변동폭 38계단 · 신규 진입'
      + ' 111건). 상위 20위로 좁히면 8계단 · 23건입니다.</p>'
      + '<table class="vt"><tr><th>소스</th><th>브랜드</th><th>제품</th>'
      + '<th>처음</th><th>마지막</th><th>이동</th></tr>'
      + pick.map(r => `<tr><td>${{r.source}}</td><td>${{r.brand}}</td>`
        + `<td>${{r.product_name.slice(0, 40)}}</td>`
        + `<td class="n">${{r.first_rank}}</td><td class="n">${{r.last_rank}}</td>`
        + `<td class="n${{+r.moved < 0 ? ' gain' : ''}}">`
        + `${{+r.moved > 0 ? '+' : ''}}${{r.moved}}</td></tr>`).join('')
      + '</table>'
      + '<p style="margin-top:8px"><b>6일로 분기 트렌드는 못 봅니다.</b> 우리 유튜브 지표는'
      + ' 전년 동분기 비교이고 계절성을 상쇄하려고 그렇게 만들었습니다. 커머스가 답하는'
      + ' 질문은 다릅니다 &mdash; &ldquo;지금 무엇이 오르고 있나&rdquo;입니다.</p>';
  }}
  $('verify').innerHTML = h;
}}

draw();
verify();
</script></body></html>
"""


def demo() -> None:
    assert num("1.5") == 1.5 and num("") is None and num(None, 0) == 0
    assert "급상승" in TYPE_COLOR and TYPE_COLOR["급상승"].startswith("#")
    # 판정 못 한 유형은 옅은 색이어야 격자에서 구분된다
    for t in ("근거 부족", "판정 보류", "미확정(진행 중)"):
        assert TYPE_COLOR[t].upper() > "#C", t
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--judgement", type=Path, default=Path("reports/trend_judgement_v0.2.csv"))
    p.add_argument("--evidence", type=Path, default=Path("reports/evidence_comments.csv"))
    p.add_argument("--gain", type=Path, default=Path("reports/transcript_gain.csv"))
    p.add_argument("--commerce", type=Path, default=Path("reports/commerce_crosscheck.csv"))
    p.add_argument("--sources", type=Path, default=Path("reports/source_composition.csv"))
    p.add_argument("--ingredient", type=Path, default=Path("reports/ingredient_axis.csv"))
    p.add_argument("--adfilter", type=Path, default=Path("reports/spam_ad_sensitivity.csv"))
    p.add_argument("--backtest", type=Path, default=Path("reports/backtest.csv"))
    p.add_argument("--unmatched", type=Path, default=Path("reports/unmatched_terms.csv"))
    p.add_argument("--ranking", type=Path, default=Path("reports/commerce_ranking.csv"))
    p.add_argument("--out", type=Path, default=Path("reports/dashboard.html"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0
    meta = build(a.judgement, a.evidence, a.gain, a.commerce, a.sources,
                 a.ingredient, a.out, a.adfilter, a.backtest, a.unmatched,
                 a.ranking)
    size = a.out.stat().st_size / 1024
    print(f"{a.out} : {size:.0f} KB · {meta['cells']}셀 · 판정 {meta['judged']}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
