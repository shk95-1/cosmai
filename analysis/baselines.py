"""contracts/interfaces.md 기준선 표의 코드본. 표와 어긋나면 tests/test_baselines.py 가 잡는다."""

from __future__ import annotations

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


def for_task(task: str) -> tuple[EvalSet, ...]:
    return tuple(b for b in BASELINES if b.task == task)
