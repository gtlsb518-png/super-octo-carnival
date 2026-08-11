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
    """대사에서 찾을 신호 하나.

    효과·전환·퇴장 애니메이션을 규칙마다 정해 둔다. 순서대로 돌려 쓰면
    성공 장면에 흑백이 깔리는 식으로 엉뚱해진다. 순간의 성격에 맞춰야 한다.
    """

    pattern: str          # 정규식
    text: str             # 붙일 자막 문구
    style: str
    anim: str             # 등장 애니메이션
    score: float          # 높을수록 먼저 채택
    sfx: tuple[str, ...] = ()      # 이 중 라이브러리에 있는 첫 번째를 깐다
    zoom: bool = False             # 줌을 걸 만한 순간인가
    outro: str = "fade"            # 퇴장 애니메이션
    effect: tuple[str, ...] = ()   # 이 순간에 어울리는 화면효과 후보
    trans: tuple[str, ...] = ()    # 근처 컷 경계에 쓸 전환 후보


#: 위에 있을수록 우선. 같은 대사에 여러 개 걸리면 점수 높은 것 하나만 쓴다.
RULES: tuple[Rule, ...] = (
    # --- 강한 리액션 ---
    Rule(r"대박|미쳤|말도\s*안\s*[돼되]|실화|헐", "헐 대박", "emphasis", "zoom", 10.0,
         ("drum", "ding"), zoom=True, outro="drop",
         effect=("impact", "quake", "flash"), trans=("glitch_hard", "flash_white")),
    Rule(r"우와+|와아+|와,|^와\b|와!", "와-", "reaction", "bounce", 9.0,
         ("sparkle", "ding"), zoom=True, outro="spring",
         effect=("sparkle", "starzoom", "glow"), trans=("zoom_star", "radial")),
    Rule(r"세상에|어머|아이[고구]", "어머", "reaction", "swing", 8.5, ("boing",),
         outro="spring", effect=("heart", "comic"), trans=("bubble", "blur_soft")),
    # --- 성공과 실패 ---
    Rule(r"됐다|됐어|성공|해냈|드디어", "성공!", "emphasis", "sparkle", 9.5,
         ("correct", "sparkle"), zoom=True, outro="rise",
         effect=("firework", "confetti", "sparkle"), trans=("flash_white", "sparkle")),
    Rule(r"망했|실패|틀렸|안\s*[돼되]네|못\s*하겠", "실패", "emphasis", "glitch_type", 9.0,
         ("fail", "error"), outro="glitch",
         effect=("bw", "vhs", "noise"), trans=("fade_black", "glitch_color")),
    Rule(r"어\?|어어|잠[깐시]|어떡", "?!", "reaction", "shake", 8.0,
         ("record_scratch", "boing"), zoom=True, outro="signal",
         effect=("question", "freeze", "shake"), trans=("glitch", "wipe_left")),
    # --- 감정 ---
    Rule(r"[ㅋㅎ]{2,}|하하|크크|푸하", "(웃음)", "narration", "cute", 7.0, ("boing",),
         outro="spring", effect=("comic", "comic3"), trans=("bubble", "page")),
    Rule(r"무섭|겁나|떨[려린]", "(긴장)", "whisper", "blur_in", 6.5, ("tension",),
         outro="blur", effect=("dream", "oldtv"), trans=("blur_v", "fade_black")),
    Rule(r"모르겠|글쎄|음+\.\.\.|어렵", "(당황)", "whisper", "drop", 6.0,
         outro="blur", effect=("ripple", "shake_soft"), trans=("blur", "gradient")),
    Rule(r"짜증|화나|열받", "(폭발)", "emphasis", "dash", 7.5, ("drum",),
         outro="drop", effect=("rush", "lightning", "strobe"),
         trans=("glitch_hard", "radial")),
    # --- 말투 ---
    Rule(r"진짜\?|정말\?|레알", "진짜?", "reaction", "flip", 7.5, ("ding",),
         outro="spin", effect=("freeze", "question"), trans=("spin", "wipe_right")),
    Rule(r"\?$", "?", "reaction", "spin_in", 4.0, ("pop",), outro="spring",
         effect=("question",), trans=("wipe_up", "dissolve")),
    Rule(r"!$|!!", "!", "reaction", "pop", 4.5, ("pop",), outro="spring",
         effect=("flash", "shake_soft"), trans=("flash_white", "push")),
    # --- 진행 ---
    Rule(r"자,?\s*(이제|그럼)|시작(할|해|합)", "시작", "situation", "slide_side", 5.5,
         ("swoosh",), outro="slide_side",
         effect=("glow", "retro"), trans=("wipe_right", "curtain")),
    Rule(r"마지막|끝났|마무리|정리하", "마무리", "situation", "scan", 5.5,
         ("swoosh",), outro="slide_side",
         effect=("dream", "retro"), trans=("fade_black", "gradient")),
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
    effect: tuple[str, ...] = ()
    trans: tuple[str, ...] = ()


#: 대부분의 컷에 깔 무난한 전환. 셋을 돌려 써서 티 나게 반복되지 않게 한다.
_CALM_TRANSITIONS = ("dissolve", "color_melt", "glare", "blur_soft")

#: 화면효과 하나가 화면에 머무는 시간
_EFFECT_HOLD = 0.45

#: 신호가 없는 클립에 깔 얌전한 효과. 센 걸 쓰면 아무 일도 없는데 요란해진다.
_CALM_EFFECTS = ("zoom_pulse", "glow", "shake_soft", "retro", "glow")

#: 신호가 없는 클립에 쓸 등장/퇴장 애니메이션
_CALM_ANIMS = ("pop", "rise", "slide_side", "fade", "bounce")

#: 밀도별로 '이 길이 이상인 클립에만 자막을 넣는다'
_MIN_CLIP = {"low": 2.5, "normal": 1.2, "high": 0.7}

#: 포인트 자막으로 뽑을 최대 글자 수
_QUOTE_CHARS = 12

#: 자막이 클립 경계에 딱 붙지 않게 남기는 여유
_CLIP_PAD = 0.12


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


_TRAIL = re.compile(r"[\s,.!?~…]+$")


def _quote(text: str) -> str:
    """대사에서 포인트 자막으로 쓸 짧은 한 토막.

    규칙에 안 걸리는 평범한 대사에도 자막은 있어야 한다. 문구를 지어내면
    거짓말이 되니 **실제로 한 말**을 줄여 쓴다. 예능에서 말꼬리를 받아
    큼직하게 띄우는 것과 같은 방식이라, 뒤쪽을 남긴다.
    """
    text = _TRAIL.sub("", text.strip())
    if not text:
        return ""
    if len(text) <= _QUOTE_CHARS:
        return text

    words = text.split()
    tail: list[str] = []
    for word in reversed(words):
        candidate = [word, *tail]
        if len(" ".join(candidate)) > _QUOTE_CHARS:
            break
        tail = candidate
    if tail:
        return " ".join(tail)
    return text[-_QUOTE_CHARS:]        # 띄어쓰기가 없는 긴 덩어리


def build_cuesheet(
    utterances: list[Utterance],
    total: float,
    *,
    density: str = "normal",
    sfx_names: list[str] | None = None,
    effect_names: list[str] | None = None,
    transition_names: list[str] | None = None,
    boundaries: list[float] | None = None,
    segments: list[tuple[float, float]] | None = None,
) -> Cuesheet:
    """대사에서 규칙으로 큐시트를 만든다.

    segments(메인 영상 클립들)를 주면 **클립 하나당 자막 하나·효과 하나**로
    고르게 배급한다. 안 주면 대사 밀도만 보고 배급한다.
    """
    if segments:
        return _build_per_clip(
            utterances, total, segments,
            density=density, sfx_names=sfx_names, effect_names=effect_names,
            transition_names=transition_names, boundaries=boundaries,
        )

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
                    outro=rule.outro,
                ),
                score=rule.score,
                sfx=rule.sfx,
                zoom=rule.zoom,
                effect=rule.effect,
                trans=rule.trans,
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
                    style="narration", position="upper", anim="fade", outro="fade",
                ),
                score=6.8,
                sfx=("crickets",),
                zoom=False,
                effect=("bw", "freeze"),
                trans=("fade_black", "dissolve"),
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

    # 5) 화면효과는 센 순간에, 영상 길이에 비례해서
    #
    # 예전엔 무조건 3개로 잘랐다. 10분짜리에도 3개뿐이라 화면이 밋밋했다.
    # 12초에 하나꼴로 두되, 남발하면 촌스러우니 자막 수의 절반은 넘기지 않는다.
    if effect_pool:
        available = set(effect_pool)
        budget = max(2, round(total / 12.0))
        budget = min(budget, max(2, len(sheet.captions) // 2 + 1))

        strong = sorted(
            (c for c in chosen if c.score >= 7.0 and c.effect),
            key=lambda c: (-c.score, c.time),
        )[:budget]

        # 같은 규칙이 여러 번 걸리면 후보를 돌려 쓴다. 안 그러면 "와 대박" 이
        # 열 번 나올 때 같은 효과가 열 번 깔린다.
        seen: dict[tuple[str, ...], int] = {}
        for cand in sorted(strong, key=lambda c: c.time):
            usable = [n for n in cand.effect if n in available]
            if not usable:
                continue
            turn = seen.get(cand.effect, 0)
            seen[cand.effect] = turn + 1
            end = min(cand.caption.start + _EFFECT_HOLD, total)
            if end > cand.caption.start:
                sheet.effects.append(
                    EffectCue(cand.caption.start, end, usable[turn % len(usable)])
                )

    # 6) 전환은 **모든** 컷 경계에
    #
    # 예전엔 경계 네 곳에만 넣어서, 나머지 컷은 툭툭 끊겼다. 컷이 있으면
    # 전환이 있는 게 맞다. 다만 종류는 가려 쓴다 — 자막이 붙은 자리는
    # 그 순간에 맞는 걸 쓰고, 나머지는 눈에 안 띄는 디졸브 계열로 깐다.
    if transition_pool and boundaries:
        available = set(transition_pool)
        punchy = {
            round(c.caption.start, 2): c.trans
            for c in chosen if c.trans
        }
        calm = [n for n in _CALM_TRANSITIONS if n in available] or transition_pool

        seen_trans: dict[tuple[str, ...], int] = {}
        for i, at in enumerate(sorted(boundaries)):
            name = None
            near = min(punchy, key=lambda t: abs(t - at), default=None)
            if near is not None and abs(near - at) <= 1.2:
                usable = [n for n in punchy[near] if n in available]
                if usable:
                    turn = seen_trans.get(punchy[near], 0)
                    seen_trans[punchy[near]] = turn + 1
                    name = usable[turn % len(usable)]
            if name is None:
                name = calm[i % len(calm)]
            sheet.transitions.append(TransitionCue(at, name, 0.5))

    return _tidy(sheet, total)


def _build_per_clip(
    utterances: list[Utterance],
    total: float,
    segments: list[tuple[float, float]],
    *,
    density: str,
    sfx_names: list[str] | None,
    effect_names: list[str] | None,
    transition_names: list[str] | None,
    boundaries: list[float] | None,
) -> Cuesheet:
    """메인 영상 클립 하나하나를 기준으로 자막·효과를 배치한다.

    클립마다 자막 하나가 들어가고, 화면효과는 **그 자막 클립에 딱 맞춰** 깔린다.
    캡컷에서 보면 영상 클립 위에 자막 클립이 있고 그 아래 효과가 같은 길이로
    누워 있어서, 하나를 잡고 밀면 셋이 같이 움직인다.
    """
    sfx_pool = set(sfx_names or [])
    effect_pool = list(effect_names or [])
    transition_pool = list(transition_names or [])
    boundaries = boundaries or []
    min_clip = _MIN_CLIP.get(density, _MIN_CLIP["normal"])

    sheet = Cuesheet()
    emphasis_budget = max(1, round(total / 45.0))
    seen_fx: dict[tuple[str, ...], int] = {}
    index = 0

    for clip_start, clip_end in segments:
        clip_end = min(clip_end, total)
        if clip_end - clip_start < min_clip:
            continue

        inside = [
            u for u in utterances
            if u.end > clip_start + 0.05 and u.start < clip_end - 0.05
        ]

        # 이 클립에서 가장 센 신호 하나를 고른다
        best: Rule | None = None
        best_at = clip_start
        for u in inside:
            rule = _match(u)
            if rule is not None and (best is None or rule.score > best.score):
                best, best_at = rule, u.start

        if best is not None:
            text, style, anim, outro = best.text, best.style, best.anim, best.outro
            fx, trans, sfx, zoom = best.effect, best.trans, best.sfx, best.zoom
        elif inside:
            # 규칙에 안 걸리는 평범한 대사 — 실제로 한 말을 줄여서 띄운다
            longest = max(inside, key=lambda u: len(u.text))
            text = _quote(longest.text)
            if not text:
                continue
            best_at = longest.start
            style, outro = "narration", "fade"
            anim = _CALM_ANIMS[index % len(_CALM_ANIMS)]
            fx = (_CALM_EFFECTS[index % len(_CALM_EFFECTS)],)
            trans, sfx, zoom = (), (), False
        else:
            continue  # 말이 없는 클립엔 지어낼 말이 없다

        # 자막은 반드시 이 클립 안에 들어가야 한다
        start = min(max(best_at + 0.1, clip_start + _CLIP_PAD), clip_end - _CLIP_PAD)
        end = min(start + _CAPTION_HOLD, clip_end - 0.02)
        if end - start < 0.35:
            continue

        if style == "emphasis":
            if emphasis_budget > 0:
                emphasis_budget -= 1
            else:
                style, anim = "reaction", "pop"

        caption = Caption(
            start=start, end=end, text=text, style=style,
            position=_rotate_position(index), anim=anim, outro=outro,
        )
        sheet.captions.append(caption)
        index += 1

        name = next((n for n in sfx if n in sfx_pool), None)
        if name:
            sheet.sfx.append(SfxCue(start, name))

        # 화면효과는 자막 클립과 같은 자리·같은 길이로
        usable = [n for n in fx if n in effect_pool]
        if usable:
            turn = seen_fx.get(fx, 0)
            seen_fx[fx] = turn + 1
            sheet.effects.append(EffectCue(start, end, usable[turn % len(usable)]))

        if zoom:
            z_end = min(start + 1.8, clip_end)
            if z_end - start >= 0.8:
                sheet.zooms.append(ZoomCue(max(clip_start, start - 0.2), z_end, 1.28))

    _add_transitions(sheet, boundaries, transition_pool)
    return _tidy(sheet, total)


def _add_transitions(
    sheet: Cuesheet, boundaries: list[float], transition_pool: list[str]
) -> None:
    """모든 컷 경계에 전환을 건다. 자막이 붙은 자리는 그 순간에 맞는 것으로."""
    if not (transition_pool and boundaries):
        return
    available = set(transition_pool)
    calm = [n for n in _CALM_TRANSITIONS if n in available] or transition_pool

    by_time = {round(c.start, 2): c for c in sheet.captions}
    for i, at in enumerate(sorted(boundaries)):
        near = min(by_time, key=lambda t: abs(t - at), default=None)
        name = None
        if near is not None and abs(near - at) <= 1.2:
            rule = _RULE_BY_TEXT.get(by_time[near].text)
            if rule is not None:
                usable = [n for n in rule.trans if n in available]
                if usable:
                    name = usable[i % len(usable)]
        sheet.transitions.append(TransitionCue(at, name or calm[i % len(calm)], 0.5))


#: 자막 문구 → 그 문구를 만든 규칙. 전환을 순간에 맞추는 데 쓴다.
_RULE_BY_TEXT: dict[str, Rule] = {r.text: r for r in RULES}


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
