"""`cosmai eval <task>`: 구현체가 없으면 멈추고, 있으면 점수 표와 analysis_run 한 행을 남긴다."""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Sequence

import pytest

from analysis.registry import LabeledRow, register, unregister
from cosmai.cli import main
from db import seed
from db.seed._common import connect

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def harness_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 파일은 하네스를 잰다 — 실제 구현체가 import 되면 아래 오라클을 덮어쓴다."""
    monkeypatch.setattr("analysis.registry.IMPLEMENTATIONS", ())


@pytest.fixture(autouse=True)
def _restore_default_registrations() -> Iterator[None]:
    """이 파일의 여러 테스트가 쓰는 unregister() 는 기본 등록을 되돌려 주지 않는다 — 되돌리지
    않으면 뒤에 도는 파일(test_polarity.py)이 '등록 없음'(exit 2)을 만난다 (#30).

    harness_only 가 IMPLEMENTATIONS 를 비운 것이 아직 살아 있는 시점이라(monkeypatch 되돌리기가
    나중이다) load_implementations() 가 아니라 모듈을 직접 짚는다."""
    yield
    for module in ("analysis.predictors", "analysis.linker.evaluators"):
        importlib.import_module(module).register_implementations()


@pytest.fixture
def labeled(needs_runtime_url: str) -> str:
    seed.run_all(needs_runtime_url, only=("labeled",))
    return needs_runtime_url


@pytest.fixture
def oracle() -> Iterator[None]:
    """골드를 그대로 돌려주는 더미 구현체 — 하네스가 재는 것이 배선이지 규칙이 아님을 분명히 한다."""

    def predict(rows: Sequence[LabeledRow]) -> Sequence[str]:
        return [row.gold for row in rows]

    def accept_exact(rows: Sequence[LabeledRow]) -> Sequence[str]:
        # product_match 의 예측은 라벨이 아니라 채택/비채택이다 — gold 를 그대로 낼 수 없다.
        return ["Y" if row.gold == "Y" else "N" for row in rows]

    for task in ("polarity", "wish_class", "brand_link"):
        register(task, "oracle-v0", predict)
    register("product_match", "oracle-v0", accept_exact)
    yield
    for task in ("polarity", "wish_class", "brand_link", "product_match"):
        unregister(task)


def test_an_unregistered_task_stops_with_exit_code_2(labeled: str, capsys):
    unregister("polarity")  # 앞선 테스트가 남긴 등록은 이 테스트의 전제가 아니다
    assert main(["eval", "polarity", "--url", labeled]) == 2
    assert "polarity" in capsys.readouterr().out


def test_every_eval_set_of_the_task_is_scored_and_one_run_row_is_written(labeled, oracle, capsys):
    assert main(["eval", "polarity", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "sun holdout 100" in out and "p1 blind40" in out
    assert "n=100" in out and "n=40" in out
    with connect(labeled) as conn, conn.cursor() as cur:
        cur.execute("SELECT note, versions FROM analysis_run")
        rows = cur.fetchall()
    assert len(rows) == 1
    note, versions = rows[0]
    assert note == "eval:polarity:oracle-v0"
    assert versions["polarity"] == "oracle-v0"
    assert versions["scores"]["sun holdout 100"]["acc"] == 1.0


def test_product_match_reports_strict_and_variant_tolerant_side_by_side(labeled, oracle, capsys):
    # 오라클은 gold='Y' 인 30쌍만 채택한다 — 분모 30, 분자도 30 이라 양쪽 다 1.000.
    assert main(["eval", "product_match", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "strict 1.000" in out and "변형허용 1.000" in out
    assert "n=40" in out


def test_the_v2_rule_reproduces_the_contract_baseline_from_its_own_accepted_pairs(labeled, capsys):
    """기준선 .77/.95 = v2 규칙이 채택한 39쌍(in_final=1)의 정밀도. 재현 안 되면 기준선이 아니다."""

    def in_final(rows):
        return ["Y" if row.extra.get("in_final") == "1" else "N" for row in rows]

    register("product_match", "slice-p2-v2", in_final)
    try:
        assert main(["eval", "product_match", "--url", labeled]) == 0
    finally:
        unregister("product_match")
    with connect(labeled) as conn, conn.cursor() as cur:
        cur.execute("SELECT versions FROM analysis_run")
        row = cur.fetchone()
    assert row is not None
    scored = row[0]["scores"]["P2 blind 40"]
    assert (round(scored["strict"], 3), round(scored["변형허용"], 3)) == (0.769, 0.949)
    assert (round(scored["strict"], 2), round(scored["변형허용"], 2)) == (0.77, 0.95)
    assert "strict 0.769" in capsys.readouterr().out


def test_a_label_outside_y_v_n_is_refused_instead_of_folded(labeled, capsys):
    register("product_match", "sloppy", lambda rows: ["maybe"] * len(rows))
    try:
        assert main(["eval", "product_match", "--url", labeled]) == 2
    finally:
        unregister("product_match")
    assert "maybe" in capsys.readouterr().out


def test_brand_link_folds_the_reason_carrying_gold_into_ok_and_fp(labeled, oracle, capsys):
    assert main(["eval", "brand_link", "--url", labeled]) == 0
    out = capsys.readouterr().out
    assert "n=120" in out
    assert "OK" in out and "OK(retailer)" not in out


def test_a_brand_link_label_that_is_neither_ok_nor_fp_is_refused(labeled, capsys):
    register("brand_link", "sloppy", lambda rows: ["unsure"] * len(rows))
    try:
        assert main(["eval", "brand_link", "--url", labeled]) == 2
    finally:
        unregister("brand_link")
    assert "unsure" in capsys.readouterr().out


def test_the_wish_baseline_set_is_the_blind_60_and_not_the_whole_holdout(labeled, oracle, capsys):
    assert main(["eval", "wish_class", "--url", labeled]) == 0
    assert "n=60" in capsys.readouterr().out


def test_check_baseline_passes_for_the_oracle_and_fails_for_a_constant(labeled, oracle):
    assert main(["eval", "wish_class", "--url", labeled, "--check-baseline"]) == 0
    register("wish_class", "always-n", lambda rows: ["n"] * len(rows))
    assert main(["eval", "wish_class", "--url", labeled, "--check-baseline"]) == 1


def test_a_prediction_of_the_wrong_length_is_refused(labeled, capsys):
    register("polarity", "short", lambda rows: ["중립"])
    try:
        assert main(["eval", "polarity", "--url", labeled]) == 2
    finally:
        unregister("polarity")
    assert "returned 1 prediction(s) for 100 row(s)" in capsys.readouterr().out


def test_url_flag_also_points_the_lexicon_connection_at_that_same_db(labeled, oracle, monkeypatch):
    """`--url` 은 labeled_set/analysis_run 만이 아니라 사전 커넥션도 같은 DB 로 보내야 한다(#12 회귀)."""
    from analysis import predictors

    monkeypatch.setattr(
        "db.runtime.runtime_url",
        lambda: (_ for _ in ()).throw(AssertionError("must not touch prod runtime_url")),
    )
    assert main(["eval", "polarity", "--url", labeled]) == 0
    assert predictors.LEXICON_URL == labeled


def test_without_url_flag_lexicon_url_stays_none_for_the_runtime_fallback():
    """--url 없는 호출은 사전 접속을 건드리지 않아야 한다 -- 그래야 운영 폴백이 남아 있다."""
    from analysis import predictors

    unregister("polarity")  # 등록이 없으면 _connect/runtime_url 에 닿기 전에 exit 2 로 멈춘다
    assert main(["eval", "polarity"]) == 2
    assert predictors.LEXICON_URL is None


def test_the_registry_loads_its_implementation_modules_from_the_list(monkeypatch):
    """유닛은 IMPLEMENTATIONS 에 한 줄만 더한다 — cli.py 에 import 를 끼우면 넷이 같은 줄에서 충돌한다."""
    import analysis.registry as reg

    reg.load_implementations()  # 등록 모듈이 없어도(빈 튜플) 있어도 조용히 성공해야 한다
    monkeypatch.setattr(reg, "IMPLEMENTATIONS", ("tests.fake_implementation",))
    try:
        reg.load_implementations()
        loaded = reg.get("polarity")
        assert loaded is not None and loaded.version == "fake-v0"
    finally:
        unregister("polarity")
