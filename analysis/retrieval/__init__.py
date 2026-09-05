"""The retrieval unit -- the five-column chunk contract and BM25 lexical search.

The rules are carried over from ydc `trend.py` · `chunks.py` · `topics.py` · `bm25.py` · `retrieval_eval.py` ·
`encode_chunks.py` · `hybrid.py` (shk95-1/cosmai-ydc-old `v0.1.0` `02440ab`) -- which file each piece came
from is the parenthesis in each module header. `bm25.py` · `retrieval_eval.py` changed later in `v0.3.0` and
`chunks.py` in `v0.2.0`, each dispositioned on goal #1. The pinned copy `analysis/slices/ydc/` was a
reference copy, never imported, and once everything to carry over had been carried, fork #9 deleted that
directory. The import pin (`v0.4.0` `76db718`) lives in `contracts/versioning.md`.
"""
