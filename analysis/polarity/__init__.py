"""규칙 극성 rule-v2.2 — 순수 함수(DB 없음). #6 의 LLM 구현이 같은 시그니처로 갈아 끼운다.

어휘와 규칙은 slice-suncare/polarity.py(v2.2)를 그대로 옮긴 것이다: 부정어 분리, 타제품·취향·피부타입
맥락 제외, 부정된 만족어, 중립 명사는 불만어를 동반할 때만 불만. aspect 사전은 인자로 받는다 —
slice-p1/aspects_generic.py 가 런타임에 사전만 바꿔 끼워 카테고리 횡단을 돌린 것과 같은 자리다.
"""

from __future__ import annotations

import re

from analysis.types import AspectLexicon, AspectPattern, PolarityResult

VERSION = "rule-v2.2"
SUNCARE_CATEGORY = "선블록"
SUNCARE_RULESET = "suncare-v2.2"
GENERIC_RULESET = "p1-v2.2"

POS = re.compile(
    r"좋[아은다네요]|좋고|좋습|괜찮|나쁘지 ?않|만족|추천|최고|굿|짱|대박|편[해하했]|순[해하]|촉촉|보송"
    r"|산뜻"
    r"|가볍|가벼|부드럽|완벽|인생|재구매|또 ?살|잘 ?맞|마음에 ?[들듭]|맘에 ?[들듭]|강추|사랑|딱이|무난"
)
POS_NEGATED = re.compile(
    r"(재구매|구입|구매|추천)[은는를도]?\s?(안|않|절대|다신|다시는)|다신 ?(구입|구매|사지)|절대 ?사지"
    r"|손이 ?안 ?가|손에 ?많이 ?가지는 ?않|그냥 ?그래|나을 ?듯|미지수|기대했는데|못 ?쓰|안 ?맞|안 ?좋"
    r"|비추|다른 ?제품 ?쓰"
)
WISH = re.compile(r"좋겠|좋았으면|었으면|았으면")
NEG_STRONG = re.compile(
    r"너무 ?(심|많|세|강|답답|무거|건조|끈적|따가|밀|두껍)|(?<!심하지 )심[하해했함]"
    r"|엄청 ?(심|따|밀|건조|끈적|어두)|진짜 ?(심|많|따|밀|건조|끈적)|싫|별로|최악|실망|못 ?쓰|안 ?맞"
    r"|안 ?좋"
    r"|그닥|아쉬|아쉽|단점|불편|후회|환불|버렸|돈 ?아깝|짜증|기대 ?이하|쓰레기|때처럼|개[따뻑별]|레전드"
    r"|충격|중단|신중|테스트 ?해보|피하는게|비추|어두[운움워]|흘러[내있]|힘들|못 ?쓸|안 ?먹|잘 ?모르겠"
    r"|떨어지는|쓰면 ?안|알될|안될"
)
NEG_AFTER = re.compile(
    r"^.{0,14}?(없|않|안(?= ?[가-힣])|적[고어은다]|덜|제로|zero|1도|하나도|전혀|거의|심하지"
    r"|크게 ?(느껴지지|않)|X|x)",
    re.I,
)
NEG_BEFORE = re.compile(r"(없|않|안 |덜|전혀|하나도|1도|심하지)\s?.{0,6}$")
# aspect 가 '이 제품'이 아닌 것에 붙는 맥락.
CONTEXT_BEFORE = re.compile(
    r"(다른 ?(선크림|제품|거)|유기자차[는은]?|무기자차[는은]?|사용하던|기존|전에 ?쓰던|예전|평소|특유의"
    r"|선크림 ?특유|선크림[은는]? ?(끈적|답답|백탁)|날씨|가을|겨울|피부 ?타입|피부[가는]? ?(좀 )?(건조"
    r"|예민|민감)"
    r"|프라이머|파데|쿠션만|후기에|리뷰에|리뷰가|후기가)\s?[가-힣]{0,6}$"
)
SKIN_CONTEXT = re.compile(
    r"(피부 ?타입|피부입니다|피부예요|피부에요|편이에요|편입니다|피부 ?\.\.|싶으신 ?분|이신 ?분|하신 ?분"
    r"|분들은|분들에게|분이라면|분들이면|피부라면)"
)
WARNING = re.compile(r"안 ?쓰|신중|비추|테스트|피하|고민|주의|조심")
CONTRAST_THIS = re.compile(
    r"(는데|지만|인데)\s?(얘는|이건|이거는|이 ?제품은|요건|이게|얘가)|싫어하는데|싫어서|싫은데"
)
NEGATED_TAIL = re.compile(r"없어서|없고|않아서|않고")

_CONTEXT = None  # 맥락 히트: 부정 여부를 판정하지 않는다 (aspect 가 이 제품 것이 아니다).


class _Hit:
    __slots__ = ("aspect", "neutral", "negated", "start")

    def __init__(self, pattern: AspectPattern, negated: bool | None, start: int) -> None:
        self.aspect = pattern.aspect
        self.neutral = pattern.is_neutral_noun
        self.negated = negated
        self.start = start


def ruleset_for(category: str | None) -> str:
    """suncare 사전은 '선블록' 행만 갖는다 — 다른 카테고리에 물으면 빈 사전이 온다 (formats.md §ruleset)."""
    return SUNCARE_RULESET if category == SUNCARE_CATEGORY else GENERIC_RULESET


def _hits(sentence: str, patterns: tuple[AspectPattern, ...]) -> list[_Hit]:
    found: list[_Hit] = []
    for pattern in patterns:
        for m in pattern.pattern.finditer(sentence):
            after = sentence[m.end() : m.end() + 16]
            before = sentence[max(0, m.start() - 22) : m.start()]
            if CONTEXT_BEFORE.search(before):
                found.append(_Hit(pattern, _CONTEXT, m.start()))
                continue
            negated = bool(NEG_AFTER.search(after)) or bool(NEG_BEFORE.search(before))
            found.append(_Hit(pattern, negated, m.start()))
    return found


def _first(*groups: list[_Hit]) -> str | None:
    for group in groups:
        if group:
            return group[0].aspect
    return None


class RulePolarity:
    version = VERSION

    def __init__(self) -> None:
        # for_category 는 부를 때마다 사전을 다시 훑는다 (types.py 주석) — 카테고리별로 캐시한다.
        # 캐시가 사전을 붙들고 있으므로 id 는 살아 있는 동안 재사용되지 않는다.
        self._patterns: dict[tuple[int, str | None], tuple[AspectLexicon, tuple[AspectPattern, ...]]] = {}

    def patterns_for(self, aspects: AspectLexicon, category: str | None) -> tuple[AspectPattern, ...]:
        key = (id(aspects), category)
        cached = self._patterns.get(key)
        if cached is None or cached[0] is not aspects:
            cached = (aspects, aspects.for_category(category))
            self._patterns[key] = cached
        return cached[1]

    def classify(
        self, sentence: str, rating: float | None, category: str | None, aspects: AspectLexicon
    ) -> PolarityResult:
        # rating 은 규칙 v2 가 폴백을 걷어낸 뒤로 쓰지 않는다 — 기준선 .77 이 그 규칙에서 나온 숫자다.
        aspect, polarity, reason = self._judge(sentence, self.patterns_for(aspects, category))
        return PolarityResult(aspect=aspect, polarity=polarity, reason=reason, version=self.version)

    def _judge(self, sentence: str, patterns: tuple[AspectPattern, ...]) -> tuple[str | None, str, str]:
        s = sentence
        found = _hits(s, patterns)
        has_wish = bool(WISH.search(s))
        has_pos = (
            bool(POS.search(s))
            and not POS_NEGATED.search(s)
            and not (has_wish and not NEGATED_TAIL.search(s))
        )
        has_neg = bool(NEG_STRONG.search(s)) or bool(POS_NEGATED.search(s))
        ctx = [f for f in found if f.negated is _CONTEXT]
        real = [f for f in found if f.negated is not _CONTEXT]

        if SKIN_CONTEXT.search(s) and not CONTRAST_THIS.search(s):
            if WARNING.search(s) or POS_NEGATED.search(s):
                return _first(real, ctx), "불만", "warning-to-others"
            if has_pos and not has_neg and real:
                return real[0].aspect, "만족", "skin-ctx+pos"
            return _first(real, ctx), "중립", "skin-context"
        if not real:
            if has_neg and not has_pos:
                return _first(ctx), "불만", "neg-only"
            if has_pos and not has_neg:
                return _first(ctx), "만족", "pos-only"
            return _first(ctx), "중립", "context-only" if ctx else "no-aspect"

        negated = [f for f in real if f.negated]
        raw = [f for f in real if not f.negated]
        raw_complaint = [f for f in raw if not f.neutral]
        raw_neutral = [f for f in raw if f.neutral]

        contrast = CONTRAST_THIS.search(s)
        if contrast:  # "X 싫어하는데 얘는 …": 뒤 절이 결론이다.
            tail = s[contrast.end() :]
            tail_found = [f for f in real if f.start >= contrast.start()]
            tail_raw = [f for f in tail_found if not f.negated and not f.neutral]
            if tail_raw and NEG_STRONG.search(tail):
                return tail_raw[0].aspect, "불만", "contrast-tail-raw"
            if POS.search(tail) or any(f.negated for f in tail_found):
                return (tail_found or real)[0].aspect, "만족", "contrast-tail-pos"
        if raw_complaint:
            key = raw_complaint[0].aspect
            if has_pos and not has_neg and negated and len(negated) >= len(raw_complaint):
                return key, "만족", "mostly-negated+pos"
            return key, "불만", "aspect-raw"
        if raw_neutral and not negated:
            key = raw_neutral[0].aspect
            if has_neg and not has_pos:
                return key, "불만", "neutral-aspect+neg"
            if has_pos:
                return key, "만족", "neutral-aspect+pos"
            return key, "중립", "neutral-aspect-only"
        key = negated[0].aspect
        if has_neg and not has_pos and not NEG_AFTER.search(s[negated[0].start :]):
            return key, "불만", "aspect-negated+neg"
        return key, "만족", "aspect-negated"
