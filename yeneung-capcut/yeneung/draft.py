"""큐시트를 캡컷 초안 프로젝트로 굽는다.

컷은 영상을 물리적으로 자르지 않고, 남길 구간마다 클립을 하나씩 놓아
타임라인에 이어 붙인다. 캡컷에서 각 컷을 그대로 다시 늘리거나 줄일 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import styles as style_map
from .config import POSITION_OVERRIDES, Config
from .cuesheet import Cuesheet
from .cuts import KeepRange, TimelineMap
from .media import VideoInfo
from .sfxlib import SfxLibrary

US = 1_000_000  # 캡컷의 시간 단위는 마이크로초

TRACK_MAIN = "main"
TRACK_FX = "fx"
TRACK_SFX = "sfx"
TRACK_SUBTITLE = "subtitle"
TRACK_CAPTION = "caption"


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
    warnings: list[str]


def _us(seconds: float) -> int:
    return int(round(max(0.0, seconds) * US))


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
    folder = DraftFolder(str(draft_dir))
    script = folder.create_draft(
        draft_name, video.width, video.height,
        fps=int(round(video.fps)) or 30, allow_replace=allow_replace,
    )

    # 아래에서 위로 쌓이는 순서대로 트랙을 만든다
    script.add_track(TrackType.video, TRACK_MAIN)
    if cfg.effect.enabled and sheet.effects:
        script.add_track(TrackType.effect, TRACK_FX)
    if cfg.sfx.enabled and sheet.sfx:
        script.add_track(TrackType.audio, TRACK_SFX)

    material = VideoMaterial(str(video.path))

    # ---------------------------------------------------------- 컷 클립 배치
    segments: list = []
    cursor = 0
    for keep in keeps:
        dur = _us(keep.duration)
        if dur <= 0:
            continue
        seg = VideoSegment(
            material,
            Timerange(cursor, dur),
            source_timerange=Timerange(_us(keep.start), dur),
        )
        script.add_segment(seg, TRACK_MAIN)
        segments.append(seg)
        cursor += dur

    total = cursor / US
    if not segments:
        raise RuntimeError("배치할 영상 클립이 없습니다. 컷 설정을 확인해 주세요.")

    # ---------------------------------------------------------- 줌 (키프레임)
    zoom_count = 0
    if cfg.zoom.enabled:
        for cue in sheet.zooms:
            placed = _apply_zoom(
                cue, segments, timeline, cfg, KeyframeProperty.uniform_scale, warnings
            )
            zoom_count += int(placed)

    # ---------------------------------------------------------- 대사 자막(SRT)
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
        script.add_track(TrackType.text, TRACK_CAPTION)
        for cap in sheet.captions:
            preset = cfg.styles.get(cap.style)
            if preset is None:
                warnings.append(f"모르는 자막 스타일 '{cap.style}' — reaction 으로 대체")
                preset = cfg.styles["reaction"]

            pos_y = POSITION_OVERRIDES.get(cap.position, preset.position_y)
            start, end = max(0.0, cap.start), min(cap.end, total)
            if end <= start:
                continue

            seg = TextSegment(
                cap.text,
                Timerange(_us(start), _us(end - start)),
                style=TextStyle(
                    size=preset.size, bold=preset.bold, color=preset.color, align=1,
                ),
                border=TextBorder(color=preset.border_color, width=preset.border_width),
                clip_settings=ClipSettings(transform_y=pos_y),
            )

            intro = style_map.intro_type(preset.intro)
            if intro is not None:
                seg.add_animation(intro, _us(preset.intro_ms / 1000.0))
            loop = style_map.loop_type(preset.loop)
            if loop is not None:
                try:
                    seg.add_animation(loop)
                except Exception:
                    pass  # 등장 애니와 충돌하는 버전이 있어 무시

            script.add_segment(seg, TRACK_CAPTION)
            caption_count += 1

    # ---------------------------------------------------------- 효과음
    sfx_count = 0
    if cfg.sfx.enabled and sheet.sfx:
        for cue in sheet.sfx:
            entry = sfx_library.get(cue.name)
            if entry is None:
                warnings.append(f"효과음 '{cue.name}' 을 라이브러리에서 찾지 못했습니다")
                continue
            audio = AudioMaterial(str(entry.path))
            dur = min(audio.duration, _us(max(0.05, total - cue.time)))
            if dur <= 0:
                continue
            script.add_segment(
                AudioSegment(
                    audio, Timerange(_us(cue.time), dur),
                    source_timerange=Timerange(0, dur), volume=cfg.sfx.volume,
                ),
                TRACK_SFX,
            )
            sfx_count += 1

    # ---------------------------------------------------------- 화면 효과
    effect_count = 0
    if cfg.effect.enabled and sheet.effects:
        for cue in sheet.effects:
            eff = style_map.effect_type(cue.name)
            if eff is None:
                warnings.append(f"화면효과 '{cue.name}' 이 이 캡컷 버전에 없습니다")
                continue
            end = min(cue.end, total)
            if end <= cue.start:
                continue
            script.add_effect(eff, Timerange(_us(cue.start), _us(end - cue.start)), TRACK_FX)
            effect_count += 1

    script.save()

    return BuildReport(
        draft_path=Path(draft_dir) / draft_name,
        name=draft_name,
        duration=total,
        clips=len(segments),
        captions=caption_count,
        subtitles=subtitle_count,
        sfx=sfx_count,
        zooms=zoom_count,
        effects=effect_count,
        warnings=warnings,
    )


def _count_srt_entries(path: Path) -> int:
    """SRT 자막 개수 = '-->' 줄 수."""
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "-->" in line)


def _apply_zoom(cue, segments, timeline: TimelineMap, cfg: Config, prop, warnings) -> bool:
    """줌 큐를 해당 클립의 키프레임으로 변환한다.

    키프레임 시각은 클립 시작 기준이므로, 큐가 걸친 클립을 찾아 그 안으로 자른다.
    """
    located = timeline.segment_of(cue.start)
    if located is None:
        return False
    index, local_start = located
    seg = segments[index]
    clip_len = timeline.keeps[index].duration

    local_end = min(cue.end - timeline.cut_start_of(index), clip_len)
    if local_end - local_start < 0.3:
        return False  # 너무 짧으면 줌이 보이지도 않는다

    scale = max(cfg.zoom.min_scale, min(cue.scale, cfg.zoom.max_scale))
    ramp = min(cfg.zoom.ramp, (local_end - local_start) / 2.0)

    points = [
        (local_start, 1.0),
        (local_start + ramp, scale),
        (local_end - ramp, scale),
        (local_end, 1.0),
    ]
    for offset, value in points:
        seg.add_keyframe(prop, _us(offset), value)
    return True
