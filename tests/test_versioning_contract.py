"""The executable subset of contracts/versioning.md (#170).

The Markdown block is the inventory. These tests ask it back from the fully composed needs schema and
from the focused writer sites, instead of keeping a second table of expected columns in Python.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
VERSIONING = ROOT / "contracts" / "versioning.md"
JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.DOTALL)
RULE_VERSION = re.compile(r"^rule-v\d+\.\d+$")
LLM_VERSION = re.compile(r"^llm-.+-\d{8}$")
SLICE_VERSION = re.compile(r"^slice-[a-z0-9_-]+$")
KNOWN_RUN_FORMATS = {
    "run-version-list-or-null",
    "positive-integer-or-object",
    "exact",
    "rule-vX.Y",
    "positive-integer",
    "iso-8601",
    "positive-integer-or-null",
}
POLARITY_VERSION_REFERENCE = re.compile(
    r"\bversions\s*(?:(?:->>|->)\s*'polarity'|(?:#>>|#>)\s*'\{polarity\}'|"
    r"\[\s*'polarity'\s*\]|\.\s*polarity)(?!\w)",
    re.IGNORECASE,
)


def _manifest() -> dict[str, Any]:
    blocks = JSON_BLOCK.findall(VERSIONING.read_text(encoding="utf-8"))
    assert len(blocks) == 1, "versioning.md must carry exactly one executable JSON block"
    return json.loads(blocks[0])


def _version_columns(url: str, schema: str) -> dict[str, dict[str, Any]]:
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT t.relname AS table_name, a.attname AS column_name,
                       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
                       EXISTS (
                         SELECT 1
                         FROM pg_catalog.pg_index i
                         WHERE i.indrelid = t.oid AND i.indisunique AND a.attnum = ANY(i.indkey)
                       ) AS natural_key
                FROM pg_catalog.pg_class t
                JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_catalog.pg_attribute a ON a.attrelid = t.oid
                WHERE n.nspname = :schema AND t.relkind IN ('r', 'p')
                  AND a.attnum > 0 AND NOT a.attisdropped
                  AND (a.attname = 'version' OR a.attname LIKE '%\\_version' ESCAPE '\\')
                ORDER BY t.relname, a.attnum
                """
            ),
            {"schema": schema},
        ).mappings()
        found = {
            f"needs.{row['table_name']}.{row['column_name']}": {
                "type": row["data_type"],
                "natural_key": row["natural_key"],
            }
            for row in rows
        }
    engine.dispose()
    return found


def test_the_composed_schema_has_exactly_the_declared_semantic_version_columns(
    needs_runtime_url: str, _schema_name: str
):
    manifest = _manifest()
    found = _version_columns(needs_runtime_url, _schema_name)
    excluded = set(manifest["excluded_operational_columns"])
    assert {key: value for key, value in found.items() if key not in excluded} == manifest["version_columns"]


def test_the_operational_ledger_exclusion_names_the_table_migrate_creates():
    manifest = _manifest()
    assert manifest["excluded_operational_columns"] == ["needs.schema_migration.version"]
    migrate = (ROOT / "db" / "migrate.sh").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS needs.schema_migration" in migrate
    assert re.search(
        r"CREATE TABLE IF NOT EXISTS needs\.schema_migration \([^)]*\bversion\s+text", migrate, re.DOTALL
    )


def test_a19_metric_rows_keep_definition_versions_on_the_run(needs_runtime_url: str, _schema_name: str):
    manifest = _manifest()
    engine = create_engine(needs_runtime_url)
    with engine.connect() as conn:
        for qualified, allowed in manifest["a19_metrics"].items():
            _, table = qualified.split(".", 1)
            columns = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :table"
                    ),
                    {"schema": _schema_name, "table": table},
                )
            }
            assert "run_id" in columns
            version_columns = {name for name in columns if name == "version" or name.endswith("_version")}
            assert version_columns == set(allowed)
    engine.dispose()


def _literal_dict_keys(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _constant(path: str) -> object:
    module_name, _, attribute = path.rpartition(".")
    return getattr(importlib.import_module(module_name), attribute)


def _valid_constant(value: object, spec: dict[str, Any]) -> bool:
    if not isinstance(value, str):
        return False
    format_name = spec["format"]
    if format_name == "exact":
        return value == spec["value"]
    if format_name == "rule-vX.Y":
        return bool(RULE_VERSION.fullmatch(value))
    if format_name == "run-version-list-or-null":
        return all(
            RULE_VERSION.fullmatch(part) or LLM_VERSION.fullmatch(part) or SLICE_VERSION.fullmatch(part)
            for part in value.split(";")
        )
    raise AssertionError(f"constant {spec['constant']} uses an unsupported format {format_name!r}")


def test_declared_run_keys_exist_at_their_focused_writer_sites_and_constants_match():
    for key, spec in _manifest()["analysis_run_version_keys"].items():
        assert spec["format"] in KNOWN_RUN_FORMATS, f"versions.{key} has an unknown format"
        if writer := spec.get("writer"):
            assert key in _literal_dict_keys(ROOT / writer), f"{writer} does not write versions.{key}"
        if constant := spec.get("constant"):
            assert _valid_constant(_constant(constant), spec), f"{constant} violates {spec['format']}"


def _sql_without_comments(sql: str) -> str:
    return re.sub(r"/\*.*?\*/", "", re.sub(r"--[^\n]*", "", sql), flags=re.DOTALL)


def test_the_lineage_guard_detects_the_forbidden_run_polarity_population_predicate():
    assert POLARITY_VERSION_REFERENCE.search("WHERE m.polarity_version = r.versions->>'polarity'")
    assert POLARITY_VERSION_REFERENCE.search("WHERE versions.polarity = m.polarity_version")
    assert POLARITY_VERSION_REFERENCE.search("WHERE r.versions #>> '{polarity}' = m.polarity_version")
    assert POLARITY_VERSION_REFERENCE.search("WHERE r.versions['polarity'] = to_jsonb(m.polarity_version)")


def test_no_lineage_view_filters_on_the_run_polarity_version():
    offenders = [
        path.relative_to(ROOT).as_posix()
        for path in sorted((ROOT / "db" / "views").glob("*lineage*.sql"))
        if POLARITY_VERSION_REFERENCE.search(_sql_without_comments(path.read_text(encoding="utf-8")))
    ]
    assert not offenders, f"run polarity is not a lineage population predicate: {offenders}"
