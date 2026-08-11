"""파이프라인 흐름 테스트.

오래 걸리는 일(받아쓰기)을 필요할 때만 하는지, 없어도 되는 것 때문에
전체가 죽지는 않는지를 본다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("pycapcut")
pytest.importorskip("av")

from yeneung import cuesheet as cuesheet_mod  # noqa: E402
from yeneung import pipeline, sfxgen, transcribe  # noqa: E402
from yeneung.config import Config  # noqa: E402


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    ff = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    out = tmp_path_factory.mktemp("vid") / "v.mp4"
    subprocess.run(
        [ff, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=8",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True,
    )
    return out


@pytest.fixture()
def env(video: Path, tmp_path: Path):
    """실행에 필요한 폴더와 설정을 갖춘다."""
    cfg = Config()
    cfg.sfx.library = str(tmp_path / "sfx")
    sfxgen.generate_pack(cfg.sfx.library)

    drafts = tmp_path / "drafts"
    drafts.mkdir()
    opts = pipeline.RunOptions(
        video=video, draft_name="테스트", work_dir=tmp_path / "work",
        draft_dir=drafts, allow_replace=True,
    )
    return opts, cfg


def _cuesheet(tmp_path: Path) -> Path:
    p = tmp_path / "cue.json"
    p.write_text(
        '{"captions": [{"start": 0.5, "end": 2.0, "text": "직접 쓴 자막"}]}',
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------- API 없이


def test_no_api_key_falls_back_to_rules(env, monkeypatch):
    """키가 없어도 그냥 돌아야 한다. 규칙 기반으로 알아서 넘어간다."""
    opts, cfg = env
    monkeypatch.setattr(
        transcribe, "transcribe",
        lambda *a, **k: [transcribe.Utterance(0.5, 2.0, "와 진짜 대박이다")],
    )

    logs: list[str] = []
    report = pipeline.run(opts, cfg, log=logs.append)
    assert report.captions >= 1
    assert any("규칙 기반" in line for line in logs)


def test_rules_mode_never_touches_claude(env, monkeypatch):
    """키가 있어도 mode=rules 면 API 를 부르면 안 된다."""
    opts, cfg = env
    cfg.cuesheet.mode = "rules"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(
        transcribe, "transcribe",
        lambda *a, **k: [transcribe.Utterance(0.5, 2.0, "와 진짜 대박이다")],
    )

    def boom(*_a, **_k):
        raise AssertionError("규칙 모드인데 Claude 를 불렀다")

    monkeypatch.setattr(cuesheet_mod, "generate", boom)
    assert pipeline.run(opts, cfg, log=lambda _m: None).captions >= 1


def test_claude_mode_without_key_fails_before_transcribing(env, monkeypatch):
    """받아쓰기에 몇 분 쓰고 나서 키가 없다고 죽으면 안 된다."""
    opts, cfg = env
    cfg.cuesheet.mode = "claude"
    called: list[int] = []
    monkeypatch.setattr(
        transcribe, "transcribe", lambda *a, **k: called.append(1) or []
    )

    with pytest.raises(RuntimeError, match="API 키"):
        pipeline.run(opts, cfg, log=lambda _m: None)
    assert called == [], "키 확인 전에 받아쓰기가 돌았다"


def test_error_message_tells_how_to_fix(env):
    opts, cfg = env
    cfg.cuesheet.mode = "claude"
    with pytest.raises(RuntimeError) as info:
        pipeline.run(opts, cfg, log=lambda _m: None)
    message = str(info.value)
    assert "setx ANTHROPIC_API_KEY" in message
    assert "--no-api" in message


def test_own_cuesheet_needs_no_api_key(env, tmp_path, monkeypatch):
    opts, cfg = env
    opts.cuesheet = _cuesheet(tmp_path)
    cfg.subtitle.enabled = False
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: [])

    report = pipeline.run(opts, cfg, log=lambda _m: None)
    assert report.captions == 1


def test_cached_cuesheet_needs_no_api_key(env, monkeypatch):
    opts, cfg = env
    opts.work_dir.mkdir(parents=True, exist_ok=True)
    (opts.work_dir / "cuesheet.json").write_text(
        '{"captions": [{"start": 0.5, "end": 2.0, "text": "캐시"}]}', encoding="utf-8"
    )
    cfg.subtitle.enabled = False
    monkeypatch.setattr(transcribe, "transcribe", lambda *a, **k: [])

    report = pipeline.run(opts, cfg, log=lambda _m: None)
    assert report.captions == 1


# ---------------------------------------------------------------- 받아쓰기


def test_skips_transcription_when_not_needed(env, tmp_path, monkeypatch):
    """큐시트를 직접 줬고 대사 자막도 끄면 받아쓸 이유가 없다."""
    opts, cfg = env
    opts.cuesheet = _cuesheet(tmp_path)
    cfg.subtitle.enabled = False

    called: list[int] = []
    monkeypatch.setattr(
        transcribe, "transcribe", lambda *a, **k: called.append(1) or []
    )

    logs: list[str] = []
    pipeline.run(opts, cfg, log=logs.append)
    assert called == [], "필요 없는데 받아쓰기가 돌았다"
    assert any("건너뜁니다" in line for line in logs)


def test_transcribes_when_subtitles_wanted(env, tmp_path, monkeypatch):
    opts, cfg = env
    opts.cuesheet = _cuesheet(tmp_path)
    cfg.subtitle.enabled = True

    called: list[int] = []

    def fake(*_a, **_k):
        called.append(1)
        return [transcribe.Utterance(0.5, 2.0, "안녕하세요")]

    monkeypatch.setattr(transcribe, "transcribe", fake)
    report = pipeline.run(opts, cfg, log=lambda _m: None)
    assert called == [1]
    assert report.subtitles == 1


def test_transcription_failure_is_survivable_with_own_cuesheet(env, tmp_path, monkeypatch):
    """대사 자막은 있으면 좋은 것일 뿐, 없다고 전체가 죽으면 안 된다."""
    opts, cfg = env
    opts.cuesheet = _cuesheet(tmp_path)
    cfg.subtitle.enabled = True

    def boom(*_a, **_k):
        raise RuntimeError("faster-whisper 없음")

    monkeypatch.setattr(transcribe, "transcribe", boom)

    logs: list[str] = []
    report = pipeline.run(opts, cfg, log=logs.append)
    assert report.captions == 1        # 예능 자막은 그대로 들어간다
    assert report.subtitles == 0
    assert any("받아쓰기 실패" in line for line in logs)


def test_transcription_failure_is_fatal_when_cuesheet_needs_it(env, monkeypatch):
    """큐시트를 만들어야 하는데 대사가 없으면 더 갈 수 없다."""
    opts, cfg = env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def boom(*_a, **_k):
        raise RuntimeError("faster-whisper 없음")

    monkeypatch.setattr(transcribe, "transcribe", boom)
    with pytest.raises(RuntimeError, match="faster-whisper"):
        pipeline.run(opts, cfg, log=lambda _m: None)


def test_transcript_cache_is_reused(env, tmp_path, monkeypatch):
    opts, cfg = env
    opts.cuesheet = _cuesheet(tmp_path)
    opts.work_dir.mkdir(parents=True, exist_ok=True)
    transcribe.save(
        [transcribe.Utterance(0.5, 2.0, "캐시된 대사")], opts.work_dir / "transcript.json"
    )

    called: list[int] = []
    monkeypatch.setattr(
        transcribe, "transcribe", lambda *a, **k: called.append(1) or []
    )
    report = pipeline.run(opts, cfg, log=lambda _m: None)
    assert called == [], "캐시가 있는데 다시 받아썼다"
    assert report.subtitles == 1
