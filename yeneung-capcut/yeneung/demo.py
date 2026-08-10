"""캡컷 호환성만 빠르게 확인하는 데모.

받아쓰기(Whisper)도 Claude 도 쓰지 않고, 영상 길이에 맞춰 샘플 큐시트를
직접 만들어 초안을 굽는다. 처음 설치했을 때 "캡컷에서 열리기는 하는가"만
30초 안에 확인하기 위한 것이다.

여기서 만들어진 초안이 캡컷에서 정상적으로 열린다면, 이후 문제는 캡컷
호환성이 아니라 받아쓰기나 큐시트 쪽이다.
"""
from __future__ import annotations

from .cuesheet import Caption, Cuesheet, EffectCue, SfxCue, TransitionCue, ZoomCue

#: 데모에서 훑어볼 자막 스타일과 애니메이션 조합
_SAMPLES: list[tuple[str, str, str, str]] = [
    # (문구, 스타일, 위치, 애니메이션)
    ("이게 되네?!", "reaction", "top", "pop"),
    ("(정적)", "narration", "upper", "fade"),
    ("결국 실패", "emphasis", "default", "zoom"),
    ("속으로 당황", "whisper", "center", "slide_up"),
    ("오후 3시", "situation", "lower", "slide_side"),
    ("한 글자씩", "reaction", "top", "typewriter"),
    ("흔들흔들", "reaction", "upper", "shake"),
    ("반짝", "emphasis", "default", "sparkle"),
]

_SFX_CYCLE = ["ding", "boing", "drum", "swoosh", "pop", "sparkle"]
_EFFECT_CYCLE = ["flash", "shake", "zoom_pulse", "glitch"]
_TRANSITION_CYCLE = ["dissolve", "flash_white", "fade_black", "blur"]


def build_cuesheet(
    total: float,
    *,
    boundaries: list[float] | None = None,
    sfx_names: list[str] | None = None,
    effect_names: list[str] | None = None,
    transition_names: list[str] | None = None,
) -> Cuesheet:
    """영상 길이에 맞춰 골고루 뿌린 샘플 큐시트를 만든다.

    실제 내용과는 아무 상관이 없다. 기능이 하나씩 다 들어가는지만 본다.
    """
    sheet = Cuesheet()
    if total < 2.0:
        return sheet

    sfx_pool = [n for n in _SFX_CYCLE if n in set(sfx_names or [])]
    effect_pool = [n for n in _EFFECT_CYCLE if n in set(effect_names or [])]
    transition_pool = [n for n in _TRANSITION_CYCLE if n in set(transition_names or [])]

    # 자막은 2초 간격으로, 스타일과 애니메이션을 돌아가며
    slots = max(1, min(len(_SAMPLES), int(total // 2)))
    for i in range(slots):
        start = 0.6 + i * (total - 1.2) / slots
        end = min(start + 1.4, total)
        if end - start < 0.5:
            break
        text, style, position, anim = _SAMPLES[i % len(_SAMPLES)]
        sheet.captions.append(Caption(start, end, text, style, position, anim))

        # 자막이 뜨는 순간에 효과음을 겹친다
        if sfx_pool:
            sheet.sfx.append(SfxCue(start, sfx_pool[i % len(sfx_pool)]))

    # 줌은 두 군데만
    for i in range(min(2, slots)):
        start = 1.0 + i * total / 2.4
        end = min(start + 1.6, total)
        if end - start >= 0.8:
            sheet.zooms.append(ZoomCue(start, end, 1.25 + 0.1 * i))

    # 화면효과 한두 개
    for i, name in enumerate(effect_pool[:2]):
        start = 1.2 + i * total / 3.0
        end = min(start + 0.5, total)
        if end > start:
            sheet.effects.append(EffectCue(start, end, name))

    # 컷 경계마다 전환을 돌아가며
    if transition_pool:
        for i, at in enumerate(boundaries or []):
            sheet.transitions.append(
                TransitionCue(at, transition_pool[i % len(transition_pool)], 0.5)
            )

    return sheet
