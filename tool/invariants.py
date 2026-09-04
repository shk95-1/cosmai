"""Text-blind comparison of two revisions: `tool/checks/invariants <base> [paths...]` (#215).

The translation wave of #192 had to answer one question about every file it touched -- did anything
but the text change? -- and answered it with three throwaway scripts. This is those three, kept:

  python    the AST with docstrings dropped, every string constant blanked, and each f-string
            collapsed to the SET of expressions it interpolates (a translation reorders the values
            inside one message, so the order is text while the values are code)
  sql       the statements with their comments stripped and their whitespace normalized
  shell/js  the diff itself: every added and removed line must be a comment line
  markdown  the anchors and literals a translation has to carry across (`§2`, `#214`, code spans)

Every rule fails closed. A file that is new, gone, binary, unparsable or of a type no rule covers is
reported as differing, because "nothing changed" and "I could not tell" must not share an exit status.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
import sys

JS_SUFFIXES = (".js", ".mjs", ".cjs")

# What a translation must carry across a Markdown file unchanged: section anchors, issue numbers and
# anything in backticks (a path, a command, a column name).
MARKDOWN_LITERAL = re.compile(r"§\s?[0-9A-Za-z.\-]+|#\d+|`[^`\n]+`")

COMMENT_PREFIXES = {"shell": ("#",), "js": ("//", "/*", "*", "*/")}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def blob(rev: str, path: str) -> str | None:
    """The file's content at a revision; None when it is absent there or is not text."""
    done = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True)
    if done.returncode != 0:
        return None
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def kind_of(path: str, text: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith(".py"):
        return "python"
    if name.endswith(".sql"):
        return "sql"
    if name.endswith(".md"):
        return "markdown"
    if name.endswith(JS_SUFFIXES):
        return "js"
    if name.endswith(".sh"):
        return "shell"
    if "." not in name:
        # tool/ and .githooks/ hold python and shell as extension-less executables, so the shebang is
        # the only thing that says which is which.
        first = text.split("\n", 1)[0]
        if first.startswith("#!") and "python" in first:
            return "python"
        if first.startswith("#!") and "sh" in first:
            return "shell"
    return ""


class Blind(ast.NodeTransformer):
    """Every string constant becomes the same constant, so only the code around them is compared."""

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        self.generic_visit(node)
        parts = sorted(ast.dump(v.value) for v in node.values if isinstance(v, ast.FormattedValue))
        return ast.Constant(value="<fstr:" + "|".join(parts) + ">")

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.Constant(value="<str>") if isinstance(node.value, str) else node


def strip_docstrings(tree: ast.AST) -> None:
    """Blanking a docstring is not enough: a module that GAINED one would still read as changed code."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                node.body = node.body[1:] or [ast.Pass()]


def python_fingerprint(text: str) -> str | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    strip_docstrings(tree)
    return ast.dump(Blind().visit(tree), include_attributes=False)


def sql_fingerprint(text: str) -> str:
    kept: list[str] = []
    i, end = 0, len(text)
    while i < end:
        if text[i] == "'":
            # A quoted literal is data: a `--` inside it is part of the value, not a comment.
            j = i + 1
            while j < end:
                if text[j] == "'":
                    if text[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            kept.append(text[i:j])
            i = j
        elif text.startswith("--", i):
            newline = text.find("\n", i)
            i = end if newline < 0 else newline
        elif text.startswith("/*", i):
            close = text.find("*/", i + 2)
            i = end if close < 0 else close + 2
        else:
            kept.append(text[i])
            i += 1
    return hashlib.md5(" ".join("".join(kept).split()).encode("utf-8")).hexdigest()


def markdown_literals(text: str) -> list[str]:
    return sorted(match.group(0).replace(" ", "") for match in MARKDOWN_LITERAL.finditer(text))


def changed_lines(base: str, head: str, path: str) -> list[str]:
    diff = git("diff", "-U0", "--no-color", base, head, "--", path)
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line[:1] in ("+", "-"):
            out.append(line[1:].strip())
    return out


def comment_only(base: str, head: str, path: str, kind: str) -> bool:
    prefixes = COMMENT_PREFIXES[kind]
    return all(not line or line.startswith(prefixes) for line in changed_lines(base, head, path))


def differs(base: str, head: str, path: str) -> str | None:
    """The reason this file is not provably unchanged, or None when it is."""
    before = blob(base, path)
    after = blob(head, path)
    if before is None:
        return "added, binary or unreadable at the base: there is nothing to compare it against"
    if after is None:
        return "removed, binary or unreadable at the head"
    kind = kind_of(path, before)
    if not kind:
        return "no invariant covers this file type"
    if kind == "python":
        one, two = python_fingerprint(before), python_fingerprint(after)
        if one is None or two is None:
            return "python that does not parse"
        return None if one == two else "the code differs (AST)"
    if kind == "sql":
        return None if sql_fingerprint(before) == sql_fingerprint(after) else "the statements differ"
    if kind == "markdown":
        lost = sorted(set(markdown_literals(before)) - set(markdown_literals(after)))
        gained = sorted(set(markdown_literals(after)) - set(markdown_literals(before)))
        if lost or gained:
            return f"anchors and literals changed (lost {lost}, gained {gained})"
        return None
    return None if comment_only(base, head, path, kind) else "a line that is not a comment changed"


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: invariants.py <base> [paths...]", file=sys.stderr)
        return 2
    base, paths = argv[0], argv[1:]
    head = "HEAD"
    # The classifier asks about `<base>...HEAD`, so the comparison starts where the branch did:
    # against the tip, every commit main gained meanwhile would read as this branch's change.
    merge_base = subprocess.run(
        ["git", "merge-base", base, head], capture_output=True, text=True, check=False
    ).stdout.strip()
    start = merge_base or base
    files = [f for f in git("diff", "--name-only", start, head, "--", *paths).split("\n") if f]
    bad = 0
    for path in files:
        reason = differs(start, head, path)
        if reason:
            print(f"{path}: {reason}")
            bad += 1
    print(f"invariants: {len(files)} file(s) compared against {base}, {bad} differ")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
