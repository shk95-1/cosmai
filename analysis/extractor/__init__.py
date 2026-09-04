"""Rule extractor rule-v2.3 -- candidate sentences (slice-suncare · slice-p1) and the wish classes a/b/c/n
(slice-p9).

The sentence splitting and the marker rules use the same regexes across the three slices. The wish markers
are attached to the rule version rather than to the dictionary table (the same place as the discourse markers
in analysis/lexicon.py), so they live here as constants. Only brands, formats and attributes come from
entity_lexicon.
"""

from __future__ import annotations

import re

from analysis.types import AspectLexicon, Candidate, Lexicon, TextUnit, WishResult

VERSION = "rule-v2.3"
# 슬라이스 셋이 같은 분할점을 쓴다: 종결 문장부호, '요/다' 뒤의 새 한글, ㅎㅎ/ㅋㅋ 뒤.
SPLIT = re.compile(r"(?<=[.!?~ㅠㅜ])\s+|(?<=요)\s+(?=[가-힣])|(?<=다)\s+(?=[가-힣])|(?<=[ㅎㅋ]{2})\s+")
SENTENCE_MIN = 4  # for the candidate decision (slice-suncare · slice-p1)
WISH_SENTENCE_MIN = 3  # for the wish decision (slice-p9)
LOW_RATING = 3.0
LIST_MAX = 3  # A12: format and attribute take at most 3 separated by ';'

# ---------- wish markers (slice-p9/wish_extractor.py) ----------
WISH_ANY = re.compile(
    r"면 좋겠|면 좋을|았으면|었으면|으면 해|나왔으면|있었으면|싶어요|싶네요|싶다|싶습니다|싶어\b|싶은데"
    r"|싶음"
    r"|싶당|싶네|싶어용|원해요|원합니다|바랍니다|바래요|바랄게|했음 좋|됐음 좋|있음 좋|줬음|줬으면"
    r"|기대(돼|되|합니다|중|됩니다)|싶지만|싶은|싶지"
)
NOT_C = re.compile(r"아닌가 ?싶|인가 ?싶|하지 ?싶|나 ?싶|지 ?싶|까 ?싶")
LAUNCH = re.compile(
    r"출시 ?(됐|되었|되면|됐으면|되었으면|되길|되면 좋|해 ?주|해 ?줬|했으면|하면 좋|하길|하라|해라|해줘"
    r"|해야"
    r"|하셨으면|해주길|해 ?주세요|좀|원|바|부탁|요청|시켜|됬)"
    r"|재출시|다시 출시|정식 출시|국내 출시|한국 출시"
    # 자리 표지는 그 자리에 없다/내달라가 뒤따를 때만 출시 요청이다 — 있다는 서술도 같은 표지를 쓴다.
    r"|(한국에도|국내에도|한국에서도|올영에도|올리브영에도|오프라인에도)"
    r"(?=[^.!?]{0,12}(없|나왔으면|나오면|나오길|출시|입점|팔|판매|들여|수입|해 ?주|주세요|좋겠|바라))"
    r"|나왔으면|나오면 좋|나오면 (바로 |꼭 )?(사야|살|삽니다|살게|사고 ?싶|사려|사겠|살거|사겠|대박)"
    r"|나왔음 좋"
    r"|나왔음 하|나와 ?줬으면|나와 ?주세요|(다시|도|또) ?나와야|나오길(?!래)|나오기를"
    r"|(?<!보)(?<!보내)내 ?줬으면|(?<!보)내 ?주세요|(?<!보)내 ?주시면|(?<!보)내 ?주셨으면"
    r"|(?<!보)내 ?주면 좋"
    r"|(?<!보)내 ?주길|(?<!보)내 ?줘(?!야|서|도|라|보|야지)|(?<!보)내줬음|출시해줘"
    r"|(?<!게 )(?<!게)만들어 ?(줘(?!서|요서)|주세요|주셨으면|주시면|줬으면|주면|주라|주길|주세여|주십"
    r"|주시길"
    r"|주시지|줬음|주삼|달라|주면 좋|주지)|만들면 좋|만들어 ?달"
    r"|단종 ?(안 ?되|하지 ?마|되지 ?않(?!았)|되지 ?말|시키지|말아|안되게|되면 ?안|하면 ?안|하면 너"
    r"|하지 ?말"
    r"|되지마|절대|시키면|만 ?안)|단종[^.!?]{0,12}(재출시|다시 ?(나|출|팔|만)|부활|살려|제발)"
    r"|따로 ?(해 ?주|만들|내 ?주|나오|나왔|팔|출시|판매)|판매 ?(해 ?주|했으면|하면 좋|해줬으면)|팔았으면"
    r"|팔아 ?주|팔면 좋|입점 ?(해 ?주|했으면|됐으면|되면|좀|부탁)"
    r"|(색상|컬러|호수|향|사이즈|용량|종류|제품|라인|옵션|구성|제형|톤)[^.!?]{0,8}"
    r"다양(하게 ?(나|내|출시|만들|좀|해 ?주|했으면)|해지|해졌으면|했으면|해진다면)|다양하게 ?(내|나|출시"
    r"|만들|좀)"
    r"|(버전|버젼|라인|사이즈|용량|색상|컬러|호수|향|제형|스틱형|쿠션형|타입)도"
    r"(?=.{0,25}(나오면|나왔으면|나오길(?!래)|있으면 좋|있었으면|출시(해|됐으면|되었으면|되면|좀)|만들어 ?주"
    r"|만들면"
    r"|내 ?주|내 ?줬|다양하게 ?(내|나|만|해 ?주|좀)|추가해|늘려|좋겠|주세요|부탁))"
    r"|(대용량|큰 ?용량|용량)(으로|도|이|은|을|만)? ?(나왔으면|나오면 좋|있으면 좋|있었으면|출시|만들어"
    r"|내 ?주"
    r"|내 ?줬|팔았으면|팔아|없나|없어서|좀|늘(?!려주셔|려 ?주셔|어나)|더 크)"
    r"|리필(도|로|이|용|은)? ?(있으면|있었으면|나왔으면|나오면|출시|만들|내 ?주|내 ?줬|팔|없나|없어서|좀"
    r"|해 ?주|판매)"
    r"|(남성|남자|여성|여자|바디|얼굴|손|발|아이|키즈|임산부)용(도|으로|은|이)? ?(나|있|출|만|있었|나왔"
    r"|좀)"
    r"|(도|만) ?(나왔|있었)(으면|음 좋|음 하|으면)|(도|만) ?(출시|만들)|(도|만) 있으면 좋|도 나오(면|길"
    r"|게)"
    r"|도 만들(어|면)"
    r"|었으면 하는 제품|있으면 좋겠는 제품|있었으면 하는"
    r"|(제품|버전|버젼|라인|사이즈|용량|색상|컬러|호수|향|옵션|구성|리필|단품|선택지|남성용|여성용|미니"
    r"|세트"
    r"|본품|매장|오프라인|온라인|국내|한국)[^.!?]{0,6}(없어서|없는게|없다는게|없다니) 아쉬|만 있어서 아쉬"
    r"|만 나와서 아쉬|없다는게 아쉬"
    r"|가격(이|도|은|만|좀|을)? ?(내려(줬으면|주세요|가면|갔으면|라|주시|오면|줘)|낮아졌으면|낮춰"
    r"|싸게 ?(해|팔|내|좀)|싸면 좋|싸졌으면|저렴하게 ?(해|팔|내|좀)|저렴했으면|인하|착하게 ?(해|좀))"
)
CONTENT = re.compile(
    r"리뷰|비교|추천|영상|소개|알려|설명|랭킹|테스트|보여|올려|찍어|공구|마켓|공동구매|튜토|룩북|콘텐츠"
    r"|컨텐츠"
    r"|방법|꿀팁|팁 |루틴|하울|브이로그|라이브|라방|강의|수업|레슨|자료|구독|채널|컬렉션|편도|2탄|시리즈"
    r"|후속"
    r"|다음 편|다음편|해보|써보|써 ?주|발라 ?주|발라 ?보|사용해 ?주|써주|답변|답글|댓글|질문|정리|분석"
    r"|모음"
    r"|쇼츠|특집|편 |회차|방송|겨울 ?버전 ?영상|여름 ?버전 ?영상|스타일링"
    r"|메이크업 ?(해|영상|법|도|도 보|보여)|화장법|화장 ?해|피셜|시연|출연|합방|콜라보 ?(영상|해)|챌린지"
    r"|기획"
    r"|이벤트|경품|나눔|할인|세일|쿠폰|행사|응모|당첨"
)
CONTENT_NOMARKET = re.compile(CONTENT.pattern.replace("|공구|마켓|공동구매", ""))
PAST_LAUNCH = re.compile(
    r"출시해 ?주(셔|신|시는|시고|셨)|만들어 ?주(셔서|신|셨|시는|시고)"
    r"|출시(된|한|하신|하셨|했|됐던|되어|되니|되자|되었네|했네|됐네|기념|일|날|하자|예정|소식|하셨네"
    r"|되었다"
    r"|하면서|하고|했다|한다|합니다|하니|돼서|되서|되고|해서|했을|했던|되기|후|전|하는|되는|이후|되었습"
    r"|하기"
    r"|되었어|했어|했구|됐구|되었네|했네)"
)
LAUNCH_AGAIN = re.compile(
    r"재출시|다시 출시|또 출시|도 출시|출시 ?(해 ?주(세|시면|실|셨으면|길|면)|했으면|좀|되었으면|됐으면)"
)
LAUNCH_NOT = re.compile(
    r"돈쭐|교과서|정신 ?차리|혼 ?내|화 ?내|짜증 ?내|성질 ?내|티 ?내|빛 ?내|소리 ?내|열 ?내|겟잇뷰티"
    r"|방송에"
    r"|출연|나와주세요"
)
# 표지 바로 앞의 "덜/적게/안": 나오는 주체가 제품이 아니라 증상이라 출시 요청이 아니다.
LESS_NOT_LAUNCH = re.compile(r"(?:^|\s)(덜|적게|안|그만|더는|더 이상)\s*$")
LAUNCH_OVER_CONTENT = re.compile(
    r"출시 ?(해 ?주|했으면|좀|되었으면|됐으면|해줘)|나왔으면|내 ?줬으면|재출시|단종"
)
CONTENT_CHANNEL = re.compile(r"컨텐츠|콘텐츠|영상|채널|편\b")
PRODUCT_Q = re.compile(
    r"(제품|템|것|거|버전|버젼|라인|사이즈|용량|색상|컬러|호수|향|선크림|썬크림|쿠션|파데|파운데이션|립"
    r"|틴트"
    r"|샴푸|크림|로션|토너|세럼|앰플|에센스|팩|마스크|클렌징|폼|오일|밤|미스트|픽서|파우더|컨실러|블러셔"
    r"|섀도"
    r"|섀도우|쉐도우|마스카라|라이너|브로우|향수|퍼퓸|바디|핸드크림|립밤|스틱|젤|치약|비누|워시"
    r"|트리트먼트"
    r"|린스|패드|패치|세트|구성|키트|기획|단품|본품|정품|리필)"
    r"\s*(은|는|이|가|도|만|로|으로)?\s*"
    r"(없나요|없을까요|없을까|없나|없어요\?|없음\?|왜 없|안 나오|안나오|안 만들|안만들|안 파|안파|안 팔)"
)
PRODUCT_Q_NOT = re.compile(r"추천|어디|뭐|무슨|어떤|답이|방법")
B_END = re.compile(
    r"(해|드려|드립|해 ?줘|해 ?주|해주|하면|해 ?주시|좀|꼭|도|부탁)?\s*"
    r"(주세요|주세여|주셔요|주셔용|주세용|주시면|주실|주면|줘요|줘\b|줘$|주라|주길|주셨으면|줬으면|주시길"
    r"|주시죠|주십|주삼|달라(?!질|지|서|진|요$)|부탁|원해요|원합니다|원해|바랍니다|바래요|해 ?주\b|해줘"
    r"|해주\b"
    r"|해주셨으면|해줬으면|해주시길|해주면|하면 안 ?될까|하면 안될까|되나요\?|해 ?주실래|해주쇼|해줘야"
    r"|하자\b"
    r"|해줘$)"
)
B_BAD = re.compile(
    r"주셔서|주신|주시네|주셨|주셔도|주시는|주시고|주셔야|주셨던|주시니|주시던|주셨는|주셔용$|구매해 ?주"
    r"|시청해 ?주|봐 ?주셔|사용 ?부탁|입력|확인 ?부탁|문의|신청|답변 ?드|알림|링크|오픈합니다|당첨"
    r"|응원합니다"
    r"|감사합니다|축하"
)
B_OBJ = re.compile(
    r"리뷰|비교|추천|영상|소개|알려|설명|랭킹|테스트|보여|올려|찍어|공구|마켓|공동구매|튜토|룩북|콘텐츠"
    r"|컨텐츠"
    r"|방법|꿀팁|팁|루틴|하울|브이로그|라이브|라방|강의|수업|컬렉션|2탄|시리즈|후속|다음 ?편|해 ?보"
    r"|써 ?보"
    r"|써 ?주|발라 ?주|발라 ?보|사용해 ?주|써봐 ?주|정리|분석|모음|쇼츠|특집|편\b|방송|스타일링|메이크업"
    r"|화장법"
    r"|화장 ?해|시연|출연|합방|콜라보|챌린지|기획|조합|정보|성분|차이|순서|사용법|쓰는 ?법|바르는 ?법"
    r"|관리법"
    r"|케어|다뤄|재입고|재판매|오픈|링크|제품명|이름|가르쳐|공개|말씀|얘기|이야기|답변|답글|대답|풀어"
    r"|탐구"
    r"|파헤|점검|진단|피드백|코멘트|평가|솔직|언박싱|득템|쇼핑|구매템|추천템|사용템|유목민|정착템|입문"
    r"|초보"
    r"|가이드|총정리|베스트|워스트|ㅊㅊ|선물|나눔|이벤트|굿즈|세트|자주|많이|앞으로|또 ?(해|올|보|만)"
    r"|더 ?(해|올|보|만)|도 해|도 부탁|도 좀"
)
B2 = re.compile(
    r"(정보|영상|이런 ?거|이런거|이런 ?영상|컨텐츠|콘텐츠|리뷰|비교|추천|시리즈|후속|2탄|룩북|튜토리얼"
    r"|브이로그"
    r"|하울|메이크업|화장법|팁|꿀팁|방법)[^.!?]{0,14}"
    r"(보고 ?싶|알고 ?싶|궁금해요|원해|기다리|없을까|없나요|해주|부탁|바랍)"
    r"|(마켓|공구|공동구매|판매|재입고|입고|재판매|오픈|라방|라이브)[^.!?]{0,12}"
    r"(없나요|없을까요|없을까|안 ?하|언제|있나요|있을까요)"
)
B_WINDOW = 30  # a content object must be within 30 characters before the request ending for a creator ask
CLASS_ORDER = {"a": 0, "b": 1, "c": 2, "n": 3}


def sentences(text: str, minimum: int = SENTENCE_MIN) -> list[str]:
    parts = [p.strip() for p in SPLIT.split(text or "") if p and len(p.strip()) > minimum]
    whole = (text or "").strip()
    return parts or ([whole] if whole else [])


def _is_creator_request(sentence: str) -> str | None:
    found = B2.search(sentence)
    if found and not B_BAD.search(sentence):
        return found.group(0)
    for m in B_END.finditer(sentence):
        if B_BAD.search(sentence[max(0, m.start() - 6) : m.end() + 4]):
            continue
        if B_OBJ.search(sentence[max(0, m.start() - B_WINDOW) : m.end()]):
            return m.group(0)
    return None


def classify_wish(sentence: str) -> tuple[str, str]:
    """The wish class of one sentence (a product/launch request · b creator request · c general wish · n
    none)."""
    stated = PAST_LAUNCH.search(sentence) and not LAUNCH_AGAIN.search(sentence)
    launch = None if stated else LAUNCH.search(sentence)
    if LAUNCH_NOT.search(sentence):
        launch = None
    if launch and LESS_NOT_LAUNCH.search(sentence[: launch.start()]):
        launch = None
    if launch and not CONTENT.search(sentence):
        return "a", launch.group(0)
    asked = PRODUCT_Q.search(sentence)
    if asked and not CONTENT_NOMARKET.search(sentence) and not PRODUCT_Q_NOT.search(sentence):
        return "a", asked.group(0)
    # "X 출시해주세요, 리뷰도요": 출시 동사가 콘텐츠 요청을 이긴다.
    if launch and LAUNCH_OVER_CONTENT.search(sentence) and not CONTENT_CHANNEL.search(sentence):
        return "a", launch.group(0)
    creator = _is_creator_request(sentence)
    if creator:
        return "b", creator
    hope = WISH_ANY.search(sentence)
    if hope and not NOT_C.search(sentence):
        return "c", hope.group(0)
    return "n", ""


class RuleExtractor:
    version = VERSION

    def __init__(self) -> None:
        # complaint_marker_re compiles the regex on every call (types.py) -- it is built once per category.
        # The cache holds the dictionary, so its id is not reused while it is alive.
        self._markers: dict[tuple[int, str | None], tuple[AspectLexicon, re.Pattern[str]]] = {}
        self._brands: dict[int, tuple[Lexicon, dict[str, str]]] = {}

    def _marker_re(self, aspects: AspectLexicon, category: str | None) -> re.Pattern[str]:
        key = (id(aspects), category)
        cached = self._markers.get(key)
        if cached is None or cached[0] is not aspects:
            cached = (aspects, aspects.complaint_marker_re(category))
            self._markers[key] = cached
        return cached[1]

    def _brand_of(self, lexicon: Lexicon, sentence: str) -> str | None:
        cached = self._brands.get(id(lexicon))
        if cached is None or cached[0] is not lexicon:
            cached = (
                lexicon,
                {
                    s.surface: s.canonical
                    for s in lexicon.surfaces
                    if s.kind == "brand" and s.canonical not in lexicon.stop
                },
            )
            self._brands[id(lexicon)] = cached
        brands = cached[1]
        for m in lexicon.surface_re.finditer(sentence):
            canonical = brands.get(m.group(1) if m.lastindex else m.group(0))
            if canonical is None:
                continue
            if canonical in lexicon.cooc_required:
                window = sentence[max(0, m.start() - lexicon.cooc_window) : m.end() + lexicon.cooc_window]
                if not lexicon.product_word_re.search(window):
                    continue
            return canonical
        return None

    @staticmethod
    def _listed(patterns: tuple[tuple[str, re.Pattern[str]], ...], sentence: str) -> str | None:
        found = [name for name, rx in patterns if rx.search(sentence)]
        return ";".join(found[:LIST_MAX]) or None

    def candidates(
        self, unit: TextUnit, aspects: AspectLexicon, lexicon_category: str | None = None
    ) -> list[Candidate]:
        """lexicon_category chooses the dictionary -- unit.category is the site original and not a dictionary
        key."""
        category = lexicon_category if lexicon_category is not None else unit.category
        marker_re = self._marker_re(aspects, category)
        low = unit.rating is not None and unit.rating <= LOW_RATING
        subject = unit.product_key if unit.src == "review" else unit.channel_id
        found: list[Candidate] = []
        seen: set[str] = set()
        for sentence in sentences(unit.text):
            if sentence in seen:
                continue
            wish = aspects.wish_marker_re.search(sentence)
            complaint = None if wish else marker_re.search(sentence)
            if wish is None and complaint is None and not low:
                continue
            seen.add(sentence)
            marker = wish or complaint
            found.append(
                Candidate(
                    unit_ref=unit.ref,
                    sentence=sentence,
                    kind="wish" if wish else ("complaint" if complaint else "low_rating"),
                    marker=marker.group(0) if marker else f"rating={unit.rating}",
                    subject=subject,
                )
            )
        return found

    def wishes(self, unit: TextUnit, lexicon: Lexicon) -> WishResult | None:
        """Class n (not a wish) is not a row -- wish_mention.wish_class takes only a|b|c (001)."""
        best: tuple[str, str, str] = ("n", "", "")
        for sentence in sentences(unit.text, WISH_SENTENCE_MIN):
            found, marker = classify_wish(sentence)
            if CLASS_ORDER[found] < CLASS_ORDER[best[0]]:
                best = (found, marker, sentence)
        if best[0] == "n":
            return None
        return WishResult(
            wish_class=best[0],
            brand=self._brand_of(lexicon, best[2]),
            format=self._listed(lexicon.format_patterns, best[2]),
            attribute=self._listed(lexicon.attribute_patterns, best[2]),
            marker=best[1],
            sentence=best[2],
        )
