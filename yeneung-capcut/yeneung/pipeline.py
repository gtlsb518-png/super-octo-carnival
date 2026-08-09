"""전체 파이프라인: 영상 → 캡컷 예능 초안."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import cuesheet as cuesheet_mod
from . import cuts, draft, media, sfxgen, sfxlib, styleref, styles, transcribe
from .config import POSITION_OVERRIDES, Config, find_capcut_draft_dir

Log = Callable[[str], None]

_SAMPLE_RATE = 16_000


@dataclass
class RunOptions:
    video: Path
    draft_name: str
    work_dir: Path
    draft_dir: Path | None = None
    allow_replace: bool = False
    dry_run: bool = False
    refresh_transcript: bool = False
    refresh_cuesheet: bool = False
    #: 직접 쓴 큐시트 파일. 주면 Claude 를 호출하지 않는다
    cuesheet: Path | None = None


def _remap(
    utterances: list[transcribe.Utterance], timeline: cuts.TimelineMap
) -> list[transcribe.Utterance]:
    """원본 시각의 대사를 컷 편집 후 시각으로 옮긴다."""
    out: list[transcribe.Utterance] = []
    for u in utterances:
        start = timeline.to_cut(u.start)
        end = timeline.to_cut(u.end)
        if end - start >= 0.15:  # 통째로 잘려나간 대사는 버린다
            out.append(transcribe.Utterance(start=start, end=end, text=u.text))
    return out


def run(opts: RunOptions, cfg: Config, log: Log = print) -> draft.BuildReport:
    opts.work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 영상 정보 --------------------------------------------------------
    info = media.probe(opts.video)
    log(f"영상: {info.width}x{info.height} @ {info.fps:.2f}fps, {info.duration:.1f}초")
    if not info.has_audio:
        log("  ! 오디오 트랙이 없습니다 — 컷/받아쓰기를 건너뜁니다")

    # 2. 무음 구간 컷 -----------------------------------------------------
    if cfg.cut.enabled and info.has_audio:
        log("무음 구간 분석 중...")
        samples = media.decode_audio_mono(opts.video, _SAMPLE_RATE)
        keeps = cuts.detect_keep_ranges(samples, _SAMPLE_RATE, info.duration, cfg.cut)
    else:
        keeps = [cuts.KeepRange(0.0, info.duration)]

    timeline = cuts.TimelineMap(keeps)
    removed = info.duration - timeline.total
    log(
        f"컷: {len(keeps)}개 구간, {timeline.total:.1f}초 "
        f"(원본 대비 {removed:.1f}초 단축, {removed / max(info.duration, 1e-9) * 100:.0f}%)"
    )

    # 3. 받아쓰기 ---------------------------------------------------------
    transcript_path = opts.work_dir / "transcript.json"
    if info.has_audio:
        if transcript_path.exists() and not opts.refresh_transcript:
            utterances = transcribe.load(transcript_path)
            log(f"받아쓰기: 캐시 사용 ({len(utterances)}줄) — {transcript_path.name}")
        else:
            log(f"받아쓰기 중 (whisper {cfg.transcribe.model})... 처음이면 모델을 내려받습니다")
            utterances = transcribe.transcribe(opts.video, cfg.transcribe)
            transcribe.save(utterances, transcript_path)
            log(f"받아쓰기: {len(utterances)}줄")
    else:
        utterances = []

    cut_utterances = _remap(utterances, timeline)
    srt_path = opts.work_dir / "dialogue.srt"
    if cut_utterances:
        transcribe.write_srt(cut_utterances, srt_path)

    # 4. 효과음 라이브러리 ------------------------------------------------
    if cfg.sfx.enabled:
        root = Path(cfg.sfx.library).resolve()
        library = sfxlib.load_library(root)
        if not library and cfg.sfx.autogenerate:
            # 처음 돌릴 때 효과음이 없으면 기본 팩을 만들어 준다.
            # 합성으로 만드는 것이라 저작권 문제가 없고 1초도 안 걸린다.
            log(f"효과음이 없어 기본 팩을 생성합니다 — {root}")
            created, _ = sfxgen.generate_pack(root)
            log(f"  {len(created)}개 생성 (마음에 안 들면 같은 이름 파일로 덮어쓰세요)")
            library = sfxlib.load_library(root)

        if not library:
            log(f"  ! 효과음이 없습니다 ({root}) — 효과음 없이 진행합니다")
        else:
            log(f"효과음: {len(library)}개 사용 가능 — {root}")
            undescribed = [s.name for s in library if s.description == s.name]
            if undescribed:
                log(f"  ! {len(undescribed)}개에 설명이 없습니다. "
                    f"`yeneung sfx scan` 을 돌리면 훨씬 잘 골라 씁니다")
            if len(library) > cfg.sfx.max_catalog:
                log(f"  ! 효과음이 많아 앞 {cfg.sfx.max_catalog}개만 후보로 씁니다 "
                    f"(config 의 sfx.max_catalog 로 조절)")
    else:
        library = sfxlib.SfxLibrary({}, Path("."))

    effect_names = styles.available_effects() if cfg.effect.enabled else []

    # 자막 스타일 예시 (내가 예전에 쓰던 자막)
    style_examples: list[str] | None = None
    if cfg.cuesheet.style_ref:
        style_examples = styleref.load(cfg.cuesheet.style_ref)
        log(f"자막 스타일 예시: {len(style_examples)}줄 — {cfg.cuesheet.style_ref}")

    # 5. 큐시트 -----------------------------------------------------------
    sheet_path = opts.work_dir / "cuesheet.json"
    if opts.cuesheet:
        # 직접 쓴 큐시트를 쓴다. 사용자 파일은 건드리지 않는다.
        sheet = cuesheet_mod.Cuesheet.load(opts.cuesheet)
        for warning in cuesheet_mod.check_names(
            sheet,
            style_names=list(cfg.styles),
            sfx_names=library.names(),
            effect_names=effect_names,
            positions=POSITION_OVERRIDES,
        ):
            log(f"  ! {warning}")
        sheet = cuesheet_mod.sanitize(sheet, timeline.total)
        log(f"큐시트: 직접 준 파일 사용 — {sheet.summary()} ({opts.cuesheet})")
    elif sheet_path.exists() and not opts.refresh_cuesheet:
        sheet = cuesheet_mod.Cuesheet.load(sheet_path)
        log(f"큐시트: 캐시 사용 — {sheet.summary()}")
    elif not cut_utterances:
        sheet = cuesheet_mod.Cuesheet()
        log("큐시트: 대사가 없어 건너뜁니다")
    else:
        log(f"큐시트 생성 중 ({cfg.cuesheet.model}, 밀도 {cfg.cuesheet.density})...")

        def progress(i: int, n: int) -> None:
            log(f"  구간 {i}/{n}")

        sheet = cuesheet_mod.generate(
            cut_utterances,
            timeline.total,
            cfg.cuesheet,
            style_names=list(cfg.styles),
            sfx_names=library.names()[: cfg.sfx.max_catalog],
            effect_names=effect_names,
            style_ref=style_examples,
            progress=progress,
        )
        sheet.save(sheet_path)
        log(f"큐시트: {sheet.summary()}")

    # 6. 초안 생성 ---------------------------------------------------------
    if opts.dry_run:
        log("\n--- dry-run: 캡컷 초안을 쓰지 않았습니다 ---")
        log(f"큐시트 미리보기: {sheet_path}")
        _preview(sheet, log)
        return draft.BuildReport(
            draft_path=Path("(dry-run)"), name=opts.draft_name, duration=timeline.total,
            clips=len(keeps), captions=len(sheet.captions), subtitles=len(cut_utterances),
            sfx=len(sheet.sfx), zooms=len(sheet.zooms), effects=len(sheet.effects),
            warnings=[],
        )

    draft_dir = opts.draft_dir or (Path(cfg.draft_dir) if cfg.draft_dir else find_capcut_draft_dir())
    if draft_dir is None:
        raise RuntimeError(
            "캡컷 초안 폴더를 찾지 못했습니다. --draft-dir 로 직접 지정해 주세요.\n"
            r"보통 위치: %LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft"
        )

    log(f"캡컷 초안 생성 중: {draft_dir / opts.draft_name}")
    return draft.build(
        video=info, keeps=keeps, timeline=timeline, sheet=sheet, cfg=cfg,
        draft_dir=Path(draft_dir), draft_name=opts.draft_name, sfx_library=library,
        srt_path=srt_path if cut_utterances else None,
        allow_replace=opts.allow_replace,
    )


def _preview(sheet: cuesheet_mod.Cuesheet, log: Log, limit: int = 12) -> None:
    for cap in sheet.captions[:limit]:
        log(f"  {cap.start:6.2f}s  [{cap.style:9s}] {cap.text}")
    if len(sheet.captions) > limit:
        log(f"  ... 외 {len(sheet.captions) - limit}개")
