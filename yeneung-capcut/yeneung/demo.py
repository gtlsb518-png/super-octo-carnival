"""캡컷 호환성만 빠르게 확인하는 데모.

받아쓰기(Whisper)도 Claude 도 쓰지 않고, 영상 길이에 맞춰 샘플 큐시트를
직접 만들어 초안을 굽는다. 처음 설치했을 때 "캡컷에서 열리기는 하는가"만
30초 안에 확인하기 위한 것이다.

여기서 만들어진 초안이 캡컷에서 정상적으로 열린다면, 이후 문제는 캡컷
호환성이 아니라 받아쓰기나 큐시트 쪽이다.
"""
from __future__ import annotations

from .cuesheet import Caption, Cuesheet, EffectCue, SfxCue, TransitionCue, ZoomCue

#: 데모 자막은 **자기가 무엇을 검사 중인지** 말하도록 적는다.
#: 그래야 캡컷에서 열었을 때 위치가 뒤집혔는지, 어느 스타일이 이상한지를
#: 눈으로 바로 짚을 수 있다.
_SAMPLES: list[tuple[str, str, str, str]] = [
    # (문구, 스타일, 위치, 애니메이션)
    ("① 맨위 top", "reaction", "top", "pop"),
    ("② 중상 upper", "narration", "upper", "fade"),
    ("③ 한가운데 center", "whisper", "center", "slide_up"),
    ("④ 중하 lower", "situation", "lower", "slide_side"),
    ("⑤ 기본위치 강조", "emphasis", "default", "zoom"),
    ("⑥ 타자기 효과", "reaction", "top", "typewriter"),
    ("⑦ 흔들림 효과", "reaction", "upper", "shake"),
    ("⑧ 반짝 효과", "emphasis", "default", "sparkle"),
]

#: 캡컷에서 열었을 때 확인할 항목. CLI 가 그대로 출력한다.
CHECKLIST = [
    "자막 ①②③④ 가 위에서 아래 순서로 떠 있는가 (뒤집혔으면 알려주세요)",
    "한글이 □□□ 로 깨지지 않는가 (깨지면 폰트 문제)",
    "자막마다 등장 방식이 다른가 (①툭 ②서서히 ③아래서 ⑥한글자씩 ⑦흔들림)",
    "스타일별로 크기·색이 다른가 (⑤가 가장 크고 붉은 계열)",
    "영상이 여러 클립으로 잘려 있는가",
    "효과음이 자막 뜰 때 같이 들리는가",
    "줌 클립에서 화면이 확대됐다 돌아오는가",
    "컷 경계에 전환(디졸브 등)이 걸려 있는가",
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

    # 자막은 앞에서부터 순서대로. ①②③④ 가 차례로 떠야 위치를 확인할 수 있다.
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
