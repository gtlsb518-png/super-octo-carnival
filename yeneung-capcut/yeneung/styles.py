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
#: 캡컷에는 1500가지가 넘게 있지만, 예능에서 실제로 쓸 만한 것만 골랐다.
EFFECT_CANDIDATES: dict[str, tuple[str, ...]] = {
    # --- 번쩍임: 놀람, 한 방 ---
    "flash":     ("闪白", "闪白_II", "矩形闪白"),
    "flash_dark": ("闪黑", "闪黑_II", "速切闪黑"),
    "strobe":    ("闪屏", "闪光灯", "交叉震闪"),
    "lightning": ("闪电", "闪光灯", "闪屏"),
    # --- 흔들림: 충격, 당황 ---
    "shake":     ("抖动", "轻微抖动", "震动"),
    "shake_soft": ("轻微抖动", "抖动", "抖动运镜"),
    "impact":    ("冲击波", "层层冲击", "动感猛震"),
    "quake":     ("动感猛震", "震动", "多屏抖动"),
    "ripple":    ("水波纹", "波纹抖动", "波纹卡顿"),
    # --- 고장: 어이없음, 정색 ---
    "glitch":    ("RGB_故障", "色差故障_II", "信号不好"),
    "noise":     ("荧幕噪点", "噪点", "黑色噪点"),
    "vhs":       ("黑白VHS", "复古_DV", "隔行扫描"),
    "oldtv":     ("老电视卡顿", "隔行扫描", "荧幕噪点_II"),
    "retro":     ("复古_DV", "复古发光", "复古紫调"),
    # --- 반짝임: 성공, 감동 ---
    "sparkle":   ("kirakira", "星光绽放", "模糊星光"),
    "starzoom":  ("星光变焦", "星光绽放", "复古星光"),
    "glow":      ("复古发光", "震动发光", "失焦光斑"),
    "firework":  ("烟花_III", "新年烟花", "变焦烟花"),
    "confetti":  ("彩带球", "新年烟花_II", "烟花_III"),
    # --- 감정 표현 ---
    "heart":     ("爱心放射", "荧光爱心", "爱心Kira"),
    "question":  ("满屏问号", "黑白漫画", "三格漫画"),
    "heartbeat": ("心跳", "心跳_II"),
    # --- 만화 / 회상 ---
    "comic":     ("彩色漫画", "复古漫画", "三格漫画"),
    "comic_bw":  ("黑白漫画", "黑白线描", "黑白漫画_II"),
    "comic3":    ("三格漫画", "彩色漫画", "复古漫画"),
    "rush":      ("漫画冲刺", "冲击波", "层层冲击"),
    "dream":     ("梦境", "梦境_III", "梦回回响"),
    # --- 정지 / 흑백 ---
    "bw":        ("黑白影调", "黑白胶片", "黑白漫画"),
    "freeze":    ("相片定格", "定格闪烁", "故障定格"),
    "freeze_glitch": ("故障定格", "定格闪烁", "相片定格"),
    "zoom_pulse": ("轻微放大", "变焦推镜", "镜头变焦"),
}

#: 장면 전환: 논리 이름 → 캡컷 TransitionType 후보
#: 컷과 컷 사이에만 들어간다. 같은 장면을 쪼갠 자리에는 넣지 않는다.
TRANSITION_CANDIDATES: dict[str, tuple[str, ...]] = {
    # --- 무난한 것: 대부분의 컷에 쓴다 ---
    "dissolve":    ("叠化", "色彩溶解", "丝滑渐隐"),      # 가장 무난한 디졸브
    "color_melt":  ("色彩溶解", "叠化", "眩光叠化"),
    "glare":       ("眩光叠化", "叠化", "亮点模糊"),
    "blur":        ("横向模糊", "转场_模糊", "竖向模糊"),  # 흐려졌다 선명해짐
    "blur_v":      ("竖向模糊", "竖向模糊_II", "横向模糊"),
    "blur_soft":   ("亮点模糊", "模糊细闪", "泡泡模糊"),
    "bubble":      ("泡泡模糊", "亮点模糊", "模糊细闪"),
    # --- 번쩍: 장면이 확 바뀔 때 ---
    "fade_black":  ("闪黑", "闪黑_II", "微震闪黑"),        # 검게 떨어졌다 올라옴
    "flash_white": ("闪白", "闪白_II"),                    # 하얗게 번쩍
    "sparkle":     ("模糊细闪", "星星变焦", "亮点模糊"),
    # --- 움직임 ---
    "slide":       ("滑动", "向右滑动", "交错滑动"),        # 밀려 들어옴
    "push":        ("推近", "拉近"),                        # 확 다가감
    "pull":        ("拉远", "拉伸"),                        # 확 물러남
    "stretch":     ("拉伸", "拉伸_ll", "向右拉伸"),
    "zoom_star":   ("星星变焦", "放射", "推近"),
    "radial":      ("放射", "星星变焦", "推近"),
    "spin":        ("中轴旋转", "顺时针旋转", "三维旋转"),  # 돌아가며 전환
    # --- 닦아내기 ---
    "wipe_up":     ("向上擦除", "向下擦除", "渐变擦除"),
    "wipe_down":   ("向下擦除", "向上擦除", "渐变擦除"),
    "wipe_left":   ("向左擦除", "向右擦除", "渐变擦除"),
    "wipe_right":  ("向右擦除", "向左擦除", "渐变擦除"),
    "brush":       ("画笔擦除", "渐变擦除", "向右擦除"),
    "gradient":    ("渐变擦除", "画笔擦除", "叠化"),
    "curtain":     ("横向拉幕", "竖向拉幕", "向右擦除"),
    "page":        ("翻页", "画笔擦除", "叠化"),
    # --- 고장 ---
    "glitch":      ("故障", "信号故障", "彩色故障"),        # 신호 튐
    "glitch_hard": ("故障爆闪", "故障", "彩色故障"),
    "glitch_color": ("彩色故障", "色差故障", "故障"),
}

#: 자막 등장 애니메이션: 큐시트에서 쓰는 논리 이름 → 캡컷 TextIntro 후보.
#: 스타일에 박힌 기본값 대신 장면마다 골라 쓸 수 있게 한다.
CAPTION_ANIM_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pop":        ("弹出", "弹入", "放大"),               # 툭 튀어나옴 (기본)
    "bounce":     ("弹性伸缩_II", "交响弹动", "弹出"),
    "swing":      ("左移弹动", "交响弹动", "弹出"),
    "cute":       ("可爱悦动", "俏皮旋入", "弹出"),
    "fade":       ("渐显", "魔幻渐显"),                    # 조용히 떠오름
    "dissolve":   ("溶解_入场", "渐显", "向下溶解"),
    "drop":       ("向下溶解", "滑动上升", "渐显"),
    "blur_in":    ("模糊", "扭曲模糊", "向左模糊"),
    "slide_up":   ("向上弹入", "向上滑动", "滑入上升"),
    "rise":       ("滑动上升", "向上弹入", "向上滑动"),
    "slide_side": ("向右滑动", "向右缓入", "环绕滑入"),
    "typewriter": ("打字机", "打字机_I", "随机打字机"),    # 한 글자씩
    "letter":     ("逐字", "打字机", "打字机_II"),
    "karaoke":    ("卡拉OK", "逐字", "打字机"),
    "zoom":       ("放大震动", "放大旋转", "波浪放大"),    # 크게 터짐
    "shake":      ("抖动甩入", "抖动拼凑", "故障弹跳"),    # 흔들며 등장
    "glitch":     ("故障", "乱码故障", "故障翻转"),
    "glitch_type": ("故障打字机", "故障弹跳", "故障"),
    "sparkle":    ("星光闪闪", "辉光入场", "金光弹出"),
    "glow":       ("辉光入场", "荧光聚合", "辉光飞入"),
    "neon":       ("炫彩弹入", "炫彩翻页", "辉光入场"),
    "scan":       ("光线扫描", "扫光入场", "辉光入场"),
    "spin_in":    ("旋入", "俏皮旋入", "逆转回旋"),
    "flip":       ("翻转入场", "侧身翻转", "空翻"),
    "page_in":    ("翻页入场", "漂浮翻页", "翻页渐显"),
    "wave":       ("波浪弹入", "波浪律动", "波浪翻转"),
    "dash":       ("冲屏位移", "甩出", "左移弹动"),
}

#: 자막 **퇴장** 애니메이션. 없으면 자막이 뚝 끊기듯 사라진다.
CAPTION_OUTRO_CANDIDATES: dict[str, tuple[str, ...]] = {
    "fade":       ("渐隐", "溶解_出场", "模糊"),           # 기본
    "dissolve":   ("溶解_出场", "渐隐", "向上溶解"),
    "blur":       ("模糊", "扭曲模糊", "渐隐"),
    "rise":       ("向上溶解", "渐隐", "溶解_出场"),
    "drop":       ("滑动下落", "向下滑动", "渐隐"),
    "slide_side": ("向右滑动", "向左滑动", "渐隐"),
    "spin":       ("旋出", "时空旋转", "渐隐"),
    "glitch":     ("故障", "信号失真", "扭曲模糊"),
    "signal":     ("信号失真", "故障", "格式化"),
    "typewriter": ("打字机", "故障打字机", "渐隐"),
    "spring":     ("弹簧", "展开", "渐隐"),
    "ripple":     ("涟漪", "水墨晕开", "渐隐"),
    "trail":      ("拖尾", "渐隐", "模糊"),
    "ink":        ("水墨晕开", "涟漪", "渐隐"),
}

#: 자막 등장 애니메이션 후보 (스타일 프리셋이 쓰는 캡컷 원래 이름)
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
    "颤抖_II":  ("颤抖_II", "投影颤抖", "晃动"),
    "故障闪动": ("故障闪动", "色差故障", "闪动脉冲"),
    "呼喊":     ("呼喊", "呐喊", "随机弹跳"),
    "漂浮":     ("漂浮", "漂浮发光", "波浪"),
    "像素跳动": ("像素跳动", "随机弹跳", "调皮"),
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


@lru_cache(maxsize=None)
def caption_outro_type(name: str | None) -> Any | None:
    """'fade' 같은 논리 이름 → TextOutro 멤버 (없으면 None)."""
    if not name:
        return None
    from pycapcut import TextOutro

    return _resolve(TextOutro, CAPTION_OUTRO_CANDIDATES.get(name, (name,)))


def available_effects() -> list[str]:
    """이 캡컷 설치본에서 실제로 쓸 수 있는 논리 효과 이름들."""
    return [name for name in EFFECT_CANDIDATES if effect_type(name) is not None]


def available_transitions() -> list[str]:
    return [name for name in TRANSITION_CANDIDATES if transition_type(name) is not None]


def available_caption_anims() -> list[str]:
    return [name for name in CAPTION_ANIM_CANDIDATES if caption_anim_type(name) is not None]


def available_caption_outros() -> list[str]:
    return [name for name in CAPTION_OUTRO_CANDIDATES if caption_outro_type(name) is not None]
