"""나레이션 음성 합성 (TTS).

gTTS(구글 TTS)로 한국어 음성을 만든다. 네트워크 실패 시
글자 수에 비례한 무음 트랙으로 대체해 파이프라인이 끊기지 않게 한다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _duration_of(path: Path) -> float:
    """ffprobe로 오디오 길이(초)를 구한다."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _silent(path: Path, seconds: float) -> None:
    """지정 길이의 무음 mp3를 만든다."""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"anullsrc=channel_layout=mono:sample_rate=24000",
         "-t", f"{seconds:.2f}", "-q:a", "9", str(path)],
        capture_output=True,
    )


def synthesize(text: str, out_path: Path, *, lang: str = "ko") -> float:
    """text를 음성 파일(out_path, mp3)로 만들고 길이(초)를 반환한다."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from gtts import gTTS  # lazy import
        gTTS(text=text, lang=lang).save(str(out_path))
        dur = _duration_of(out_path)
        if dur > 0:
            return dur
        raise RuntimeError("gTTS 결과 길이 0")
    except Exception as e:  # noqa: BLE001
        # 대략 한국어 4.5자/초로 길이 추정, 최소 2초
        est = max(2.0, len(text) / 4.5)
        print(f"[narration] TTS 실패 → 무음 {est:.1f}s 대체: {e}")
        _silent(out_path, est)
        return est
