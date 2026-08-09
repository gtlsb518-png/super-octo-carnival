"""내 효과음 폴더를 읽어 매니페스트를 만들어 준다.

파일 이름만으로는 Claude 가 언제 그 소리를 쓸지 알 수 없다. 그래서 실제로
파형을 열어 길이·밝기·타격감 같은 성질을 재고, 파일 이름과 함께 넘겨
설명을 짓게 한다. 그 설명이 큐시트를 만들 때의 판단 근거가 된다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .media import decode_audio_mono

_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
_SR = 22_050
_ANALYSIS_SECONDS = 10.0


@dataclass
class Probe:
    """효과음 하나에서 측정한 성질."""

    name: str
    relative_path: str
    duration: float
    brightness: float      # 스펙트럼 무게중심 (Hz)
    attack: float          # 에너지 무게중심 위치 (0=앞쪽 타격, 1=뒤로 갈수록 커짐)
    tonality: float        # 0=노이즈에 가까움, 1=음정이 뚜렷함
    pitch_move: float      # 양수면 올라가는 소리, 음수면 내려가는 소리

    def describe(self) -> str:
        """사람이(그리고 Claude 가) 읽을 수 있는 한 줄 요약."""
        bits = [f"{self.duration:.2f}초"]
        bits.append("아주 밝음" if self.brightness > 4000 else
                    "밝음" if self.brightness > 2000 else
                    "중간" if self.brightness > 700 else "낮고 묵직함")
        bits.append("타격형(앞에서 터짐)" if self.attack < 0.35 else
                    "점점 커짐" if self.attack > 0.6 else "고른 편")
        bits.append("음정 뚜렷" if self.tonality > 0.55 else
                    "노이즈성" if self.tonality < 0.3 else "혼합")
        if self.pitch_move > 0.15:
            bits.append("음이 올라감")
        elif self.pitch_move < -0.15:
            bits.append("음이 내려감")
        return ", ".join(bits)


def find_audio_files(root: str | Path) -> list[Path]:
    """폴더를 재귀적으로 훑어 오디오 파일을 찾는다."""
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(
        p for p in base.rglob("*")
        if p.is_file() and p.suffix.lower() in _AUDIO_EXT
    )


def probe_file(path: Path, root: Path) -> Probe | None:
    """파형을 열어 성질을 잰다. 못 읽는 파일은 None."""
    try:
        samples = decode_audio_mono(path, _SR)
    except Exception:
        return None
    if samples.size < _SR // 20:  # 0.05초 미만이면 분석 의미 없음
        return None

    duration = len(samples) / _SR
    clip = samples[: int(_SR * _ANALYSIS_SECONDS)]

    spec = np.abs(np.fft.rfft(clip))
    freqs = np.fft.rfftfreq(len(clip), 1.0 / _SR)
    total = float(spec.sum()) or 1.0
    brightness = float((spec * freqs).sum() / total)

    envelope = np.abs(clip)
    t = np.arange(len(clip)) / _SR
    attack = float((envelope * t).sum() / (float(envelope.sum()) or 1.0) / max(len(clip) / _SR, 1e-9))

    # 스펙트럼 평탄도: 기하평균/산술평균. 노이즈일수록 1 에 가깝다.
    power = spec**2 + 1e-12
    flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
    tonality = float(np.clip(1.0 - flatness**0.25, 0.0, 1.0))

    # 앞/뒤 절반의 무게중심 주파수를 비교해 음이 오르내리는지 본다
    half = len(clip) // 2
    pitch_move = 0.0
    if half > 128:
        def centroid(chunk: np.ndarray) -> float:
            s = np.abs(np.fft.rfft(chunk))
            f = np.fft.rfftfreq(len(chunk), 1.0 / _SR)
            return float((s * f).sum() / (float(s.sum()) or 1.0))

        first, second = centroid(clip[:half]), centroid(clip[half:])
        pitch_move = float(np.clip((second - first) / max(first, 1.0), -1.0, 1.0))

    return Probe(
        name=path.stem,
        relative_path=path.relative_to(root).as_posix(),
        duration=duration,
        brightness=brightness,
        attack=attack,
        tonality=tonality,
        pitch_move=pitch_move,
    )


# ---------------------------------------------------------------- 설명 생성

_SYSTEM = """\
너는 한국 예능 편집자다. 효과음 목록을 받아서 각각에 대해 '언제 쓰는 소리인지'
한 줄 설명을 쓴다. 이 설명은 나중에 자동 편집기가 '이 장면에 이 소리를 깔까'를
판단하는 유일한 근거가 된다.

규칙:
- 소리의 생김새와 쓰임새를 함께 적는다. 예: "띵! 짧고 맑은 종소리. 정답, 깨달음, 포인트 강조"
- 30자 안팎으로 짧게. 한국어로 쓴다.
- 파일 이름이 의미를 담고 있으면 그걸 우선 믿되, 측정값과 어긋나면 측정값을 따른다.
  (이름이 'drum1' 인데 아주 밝고 짧으면 북이 아니라 다른 소리일 수 있다)
- 파일 이름이 'sfx_03' 처럼 아무 의미가 없으면 측정값만 보고 판단한다.
- 쓰임새는 구체적으로. "여러 상황에 사용" 같은 말은 쓸모가 없다.
"""


def _schema(names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": names},
                        "desc": {"type": "string"},
                    },
                    "required": ["name", "desc"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def describe(
    probes: list[Probe],
    *,
    model: str = "claude-opus-5",
    client: Any | None = None,
    batch_size: int = 40,
    progress: Any = None,
) -> dict[str, str]:
    """측정값을 바탕으로 각 효과음의 설명을 짓는다."""
    if not probes:
        return {}

    import anthropic

    client = client or anthropic.Anthropic()
    out: dict[str, str] = {}
    batches = [probes[i : i + batch_size] for i in range(0, len(probes), batch_size)]

    for index, batch in enumerate(batches, start=1):
        if progress:
            progress(index, len(batches))

        listing = "\n".join(
            f"- {p.name} (파일: {p.relative_path}) — {p.describe()}" for p in batch
        )
        user = f"효과음 {len(batch)}개다. 각각 설명을 지어라.\n\n{listing}"

        response = client.messages.create(
            model=model,
            max_tokens=8000,
            system=_SYSTEM,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": _schema([p.name for p in batch])},
            },
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("Claude 가 설명 생성을 거절했습니다.")

        text = next((b.text for b in response.content if b.type == "text"), "")
        for item in json.loads(text).get("items", []):
            out[item["name"]] = item["desc"].strip()

    return out


def scan(
    root: str | Path,
    *,
    model: str = "claude-opus-5",
    client: Any | None = None,
    keep_existing: bool = True,
    progress: Any = None,
    on_probe: Any = None,
) -> tuple[dict[str, dict[str, str]], list[Path]]:
    """폴더를 훑어 manifest.json 을 만든다.

    Returns:
        (매니페스트, 읽지 못한 파일들)
    """
    base = Path(root).resolve()
    files = find_audio_files(base)

    probes: list[Probe] = []
    unreadable: list[Path] = []
    for path in files:
        probe = probe_file(path, base)
        if probe is None:
            unreadable.append(path)
        else:
            probes.append(probe)
            if on_probe:
                on_probe(probe)

    manifest_path = base / "manifest.json"
    existing: dict[str, dict[str, str]] = {}
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 이미 설명이 붙은 건 다시 물어보지 않는다 (돈과 시간 절약)
    todo = [
        p for p in probes
        if not (keep_existing and existing.get(p.name, {}).get("desc"))
    ]
    described = describe(todo, model=model, client=client, progress=progress) if todo else {}

    manifest: dict[str, dict[str, str]] = {}
    for probe in probes:
        prior = existing.get(probe.name, {})
        desc = described.get(probe.name) or prior.get("desc") or probe.describe()
        manifest[probe.name] = {"file": probe.relative_path, "desc": desc}

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest, unreadable


def probes_to_json(probes: list[Probe]) -> str:
    return json.dumps([asdict(p) for p in probes], ensure_ascii=False, indent=2)
