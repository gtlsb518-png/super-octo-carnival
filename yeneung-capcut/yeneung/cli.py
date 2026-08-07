"""명령줄 인터페이스."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import sfxlib, styles
from .config import Config, find_capcut_draft_dir, load_config
from .pipeline import RunOptions, run


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="yeneung",
        description="컷편집만 된 영상을 캡컷 예능 프로젝트로 만들어 줍니다.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ------------------------------------------------------------- doctor
    doc = sub.add_parser("doctor", help="환경 점검 (캡컷 폴더, 효과음, 라이브러리)")
    doc.add_argument("-c", "--config", help="config.toml 경로")

    # ---------------------------------------------------------------- sfx
    s = sub.add_parser("sfx", help="효과음 라이브러리 확인")
    s.add_argument("-c", "--config", help="config.toml 경로")

    # ---------------------------------------------------------------- run
    r = sub.add_parser("run", help="영상 → 캡컷 초안 생성")
    r.add_argument("video", type=Path, help="컷편집된 영상 파일")
    r.add_argument("-n", "--name", help="캡컷 프로젝트 이름 (기본: 파일명_예능)")
    r.add_argument("-c", "--config", help="config.toml 경로")
    r.add_argument("--draft-dir", type=Path, help="캡컷 초안 폴더 (기본: 자동 탐지)")
    r.add_argument("--work-dir", type=Path, help="중간 파일 폴더 (기본: 영상 옆 .yeneung)")
    r.add_argument("--density", choices=["low", "normal", "high"], help="자막·효과 밀도")
    r.add_argument("--tone", help="프로그램 톤 (예: '차분한 다큐 예능')")
    r.add_argument("--whisper-model", help="whisper 모델 (기본: large-v3)")
    r.add_argument("--lang", help="받아쓰기 언어 (기본: ko)")
    r.add_argument("--no-cut", action="store_true", help="무음 컷 끄기")
    r.add_argument("--no-sfx", action="store_true", help="효과음 끄기")
    r.add_argument("--no-zoom", action="store_true", help="줌 끄기")
    r.add_argument("--no-effects", action="store_true", help="화면효과 끄기")
    r.add_argument("--no-subtitle", action="store_true", help="대사 자막 끄기")
    r.add_argument("--dry-run", action="store_true", help="큐시트만 만들고 초안은 안 씀")
    r.add_argument("--replace", action="store_true", help="같은 이름 프로젝트 덮어쓰기")
    r.add_argument("--refresh-transcript", action="store_true", help="받아쓰기 캐시 무시")
    r.add_argument("--refresh-cuesheet", action="store_true", help="큐시트 캐시 무시")

    return p


def _apply_overrides(cfg: Config, args: argparse.Namespace) -> Config:
    if args.density:
        cfg.cuesheet.density = args.density
    if args.tone:
        cfg.cuesheet.tone = args.tone
    if args.whisper_model:
        cfg.transcribe.model = args.whisper_model
    if args.lang:
        cfg.transcribe.language = args.lang
    if args.no_cut:
        cfg.cut.enabled = False
    if args.no_sfx:
        cfg.sfx.enabled = False
    if args.no_zoom:
        cfg.zoom.enabled = False
    if args.no_effects:
        cfg.effect.enabled = False
    if args.no_subtitle:
        cfg.subtitle.enabled = False
    return cfg


def _cmd_doctor(cfg: Config) -> int:
    ok = True
    print("== 환경 점검 ==\n")

    for module, package, label in [
        ("pycapcut", "pycapcut", "캡컷 초안 생성"),
        ("pymediainfo", "pymediainfo", "영상 정보 읽기"),
        ("av", "av", "오디오 디코딩"),
        ("numpy", "numpy", "무음 분석"),
        ("faster_whisper", "faster-whisper", "받아쓰기"),
        ("anthropic", "anthropic", "큐시트 생성"),
    ]:
        try:
            __import__(module)
            print(f"  [OK]   {module:16s} — {label}")
        except ImportError:
            ok = False
            print(f"  [없음] {module:16s} — {label}  →  pip install {package}")

    print()
    draft_dir = Path(cfg.draft_dir) if cfg.draft_dir else find_capcut_draft_dir()
    if draft_dir and draft_dir.is_dir():
        existing = [d.name for d in draft_dir.iterdir() if d.is_dir()]
        print(f"  [OK]   캡컷 초안 폴더: {draft_dir}")
        print(f"         기존 프로젝트 {len(existing)}개")
    else:
        ok = False
        print("  [없음] 캡컷 초안 폴더를 찾지 못했습니다.")
        print(r"         보통 %LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft")
        print("         --draft-dir 로 직접 지정하거나 config.toml 에 draft_dir 을 적어주세요.")

    print()
    lib = sfxlib.load_library(cfg.sfx.library)
    if lib:
        print(f"  [OK]   효과음 {len(lib)}개 — {Path(cfg.sfx.library).resolve()}")
    else:
        print(f"  [주의] 효과음이 없습니다 — {Path(cfg.sfx.library).resolve()}")
        print("         효과음 없이도 동작하지만, 넣으면 훨씬 예능다워집니다.")
    for miss in sfxlib.missing_files(cfg.sfx.library):
        print(f"  [주의] 매니페스트에 있으나 파일 없음: {miss}")

    print()
    try:
        avail = styles.available_effects()
        print(f"  [OK]   화면효과 {len(avail)}개 사용 가능: {', '.join(avail)}")
    except Exception as exc:
        print(f"  [주의] 화면효과 목록을 읽지 못했습니다: {exc}")

    print("\n" + ("모두 준비됐습니다." if ok else "위 [없음] 항목을 먼저 해결해 주세요."))
    return 0 if ok else 1


def _cmd_sfx(cfg: Config) -> int:
    lib = sfxlib.load_library(cfg.sfx.library)
    root = Path(cfg.sfx.library).resolve()
    if not lib:
        print(f"효과음이 없습니다: {root}")
        print("\n오디오 파일을 넣고 manifest.json 에 이름과 설명을 적어주세요.")
        print("설명은 Claude 가 '언제 이 효과음을 쓸지' 판단하는 근거가 됩니다.")
        return 1
    print(f"효과음 {len(lib)}개 — {root}\n")
    for s in lib:
        print(f"  {s.name:16s} {s.description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        cfg = load_config(getattr(args, "config", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        return _cmd_doctor(cfg)
    if args.command == "sfx":
        return _cmd_sfx(cfg)

    cfg = _apply_overrides(cfg, args)
    video: Path = args.video
    if not video.exists():
        print(f"오류: 영상 파일이 없습니다 — {video}", file=sys.stderr)
        return 2

    opts = RunOptions(
        video=video.resolve(),
        draft_name=args.name or f"{video.stem}_예능",
        work_dir=(args.work_dir or video.parent / f".yeneung_{video.stem}").resolve(),
        draft_dir=args.draft_dir,
        allow_replace=args.replace,
        dry_run=args.dry_run,
        refresh_transcript=args.refresh_transcript,
        refresh_cuesheet=args.refresh_cuesheet,
    )

    try:
        report = run(opts, cfg)
    except Exception as exc:
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1

    print()
    if args.dry_run:
        print(f"큐시트 준비 완료 — 자막 {report.captions} · 효과음 {report.sfx} · "
              f"줌 {report.zooms} · 화면효과 {report.effects}")
        return 0

    print(f"완료: {report.draft_path}")
    print(f"  길이 {report.duration:.1f}초 · 컷 {report.clips}개 · 대사자막 {report.subtitles}개")
    print(f"  예능자막 {report.captions}개 · 효과음 {report.sfx}개 · "
          f"줌 {report.zooms}개 · 화면효과 {report.effects}개")
    for w in report.warnings:
        print(f"  ! {w}")
    print("\n캡컷을 열고 프로젝트 목록에서 확인하세요.")
    print("목록에 안 보이면 아무 프로젝트나 열었다 나오거나 캡컷을 재시작하면 갱신됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
