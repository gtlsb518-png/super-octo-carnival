"""큐시트를 캡컷 초안 프로젝트로 굽는다.

두 가지 원칙으로 배치한다.

1. 아무것도 버리지 않는다. 겹치는 항목이 있으면 트랙을 늘려서 나눠 담는다.
2. 하나씩 고칠 수 있게 만든다. 컷은 클립 분할로, 줌은 줌 구간만큼의 클립으로
   떼어놓아서 캡컷에서 해당 클립만 잡고 수정할 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import styles as style_map
from .config import POSITION_OVERRIDES, Config
from .cuesheet import Cuesheet
from .cuts import KeepRange, TimelineMap
from .media import VideoInfo
from .packing import VideoPiece, pack_tracks, split_video_pieces
from .sfxlib import SfxLibrary

US = 1_000_000  # 캡컷의 시간 단위는 마이크로초

TRACK_MAIN = "main"
TRACK_SUBTITLE = "subtitle"


@dataclass
class BuildReport:
    draft_path: Path
    name: str
    duration: float
    clips: int
    captions: int
    subtitles: int
    sfx: int
    zooms: int
    effects: int
    transitions: int = 0
    warnings: list[str] = field(default_factory=list)
    #: 종류별로 만들어진 트랙 수 (겹침 때문에 늘어난 만큼)
    tracks: dict[str, int] = field(default_factory=dict)


def _us(seconds: float) -> int:
    return int(round(max(0.0, seconds) * US))


def _track_names(prefix: str, count: int) -> list[str]:
    return [prefix if i == 0 else f"{prefix}{i + 1}" for i in range(count)]


def build(
    *,
    video: VideoInfo,
    keeps: list[KeepRange],
    timeline: TimelineMap,
    sheet: Cuesheet,
    cfg: Config,
    draft_dir: Path,
    draft_name: str,
    sfx_library: SfxLibrary,
    srt_path: Path | None = None,
    allow_replace: bool = False,
) -> BuildReport:
    from pycapcut import (
        AudioMaterial, AudioSegment, ClipSettings, DraftFolder, KeyframeProperty,
        TextBorder, TextSegment, TextStyle, Timerange, TrackType, VideoMaterial,
        VideoSegment,
    )

    warnings: list[str] = []
    track_counts: dict[str, int] = {}

    folder = DraftFolder(str(draft_dir))
    script = folder.create_draft(
        draft_name, video.width, video.height,
        fps=int(round(video.fps)) or 30, allow_replace=allow_replace,
    )

    # ---------------------------------------------------------- 영상 클립
    # 컷 구간을 줌 경계에서 한 번 더 쪼갠다. 줌 하나 = 클립 하나.
    zooms = sheet.zooms if cfg.zoom.enabled else []
    pieces = split_video_pieces(
        keeps, timeline, zooms,
        min_scale=cfg.zoom.min_scale, max_scale=cfg.zoom.max_scale,
    )
    if not pieces:
        raise RuntimeError("배치할 영상 클립이 없습니다. 컷 설정을 확인해 주세요.")

    script.add_track(TrackType.video, TRACK_MAIN)
    material = VideoMaterial(str(video.path))

    total = max(p.cut_end for p in pieces)
    zoom_count = 0
    placed: list[tuple[VideoPiece, Any]] = []
    for piece in pieces:
        dur = _us(piece.duration)
        if dur <= 0:
            continue
        seg = VideoSegment(
            material,
            Timerange(_us(piece.cut_start), dur),
            source_timerange=Timerange(_us(piece.source_start), dur),
        )
        if piece.zoom_scale is not None:
            _apply_zoom(seg, piece, cfg.zoom.ramp, KeyframeProperty.uniform_scale)
            zoom_count += 1
        placed.append((piece, seg))

    # 전환은 반드시 트랙에 넣기 **전에** 붙여야 한다.
    # add_segment 가 그 시점의 전환 소재를 등록하므로, 나중에 붙이면
    # 참조만 남고 소재가 빠져 캡컷에서 전환이 사라진다.
    transition_count = 0
    if cfg.transition.enabled and sheet.transitions:
        transition_count = _apply_transitions(
            placed, sheet.transitions, cfg.transition.snap, warnings
        )

    for _piece, seg in placed:
        script.add_segment(seg, TRACK_MAIN)
    track_counts["video"] = 1

    # ---------------------------------------------------------- 화면 효과
    effect_count = 0
    if cfg.effect.enabled and sheet.effects:
        resolved = []
        for cue in sheet.effects:
            eff = style_map.effect_type(cue.name)
            if eff is None:
                warnings.append(f"화면효과 '{cue.name}' 이 이 캡컷 버전에 없습니다")
                continue
            end = min(cue.end, total)
            if end > cue.start:
                resolved.append((cue.start, end, eff))

        groups = pack_tracks(resolved, lambda r: r[0], lambda r: r[1])
        for name, group in zip(_track_names("fx", len(groups)), groups):
            script.add_track(TrackType.effect, name)
            for start, end, eff in group:
                script.add_effect(eff, Timerange(_us(start), _us(end - start)), name)
                effect_count += 1
        track_counts["effect"] = len(groups)

    # ---------------------------------------------------------- 효과음
    sfx_count = 0
    if cfg.sfx.enabled and sheet.sfx:
        cache: dict[str, Any] = {}
        resolved = []
        for cue in sheet.sfx:
            entry = sfx_library.get(cue.name)
            if entry is None:
                warnings.append(f"효과음 '{cue.name}' 을 라이브러리에서 찾지 못했습니다")
                continue
            key = str(entry.path)
            if key not in cache:
                cache[key] = AudioMaterial(key)
            audio = cache[key]
            dur = min(audio.duration / US, max(0.05, total - cue.time))
            if dur > 0:
                resolved.append((cue.time, cue.time + dur, audio, dur))

        groups = pack_tracks(resolved, lambda r: r[0], lambda r: r[1])
        for name, group in zip(_track_names("sfx", len(groups)), groups):
            script.add_track(TrackType.audio, name)
            for start, _end, audio, dur in group:
                script.add_segment(
                    AudioSegment(
                        audio, Timerange(_us(start), _us(dur)),
                        source_timerange=Timerange(0, _us(dur)),
                        volume=cfg.sfx.volume,
                    ),
                    name,
                )
                sfx_count += 1
        track_counts["audio"] = len(groups)

    # ---------------------------------------------------------- 대사 자막
    subtitle_count = 0
    if cfg.subtitle.enabled and srt_path and srt_path.exists():
        sub = cfg.subtitle
        try:
            script.import_srt(
                str(srt_path),
                TRACK_SUBTITLE,
                text_style=TextStyle(
                    size=sub.size, bold=sub.bold, color=sub.color, align=1,
                    auto_wrapping=True, max_line_width=sub.max_line_width,
                ),
                clip_settings=ClipSettings(transform_y=sub.position_y),
            )
            subtitle_count = _count_srt_entries(srt_path)
        except Exception as exc:  # 자막 하나 때문에 전체가 죽으면 안 된다
            warnings.append(f"대사 자막 가져오기 실패: {exc}")

    # ---------------------------------------------------------- 예능 자막
    caption_count = 0
    if sheet.captions:
        usable = [c for c in sheet.captions if min(c.end, total) > c.start]
        groups = pack_tracks(usable, lambda c: c.start, lambda c: min(c.end, total))

        for name, group in zip(_track_names("caption", len(groups)), groups):
            script.add_track(TrackType.text, name)
            for cap in group:
                preset = cfg.styles.get(cap.style)
                if preset is None:
                    warnings.append(f"모르는 자막 스타일 '{cap.style}' — reaction 으로 대체")
                    preset = cfg.styles["reaction"]

                pos_y = POSITION_OVERRIDES.get(cap.position, preset.position_y)
                start, end = max(0.0, cap.start), min(cap.end, total)

                seg = TextSegment(
                    cap.text,
                    Timerange(_us(start), _us(end - start)),
                    style=TextStyle(
                        size=preset.size, bold=preset.bold, color=preset.color, align=1,
                    ),
                    border=TextBorder(color=preset.border_color, width=preset.border_width),
                    clip_settings=ClipSettings(transform_y=pos_y),
                )

                intro = style_map.caption_anim_type(
                    cap.anim if cap.anim and cap.anim != "default" else None
                )
                if intro is None:
                    intro = style_map.intro_type(preset.intro)
                if intro is not None:
                    seg.add_animation(intro, _us(preset.intro_ms / 1000.0))
                loop = style_map.loop_type(preset.loop)
                if loop is not None:
                    try:
                        seg.add_animation(loop)
                    except Exception:
                        pass  # 등장 애니와 충돌하는 버전이 있어 무시

                script.add_segment(seg, name)
                caption_count += 1

        track_counts["caption"] = len(groups)

    script.save()

    return BuildReport(
        draft_path=Path(draft_dir) / draft_name,
        name=draft_name,
        duration=total,
        clips=len(pieces),
        captions=caption_count,
        subtitles=subtitle_count,
        sfx=sfx_count,
        zooms=zoom_count,
        effects=effect_count,
        transitions=transition_count,
        warnings=warnings,
        tracks=track_counts,
    )


def _count_srt_entries(path: Path) -> int:
    """SRT 자막 개수 = '-->' 줄 수."""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "-->" in line)


def _apply_transitions(placed, cues, snap: float, warnings: list[str]) -> int:
    """전환을 실제 컷 경계에 붙인다.

    캡컷은 전환을 **앞 클립**에 붙인다. 그리고 줌 때문에 쪼갠 자리는 화면이
    이어지므로 전환을 걸면 안 된다 — 원본이 실제로 건너뛴 자리만 고른다.
    """
    boundaries: dict[int, Any] = {}   # 경계 시각(ms) → 앞 클립
    for (before, seg), (after, _) in zip(placed, placed[1:]):
        if before.keep_index != after.keep_index:
            boundaries[int(round(after.cut_start * 1000))] = seg

    if not boundaries:
        if cues:
            warnings.append("컷 경계가 없어 장면 전환을 넣지 못했습니다")
        return 0

    used: set[int] = set()
    count = 0
    for cue in cues:
        kind = style_map.transition_type(cue.name)
        if kind is None:
            warnings.append(f"장면 전환 '{cue.name}' 이 이 캡컷 버전에 없습니다")
            continue

        want = int(round(cue.time * 1000))
        near = min(boundaries, key=lambda b: abs(b - want))
        if abs(near - want) > snap * 1000:
            warnings.append(
                f"{cue.time:.2f}초에는 컷 경계가 없어 전환 '{cue.name}' 을 건너뜁니다"
            )
            continue
        if near in used:
            continue  # 한 경계에 전환은 하나만

        try:
            boundaries[near].add_transition(kind, duration=_us(cue.duration))
        except ValueError:
            continue  # 이미 전환이 붙은 클립
        used.add(near)
        count += 1

    return count


def _apply_zoom(segment, piece: VideoPiece, ramp: float, prop) -> None:
    """조각 하나에 줌 키프레임을 건다. 시각은 클립 시작 기준이다.

    조각이 곧 줌 구간이므로 0 에서 시작해 끝에서 원래대로 돌아온다.
    """
    assert piece.zoom_scale is not None
    ramp = min(ramp, piece.duration / 2.0)
    for offset, value in (
        (0.0, 1.0),
        (ramp, piece.zoom_scale),
        (piece.duration - ramp, piece.zoom_scale),
        (piece.duration, 1.0),
    ):
        segment.add_keyframe(prop, _us(offset), value)
