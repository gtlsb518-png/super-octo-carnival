"""효과음 합성.

예능에서 쓰는 전형적인 효과음들을 numpy 로 직접 만든다.
받아서 쓰는 게 아니라 생성하는 것이라 저작권 문제가 없고,
네트워크도 필요 없다.

마음에 안 드는 소리는 같은 이름의 파일로 덮어쓰면 그대로 대체된다.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

SR = 48_000
_PEAK = 0.89  # 클리핑 여유


# ---------------------------------------------------------------- 기본 도구


def _n(duration: float) -> int:
    """길이(초) → 샘플 수. 절삭이 아니라 반올림해야 길이가 어긋나지 않는다."""
    return int(round(SR * duration))


def _t(duration: float) -> np.ndarray:
    return np.arange(_n(duration), dtype=np.float64) / SR


def _sweep(f0: float, f1: float, duration: float, curve: float = 1.0) -> np.ndarray:
    """f0 에서 f1 로 미끄러지는 사인파.

    주파수를 적분해 위상을 만든다. sin(2*pi*f(t)*t) 로 하면 위상이 튄다.
    curve > 1 이면 초반에 빠르게 변한다.
    """
    n = _n(duration)
    x = np.linspace(0.0, 1.0, n, endpoint=False) ** curve
    freq = f0 + (f1 - f0) * x
    return np.sin(2 * np.pi * np.cumsum(freq) / SR)


def _decay(duration: float, tau: float) -> np.ndarray:
    """지수 감쇠 포락선."""
    return np.exp(-_t(duration) / tau)


def _noise(duration: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(_n(duration))


def _band(signal: np.ndarray, low: float, high: float) -> np.ndarray:
    """FFT 로 대역 통과. 가장자리를 부드럽게 깎아 링잉을 줄인다."""
    spec = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1.0 / SR)
    centre = np.sqrt(max(low, 1.0) * high)
    width = max(high - low, 1.0) / 2.0
    gain = np.exp(-(((freqs - centre) / width) ** 2))
    gain[(freqs < low * 0.5) | (freqs > high * 2.0)] = 0.0
    return np.fft.irfft(spec * gain, n=len(signal))


def _bell(freq: float, duration: float, tau: float, partials=(1.0, 2.76, 5.40, 8.93)) -> np.ndarray:
    """종소리. 배음이 정수배가 아니라서 금속성이 난다."""
    out = np.zeros(_n(duration))
    for i, ratio in enumerate(partials):
        amp = 1.0 / (i + 1) ** 1.4
        out += amp * np.sin(2 * np.pi * freq * ratio * _t(duration)) * _decay(duration, tau / (i + 1) ** 0.5)
    return out


def _finish(signal: np.ndarray, fade: float = 0.004) -> np.ndarray:
    """앞뒤를 짧게 페이드해 딸깍 소리를 없앤 뒤 정규화한다.

    순서가 중요하다. 정규화를 먼저 하면 타격음처럼 피크가 맨 앞에 있는 소리는
    뒤이은 페이드인에 피크가 깎여서 목표 음량에 못 미친다.
    """
    signal = signal.astype(np.float64, copy=True)

    n = min(_n(fade), len(signal) // 2)
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n)
        signal[:n] *= ramp
        signal[-n:] *= ramp[::-1]

    peak = float(np.max(np.abs(signal))) or 1.0
    return signal / peak * _PEAK


# ---------------------------------------------------------------- 효과음들


def ding() -> np.ndarray:
    """띵! 맑은 종. 정답, 깨달음, 포인트 강조."""
    return _finish(_bell(1568.0, 1.1, 0.42))


def correct() -> np.ndarray:
    """딩동댕. 정답 announce."""
    out = np.zeros(_n(1.3))
    for i, freq in enumerate((1046.5, 1318.5, 1568.0)):
        tone = _bell(freq, 1.3 - i * 0.18, 0.34)
        start = _n(i * 0.17)
        out[start:start + len(tone)] += tone * 0.8
    return _finish(out)


def error() -> np.ndarray:
    """삐- 삐-. 오답, 탈락."""
    out = np.zeros(_n(0.62))
    beep = np.sign(np.sin(2 * np.pi * 233.0 * _t(0.2))) * 0.35 + np.sin(2 * np.pi * 233.0 * _t(0.2))
    beep *= np.minimum(1.0, _decay(0.2, 0.14) * 1.6)
    for start in (0, _n(0.32)):
        out[start:start + len(beep)] += beep
    return _finish(out)


def boing() -> np.ndarray:
    """뿅! 통통 튀는 소리. 엉뚱한 행동, 귀여운 실수."""
    dur = 0.42
    vibrato = 1.0 + 0.22 * np.sin(2 * np.pi * 17.0 * _t(dur))
    n = _n(dur)
    x = np.linspace(0.0, 1.0, n, endpoint=False)
    freq = (760.0 - 580.0 * x) * vibrato
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return _finish(tone * _decay(dur, 0.16))


def pop() -> np.ndarray:
    """뾱. 아주 짧게 튀는 소리. 자막 등장."""
    return _finish(_sweep(380.0, 1150.0, 0.09, curve=0.5) * _decay(0.09, 0.03))


def swoosh() -> np.ndarray:
    """슝. 빠른 이동, 화면 전환.

    저역/중역/고역 노이즈를 시간에 따라 교차시켜 필터가 훑는 느낌을 낸다.
    """
    dur = 0.55
    n = _n(dur)
    base = _noise(dur, seed=11)
    bands = [_band(base, 200, 900), _band(base, 900, 3500), _band(base, 3500, 11000)]

    x = np.linspace(0.0, 1.0, n)
    centre = 0.15 + 1.7 * x  # 저→고로 훑고 지나감
    mixed = np.zeros(n)
    for i, band in enumerate(bands):
        mixed += band * np.exp(-((centre - i) ** 2) / 0.35)

    envelope = np.sin(np.pi * x) ** 1.5
    return _finish(mixed * envelope)


def drum() -> np.ndarray:
    """둥! 북 한 방. 결정적 순간, 반전 직전."""
    dur = 0.7
    body = _sweep(165.0, 46.0, dur, curve=0.35) * _decay(dur, 0.16)
    # 어택 노이즈는 '치는 순간'만 살짝. 크면 북이 아니라 스네어처럼 들린다.
    click = _band(_noise(dur, seed=3), 900, 4000) * _decay(dur, 0.008) * 0.22
    return _finish(body + click)


def tension() -> np.ndarray:
    """두구두구. 긴장감 고조 후 한 방. 결과 발표 직전."""
    roll_dur, hit_dur = 1.9, 0.7
    n = _n(roll_dur)
    x = np.linspace(0.0, 1.0, n)

    roll = _band(_noise(roll_dur, seed=7), 90, 2200)
    rate = 11.0 + 20.0 * x                                  # 점점 빨라짐
    tremolo = 0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(rate) / SR)
    roll *= tremolo ** 1.6 * (0.18 + 0.82 * x ** 2)         # 점점 커짐

    out = np.zeros(_n(roll_dur) + _n(hit_dur))
    out[:n] += roll * 0.75
    hit = drum()[: _n(hit_dur)]
    out[n:n + len(hit)] += hit
    return _finish(out)


def fail() -> np.ndarray:
    """뿌우-. 하강하는 실패음. 실수, 좌절, 계획 무산."""
    dur = 1.0
    n = _n(dur)
    x = np.linspace(0.0, 1.0, n, endpoint=False)
    # 계단식으로 떨어지는 트롬본 느낌
    steps = np.floor(x * 4) / 4
    freq = 330.0 * (2 ** (-1.15 * steps)) * (1.0 + 0.03 * np.sin(2 * np.pi * 5.5 * x * dur))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    tone = np.sin(phase) + 0.45 * np.sin(2 * phase) + 0.22 * np.sin(3 * phase)
    return _finish(tone * np.minimum(1.0, _decay(dur, 0.55) * 1.4))


def sparkle() -> np.ndarray:
    """반짝. 예쁜 것 등장, 자화자찬, 뿌듯한 순간."""
    dur = 1.0
    out = np.zeros(_n(dur))
    rng = np.random.default_rng(23)
    for i in range(9):
        freq = float(rng.choice([2093, 2637, 3136, 3520, 4186, 5274]))
        start = _n(0.02 + i * 0.055 + rng.uniform(0, 0.02))
        tone = _bell(freq, 0.5, 0.13, partials=(1.0, 2.4)) * rng.uniform(0.45, 1.0)
        end = min(start + len(tone), len(out))
        out[start:end] += tone[: end - start]
    return _finish(out)


def crickets() -> np.ndarray:
    """귀뚜라미. 아무도 반응 안 하는 정적, 썰렁함."""
    dur = 2.6
    out = np.zeros(_n(dur))
    chirp_len = 0.075
    chirp_t = _t(chirp_len)
    # 4.5kHz 톤을 잘게 끊어 문지르는 소리
    chirp = np.sin(2 * np.pi * 4500.0 * chirp_t) * (0.5 + 0.5 * np.sin(2 * np.pi * 55.0 * chirp_t))
    chirp *= np.sin(np.pi * np.linspace(0, 1, len(chirp_t))) ** 0.7

    for group in range(4):
        for k in range(3):
            start = _n(0.15 + group * 0.62 + k * 0.11)
            end = min(start + len(chirp), len(out))
            out[start:end] += chirp[: end - start] * (0.75 if k else 1.0)
    return _finish(out)


def record_scratch() -> np.ndarray:
    """찌익. 분위기가 급정지하는 순간, 예상 빗나감."""
    dur = 0.6
    n = _n(dur)
    x = np.linspace(0.0, 1.0, n, endpoint=False)
    # 앞뒤로 빠르게 문지르다 아래로 미끄러짐
    wobble = np.sin(2 * np.pi * 9.0 * x) * (1.0 - x)
    freq = 420.0 * (1.0 + 1.1 * wobble) * (1.0 - 0.72 * x ** 1.6)
    tone = np.sin(2 * np.pi * np.cumsum(np.maximum(freq, 40.0)) / SR)
    grit = _band(_noise(dur, seed=31), 700, 5200) * 0.55 * (1.0 - x)
    return _finish((tone * 0.8 + grit) * np.minimum(1.0, _decay(dur, 0.42) * 1.5))


#: 이름 → (생성 함수, 설명). 설명은 Claude 가 언제 쓸지 판단하는 근거다.
GENERATORS: dict[str, tuple] = {
    "ding":           (ding, "띵! 짧고 맑은 종소리. 정답, 깨달음, 포인트 강조"),
    "correct":        (correct, "딩동댕. 정답, 성공, 통과"),
    "error":          (error, "삐-삐-. 오답, 탈락, 규칙 위반"),
    "boing":          (boing, "뿅! 통통 튀는 소리. 엉뚱한 행동, 귀여운 실수"),
    "pop":            (pop, "뾱. 아주 짧게 튀는 소리. 자막 등장, 가벼운 포인트"),
    "swoosh":         (swoosh, "슝. 빠른 이동, 화면 전환, 시선 이동"),
    "drum":           (drum, "둥! 북 한 방. 결정적 순간, 반전 직전의 타격감"),
    "tension":        (tension, "두구두구. 긴장감 고조 후 한 방. 결과 발표 직전"),
    "fail":           (fail, "뿌우-. 하강하는 실패음. 실수, 좌절, 계획 무산"),
    "sparkle":        (sparkle, "반짝. 예쁜 것 등장, 자화자찬, 뿌듯한 순간"),
    "crickets":       (crickets, "귀뚜라미. 아무도 반응 안 하는 정적, 썰렁함"),
    "record_scratch": (record_scratch, "찌익. 분위기가 급정지하는 순간, 예상 빗나감"),
}


# ---------------------------------------------------------------- 파일 쓰기


def write_wav(signal: np.ndarray, path: str | Path) -> Path:
    """16bit PCM 모노 WAV 로 저장."""
    pcm = np.clip(signal, -1.0, 1.0)
    data = (pcm * 32767.0).astype("<i2")
    p = Path(path)
    with wave.open(str(p), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes(data.tobytes())
    return p


def generate_pack(dest: str | Path, *, overwrite: bool = False) -> tuple[list[str], list[str]]:
    """기본 효과음 팩을 만들고 manifest.json 을 갱신한다.

    Returns:
        (새로 만든 이름들, 이미 있어서 건너뛴 이름들)
    """
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped: list[str] = []

    manifest_path = root / "manifest.json"
    manifest: dict[str, dict[str, str]] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for name, (fn, desc) in GENERATORS.items():
        target = root / f"{name}.wav"
        if target.exists() and not overwrite:
            skipped.append(name)
        else:
            write_wav(fn(), target)
            created.append(name)
        # 직접 넣은 파일이라도 설명이 없으면 채워준다
        entry = manifest.get(name)
        if not entry or not entry.get("desc"):
            manifest[name] = {"file": f"{name}.wav", "desc": desc}

    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return created, skipped
