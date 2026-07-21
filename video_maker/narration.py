"""나레이션 음성 합성 (TTS) — 여러 엔진 지원 + 자동 폴백.

엔진 선택 우선순위:
    1) 인자 engine 또는 환경변수 VIDEO_MAKER_TTS 로 명시한 엔진
    2) "auto"(기본): openai → elevenlabs → edge → gtts → espeak 순으로 시도
모두 실패하면 글자 수에 맞춘 무음 트랙으로 대체해 파이프라인이 끊기지 않게 한다.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .tts_engines import ENGINES, AUTO_ORDER, TTSError


def _duration_of(path: Path) -> float:
    """ffprobe로 오디오 길이(초)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _silent(out_base: Path, seconds: float) -> Path:
    """지정 길이의 무음 mp3."""
    out = out_base.with_suffix(".mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
         "-t", f"{seconds:.2f}", "-q:a", "9", str(out)],
        capture_output=True,
    )
    return out


def _engine_order(engine: str) -> list[str]:
    if engine == "auto":
        return list(AUTO_ORDER)
    if engine in ENGINES:
        return [engine]
    raise ValueError(f"알 수 없는 TTS 엔진: {engine} (가능: {', '.join(ENGINES)}, auto)")


def synthesize(text: str, out_base: Path, *, engine: str | None = None) -> tuple[Path, float]:
    """text를 음성 파일로 만들고 (실제경로, 길이초)를 반환한다.

    out_base: 확장자 없는 기본 경로. 엔진이 .mp3/.wav 등을 붙여 반환한다.
    """
    out_base.parent.mkdir(parents=True, exist_ok=True)
    if engine is None:
        engine = os.environ.get("VIDEO_MAKER_TTS", "auto")

    last_err: Exception | None = None
    for name in _engine_order(engine):
        try:
            path = ENGINES[name](text, out_base)
            dur = _duration_of(path)
            if dur > 0:
                return path, dur
            last_err = TTSError(f"{name}: 길이 0")
        except Exception as e:  # noqa: BLE001
            last_err = e
            # auto가 아니라 특정 엔진을 콕 집었으면 조용히 폴백하지 않고 알린다
            if engine != "auto":
                print(f"[narration] '{name}' 실패: {e}")

    # 전부 실패 → 무음 대체
    est = max(2.0, len(text) / 4.5)
    print(f"[narration] 모든 TTS 실패 → 무음 {est:.1f}s 대체 (마지막 오류: {last_err})")
    path = _silent(out_base, est)
    return path, est
