# data_api fixtures

The response shapes of the three YouTube Data API v3 methods `collectors/youtube/transport.py` calls:
`channels.list`, `playlistItems.list` and `videos.list`, plus the 403 body a spent quota comes back as.

**Provenance, said plainly.** These four files were transcribed from Google's published response
schema for those methods, field for field, including the fields this collector does not read
(`etag`, `thumbnails`, `pageInfo.resultsPerPage`). They are **not** captures of a live response --
issue #183 forbids the implementer from making a live call, and the one live run is the
coordinator's. Every value is a shape, not a measurement: no count, id or date in them is evidence
about any real channel.

What they are for is the axis a request-shaped fake cannot cover (#90): `uploads-page-1.json` hands
back a `nextPageToken` and `uploads-page-2.json` then returns **fewer** items than the page size and
no token, so a walker that trusts `pageInfo.totalResults` disagrees with what it actually holds.
`uploads-page-empty.json` is the page that answers with a token and no items at all, which the Data
API does return and which loops a walker forever.

When the live run happens, replacing these with captured bodies is worth doing -- the field set is
what this asserts, and only a capture proves the field set.
