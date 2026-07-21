"""TTS 엔진 모음.

여러 음성 합성 백엔드를 공통 인터페이스로 감싼다. 각 엔진은
`synth(text, out_base) -> Path` 형태로 실제 생성한 오디오 파일 경로를 돌려준다.
(엔진마다 mp3/wav 확장자가 다를 수 있어 out_base는 확장자 없는 경로다.)

지원 엔진:
    openai      : OpenAI TTS   (환경변수 OPENAI_API_KEY)         — 고품질
    elevenlabs  : ElevenLabs   (환경변수 ELEVENLABS_API_KEY)     — 고품질
    edge        : edge-tts     (무료, 키 불필요, 네트워크 필요)  — 고품질 뉴럴
    gtts        : gTTS         (무료, 네트워크 필요)             — 보통
    espeak      : espeak-ng    (오프라인, 네트워크 불필요)       — 로봇 톤이지만 확실
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class TTSError(Exception):
    """엔진이 음성을 만들지 못했을 때."""


def _ca_bundle() -> str | None:
    return os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")


# ---------------------------------------------------------------- OpenAI TTS
def openai_tts(text: str, out_base: Path) -> Path:
    """OpenAI TTS. OPENAI_API_KEY 필요. mp3 반환."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise TTSError("OPENAI_API_KEY 없음")
    import requests  # honors HTTPS_PROXY / REQUESTS_CA_BUNDLE

    out = out_base.with_suffix(".mp3")
    resp = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            "voice": os.environ.get("OPENAI_TTS_VOICE", "alloy"),
            "input": text,
            "response_format": "mp3",
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise TTSError(f"OpenAI TTS {resp.status_code}: {resp.text[:200]}")
    out.write_bytes(resp.content)
    return out


# ------------------------------------------------------------- ElevenLabs TTS
def elevenlabs_tts(text: str, out_base: Path) -> Path:
    """ElevenLabs TTS. ELEVENLABS_API_KEY 필요. mp3 반환."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise TTSError("ELEVENLABS_API_KEY 없음")
    import requests

    # 기본 다국어 음성(Rachel). 한국어는 multilingual 모델 사용.
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    out = out_base.with_suffix(".mp3")
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": key, "accept": "audio/mpeg",
                 "content-type": "application/json"},
        json={
            "text": text,
            "model_id": os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise TTSError(f"ElevenLabs {resp.status_code}: {resp.text[:200]}")
    out.write_bytes(resp.content)
    return out


# ------------------------------------------------------------------ edge-tts
def edge_tts_synth(text: str, out_base: Path) -> Path:
    """Microsoft edge-tts. 무료·키 불필요, 네트워크 필요. mp3 반환.

    커스텀 CA(사내 프록시 등) 환경에서도 되도록 SSL_CERT_FILE을 신뢰한다.
    """
    import asyncio
    import ssl

    import aiohttp
    import edge_tts

    voice = os.environ.get("EDGE_TTS_VOICE", "ko-KR-SunHiNeural")
    out = out_base.with_suffix(".mp3")

    async def _run() -> None:
        ca = _ca_bundle()
        ctx = ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
        conn = aiohttp.TCPConnector(ssl=ctx)
        try:
            comm = edge_tts.Communicate(text, voice, connector=conn,
                                        proxy=os.environ.get("HTTPS_PROXY"))
            await comm.save(str(out))
        finally:
            await conn.close()

    asyncio.run(_run())
    if not out.exists() or out.stat().st_size == 0:
        raise TTSError("edge-tts 결과 비어있음")
    return out


# ---------------------------------------------------------------------- gTTS
def gtts_synth(text: str, out_base: Path) -> Path:
    """Google TTS. 무료, 네트워크 필요. mp3 반환."""
    from gtts import gTTS

    out = out_base.with_suffix(".mp3")
    gTTS(text=text, lang=os.environ.get("GTTS_LANG", "ko")).save(str(out))
    if not out.exists() or out.stat().st_size == 0:
        raise TTSError("gTTS 결과 비어있음")
    return out


# ----------------------------------------------------------------- espeak-ng
def espeak_synth(text: str, out_base: Path) -> Path:
    """espeak-ng 오프라인 합성. 네트워크 불필요. wav 반환.

    로봇 같은 음색이지만 어떤 환경에서도 소리가 난다(최후의 실음성 보루).
    ESPEAK_NG_BIN / ESPEAK_NG_PATH 환경변수로 바이너리·데이터 경로 지정 가능.
    """
    binary = os.environ.get("ESPEAK_NG_BIN") or shutil.which("espeak-ng")
    if not binary:
        raise TTSError("espeak-ng 실행 파일을 찾을 수 없음")
    out = out_base.with_suffix(".wav")
    cmd = [binary, "-v", os.environ.get("ESPEAK_NG_VOICE", "ko"),
           "-s", os.environ.get("ESPEAK_NG_SPEED", "150"), text, "-w", str(out)]
    data_path = os.environ.get("ESPEAK_NG_PATH")
    if data_path:
        cmd += ["--path", data_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        raise TTSError(f"espeak-ng 실패: {proc.stderr[:200]}")
    return out


# 이름 -> 함수
ENGINES = {
    "openai": openai_tts,
    "elevenlabs": elevenlabs_tts,
    "edge": edge_tts_synth,
    "gtts": gtts_synth,
    "espeak": espeak_synth,
}

# auto 모드에서 시도하는 순서 (품질 높은 것 → 확실한 것)
AUTO_ORDER = ["openai", "elevenlabs", "edge", "gtts", "espeak"]
