"""초안 생성 통합 테스트.

실제로 캡컷 draft_content.json 을 만들고 구조를 검사한다.
테스트용 영상/오디오는 imageio-ffmpeg 가 들고 있는 ffmpeg 로 즉석에서 만든다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import cuts, draft, media, sfxlib  # noqa: E402
from yeneung.config import Config  # noqa: E402
from yeneung.cuesheet import Caption, Cuesheet, EffectCue, SfxCue, ZoomCue  # noqa: E402

pytest.importorskip("pycapcut")
pytest.importorskip("pymediainfo")


@pytest.fixture(scope="module")
def ffmpeg() -> str:
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


@pytest.fixture(scope="module")
def fixtures(ffmpeg: str, tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("fixtures")
    video = d / "clip.mp4"
    sfx = d / "ding.wav"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        check=True,
    )
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=0.4", str(sfx)],
        check=True,
    )
    return {"dir": d, "video": video, "sfx": sfx}


@pytest.fixture()
def library(fixtures, tmp_path: Path) -> sfxlib.SfxLibrary:
    root = tmp_path / "sfx"
    root.mkdir()
    (root / "ding.wav").write_bytes(fixtures["sfx"].read_bytes())
    (root / "manifest.json").write_text(
        json.dumps({"ding": {"file": "ding.wav", "desc": "띵!"}}), encoding="utf-8"
    )
    return sfxlib.load_library(root)


@pytest.fixture()
def built(fixtures, library, tmp_path: Path):
    """3컷 + 자막 + 효과음 + 줌 + 화면효과가 들어간 초안을 실제로 만든다."""
    info = media.probe(fixtures["video"])
    keeps = [
        cuts.KeepRange(0.0, 3.0),
        cuts.KeepRange(4.5, 7.0),
        cuts.KeepRange(8.0, 11.5),
    ]
    timeline = cuts.TimelineMap(keeps)  # 편집본 총 9.0초

    sheet = Cuesheet(
        captions=[
            Caption(0.5, 1.6, "?!", "reaction", "top"),
            Caption(3.4, 4.6, "이게 되네", "emphasis", "default"),
            Caption(6.2, 7.4, "(정적)", "narration", "upper"),
        ],
        sfx=[SfxCue(0.5, "ding"), SfxCue(3.4, "ding")],
        zooms=[ZoomCue(3.2, 5.2, 1.35)],
        effects=[EffectCue(3.3, 3.8, "flash")],
    )

    drafts = tmp_path / "drafts"
    drafts.mkdir()
    report = draft.build(
        video=info, keeps=keeps, timeline=timeline, sheet=sheet, cfg=Config(),
        draft_dir=drafts, draft_name="테스트_예능", sfx_library=library,
        srt_path=None, allow_replace=True,
    )
    content = json.loads((report.draft_path / "draft_content.json").read_text(encoding="utf-8"))
    return report, content


def test_draft_files_written(built):
    report, _ = built
    assert (report.draft_path / "draft_content.json").exists()
    assert (report.draft_path / "draft_meta_info.json").exists()
    assert report.warnings == []


def test_targets_capcut_windows(built):
    _, content = built
    assert content["platform"]["app_source"] == "cc"
    assert content["platform"]["os"] == "windows"


def test_timeline_duration_matches_cuts(built):
    report, content = built
    assert report.duration == pytest.approx(9.0)
    assert content["duration"] == 9_000_000  # 마이크로초


def test_cut_clips_are_contiguous(built):
    _, content = built
    video_track = next(t for t in content["tracks"] if t["type"] == "video")
    segs = sorted(video_track["segments"], key=lambda s: s["target_timerange"]["start"])
    assert len(segs) == 3

    cursor = 0
    for seg in segs:
        assert seg["target_timerange"]["start"] == cursor
        cursor += seg["target_timerange"]["duration"]
    assert cursor == 9_000_000

    # 원본에서 가져오는 위치가 keep 범위와 일치해야 한다
    assert [s["source_timerange"]["start"] for s in segs] == [0, 4_500_000, 8_000_000]


def test_zoom_becomes_keyframes_on_right_clip(built):
    _, content = built
    video_track = next(t for t in content["tracks"] if t["type"] == "video")
    segs = sorted(video_track["segments"], key=lambda s: s["target_timerange"]["start"])

    # 줌 큐 3.2s 는 두 번째 클립(3.0s 시작) 안에 있다
    assert not segs[0].get("common_keyframes")
    kfs = segs[1]["common_keyframes"]
    assert kfs, "두 번째 클립에 키프레임이 있어야 한다"

    points = kfs[0]["keyframe_list"]
    assert len(points) == 4                          # 1.0 → 배율 → 배율 → 1.0
    values = [p["values"][0] for p in points]
    assert values[0] == pytest.approx(1.0)
    assert values[1] == pytest.approx(1.35)
    assert values[-1] == pytest.approx(1.0)
    # 시각은 클립 시작 기준(0.2s = 3.2s - 3.0s)
    assert points[0]["time_offset"] == pytest.approx(200_000, abs=1000)


def test_captions_have_style_and_no_overlap(built):
    report, content = built
    assert report.captions == 3
    text_track = next(t for t in content["tracks"] if t["type"] == "text")
    segs = sorted(text_track["segments"], key=lambda s: s["target_timerange"]["start"])
    assert len(segs) == 3

    for a, b in zip(segs, segs[1:]):
        a_end = a["target_timerange"]["start"] + a["target_timerange"]["duration"]
        assert b["target_timerange"]["start"] >= a_end

    texts = [json.loads(m["content"])["text"] for m in content["materials"]["texts"]]
    assert "이게 되네" in texts


def test_caption_style_applied(built):
    _, content = built
    materials = {json.loads(m["content"])["text"]: json.loads(m["content"])
                 for m in content["materials"]["texts"]}
    emphasis = materials["이게 되네"]["styles"][0]
    reaction = materials["?!"]["styles"][0]
    # emphasis 가 reaction 보다 커야 한다 (기본 프리셋 15.0 vs 13.0)
    assert emphasis["size"] > reaction["size"]
    assert emphasis["strokes"], "외곽선이 있어야 한다"


def test_sfx_and_effects_placed(built):
    report, content = built
    assert report.sfx == 2
    assert report.effects == 1
    audio_track = next(t for t in content["tracks"] if t["type"] == "audio")
    assert len(audio_track["segments"]) == 2
    effect_track = next(t for t in content["tracks"] if t["type"] == "effect")
    assert len(effect_track["segments"]) == 1


def test_unknown_sfx_is_warned_not_fatal(fixtures, library, tmp_path: Path):
    info = media.probe(fixtures["video"])
    keeps = [cuts.KeepRange(0.0, 5.0)]
    sheet = Cuesheet(sfx=[SfxCue(1.0, "존재하지_않는_효과음")])
    drafts = tmp_path / "d2"
    drafts.mkdir()

    report = draft.build(
        video=info, keeps=keeps, timeline=cuts.TimelineMap(keeps), sheet=sheet,
        cfg=Config(), draft_dir=drafts, draft_name="경고", sfx_library=library,
        srt_path=None, allow_replace=True,
    )
    assert report.sfx == 0
    assert any("존재하지_않는_효과음" in w for w in report.warnings)


def test_srt_subtitles_imported(fixtures, library, tmp_path: Path):
    from yeneung.transcribe import Utterance, write_srt

    info = media.probe(fixtures["video"])
    keeps = [cuts.KeepRange(0.0, 6.0)]
    srt = write_srt(
        [Utterance(0.5, 2.0, "안녕하세요"), Utterance(2.5, 4.0, "반갑습니다")],
        tmp_path / "d.srt",
    )
    drafts = tmp_path / "d3"
    drafts.mkdir()

    report = draft.build(
        video=info, keeps=keeps, timeline=cuts.TimelineMap(keeps), sheet=Cuesheet(),
        cfg=Config(), draft_dir=drafts, draft_name="자막", sfx_library=library,
        srt_path=srt, allow_replace=True,
    )
    assert report.subtitles == 2
    assert report.warnings == []

    content = json.loads((report.draft_path / "draft_content.json").read_text(encoding="utf-8"))
    texts = [json.loads(m["content"])["text"] for m in content["materials"]["texts"]]
    assert "안녕하세요" in texts
