"""Does `cosmai retrieval ...` stand without the retrieval and embed extras.

이미지에는 extra 가 없다(pyproject `[project.optional-dependencies]`). 그런데 tool/checks/test:11
은 `--extra retrieval` 을 깔고 도움말 스냅샷을 뜨므로(tests/test_cli_help.py), 모듈 최상단으로
올라온 kiwipiepy·numpy import 를 이 스위트는 못 본다 -- 이미지 안에서는 같은 명령이 "연결 거절
exit 2" 가 아니라 ModuleNotFoundError 로 죽는다(#18 M14).

가볍게: 서브프로세스 **하나**가 그 모듈들을 막고, 검색 유닛 모듈을 import 한 뒤 하위명령 다섯의
--help 를 한 프로세스에서 돈다. 도움말만으로는 부족하다 -- cli 는 `analysis.retrieval` 을 함수
안에서 부르므로 --help 는 그 모듈들에 닿지도 않는다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The two retrieval extras and the one embed extra, plus the two heavy ones sentence-transformers drags in.
BLOCKED = ("kiwipiepy", "numpy", "sentence_transformers", "torch", "huggingface_hub")
# Every module `cosmai retrieval <anything>` really pulls in (the `_run_retrieval` family in cosmai/cli.py).
MODULES = (
    "analysis.retrieval.bm25",
    "analysis.retrieval.corpus",
    "analysis.retrieval.pipeline",
    "analysis.retrieval.eval",
    "analysis.retrieval.embed",
    "analysis.retrieval.vectors",
    "analysis.retrieval.ask",
)
COMMANDS = (
    "retrieval",
    "retrieval chunk",
    "retrieval search",
    "retrieval ask",
    "retrieval eval",
    "retrieval embed",
)

# The block is put on with a finder that raises ModuleNotFoundError at import time -- planting an empty fake
# module would make it "present but empty", which tests a different situation from the image's "absent".
PROGRAM = """
import contextlib, importlib, io, sys

blocked = set(sys.argv[1].split(","))


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in blocked:
            raise ModuleNotFoundError(f"blocked by the test: {name}", name=name)
        return None


sys.meta_path.insert(0, Blocker())
try:
    import kiwipiepy
except ModuleNotFoundError:
    pass
else:
    raise SystemExit("차단이 안 걸렸다 -- 이 테스트는 아무것도 증명하지 못한다")

for name in sys.argv[2].split(","):
    importlib.import_module(name)

from cosmai.cli import main

for argv in [command.split() for command in sys.argv[3:]]:
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        try:
            main([*argv, "--help"])
            code = 0
        except SystemExit as stop:
            code = stop.code or 0
    if code != 0 or "usage" not in captured.getvalue():
        raise SystemExit(f"{argv}: exit {code}")
print("OK")
"""


def test_the_retrieval_unit_imports_and_helps_with_no_extra_installed():
    out = subprocess.run(
        [sys.executable, "-c", PROGRAM, ",".join(BLOCKED), ",".join(MODULES), *COMMANDS],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        cwd=ROOT,
        check=False,
    )
    assert out.returncode == 0, out.stderr or out.stdout
    assert out.stdout.strip() == "OK"
