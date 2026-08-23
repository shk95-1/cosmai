# eval — 손 라벨 평가셋과 사전 시드 (1급 자산)

| 디렉터리 | 파일 | 건수 | 출처 |
|---|---|---|---|
| polarity/ | suncare_tune200 · suncare_holdout100 · crosscat_60 · crosscat_blind40 | 400 | slice-suncare, slice-p1 |
| wish/ | tune100 · holdout60 | 160 | slice-p9 |
| brand_link/ | precision_sample60 · precision_sample60_weighted · alias_verification · stoplist | 120+ | slice-p3 |
| product_match/ | match_check40 · match_check40_v2_blind | 80쌍 | slice-p2 |
| lexicon/ | brand_lexicon_v1 · ingredient_kr_colloquial_v1 · site_axis_map_seed | 847 / 32 / 25 | slice-p3 · slice-p4 · slice-p1 |

규칙: 라벨은 고치지 않는다(고치면 새 행). 구현 교체는 `contracts/interfaces.md` 기준선 표를 이 셋으로 갱신하는 PR 로만. 적재: `cosmai lexicon load`, `labeled_set` 은 `formats.md` 포맷으로 변환해 적재.
