"""contracts/interfaces.md 기준선 표의 코드본. 표와 어긋나면 tests/test_baselines.py 가 잡는다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    metric: str  # evaluate 가 내는 이름: acc | P:<라벨> | R:<라벨> | strict | 변형허용
    threshold: float


@dataclass(frozen=True)
class EvalSet:
    """기준선 표의 한 행 = labeled_set 에서 골라낼 한 덩어리."""

    task: str
    name: str
    split: str
    checks: tuple[Check, ...]
    ref_prefix: str = ""
    extra_key: str = ""  # labeled_set.extra 의 키 — ref 접두로 가릴 수 없는 셋을 고른다
    extra_value: str = ""


# interfaces.md §평가 하네스가 대조하는 기준선 — 행 순서까지 표와 같다.
BASELINES: tuple[EvalSet, ...] = (
    EvalSet(
        task="polarity",
        name="sun holdout 100",
        split="holdout",
        checks=(Check("acc", 0.77), Check("P:불만", 0.89)),
        ref_prefix="sun:",
    ),
    EvalSet(
        task="polarity",
        name="p1 blind40",
        split="holdout",
        checks=(Check("acc", 0.47), Check("P:불만", 0.67)),
        ref_prefix="p1:",
    ),
    # wish 의 ref 는 comment_id 단독이라(formats.md) holdout60 과 blind60_v2 를 접두로 가를 수 없다 —
    # 시드가 labeled_set.extra.set 에 셋 이름을 넣는다.
    EvalSet(
        task="wish_class",
        name="P9 blind60_v2",
        split="holdout",
        checks=(Check("P:a", 0.90),),
        extra_key="set",
        extra_value="blind60_v2",
    ),
    # brand_link 는 두 표본 120행 전체가 그대로 한 셋이라 고를 것이 없다.
    EvalSet(task="brand_link", name="P3 120", split="holdout", checks=(Check("P:OK", 0.97),)),
    EvalSet(
        task="product_match",
        name="P2 blind 40",
        split="holdout",
        checks=(Check("strict", 0.769),),
        ref_prefix="v2:",
    ),
)


# 기준선이 없는 관측용 셋. 표는 홀드아웃 두 줄뿐이지만 1차 패스는 튠 셋 점수도 봐야 하고(같은 규칙이
# 어디에 맞춰졌는지), wish 는 블라인드가 아닌 두 셋과의 차가 곧 과적합의 크기다. 기준선 표의 사본인
# BASELINES 는 건드리지 않는다 — tests/test_baselines.py 가 표와 행 순서까지 대조한다.
OBSERVED: tuple[EvalSet, ...] = (
    EvalSet(task="polarity", name="sun tune 200", split="tune", checks=(), ref_prefix="sun:"),
    EvalSet(task="polarity", name="p1 crosscat 60", split="tune", checks=(), ref_prefix="p1:"),
    EvalSet(
        task="wish_class",
        name="P9 tune100",
        split="tune",
        checks=(),
        extra_key="set",
        extra_value="tune100",
    ),
    EvalSet(
        task="wish_class",
        name="P9 holdout60",
        split="holdout",
        checks=(),
        extra_key="set",
        extra_value="holdout60",
    ),
)


# interfaces.md §규칙 실측 — 채택 조건은 계약이 요구하는 바닥이고, 구현 교체는 규칙이 실제로 낸 이 숫자를
# 넘어야 한다. 두 표가 갈라지면 tests/test_baselines.py 가 잡는다.
RULE_MEASURED: Mapping[str, Mapping[str, Mapping[str, float]]] = {
    "polarity": {
        "sun holdout 100": {"acc": 0.870, "P:불만": 0.915},
        "p1 blind40": {"acc": 0.475, "P:불만": 0.667},
    }
}


def meets(metric: float, threshold: float) -> bool:
    """지표가 임계값을 넘었는가. 게이트의 비교는 여기 한 곳이다 — 두 표가 같은 자를 쓰게 하려고 모았다."""
    return metric >= threshold


def adoption_misses(task: str, scores: Mapping[str, Mapping[str, float]]) -> tuple[str, ...]:
    """규칙에 못 미친 칸. 빈 튜플이어야 교체다 — 셋이 통째로 빠진 실행은 판정이 아니라 오류다."""
    wanted = RULE_MEASURED.get(task, {})
    absent = [name for name in wanted if name not in scores]
    if absent:
        raise LookupError(f"{task}: no adoption verdict without {', '.join(absent)} — run --split holdout")
    return tuple(
        f"{name}: {metric} {scores[name].get(metric, 0.0):.3f} < rule {want:.3f}"
        for name, wants in wanted.items()
        for metric, want in wants.items()
        if not meets(scores[name].get(metric, 0.0), want)
    )


def for_task(task: str) -> tuple[EvalSet, ...]:
    """기준선 셋이 먼저다 — 채택 판정이 관측용 셋에 밀려 뒤에서 읽히지 않도록."""
    return tuple(b for b in BASELINES + OBSERVED if b.task == task)
