"""API 없이 규칙으로 큐시트를 만든다.

Claude 를 쓰지 않고, 받아쓴 대사에서 리액션이 될 만한 지점을 직접 찾는다.
감탄사·질문·웃음·정적처럼 **글자만 보고도 알 수 있는 신호**에 기댄다.

한계는 분명하다. 맥락을 이해하지 못하므로 "지금 이 장면이 왜 웃긴지"는
모른다. 그래서 Claude 판보다 자막이 밋밋하고, 가끔 엉뚱한 데 붙는다.
대신 API 키도 돈도 필요 없고, 결과가 항상 같아서 예측 가능하다.
캡컷에서 지우고 고치는 걸 전제로 쓰면 충분히 쓸 만하다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .cuesheet import Caption, Cuesheet, EffectCue, SfxCue, TransitionCue, ZoomCue
from .transcribe import Utterance


@dataclass(frozen=True)
class Rule:
    """대사에서 찾을 신호 하나."""

    pattern: str          # 정규식
    text: str             # 붙일 자막 문구
    style: str
    anim: str
    score: float          # 높을수록 먼저 채택
    sfx: tuple[str, ...] = ()   # 있으면 이 중 라이브러리에 있는 첫 번째를 깐다
    zoom: bool = False    # 줌을 걸 만한 순간인가


#: 위에 있을수록 우선. 같은 대사에 여러 개 걸리면 점수 높은 것 하나만 쓴다.
RULES: tuple[Rule, ...] = (
    # --- 강한 리액션 ---
    Rule(r"대박|미쳤|말도\s*안\s*[돼되]|실화|헐", "헐 대박", "emphasis", "zoom", 10.0,
         ("drum", "ding"), zoom=True),
    Rule(r"우와+|와아+|와,|^와\b|와!", "와-", "reaction", "pop", 9.0, ("sparkle", "ding"),
         zoom=True),
    Rule(r"세상에|어머|아이[고구]", "어머", "reaction", "shake", 8.5, ("boing",)),
    # --- 성공과 실패 ---
    Rule(r"됐다|됐어|성공|해냈|드디어", "성공!", "emphasis", "sparkle", 9.5,
         ("correct", "sparkle"), zoom=True),
    Rule(r"망했|실패|틀렸|안\s*[돼되]네|못\s*하겠", "실패", "emphasis", "zoom", 9.0,
         ("fail", "error")),
    Rule(r"어\?|어어|잠[깐시]|어떡", "?!", "reaction", "shake", 8.0,
         ("record_scratch", "boing"), zoom=True),
    # --- 감정 ---
    Rule(r"[ㅋㅎ]{2,}|하하|크크|푸하", "(웃음)", "narration", "fade", 7.0, ("boing",)),
    Rule(r"무섭|겁나|떨[려린]", "(긴장)", "whisper", "slide_up", 6.5, ("tension",)),
    Rule(r"모르겠|글쎄|음+\.\.\.|어렵", "(당황)", "whisper", "slide_up", 6.0),
    Rule(r"짜증|화나|열받", "(폭발)", "emphasis", "shake", 7.5, ("drum",)),
    # --- 말투 ---
    Rule(r"진짜\?|정말\?|레알", "진짜?", "reaction", "pop", 7.5, ("ding",)),
    Rule(r"\?$", "?", "reaction", "pop", 4.0, ("pop",)),
    Rule(r"!$|!!", "!", "reaction", "pop", 4.5, ("pop",)),
    # --- 진행 ---
    Rule(r"자,?\s*(이제|그럼)|시작(할|해|합)", "시작", "situation", "slide_side", 5.5,
         ("swoosh",)),
    Rule(r"마지막|끝났|마무리|정리하", "마무리", "situation", "slide_side", 5.5,
         ("swoosh",)),
)

#: 이 길이(초) 이상 말이 없으면 정적 자막을 붙인다
SILENCE_GAP = 1.6

#: 밀도별 1분당 목표 자막 수
_DENSITY_PER_MIN = {"low": 4.0, "normal": 8.0, "high": 14.0}

#: 자막 하나가 화면에 머무는 시간
_CAPTION_HOLD = 1.3

#: 자막끼리 최소 간격
_MIN_GAP = 0.25


@dataclass
class _Candidate:
    time: float
    caption: Caption
    score: float
    sfx: tuple[str, ...]
    zoom: bool


def _match(utterance: Utterance) -> Rule | None:
    """대사 한 줄에 걸리는 규칙 중 가장 점수 높은 것."""
    best: Rule | None = None
    for rule in RULES:
        if re.search(rule.pattern, utterance.text):
            if best is None or rule.score > best.score:
                best = rule
    return best


def _rotate_position(index: int) -> str:
    """자막이 한 자리에만 몰리지 않게 돌려 쓴다."""
    return ("default", "top", "upper")[index % 3]


def build_cuesheet(
    utterances: list[Utterance],
    total: float,
    *,
    density: str = "normal",
    sfx_names: list[str] | None = None,
    effect_names: list[str] | None = None,
    transition_names: list[str] | None = None,
    boundaries: list[float] | None = None,
) -> Cuesheet:
    """대사에서 규칙으로 큐시트를 만든다."""
    sfx_pool = set(sfx_names or [])
    effect_pool = list(effect_names or [])
    transition_pool = list(transition_names or [])
    boundaries = boundaries or []

    candidates: list[_Candidate] = []

    # 1) 대사에서 신호 찾기
    for utterance in utterances:
        rule = _match(utterance)
        if rule is None:
            continue
        start = min(utterance.start + 0.1, max(0.0, total - 0.5))
        candidates.append(
            _Candidate(
                time=start,
                caption=Caption(
                    start=start,
                    end=min(start + _CAPTION_HOLD, total),
                    text=rule.text,
                    style=rule.style,
                    position="default",
                    anim=rule.anim,
                ),
                score=rule.score,
                sfx=rule.sfx,
                zoom=rule.zoom,
            )
        )

    # 2) 말이 끊긴 자리 = 정적
    for before, after in zip(utterances, utterances[1:]):
        gap = after.start - before.end
        if gap < SILENCE_GAP:
            continue
        start = before.end + 0.3
        if start + 0.5 > total:
            continue
        candidates.append(
            _Candidate(
                time=start,
                caption=Caption(
                    start=start, end=min(start + _CAPTION_HOLD, total),
                    text=f"({gap:.0f}초 정적)" if gap >= 3 else "(정적)",
                    style="narration", position="upper", anim="fade",
                ),
                score=6.8,
                sfx=("crickets",),
                zoom=False,
            )
        )

    # 3) 목표 밀도만큼 점수 높은 순으로 채택
    minutes = max(total / 60.0, 0.2)
    budget = max(1, round(_DENSITY_PER_MIN.get(density, 8.0) * minutes))
    candidates.sort(key=lambda c: (-c.score, c.time))
    chosen = sorted(candidates[:budget], key=lambda c: c.time)

    # 4) 겹치지 않게 정리하고, 강조를 아껴 쓰고, 위치를 돌려 쓴다
    #
    # 점수가 높은 규칙이 죄다 emphasis 라서 그냥 두면 화면이 온통 큼직한
    # 빨간 자막이 된다. 실제 예능은 강조를 한 구간에 하나쯤만 쓴다.
    emphasis_budget = max(1, round(total / 45.0))

    sheet = Cuesheet()
    last_end = -1.0
    for i, cand in enumerate(chosen):
        if cand.caption.start < last_end + _MIN_GAP:
            continue
        if cand.caption.style == "emphasis":
            if emphasis_budget > 0:
                emphasis_budget -= 1
            else:
                cand.caption.style = "reaction"     # 한 단계 낮춰 쓴다
                cand.caption.anim = "pop"
        cand.caption.position = _rotate_position(i)
        sheet.captions.append(cand.caption)
        last_end = cand.caption.end

        name = next((n for n in cand.sfx if n in sfx_pool), None)
        if name:
            sheet.sfx.append(SfxCue(cand.caption.start, name))

        if cand.zoom:
            end = min(cand.caption.start + 1.8, total)
            if end - cand.caption.start >= 0.8:
                sheet.zooms.append(ZoomCue(cand.caption.start - 0.2, end, 1.28))

    # 5) 화면효과는 가장 센 순간 몇 개만
    if effect_pool:
        strong = [c for c in chosen if c.score >= 9.0][:3]
        for i, cand in enumerate(strong):
            end = min(cand.caption.start + 0.45, total)
            if end > cand.caption.start:
                sheet.effects.append(
                    EffectCue(cand.caption.start, end, effect_pool[i % len(effect_pool)])
                )

    # 6) 전환은 컷 경계에 띄엄띄엄
    if transition_pool and boundaries:
        step = max(1, len(boundaries) // 4)   # 대략 네 군데만
        for i, at in enumerate(boundaries[::step]):
            sheet.transitions.append(
                TransitionCue(at, transition_pool[i % len(transition_pool)], 0.5)
            )

    return _tidy(sheet, total)


def _tidy(sheet: Cuesheet, total: float) -> Cuesheet:
    """줌·효과가 서로 겹치지 않게 앞의 것만 남긴다."""

    def dedupe(items):
        out = []
        for item in sorted(items, key=lambda x: x.start):
            if out and item.start < out[-1].end:
                continue
            item.end = min(item.end, total)
            item.start = max(0.0, item.start)
            if item.end > item.start:
                out.append(item)
        return out

    sheet.zooms = dedupe(sheet.zooms)
    sheet.effects = dedupe(sheet.effects)
    return sheet
