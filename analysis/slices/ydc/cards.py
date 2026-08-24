#!/usr/bin/env python3
"""R&D Opportunity Card 를 규칙으로 만든다. 숫자와 유형은 코드가 정하고 LLM 은 쓰지 않는다.

기획안 §8 이 요구한 산출물이다. 카드 하나가 "이번 분기에 이 주제를 추가로 조사할지"를
담당자가 판단할 수 있는 최소 단위다.

설계 원칙 네 개.
  1. 카드 유형은 규칙이 정한다. LLM 이 "이건 제품 공백이야"라고 판단하지 않는다.
  2. 모든 수치는 이미 만든 산출물에서 그대로 가져온다. 여기서 새로 계산하지 않는다.
  3. 근거는 원문 링크까지 붙인다. 링크가 없으면 카드로 만들지 않는다.
  4. 한계를 카드 안에 넣는다. 표본 부족·단일 소스·과소 집계를 숨기지 않는다.

카드 유형. 기획안은 5종을 정했는데 실측에서 하나가 더 필요해졌다.
  검증된 성장      양쪽 소스가 같이 오르고 갭이 작다
  제품 공백 기회    댓글이 영상보다 훨씬 많이 말한다 (gap >= +2%p)
  포화 시장        구성비가 높은데 변화가 없다
  단기 유행 위험    단기 피크
  선행 연구 기회    논문 계열이 앞서고 소비자 언급이 낮다  ← 논문 데이터 미도착, 보류
  표현 공백        제품은 많은데 그 이름으로 말하는 사람이 없다  ← 신규
마지막이 신규다. 혼합자차가 선크림 제품의 32.9% 인데 댓글 구성비는 1.21% 였다.
`제품 공백 기회`와 방향이 반대다 — 그쪽은 제품이 없고 이쪽은 이름이 없다.

LLM 을 쓰지 않는 이유. 지금 키가 없다. 요약 문장 자리에는 근거 원문을 그대로 넣는다.
키가 생기면 그 자리만 교체하면 되고, 나머지는 전부 코드가 만든 값이라 바뀌지 않는다.

사용법:
    python cards.py --quarter 2026Q2
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from topics import TOPICS

csv.field_size_limit(10 ** 8)

GAP_PRODUCT_GAP = 2.0      # 제품 공백으로 볼 최소 갭(%p)
EXPRESSION_RATIO = 5.0     # 표현 공백으로 볼 최소 배수(제품 비중 / 담론 비중)
SATURATED_COMPOSITION = 15.0
RISING = ("급상승", "단기 피크")
STEADY = ("지속 인기", "채널 확산")
NOT_JUDGED = ("근거 부족", "판정 보류", "미확정(진행 중)", "")

# 별칭은 topics.py 에 구체적인 것부터 적혀 있다. 근거를 고를 때 이 순서를 먼저 보고
# 좋아요를 나중에 본다. 그러지 않으면 `제형`·`성분` 같은 일반어로 걸린 댓글이 좋아요가
# 많다는 이유로 뽑혀, 주제와 무관한 문장이 근거로 실린다(발림성에서 실제로 그랬다).
ALIAS_RANK = {t["topic"]: {term: i for i, term in enumerate(t["ko"] + t["latin"])}
              for t in TOPICS}
GENERIC_ALIAS = {"발림성": {"제형", "텍스처"}, "성분_신제품": {"성분"},
                 "촉촉함_건조함": {"수분"}, "지속력_워터프루프": {"지속"}}


def quote_key(topic: str, row: dict) -> tuple:
    """구체적인 별칭이 먼저, 그다음 좋아요 순."""
    rank = ALIAS_RANK.get(topic, {}).get(row.get("matched_term") or "", 99)
    return (rank, -num(row.get("like_count")))


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as h:
        return list(csv.DictReader(h))


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def classify(topic: str, cmt: dict, vid: dict, ingr: dict | None) -> tuple[str, str] | None:
    """(카드 유형, 배정 근거). 어느 규칙에도 안 걸리면 None — 카드로 만들지 않는다."""
    gap = num(cmt.get("gap_pp"))
    c_type, v_type = cmt.get("trend_type", ""), vid.get("trend_type", "")
    comp = num(cmt.get("composition")) * 100

    if ingr and num(ingr.get("product_over_comment")) >= EXPRESSION_RATIO:
        return ("표현 공백",
                f"제품 비중 {ingr['product_pct']}%(순위 {ingr['product_rank']})인데 "
                f"댓글 구성비 {ingr['youtube_comment_pct']}%(순위 {ingr['comment_rank']}), "
                f"{ingr['product_over_comment']}배 차이")
    if gap >= GAP_PRODUCT_GAP and c_type not in NOT_JUDGED:
        return ("제품 공백 기회",
                f"갭 +{gap:.2f}%p — 댓글이 영상 설명보다 훨씬 많이 말한다 (댓글 판정 {c_type})")
    if c_type in RISING or v_type in RISING:
        if abs(gap) < GAP_PRODUCT_GAP:
            return ("검증된 성장",
                    f"댓글 {c_type or '—'} / 영상 {v_type or '—'}, 갭 {gap:+.2f}%p 로 작다")
        return ("단기 유행 위험", f"단기 피크 관측 (댓글 {c_type} / 영상 {v_type})")
    if c_type in STEADY and v_type in STEADY and comp >= SATURATED_COMPOSITION:
        return ("포화 시장", f"구성비 {comp:.2f}% 로 상위인데 양쪽 다 {c_type}·{v_type}")
    return None


def build(quarter: str, reports: Path) -> list[dict]:
    j = read(reports / "trend_judgement_v0.2.csv")
    cmt = {r["topic_id"]: r for r in j if r["source"] == "youtube_comment" and r["quarter"] == quarter}
    vid = {r["topic_id"]: r for r in j if r["source"] == "youtube_video" and r["quarter"] == quarter}
    ev: dict[str, list[dict]] = {}
    for r in read(reports / "evidence_comments.csv"):
        if r["quarter"] == quarter:
            ev.setdefault(r["topic_id"], []).append(r)
    xsrc = {r["topic_id"]: r for r in read(reports / "source_composition.csv")}
    ingr = {r["filter_type"]: r for r in read(reports / "ingredient_axis.csv")}
    comm = {r["topic_id"]: r for r in read(reports / "commerce_crosscheck.csv")}
    gain = {}
    for r in read(reports / "transcript_gain.csv"):
        if r["bucket"] == "장문":
            gain[r["topic_id"]] = r

    cards = []
    for topic in sorted(set(cmt) | set(vid)):
        c, v = cmt.get(topic, {}), vid.get(topic, {})
        got = classify(topic, c, v, ingr.get(topic))
        if not got:
            continue
        kind, basis = got
        quotes = sorted(ev.get(topic, []), key=lambda r: quote_key(topic, r))[:3]
        if not quotes:
            continue                      # 근거 원문이 없으면 카드로 만들지 않는다

        limits = []
        for label, row in (("댓글", c), ("영상 설명", v)):
            if row.get("hold_reason"):
                limits.append(f"{label}: {row['hold_reason']}")
            if row.get("single_source") == "true":
                limits.append(f"{label}: 단일 소스 판정 — 플랫폼 간 교차 확인 없음")
        if topic in gain and int(gain[topic]["gained"]) > 0:
            g = gain[topic]
            limits.append(
                f"자막 표본 45편에서 이 주제는 설명란만 볼 때 {g['videos_matched_baseline']}편, "
                f"자막까지 보면 {g['videos_matched_with_transcript']}편이다. "
                f"현재 판정은 설명란만 쓰므로 {g['gained']}편만큼 못 보고 있다")
        used = {q.get("matched_term") for q in quotes}
        generic = used & GENERIC_ALIAS.get(topic, set())
        if generic:
            limits.append(
                f"근거가 일반어 별칭({', '.join(sorted(generic))})으로 걸렸다. "
                f"이 주제의 구체 표현이 담긴 댓글이 부족하다는 뜻이므로 근거를 사람이 확인해야 한다")
        limits.append("최근 분기는 댓글이 계속 쌓이므로 구조적으로 과소 집계된다")

        cross = []
        if topic in xsrc:
            x = xsrc[topic]
            cross.append(f"커머스 리뷰 구성비 {x['commerce_review_pct']}% "
                         f"(영상 {x['youtube_video_pct']}% · 댓글 {x['youtube_comment_pct']}%)"
                         + (f" — {x['reading']}" if x.get("reading") else ""))
        if topic in comm:
            cross.append(f"커머스 플랫폼 설문 긍정률 {comm[topic]['positive_rate_mean']}% "
                         f"(평가 제품 {comm[topic]['products_rated']}개 — 근거 부족)")
        if topic in ingr:
            i = ingr[topic]
            cross.append(f"실제 제품 구성 {i['product_pct']}% ({i['products']}개 제품, "
                         f"순위 {i['product_rank']})")

        # 유형을 정한 근거가 곧 그 카드의 세기다. 전부 opportunity_score 로 줄을 세우면
        # 판정 보류라 점수가 낮은 표현 공백이 밀린다 — 그 카드는 제품 쪽 근거로 서는 것이다.
        strength = (num(ingr[topic]["product_over_comment"]) if kind == "표현 공백" and topic in ingr
                    else abs(num(c.get("gap_pp"))) if kind == "제품 공백 기회"
                    else num(c.get("opportunity_score") or v.get("opportunity_score")))
        cards.append({
            "topic_id": topic, "quarter": quarter, "card_type": kind, "type_basis": basis,
            "_strength": round(strength, 2),
            "opportunity_score": c.get("opportunity_score") or v.get("opportunity_score") or "",
            "comment_type": c.get("trend_type", ""), "video_type": v.get("trend_type", ""),
            "comment_composition_pct": round(num(c.get("composition")) * 100, 2),
            "video_composition_pct": round(num(v.get("composition")) * 100, 2),
            "gap_pp": num(c.get("gap_pp")),
            "velocity_yoy": c.get("velocity_yoy") or "",
            "evidence_strength": c.get("evidence_strength") or "",
            "document_count": c.get("document_count") or "",
            "cross_checks": cross, "limits": limits, "quotes": quotes,
            "decision": "", "decision_reason": "", "next_action": "",
        })

    # 유형이 겹치지 않게 고른다. 같은 유형 카드 3장은 데모에서 한 장과 같다.
    picked, seen = [], set()
    for c in sorted(cards, key=lambda c: -c["_strength"]):
        if c["card_type"] in seen:
            continue
        seen.add(c["card_type"])
        picked.append(c)
    for c in picked:
        c.pop("_strength", None)
    return picked


def render(cards: list[dict], quarter: str) -> str:
    out = [f"# R&D Opportunity Card — {quarter}", "",
           "유형은 규칙이 배정했고 모든 수치는 저장된 산출물에서 가져왔다. "
           "요약 문장은 LLM 을 쓰지 않고 근거 원문을 그대로 실었다.", ""]
    for i, c in enumerate(cards, 1):
        out += [f"## {i}. {c['topic_id']} — {c['card_type']}", "",
                f"**유형 배정 근거** {c['type_basis']}", "",
                "| | |", "|---|---|",
                f"| 기회 점수 | {c['opportunity_score']} |",
                f"| 판정 | 댓글 {c['comment_type'] or '—'} / 영상 {c['video_type'] or '—'} |",
                f"| 구성비 | 댓글 {c['comment_composition_pct']}% · 영상 {c['video_composition_pct']}% |",
                f"| 갭(댓글−영상) | {c['gap_pp']:+.2f}%p |",
                f"| velocity(YoY) | {c['velocity_yoy'] or '—'} |",
                f"| 근거 강도 / 문서 | {c['evidence_strength'] or '—'} / {c['document_count'] or '—'} |",
                ""]
        if c["cross_checks"]:
            out += ["**교차 검증**", ""] + [f"- {x}" for x in c["cross_checks"]] + [""]
        out += ["**소비자 발화 (좋아요 상위)**", ""]
        for q in c["quotes"]:
            body = " ".join((q.get("text") or "").split())[:220]
            out += [f"> {body}", "",
                    f"  좋아요 {q.get('like_count')} · 걸린 표현 `{q.get('matched_term')}` · "
                    f"[원문]({q.get('url')})", ""]
        out += ["**한계**", ""] + [f"- {x}" for x in c["limits"]] + [""]
        out += ["**검토** accept / watch / reject — 사유와 다음 작업을 여기에 적는다.", "", "---", ""]
    return "\n".join(out)


def demo() -> None:
    cmt = {"trend_type": "지속 인기", "gap_pp": "5.29", "composition": "0.1239"}
    vid = {"trend_type": "지속 인기"}
    assert classify("자극_눈시림", cmt, vid, None)[0] == "제품 공백 기회"
    ingr = {"product_over_comment": "27.1", "product_pct": "32.9", "product_rank": "2",
            "youtube_comment_pct": "1.21", "comment_rank": "3"}
    assert classify("혼합자차", {"trend_type": "판정 보류"}, {}, ingr)[0] == "표현 공백"
    # 표현 공백은 판정이 보류여도 잡혀야 한다 — 제품 쪽 근거로 서는 카드다
    assert classify("x", {"trend_type": "급상승", "gap_pp": "0.1"}, {}, None)[0] == "검증된 성장"
    assert classify("x", {"trend_type": "판정 보류"}, {"trend_type": "근거 부족"}, None) is None
    print("demo ok")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reports", type=Path, default=Path("reports"))
    p.add_argument("--quarter", default="2026Q2")
    p.add_argument("--out", type=Path, default=Path("reports/opportunity_cards.md"))
    p.add_argument("--json", type=Path, default=Path("reports/opportunity_cards.json"))
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()
    if a.demo:
        demo()
        return 0

    cards = build(a.quarter, a.reports)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(cards, a.quarter), encoding="utf-8")
    a.json.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"카드 {len(cards)}건")
    for c in cards:
        print(f"  {c['topic_id']:<14} {c['card_type']:<12} 점수 {c['opportunity_score'] or '—':>4}"
              f" · 근거 {len(c['quotes'])}건 · 교차 {len(c['cross_checks'])} · 한계 {len(c['limits'])}")
    print(f"\n{a.out}\n{a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
