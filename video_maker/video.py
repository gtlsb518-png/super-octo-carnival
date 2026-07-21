"""장면 이미지 + 나레이션 -> 최종 영상 합성 (ffmpeg).

각 장면을 (이미지 루프 + 오디오 + 잔잔한 줌 + 페이드) mp4 클립으로 만든 뒤
전체를 이어붙여 한 편의 영상을 출력한다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import VideoConfig
from .narration import synthesize
from .script_gen import Script
from .visuals import render_scene_image


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패:\n{' '.join(cmd)}\n{proc.stderr[-800:]}")


def _build_scene_clip(image: Path, audio: Path, duration: float,
                      cfg: VideoConfig, out: Path) -> None:
    """이미지+오디오 한 장면을 mp4 클립으로 만든다."""
    w, h = cfg.size
    frames = max(1, int(duration * cfg.fps))
    fade = cfg.fade
    fade_out_start = max(0.0, duration - fade)
    # 잔잔한 줌인(켄 번스) + 시작/끝 페이드
    vf = (
        f"scale={w*2}:-1,"
        f"zoompan=z='min(zoom+0.0006,1.12)':d={frames}:"
        f"s={w}x{h}:fps={cfg.fps},"
        f"fade=t=in:st=0:d={fade},"
        f"fade=t=out:st={fade_out_start:.2f}:d={fade}"
    )
    _run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(audio),
        "-t", f"{duration:.2f}",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-shortest",
        str(out),
    ])


def build_video(script: Script, workdir: Path, cfg: VideoConfig,
                out_path: Path) -> Path:
    """대본 전체를 영상으로 만들어 out_path에 저장하고 경로를 반환한다."""
    workdir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []

    for i, scene in enumerate(script.scenes):
        print(f"[{i+1}/{len(script.scenes)}] 장면 생성: {scene.headline}")
        img = workdir / f"scene_{i:02d}.png"
        aud = workdir / f"scene_{i:02d}.mp3"
        clip = workdir / f"scene_{i:02d}.mp4"

        render_scene_image(scene, cfg, i, len(script.scenes), img)
        dur = synthesize(scene.narration, aud)
        dur = max(dur + 0.6, 2.0)  # 말끝에 약간 여유
        _build_scene_clip(img, aud, dur, cfg, clip)
        clips.append(clip)

    # concat 목록 파일
    concat_file = workdir / "concat.txt"
    concat_file.write_text(
        "".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8"
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    print("장면 이어붙이는 중...")
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
        "-c:a", "aac", "-b:a", "192k",
        str(out_path),
    ])
    return out_path
