"""트랙 배치와 영상 클립 분할 테스트.

핵심 요구사항 두 가지를 지키는지 본다.
1. 겹친다는 이유로 아무것도 버리지 않는다.
2. 줌 하나가 클립 하나에 대응해서 따로 수정할 수 있다.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yeneung.cuesheet import ZoomCue  # noqa: E402
from yeneung.cuts import KeepRange, TimelineMap  # noqa: E402
from yeneung.packing import MIN_PIECE, pack_tracks, split_video_pieces  # noqa: E402


@dataclass
class Item:
    start: float
    end: float
    tag: str = ""


def _pack(items: list[Item], gap: float = 0.0) -> list[list[Item]]:
    return pack_tracks(items, lambda i: i.start, lambda i: i.end, gap=gap)


# ---------------------------------------------------------------- 트랙 배치


def test_no_overlap_stays_on_one_track():
    tracks = _pack([Item(0, 1), Item(2, 3), Item(4, 5)])
    assert len(tracks) == 1
    assert len(tracks[0]) == 3


def test_overlap_creates_second_track():
    tracks = _pack([Item(0, 2), Item(1, 3)])
    assert len(tracks) == 2
    assert [len(t) for t in tracks] == [1, 1]


def test_nothing_is_dropped():
    items = [Item(0, 5), Item(1, 6), Item(2, 7), Item(3, 8), Item(9, 10)]
    tracks = _pack(items)
    packed = [i for track in tracks for i in track]
    assert len(packed) == len(items)
    assert {id(i) for i in packed} == {id(i) for i in items}


def test_uses_minimum_tracks():
    """4개가 겹쳐 있으면 트랙 4개, 그 이상은 만들지 않는다."""
    tracks = _pack([Item(0, 4), Item(1, 5), Item(2, 6), Item(3, 7)])
    assert len(tracks) == 4


def test_reuses_freed_track():
    """앞 항목이 끝났으면 같은 트랙을 다시 쓴다."""
    tracks = _pack([Item(0, 1), Item(0.5, 1.5), Item(2, 3)])
    assert len(tracks) == 2
    assert len(tracks[0]) == 2  # 0-1 과 2-3 은 같은 트랙


def test_within_track_no_overlap():
    items = [Item(0, 3), Item(1, 4), Item(2, 5), Item(3.5, 6), Item(5.5, 7)]
    for track in _pack(items):
        for a, b in zip(track, track[1:]):
            assert b.start >= a.end, "같은 트랙에서 겹침 발생"


def test_gap_forces_separation():
    """간격을 요구하면 살짝 떨어진 것도 다른 트랙으로 간다."""
    assert len(_pack([Item(0, 1), Item(1.05, 2)], gap=0.0)) == 1
    assert len(_pack([Item(0, 1), Item(1.05, 2)], gap=0.2)) == 2


def test_empty_input():
    assert _pack([]) == []


# ---------------------------------------------------------------- 클립 분할


def _timeline() -> tuple[list[KeepRange], TimelineMap]:
    keeps = [KeepRange(0.0, 5.0), KeepRange(10.0, 15.0)]   # 편집본 0-5, 5-10
    return keeps, TimelineMap(keeps)


def _split(zooms, min_scale=1.05, max_scale=1.6):
    keeps, timeline = _timeline()
    return split_video_pieces(keeps, timeline, zooms, min_scale=min_scale, max_scale=max_scale)


def test_no_zoom_gives_one_piece_per_keep():
    pieces = _split([])
    assert len(pieces) == 2
    assert all(p.zoom_scale is None for p in pieces)


def test_pieces_are_contiguous_and_cover_timeline():
    pieces = _split([ZoomCue(2.0, 3.0, 1.3), ZoomCue(6.0, 7.5, 1.4)])
    cursor = 0.0
    for piece in pieces:
        assert piece.cut_start == pytest.approx(cursor)
        cursor = piece.cut_end
    assert cursor == pytest.approx(10.0)


def test_zoom_gets_its_own_piece():
    pieces = _split([ZoomCue(2.0, 3.0, 1.3)])
    zoomed = [p for p in pieces if p.zoom_scale is not None]
    assert len(zoomed) == 1
    assert zoomed[0].cut_start == pytest.approx(2.0)
    assert zoomed[0].duration == pytest.approx(1.0)
    assert zoomed[0].zoom_scale == pytest.approx(1.3)


def test_source_time_follows_the_split():
    """두 번째 컷 구간(원본 10초부터)의 줌도 원본 시각이 맞아야 한다."""
    pieces = _split([ZoomCue(6.0, 7.0, 1.25)])
    zoomed = next(p for p in pieces if p.zoom_scale is not None)
    # 편집본 6.0초 = 두 번째 구간의 1.0초 지점 = 원본 11.0초
    assert zoomed.source_start == pytest.approx(11.0)


def test_scale_is_clamped():
    assert _split([ZoomCue(1.0, 2.0, 9.0)])[1].zoom_scale == pytest.approx(1.6)
    assert _split([ZoomCue(1.0, 2.0, 1.0)])[1].zoom_scale == pytest.approx(1.05)


def test_zoom_spanning_a_cut_is_clipped_to_the_keep():
    """줌이 컷 경계를 넘으면 각 구간 안쪽으로 잘린다."""
    pieces = _split([ZoomCue(4.0, 6.0, 1.3)])
    zoomed = [p for p in pieces if p.zoom_scale is not None]
    assert len(zoomed) == 2
    assert zoomed[0].cut_end == pytest.approx(5.0)   # 첫 구간 끝에서 멈춤
    assert zoomed[1].cut_start == pytest.approx(5.0)
    # 두 번째 조각은 원본 10초(두 번째 구간 시작)부터
    assert zoomed[1].source_start == pytest.approx(10.0)


def test_overlapping_zooms_first_one_wins():
    """한 클립에 배율 두 개는 불가능하므로 앞의 줌만 적용된다."""
    pieces = _split([ZoomCue(1.0, 3.0, 1.3), ZoomCue(2.0, 4.0, 1.5)])
    scales = [p.zoom_scale for p in pieces if p.zoom_scale is not None]
    assert scales == [pytest.approx(1.3)]


def test_too_short_zoom_is_ignored():
    pieces = _split([ZoomCue(2.0, 2.0 + MIN_PIECE / 2, 1.3)])
    assert all(p.zoom_scale is None for p in pieces)
    assert len(pieces) == 2  # 쓸데없이 쪼개지도 않는다


def test_no_sliver_pieces():
    """프레임 몇 개짜리 조각이 생기면 안 된다."""
    pieces = _split([ZoomCue(0.05, 1.0, 1.3), ZoomCue(4.95, 5.0, 1.2)])
    for piece in pieces:
        assert piece.duration >= MIN_PIECE * 0.999, f"너무 짧은 조각 {piece}"


def test_zoom_covering_whole_keep():
    pieces = _split([ZoomCue(0.0, 5.0, 1.4)])
    first = pieces[0]
    assert first.cut_start == pytest.approx(0.0)
    assert first.duration == pytest.approx(5.0)
    assert first.zoom_scale == pytest.approx(1.4)
