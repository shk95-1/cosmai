"""origin: written 2026-08-23 for playbook/metrics.md; reuse: python3 measure_prose_ratio.py <repo> ... (stdlib only; prints prose/code, md/code, commit shape, hooks).

Usage: python measure.py <repo> [<repo> ...]
Counts only tracked-ish source: .py under src/, apps/, tests/, tool/, experiments/ etc.,
skipping .venv, node_modules, __pycache__, .git.
"""
import ast, io, sys, tokenize, subprocess, re
from pathlib import Path

SKIP = {".venv", "node_modules", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".superpowers", ".worktrees", "var", "data", "out"}

def classify(path: Path):
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    n = len(lines)
    doc_lines, comment_lines, blank = set(), set(), set()
    for i, l in enumerate(lines, 1):
        if not l.strip():
            blank.add(i)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return n, 0, 0, len(blank)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
                d = node.body[0]
                for ln in range(d.lineno, d.end_lineno + 1):
                    doc_lines.add(ln)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                # full-line comment only (trailing comments count as code lines)
                if lines[tok.start[0]-1].strip().startswith("#"):
                    comment_lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    prose = doc_lines | comment_lines
    code = n - len(prose) - len(blank)
    return n, len(doc_lines), len(comment_lines), len(blank)

def walk(root: Path):
    for p in root.rglob("*.py"):
        if SKIP.intersection(p.parts):
            continue
        yield p

def md_stats(root: Path):
    files = [p for p in root.rglob("*.md") if not SKIP.intersection(p.parts)]
    return len(files), sum(len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in files)

def git(root, *args):
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""

CONV = re.compile(r"^(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([^)]*\))?!?: ")

for arg in sys.argv[1:]:
    root = Path(arg)
    tot = dict(n=0, doc=0, com=0, blank=0, files=0)
    per_bucket = {}
    for p in walk(root):
        n, d, c, b = classify(p)
        bucket = p.relative_to(root).parts[0]
        bk = per_bucket.setdefault(bucket, dict(n=0, doc=0, com=0, blank=0, files=0))
        for k, v in (("n", n), ("doc", d), ("com", c), ("blank", b), ("files", 1)):
            tot[k] += v; bk[k] += v
    code = tot["n"] - tot["doc"] - tot["com"] - tot["blank"]
    prose = tot["doc"] + tot["com"]
    mdn, mdl = md_stats(root)
    subjects = [s for s in git(root, "log", "--format=%s").splitlines() if s]
    bodies = git(root, "log", "--format=%b")
    claude = bodies.count("Co-Authored-By: Claude")
    conv = sum(1 for s in subjects if CONV.match(s) or s.startswith(("Merge ", "Revert ")))
    lens = sorted(len(s) for s in subjects) or [0]
    hooks = list((root / ".githooks").glob("*")) if (root / ".githooks").exists() else []
    print(f"== {root.name}")
    print(f"  py files={tot['files']} total={tot['n']} code={code} docstring={tot['doc']} comment={tot['com']} blank={tot['blank']}  prose/code={prose/max(code,1):.2f}  docstring/code={tot['doc']/max(code,1):.2f}")
    for b, bk in sorted(per_bucket.items(), key=lambda kv: -kv[1]["n"])[:6]:
        c2 = bk["n"] - bk["doc"] - bk["com"] - bk["blank"]
        print(f"    {b:<14} files={bk['files']:>4} code={c2:>6} doc={bk['doc']:>6} com={bk['com']:>6} prose/code={(bk['doc']+bk['com'])/max(c2,1):.2f}")
    print(f"  md files={mdn} md lines={mdl}  md_lines/code={mdl/max(code,1):.2f}")
    print(f"  commits={len(subjects)} claude_coauthored={claude} conventional={conv} subject_len median={lens[len(lens)//2]} max={lens[-1]} over72={sum(1 for l in lens if l>72)}")
    print(f"  hooks={[h.name for h in hooks]}")
