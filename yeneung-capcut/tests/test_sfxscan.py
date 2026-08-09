"""내 효과음 폴더 분석 테스트.

Claude 호출 없이, 파형에서 성질을 제대로 읽어내는지만 본다.
설명을 짓는 단계는 그 측정값을 그대로 넘길 뿐이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("av")

from yeneung import sfxgen, sfxlib, sfxscan  # noqa: E402


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("mysfx")
    sfxgen.generate_pack(root)
    return root


def _probe(pack: Path, name: str) -> sfxscan.Probe:
    probe = sfxscan.probe_file(pack / f"{name}.wav", pack)
    assert probe is not None
    return probe


# ---------------------------------------------------------------- 파일 찾기


def test_finds_audio_recursively(tmp_path: Path):
    (tmp_path / "sub" / "deep").mkdir(parents=True)
    (tmp_path / "a.wav").write_bytes(b"")
    (tmp_path / "sub" / "b.mp3").write_bytes(b"")
    (tmp_path / "sub" / "deep" / "c.flac").write_bytes(b"")
    (tmp_path / "readme.txt").write_text("무시", encoding="utf-8")

    found = {p.name for p in sfxscan.find_audio_files(tmp_path)}
    assert found == {"a.wav", "b.mp3", "c.flac"}


def test_missing_folder_is_empty():
    assert sfxscan.find_audio_files("/존재하지/않는/폴더") == []


def test_unreadable_file_returns_none(tmp_path: Path):
    bad = tmp_path / "broken.wav"
    bad.write_bytes("이건 오디오가 아님".encode())
    assert sfxscan.probe_file(bad, tmp_path) is None


# ---------------------------------------------------------------- 측정


def test_measures_duration(pack: Path):
    assert _probe(pack, "ding").duration == pytest.approx(1.1, abs=0.05)
    assert _probe(pack, "pop").duration == pytest.approx(0.09, abs=0.02)


def test_brightness_separates_drum_from_ding(pack: Path):
    assert _probe(pack, "drum").brightness < 900
    assert _probe(pack, "ding").brightness > 2500


def test_attack_detects_percussive(pack: Path):
    """북·띵은 앞에서 터지고, 두구두구는 뒤로 갈수록 커진다."""
    assert _probe(pack, "drum").attack < 0.35
    assert _probe(pack, "tension").attack > 0.5


def test_tonality_separates_tone_from_noise(pack: Path):
    assert _probe(pack, "ding").tonality > _probe(pack, "swoosh").tonality


def test_pitch_move_detects_falling_sound(pack: Path):
    """fail 은 내려가는 소리, boing 도 아래로 떨어진다."""
    assert _probe(pack, "fail").pitch_move < 0
    assert _probe(pack, "boing").pitch_move < 0


def test_relative_path_is_kept(tmp_path: Path):
    sub = tmp_path / "타격"
    sub.mkdir()
    sfxgen.write_wav(sfxgen.drum(), sub / "drum.wav")
    probe = sfxscan.probe_file(sub / "drum.wav", tmp_path)
    assert probe is not None
    assert probe.relative_path == "타격/drum.wav"


def test_describe_is_human_readable(pack: Path):
    text = _probe(pack, "drum").describe()
    assert "초" in text
    assert "묵직" in text or "낮" in text
    assert "타격" in text


# ---------------------------------------------------------------- 매니페스트


def test_scan_without_api_falls_back_to_measurements(tmp_path: Path, monkeypatch):
    """설명 생성이 불가능해도 측정값으로 된 설명이라도 남겨야 한다."""
    sfxgen.write_wav(sfxgen.ding(), tmp_path / "mystery_01.wav")
    monkeypatch.setattr(sfxscan, "describe", lambda *a, **k: {})

    manifest, unreadable = sfxscan.scan(tmp_path)
    assert unreadable == []
    assert "mystery_01" in manifest
    assert "초" in manifest["mystery_01"]["desc"]

    # 라이브러리가 바로 읽을 수 있어야 한다
    lib = sfxlib.load_library(tmp_path)
    assert lib.names() == ["mystery_01"]


def test_scan_keeps_existing_descriptions(tmp_path: Path, monkeypatch):
    sfxgen.write_wav(sfxgen.ding(), tmp_path / "ding.wav")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"ding": {"file": "ding.wav", "desc": "내가 쓴 설명"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    asked: list[list] = []

    def fake_describe(probes, **kwargs):
        asked.append(probes)
        return {}

    monkeypatch.setattr(sfxscan, "describe", fake_describe)
    manifest, _ = sfxscan.scan(tmp_path, keep_existing=True)

    assert manifest["ding"]["desc"] == "내가 쓴 설명"
    assert asked == [] or asked[0] == [], "이미 설명이 있으면 다시 물어보지 않는다"


def test_scan_records_subfolder_paths(tmp_path: Path, monkeypatch):
    (tmp_path / "웃음").mkdir()
    sfxgen.write_wav(sfxgen.sparkle(), tmp_path / "웃음" / "twinkle.wav")
    monkeypatch.setattr(sfxscan, "describe", lambda *a, **k: {})

    manifest, _ = sfxscan.scan(tmp_path)
    assert manifest["twinkle"]["file"] == "웃음/twinkle.wav"
    # 하위 폴더 파일도 라이브러리가 찾아낼 수 있어야 한다
    assert sfxlib.load_library(tmp_path).get("twinkle") is not None


def test_scan_reports_unreadable(tmp_path: Path, monkeypatch):
    sfxgen.write_wav(sfxgen.pop(), tmp_path / "ok.wav")
    (tmp_path / "broken.wav").write_bytes("쓰레기".encode())
    monkeypatch.setattr(sfxscan, "describe", lambda *a, **k: {})

    manifest, unreadable = sfxscan.scan(tmp_path)
    assert "ok" in manifest
    assert [p.name for p in unreadable] == ["broken.wav"]
