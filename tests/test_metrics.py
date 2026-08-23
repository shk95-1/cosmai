"""점수 계산의 고정 벡터. 손으로 셀 수 있는 크기라 값이 바뀌면 규칙이 바뀐 것이다."""

from __future__ import annotations

from analysis.metrics import Scores, precision_over, score

PAIRS = [("불만", "불만"), ("불만", "만족"), ("만족", "만족"), ("중립", "중립"), ("중립", "불만")]
# (gold, 예측). 채택 = 예측 'Y' 두 행뿐이고 분모는 그 둘이다.
MATCHES = [("Y", "Y"), ("V", "Y"), ("N", "N"), ("Y", "N")]


def test_accuracy_and_per_class_precision_recall():
    s = score(PAIRS)
    assert (s.n, s.accuracy) == (5, 0.6)
    by_label = {c.label: c for c in s.classes}
    assert [c.label for c in s.classes] == ["만족", "불만", "중립"]
    assert (by_label["불만"].support, by_label["불만"].predicted, by_label["불만"].hit) == (2, 2, 1)
    assert (by_label["불만"].precision, by_label["불만"].recall) == (0.5, 0.5)
    assert (by_label["만족"].precision, by_label["만족"].recall) == (0.5, 1.0)
    assert (by_label["중립"].precision, by_label["중립"].recall) == (1.0, 0.5)


def test_a_label_nobody_predicted_scores_zero_instead_of_dividing_by_zero():
    s = score([("a", "b"), ("a", "b")])
    by_label = {c.label: c for c in s.classes}
    assert (by_label["a"].precision, by_label["a"].recall) == (0.0, 0.0)
    assert (by_label["b"].precision, by_label["b"].recall) == (0.0, 0.0)
    assert score([]) == Scores(n=0, accuracy=0.0, classes=())


def test_precision_counts_only_the_rows_the_implementation_accepted():
    assert precision_over(MATCHES, {"Y"}, {"Y"}) == 0.5
    assert precision_over(MATCHES, {"Y"}, {"Y", "V"}) == 1.0
    # 아무것도 채택하지 않으면 분모가 0 이다 — 0 으로 나누지 않고 0.0 을 낸다.
    assert precision_over(MATCHES, {"Z"}, {"Y"}) == 0.0
    assert precision_over([], {"Y"}, {"Y"}) == 0.0
