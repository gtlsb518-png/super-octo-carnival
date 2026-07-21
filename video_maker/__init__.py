"""주제 -> 대본 -> 나레이션 -> 영상 자동 생성 파이프라인."""

from .config import VideoConfig
from .script_gen import Script, Scene, generate_script
from .video import build_video

__all__ = ["VideoConfig", "Script", "Scene", "generate_script", "build_video"]
