"""반입하는 스냅샷이 계약이 설명하는 그 스냅샷인지 되묻는다 (포크 #4).

`manifest.json` 의 `rules`·`limitations`·`text_rule` 은 숫자가 무엇을 센 것인지를 말하는 문장이다.
그 문장이 파일 안에만 있으면 나중에 숫자만 남고 뜻이 사라지므로, 여기 상수로 옮겨 계약
(`contracts/formats.md` §코퍼스 스냅샷 · `contracts/interfaces.md` §모집단의 한계)이 같은 문장을
지고, 적재기는 읽어 들인 매니페스트가 이 문장들과 다르면 거절한다 -- 다른 규칙으로 만들어진 코퍼스가
같은 표에 조용히 섞이면 그 표의 모든 비율이 오류 없이 달라진다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# manifest.json 의 rules 11줄 그대로. 계약 문장의 자리는 contracts/formats.md §코퍼스 스냅샷.
RULES: tuple[str, ...] = (
    "유일키는 source + source_item_id 다. doc_id 는 그 둘을 콜론으로 이은 값이다.",
    "분기는 저장하지 않는다. published_at 의 연·월로 달력 분기를 만든다"
    "(수집 13,979편 전부 analysis_month 와 일치함을 확인).",
    "댓글은 published_at 이 자기 시각이므로 분기 판정에 쓰지 않는다. "
    "parent_item_id 로 부모 영상에 조인해 부모의 분기에 배정한다.",
    "트렌드 판정 분모는 content_type = video_long 만 쓴다. "
    "video_short 는 별도 계열, video_unknown 은 양쪽에서 제외한다.",
    "판정·보고 모집단은 channel.panel_role = product 로 한정한다.",
    "선크림 모집단 필터는 topic_id = 선크림(trend_use = false)으로 만든다.",
    "mention 은 주제 15개 전부를 담는다. 판정용 13개는 trend_use = true 로 필터한다.",
    "행을 지우지 않는다. 품질 문제는 quality_flags 로 표시한다(empty_text, duplicate_in_parent).",
    "언급량 집계에서는 quality_flags 가 빈 문서만 센다. "
    "duplicate_in_parent 는 같은 영상 안 복붙이라 반응 1건으로 보지 않는다.",
    "댓글은 주제 사전에 걸린 영상만 수집했다. 전체 영상에 대한 댓글 분모는 존재하지 않는다.",
    "태그를 판정 텍스트에 포함할지는 미결이다. "
    "포함하면 선크림 장문이 962 → 1,019편이 되고 모든 composition 이 움직인다.",
)

# source_run_manifests[*].limitations 8줄. 두 런이 같은 목록을 싣는다.
# 계약 문장의 자리는 contracts/interfaces.md §모집단의 한계.
LIMITATIONS: tuple[str, ...] = (
    "모집단은 시드 채널 집합이며 전체 YouTube가 아니다(고정 패널).",
    "패널 밖 신규 채널·신규 브랜드의 등장은 관측되지 않는다.",
    "조회수·좋아요는 collected_at 시점 스냅샷이다.",
    "업로드 플레이리스트 최신순 가정에 기반해 cutoff에서 조기 종료한다.",
    "댓글은 주제 사전에 걸린 영상만 받는다. 전체 영상의 댓글 분모는 존재하지 않는다.",
    "댓글 published_at은 댓글 자체 시각이다. 분기 귀속은 video_id로 부모 영상에 붙인다.",
    "댓글은 계속 쌓이므로 최근 분기는 구조적으로 과소 집계된다.",
    "order=relevance는 유튜브 비공개 알고리즘이며 좋아요 순이 아니다.",
)

TEXT_RULE = (
    "영상 text = 정규화(제목 + 공백 + 설명). 댓글 text = 정규화(본문). "
    "정규화는 HTML 엔티티 해제 → NFKC → 제어문자 제거 → 공백 축약이며 trend.py 의 normalize_text 를 "
    "그대로 쓴다. 태그는 text 에 넣지 않고 source_metadata.tags 로 보낸다. 자막·음성은 PoC 제외."
)

# 매니페스트가 선언한 행수를 담는 두 칸. 문장이 아니라 수라 상수로 옮기지 않고 대조만 한다 --
# 판본마다 다른 값이고, 계약이 지는 것은 "대조한다"는 규칙 쪽이다(contracts/formats.md §코퍼스 스냅샷).
COUNT_KEYS = ("table_counts", "documents_by_content_type")

# The vocabulary the loader knows. Must be the same list as the DDL's CHECK (023).
SOURCES = ("youtube_video", "youtube_comment")
CONTENT_TYPES = ("video_long", "video_short", "video_unknown", "comment")


class ManifestMismatch(ValueError):
    """The manifest declared rules that differ from the contract. A row with a different meaning does
    not go into the same table."""


def _limitations(manifest: Mapping[str, Any]) -> list[tuple[str, ...]]:
    return [tuple(run.get("limitations", ())) for run in manifest.get("source_run_manifests", ())]


def check(manifest: Mapping[str, Any]) -> None:
    """Whether the manifest's rules, limitations and text rule match the sentences the contract
    carries. If not, says what differs."""
    problems: list[str] = []
    if tuple(manifest.get("rules", ())) != RULES:
        problems.append("rules")
    if manifest.get("text_rule") != TEXT_RULE:
        problems.append("text_rule")
    # With several runs, all of them must carry the same limitations list -- if just one run was
    # gathered under different rules, that fact is caught right here.
    for index, found in enumerate(_limitations(manifest)):
        if found != LIMITATIONS:
            problems.append(f"source_run_manifests[{index}].limitations")
    if problems:
        raise ManifestMismatch(
            "manifest declares rules the contract does not carry: "
            + ", ".join(problems)
            + " (db/corpus/contract.py, contracts/formats.md §코퍼스 스냅샷)"
        )


def check_counts(manifest: Mapping[str, Any], measured: Mapping[str, Mapping[str, int]]) -> None:
    """Whether the row count the manifest declared matches the row count the import actually read.

    A CSV that arrived truncated **is only short of rows**, with no error raised -- then every ratio in
    this snapshot quietly changes and that fact shows up in no number at all. The value differs per
    version, so what the contract carries is not a number but the rule that says to compare it.
    """
    problems: list[str] = []
    for key in COUNT_KEYS:
        declared = manifest.get(key)
        if declared is None:
            problems.append(f"{key} is missing")
            continue
        found = measured[key]
        if dict(declared) != dict(found):
            problems.append(f"{key} declares {dict(declared)} but the load read {dict(found)}")
    if problems:
        raise ManifestMismatch(
            "manifest counts do not match what arrived: "
            + "; ".join(problems)
            + " -- the rows stay under this snapshot_id and are never activated; fix the source and"
            " re-run the same snapshot_id, ON CONFLICT DO NOTHING fills the gaps"
            " (db/corpus/contract.py, contracts/formats.md §코퍼스 스냅샷)"
        )


def check_unique(read: Mapping[str, int], inserted: Mapping[str, int]) -> None:
    """For a new version, **does the row count read equal the row count inserted** = is there no unique
    key repeated twice inside the file.

    This is where the manifest's `input_counts.duplicate_docs = 0` gets checked -- every INSERT is
    `ON CONFLICT DO NOTHING`, so a duplicate is silently dropped, and without counting there is no way
    to tell that apart from a file that arrived truncated. Calling this again on a version that already
    has rows inserts 0 rows and is meaningless, so the caller only calls it for a genuinely new version.
    """
    off = {
        name: (count, inserted[name]) for name, count in read.items() if inserted.get(name, count) != count
    }
    if off:
        raise ManifestMismatch(
            "duplicate unique keys in the source: "
            + "; ".join(f"{n} has {c} rows but {i} arrived" for n, (c, i) in off.items())
            + " (db/corpus/contract.py, contracts/formats.md §코퍼스 스냅샷)"
        )
