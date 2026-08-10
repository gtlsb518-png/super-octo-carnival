"""데모 큐시트 테스트.

데모는 진단 도구다. 캡컷에서 열었을 때 무엇이 잘못됐는지 눈으로 짚을 수
있어야 하므로, 자막이 위치·스타일·애니메이션을 골고루 훑는지 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung import demo, styles  # noqa: E402
from yeneung.config import DEFAULT_STYLES, POSITION_OVERRIDES  # noqa: E402

_SFX = ["ding", "boing", "drum", "swoosh", "pop", "sparkle"]
_EFFECTS = ["flash", "shake", "zoom_pulse", "glitch"]
_TRANSITIONS = ["dissolve", "flash_white", "fade_black", "blur"]


def _build(total: float, boundaries=None):
    return demo.build_cuesheet(
        total, boundaries=boundaries or [],
        sfx_names=_SFX, effect_names=_EFFECTS, transition_names=_TRANSITIONS,
    )


# ---------------------------------------------------------------- 기본 동작


def test_short_video_gives_empty_sheet():
    assert _build(1.0).captions == []


def test_covers_positions_top_to_bottom():
    """①②③④ 로 위치를 확인할 수 있어야 한다."""
    sheet = _build(30.0)
    labelled = [c for c in sheet.captions if c.text[0] in "①②③④"]
    assert len(labelled) == 4

    ys = [POSITION_OVERRIDES[c.position] for c in labelled]
    assert ys == sorted(ys, reverse=True), "위에서 아래 순서가 아니다"
    assert [c.position for c in labelled] == ["top", "upper", "center", "lower"]


def test_labels_appear_in_order_on_timeline():
    """①②③④ 가 시간 순서대로 떠야 위치를 헷갈리지 않는다."""
    sheet = _build(30.0)
    labelled = [c for c in sheet.captions if c.text[0] in "①②③④"]
    assert labelled == sorted(labelled, key=lambda c: c.start)


def test_covers_every_style():
    sheet = _build(30.0)
    used = {c.style for c in sheet.captions}
    assert used == set(DEFAULT_STYLES), f"안 쓰인 스타일: {set(DEFAULT_STYLES) - used}"


def test_uses_several_animations():
    sheet = _build(30.0)
    anims = {c.anim for c in sheet.captions}
    assert len(anims) >= 5, "애니메이션이 다양하지 않으면 비교가 안 된다"
    assert all(a in styles.available_caption_anims() for a in anims)


def test_captions_do_not_overlap():
    sheet = _build(30.0)
    ordered = sorted(sheet.captions, key=lambda c: c.start)
    for a, b in zip(ordered, ordered[1:]):
        assert b.start >= a.end, "데모 자막이 겹치면 트랙이 늘어나 헷갈린다"


def test_captions_stay_inside_video():
    total = 20.0
    for cap in _build(total).captions:
        assert 0.0 <= cap.start < cap.end <= total


# ---------------------------------------------------------------- 나머지 큐


def test_sfx_lands_with_captions():
    """효과음은 자막이 뜨는 순간에 겹쳐야 들었을 때 확인이 된다."""
    sheet = _build(30.0)
    starts = {round(c.start, 3) for c in sheet.captions}
    assert sheet.sfx
    for cue in sheet.sfx:
        assert round(cue.time, 3) in starts


def test_only_known_names_are_used():
    sheet = _build(30.0)
    assert all(c.name in _SFX for c in sheet.sfx)
    assert all(c.name in _EFFECTS for c in sheet.effects)
    assert all(c.name in _TRANSITIONS for c in sheet.transitions)


def test_empty_pools_produce_nothing():
    sheet = demo.build_cuesheet(30.0, boundaries=[5.0], sfx_names=[],
                                effect_names=[], transition_names=[])
    assert sheet.captions            # 자막은 그대로 나온다
    assert sheet.sfx == []
    assert sheet.effects == []
    assert sheet.transitions == []


def test_transitions_only_at_given_boundaries():
    sheet = _build(30.0, boundaries=[8.0, 16.0])
    assert [c.time for c in sheet.transitions] == [8.0, 16.0]


def test_no_boundaries_no_transitions():
    assert _build(30.0, boundaries=[]).transitions == []


def test_zooms_are_long_enough_to_notice():
    for cue in _build(30.0).zooms:
        assert cue.end - cue.start >= 0.8
        assert 1.0 < cue.scale <= 1.6


def test_checklist_is_present():
    assert len(demo.CHECKLIST) >= 5
    assert any("한글" in item for item in demo.CHECKLIST)
    assert any("전환" in item for item in demo.CHECKLIST)
