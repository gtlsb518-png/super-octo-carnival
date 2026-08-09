"""핵심 로직 테스트. Claude API 호출 없이 돌아간다."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import cuts, sfxlib, styles, transcribe  # noqa: E402
from yeneung.config import CutConfig, load_config  # noqa: E402
from yeneung.cuesheet import Caption, Cuesheet, EffectCue, SfxCue, ZoomCue, sanitize  # noqa: E402

SR = 16_000


def _tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def _quiet(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


# ---------------------------------------------------------------- 무음 컷


def test_detect_removes_long_silence():
    # 소리 2초 / 무음 2초 / 소리 2초
    audio = np.concatenate([_tone(2.0), _quiet(2.0), _tone(2.0)])
    keeps = cuts.detect_keep_ranges(audio, SR, 6.0, CutConfig())

    assert len(keeps) == 2
    assert keeps[0].start == pytest.approx(0.0, abs=0.05)
    assert keeps[0].end == pytest.approx(2.12, abs=0.1)   # padding 만큼 여유
    assert keeps[1].start == pytest.approx(3.88, abs=0.1)


def test_short_silence_is_kept():
    # 0.3초 무음은 min_silence(0.6) 미만이라 그대로 둔다
    audio = np.concatenate([_tone(1.0), _quiet(0.3), _tone(1.0)])
    keeps = cuts.detect_keep_ranges(audio, SR, 2.3, CutConfig())
    assert len(keeps) == 1


def test_disabled_returns_whole():
    audio = np.concatenate([_tone(1.0), _quiet(3.0)])
    keeps = cuts.detect_keep_ranges(audio, SR, 4.0, CutConfig(enabled=False))
    assert keeps == [cuts.KeepRange(0.0, 4.0)]


def test_all_silence_falls_back_to_whole():
    keeps = cuts.detect_keep_ranges(_quiet(5.0), SR, 5.0, CutConfig())
    assert len(keeps) == 1
    assert keeps[0].duration == pytest.approx(5.0)


def test_no_audio_returns_whole():
    keeps = cuts.detect_keep_ranges(np.zeros(0, dtype=np.float32), SR, 9.0, CutConfig())
    assert keeps == [cuts.KeepRange(0.0, 9.0)]


# ---------------------------------------------------------------- 타임라인


def _timeline() -> cuts.TimelineMap:
    return cuts.TimelineMap([
        cuts.KeepRange(0.0, 3.0),
        cuts.KeepRange(5.0, 8.0),
        cuts.KeepRange(10.0, 12.0),
    ])


def test_timeline_total_and_mapping():
    tl = _timeline()
    assert tl.total == pytest.approx(8.0)
    assert tl.to_cut(1.0) == pytest.approx(1.0)     # 첫 구간
    assert tl.to_cut(6.0) == pytest.approx(4.0)     # 두 번째 구간
    assert tl.to_cut(11.0) == pytest.approx(7.0)    # 세 번째 구간


def test_timeline_snaps_removed_regions():
    tl = _timeline()
    # 4초는 잘려나간 구간 → 다음 구간 시작(3.0)으로 스냅
    assert tl.to_cut(4.0) == pytest.approx(3.0)
    # 끝을 넘어가면 전체 길이
    assert tl.to_cut(99.0) == pytest.approx(8.0)


def test_segment_of():
    tl = _timeline()
    assert tl.segment_of(1.0) == (0, pytest.approx(1.0))
    assert tl.segment_of(4.0) == (1, pytest.approx(1.0))
    assert tl.segment_of(7.5) == (2, pytest.approx(1.5))  # 세 번째 구간은 6.0초에 시작
    assert tl.segment_of(100.0) is None


def test_cut_start_of():
    tl = _timeline()
    assert tl.cut_start_of(0) == pytest.approx(0.0)
    assert tl.cut_start_of(1) == pytest.approx(3.0)
    assert tl.cut_start_of(2) == pytest.approx(6.0)


# ---------------------------------------------------------------- 큐시트 정리


def test_sanitize_keeps_overlapping_captions():
    """겹친다고 버리거나 밀지 않는다. 트랙을 나누는 건 배치 단계의 일."""
    sheet = Cuesheet(captions=[
        Caption(1.0, 2.0, "가", "reaction"),
        Caption(1.5, 2.5, "나", "reaction"),   # 앞과 겹침
    ])
    out = sanitize(sheet, total=30.0)
    assert len(out.captions) == 2
    assert out.captions[0].start == 1.0 and out.captions[0].end == 2.0
    assert out.captions[1].start == 1.5 and out.captions[1].end == 2.5


def test_sanitize_drops_duplicate_captions_from_chunk_overlap():
    """청크 경계에서 같은 자막이 두 번 나오는 건 중복이므로 하나만 남긴다."""
    sheet = Cuesheet(captions=[
        Caption(10.0, 11.0, "이게 되네", "reaction"),
        Caption(10.2, 11.2, "이게 되네", "reaction"),
        Caption(30.0, 31.0, "이게 되네", "reaction"),   # 한참 뒤면 다른 자막
    ])
    out = sanitize(sheet, total=60.0)
    assert len(out.captions) == 2


def test_sanitize_drops_duplicate_sfx():
    sheet = Cuesheet(sfx=[SfxCue(5.0, "ding"), SfxCue(5.01, "ding"), SfxCue(5.0, "drum")])
    out = sanitize(sheet, total=30.0)
    assert len(out.sfx) == 2  # 같은 시각의 ding 둘은 하나로, drum 은 별개


def test_sanitize_clamps_to_total():
    sheet = Cuesheet(captions=[Caption(9.0, 20.0, "끝", "reaction")])
    out = sanitize(sheet, total=10.0)
    assert out.captions[0].end <= 10.0


def test_sanitize_drops_empty_and_reversed():
    sheet = Cuesheet(captions=[
        Caption(1.0, 2.0, "   ", "reaction"),   # 빈 텍스트
        Caption(5.0, 4.0, "거꾸로", "reaction"),  # 끝이 시작보다 앞
    ])
    assert sanitize(sheet, total=10.0).captions == []


def test_sanitize_keeps_overlapping_zooms_and_effects():
    """겹치는 화면효과는 트랙을 나눠 둘 다 살린다. 줌은 배치 단계에서 걸러진다."""
    sheet = Cuesheet(
        zooms=[ZoomCue(1.0, 3.0, 1.3), ZoomCue(2.0, 4.0, 1.4)],
        effects=[EffectCue(1.0, 2.0, "flash"), EffectCue(1.5, 2.5, "shake")],
    )
    out = sanitize(sheet, total=30.0)
    assert len(out.zooms) == 2
    assert len(out.effects) == 2


def test_sanitize_keeps_sfx_within_range():
    sheet = Cuesheet(sfx=[SfxCue(2.0, "ding"), SfxCue(99.0, "ding")])
    out = sanitize(sheet, total=10.0)
    assert [s.time for s in out.sfx] == [2.0]


def test_cuesheet_roundtrip(tmp_path: Path):
    sheet = Cuesheet(
        captions=[Caption(1.0, 2.0, "이게 되네?!", "reaction", "top")],
        sfx=[SfxCue(1.0, "ding")],
        zooms=[ZoomCue(1.0, 2.5, 1.3)],
        effects=[EffectCue(1.0, 1.4, "flash")],
    )
    p = tmp_path / "cuesheet.json"
    sheet.save(p)
    again = Cuesheet.load(p)
    assert again.captions[0].text == "이게 되네?!"
    assert again.zooms[0].scale == 1.3


# ---------------------------------------------------------------- SRT


def test_write_srt(tmp_path: Path):
    utterances = [
        transcribe.Utterance(0.0, 1.5, "안녕하세요"),
        transcribe.Utterance(2.0, 3.25, "반갑습니다"),
    ]
    p = transcribe.write_srt(utterances, tmp_path / "a.srt")
    body = p.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,500" in body
    assert "00:00:02,000 --> 00:00:03,250" in body
    assert "반갑습니다" in body


# ---------------------------------------------------------------- 효과음


def test_sfx_manifest(tmp_path: Path):
    (tmp_path / "ding.wav").write_bytes(b"\0")
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "ding": {"file": "ding.wav", "desc": "띵!"},
            "missing": {"file": "nope.wav", "desc": "없음"},
        }),
        encoding="utf-8",
    )
    lib = sfxlib.load_library(tmp_path)
    assert lib.names() == ["ding"]           # 파일 없는 항목은 빠진다
    assert "띵!" in lib.catalog()
    assert sfxlib.missing_files(tmp_path) == ["missing -> nope.wav"]


def test_sfx_autodiscover(tmp_path: Path):
    (tmp_path / "boing.wav").write_bytes(b"\0")
    (tmp_path / "note.txt").write_text("무시됨", encoding="utf-8")
    lib = sfxlib.load_library(tmp_path)
    assert lib.names() == ["boing"]


def test_sfx_missing_folder(tmp_path: Path):
    assert not sfxlib.load_library(tmp_path / "없음")


# ---------------------------------------------------------------- 설정


def test_config_overrides(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[cut]\nmin_silence = 1.2\n\n'
        '[cuesheet]\ndensity = "high"\n\n'
        '[styles.reaction]\nsize = 20.0\ncolor = "#FF0000"\n',
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.cut.min_silence == 1.2
    assert cfg.cuesheet.density == "high"
    assert cfg.styles["reaction"].size == 20.0
    assert cfg.styles["reaction"].color == pytest.approx((1.0, 0.0, 0.0))
    assert cfg.cut.padding == 0.12   # 안 건드린 값은 기본값 유지


def test_config_rejects_unknown_key(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("[cut]\nnonsense = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nonsense"):
        load_config(p)


# ---------------------------------------------------------------- 효과 매핑


def test_effect_names_resolve():
    avail = styles.available_effects()
    assert "flash" in avail and "shake" in avail
    assert styles.effect_type("flash") is not None
    assert styles.effect_type("존재하지_않는_효과") is None


def test_intro_and_loop_resolve():
    assert styles.intro_type("弹出") is not None
    assert styles.intro_type(None) is None
    assert styles.loop_type("心跳频闪") is not None
