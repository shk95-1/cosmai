"""주제 사전을 이 디렉터리의 테스트에 세우는 자리 (#8).

주제 확장의 원천은 `needs.aspect_lexicon` 의 활성 버전이다. DB 없이 도는 유닛(토큰화·질의 목록)은
레포의 적재 원본 CSV 를 그대로 프로세스에 세워 두고, DB 를 쓰는 테스트는 `cosmai lexicon load` 가
쓰는 바로 그 변환(`cosmai.cli._csv_rows`)으로 같은 CSV 를 스키마에 넣는다 -- 픽스처가 사전을 손으로
다시 적으면 그 순간 사전이 두 벌이 되고, 동등성 증명이 자기 사본을 증명하게 된다.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, cast

import psycopg
import pytest

from analysis.retrieval import stopwords, topics


def csv_rows(path=topics.DICTIONARY_CSV) -> list[tuple[object, ...]]:
    """CSV 한 벌을 `db.lexicon.insert_aspects` 가 받는 행으로. CLI 와 같은 함수를 탄다."""
    from cosmai.cli import _csv_rows

    return _csv_rows("aspect", str(path))


def csv_topics(version: int | None = 1) -> topics.Topics:
    from db.lexicon import ASPECT_COLUMNS

    aspect, pattern, extra = (ASPECT_COLUMNS.index(c) for c in ("aspect", "pattern", "extra"))
    rows = [(str(r[aspect]), str(r[pattern]), cast(Mapping[str, Any], r[extra])) for r in csv_rows()]
    return topics.from_rows(rows, version)


def install_topics(conn: psycopg.Connection, version: int = 1, active: bool = True) -> None:
    """운영에서 `cosmai lexicon load --kind aspect` + `activate` 가 하는 일."""
    from db.lexicon import insert_aspects

    with conn.cursor() as cur:
        insert_aspects(cur, csv_rows(), version, active=active)
    conn.commit()


def stopword_rows(path=None) -> list[tuple[object, ...]]:
    """질의 불용어 CSV 한 벌을 `db.lexicon.insert_entities` 가 받는 행으로. 주제 사전과 같은 이유로
    CLI 와 같은 함수를 탄다 -- 픽스처가 목록을 손으로 다시 적으면 사전이 두 벌이 된다."""
    from cosmai.cli import _csv_rows

    return _csv_rows(stopwords.KIND, str(path or stopwords.DICTIONARY_CSV))


def csv_stopwords(version: int = 1) -> stopwords.QueryStopwords:
    surface = 2  # db.lexicon.ENTITY_COLUMNS 의 자리
    return stopwords.from_rows([(str(row[surface]), version) for row in stopword_rows()])


def install_stopwords(conn: psycopg.Connection, version: int = 1, active: bool = True) -> None:
    """운영에서 `cosmai lexicon load --kind stopword` + `activate` 가 하는 일."""
    from db.lexicon import insert_entities

    with conn.cursor() as cur:
        insert_entities(cur, stopword_rows(), version, active=active)
    conn.commit()


@pytest.fixture(autouse=True)
def repo_dictionary() -> Iterator[topics.Topics]:
    """DB 를 안 쓰는 테스트를 위한 활성 사전. DB 를 쓰는 입구는 자기 스키마에서 다시 세운다."""
    installed = topics.use(csv_topics())
    yield installed
    topics.forget()
