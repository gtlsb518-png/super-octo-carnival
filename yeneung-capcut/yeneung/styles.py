"""논리 이름 → 캡컷 내부 리소스 이름 매핑.

큐시트에는 'flash', 'shake' 같은 읽기 쉬운 이름만 쓰고,
여기서 캡컷의 실제 효과/애니메이션 이름으로 바꾼다.
캡컷 버전에 따라 일부 리소스가 없을 수 있으므로 후보를 여러 개 두고
실제로 존재하는 첫 번째를 고른다.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

#: 화면 효과: 논리 이름 → 캡컷 VideoSceneEffectType 후보(앞에서부터 시도)
EFFECT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "flash":     ("闪白", "闪白_II", "矩形闪白"),
    "flash_dark": ("闪黑", "闪黑_II", "速切闪黑"),
    "shake":     ("抖动", "轻微抖动", "震动"),
    "glitch":    ("RGB_故障", "色差故障_II", "信号不好"),
    "heartbeat": ("心跳", "心跳_II"),
    "zoom_pulse": ("轻微放大", "变焦推镜", "镜头变焦"),
    "sparkle":   ("kirakira", "星光绽放", "模糊星光"),
    "bw":        ("黑白影调", "黑白漫画", "黑白胶片"),
    "comic":     ("彩色漫画", "复古漫画", "三格漫画"),
    "freeze":    ("相片定格", "定格闪烁", "故障定格"),
}

#: 장면 전환: 논리 이름 → 캡컷 TransitionType 후보
#: 컷과 컷 사이에만 들어간다. 같은 장면을 쪼갠 자리에는 넣지 않는다.
TRANSITION_CANDIDATES: dict[str, tuple[str, ...]] = {
    "dissolve":    ("叠化", "色彩溶解", "丝滑渐隐"),      # 가장 무난한 디졸브
    "fade_black":  ("闪黑", "闪黑_II", "微震闪黑"),        # 검게 떨어졌다 올라옴
    "flash_white": ("闪白", "闪白_II"),                    # 하얗게 번쩍
    "blur":        ("转场_模糊", "横向模糊", "竖向模糊"),  # 흐려졌다 선명해짐
    "slide":       ("滑动", "向右滑动", "交错滑动"),        # 밀려 들어옴
    "push":        ("推近", "拉近"),                        # 확 다가감
    "pull":        ("拉远", "拉伸"),                        # 확 물러남
    "glitch":      ("故障", "信号故障", "彩色故障"),        # 신호 튐
    "spin":        ("中轴旋转", "顺时针旋转", "三维旋转"),  # 돌아가며 전환
}

#: 자막 등장 애니메이션: 큐시트에서 쓰는 논리 이름 → 캡컷 TextIntro 후보.
#: 스타일에 박힌 기본값 대신 장면마다 골라 쓸 수 있게 한다.
CAPTION_ANIM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pop":        ("弹出", "弹入", "放大"),               # 툭 튀어나옴 (기본)
    "fade":       ("渐显", "魔幻渐显"),                    # 조용히 떠오름
    "slide_up":   ("向上弹入", "向上滑动", "滑入上升"),
    "slide_side": ("向右滑动", "向右缓入", "环绕滑入"),
    "typewriter": ("打字机", "打字机_I", "随机打字机"),    # 한 글자씩
    "zoom":       ("放大震动", "放大旋转", "波浪放大"),    # 크게 터짐
    "shake":      ("抖动甩入", "抖动拼凑", "故障弹跳"),    # 흔들며 등장
    "glitch":     ("故障", "乱码故障", "故障翻转"),
    "sparkle":    ("星光闪闪", "辉光入场", "金光弹出"),
}

#: 자막 등장 애니메이션 후보
INTRO_CANDIDATES: dict[str, tuple[str, ...]] = {
    "弹出":      ("弹出", "弹入", "放大"),
    "放大震动":  ("放大震动", "放大旋转", "放大"),
    "渐显":      ("渐显", "魔幻渐显", "模糊"),
    "向上弹入":  ("向上弹入", "向上滑动", "弹入"),
    "向右滑动":  ("向右滑动", "向右缓入", "滑入上升"),
    "打字机":    ("打字机", "打字机_I", "随机打字机"),
}

#: 자막 반복 애니메이션 후보
LOOP_CANDIDATES: dict[str, tuple[str, ...]] = {
    "心跳频闪": ("心跳频闪", "晃动", "闪动脉冲"),
    "晃动":     ("晃动", "投影颤抖", "调皮"),
    "波浪":     ("波浪", "波浪_II", "漂浮"),
}


def _resolve(enum_cls: Any, candidates: tuple[str, ...]) -> Any | None:
    """후보 중 이 캡컷 버전에 실제로 있는 첫 항목을 반환."""
    members = enum_cls.__members__
    for name in candidates:
        if name in members:
            return members[name]
    return None


@lru_cache(maxsize=None)
def effect_type(name: str) -> Any | None:
    """'flash' 같은 논리 이름 → VideoSceneEffectType 멤버 (없으면 None)."""
    from pycapcut import VideoSceneEffectType

    return _resolve(VideoSceneEffectType, EFFECT_CANDIDATES.get(name, (name,)))


@lru_cache(maxsize=None)
def intro_type(name: str | None) -> Any | None:
    if not name:
        return None
    from pycapcut import TextIntro

    return _resolve(TextIntro, INTRO_CANDIDATES.get(name, (name,)))


@lru_cache(maxsize=None)
def loop_type(name: str | None) -> Any | None:
    if not name:
        return None
    from pycapcut import TextLoopAnim

    return _resolve(TextLoopAnim, LOOP_CANDIDATES.get(name, (name,)))


@lru_cache(maxsize=None)
def transition_type(name: str) -> Any | None:
    """'dissolve' 같은 논리 이름 → TransitionType 멤버 (없으면 None)."""
    from pycapcut import TransitionType

    return _resolve(TransitionType, TRANSITION_CANDIDATES.get(name, (name,)))


@lru_cache(maxsize=None)
def caption_anim_type(name: str | None) -> Any | None:
    """'pop' 같은 논리 이름 → TextIntro 멤버 (없으면 None)."""
    if not name:
        return None
    from pycapcut import TextIntro

    return _resolve(TextIntro, CAPTION_ANIM_CANDIDATES.get(name, (name,)))


def available_effects() -> list[str]:
    """이 캡컷 설치본에서 실제로 쓸 수 있는 논리 효과 이름들."""
    return [name for name in EFFECT_CANDIDATES if effect_type(name) is not None]


def available_transitions() -> list[str]:
    return [name for name in TRANSITION_CANDIDATES if transition_type(name) is not None]


def available_caption_anims() -> list[str]:
    return [name for name in CAPTION_ANIM_CANDIDATES if caption_anim_type(name) is not None]
