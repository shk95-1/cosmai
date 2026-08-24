import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from youtube_collector import (  # noqa: E402
    duration_to_seconds,
    flatten_comment,
    flatten_video,
    normalize_api_datetime,
    unique_preserving_order,
)


class CollectorUtilityTests(unittest.TestCase):
    def test_normalize_date_range(self):
        self.assertEqual(normalize_api_datetime("2026-08-18"), "2026-08-18T00:00:00Z")
        self.assertEqual(
            normalize_api_datetime("2026-08-18", end_of_day=True),
            "2026-08-18T23:59:59Z",
        )

    def test_duration_to_seconds(self):
        self.assertEqual(duration_to_seconds("PT1H2M3S"), 3723)
        self.assertEqual(duration_to_seconds("PT7M"), 420)
        self.assertIsNone(duration_to_seconds(None))

    def test_unique_queries(self):
        self.assertEqual(unique_preserving_order(["선크림", " 선크림 ", "백탁"]), ["선크림", "백탁"])

    def test_flatten_video(self):
        resource = {
            "id": "abc123",
            "snippet": {
                "publishedAt": "2026-08-01T00:00:00Z",
                "channelId": "channel1",
                "channelTitle": "채널",
                "title": "영상",
                "description": "설명",
                "tags": ["선크림"],
                "thumbnails": {"high": {"url": "https://example.com/thumb.jpg"}},
            },
            "contentDetails": {"duration": "PT2M30S", "caption": "true"},
            "statistics": {"viewCount": "100", "likeCount": "5", "commentCount": "2"},
        }
        row = flatten_video(resource, ["선크림 백탁"], ["2026-08"], [2], "2026-08-18T00:00:00Z")
        self.assertEqual(row["duration_seconds"], 150)
        self.assertEqual(row["view_count"], 100)
        self.assertEqual(row["best_search_rank"], 2)

    def test_flatten_comment_hashes_author(self):
        comment = {
            "id": "comment1",
            "snippet": {
                "textDisplay": "촉촉해요",
                "authorChannelId": {"value": "private-channel-id"},
                "likeCount": 3,
            },
        }
        row = flatten_comment(
            comment,
            video_id="abc123",
            matched_queries=["선크림 촉촉함"],
            matched_windows=["2026-08"],
            collected_at="2026-08-18T00:00:00Z",
            thread_id="thread1",
            parent_comment_id=None,
            is_reply=False,
            total_reply_count=0,
        )
        self.assertEqual(row["text"], "촉촉해요")
        self.assertNotEqual(row["author_channel_hash"], "private-channel-id")
        self.assertEqual(len(row["author_channel_hash"]), 24)


if __name__ == "__main__":
    unittest.main()
