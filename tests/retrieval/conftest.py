"""Where the topic dictionary is set up for the tests in this directory (#8).

The source of topic expansion is the active version of `needs.aspect_lexicon`. The units that run without a
DB (tokenization, the query list) set the repo's load-source CSV up in the process as it is, and the tests
that use a DB put the same CSV into the schema through the very conversion `cosmai lexicon load` uses
(`cosmai.cli._csv_rows`) -- the moment a fixture writes the dictionary out by hand there are two
dictionaries, and the equivalence proof proves its own copy.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

import psycopg
import pytest

from analysis.retrieval import stopwords, topics


def csv_rows(path=topics.DICTIONARY_CSV) -> list[tuple[object, ...]]:
    """One CSV into the rows `db.lexicon.insert_aspects` takes. It goes through the same function as the
    CLI."""
    from cosmai.cli import _csv_rows

    return _csv_rows("aspect", str(path))


def csv_topics(version: int | None = 1) -> topics.Topics:
    from db.lexicon import ASPECT_COLUMNS

    aspect, pattern, extra = (ASPECT_COLUMNS.index(c) for c in ("aspect", "pattern", "extra"))
    rows = [(str(r[aspect]), str(r[pattern]), cast(Mapping[str, Any], r[extra])) for r in csv_rows()]
    return topics.from_rows(rows, version)


def install_topics(conn: psycopg.Connection, version: int = 1, active: bool = True) -> None:
    """What `cosmai lexicon load --kind aspect` + `activate` does in production."""
    from db.lexicon import insert_aspects

    with conn.cursor() as cur:
        insert_aspects(cur, csv_rows(), version, active=active)
    conn.commit()


def stopword_rows(path=None) -> list[tuple[object, ...]]:
    """One query-stopword CSV into the rows `db.lexicon.insert_entities` takes. It goes through the same
    function as the CLI, for the same reason as the topic dictionary -- a fixture writing the list out by
    hand makes two dictionaries."""
    from cosmai.cli import _csv_rows

    return _csv_rows(stopwords.KIND, str(path or stopwords.DICTIONARY_CSV))


def csv_stopwords(version: int = 1) -> stopwords.QueryStopwords:
    surface = 2  # db.lexicon.ENTITY_COLUMNS 의 자리
    return stopwords.from_rows([(str(row[surface]), version) for row in stopword_rows()])


def install_stopwords(conn: psycopg.Connection, version: int = 1, active: bool = True) -> None:
    """What `cosmai lexicon load --kind stopword` + `activate` does in production."""
    from db.lexicon import insert_entities

    with conn.cursor() as cur:
        insert_entities(cur, stopword_rows(), version, active=active)
    conn.commit()


@pytest.fixture(autouse=True)
def repo_dictionary() -> Iterator[topics.Topics]:
    """The active dictionary for tests that use no DB. An entrance that uses a DB sets it up again in its own
    schema."""
    installed = topics.use(csv_topics())
    yield installed
    topics.forget()
