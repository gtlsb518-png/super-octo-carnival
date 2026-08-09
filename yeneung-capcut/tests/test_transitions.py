"""장면 전환과 자막 애니메이션 테스트.

전환은 '앞 클립'에 붙고, 진짜 컷 경계에만 들어가야 한다.
줌 때문에 쪼갠 자리는 화면이 이어지므로 전환을 넣으면 안 된다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("pycapcut")

from yeneung import cuts, draft, media, packing, sfxgen, sfxlib, styles  # noqa: E402
from yeneung.config import Config  # noqa: E402
from yeneung.cuesheet import Caption, Cuesheet, TransitionCue, ZoomCue  # noqa: E402


# ---------------------------------------------------------------- 컷 경계


def test_cut_boundaries_excludes_start_and_end():
    keeps = [cuts.KeepRange(0, 3), cuts.KeepRange(10, 13), cuts.KeepRange(20, 22)]
    tl = cuts.TimelineMap(keeps)
    # 편집본에서 구간은 0-3, 3-6, 6-8 → 경계는 3.0 과 6.0
    assert packing.cut_boundaries(tl) == [pytest.approx(3.0), pytest.approx(6.0)]


def test_single_keep_has_no_boundary():
    tl = cuts.TimelineMap([cuts.KeepRange(0, 10)])
    assert packing.cut_boundaries(tl) == []


# ---------------------------------------------------------------- 이름 매핑


def test_transition_names_resolve():
    avail = styles.available_transitions()
    for name in ("dissolve", "fade_black", "flash_white"):
        assert name in avail
        assert styles.transition_type(name) is not None
    assert styles.transition_type("없는전환") is None


def test_caption_anim_names_resolve():
    avail = styles.available_caption_anims()
    for name in ("pop", "fade", "typewriter", "zoom"):
        assert name in avail
    assert styles.caption_anim_type("typewriter") is not None
    assert styles.caption_anim_type("default") is None   # 스타일 기본값으로 넘김
    assert styles.caption_anim_type(None) is None


# ---------------------------------------------------------------- 초안 생성


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    ff = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    out = tmp_path_factory.mktemp("vid") / "v.mp4"
    subprocess.run(
        [ff, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=20",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True,
    )
    return out


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> sfxlib.SfxLibrary:
    root = tmp_path_factory.mktemp("sfx")
    sfxgen.generate_pack(root)
    return sfxlib.load_library(root)


def _build(video: Path, library, tmp_path: Path, sheet: Cuesheet, name: str, cfg=None):
    """컷 3개(편집본 0-5, 5-10, 10-15)짜리 초안을 만든다."""
    keeps = [cuts.KeepRange(0, 5), cuts.KeepRange(6, 11), cuts.KeepRange(13, 18)]
    drafts = tmp_path / "d"
    drafts.mkdir(exist_ok=True)
    report = draft.build(
        video=media.probe(video), keeps=keeps, timeline=cuts.TimelineMap(keeps),
        sheet=sheet, cfg=cfg or Config(), draft_dir=drafts, draft_name=name,
        sfx_library=library, srt_path=None, allow_replace=True,
    )
    content = json.loads((report.draft_path / "draft_content.json").read_text(encoding="utf-8"))
    return report, content


def _transition_materials(content) -> dict[str, dict]:
    return {t["id"]: t for t in content["materials"].get("transitions", [])}


def _video_segments(content) -> list[dict]:
    track = next(t for t in content["tracks"] if t["type"] == "video")
    return sorted(track["segments"], key=lambda s: s["target_timerange"]["start"])


def test_transition_material_is_registered(video, library, tmp_path):
    """전환 소재가 실제로 등록돼야 한다.

    클립을 트랙에 넣은 뒤에 전환을 붙이면 참조만 남고 소재가 빠진다.
    그러면 캡컷에서 전환이 사라진다 — 실제로 겪은 버그라 못박아 둔다.
    """
    sheet = Cuesheet(transitions=[TransitionCue(5.0, "dissolve", 0.5)])
    report, content = _build(video, library, tmp_path, sheet, "전환소재")

    materials = _transition_materials(content)
    assert len(materials) == 1, "전환 소재가 materials 에 없다"
    assert report.transitions == 1

    refs = {r for s in _video_segments(content) for r in s["extra_material_refs"]}
    assert set(materials) <= refs, "등록된 소재를 아무 클립도 참조하지 않는다"


def test_transition_attaches_to_preceding_clip(video, library, tmp_path):
    """캡컷은 전환을 '앞' 클립에 붙인다."""
    sheet = Cuesheet(transitions=[TransitionCue(5.0, "dissolve", 0.5)])
    _, content = _build(video, library, tmp_path, sheet, "앞클립")

    materials = _transition_materials(content)
    segs = _video_segments(content)
    assert any(r in materials for r in segs[0]["extra_material_refs"])
    assert not any(r in materials for r in segs[1]["extra_material_refs"])


def test_transition_snaps_to_nearby_boundary(video, library, tmp_path):
    sheet = Cuesheet(transitions=[TransitionCue(5.2, "dissolve", 0.5)])   # 5.0 경계 근처
    report, _ = _build(video, library, tmp_path, sheet, "스냅")
    assert report.transitions == 1
    assert report.warnings == []


def test_transition_far_from_boundary_is_skipped(video, library, tmp_path):
    sheet = Cuesheet(transitions=[TransitionCue(2.5, "dissolve", 0.5)])
    report, content = _build(video, library, tmp_path, sheet, "경계아님")
    assert report.transitions == 0
    assert any("컷 경계가 없어" in w for w in report.warnings)
    assert _transition_materials(content) == {}


def test_no_transition_at_zoom_split(video, library, tmp_path):
    """줌 때문에 쪼갠 자리는 화면이 이어지므로 전환을 걸면 안 된다."""
    sheet = Cuesheet(
        zooms=[ZoomCue(2.0, 3.0, 1.3)],                      # 첫 컷 안을 쪼갬
        transitions=[TransitionCue(3.0, "dissolve", 0.5)],   # 그 쪼갠 자리를 노림
    )
    report, _ = _build(video, library, tmp_path, sheet, "줌자리")
    assert report.zooms == 1
    assert report.transitions == 0, "줌 분할 자리에 전환이 들어갔다"
    assert any("컷 경계가 없어" in w for w in report.warnings)


def test_two_transitions_on_two_boundaries(video, library, tmp_path):
    sheet = Cuesheet(transitions=[
        TransitionCue(5.0, "dissolve", 0.5),
        TransitionCue(10.0, "flash_white", 0.3),
    ])
    report, content = _build(video, library, tmp_path, sheet, "두개")
    assert report.transitions == 2
    names = {t["name"] for t in _transition_materials(content).values()}
    assert len(names) == 2


def test_duration_is_honoured(video, library, tmp_path):
    sheet = Cuesheet(transitions=[TransitionCue(5.0, "dissolve", 0.8)])
    _, content = _build(video, library, tmp_path, sheet, "길이")
    (material,) = _transition_materials(content).values()
    assert material["duration"] == pytest.approx(800_000, abs=2000)


def test_unknown_transition_warns(video, library, tmp_path):
    sheet = Cuesheet(transitions=[TransitionCue(5.0, "없는전환", 0.5)])
    report, _ = _build(video, library, tmp_path, sheet, "모르는전환")
    assert report.transitions == 0
    assert any("없는전환" in w for w in report.warnings)


def test_transitions_can_be_disabled(video, library, tmp_path):
    cfg = Config()
    cfg.transition.enabled = False
    sheet = Cuesheet(transitions=[TransitionCue(5.0, "dissolve", 0.5)])
    report, content = _build(video, library, tmp_path, sheet, "끄기", cfg)
    assert report.transitions == 0
    assert _transition_materials(content) == {}


# ---------------------------------------------------------------- 자막 애니


def _caption_anims(content) -> dict[str, list[str]]:
    materials = {m["id"]: m for m in content["materials"]["material_animations"]}
    texts = {m["id"]: json.loads(m["content"])["text"] for m in content["materials"]["texts"]}
    out: dict[str, list[str]] = {}
    for track in content["tracks"]:
        if track["type"] != "text":
            continue
        for seg in track["segments"]:
            names = [a["name"] for r in seg["extra_material_refs"] if r in materials
                     for a in materials[r]["animations"]]
            out[texts[seg["material_id"]]] = names
    return out


def test_caption_anim_overrides_style_default(video, library, tmp_path):
    sheet = Cuesheet(captions=[
        Caption(1.0, 2.0, "타자기", "reaction", anim="typewriter"),
        Caption(3.0, 4.0, "기본값", "reaction"),
    ])
    _, content = _build(video, library, tmp_path, sheet, "애니")
    anims = _caption_anims(content)
    assert "打字机" in anims["타자기"]
    assert "打字机" not in anims["기본값"]      # 스타일 기본값(弹出)을 씀
    assert anims["기본값"], "기본 애니메이션이 빠졌다"


def test_unknown_anim_falls_back_to_style(video, library, tmp_path):
    sheet = Cuesheet(captions=[Caption(1.0, 2.0, "이상함", "reaction", anim="없는애니")])
    report, content = _build(video, library, tmp_path, sheet, "이상한애니")
    assert report.captions == 1
    assert _caption_anims(content)["이상함"], "기본 애니메이션으로라도 들어가야 한다"
