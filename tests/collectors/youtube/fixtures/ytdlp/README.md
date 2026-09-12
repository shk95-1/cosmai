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
- `video-with-captions.json` carries a manual `ko` track, an ASR `ko-orig` track and an
  auto-translated `en` track (`tlang=` in its URL). The translated one is never a candidate.
- `video-without-captions.json` has both caption buckets empty.
