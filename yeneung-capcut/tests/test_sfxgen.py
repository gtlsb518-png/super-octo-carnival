"""효과음 합성 테스트.

소리를 들어볼 수는 없으니, 측정 가능한 성질로 검증한다.
길이·음량·클리핑 같은 기본 위생과, 각 소리가 의도한 음역에 있는지.
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import sfxgen, sfxlib  # noqa: E402


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as fh:
        assert fh.getnchannels() == 1
        assert fh.getsampwidth() == 2
        sr = fh.getframerate()
        raw = fh.readframes(fh.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0, sr


def _centroid(signal: np.ndarray, sr: int) -> float:
    """스펙트럼 무게중심 — 소리가 얼마나 밝은지."""
    spec = np.abs(np.fft.rfft(signal))
    freqs = np.fft.rfftfreq(len(signal), 1.0 / sr)
    return float((spec * freqs).sum() / spec.sum())


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("sfx")
    sfxgen.generate_pack(dest)
    return dest


# ---------------------------------------------------------------- 기본 위생


@pytest.mark.parametrize("name", sorted(sfxgen.GENERATORS))
def test_signal_is_sane(name: str):
    signal = sfxgen.GENERATORS[name][0]()
    assert len(signal) > 0
    assert not np.isnan(signal).any(), "NaN 포함"
    assert not np.isinf(signal).any(), "무한대 포함"
    peak = float(np.abs(signal).max())
    assert peak <= 1.0, f"클리핑 ({peak})"
    assert peak > 0.85, f"정규화가 안 됨 ({peak})"


@pytest.mark.parametrize("name", sorted(sfxgen.GENERATORS))
def test_no_click_at_edges(name: str):
    """맨 앞/뒤가 0 근처여야 재생 시 딸깍거리지 않는다."""
    signal = sfxgen.GENERATORS[name][0]()
    assert abs(signal[0]) < 0.02
    assert abs(signal[-1]) < 0.02


@pytest.mark.parametrize("name", sorted(sfxgen.GENERATORS))
def test_not_silent_anywhere_important(name: str):
    signal = sfxgen.GENERATORS[name][0]()
    rms = float(np.sqrt(np.mean(signal**2)))
    assert rms > 0.01, f"거의 무음 (rms={rms})"


# ---------------------------------------------------------------- 음색


def test_bright_sounds_are_bright():
    for name in ("ding", "sparkle", "crickets"):
        signal = sfxgen.GENERATORS[name][0]()
        c = _centroid(signal, sfxgen.SR)
        assert c > 3000, f"{name} 이 충분히 밝지 않음 ({c:.0f}Hz)"


def test_low_sounds_are_low():
    for name in ("drum", "fail"):
        signal = sfxgen.GENERATORS[name][0]()
        c = _centroid(signal, sfxgen.SR)
        assert c < 800, f"{name} 이 너무 밝음 ({c:.0f}Hz)"


def test_drum_is_much_darker_than_ding():
    drum = _centroid(sfxgen.drum(), sfxgen.SR)
    ding = _centroid(sfxgen.ding(), sfxgen.SR)
    assert ding > drum * 3


def test_percussive_sounds_are_front_loaded():
    """타격음은 에너지가 앞쪽에 쏠려 있어야 한다."""
    for name in ("drum", "pop", "ding"):
        signal = np.abs(sfxgen.GENERATORS[name][0]())
        t = np.arange(len(signal)) / sfxgen.SR
        centre = float((signal * t).sum() / signal.sum())
        assert centre / (len(signal) / sfxgen.SR) < 0.4, f"{name} 은 타격감이 없음"


def test_tension_gets_louder():
    """두구두구는 뒤로 갈수록 커져야 긴장이 산다."""
    signal = sfxgen.tension()
    third = len(signal) // 3
    head = float(np.sqrt(np.mean(signal[:third] ** 2)))
    tail = float(np.sqrt(np.mean(signal[-third:] ** 2)))
    assert tail > head * 1.5


def test_sweeps_are_phase_continuous():
    """주파수 적분으로 만든 스윕은 인접 샘플이 급변하지 않는다."""
    signal = sfxgen._sweep(800.0, 100.0, 0.5)
    assert float(np.abs(np.diff(signal)).max()) < 0.2


# ---------------------------------------------------------------- 팩 생성


def test_pack_writes_all_files(pack: Path):
    for name in sfxgen.GENERATORS:
        assert (pack / f"{name}.wav").exists()


def test_pack_files_are_valid_wav(pack: Path):
    for name in sfxgen.GENERATORS:
        signal, sr = _read_wav(pack / f"{name}.wav")
        assert sr == sfxgen.SR
        assert len(signal) > 0
        assert np.abs(signal).max() <= 1.0


def test_pack_manifest_matches_files(pack: Path):
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == set(sfxgen.GENERATORS)
    for name, entry in manifest.items():
        assert entry["file"] == f"{name}.wav"
        assert len(entry["desc"]) > 5, f"{name} 설명이 부실함"


def test_pack_is_loadable_by_library(pack: Path):
    lib = sfxlib.load_library(pack)
    assert len(lib) == len(sfxgen.GENERATORS)
    assert "ding" in lib.names()
    assert sfxlib.missing_files(pack) == []


def test_pack_skips_existing_by_default(tmp_path: Path):
    custom = tmp_path / "sfx"
    custom.mkdir()
    (custom / "ding.wav").write_bytes("내가 직접 넣은 파일".encode())

    created, skipped = sfxgen.generate_pack(custom)
    assert "ding" in skipped
    assert "ding" not in created
    # 직접 넣은 파일은 건드리지 않는다
    assert (custom / "ding.wav").read_bytes() == "내가 직접 넣은 파일".encode()


def test_pack_overwrites_with_force(tmp_path: Path):
    custom = tmp_path / "sfx"
    custom.mkdir()
    (custom / "ding.wav").write_bytes("덮어써도 되는 파일".encode())

    created, skipped = sfxgen.generate_pack(custom, overwrite=True)
    assert "ding" in created
    assert skipped == []
    assert (custom / "ding.wav").read_bytes() != "덮어써도 되는 파일".encode()


def test_pack_keeps_user_descriptions(tmp_path: Path):
    custom = tmp_path / "sfx"
    custom.mkdir()
    (custom / "manifest.json").write_text(
        json.dumps({"ding": {"file": "ding.wav", "desc": "내가 쓴 설명"}}),
        encoding="utf-8",
    )
    sfxgen.generate_pack(custom)
    manifest = json.loads((custom / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ding"]["desc"] == "내가 쓴 설명"
    assert "drum" in manifest  # 나머지는 새로 채워짐
