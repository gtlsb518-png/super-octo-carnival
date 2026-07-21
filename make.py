#!/usr/bin/env python3
"""주제 하나로 대본부터 영상까지 자동 생성하는 CLI.

사용법:
    python make.py "주제 문장"
    python make.py "우주 쓰레기 문제" --orientation horizontal
    python make.py "커피의 역사" --no-api        # Claude 없이 템플릿 대본
    python make.py "환율" --script-only          # 대본만 출력(영상 생략)

환경변수:
    ANTHROPIC_API_KEY   있으면 Claude로 대본 생성
    VIDEO_MAKER_MODEL   사용할 모델(기본 claude-opus-4-8)
    VIDEO_MAKER_OUT     출력 폴더(기본 output)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from video_maker.config import VideoConfig, OUTPUT_DIR
from video_maker.script_gen import generate_script
from video_maker.video import build_video


def _slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "_", text).strip("_")
    return (s[:40] or "video").lower()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="주제 -> 대본 -> 영상 자동 생성")
    p.add_argument("topic", help="영상 주제")
    p.add_argument("--orientation", choices=["vertical", "horizontal"],
                   default="vertical", help="세로(쇼츠) 또는 가로(유튜브)")
    p.add_argument("--no-api", action="store_true",
                   help="Claude API를 쓰지 않고 템플릿 대본 사용")
    p.add_argument("--tts", default=None,
                   choices=["auto", "openai", "elevenlabs", "edge", "gtts", "espeak"],
                   help="TTS 엔진 (기본 auto: 가능한 것부터 시도). 환경변수 VIDEO_MAKER_TTS로도 지정 가능")
    p.add_argument("--script-only", action="store_true",
                   help="대본만 생성하고 영상은 만들지 않음")
    p.add_argument("-o", "--out", default=None, help="출력 mp4 경로")
    args = p.parse_args(argv)

    print(f"■ 주제: {args.topic}")
    print("■ 대본 생성 중...")
    script = generate_script(args.topic, use_api=False if args.no_api else None)

    print(f"\n── 대본: {script.title} ──")
    for i, s in enumerate(script.scenes, 1):
        print(f"  {i}. [{s.headline}] {s.narration}")
    print()

    out_dir = OUTPUT_DIR / _slug(args.topic)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "script.json").write_text(
        json.dumps(script.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"■ 대본 저장: {out_dir / 'script.json'}")

    if args.script_only:
        return 0

    cfg = VideoConfig(orientation=args.orientation)
    out_path = Path(args.out) if args.out else out_dir / f"{_slug(args.topic)}.mp4"
    print("■ 영상 생성 중... (TTS + 이미지 + 합성)")
    result = build_video(script, out_dir / "work", cfg, out_path, tts_engine=args.tts)
    print(f"\n✅ 완료: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
