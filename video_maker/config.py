"""영상 생성 전역 설정.

해상도, 폰트, 색상, 경로 등을 한곳에서 관리한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _find_font() -> str:
    """시스템에서 한글 지원 폰트를 찾아 경로를 반환한다."""
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumSquareRoundB.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",  # macOS
        "C:/Windows/Fonts/malgunbd.ttf",  # Windows
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(
        "한글 폰트를 찾을 수 없습니다. NanumSquareRound 또는 WenQuanYi 폰트를 설치하세요."
    )


def _find_body_font(bold_path: str) -> str:
    """본문(나레이션)용 조금 얇은 폰트를 찾는다. 없으면 볼드와 동일."""
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumSquareRoundR.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return bold_path


@dataclass
class VideoConfig:
    """영상 출력 형식 설정."""

    # 화면 방향: "vertical"(9:16 쇼츠) 또는 "horizontal"(16:9 유튜브)
    orientation: str = "vertical"
    fps: int = 30

    # 폰트
    title_font: str = field(default_factory=_find_font)
    body_font: str = ""

    # 색상 (배경 그라데이션 두 끝 + 텍스트)
    bg_top: tuple[int, int, int] = (24, 26, 42)
    bg_bottom: tuple[int, int, int] = (58, 42, 92)
    accent: tuple[int, int, int] = (255, 214, 92)
    text_color: tuple[int, int, int] = (245, 245, 250)
    sub_color: tuple[int, int, int] = (200, 205, 220)

    # 장면 전환 시 페이드(초)
    fade: float = 0.35

    def __post_init__(self) -> None:
        if not self.body_font:
            self.body_font = _find_body_font(self.title_font)

    @property
    def size(self) -> tuple[int, int]:
        """(width, height) 픽셀."""
        if self.orientation == "horizontal":
            return (1920, 1080)
        return (1080, 1920)


# 작업 산출물 기본 저장 위치
OUTPUT_DIR = Path(os.environ.get("VIDEO_MAKER_OUT", "output"))
