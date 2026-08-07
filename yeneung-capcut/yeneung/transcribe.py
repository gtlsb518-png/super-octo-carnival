"""faster-whisper 로 대사를 받아쓴다."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import TranscribeConfig


@dataclass
class Utterance:
    """대사 한 덩어리."""

    start: float
    end: float
    text: str


def transcribe(video_path: str | Path, cfg: TranscribeConfig) -> list[Utterance]:
    """영상에서 대사를 뽑는다. 모델은 최초 1회 자동 다운로드된다."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - 설치 안내용
        raise RuntimeError(
            "faster-whisper 가 설치되어 있지 않습니다. `pip install faster-whisper`"
        ) from exc

    device = cfg.device
    if device == "auto":
        device = "cuda" if _has_cuda() else "cpu"

    compute_type = cfg.compute_type
    if compute_type == "default":
        compute_type = "float16" if device == "cuda" else "int8"

    model = WhisperModel(cfg.model, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(video_path),
        language=cfg.language,
        beam_size=cfg.beam_size,
        vad_filter=cfg.vad_filter,
    )

    out: list[Utterance] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append(Utterance(start=float(seg.start), end=float(seg.end), text=text))
    return out


def _has_cuda() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def save(utterances: list[Utterance], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps([asdict(u) for u in utterances], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load(path: str | Path) -> list[Utterance]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Utterance(**item) for item in raw]


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(utterances: list[Utterance], path: str | Path) -> Path:
    """캡컷이 그대로 읽을 수 있는 SRT 파일을 쓴다."""
    lines: list[str] = []
    for i, u in enumerate(utterances, start=1):
        if u.end <= u.start:
            continue
        lines += [str(i), f"{_srt_time(u.start)} --> {_srt_time(u.end)}", u.text, ""]
    p = Path(path)
    p.write_text("\n".join(lines), encoding="utf-8")
    return p
