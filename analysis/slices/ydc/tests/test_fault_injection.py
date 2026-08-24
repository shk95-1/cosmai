"""오류 주입 테스트. (기획안 08.25 — "REST timeout·429·부분 응답·오류 행·정규화
실패·쿼터 초과를 주입해 예외 처리를 검증한다")

왜 이걸 하나. 수집기는 이미 돌았고 데이터는 받아 뒀다. 그래도 이 테스트가 필요한
이유는 두 가지다.

1. **재수집이 남아 있다.** 상한 10 아티팩트로 재수집한 이력이 있고, NAVER·논문이
   들어오면 유튜브도 다시 돌릴 수 있다. 그때 429 를 조용히 삼키면 부분 수집이
   전체 수집으로 위장된다. 이건 데이터 손실보다 나쁘다 — 손실은 보이고 위장은 안 보인다.

2. **쿼터 초과는 재시도하면 안 된다.** 429(rate limit)는 기다렸다 다시 걸어야
   하지만 403 quotaExceeded 는 하루가 지나야 풀린다. 둘을 같이 다루면 쿼터를
   태워 가며 3번 더 부른다. 이 구분이 실제로 코드에 있는지 확인한다.

느린 부분은 없다 — `time.sleep` 을 막고 `urlopen` 만 갈아 끼운다.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import trend  # noqa: E402
from youtube_collector import (  # noqa: E402
    YouTubeAPIError,
    YouTubeClient,
    duration_to_seconds,
    flatten_comment,
    flatten_video,
    to_int,
)


class FakeResponse(io.BytesIO):
    """urlopen 의 컨텍스트 매니저 흉내. read() 만 쓰므로 이걸로 충분하다."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok_body(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def http_error(code: int, reason: str, *, retry_after: str | None = None) -> HTTPError:
    body = json.dumps({"error": {"message": f"{reason} 발생", "errors": [{"reason": reason}]}})
    headers = {"Retry-After": retry_after} if retry_after else {}
    return HTTPError("https://x", code, reason, headers, io.BytesIO(body.encode("utf-8")))


class RetryableFaults(unittest.TestCase):
    """재시도해야 하는 오류들."""

    def setUp(self):
        self.sleep = mock.patch("youtube_collector.time.sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.client = YouTubeClient("key", timeout_seconds=1, max_retries=3)

    def test_429_는_기다렸다_다시_걸어_성공한다(self):
        responses = [http_error(429, "rateLimitExceeded", retry_after="7"),
                     ok_body({"items": [1]})]
        with mock.patch("youtube_collector.urlopen", side_effect=responses):
            self.assertEqual(self.client.get("videos", {}), {"items": [1]})
        # Retry-After 를 무시하고 자기 backoff 를 쓰면 서버가 준 대기를 어긴다
        self.sleep.assert_called_once_with(7.0)

    def test_timeout_은_재시도하고_끝내_RuntimeError(self):
        with mock.patch("youtube_collector.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(RuntimeError) as caught:
                self.client.get("videos", {})
        self.assertNotIsInstance(caught.exception, YouTubeAPIError)
        # 최초 1회 + 재시도 3회 = 4회. 조용히 빈 응답을 돌려주면 안 된다
        self.assertEqual(self.client.calls["videos"], 4)

    def test_부분_응답은_JSON_오류로_재시도된다(self):
        truncated = FakeResponse(b'{"items": [{"id": "abc"')
        with mock.patch("youtube_collector.urlopen",
                        side_effect=[truncated, ok_body({"items": []})]):
            self.assertEqual(self.client.get("videos", {}), {"items": []})

    def test_네트워크_끊김도_재시도된다(self):
        with mock.patch("youtube_collector.urlopen",
                        side_effect=[URLError("dns"), ok_body({"ok": True})]):
            self.assertEqual(self.client.get("videos", {}), {"ok": True})

    def test_500번대는_재시도한다(self):
        with mock.patch("youtube_collector.urlopen",
                        side_effect=[http_error(503, "backendError"), ok_body({"ok": 1})]):
            self.assertEqual(self.client.get("videos", {}), {"ok": 1})


class TerminalFaults(unittest.TestCase):
    """재시도하면 안 되는 오류들. 쿼터를 태우며 다시 부르는 게 최악이다."""

    def setUp(self):
        self.sleep = mock.patch("youtube_collector.time.sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.client = YouTubeClient("key", timeout_seconds=1, max_retries=3)

    def test_쿼터_초과는_즉시_멈춘다(self):
        with mock.patch("youtube_collector.urlopen",
                        side_effect=http_error(403, "quotaExceeded")) as opened:
            with self.assertRaises(YouTubeAPIError) as caught:
                self.client.get("videos", {})
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.reason, "quotaExceeded")
        self.assertEqual(opened.call_count, 1, "쿼터 초과에 재시도하면 하루치를 태운다")
        self.sleep.assert_not_called()

    def test_키_오류도_즉시_멈춘다(self):
        with mock.patch("youtube_collector.urlopen",
                        side_effect=http_error(400, "keyInvalid")) as opened:
            with self.assertRaises(YouTubeAPIError):
                self.client.get("videos", {})
        self.assertEqual(opened.call_count, 1)

    def test_댓글_비활성_영상은_구분되는_reason_을_준다(self):
        # 이 reason 은 실패가 아니라 comment_coverage=0 으로 남길 정상 상태다
        with mock.patch("youtube_collector.urlopen",
                        side_effect=http_error(403, "commentsDisabled")):
            with self.assertRaises(YouTubeAPIError) as caught:
                self.client.get("commentThreads", {})
        self.assertEqual(caught.exception.reason, "commentsDisabled")


class BadRows(unittest.TestCase):
    """오류 행. 필드가 빠진 응답이 와도 죽지 말고 None 으로 남겨야 한다."""

    def test_필드가_다_빠진_영상(self):
        row = flatten_video({}, [], [], [], "2026-08-21T00:00:00Z")
        self.assertIsNone(row["source_item_id"])
        self.assertIsNone(row["duration_seconds"])
        self.assertIsNone(row["view_count"])

    def test_숫자_자리에_문자가_온_경우(self):
        resource = {"id": "v1", "snippet": {"title": "선크림"},
                    "statistics": {"viewCount": "많음", "likeCount": None},
                    "contentDetails": {"duration": "이상한값"}}
        row = flatten_video(resource, [], [], [], "2026-08-21T00:00:00Z")
        self.assertIsNone(row["view_count"], "int 로 못 바꾸는 값은 None 이어야 한다")
        self.assertIsNone(row["duration_seconds"])
        self.assertEqual(row["title"], "선크림")

    def test_작성자_없는_댓글(self):
        row = flatten_comment({"id": "c1", "snippet": {"textOriginal": "좋아요"}},
                              video_id="v1", matched_queries=[], matched_windows=[],
                              collected_at="2026-08-21T00:00:00Z",
                              thread_id=None, parent_comment_id=None,
                              is_reply=False, total_reply_count=None)
        self.assertIsNone(row["author_channel_hash"])
        self.assertEqual(row["video_id"], "v1")

    def test_to_int_과_duration_의_경계(self):
        self.assertIsNone(to_int(""))
        self.assertIsNone(to_int(None))
        self.assertIsNone(to_int("1.5"))
        self.assertEqual(to_int("0"), 0)
        self.assertIsNone(duration_to_seconds("P1D"), "일 단위는 우리 정규식 밖이다")
        self.assertIsNone(duration_to_seconds(""))
        self.assertEqual(duration_to_seconds("PT1H2M3S"), 3723)


class NormalizationFaults(unittest.TestCase):
    """정규화 실패. 여기서 죽으면 문서 하나가 아니라 그 run 전체가 멈춘다."""

    def test_깨진_입력에도_문자열을_돌려준다(self):
        for bad in (None, "", "   ", "\x00\x07", "&#x1F600;&amp;", "\ud83d",
                    "a" * 100_000):
            self.assertIsInstance(trend.normalize_text(bad), str)

    def test_제어문자와_엔티티(self):
        self.assertEqual(trend.normalize_text("백탁\x00 없음"), "백탁 없음")
        self.assertEqual(trend.normalize_text("&lt;백탁&gt;"), "<백탁>")

    def test_주제_매칭은_깨진_입력에도_리스트를_준다(self):
        from topics import match_topics
        for bad in ("", "\x00", "ᅲᅲ", "🙂🙂🙂", "a" * 10_000):
            self.assertIsInstance(match_topics(trend.normalize_text(bad)), list)

    def test_분기_배정은_잘린_날짜에_죽는다(self):
        # 죽는 게 맞다. 조용히 엉뚱한 분기로 넣으면 시계열이 조용히 틀린다
        from unmatched_terms import quarter_of
        self.assertEqual(quarter_of("2026-04-01T00:00:00Z"), "2026Q2")
        with self.assertRaises(ValueError):
            quarter_of("2026")


if __name__ == "__main__":
    unittest.main(verbosity=2)
