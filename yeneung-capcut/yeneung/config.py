"""설정 로딩 및 기본값.

모든 튜닝 파라미터는 config.toml 로 덮어쓸 수 있습니다.
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------- 컷 편집


@dataclass
class CutConfig:
    """무음 구간 자동 컷 파라미터."""

    enabled: bool = True
    # 이 값(dBFS)보다 조용하면 무음으로 간주
    threshold_db: float = -38.0
    # 이 길이(초) 이상 이어진 무음만 잘라냄. 짧은 숨은 남겨야 말이 자연스러움
    min_silence: float = 0.60
    # 컷 앞뒤로 남기는 여유(초). 말꼬리가 잘리는 걸 방지
    padding: float = 0.12
    # 이보다 짧은 조각은 따로 남기지 않고 앞 조각에 흡수
    min_keep: float = 0.30
    # 영상 맨 앞/뒤 무음은 이 값 이상이면 잘라냄
    trim_edges: bool = True


# ---------------------------------------------------------------- 받아쓰기


@dataclass
class TranscribeConfig:
    """faster-whisper 설정."""

    model: str = "large-v3"
    language: str = "ko"
    device: str = "auto"
    compute_type: str = "default"
    # VAD 필터로 헛인식(무음에서 환청) 억제
    vad_filter: bool = True
    beam_size: int = 5


# ---------------------------------------------------------------- 큐시트


@dataclass
class CuesheetConfig:
    """Claude 로 예능 큐시트를 생성할 때의 설정."""

    model: str = "claude-opus-5"
    effort: str = "high"
    # 한 번에 보낼 대사 줄 수. 길면 맥락은 좋지만 응답이 길어짐
    chunk_lines: int = 120
    # 앞 청크의 마지막 몇 줄을 맥락으로 겹쳐 보낼지
    overlap_lines: int = 8
    # low | normal | high — 자막/효과를 얼마나 촘촘히 넣을지
    density: str = "normal"
    max_tokens: int = 32000
    # 프로그램 톤. 프롬프트에 그대로 들어감
    tone: str = "밝고 빠른 템포의 관찰 예능"


# ---------------------------------------------------------------- 자막 스타일


@dataclass
class CaptionStyle:
    """예능 자막 한 종류의 겉모습."""

    size: float
    color: tuple[float, float, float]
    border_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    border_width: float = 60.0
    bold: bool = True
    # 화면 세로 위치. 단위는 '캔버스 높이의 절반', 양수가 위쪽
    position_y: float = 0.55
    # 등장 애니메이션 (pycapcut TextIntro 이름). None 이면 없음
    intro: str | None = "弹出"
    intro_ms: int = 400
    # 반복 애니메이션 (pycapcut TextLoopAnim 이름)
    loop: str | None = None


@dataclass
class SubtitleConfig:
    """대사 자막(하단 기본 자막) 설정."""

    enabled: bool = True
    size: float = 6.0
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    border_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    border_width: float = 45.0
    bold: bool = False
    position_y: float = -0.78
    # 자막 한 줄 최대 폭(캔버스 대비 비율)
    max_line_width: float = 0.82


# ---------------------------------------------------------------- 효과음 / 줌


@dataclass
class SfxConfig:
    enabled: bool = True
    # 효과음 폴더. 상대경로는 프로젝트 루트 기준
    library: str = "assets/sfx"
    # 기본 음량 (0.0 ~ 1.0)
    volume: float = 0.65


@dataclass
class ZoomConfig:
    enabled: bool = True
    # 줌 인/아웃에 걸리는 시간(초)
    ramp: float = 0.25
    # LLM 이 요청한 배율을 이 범위로 제한
    min_scale: float = 1.05
    max_scale: float = 1.60


@dataclass
class EffectConfig:
    enabled: bool = True


# ---------------------------------------------------------------- 전체


@dataclass
class Config:
    cut: CutConfig = field(default_factory=CutConfig)
    transcribe: TranscribeConfig = field(default_factory=TranscribeConfig)
    cuesheet: CuesheetConfig = field(default_factory=CuesheetConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)
    sfx: SfxConfig = field(default_factory=SfxConfig)
    zoom: ZoomConfig = field(default_factory=ZoomConfig)
    effect: EffectConfig = field(default_factory=EffectConfig)
    # 캡컷 초안 폴더. None 이면 자동 탐지
    draft_dir: str | None = None
    # 자막 스타일 프리셋
    styles: dict[str, CaptionStyle] = field(default_factory=lambda: dict(DEFAULT_STYLES))


#: 예능 자막 기본 프리셋. config.toml 의 [styles.<이름>] 으로 덮어쓸 수 있음
DEFAULT_STYLES: dict[str, CaptionStyle] = {
    # 감탄·리액션. 화면 위쪽에 크게
    "reaction": CaptionStyle(
        size=13.0, color=(1.0, 0.92, 0.23), border_width=65.0,
        position_y=0.58, intro="弹出", intro_ms=350,
    ),
    # 핵심 포인트 강조. 가장 크고 강함
    "emphasis": CaptionStyle(
        size=15.0, color=(1.0, 0.36, 0.24), border_width=70.0,
        position_y=0.30, intro="放大震动", intro_ms=400, loop="心跳频闪",
    ),
    # 제작진 시점의 상황 설명
    "narration": CaptionStyle(
        size=9.0, color=(1.0, 1.0, 1.0), border_width=50.0,
        position_y=0.72, intro="渐显", intro_ms=300, bold=False,
    ),
    # 속마음·작은 목소리
    "whisper": CaptionStyle(
        size=8.0, color=(0.75, 0.80, 0.90), border_width=45.0,
        position_y=0.42, intro="向上弹入", intro_ms=300, bold=False,
    ),
    # 상황 자막 CG (장소/시간/설명)
    "situation": CaptionStyle(
        size=10.0, color=(0.40, 0.95, 1.0), border_width=55.0,
        position_y=0.66, intro="向右滑动", intro_ms=350,
    ),
}

#: 큐시트에서 쓸 수 있는 자막 위치 이름 → position_y 덮어쓰기 값
POSITION_OVERRIDES: dict[str, float] = {
    "top": 0.72,
    "upper": 0.45,
    "center": 0.0,
    "lower": -0.45,
}


def _as_rgb(v: Any, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    """[r,g,b] 리스트 또는 '#RRGGBB' 문자열을 0~1 튜플로."""
    if isinstance(v, str) and v.startswith("#") and len(v) == 7:
        return tuple(int(v[i : i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return (float(v[0]), float(v[1]), float(v[2]))
    return fallback


def _apply(obj: Any, table: dict[str, Any], path: str) -> Any:
    """dataclass 에 dict 값을 필드 단위로 덮어씌운다. 모르는 키는 에러."""
    known = {f.name for f in obj.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    updates: dict[str, Any] = {}
    for key, value in table.items():
        if key not in known:
            raise ValueError(f"설정 오류: [{path}] 에 알 수 없는 항목 '{key}'")
        current = getattr(obj, key)
        if isinstance(current, tuple) and len(current) == 3:
            updates[key] = _as_rgb(value, current)
        else:
            updates[key] = value
    return replace(obj, **updates)


def load_config(path: str | os.PathLike[str] | None) -> Config:
    """config.toml 을 읽어 Config 를 만든다. path 가 None 이면 기본값."""
    cfg = Config()
    if path is None:
        return cfg

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {p}")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)

    simple = {
        "cut": "cut", "transcribe": "transcribe", "cuesheet": "cuesheet",
        "subtitle": "subtitle", "sfx": "sfx", "zoom": "zoom", "effect": "effect",
    }
    for section, attr in simple.items():
        if section in raw:
            setattr(cfg, attr, _apply(getattr(cfg, attr), raw[section], section))

    if "draft_dir" in raw:
        cfg.draft_dir = raw["draft_dir"]

    for name, table in (raw.get("styles") or {}).items():
        base = cfg.styles.get(name, CaptionStyle(size=10.0, color=(1.0, 1.0, 1.0)))
        cfg.styles[name] = _apply(base, table, f"styles.{name}")

    return cfg


def find_capcut_draft_dir() -> Path | None:
    """윈도우 캡컷 초안 폴더를 자동 탐지."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates += [
                Path(local) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
                Path(local) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft",
            ]
    elif sys.platform == "darwin":
        home = Path.home()
        candidates += [
            home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
            home / "Library" / "Containers" / "com.lemon.lvoverseas" / "Data"
            / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
        ]
    for c in candidates:
        if c.is_dir():
            return c
    return None
