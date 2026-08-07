"""영상 정보 읽기와 오디오 디코딩.

ffmpeg CLI 를 설치하지 않아도 되도록, 영상 메타데이터는 pymediainfo 로
(pycapcut 이 이미 쓰는 라이브러리), 오디오 파형은 PyAV 로 읽는다.
PyAV 는 faster-whisper 가 이미 의존하므로 추가 설치가 없다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class VideoInfo:
    path: Path
    duration: float          # 초
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: str | Path) -> VideoInfo:
    """영상의 길이·해상도·프레임레이트를 읽는다."""
    import pymediainfo

    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {p}")

    info = pymediainfo.MediaInfo.parse(str(p))
    if not info.video_tracks:
        raise ValueError(f"영상 트랙이 없습니다: {p}")

    vt = info.video_tracks[0]
    if vt.duration is None:
        raise ValueError(f"영상 길이를 읽을 수 없습니다: {p}")

    fps = 30.0
    if vt.frame_rate:
        try:
            fps = float(vt.frame_rate)
        except (TypeError, ValueError):
            pass

    return VideoInfo(
        path=p,
        duration=float(vt.duration) / 1000.0,
        width=int(vt.width),
        height=int(vt.height),
        fps=fps,
        has_audio=bool(info.audio_tracks),
    )


def decode_audio_mono(path: str | Path, sample_rate: int = 16_000) -> np.ndarray:
    """오디오를 모노 float32 파형으로 디코딩한다.

    오디오 트랙이 없으면 빈 배열을 돌려준다.
    """
    import av

    p = str(Path(path).resolve())
    chunks: list[np.ndarray] = []

    with av.open(p) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)

        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"

        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=sample_rate
        )
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                arr = resampled.to_ndarray().reshape(-1)
                chunks.append(arr.astype(np.float32) / 32768.0)

        # 리샘플러 내부 버퍼 비우기
        for resampled in resampler.resample(None):
            arr = resampled.to_ndarray().reshape(-1)
            chunks.append(arr.astype(np.float32) / 32768.0)

    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)
