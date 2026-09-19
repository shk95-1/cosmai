# ytdlp fixtures

`yt_dlp.YoutubeDL.extract_info(..., download=False)` dumps, after `sanitize_info`, for the two kinds
`collectors/youtube/transport.py` takes over yt-dlp: a comment harvest and a caption-track discovery.
They sit beside the four #8 fixtures (`../comments`, `../listing`, `../transcript`,
`../video_metadata`), which are the same shape and are still what `tests/.../test_sources.py` reads.

**Provenance.** Transcribed from the archived reader of that shape
(`service/yt-scrapper/src/tubedepth/sources/{comments,transcript}.py` -- every key here is a key that
code reads) and trimmed to it. Not captures: #183 forbids the implementer a live call.

The axis they exist for is "the source returned less than we asked for" (#90):

- `comments-truncated-at-limit.json` holds exactly as many comments as the request asked for, which
  is how a harvest that was cut off looks -- indistinguishable from a complete one unless the count
  is compared against the limit.
- `comments-disabled.json` is `comments: null`, which is what yt-dlp returns for a video with
  comments turned off. Zero rows and no error.
- `comments-with-replies.json` is three top-level threads and four replies, interleaved the way
  yt-dlp returns them. It exists because the argument list is the thing #183's fix round found
  wrong: `max_comments`'s first element counts replies too, so a dump like this is "seven" by that
  number and three by the archive's instrument. The test that reads it asserts the **stored rows**
  -- top-level only, no `parent_id` -- rather than the arguments that were sent.
- `comments-author-shapes.json` is four top-level comments whose `author_id` covers the shapes a
  harvest actually hands over: a `UC…` channel id, a handle, a legacy `/user/` path and no
  identifier at all. It exists for a different axis (#267): the author rule is about what the row
  **keeps**, and a predicate that recognises only `UC…` would wave the middle two through, so the
  hashing has to be proved on a value that is raw without being channel-id shaped.
- `video-with-captions.json` carries a manual `ko` track, an ASR `ko-orig` track and an
  auto-translated `en` track (`tlang=` in its URL). The translated one is never a candidate.
- `video-without-captions.json` has both caption buckets empty.
