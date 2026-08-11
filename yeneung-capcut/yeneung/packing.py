"""타임라인 배치 계산.

캡컷 한 트랙에는 겹치는 클립을 놓을 수 없다. 그래서 겹치는 항목이 있으면
트랙을 필요한 만큼 늘려서 나눠 담는다. 아무것도 버리지 않는 게 목적이다.

영상 클립도 줌 구간 경계에서 쪼갠다. 그래야 줌 하나가 클립 하나에 대응해서
캡컷에서 그 줌만 따로 잡고 고칠 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

from .cuts import KeepRange, TimelineMap

T = TypeVar("T")

#: 이보다 짧은 영상 조각은 만들지 않는다 (프레임 몇 개짜리 클립 방지)
MIN_PIECE = 0.20


def pack_tracks(
    items: Sequence[T],
    start_of: Callable[[T], float],
    end_of: Callable[[T], float],
    *,
    gap: float = 0.0,
) -> list[list[T]]:
    """겹치지 않도록 항목들을 여러 트랙에 나눠 담는다.

    시작 시각 순으로 훑으면서 들어갈 수 있는 첫 트랙에 넣는다. 트랙 수를
    최소로 만드는 고전적인 구간 분할 방법이다.

    Args:
        gap: 같은 트랙에 이어 놓을 때 요구하는 최소 간격(초).

    Returns:
        트랙별 항목 목록. 겹치는 게 없으면 트랙 하나만 나온다.
    """
    tracks: list[list[T]] = []
    track_ends: list[float] = []

    for item in sorted(items, key=start_of):
        start, end = start_of(item), end_of(item)
        for i, last_end in enumerate(track_ends):
            if start >= last_end + gap:
                tracks[i].append(item)
                track_ends[i] = end
                break
        else:
            tracks.append([item])
            track_ends.append(end)

    return tracks


@dataclass
class VideoPiece:
    """타임라인에 놓일 영상 클립 한 조각."""

    source_start: float   # 원본 영상에서의 시작 시각
    cut_start: float      # 편집본 타임라인에서의 시작 시각
    duration: float
    zoom_scale: float | None = None   # 이 조각 전체에 걸리는 줌 배율
    keep_index: int = 0   # 몇 번째 컷 구간에서 나왔는지

    @property
    def cut_end(self) -> float:
        return self.cut_start + self.duration


def split_video_pieces(
    keeps: Sequence[KeepRange],
    timeline: TimelineMap,
    zooms: Iterable,
    *,
    min_scale: float,
    max_scale: float,
) -> list[VideoPiece]:
    """컷 구간을 줌 경계에서 다시 쪼갠다.

    줌 하나가 클립 하나에 정확히 대응하게 만들어, 캡컷에서 그 클립만 잡고
    길이나 배율을 고칠 수 있게 한다.
    """
    zoom_list = sorted(zooms, key=lambda z: z.start)
    pieces: list[VideoPiece] = []

    for index, keep in enumerate(keeps):
        cut_start = timeline.cut_start_of(index)
        cut_end = cut_start + keep.duration

        # 이 컷 구간 안으로 잘라낸 줌들
        local: list[tuple[float, float, float]] = []
        for z in zoom_list:
            lo, hi = max(z.start, cut_start), min(z.end, cut_end)
            if hi - lo >= MIN_PIECE:
                scale = max(min_scale, min(z.scale, max_scale))
                local.append((lo, hi, scale))

        # 겹치는 줌은 앞의 것을 우선 (한 클립에 배율 두 개는 불가능)
        chosen: list[tuple[float, float, float]] = []
        for lo, hi, scale in local:
            if chosen and lo < chosen[-1][1]:
                continue
            chosen.append((lo, hi, scale))

        boundaries = [cut_start]
        for lo, hi, _ in chosen:
            boundaries += [lo, hi]
        boundaries.append(cut_end)
        boundaries = _tidy(boundaries, cut_start, cut_end)

        for a, b in zip(boundaries, boundaries[1:]):
            scale = next((s for lo, hi, s in chosen if lo <= a < hi), None)
            pieces.append(
                VideoPiece(
                    source_start=keep.start + (a - cut_start),
                    cut_start=a,
                    duration=b - a,
                    zoom_scale=scale,
                    keep_index=index,
                )
            )

    return pieces


def cut_boundaries(timeline: TimelineMap) -> list[float]:
    """실제 '컷' 이 일어난 시각들.

    줌 때문에 쪼갠 자리는 화면이 이어지므로 전환을 넣으면 안 된다.
    여기서 돌려주는 건 원본에서 내용이 실제로 건너뛴 지점뿐이다.
    맨 앞(0초)과 맨 끝은 전환을 걸 상대가 없으므로 뺀다.
    """
    return [timeline.cut_start_of(i) for i in range(1, len(timeline.keeps))]


def cut_segments(timeline: TimelineMap) -> list[tuple[float, float]]:
    """컷 편집 후 타임라인에서 **메인 영상 클립 하나하나**의 (시작, 끝).

    자막과 효과를 여기에 맞춰 나눠 담는다. 대사만 보고 배급하면 어떤 클립엔
    세 개가 몰리고 어떤 클립은 텅 빈다. 클립이 기준이어야 고르게 들어간다.
    """
    n = len(timeline.keeps)
    starts = [timeline.cut_start_of(i) for i in range(n)]
    ends = starts[1:] + [timeline.total]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


def _tidy(values: list[float], lo: float, hi: float) -> list[float]:
    """경계값을 정렬·중복제거하고, 너무 촘촘한 것은 합친다."""
    out: list[float] = []
    for v in sorted(values):
        v = min(max(v, lo), hi)
        if not out or v - out[-1] >= MIN_PIECE:
            out.append(v)
    if len(out) < 2:
        return [lo, hi]
    out[-1] = hi
    # 마지막 조각이 너무 짧아졌으면 직전 경계를 버린다
    if len(out) > 2 and out[-1] - out[-2] < MIN_PIECE:
        out.pop(-2)
    return out
