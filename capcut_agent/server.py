"""
CapCut Agent - FastAPI Server (CapCut 8.7.0 호환)
무음 구간 감지 + Whisper 한국어 자막 → CapCut draft 자동 생성
"""

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CapCut Agent")

BASE_DIR   = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
CUTOUT_DIR = BASE_DIR / "outputs" / "cutouts"   # 배경 지운 투명 PNG 보관

for d in [UPLOAD_DIR, OUTPUT_DIR, CUTOUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

_draft_cache: dict[str, Path] = {}
_asr_lock = asyncio.Lock()

# Whisper 모델 lazy init
_whisper_model = None


def ensure_faster_whisper() -> tuple[bool, str]:
    """
    자막(음성인식) 패키지가 없으면 지금 실행 중인 파이썬에 자동 설치한다.
    포터블 runtime에서도 같은 python.exe에 설치되므로 별도 bat 없이 동작.
    반환: (사용 가능 여부, 안내 메시지)
    """
    import importlib
    try:
        importlib.import_module("faster_whisper")
        return True, "자막 기능 준비됨"
    except ImportError:
        pass

    cmd = [sys.executable, "-m", "pip", "install", "--no-warn-script-location", "faster-whisper"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"설치 실행 실패: {e}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, " / ".join(tail[-3:]) if tail else "pip 설치 실패"

    importlib.invalidate_caches()
    try:
        importlib.import_module("faster_whisper")
        return True, "자막 기능 설치 완료"
    except ImportError as e:
        return False, f"설치 후에도 불러오지 못함: {e}"


def ensure_rembg() -> tuple[bool, str]:
    """
    배경제거(이미지 누끼) 패키지가 없으면 자동 설치한다.
    캡컷의 '배경제거'는 캡컷이 직접 계산한 마스크 파일이 있어야 화면이 나오는데,
    프로그램이 만든 draft에는 그 파일이 없어서 켜두면 검은 화면이 된다.
    그래서 배경제거는 우리가 직접 해서 투명 PNG로 만들어 넣는다.
    반환: (사용 가능 여부, 안내 메시지)
    """
    import importlib
    try:
        importlib.import_module("rembg")
        importlib.import_module("PIL")
        return True, "배경제거 기능 준비됨"
    except ImportError:
        pass

    cmd = [sys.executable, "-m", "pip", "install", "--no-warn-script-location",
           "pillow", "onnxruntime", "rembg"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"설치 실행 실패: {e}"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        return False, " / ".join(tail[-3:]) if tail else "pip 설치 실패"

    importlib.invalidate_caches()
    try:
        importlib.import_module("rembg")
        importlib.import_module("PIL")
        return True, "배경제거 기능 설치 완료"
    except ImportError as e:
        return False, f"설치 후에도 불러오지 못함: {e}"


_rembg_session = None


def get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session
        _rembg_session = new_session("u2net")   # 첫 실행 때 모델 176MB 내려받음
    return _rembg_session


def make_cutout_png(src: Path, out_dir: Path, stroke: bool = True,
                    stroke_frac: float = 0.15) -> Path | None:
    """
    이미지 배경을 지워 투명 PNG로 저장한다. stroke가 켜져 있으면 흰색 발광 테두리도
    직접 그려 넣는다. 캡컷에서 따로 버튼을 누르지 않아도 바로 보이게 하기 위함.
    같은 설정으로 이미 만든 파일이 있으면 그대로 재사용한다.
    반환: 만들어진 PNG 경로 (실패 시 None)
    """
    from PIL import Image, ImageFilter
    from rembg import remove

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        st = src.stat()
        key = f"{src}|{st.st_mtime_ns}|{st.st_size}|{int(stroke)}|{stroke_frac:.3f}"
    except OSError:
        return None
    out = out_dir / f"{src.stem}_cut_{hashlib.md5(key.encode()).hexdigest()[:10]}.png"
    if out.exists():
        return out

    try:
        img = Image.open(src).convert("RGBA")
        cut = remove(img, session=get_rembg_session())
        if cut.mode != "RGBA":
            cut = cut.convert("RGBA")

        if stroke and stroke_frac > 0:
            w, h = cut.size
            glow_px = max(2, round(stroke_frac * min(w, h) * 0.1))
            alpha = cut.getchannel("A")
            grown = alpha
            for _ in range(max(1, glow_px // 2)):        # 2px씩 부풀리기
                grown = grown.filter(ImageFilter.MaxFilter(5))
            grown = grown.filter(ImageFilter.GaussianBlur(max(1, glow_px / 4)))
            glow = Image.new("RGBA", cut.size, (255, 255, 255, 255))
            glow.putalpha(grown)
            cut = Image.alpha_composite(glow, cut)

        cut.save(out, "PNG")
        return out
    except Exception:
        return None


def build_cutouts(files: list[Path], out_dir: Path, stroke: bool, stroke_frac: float,
                  progress: dict | None = None) -> dict[str, str]:
    """배경제거 대상 이미지들을 투명 PNG로 미리 만들어 {원본경로: PNG경로} 로 돌려준다."""
    made: dict[str, str] = {}
    for i, f in enumerate(files):
        png = make_cutout_png(f, out_dir, stroke, stroke_frac)
        if png:
            made[str(f)] = str(png)
        if progress is not None:
            progress["done"] = i + 1
            progress["total"] = len(files)
    return made


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        try:
            _whisper_model = WhisperModel("large-v3", device="cuda", compute_type="float16")
            print("[Whisper] GPU 로드 완료")
        except Exception:
            print("[Whisper] GPU 실패 → CPU int8 fallback")
            _whisper_model = WhisperModel("large-v3", device="cpu", compute_type="int8")
    return _whisper_model


# ══════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]

def sec_to_us(sec: float) -> int:
    return int(sec * 1_000_000)

def now_us() -> int:
    return int(time.time() * 1_000_000)

FPS = 30.0
FRAME_US = int(round(1_000_000 / FPS))   # 한 프레임 길이(us)

# 자막 한 조각의 목표 글자 수 (기본값, UI에서 조절 가능).
# 어절 경계로 이 길이 안팎에서 끊는다. 사용자 완성본(알테오젠 원고 27줄) 기준.
MAX_SUBTITLE_CHARS = 12

# 말 사이가 이만큼 벌어지면 자막을 끊는다 (자연스러운 호흡 단위 분할)
SUB_PAUSE_BREAK_US = 350_000

def snap_to_frame(sec: float) -> float:
    """초 단위 시간을 가장 가까운 프레임 경계로 스냅 (30fps 기준)"""
    frame = round(sec * FPS)
    return frame / FPS


def snap_us_to_frame(us: int) -> int:
    """
    마이크로초 시각을 가장 가까운 프레임 경계로 스냅 (30fps).
    영상 클립 경계는 이미 프레임 단위인데 자막 조각 경계는 단어 발화 시각
    그대로라 프레임 중간에 떨어진다. 캡컷에서 자막이 한 프레임씩 어긋나
    보이는 것을 막기 위해 자막도 같은 격자에 올린다.
    """
    return int(round(round(us * FPS / 1_000_000) * 1_000_000 / FPS))


# ══════════════════════════════════════════════════════════
# ffmpeg 무음 감지
# ══════════════════════════════════════════════════════════

def detect_silence(video_path: Path, noise_db: float = -40.0, min_silence_sec: float = 0.5,
                   total_duration: float | None = None) -> list[dict]:
    cmd = ["ffmpeg", "-i", str(video_path),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
           "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", output)]
    ends   = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)", output)]
    # 영상이 무음으로 끝나면 ffmpeg이 마지막 silence_end를 출력하지 않음 → 영상 끝까지 무음 처리
    if len(starts) == len(ends) + 1 and total_duration is not None:
        ends.append(total_duration)
    # 프레임 경계로 스냅 (30fps)
    result_list = []
    for s, e in zip(starts, ends):
        s_snap = snap_to_frame(max(s, 0.0))
        e_snap = snap_to_frame(e)
        if e_snap > s_snap:
            result_list.append({"start": s_snap, "end": e_snap, "duration": round(e_snap - s_snap, 4)})
    return result_list

def extract_audio_wav(video_path: Path) -> Path | None:
    """무음 분석용으로 오디오만 작은 wav로 뽑는다 (여러 번 검사해도 빠르게)."""
    import tempfile
    out = Path(tempfile.gettempdir()) / f"_capcut_lvl_{uuid.uuid4().hex}.wav"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video_path),
           "-vn", "-ac", "1", "-ar", "16000", str(out)]
    try:
        subprocess.run(cmd, capture_output=True)
    except Exception:
        return None
    return out if out.exists() and out.stat().st_size > 1000 else None


AUTO_DB_CANDIDATES = [-45.0, -40.0, -35.0, -32.0, -30.0, -27.0, -25.0, -22.0]


def auto_noise_db(audio_path: Path, min_silence_sec: float, total_duration: float,
                  head_trim: float = 0.0, tail_trim: float = 0.0,
                  min_clip_sec: float = 0.0) -> tuple[float, list[str]]:
    """
    무음 기준(dB)을 영상마다 자동으로 고른다.

    녹음 환경마다 방 소음 크기가 달라서 -40dB 가 어떤 영상에서는 딱 맞고 어떤
    영상에서는 숨소리·에어컨 소리까지 '말'로 쳐서 무음이 하나도 안 잘린다.
    그래서 여러 기준으로 실제로 재보고, 말을 자르기 시작하기 직전의 가장 센 값을 쓴다.

    판단 기준:
      - 잘라낸 비율이 늘어나는 쪽이 좋다 (무음이 실제로 없어짐)
      - 단, 0.4초 미만 조각이 25%를 넘으면 말을 토막내기 시작한 것 → 탈락
      - 전체의 75% 넘게 잘라내는 것도 과함 → 탈락
    반환: (고른 dB, 사람이 읽을 수 있는 검사 결과 줄들)
    """
    best, report = None, []
    for db in AUTO_DB_CANDIDATES:
        sil = detect_silence(audio_path, db, min_silence_sec, total_duration)
        keeps = compute_keep_ranges(sil, total_duration, head_trim, tail_trim, min_clip_sec)
        if not keeps:
            report.append(f"  {db:>5.0f}dB → 남는 클립 없음")
            continue
        kept = sum(e - s for _a, _b, s, e in keeps) / 1e6
        removed = 1 - kept / total_duration if total_duration else 0
        tiny = sum(1 for _a, _b, s, e in keeps if e - s < 400_000) / len(keeps)
        ok = tiny <= 0.25 and removed <= 0.75
        report.append(f"  {db:>5.0f}dB → 무음 제거 {removed*100:4.1f}% / "
                      f"클립 {len(keeps):3d}개 / 짧은 조각 {tiny*100:4.1f}% {'✓' if ok else '✗'}")
        if ok and (best is None or removed > best[1]):
            best = (db, removed)
    if best is None:
        return -40.0, report + ["  → 판단 실패, 기본값 -40dB 사용"]
    report.append(f"  → 선택: {best[0]:.0f}dB (무음 {best[1]*100:.1f}% 제거)")
    return best[0], report


def get_video_duration(video_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def compute_keep_ranges(silences: list[dict], total_duration: float,
                        head_trim: float = 0.0, tail_trim: float = 0.0,
                        min_clip_sec: float = 0.0,
                        drop_clips: set[int] | None = None) -> list[tuple[float, float, int, int]]:
    """
    무음을 제외하고 남기는(keep) 구간 목록.
    반환: [(원본_start_sec, 원본_end_sec, 타임라인_start_us, 타임라인_end_us), ...]
    영상 세그먼트와 자막(draft 텍스트/SRT)이 모두 이 함수를 공유하므로
    타이밍이 마이크로초 단위로 완전히 일치한다.

    head_trim / tail_trim: 클립 앞뒤를 이만큼(초) 더 깎는다 (손편집처럼 타이트하게).
                           음수면 반대로 그만큼 더 남긴다 (말이 씹히지 않게 여유).
                           더 남길 때는 잘라낸 무음 안에서만 늘리고, 옆 클립은 침범하지 않는다.
    min_clip_sec:          이보다 짧아진 클립은 통째로 버린다 (0이면 끄기).
    drop_clips:            빼버릴 클립 번호 (같은 말 반복 엔지컷 삭제용).
                           번호는 '빼기 전' 목록 기준이고, 빼고 나면 타임라인은
                           빈틈 없이 다시 이어붙인다.
    """
    # 0.2초(6프레임) 미만 클립은 편집에도 못 쓰고 자막도 "..." 만 붙어서 지저분하다
    floor_sec = max(min_clip_sec, 0.2)

    # 1) 무음을 뺀 원래 keep 구간
    raw: list[tuple[float, float]] = []
    cursor = 0.0
    for sil in sorted(silences, key=lambda x: x["start"]):
        s, e = snap_to_frame(sil["start"]), snap_to_frame(sil["end"])
        if s > cursor:
            raw.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < total_duration:
        raw.append((cursor, total_duration))

    # 2) 앞뒤 여유 조정 (+면 더 깎기, -면 더 남기기)
    picked: list[tuple[float, float, int]] = []   # (원본 start, 원본 end, 길이 us)
    for i, (ks, ke) in enumerate(raw):
        if head_trim or tail_trim:
            nks, nke = ks + head_trim, ke - tail_trim
            # 더 남길 때: 영상 밖 / 옆 클립 침범 금지
            lo = raw[i - 1][1] if i > 0 else 0.0
            hi = raw[i + 1][0] if i + 1 < len(raw) else total_duration
            nks = max(nks, lo)
            nke = min(nke, hi)
            if nke - nks >= floor_sec:      # 너무 깎여 사라지면 원래대로
                ks, ke = nks, nke
        src_start = sec_to_us(snap_to_frame(ks))
        src_end   = sec_to_us(snap_to_frame(ke))
        dur = src_end - src_start
        if dur < sec_to_us(floor_sec):
            continue
        picked.append((ks, ke, dur))

    drop = drop_clips or set()
    ranges: list[tuple[float, float, int, int]] = []
    tl_us = 0
    for i, (ks, ke, dur) in enumerate(picked):
        if i in drop:
            continue
        ranges.append((ks, ke, tl_us, tl_us + dur))
        tl_us += dur
    return ranges


def _same_line(a: str, b: str, sim: float = 0.9) -> bool:
    """두 클립이 '같은 말'인지 (엔지컷 판정). 띄어쓰기·문장부호는 무시."""
    a = re.sub(r"[^0-9A-Za-z가-힣]", "", a)
    b = re.sub(r"[^0-9A-Za-z가-힣]", "", b)
    if len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    # "삼성이" → "삼성이 주가 하락의 원인은" 처럼 하다 만 테이크
    if long.startswith(short):
        return True
    import difflib
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    if ratio >= sim:
        return True
    # 앞부분이 길게 같고 끝만 조금 다르면 같은 말로 본다
    #   "금리 인하가 시장에" / "금리 인하가 시장엔"  → 같은 말 (인식 차이)
    #   "첫 번째 이유는"    / "두 번째 이유는"      → 다른 말 (앞이 다름)
    pre = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        pre += 1
    return pre >= len(short) * 0.6 and ratio >= 0.8


def find_repeat_ng_clips(keep_ranges: list[tuple[float, float, int, int]],
                         clip_texts: list[str],
                         short_sec: float = 1.5) -> list[int]:
    """
    같은 말을 연달아 여러 번 찍은 구간(엔지컷)에서 '짧은' 테이크를 골라낸다.

    규칙 (사용자 지정):
      - 같은 말이 2개 이상 연달아 나오고 그 클립이 짧으면 → 지운다
      - 같은 말이라도 클립이 길면 → 그냥 둔다 (고를 수 있게)
      - 한 묶음이 전부 짧으면 마지막 하나는 남긴다 (내용이 통째로 사라지지 않게)
    반환: 지울 클립 번호 목록
    """
    if not clip_texts or len(clip_texts) != len(keep_ranges):
        return []
    short_us = sec_to_us(short_sec)
    drop: list[int] = []
    i = 0
    n = len(clip_texts)
    while i < n:
        # 같은 말이 이어지는 묶음 찾기 (묶음 안에서 가장 긴 문장과 비교)
        group = [i]
        rep = clip_texts[i]
        j = i + 1
        while j < n and _same_line(rep, clip_texts[j]):
            group.append(j)
            if len(clip_texts[j]) > len(rep):
                rep = clip_texts[j]
            j += 1
        if len(group) >= 2:
            shorts = [k for k in group if (keep_ranges[k][3] - keep_ranges[k][2]) < short_us]
            if len(shorts) == len(group):
                shorts = shorts[:-1]      # 전부 짧으면 마지막 테이크는 남긴다
            drop.extend(shorts)
        i = j
    return drop


# ══════════════════════════════════════════════════════════
# Whisper 자막 인식
# ══════════════════════════════════════════════════════════

NO_SPEECH_PLACEHOLDER = "..."   # 말소리가 없는 클립에도 자막 클립은 만든다

# Whisper가 잡음·숨소리 구간에 넣는 효과음 태그: [grunting] (laughs) 등
_BRACKET_TAG_RE = re.compile(r"[\[\(\<\{][^\]\)\>\}]*[\]\)\>\}]")

# 괄호가 벗겨진 채 남는 효과음 단어들 (grunting젠스랑 처럼 한글에 붙기도 함)
_SOUND_TAG_WORDS = {
    "grunting", "grunts", "grunt", "groaning", "groans", "sighs", "sighing", "sigh",
    "laughs", "laughing", "laughter", "chuckles", "coughing", "coughs", "cough",
    "music", "applause", "clapping", "breathing", "inhales", "exhales", "breath",
    "silence", "inaudible", "noise", "static", "beep", "beeping", "whispering",
    "gasps", "mumbling", "humming", "footsteps", "typing", "clicking", "singing",
    "speaking", "foreign", "blank", "blankaudio", "subtitles", "sniffs", "clears",
}

# 소문자 영어라도 실제로 쓰이는 표기는 남긴다
_LATIN_KEEP = {
    "etf", "ai", "tv", "pc", "cpu", "gpu", "ceo", "gdp", "hbm", "ssd", "ram",
    "gs", "sk", "lg", "kb", "usa",
    "iphone", "youtube", "google", "apple", "tesla", "nvidia", "chatgpt", "openai",
    "lng", "lpg", "kospi", "kosdaq", "sds", "sdi", "skt", "kt", "cj", "gm", "ev",
    "tsmc", "asml", "amd", "intel", "micron", "meta", "amazon", "microsoft", "sci",
}


def _clean_latin_run(seg: str, script_tokens: set[str]) -> str:
    """
    영문 알파벳 한 덩어리(seg)가 '진짜'인지 판단해서, 진짜면 그대로 남기고
    환각이면 지운다(빈 문자열). 화이트리스트 방식이라 disadvant, enthus,
    owad, wszyst, Vanc, ambiguity처럼 예측 못 할 환각 단어도 전부 걸러진다.
    남기는 경우만 나열:
      - 알려진 효과음/잡음 태그는 대소문자 무관하게 무조건 제거
      - 대문자 약어(ETF, GS, AI, SK...) — 한국어 방송에서 실제로 쓰는 표기
      - 자주 쓰는 소문자 표기(iphone, youtube 등) 목록에 있는 것
      - 대본에 등장하는 단어 (사용자가 넣은 대본은 신뢰할 근거가 있음)
    그 외 모든 영문(소문자/혼합대소문자 단어, disadvant·Vanc류)은 제거.
    """
    low = seg.lower()
    if low in _SOUND_TAG_WORDS:
        return ""
    if len(seg) == 1:
        return ""            # "I", "E", "U" — 잡음 구간에서 나오는 한 글자는 전부 환각
    if low in _LATIN_KEEP or low in script_tokens:
        return seg
    if seg.isupper() and 2 <= len(seg) <= 6:
        return seg           # ETF, GS, AI 같은 대문자 약어 (한 글자는 제외)
    return ""                # 나머지는 전부 환각으로 간주해 제거


def clean_recognized_text(text: str, script_tokens: set[str] | None = None) -> str:
    """
    Whisper 환각/효과음 태그를 걸러낸다. 한국어 영상이라는 전제.
    - [grunting], (laughs) 같은 괄호 태그는 통째로 제거
    - 나머지 텍스트 안의 영문 알파벳 덩어리는 전부 _clean_latin_run으로 검사
      → 한글에 그대로 들러붙은 경우("disadvant짠", "wszyst그럼")도
        영문 부분만 정확히 떼어내고 한글은 보존한다
    - 영문이 지워지고 남는 게 문장부호뿐인 조각은 통째로 버린다
    """
    script_tokens = script_tokens or set()
    text = _BRACKET_TAG_RE.sub(" ", text)
    text = re.sub(r"[A-Za-z]+", lambda m: _clean_latin_run(m.group(0), script_tokens), text)
    out = [tok for tok in text.split() if re.search(r"[가-힣0-9A-Za-z]", tok)]
    return " ".join(out).strip()


def transcribe_clip_words(video_path: Path, start: float, end: float,
                          initial_prompt: str = "") -> list[dict]:
    """
    영상의 [start, end] 구간만 잘라내 단독 인식하고, 원본 영상 기준
    '절대 시각'을 가진 단어 목록을 반환한다.
    ★ 드리프트 방지 핵심: 클립을 통째로 텍스트만 뽑던 이전 방식은 클립
    구간 전체에 단어를 균등하게 흩뿌려서, 클립 안에서 말이 한쪽으로
    쏠려 있으면(엔지컷의 머뭇거림·헛기침 등) 자막이 실제 발화 위치와
    어긋났다. word_timestamps로 Whisper가 계산한 실제 단어별 시각을
    그대로 써서 이 문제를 없앤다.
    반환: [{"start": 원본초, "end": 원본초, "word": str}, ...]
    """
    import tempfile
    model = get_whisper_model()
    tmp = Path(tempfile.gettempdir()) / f"_capcut_clip_{uuid.uuid4().hex}.wav"
    offset = max(start - 0.05, 0)  # ffmpeg로 잘라낸 구간의 시작 = 원본 영상에서의 오프셋
    try:
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-ss", f"{offset:.3f}", "-to", f"{end + 0.05:.3f}",
               "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", str(tmp)]
        subprocess.run(cmd, capture_output=True)
        if not tmp.exists() or tmp.stat().st_size < 1500:
            return []
        kwargs = {"initial_prompt": initial_prompt[:700]} if initial_prompt.strip() else {}
        segs, _ = model.transcribe(
            str(tmp), language="ko", beam_size=1,
            vad_filter=False,                  # 이미 잘라낸 구간이라 VAD 불필요
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            word_timestamps=True,              # 단어별 실제 발화 시각 확보 (드리프트 방지)
            **kwargs,
        )
        words = []
        for s in segs:
            # 짧은/잡음 구간에서 흔한 환각 문구를 거른다
            if getattr(s, "no_speech_prob", 0.0) > 0.8:
                continue
            if getattr(s, "avg_logprob", 0.0) < -1.5:
                continue
            for w in (s.words or []):
                wt = (w.word or "").strip()
                if wt:
                    words.append({"start": offset + w.start, "end": offset + w.end, "word": wt})
        return words
    except Exception:
        return []
    finally:
        try:
            tmp.unlink()
        except Exception:
            pass


MAX_CHARS_PER_SEC = 14.0    # 한국어 빠른 말이 초당 6~7자. 그 두 배를 넘으면 환각으로 본다

# Whisper가 조용한 구간에 습관적으로 넣는 유튜브 상투 문구 (실제로 말한 적 없음)
HALLUCINATION_LINES = [
    "시청해주셔서감사합니다", "시청해주셔서", "시청해주셔서고맙습니다",
    "끝까지시청해주셔서감사합니다", "지금까지시청해주셔서감사합니다",
    "봐주셔서감사합니다", "영상시청해주셔서감사합니다", "오늘도시청해주셔서감사합니다",
    "구독과좋아요", "구독좋아요부탁드립니다", "구독과좋아요부탁드립니다",
    "다음영상에서만나요", "다음시간에만나요", "다음영상에서뵙겠습니다",
    "한글자막by", "자막제공", "이덕이", "MBC뉴스",
]
_SHORT_ONLY_LINES = {"감사합니다", "고맙습니다", "안녕하세요", "네", "아멘"}
# 클립 전체가 아니라 일부만 겹쳐도 환각으로 보는 마무리 인사
_HALLUC_CONTAINS = ("다음영상에서", "다음시간에", "시청해주셔서", "구독과좋아요",
                    "구독좋아요", "봐주셔서감사", "영상에서만나")


def _is_number_babble(text: str) -> bool:
    """
    "1, 2, 2 2, 3" 처럼 한 자리 숫자만 늘어놓은 인식인지 (잡음 구간 환각).
    "+12%" "2만 2천" "120일선" 같은 실제 표기는 건드리지 않는다.
    """
    t = text.strip()
    if not t or re.search(r"[가-힣A-Za-z%+]", t):
        return False
    nums = re.findall(r"\d+", t)
    return bool(nums) and all(len(n) == 1 for n in nums)


def is_hallucinated_line(text: str, clip_sec: float) -> bool:
    """클립 전체가 Whisper 상투 문구면 실제 발화가 아니라고 본다."""
    if _is_number_babble(text):
        return True
    n = re.sub(r"[^0-9A-Za-z가-힣]", "", text)
    if len(n) < 2:
        return False
    if any(p in n for p in _HALLUC_CONTAINS):
        return True
    for p in HALLUCINATION_LINES:
        if n == p:
            return True
        if len(n) >= 5 and (n.startswith(p) or p.startswith(n)):
            return True
    return clip_sec < 1.0 and n in _SHORT_ONLY_LINES


_INNER_COMMA_RE = re.compile(r"(?<=[가-힣0-9A-Za-z]),(?=[가-힣A-Za-z])")


def split_inner_commas(words: list[dict]) -> list[dict]:
    """
    "주주들,그동안" 처럼 쉼표 뒤가 붙어 한 어절이 된 것을 둘로 나눈다.
    (붙어 있으면 그 자리에서 자막을 끊을 수가 없다)
    """
    out: list[dict] = []
    for w in words:
        parts = _INNER_COMMA_RE.sub(",\u0000", w["word"]).split("\u0000")
        if len(parts) == 1:
            out.append(w)
            continue
        keys = ("tl_start", "tl_end") if "tl_start" in w else ("start", "end")
        st = w.get(keys[0], 0)
        en = w.get(keys[1], st)
        span = (en - st) / len(parts) if en > st else 0
        for i, pt in enumerate(parts):
            piece = {**w, "word": pt}
            if span:
                piece[keys[0]] = type(st)(st + span * i)
                piece[keys[1]] = type(st)(st + span * (i + 1))
            out.append(piece)
    return out


def _is_syllable_soup(words: list[dict]) -> bool:
    """
    "멘 탈 흔" 처럼 한 글자씩 흩어져 나온 인식인지.
    말이 잘린 자리에서 Whisper가 음절만 토해낸 경우라 자막으로 쓸 수 없다.
    """
    if len(words) < 3:
        return False
    ones = sum(1 for w in words if len(re.sub(r"[^가-힣]", "", w["word"])) == 1)
    return ones / len(words) >= 0.7


def drop_rare_latin(segments: list[dict], script_tokens: set[str] | None = None) -> int:
    """
    영상에서 딱 한 번만 나온 영문 덩어리는 지운다 ("QUES" 같은 환각).
    - 3글자 이하(AI, GS, LNG)나 알려진 표기, 대본에 있는 말은 그대로 둔다
    - 두 번 이상 나온 말은 실제로 쓴 표기로 보고 남긴다
    반환: 지운 개수
    """
    from collections import Counter
    script_tokens = script_tokens or set()
    freq = Counter()
    for seg in segments:
        for w in seg.get("words") or []:
            for tok in re.findall(r"[A-Za-z]{2,}", w["word"]):
                freq[tok] += 1
    drop = {t for t, n in freq.items()
            if n == 1 and len(t) >= 4
            and t.lower() not in _LATIN_KEEP and t.lower() not in script_tokens}
    if not drop:
        return 0
    n_drop = 0
    for seg in segments:
        ws = []
        for w in seg.get("words") or []:
            new = re.sub(r"[A-Za-z]{2,}", lambda m: "" if m.group(0) in drop else m.group(0),
                         w["word"]).strip()
            if not new:
                n_drop += 1
                continue
            ws.append({**w, "word": new})
        if seg.get("words"):
            seg["words"] = ws
            seg["text"] = " ".join(x["word"] for x in ws) or NO_SPEECH_PLACEHOLDER
    return n_drop


def collapse_repeats(words: list[dict]) -> list[dict]:
    """같은 어절이 3번 이상 이어지면 한 번만 남긴다 ("오늘 오늘 오늘" → "오늘")."""
    out: list[dict] = []
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and words[j + 1]["word"] == words[i]["word"]:
            j += 1
        if j - i + 1 >= 3:                 # 3번 이상 이어지면 하나로
            merged = dict(words[i])
            merged["end"] = words[j].get("end", merged.get("end"))
            out.append(merged)
        else:
            out.extend(words[i:j + 1])
        i = j + 1
    return out


def cap_by_speech_rate(words: list[dict], clip_sec: float) -> list[dict]:
    """
    짧은 클립에 말이 되지 않는 분량이 인식되면(Whisper 환각) 잘라낸다.

    0.5초짜리 엔지 조각에 30자짜리 문장이 통째로 들어오는 일이 잦은데, 그대로 두면
    0.07초짜리 자막이 우수수 지나가고 내용도 틀린다. 실제로 말할 수 있는 분량만 남긴다.
    """
    if not words or clip_sec <= 0:
        return words
    budget = max(6, int(clip_sec * MAX_CHARS_PER_SEC))
    used, out = 0, []
    for w in words:
        used += len(w["word"]) + (1 if out else 0)
        if used > budget and out:
            break
        out.append(w)
    return out


def transcribe_all_clips(video_path: Path,
                         keep_ranges: list[tuple[float, float, int, int]],
                         script_text: str = "",
                         progress: dict | None = None) -> list[dict]:
    """
    ★ 영상 클립을 하나씩 개별 인식한다 (전체 한 번에 인식하지 않음).
    전체 인식은 짧은 엔지컷을 통째로 놓치기 때문에, 클립마다 따로 물어봐서
    '모든 클립이 빠짐없이' 자기 자막을 갖도록 보장한다. 느리지만 확실하다.
    인식이 안 되는 클립도 자막 클립 자체는 만든다(내용은 "...").
    각 단어는 클립 안에서의 실제 발화 시각을 그대로 갖고 있어 자막이
    영상과 밀리지 않는다.
    """
    segs = []
    total = len(keep_ranges)
    n_halluc = 0
    script_tokens = {_norm_token(w).lower() for w in script_text.split()} if script_text else set()
    script_tokens.discard("")
    for i, (ks, ke, _tl_s, _tl_e) in enumerate(keep_ranges):
        words = []
        if ke - ks >= 0.15:
            raw_words = transcribe_clip_words(video_path, ks, ke, script_text)
            for w in raw_words:
                # 효과음 태그·영어 환각·이모지/특수문자 제거 (단어 단위로 적용)
                cleaned = sanitize_word(clean_recognized_text(w["word"], script_tokens))
                if not cleaned:
                    continue
                for piece in cleaned.split():   # 정제 후 한 단어가 여러 조각이 될 수도 있음
                    words.append({
                        "start": min(max(w["start"], ks), ke),
                        "end": min(max(w["end"], ks), ke),
                        "word": piece,
                    })
        words = cap_by_speech_rate(collapse_repeats(split_inner_commas(words)), ke - ks)
        if _is_syllable_soup(words):
            words = []                  # "멘 탈 흔" → 말로 치지 않음
        if words and is_hallucinated_line(" ".join(w["word"] for w in words), ke - ks):
            words = []          # "시청해주셔서 감사합니다" 같은 상투 문구 → 말 없음 처리
            n_halluc += 1
        if words:
            text = " ".join(w["word"] for w in words)
        else:
            text = NO_SPEECH_PLACEHOLDER
        segs.append({"start": ks, "end": ke, "text": text, "words": words})
        if progress is not None:
            progress["done"] = i + 1
            progress["total"] = total
            progress["halluc"] = n_halluc
    return segs



def make_srt(chunks: list[dict]) -> str:
    """
    자막 클립 목록 [{"start_us", "end_us", "text"}] → SRT 변환.
    (분할은 subtitle_chunks_for_timeline에서 이미 완료된 상태)
    """
    def fmt(us: int) -> str:
        total_ms = round(us / 1000)
        h, rem = divmod(total_ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for idx, chunk in enumerate(chunks, start=1):
        lines.append(str(idx))
        lines.append(f"{fmt(chunk['start_us'])} --> {fmt(chunk['end_us'])}")
        lines.append(chunk["text"])
        lines.append("")
    return "\n".join(lines)


def _norm_token(s: str) -> str:
    """정렬 비교용 정규화: 한글/영숫자만 남기고 소문자화 (문장부호·공백 제거)."""
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _best_script_window(clip_norm: list[str], script_norm: list[str],
                        near: int = 0) -> tuple[int, int, float]:
    """
    클립에서 인식된 단어열과 가장 잘 맞는 대본의 '연속 구간'을 찾는다.
    대본 구간을 소모하지 않으므로 같은 문장을 여러 번 찍은 엔지컷도 각자 매칭된다.
    반환: (대본 시작 index, 길이, 유사도)
    """
    import difflib
    target = "".join(clip_norm)
    ns = len(script_norm)
    if not target or ns == 0:
        return 0, 0, 0.0
    n = len(clip_norm)
    sm = difflib.SequenceMatcher(None, autojunk=False)
    sm.set_seq1(target)
    best = (0, 0, 0.0)
    for start in range(ns):
        maxlen = min(ns - start, n + 3)
        for length in range(max(1, n - 2), maxlen + 1):
            sm.set_seq2("".join(script_norm[start:start + length]))
            if sm.quick_ratio() <= best[2]:
                continue          # 상한값으로 가지치기
            r = sm.ratio()
            if near:
                r -= 0.0005 * min(abs(start - near), 60)  # 동점일 때만 순서 우대
            if r > best[2]:
                best = (start, length, r)
    return best


def _retime_words(src: list[dict], new_words: list[str]) -> list[dict]:
    """src 단어들의 시간 구간을 new_words에 글자 수 비례로 재분배."""
    if not new_words:
        return src
    s, e = src[0]["tl_start"], src[-1]["tl_end"]
    span = max(e - s, 0)
    total = sum(len(t) for t in new_words) or 1
    out, cur = [], s
    for i, t in enumerate(new_words):
        we = e if i == len(new_words) - 1 else cur + int(span * len(t) / total)
        out.append({**src[0], "word": t, "tl_start": cur, "tl_end": max(we, cur)})
        cur = we
    return out


def align_clip_to_script(words: list[dict], script_words: list[str], script_norm: list[str],
                         near: int = 0, threshold: float = 0.5) -> tuple[list[dict], int, int]:
    """
    한 영상 클립에서 인식된 단어열을, 대본의 '해당 문맥 구간'에 정렬해서 철자를 교정.
    문맥으로 맞추므로 단어 하나씩 비교하는 것보다 오타가 훨씬 줄고,
    띄어쓰기가 달라도(삼성이주가 → 삼성이 주가) 바로잡힌다.

    규칙 (엔지컷 편집을 위해 내용은 절대 바꾸지 않음):
      - 일치/불일치: 대본 단어로 교체 (철자 교정)
      - 대본에만 있는 단어: 말하지 않았으므로 추가하지 않음 (엔지컷 유지)
      - 인식에만 있는 단어: 대본에 없는 애드립 → 원문 그대로 유지
      - 대본에서 맞는 구간을 못 찾으면 클립 전체를 원문 그대로 유지
    반환: (교정된 단어열, 다음 클립 탐색 힌트, 교정된 단어 수)
    """
    import difflib
    clip_norm = [_norm_token(w["word"]) for w in words]
    if not any(clip_norm) or not script_norm:
        return words, near, 0

    start, length, ratio = _best_script_window(clip_norm, script_norm, near)
    if length == 0 or ratio < threshold:
        return words, near, 0      # 대본에 없는 발화 → 손대지 않음

    win_words = script_words[start:start + length]
    win_norm = script_norm[start:start + length]
    sm = difflib.SequenceMatcher(None, clip_norm, win_norm, autojunk=False)

    out, fixed = [], 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                sw = sanitize_word(win_words[j1 + k]) or words[i1 + k]["word"]
                if words[i1 + k]["word"] != sw:
                    fixed += 1
                out.append({**words[i1 + k], "word": sw})
        elif tag == "replace":
            out.extend(_retime_words(words[i1:i2], win_words[j1:j2]))
            fixed += (i2 - i1)
        elif tag == "delete":
            out.extend(words[i1:i2])   # 애드립 → 그대로
        # insert(대본에만 있는 단어): 말하지 않았으므로 넣지 않음
    return out, start + length, fixed


# 자막에 허용할 문자: 한글(음절+자모)/영문/숫자/공백/기본 문장부호(. , ? ! % -)
# 그 외(이모지, ♪·♫ 같은 음악기호, 화살표 등 특수문자)는 전부 제거한다.
_SUBTITLE_DROP_RE = re.compile(r"[^가-힣ㄱ-ㅣa-zA-Z0-9\s,.?!%\-+]")


# 같은 글자가 3번 이상 이어지는 인식 오류 ("오를때에에에" → "오를때에")
_REPEAT_CHAR_RE = re.compile(r"([가-힣])\1{2,}")


def _is_gibberish(word: str) -> bool:
    """
    Whisper가 잡음에 뱉는 알아볼 수 없는 덩어리인지.
      "쎈쎼쎽쎵쎶쎱쎡쎴쎰쎬쎩쎸쎠쎹..." 같은 것 (한 어절이 지나치게 길거나
      된소리(ㄲㄸㅃㅆㅉ)로 시작하는 글자만 잔뜩 이어진 경우)
    """
    han = [c for c in word if "가" <= c <= "힣"]
    if len(han) >= 12:
        return True
    if len(han) >= 6:
        # 한글 음절 코드에서 초성 인덱스: 1=ㄲ 4=ㄸ 8=ㅃ 10=ㅆ 13=ㅉ
        tense = sum(1 for c in han if (ord(c) - 0xAC00) // 588 in (1, 4, 8, 10, 13))
        if tense / len(han) >= 0.6:
            return True
    return False


def sanitize_word(word: str) -> str:
    """한 단어에서 이모지·특수문자 제거 (자막에 이상한 기호가 들어가지 않게)."""
    word = _SUBTITLE_DROP_RE.sub("", word)
    word = _REPEAT_CHAR_RE.sub(r"\1", word)
    word = word.strip()
    return "" if _is_gibberish(word) else word


# 마침표 제거용: 숫자와 숫자 사이(소수점 3.7%)가 아닌 점만 지운다
_PERIOD_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")


def strip_periods(text: str) -> str:
    """
    자막에서 마침표를 없앤다 (draft 자막·SRT 공통).
    - 3.7% 같은 소수점은 그대로 둔다
    - 말소리 없는 클립 표시("...")는 유지
    - 물음표·느낌표·쉼표는 건드리지 않는다
    """
    if text == NO_SPEECH_PLACEHOLDER:
        return text
    out = _PERIOD_RE.sub("", text)
    out = re.sub(r",(?=\S)", ", ", out)          # "두고,새" → "두고, 새"
    out = re.sub(r"\s{2,}", " ", out).strip()
    # 자막 끝에 남는 쉼표는 지운다 ("돈 넣은 건," → "돈 넣은 건")
    return out.rstrip(",").strip()


def unify_latin_words(segments: list[dict]) -> int:
    """
    영상 전체에서 같은 영어 단어가 제각각 인식된 걸 하나로 통일한다.
      NVIDIA / Nvidia / MVDIIA  →  가장 많이 나온 표기(NVIDIA)로
    대소문자만 다른 것은 무조건 합치고, 철자가 살짝 다른 것은 많이 나온 쪽이
    2번 이상일 때만 합친다. (NAVER 처럼 아예 다른 단어는 건드리지 않는다)
    반환: 바꾼 단어 수
    """
    import difflib
    from collections import Counter
    freq = Counter()
    for seg in segments:
        for w in seg.get("words") or []:
            for tok in re.findall(r"[A-Za-z]{2,}", w["word"]):
                freq[tok] += 1
    if not freq:
        return 0

    canon: dict[str, str] = {}
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    for tok, n in ranked:
        for base, bn in ranked:
            if base == tok or tok in canon:
                continue
            if bn < n or (bn == n and base > tok):
                continue
            if base.lower() == tok.lower():             # 대소문자만 다름
                canon[tok] = base
                break
            if bn >= 2 and len(tok) >= 4 and \
                    difflib.SequenceMatcher(None, tok.upper(), base.upper()).ratio() >= 0.65:
                canon[tok] = base                        # 흔한 표기의 오타
                break
    if not canon:
        return 0

    fixed = 0
    for seg in segments:
        for w in seg.get("words") or []:
            new = re.sub(r"[A-Za-z]{2,}", lambda m: canon.get(m.group(0), m.group(0)), w["word"])
            if new != w["word"]:
                w["word"] = new
                fixed += 1
        ws = seg.get("words") or []
        if ws:
            seg["text"] = " ".join(x["word"] for x in ws)
    return fixed


def build_word_stream(segments: list[dict]) -> list[dict]:
    """
    Whisper 세그먼트 → 단어 스트림 [{"time": 발화_중간(초), "start": 발화_시작(초), "word": str}, ...].
    - 단어별 타임스탬프(word_timestamps)가 있으면 실제 발화 시각 사용
    - 없으면(대본 보정으로 텍스트가 교체된 경우 등) 세그먼트 구간에 균등 배분
    - 이모지·특수문자는 제거하고, 비어버린 단어는 버린다
    """
    stream = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        words = seg.get("words") or []
        text_words = text.split()
        if words and len(words) == len(text_words):
            for w, tw in zip(words, text_words):
                tw = sanitize_word(tw)
                if tw:
                    stream.append({"time": (w["start"] + w["end"]) / 2, "start": w["start"], "end": w["end"], "word": tw})
        else:
            n = len(text_words)
            seg_dur = max(seg["end"] - seg["start"], 0.01)
            for wi, tw in enumerate(text_words):
                tw = sanitize_word(tw)
                if not tw:
                    continue
                ws = seg["start"] + seg_dur * wi / n
                we = seg["start"] + seg_dur * (wi + 1) / n
                stream.append({"time": (ws + we) / 2, "start": ws, "end": we, "word": tw})
    stream.sort(key=lambda x: x["time"])
    return stream


def map_words_to_timeline(stream: list[dict],
                          keep_ranges: list[tuple[float, float, int, int]]) -> list[dict]:
    """
    각 단어(원본 발화 시각)를 컷편집된 타임라인 시각(us)으로 매핑.
    - 단어 [start,end]와 가장 많이 겹치는 클립을 골라, 그 클립 안 상대위치로 변환
      → 무음 컷으로 시간이 당겨져도 자막이 실제 화면(발화)과 맞는다
    - 겹치는 클립이 없으면(잘린 무음 안) 중간점 기준 가장 가까운 클립에 붙임
    반환: [{"word", "tl_start"(us), "tl_end"(us)}, ...] tl_start 순 정렬
    """
    if not keep_ranges:
        return []
    mapped = []
    for w in stream:
        ws = w["start"]
        we = w.get("end", ws)
        if we < ws:
            we = ws
        best_i, best_ov = None, 0.0
        for i, (ks, ke, _s, _e) in enumerate(keep_ranges):
            ov = min(we, ke) - max(ws, ks)
            if ov > best_ov:
                best_ov, best_i = ov, i
        if best_i is None:
            t = w["time"]
            best_i, best_d = 0, float("inf")
            for i, (ks, ke, _s, _e) in enumerate(keep_ranges):
                d = 0.0 if ks <= t <= ke else min(abs(t - ks), abs(t - ke))
                if d < best_d:
                    best_d, best_i = d, i
        ks, ke, tl_s, tl_e = keep_ranges[best_i]
        cs = min(max(ws, ks), ke)  # 클립 범위로 클램프
        ce = min(max(we, ks), ke)
        s_us = tl_s + sec_to_us(cs - ks)
        e_us = tl_s + sec_to_us(ce - ks)
        mapped.append({"word": w["word"], "clip": best_i,
                       "tl_start": s_us, "tl_end": max(e_us, s_us)})
    mapped.sort(key=lambda x: (x["clip"], x["tl_start"]))
    return mapped


def _ends_clause(word: str) -> bool:
    """한국어 구절 끝(구두점)인지 — 여기서 끊으면 자연스럽다."""
    return word[-1:] in ",.?!…\"”)"


# 끊기 좋은 자리를 판단하는 한국어 어미·조사 목록.
# 완성본 자막이 실제로 끊은 지점('불장인데', '대응법까지', '알려줄테니까',
# '찐반등인지', '열리기', '우리가', '반등에' 등)을 분석해 정리했다.
_CONNECTIVE_ENDINGS = (          # 연결어미 — 절이 끝나는 자리라 끊기 가장 좋다
    "는데", "ㄴ데", "은데", "인데", "면서", "으면", "라면", "다면", "면",
    "아서", "어서", "여서", "지만", "으니까", "니까", "니깐",
    "까지", "부터", "는지", "은지", "인지", "이라", "아니라",
    "도록", "거나", "든지", "때문에", "덕분에", "위해", "통해",
    "해도", "아도", "어도", "여도", "더라도", "면서도", "는데도",
    "대로", "만큼", "처럼", "같이", "보다", "라서", "느라", "려고", "면은",
)
# 절(節)의 끝을 알리는 말 — "~할 때 / ~한 뒤 / ~한 다음" 뒤에서 끊으면 자연스럽다.
# (앞에서 끊으면 안 되는 의존명사이기도 해서 _BOUND_NOUNS 와 짝을 이룬다:
#  "흔들릴 / 때" 는 막고, "흔들릴 때 / 같이" 는 권한다)
_CLAUSE_TAILS = ("때", "때는", "때가", "때도", "때면", "뒤", "뒤에", "후", "후에",
                 "다음", "다음에", "동안", "순간", "순간에", "무렵", "이후", "이전")

# 뒷말과 한 덩어리로 이어지는 연결어미 — 어미이긴 하지만 여기서 끊으면
# "갔다가 / 오기라도 했지" 처럼 한 동작이 두 줄로 쪼개진다. 되도록 안 끊는다.
#   반도체는 다 같이 갔다가 / 오기라도 했지,  X
#   반도체는 다 같이 / 갔다가 오기라도 했지,  O
_CHAINING_ENDINGS = ("다가",)

# 그 자체가 한 단어이기도 한 어미들 (짧으면 연결어미로 보지 않는다)
_STANDALONE_RISK = ("대로", "만큼", "처럼", "같이", "보다")

# 어미처럼 끝나지만 사실은 명사인 말들 — 여기서 끊으면 명사구가 잘린다.
#   "레버리지 상품이" 의 '레버리지' 를 종결어미 '-지' 로 잘못 보던 문제
_ENDING_LOOKALIKE_NOUNS = {
    "레버리지", "이미지", "메시지", "페이지", "패키지", "에너지", "스테이지",
    "브릿지", "시야", "분야", "바다", "사이다", "소다", "미디어", "데이터",
}
_FINAL_ENDINGS = (               # 종결어미 — 문장이 끝나는 자리
    "습니다", "ㅂ니다", "세요", "예요", "이에요", "거든", "잖아", "거야",
    "구나", "네요", "군요", "죠", "요", "다", "야", "해", "지",
)
_PARTICLES = (                   # 조사 — 명사 뒤라 끊어도 무난
    "에서", "에게", "한테", "으로", "마다", "조차", "밖에", "이랑",
    "이나", "나마", "이나마", "라도", "이라도", "마저", "치고", "이든", "든",
    "은", "는", "이", "가", "을", "를", "에", "도", "만", "의", "와", "과", "로",
)


# 앞말에 붙어 다니는 의존명사 — 이 앞에서 끊으면 "하는 / 게" 처럼 어색해진다.
# ("거래되는" 처럼 우연히 같은 글자로 시작하는 말과 섞이지 않게 통째로 비교한다)
_BOUND_NOUNS = {
    "것", "것을", "것이", "것도", "것만", "것과", "것처럼",
    "거", "거야", "거지", "거든", "거라", "걸", "걸로",
    "게", "게다", "겁니다", "건", "건데",
    "수", "수가", "수도", "수는", "수밖에",
    "때", "때가", "때는", "때도", "때문", "때문에", "때문이야",
    "중", "중이야", "중이고", "중이다", "중에", "중에서",
    "뿐", "뿐이야", "뿐만", "데", "데서", "데서나", "데다",
    "줄", "줄은", "터", "테니깐", "테니까", "셈", "척", "만큼", "동안", "채",
}
# 뒷말을 꾸미는 말 — 여기서 끊으면 "전 / 세계" 처럼 한 덩어리가 쪼개진다.
_NO_END_WORDS = {
    "전", "제", "약", "총", "각", "매", "첫", "두", "세", "네", "몇", "여러", "온갖",
    "다", "더", "잘", "못", "안", "왜", "좀", "딱", "꼭", "또", "새", "온", "막",
    "그", "이", "저", "그런", "이런", "저런", "무슨", "어떤", "어느", "모든", "아무",
    "같은", "다른", "남은", "좋은", "나쁜", "큰", "작은", "많은", "적은", "높은",
    "낮은", "빠른", "느린", "새로운", "짧은", "긴", "어린", "젊은", "진짜", "완전",
}
# 뒷말을 꾸미는 동사형 어미 — 다음 말이 이걸로 끝나면 그 앞에서 끊는 게 낫다
_ADNOMINAL_TAILS = ("린", "던", "운", "난", "킨", "친", "된", "한", "인", "는", "울")


def _is_clause_tail(word: str, prev_word: str = "") -> bool:
    """
    "~할 때 / ~한 뒤 / ~한 다음" 처럼 절이 끝나는 말인지.
    앞말이 뒷말을 꾸미는 형태(흔들릴 때, 되돌린 다음)일 때만 절로 본다.
    "3시간 동안" "폭락 때" 처럼 명사 뒤에 붙은 건 절이 아니다.
    """
    core = word.strip().rstrip("\"'”’)]}").rstrip(",.?!")
    if not (core in _CLAUSE_TAILS or (len(core) > 1 and core.endswith(_CLAUSE_TAILS))):
        return False
    if not prev_word:
        return True
    return _ends_adnominal(prev_word)


def _ends_adnominal(word: str) -> bool:
    """뒷말을 꾸미는 형태인지 — "물린/했던/흔들릴/할" 처럼."""
    w = word.strip().rstrip("\"'”’)]}").rstrip(",.?!")
    if len(w) < 2:
        return False
    if w.endswith(_ADNOMINAL_TAILS):
        return True
    c = w[-1]
    return "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 == 8      # ㄹ 받침 (-을/-ㄹ 관형형)


def _break_score(word: str, next_word: str = "", prev_word: str = "") -> int:
    """이 어절 뒤에서 끊었을 때 얼마나 자연스러운지 (높을수록 좋은 자리)."""
    w = word.strip()
    if not w:
        return 0
    if w[-1] in ".?!…":
        return 100                       # 문장 끝
    if w[-1] in ",;":
        return 80                        # 쉼표
    core = w.rstrip("\"'”’)]}")
    if not core:
        return 10
    # 다음 말이 의존명사거나, 이 말이 뒷말을 꾸미는 말이면 끊지 않는다
    if core in _NO_END_WORDS:
        return 0
    if next_word and next_word.strip().rstrip(",.?!") in _BOUND_NOUNS:
        return 0
    if core.endswith(_STANDALONE_RISK) and len(core) < 5:
        # "그대로" "이만큼" "다 같이" 처럼 그 자체가 한 단어인 경우 —
        # 연결어미(-대로/-만큼/-처럼)만큼 좋은 자리는 아니지만 끊어도 무난하다
        return 30
    if core in _BOUND_NOUNS and _ends_adnominal(prev_word):
        return 50                        # "있는 게" 처럼 덩어리가 완성된 자리
                                         # ("3시간 동안" 처럼 명사 뒤면 해당 없음)
    if _is_clause_tail(core, prev_word) and (prev_word or _ends_adnominal(prev_word)):
        return 55                        # "~할 때 / ~한 뒤 / ~한 다음" → 절이 끝나는 자리
    if core in _ENDING_LOOKALIKE_NOUNS:
        return 10                        # 어미처럼 보이는 명사 ("레버리지")
    if core.endswith(_CHAINING_ENDINGS) and len(core) > 2:
        return 20                        # "갔다가" — 뒷말과 한 덩어리라 되도록 안 끊는다
    if core.endswith(_CONNECTIVE_ENDINGS):
        return 60                        # 연결어미
    if core.endswith(_FINAL_ENDINGS):
        return 50                        # 종결어미
    if core.endswith(_PARTICLES):
        return 30                        # 조사
    # 다음 말이 뒷말을 꾸미는 형태면("하이닉스 / 물린 사람들") 조금 미룬다
    if _ends_adnominal(next_word):
        return 5
    return 10                            # 그 밖 (관형형 등 — 끊으면 어색)


def _group_len(ws: list[dict]) -> int:
    """어절 묶음을 이어붙였을 때의 글자 수 (공백 포함)."""
    return sum(len(x["word"]) for x in ws) + max(0, len(ws) - 1)


def _best_break_index(cur: list[dict], soft_min: int, after: str = "",
                      prefer_early: bool = False) -> int:
    """
    cur 안에서 가장 자연스럽게 끊을 수 있는 위치 (동점이면 뒤쪽 우선).
    목표 길이보다 조금 짧은 자리라도 훨씬 자연스러우면 그쪽을 쓴다.
      '그래서 오늘 하이닉스 / 물린 사람들' → '그래서 오늘 / 하이닉스 물린 사람들'
    """
    late = (-1, len(cur) - 1)            # (점수, 위치)
    early = (-1, -1)
    early_min = max(6, soft_min - 4)     # 너무 짧은 조각이 생기지 않는 선까지만 앞당김
    acc = 0
    for i, x in enumerate(cur):
        acc += len(x["word"]) + (1 if i else 0)
        nxt = cur[i + 1]["word"] if i + 1 < len(cur) else after
        sc = _break_score(x["word"], nxt, cur[i - 1]["word"] if i else "")
        if acc >= soft_min or i == len(cur) - 1:
            # prefer_early: 뒤에 붙을 말이 쉼표로 끝나면, 그 말이 앞말과 함께
            # 한 조각이 되도록 동점일 때 앞쪽에서 끊는다
            if (sc > late[0]) if prefer_early else (sc >= late[0]):
                late = (sc, i)
        elif acc >= early_min:
            if sc > early[0]:
                early = (sc, i)
    return early[1] if early[0] > late[0] else late[1]


def _good_break_ahead(words: list[dict], i: int, budget: int) -> bool:
    """
    앞으로 budget 글자 안에 '어미로 끝나는 좋은 자리'가 있는지.
    있으면 지금 어정쩡하게 끊지 않고 거기까지 붙이는 게 낫다.
      "전력 사이클이 / 한번 돌면" X → "전력 사이클이 한번 돌면" O
    """
    used = 0
    for k in range(i, len(words)):
        used += len(words[k]["word"]) + (1 if k > i else 0)
        if used > budget:
            return False
        nxt = words[k + 1]["word"] if k + 1 < len(words) else ""
        prev = words[k - 1]["word"] if k else ""
        if _break_score(words[k]["word"], nxt, prev) >= 50:
            return True
    return False


def _rest_len(words: list[dict], i: int, cap: int = 40) -> int:
    """words[i]부터 문장이 끝날 때까지의 글자 수 (공백 포함, cap에서 멈춤)."""
    total = 0
    for k in range(i, len(words)):
        w = words[k]["word"]
        total += len(w) + (1 if k > i else 0)
        if w[-1:] in ".?!…" or total > cap:
            break
    return total


def _rebalance_tail(groups: list[list[dict]], hard: int) -> list[list[dict]]:
    """
    마지막 조각이 "아니야", "1위야" 처럼 혼자 덩그러니 남으면,
    앞 조각에서 어절 몇 개를 넘겨 두 조각을 고르게 만든다.
      아무 종목이나 하는 게 / 아니야   →   아무 종목이나 / 하는 게 아니야
    """
    if len(groups) < 2:
        return groups
    prev, last = groups[-2], groups[-1]
    if _group_len(last) > 4 or len(prev) < 2:
        return groups
    if _ends_clause(last[-1]["word"]):
        return groups                    # 꼬리가 그 자체로 한 문장이면(오케이?) 그대로 둔다
    best = None
    for k in range(1, len(prev)):
        new_prev, new_last = prev[:-k], prev[-k:] + last
        if _group_len(new_prev) < 3 or _group_len(new_last) > hard:
            break
        sc = _break_score(new_prev[-1]["word"], new_last[0]["word"])
        balance = -abs(_group_len(new_prev) - _group_len(new_last))
        if best is None or (sc, balance) > (best[2], best[3]):
            best = (new_prev, new_last, sc, balance)
    if best:
        groups[-2], groups[-1] = best[0], best[1]
    return groups


def limit_groups(groups: list[list[dict]], max_groups: int) -> list[list[dict]]:
    """
    조각 수를 max_groups 이하로 줄인다 (짧은 클립에서 자막이 깜빡이지 않게).
    글자 수가 고르게 나뉘도록 앞에서부터 묶는다. 순서·내용은 그대로 둔다.
    """
    if max_groups <= 0 or len(groups) <= max_groups:
        return groups
    total = sum(_group_len(g) for g in groups)
    target = total / max_groups
    out: list[list[dict]] = []
    cur: list[dict] = []
    for i, g in enumerate(groups):
        cur = cur + g
        left = len(groups) - i - 1          # 남은 조각 수
        need = max_groups - len(out) - 1    # 앞으로 더 만들어야 할 묶음 수
        if len(out) < max_groups - 1 and (_group_len(cur) >= target or left <= need):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out[:max_groups] if len(out) <= max_groups else out[:max_groups - 1] + [
        [w for g in out[max_groups - 1:] for w in g]]


def chunk_words_korean(words: list[dict], max_chars: int, tolerance: int = 3,
                       gap_break_us: int = SUB_PAUSE_BREAK_US) -> list[list[dict]]:
    """
    연속된 단어열을 한국어 자막 조각으로 분할.
    - ★ 글자 수만 보고 자르지 않고, 한국어 연결어미·종결어미·조사를 찾아
      '의미 덩어리가 안 깨지는 자리'에서 끊는다.
      예) '확대하면 신나는 불장인데' 를 한 조각으로 유지하고
          '불장인데 축소하면' 처럼 두 절이 섞이지 않게 한다.
    - 문장부호(. ? !)가 나오면 길이와 무관하게 즉시 끊는다.
    - 목표 길이 max_chars 근처에서 어미가 나오면 거기서 끊고,
      최대 max_chars+5자(완성본 최대 14자 수준)를 넘으면 그 안에서
      가장 점수 높은 자리를 찾아 끊는다.
    - 단어 사이 시간 간격이 gap_break_us 이상이면(말 사이 쉼) 무조건 끊음
    - 너무 짧은 꼬리 조각은 직전 조각에 합침 (문장부호로 끝난 경우는 유지)
    """
    words = split_inner_commas(words)
    hard = max_chars + tolerance         # 기본 상한
    soft_min = max(4, max_chars - 2)     # 이 길이부터 어미에서 끊을 수 있음
    groups: list[list[dict]] = []
    cur: list[dict] = []

    for wi, wd in enumerate(words):
        nxt = words[wi + 1]["word"] if wi + 1 < len(words) else ""
        prev_word = words[wi - 1]["word"] if wi else ""
        # 말 사이 쉼이 길면 먼저 끊기
        if cur and "tl_start" in wd and "tl_end" in cur[-1]:
            if wd["tl_start"] - cur[-1]["tl_end"] >= gap_break_us:
                groups.append(cur)
                cur = []

        sc = _break_score(wd["word"], nxt, prev_word)
        # 어미로 끝나는 어절이면 상한을 조금 넘겨도 붙인다
        # ('확대하면 신나는 / 불장인데' 로 절이 잘리는 것을 막기 위함)
        allow = hard + 2 if sc >= 50 else hard
        if cur and _group_len(cur + [wd]) > allow:
            i = _best_break_index(cur, soft_min, wd["word"], prefer_early=sc >= 80)
            groups.append(cur[:i + 1])
            cur = cur[i + 1:]

        cur.append(wd)
        cl = _group_len(cur)

        # 바로 다음 말이 쉼표로 끝나고 같이 담을 자리가 있으면 끊지 않고 붙인다
        # ("오늘 그 답 주려고 / 왔어, 물타도" → "오늘 그 답 주려고 왔어, / 물타도")
        nxt_sc = _break_score(nxt) if nxt else 0
        if sc < 100 and 80 <= nxt_sc < 100 and \
                _group_len(cur + [{"word": nxt}]) <= hard + 2:
            continue                    # 다음이 쉼표 → 같이 담는다 (문장 끝은 제외)

        if sc >= 100:                                   # 문장 끝 → 무조건
            groups.append(cur); cur = []
        elif cl >= 5 and 50 <= sc < 80 and nxt_sc >= 100:
            groups.append(cur); cur = []                # 어미로 끝났는데 다음이 한 문장 → 먼저 끊기
        elif cl >= 5 and sc >= 50 and _is_clause_tail(wd["word"], prev_word):
            groups.append(cur); cur = []                # "~할 때" 뒤 → 절이 끝나는 자리
        elif cl >= 5 and sc >= 80 and cl + 1 + _rest_len(words, wi + 1) > allow:
            groups.append(cur); cur = []                # 쉼표 (남은 문장이 다 안 들어갈 때만)
        elif cl >= soft_min and 50 <= sc < 80:          # 어미 → 목표 길이 근처에서
            groups.append(cur); cur = []
        elif cl >= max(5, soft_min - 1) and 30 <= sc < 50 and nxt \
                and cl + 1 + len(nxt) > max_chars \
                and cl + 1 + _rest_len(words, wi + 1) > allow \
                and not _good_break_ahead(words, wi + 1, hard + 2 - cl - 1):
            groups.append(cur); cur = []                # 다음 말까지 넣으면 목표를 넘길 때
                                                        # (남은 문장이 통째로 들어가면 그냥 붙인다)
        elif cl >= 6 and sc >= 30 and _ends_adnominal(nxt) \
                and max_chars < cl + 1 + _rest_len(words, wi + 1) \
                and _rest_len(words, wi + 1) <= hard:
            groups.append(cur); cur = []                # "5분 주사로 / 바꿔주는 기술이야"
                                                        # (남은 문장이 딱 한 줄일 때만)
        elif cl >= max_chars and sc >= 30:              # 조사 등 무난한 자리에서만
            groups.append(cur); cur = []
        # 끊기 나쁜 자리(관형형·꾸미는 말·의존명사 앞)면 상한까지 더 붙였다가
        # 위의 _best_break_index 로 되돌아가 좋은 자리에서 끊는다

    if cur:
        cl = _group_len(cur)
        if groups and cl <= tolerance + 2 and not _ends_clause(groups[-1][-1]["word"]):
            prev = groups[-1]
            # 시간 간격이 크지 않을 때만 꼬리 합침
            close = "tl_start" not in cur[0] or (cur[0]["tl_start"] - prev[-1].get("tl_end", cur[0]["tl_start"]) < gap_break_us)
            # 짧은 꼬리("아니야", "1위야")가 혼자 남지 않도록 상한을 조금 넘겨도 합친다
            if _group_len(prev) + 1 + cl <= hard + 2 and close:
                groups[-1] = prev + cur
                cur = []
        if cur:
            groups.append(cur)
    return _rebalance_tail(groups, hard + 2)


def subtitle_chunks_for_timeline(segments: list[dict],
                                 keep_ranges: list[tuple[float, float, int, int]],
                                 max_chars: int = MAX_SUBTITLE_CHARS,
                                 script_text: str = "",
                                 stats: dict | None = None) -> list[dict]:
    """
    최종 자막 목록 생성 (draft 텍스트 트랙 + SRT 공용).

    ★ 자막은 영상 클립 경계를 절대 넘지 않는다.
      촬영 중 같은 문장을 여러 번 말한 엔지컷("삼성이" / "삼성" / "삼성이 주가" ...)이
      각각 별도 클립이 되는데, 클립마다 그 클립에서 실제로 말한 내용이
      자기 자막으로 붙어야 나중에 테이크를 골라내는 편집이 가능하다.

    - 각 단어는 겹침이 가장 큰 클립 하나에 배정 (중복 발화도 그대로 유지)
    - 클립 안에서만 한국어 어절 경계로 max_chars(±3) 분할
    - 클립의 자막은 클립 시작~끝을 빈틈없이 채움 (조각 경계는 실제 발화 시각)
    - 말이 없는 클립은 자막 없음
    - 대본(script_text)이 있으면 클립마다 대본의 해당 문맥 구간에 정렬해 철자 교정
      (내용·순서는 유지, 대본에 없는 애드립은 원문 그대로)
    반환: [{"start_us": int, "end_us": int, "text": str}, ...]
    """
    MIN_DUR_US = 66_667      # 자막 최소 길이 (2프레임)
    MIN_SHOW_US = 300_000    # 한 조각이 최소 이만큼은 떠 있어야 읽힌다 (0.3초)
    # 완성본 자막 기준: 조각 길이 평균 0.93초 / 최대 1.53초, 조각 사이 간격 ~0.03초

    stream = build_word_stream(segments)
    mapped = map_words_to_timeline(stream, keep_ranges)
    if not mapped:
        return []

    script_words = script_text.split() if script_text.strip() else []
    script_norm = [_norm_token(w) for w in script_words] if script_words else []
    near, total_fixed = 0, 0

    # 클립별로 단어를 모은다 (자막이 클립을 넘지 않도록)
    per_clip: dict[int, list[dict]] = {}
    for w in mapped:
        per_clip.setdefault(w["clip"], []).append(w)

    out = []
    # ★ 영상 클립 전체를 순회한다 (말이 인식된 클립만이 아니라)
    #   → 클립 하나도 빠짐없이 자막이 붙고, 자막 길이 합 = 영상 클립 길이가 된다.
    for ci, (_ks, _ke, clip_start, clip_end) in enumerate(keep_ranges):
        words = per_clip.get(ci, [])

        # 대본 참고 교정: 이 클립이 대본의 어느 부분인지 찾아 문맥에 맞춰 철자만 교정
        if script_norm and words:
            words, near, fixed = align_clip_to_script(words, script_words, script_norm, near)
            total_fixed += fixed

        groups = chunk_words_korean(words, max_chars) if words else []
        # 클립이 짧으면 조각 수를 줄인다 — 0.07초짜리 자막이 우수수 지나가지 않게
        if groups:
            groups = limit_groups(groups, max(1, (clip_end - clip_start) // MIN_SHOW_US))
        if not groups:
            # 말이 없거나 전부 걸러진 클립도 자막 클립은 만든다 (클립 길이 그대로)
            if clip_end > clip_start:
                out.append({"start_us": clip_start, "end_us": clip_end,
                            "text": NO_SPEECH_PLACEHOLDER})
            continue

        # ── 조각별 표시 구간 ──────────────────────────────
        # 자막바가 중간에 사라지지 않도록 클립 전체를 빈틈없이 덮는다.
        # 조각이 바뀌는 '경계'만 실제 발화 시각을 따라가고,
        # 첫 조각은 클립 시작, 마지막 조각은 클립 끝까지 이어진다.
        # 조각 경계는 실제 발화 시각을 따라가되, 각 조각이 최소 MIN_SHOW_US 는
        # 떠 있도록 앞뒤로 밀어준다 (클립에 자리가 있을 때만).
        n_g = len(groups)
        bounds = [clip_start]
        for i, g in enumerate(groups[1:], start=1):
            lo = bounds[-1] + MIN_SHOW_US
            hi = clip_end - (n_g - i) * MIN_SHOW_US
            b = g[0]["tl_start"]
            if lo <= hi:
                b = min(max(b, lo), hi)
            else:                       # 자리가 빠듯하면 최소 2프레임만 확보
                b = min(max(b, bounds[-1] + MIN_DUR_US), clip_end)
            # ★ 길게 끄는 말("올랐는데~~~")이 잘리지 않게:
            #   앞 조각은 자기 마지막 말이 끝나기 전에는 절대 안 끊는다.
            #   (Whisper는 길게 끈 말의 끝과 다음 말의 시작을 겹쳐서 주는 일이 잦다)
            # 겹치는 인식 시각을 대비해 앞 조각에서 '가장 늦게 끝나는' 말을 기준으로
            prev_end = max((w.get("tl_end", 0) for w in groups[i - 1]), default=0)
            if prev_end:
                room = clip_end - (n_g - i) * MIN_DUR_US
                b = min(max(b, prev_end), max(room, bounds[-1] + MIN_DUR_US))
            # 영상 클립과 같은 프레임 격자에 올린다 (한 프레임도 어긋나지 않게)
            b = snap_us_to_frame(b)
            b = min(max(b, bounds[-1] + FRAME_US), clip_end)
            bounds.append(b)
        bounds.append(clip_end)

        clip_out: list[dict] = []
        for gi, g in enumerate(groups):
            s, e = bounds[gi], bounds[gi + 1]
            text = strip_periods(" ".join(w["word"] for w in g)) or NO_SPEECH_PLACEHOLDER
            if e - s < MIN_DUR_US and clip_out:
                # 자리가 부족하면 직전 자막에 합치고, 그 자리까지 직전 자막이 이어받는다
                clip_out[-1]["text"] += " " + text
                clip_out[-1]["end_us"] = e
                continue
            clip_out.append({"start_us": s, "end_us": e, "text": text})

        # ── 클립 경계 강제 정합 ────────────────────────────
        # 이 클립의 자막들은 반드시 clip_start ~ clip_end 를 빈틈없이 채운다.
        # (= 영상 클립 길이와 그 위 자막 클립들의 길이가 정확히 일치)
        clip_out[0]["start_us"] = clip_start
        for a, b in zip(clip_out, clip_out[1:]):
            a["end_us"] = b["start_us"]
        clip_out[-1]["end_us"] = clip_end
        out.extend(clip_out)

    if stats is not None:
        stats["corrected"] = total_fixed
    return out


def subtitle_coverage_report(chunks: list[dict],
                             keep_ranges: list[tuple[float, float, int, int]]) -> tuple[int, int]:
    """
    영상 클립 하나하나에 대해 '그 위 자막들의 길이 합 == 영상 클립 길이' 인지 확인.
    반환: (길이가 정확히 일치하는 클립 수, 전체 클립 수)
    """
    ok = 0
    for _ks, _ke, ts, te in keep_ranges:
        covered = sum(min(c["end_us"], te) - max(c["start_us"], ts)
                      for c in chunks
                      if c["start_us"] < te and c["end_us"] > ts)
        if covered == te - ts:
            ok += 1
    return ok, len(keep_ranges)

def _make_text_material(text_id: str, text: str, task_id: str = "") -> dict:
    """자막 텍스트 material (CapCut 8.7.0 호환)"""
    # G마켓 산스 TTF Bold, 크기 11, 배경 활성화
    content_obj = {
        "styles": [{
            "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0, 1.0]}}, "color": [1.0, 1.0, 1.0, 1.0]},
            "font": {"id": "", "path": "", "title": "G마켓 산스 TTF Bold", "url": ""},
            "range": [0, len(text)],
            "size": 11.0,
            "bold": True, "italic": False,
            "letter_spacing": 0.0, "line_spacing": 0.02, "underline": False,
        }],
        "text": text,
    }
    return {
        "id": text_id,
        "type": "text",
        "recognize_task_id": task_id,
        "name": text[:20] if text else "",
        "recognize_text": "",
        "recognize_model": "",
        "punc_model": "",
        "content": json.dumps(content_obj, ensure_ascii=False),
        "base_content": "",
        "words": {"start_time": [], "end_time": [], "text": []},
        "current_words": {"start_time": [], "end_time": [], "text": []},
        "global_alpha": 1.0,
        "combo_info": {"text_templates": []},
        "caption_template_info": {
            "resource_id": "", "third_resource_id": "", "resource_name": "",
            "category_id": "", "category_name": "", "effect_id": "",
            "request_id": "", "path": "", "is_new": False, "source_platform": 0
        },
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "text_curve": None,
        "text_loop_on_path": False,
        "offset_on_path": 0.0,
        "enable_path_typesetting": False,
        "text_exceeds_path_process_type": 0,
        "text_typesetting_paths": None,
        "text_typesetting_paths_file": "",
        "text_typesetting_path_index": 0,
        "line_spacing": 0.02,
        "has_shadow": False,
        "shadow_color": "", "shadow_alpha": 0.9, "shadow_smoothing": 0.45,
        "shadow_distance": 0.0, "shadow_point": {"x": 0.6364, "y": -0.6364},
        "shadow_angle": -45.0,
        "shadow_thickness_projection_enable": False,
        "shadow_thickness_projection_angle": 0.0,
        "shadow_thickness_projection_distance": 0.0,
        "border_alpha": 1.0, "border_color": "", "border_width": 0.0, "border_mode": 0,
        "style_name": "",
        "text_color": "#ffffff", "text_alpha": 1.0,
        "font_name": "GmarketSansTTFBold", "font_title": "G마켓 산스 TTF Bold", "font_size": 11.0,
        "font_path": "", "font_id": "", "font_resource_id": "",
        "initial_scale": 1.0, "font_url": "",
        "typesetting": 0, "alignment": 1, "line_feed": 1,
        "use_effect_default_color": True,
        "is_rich_text": False,
        "shape_clip_x": False, "shape_clip_y": False,
        "ktv_color": "", "text_to_audio_ids": [],
        "bold_width": 0.0, "italic_degree": 0,
        "underline": False, "underline_width": 0.05, "underline_offset": 0.22,
        "sub_type": 0, "check_flag": 7, "text_size": 11,
        "font_category_name": "", "font_source_platform": 0,
        "font_third_resource_id": "", "font_category_id": "",
        "add_type": 1, "operation_type": 0, "recognize_type": 0,
        "fonts": [],
        "background_color": "#000000", "background_alpha": 0.8, "background_style": 1,
        "background_round_radius": 0.3, "background_width": 0.0, "background_height": 0.0,
        "background_vertical_offset": 0.0, "background_horizontal_offset": 0.0,
        "background_fill": "",
        "single_char_bg_enable": False, "single_char_bg_color": "", "single_char_bg_alpha": 1.0,
        "single_char_bg_round_radius": 0.3, "single_char_bg_width": 0.0,
        "single_char_bg_height": 0.0, "single_char_bg_vertical_offset": 0.0,
        "single_char_bg_horizontal_offset": 0.0,
        "font_team_id": "", "tts_auto_update": False,
        "text_preset_resource_id": "", "group_id": "",
        "preset_id": "", "preset_name": "", "preset_category": "",
        "preset_category_id": "", "preset_index": 0, "preset_has_set_alignment": False,
        "force_apply_line_max_width": False,
        "language": "", "relevance_segment": [], "original_size": [],
        "fixed_width": -1.0, "fixed_height": -1.0, "line_max_width": 0.82,
        "oneline_cutoff": False, "cutoff_postfix": "",
        "subtitle_template_original_fontsize": 0.0, "subtitle_keywords": None,
        "inner_padding": -1.0, "multi_language_current": "none",
        "source_from": "", "is_lyric_effect": False, "lyric_group_id": "",
        "lyrics_template": {"resource_id": "", "resource_name": "", "panel": "", "effect_id": "", "path": "", "category_id": "", "category_name": "", "request_id": ""},
        "is_batch_replace": False, "is_words_linear": False,
        "ssml_content": "", "subtitle_keywords_config": None,
        "sub_template_id": -1, "translate_original_text": "",
    }


def _make_text_segment(seg_id: str, text_mat_id: str, start_us: int, end_us: int,
                       transform_y: float = -0.6907, render_index: int = 15000) -> dict:
    """자막 트랙 세그먼트 (CapCut 8.7.0 호환)"""
    dur = end_us - start_us
    return {
        "id": seg_id,
        "material_id": text_mat_id,
        "source_timerange": None,
        "target_timerange": {"start": start_us, "duration": dur},
        "render_timerange": {"start": 0, "duration": 0},
        "desc": "",
        "state": 0,
        "speed": 1.0,
        "is_loop": False,
        "is_tone_modify": False,
        "reverse": False,
        "intensifies_audio": False,
        "cartoon": False,
        "volume": 1.0,
        "last_nonzero_volume": 1.0,
        "clip": {
            "scale": {"x": 1.11, "y": 1.11},
            "rotation": 0.0,
            "transform": {"x": 0.0, "y": transform_y},
            "flip": {"vertical": False, "horizontal": False},
            "alpha": 1.0,
        },
        "uniform_scale": {"on": True, "value": 1.11},
        "extra_material_refs": [],
        "render_index": render_index,
        "keyframe_refs": [],
        "enable_lut": False,
        "enable_adjust": False,
        "enable_hsl": False,
        "visible": True,
        "group_id": "",
        "enable_color_curves": True,
        "enable_hsl_curves": True,
        "track_render_index": 1,
        "hdr_settings": None,
        "enable_color_wheels": True,
        "track_attribute": 0,
        "is_placeholder": False,
        "template_id": "",
        "enable_smart_color_adjust": False,
        "template_scene": "default",
        "common_keyframes": [],
        "caption_info": None,
        "responsive_layout": {
            "enable": False, "target_follow": "",
            "size_layout": 0, "horizontal_pos_layout": 0, "vertical_pos_layout": 0
        },
        "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False,
        "enable_adjust_mask": False,
        "raw_segment_id": "",
        "lyric_keyframes": None,
        "enable_video_mask": True,
        "digital_human_template_group_id": "",
        "color_correct_alg_result": "",
        "source": "segmentsourcenormal",
        "enable_mask_stroke": False,
        "enable_mask_shadow": False,
        "enable_color_adjust_pro": False,
    }


def get_video_resolution(video_path: Path) -> tuple[int, int]:
    """원본 영상의 실제 해상도 (실패 시 1920x1080)"""
    try:
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height", "-of", "json", str(video_path)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        stream = json.loads(r.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except Exception:
        return 1920, 1080


# 캔버스 비율 프리셋: (width, height, 자막 y 위치)
CANVAS_PRESETS = {
    "9:16":  {"width": 1080, "height": 1920, "subtitle_y": -0.35},   # 숏츠 (기본)
    "16:9":  {"width": 1920, "height": 1080, "subtitle_y": -0.6907},  # 가로
}

# ══════════════════════════════════════════════════════════
# 템플릿(0731 프로젝트)에서 추출한 상단 제목 / 하단 날짜 스타일
# ══════════════════════════════════════════════════════════

TEMPLATE_FONT_PATH = "C:/Users/Lusey/AppData/Local/Microsoft/Windows/Fonts/GmarketSansTTFBold.ttf"

# 제목: 상단, 1줄 빨강 + 나머지 흰색, 그림자 있음
TITLE_STYLE = {
    "font_size": 15.0, "size": 15, "y": 0.614609283208847, "scale": 1.023456061119955,
    "line_spacing": 0.12, "check_flag": 39, "render_index": 14001, "track_render_index": 7,
}
# 날짜: 하단, 노란색 + 검정 외곽선
DATE_STYLE = {
    "font_size": 15.0, "size": 15, "y": -0.7106237025002258, "scale": 0.829706636769777,
    "line_spacing": 0.02, "check_flag": 15, "render_index": 14000, "track_render_index": 6,
    "color": [0.941176474094391, 1, 0], "hex": "#f0ff00", "stroke_width": 0.0599999986588955,
}

TITLE_RED = [1, 0, 0]
TITLE_WHITE = [1, 1, 1]

# 랜덤 효과로 사용할 등장 애니메이션 (사용자 즐겨찾기 목록)
# ※ '스크롤', '겹', '흔들림 플래시', '락 세로'는 템플릿에 사용 이력이 없어
#    resource_id를 알 수 없다. 해당 효과를 쓴 프로젝트를 주면 여기에 추가 가능.
# 템플릿에서 통째로 뽑아온 값 (path = 캡컷이 받아둔 효과 캐시 위치).
# path와 third_resource_id가 비어 있으면 캡컷이 효과를 못 찾아 그냥 넘어가는 경우가
# 있어서(= 효과가 간혹 안 들어감), 템플릿에 있던 값을 그대로 넣는다.
_EFF_CACHE = "C:/Users/Lusey/AppData/Local/CapCut/User Data/Cache/effect"
TEMPLATE_IN_ANIMATIONS = [
    {"name": "FF 안", "resource_id": "7211044701367964162",
     "third_resource_id": "7211044701367964162",
     "path": f"{_EFF_CACHE}/7211044701367964162/6a680c49cd11a05f3eb0e5a3fed165f7"},
    {"name": "스워시", "resource_id": "7274915008939561473",
     "third_resource_id": "7274915008939561473",
     "path": f"{_EFF_CACHE}/7274915008939561473/ecaffca7c7e1d7744fa296a29f65b366"},
    {"name": "패들링", "resource_id": "7227021042017899010",
     "third_resource_id": "7227021042017899010",
     "path": f"{_EFF_CACHE}/7227021042017899010/57a259c58a4daddacc897c75ec9c10a4"},
    {"name": "충돌", "resource_id": "7216282356447973890",
     "third_resource_id": "7216282356447973890",
     "path": f"{_EFF_CACHE}/7216282356447973890/8a2213baaae818dc449d13528ec9ec76"},
    {"name": "X 진동", "resource_id": "7223670693685105154",
     "third_resource_id": "7223670693685105154",
     "path": f"{_EFF_CACHE}/7223670693685105154/cde910202607be12ac747e2e76316e7f"},
    {"name": "펄스 줌", "resource_id": "7530463994486820097",
     "third_resource_id": "0",
     "path": f"{_EFF_CACHE}/7530463994486820097/c2223de4486ee5b2a5900d707e9a362b"},
    {"name": "퀀텀 셰이크", "resource_id": "7626732691261607189",
     "third_resource_id": "0",
     "path": f"{_EFF_CACHE}/7626732691261607189/364995ccf6a19b3fafa4183ef62a130c"},
    {"name": "락 3", "resource_id": "6781683302672634382",
     "third_resource_id": "6781683302672634382",
     "path": f"{_EFF_CACHE}/6781683302672634382/e2799421fc7fc57796222bd27966c812"},
]


def _split_title_lines(title_text: str) -> list[str]:
    """제목을 최대 3줄로 정리 (줄바꿈이 없으면 길이에 맞춰 자동 분할)."""
    lines = [ln.strip() for ln in title_text.replace("\r", "").split("\n") if ln.strip()]
    if len(lines) > 1:
        return lines[:3]
    words = title_text.split()
    if len(words) <= 1:
        return [title_text.strip()]
    n = min(3, max(2, math.ceil(len("".join(words)) / 10)))
    per = math.ceil(len(words) / n)
    return [" ".join(words[i:i + per]) for i in range(0, len(words), per)][:3]


def _make_title_material(mat_id: str, title_text: str) -> dict:
    """
    상단 제목 텍스트 (템플릿 스타일 그대로).
    첫 줄은 빨강, 나머지 줄은 흰색.
    """
    lines = _split_title_lines(title_text)
    full = "\n".join(lines)

    shadow = [{
        "thickness_projection_angle": -45, "thickness_projection_enable": False,
        "diffuse": 0.04281949996948242, "alpha": 0.899999976158142,
        "distance": 4.999999523162842,
        "content": {"render_type": "solid", "solid": {"color": [0, 0, 0]}},
        "angle": -45, "thickness_projection_distance": 0,
    }]

    styles, pos = [], 0
    for i, ln in enumerate(lines):
        end = pos + len(ln) + (1 if i < len(lines) - 1 else 0)   # 개행 포함
        styles.append({
            "fill": {"content": {"render_type": "solid",
                                 "solid": {"color": TITLE_RED if i == 0 else TITLE_WHITE}}},
            "font": {"path": TEMPLATE_FONT_PATH, "id": ""},
            "size": TITLE_STYLE["size"], "shadows": shadow, "bold": True,
            "useLetterColor": True, "range": [pos, end],
        })
        pos = end

    content = {"styles": styles, "text": full}
    mat = _make_text_material(mat_id, full)
    mat.update({
        "content": json.dumps(content, ensure_ascii=False),
        "font_size": TITLE_STYLE["font_size"], "text_size": 30,
        "text_color": "#ffffff",
        "border_color": "#000000", "border_width": 0.08, "border_alpha": 1.0,
        "background_color": "#000000", "background_alpha": 1.0, "background_style": 0,
        "background_round_radius": 0.0, "background_height": 0.14, "background_width": 0.14,
        "line_spacing": TITLE_STYLE["line_spacing"], "alignment": 1, "line_max_width": 0.82,
        "font_path": TEMPLATE_FONT_PATH, "font_name": "", "font_title": "none",
        "has_shadow": True, "shadow_color": "#000000",
        "shadow_alpha": 0.8999999761581421, "shadow_angle": -45.0,
        "shadow_distance": 5.0, "shadow_smoothing": 0.7707509994506836,
        "check_flag": TITLE_STYLE["check_flag"], "add_type": 0,
    })
    return mat


def _make_date_material(mat_id: str, date_text: str) -> dict:
    """하단 날짜 텍스트 (노란색 + 검정 외곽선, 템플릿 스타일 그대로)."""
    content = {
        "styles": [{
            "fill": {"content": {"render_type": "solid", "solid": {"color": DATE_STYLE["color"]}}},
            "font": {"path": TEMPLATE_FONT_PATH, "id": ""},
            "strokes": [{"content": {"render_type": "solid", "solid": {"color": [0, 0, 0]}},
                         "width": DATE_STYLE["stroke_width"], "mode": 0}],
            "size": DATE_STYLE["size"], "useLetterColor": True,
            "range": [0, len(date_text)],
        }],
        "text": date_text,
    }
    mat = _make_text_material(mat_id, date_text)
    mat.update({
        "content": json.dumps(content, ensure_ascii=False),
        "font_size": DATE_STYLE["font_size"], "text_size": 30,
        "text_color": DATE_STYLE["hex"],
        "border_color": "#000000", "border_width": 0.08, "border_alpha": 1.0,
        "background_color": "#000000", "background_alpha": 1.0, "background_style": 0,
        "background_round_radius": 0.0, "background_height": 0.14, "background_width": 0.14,
        "line_spacing": DATE_STYLE["line_spacing"], "alignment": 1, "line_max_width": 0.82,
        "font_path": TEMPLATE_FONT_PATH, "font_name": "", "font_title": "none",
        "has_shadow": False,
        "check_flag": DATE_STYLE["check_flag"], "add_type": 0,
    })
    return mat


def _make_overlay_text_segment(seg_id: str, mat_id: str, start_us: int, end_us: int,
                               style: dict, anim_ref_id: str) -> dict:
    """제목/날짜용 텍스트 세그먼트 (템플릿의 위치·크기·렌더순서 그대로)."""
    seg = _make_text_segment(seg_id, mat_id, start_us, end_us,
                             transform_y=style["y"], render_index=style["render_index"])
    seg["clip"]["scale"] = {"x": style["scale"], "y": style["scale"]}
    seg["uniform_scale"] = {"on": True, "value": 1.0}
    seg["track_render_index"] = style["track_render_index"]
    seg["extra_material_refs"] = [anim_ref_id]
    return seg


def _make_sticker_animation(anim_id: str) -> dict:
    """제목/날짜 텍스트가 참조하는 빈 애니메이션 재료 (템플릿과 동일 구조)."""
    return {"id": anim_id, "type": "sticker_animation", "animations": [],
            "multi_language_current": "none"}


def _make_in_animation(anim_id: str, effect: dict, duration_us: int = 500_000) -> dict:
    """
    영상/이미지 클립에 넣을 등장 애니메이션 재료.
    템플릿(손편집본)에 들어있던 항목과 필드를 똑같이 맞춘다 — path/third_resource_id/
    source_platform/category(in_fav)가 빠지면 캡컷이 효과를 건너뛰는 경우가 있다.
    """
    return {
        "id": anim_id, "type": "sticker_animation", "multi_language_current": "none",
        "animations": [{
            "id": effect["resource_id"], "name": effect["name"],
            "type": "in", "category_id": "in_fav", "category_name": "in_fav",
            "resource_id": effect["resource_id"],
            "third_resource_id": effect.get("third_resource_id", "0"),
            "source_platform": 1,
            "path": effect.get("path", ""),
            "start": 0, "duration": duration_us,
            "anim_adjust_params": None, "platform": "all",
            "panel": "video", "material_type": "video",
            "request_id": "",
        }],
    }


# ── 자료화면 배치 기본값 ──
# scale은 템플릿 사진 29개의 중간값. 위치는 캡컷 화면에 표시되는 픽셀값으로 지정하며
# JSON에는 정규화 좌표로 변환해 넣는다 (정규화 = 픽셀 / (캔버스높이/2), 위쪽이 +).
# 이미지는 지금까지 쓰던 값 그대로, 영상은 조금 더 크게·아래로
MEDIA_PLACE       = {"scale": 0.790416, "x_px": 0.0, "y_px": -892.0}    # 이미지
MEDIA_PLACE_VIDEO = {"scale": 0.87,     "x_px": 0.0, "y_px": -1022.0}   # 영상


def _place_for(is_img: bool, canvas: dict) -> dict:
    """자료화면 배치값 (이미지/영상이 다르다) → 세그먼트에 넣을 형태로."""
    p = MEDIA_PLACE if is_img else MEDIA_PLACE_VIDEO
    nx, ny = _px_to_norm(p["x_px"], p["y_px"], canvas)
    return {"scale": p["scale"], "x": nx, "y": ny}


def assign_random_effects(segments: list[dict], effect_dur_sec: float,
                          seed: str = "") -> list[dict]:
    """
    자료화면 클립 '전부'에 랜덤 등장 효과를 넣는다 (몇 초마다가 아니라 하나하나 전부).
    - 바로 앞 클립과 같은 효과는 피한다
    - 클립보다 긴 효과는 클립 길이에 맞춘다
    - 같은 프로젝트 이름이면 항상 같은 결과 (다시 돌려도 안 바뀜)
    반환: 만들어진 애니메이션 재료 목록 (materials.material_animations 에 넣을 것)
    """
    if effect_dur_sec <= 0 or not segments:
        return []
    import random
    rnd = random.Random(seed)
    eff_us = sec_to_us(effect_dur_sec)
    out, last_pick = [], None
    for seg in segments:
        choices = [x for x in TEMPLATE_IN_ANIMATIONS if x["name"] != last_pick]
        eff = rnd.choice(choices or TEMPLATE_IN_ANIMATIONS)
        last_pick = eff["name"]
        anim_id = str(uuid.uuid4()).upper()
        dur = max(100_000, min(eff_us, seg["target_timerange"]["duration"]))
        out.append(_make_in_animation(anim_id, eff, dur))
        seg.setdefault("extra_material_refs", []).append(anim_id)
    return out


def _px_to_norm(x_px: float, y_px: float, canvas: dict) -> tuple[float, float]:
    """
    캡컷 위치(픽셀) → draft JSON 정규화 좌표.

    ★ 캡컷은 '화면 전체 크기'를 1.0 으로 잡는다 (화면 절반이 아니라).
      즉 1080x1920 에서 화면 안쪽이 -0.5 ~ +0.5 이고, y=-892px → -892/1920 = -0.4646.
      템플릿(손편집본)의 자료화면 y 중앙값이 -0.4775(= -917px)로 여기에 딱 맞는다.
      예전에는 절반(960)으로 나눠서 두 배로 내려가 화면 밖으로 나갔었다.
    """
    w = max(canvas.get("width", 1080), 1)
    h = max(canvas.get("height", 1920), 1)
    return x_px / w, y_px / h

# 배경제거 후 적용하는 '발광' 흰색 획 (템플릿에서 추출)
MATTING_STROKE_RESOURCE_ID = "7172498336719573505"
MATTING_STROKE_PATH = ("C:/Users/Lusey/AppData/Local/CapCut/User Data/Cache/effect/"
                       "7172498336719573505/84834161574d7f941e9120f2d8e78006")


def _make_matting(remove_bg: bool, stroke: bool, stroke_size: float = 0.15,
                  stroke_alpha: float = 0.6) -> dict:
    """
    캡컷 자체 배경제거(matting) 설정.

    ※ 지금은 쓰지 않는다. 캡컷의 배경제거는 캡컷이 직접 계산해 저장한 마스크 파일
      (matting/<해시>)이 있어야 화면이 나오는데, 프로그램이 만든 draft에는 그 파일이
      없어서 켜두면 검은 화면이 된다(캡컷에서 껐다 켜야 다시 계산됨).
      그래서 이미지는 make_cutout_png()로 우리가 직접 배경을 지워 넣는다.
      구조 참고용으로만 남겨둔다.

    flag: 0=없음, 1=배경제거만, 3=배경제거+획(발광)
    """
    mt = {
        "flag": 0, "path": "", "interactiveTime": [],
        "has_use_quick_brush": False, "strokes": [], "has_use_quick_eraser": False,
        "expansion": 0, "feather": 0, "reverse": False,
        "custom_matting_id": "", "enable_matting_stroke": False,
        "is_clould": False, "mask_video_path": "", "cloud_product_fps": 0.0,
    }
    if not remove_bg:
        return mt
    mt["flag"] = 3 if stroke else 1
    mt["custom_matting_id"] = str(uuid.uuid4()).upper()
    if stroke:
        mt["enable_matting_stroke"] = True
        mt["strokes"] = [{
            "resource_id": MATTING_STROKE_RESOURCE_ID,
            "third_resource_id": MATTING_STROKE_RESOURCE_ID,
            "source_platform": 1, "resource_name": "발광",
            "path": MATTING_STROKE_PATH,
            "color": [1.0, 1.0, 1.0, 1.0],          # 흰색
            "adjust_params": [
                {"name": "effects_adjust_size", "value": stroke_size,
                 "default_value": 0.3011000156402588},
                {"name": "effects_adjust_alpha", "value": stroke_alpha,
                 "default_value": 0.6},
            ],
        }]
    return mt


# 이미지로 취급할 확장자 (나머지는 영상으로 간주)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".tif", ".tiff"}
IMAGE_SOURCE_DUR_US = 10_800_000_000   # 이미지 material의 소스 길이 (3시간, CapCut 관례)
DEFAULT_IMAGE_DUR_SEC = 2.0            # 이미지 1장 타임라인 기본 노출 시간


def get_media_info(path: Path) -> tuple[int, int, float | None, bool]:
    """(width, height, duration_sec|None, is_image). 이미지는 duration None."""
    is_image = path.suffix.lower() in IMAGE_EXTS
    w, h = get_video_resolution(path)
    if is_image:
        return w, h, None, True
    try:
        dur = get_video_duration(path)
    except Exception:
        dur = None
    return w, h, dur, False


def _media_material_dict(material_id: str, path: Path, width: int, height: int,
                         source_dur_us: int, is_image: bool) -> dict:
    """영상/이미지 material dict (CapCut 8.7.0). 기존 비디오 material 구조와 동일, 타입만 분기."""
    p = str(path).replace("\\", "/")
    return {
        "id": material_id, "type": "photo" if is_image else "video",
        "path": p, "media_path": p,
        "duration": source_dur_us, "width": width, "height": height,
        "material_name": path.name,
        "category_id": "", "category_name": "local",
        "has_audio": (not is_image), "source": "none", "source_platform": 0,
        "is_ai_generate_content": False, "is_copyright": False,
        "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
        "local_id": "", "local_material_id": "", "origin_material_id": "",
        "request_id": "", "team_id": "", "text_task_id": "",
        "audio_fade": None, "cartoon_path": "", "check_flag": 63,
        "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0,
                 "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "extra_type_option": 0, "formula_id": "", "freeze": None,
        "intensifies_audio_path": "", "material_id": material_id, "material_url": p,
        "matting": {"flag": 0, "has_use_quick_brush": False, "has_use_quick_eraser": False,
                    "interactiveTime": [], "path": "", "strokes": []},
        "object_locked": None,
        "picture_from": "none", "picture_set_category_id": "", "picture_set_category_name": "",
        "reverse_intensifies_path": "", "reverse_path": "", "smart_motion": None,
        "stable": {"matrix_path": "", "stable_level": 0, "time_range": {"duration": 0, "start": 0}},
        "text_camera_move": [],
        "video_algorithm": {"algorithms": [], "deflicker": None, "motion_blur_config": None,
                            "noise_reduction": None, "path": "", "time_range": None}
    }


def _media_segment_dict(seg_id: str, material_id: str, src_start_us: int, dur_us: int,
                        tl_start_us: int, is_image: bool,
                        place: dict | None = None) -> dict:
    """메인 트랙 영상/이미지 세그먼트 dict. place가 있으면 크기·위치를 통일한다."""
    sc = place["scale"] if place else 1.0
    px = place["x"] if place else 0.0
    py = place["y"] if place else 0.0
    return {
        "id": seg_id, "material_id": material_id,
        "source_timerange": {"start": src_start_us, "duration": dur_us},
        "target_timerange": {"start": tl_start_us, "duration": dur_us},
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                 "rotation": 0.0, "scale": {"x": sc, "y": sc}, "transform": {"x": px, "y": py}},
        "cartoon": False, "enable_adjust": True, "enable_color_correct_adjust": False,
        "enable_color_curves": True, "enable_lut": True, "enable_smart_color_adjust": False,
        "extra_material_refs": [], "is_placeholder": False, "is_tone_modify": False,
        "last_nonzero_volume": 1.0, "render_index": 0, "reverse": False, "speed": 1.0,
        "template_id": "", "template_scene": "default", "track_attribute": 0, "track_render_index": 0,
        "type": "photo" if is_image else "video",
        "uniform_scale": {"on": True, "value": sc}, "visible": True,
        "volume": 0.0 if is_image else 1.0, "hdr_settings": None,
        "intensifies_audio": False, "loop": False,
    }


def _finalize_draft(output_dir: Path, draft_name: str, ratio: str, canvas: dict,
                    videos_materials: list, text_materials: list, tracks: list,
                    final_duration: int, source_paths: list[Path],
                    timeline_id: str, project_id: str, draft_id: str, ts: int,
                    anim_materials: list | None = None) -> Path:
    """draft_content/project/meta 조립 + 폴더 저장 (컷편집·시퀀스 공용)."""
    platform = {"os": "windows", "os_version": "10.0.26200", "app_id": 359289,
                "app_version": "8.7.0", "app_source": "cc",
                "device_id": "", "hard_disk_id": "", "mac_address": ""}

    draft_content = {
        "id": timeline_id, "version": 360000, "new_version": "171.0.0", "name": "",
        "duration": final_duration, "create_time": 0, "update_time": 0,
        "fps": 30.0, "is_drop_frame_timecode": False, "color_space": -1,
        "config": {
            "video_mute": False, "record_audio_last_index": 1,
            "extract_audio_last_index": 1, "original_sound_last_index": 1,
            "subtitle_recognition_id": "", "subtitle_taskinfo": [],
            "lyrics_recognition_id": "", "lyrics_taskinfo": [],
            "subtitle_sync": True, "lyrics_sync": True, "voice_change_sync": False,
            "sticker_max_index": 1, "adjust_max_index": 1, "material_save_mode": 0,
            "export_range": None, "maintrack_adsorb": True, "combination_max_index": 1,
            "attachment_info": [], "zoom_info_params": None, "system_font_list": [],
            "multi_language_mode": "none", "multi_language_main": "none",
            "multi_language_current": "none", "multi_language_list": [],
            "subtitle_keywords_config": None, "use_float_render": False},
        "canvas_config": {"ratio": ratio, "width": canvas["width"], "height": canvas["height"], "background": None},
        "tracks": tracks, "group_container": None,
        "materials": {
            "flowers": [], "videos": videos_materials, "texts": text_materials,
            "tail_leaders": [], "audios": [], "images": [],
            "effects": [], "stickers": [], "canvases": [], "transitions": [],
            "audio_effects": [], "audio_fades": [], "beats": [],
            "material_animations": anim_materials or [],
            "placeholders": [], "placeholder_infos": [],
            "speeds": [], "common_mask": [], "chromas": [], "text_templates": [],
            "realtime_denoises": [], "audio_pannings": [], "audio_pitch_shifts": [],
            "video_trackings": [], "hsl": [], "drafts": [], "color_curves": [],
            "hsl_curves": [], "primary_color_wheels": [], "log_color_wheels": [],
            "video_effects": [], "audio_balances": [], "handwrites": [],
            "manual_deformations": [], "manual_beautys": [], "plugin_effects": [],
            "sound_channel_mappings": [], "green_screens": [], "shapes": [],
            "material_colors": [], "digital_humans": [], "digital_human_model_dressing": [],
            "smart_crops": [], "ai_translates": [], "audio_track_indexes": [],
            "loudnesses": [], "vocal_beautifys": [], "vocal_separations": [],
            "smart_relights": [], "time_marks": [], "multi_language_refs": [],
            "video_shadows": [], "video_strokes": [], "video_radius": []},
        "keyframes": {"videos": [], "audios": [], "texts": [], "stickers": [],
                      "filters": [], "adjusts": [], "handwrites": [], "effects": []},
        "keyframe_graph_list": [], "platform": platform, "last_modified_platform": platform,
        "mutable_config": None, "cover": None, "retouch_cover": None,
        "extra_info": None, "relationships": [],
        "render_index_track_mode_on": True, "free_render_index_mode_on": False,
        "static_cover_image_path": "", "source": "default", "time_marks": None,
        "path": "", "lyrics_effects": [],
        "uneven_animation_template_info": {"composition": "", "content": "", "order": "", "sub_template_info_list": []},
        "draft_type": "video", "smart_ads_info": {"page_from": "", "routine": "", "draft_url": ""},
        "function_assistant_info": {
            "smart_rec_applied": False, "fixed_rec_applied": False, "auto_adjust": False,
            "auto_adjust_segid_list": [], "color_correction": False, "color_correction_segid_list": [],
            "enhance_quality": False, "smooth_slow_motion": False, "deflicker_segid_list": [],
            "video_noise_segid_list": [], "enhance_quality_segid_list": [], "smart_segid_list": [],
            "retouch": False, "retouch_segid_list": [], "enhande_voice": False,
            "enhance_voice_segid_list": [], "audio_noise_segid_list": [], "auto_caption": False,
            "auto_caption_segid_list": [], "auto_caption_template_id": "", "caption_opt": False,
            "caption_opt_segid_list": [], "eye_correction": False, "eye_correction_segid_list": [],
            "normalize_loudness": False, "normalize_loudness_segid_list": [],
            "normalize_loudness_audio_denoise_segid_list": [], "auto_adjust_fixed": False,
            "auto_adjust_fixed_value": 50.0, "color_correction_fixed": False,
            "color_correction_fixed_value": 50.0, "normalize_loudness_fixed": False,
            "enhande_voice_fixed": False, "retouch_fixed": False, "enhance_quality_fixed": False,
            "smooth_slow_motion_fixed": False, "fps": {"num": 0, "den": 1}}}

    project_json = {
        "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": ts, "id": project_id, "main_timeline_id": timeline_id,
        "timelines": [{"create_time": ts, "id": timeline_id, "is_marked_delete": False, "name": "타임라인 01", "update_time": ts}],
        "update_time": ts, "version": 0}

    capcut_root = Path(os.environ.get("USERPROFILE", "")) / \
        "AppData" / "Local" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    draft_folder_path = capcut_root / draft_name
    src_values = [str(p).replace("\\", "/") for p in source_paths]

    draft_meta_info = {
        "cloud_draft_cover": False, "cloud_draft_sync": False, "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "", "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "", "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg", "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "draft_enterprise_name": "", "enterprise_material": []},
        "draft_fold_path": str(draft_folder_path).replace("\\", "/"), "draft_id": draft_id,
        "draft_is_ae_produce": False, "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False, "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False, "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false", "draft_is_invisible": False,
        "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": src_values},
            {"type": 1, "value": []}, {"type": 2, "value": []}, {"type": 3, "value": []},
            {"type": 6, "value": []}, {"type": 7, "value": []}, {"type": 8, "value": []}],
        "draft_materials_copied_info": [], "draft_name": draft_name,
        "draft_need_rename_folder": False, "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(capcut_root).replace("/", "\\"),
        "draft_segment_extra_info": [], "draft_timeline_materials_size_": 0,
        "draft_type": "", "draft_web_article_video_enter_from": "",
        "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
        "tm_draft_create": ts, "tm_draft_modified": ts,
        "tm_draft_removed": 0, "tm_duration": final_duration}

    draft_dir = output_dir / draft_name
    if draft_dir.exists():
        shutil.rmtree(draft_dir)
    draft_dir.mkdir(parents=True)

    def write(name, data):
        (draft_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    write("draft_content.json", draft_content)
    write("draft_meta_info.json", draft_meta_info)
    write("project.json", project_json)
    write("attachment_pc_common.json", {
        "ai_packaging_infos": [], "ai_packaging_report_info": {"caption_id_list": [], "commercial_material": "", "material_source": "", "method": "", "page_from": "", "style": "", "task_id": "", "text_style": "", "tos_id": "", "video_category": ""},
        "broll": {"ai_packaging_infos": [], "ai_packaging_report_info": {"caption_id_list": [], "commercial_material": "", "material_source": "", "method": "", "page_from": "", "style": "", "task_id": "", "text_style": "", "tos_id": "", "video_category": ""}},
        "commercial_music_category_ids": [], "pc_feature_flag": 0, "recognize_tasks": [],
        "reference_lines_config": {"horizontal_lines": [], "is_lock": False, "is_visible": False, "vertical_lines": []},
        "safe_area_type": 0, "template_item_infos": [], "unlock_template_ids": []})
    write("attachment_pc_timeline.json", {"reference_lines_config": {"horizontal_lines": [], "is_lock": False, "is_visible": False, "vertical_lines": []}, "safe_area_type": 0})
    write("timeline_layout.json", {"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [timeline_id], "timelineNames": ["타임라인 01"]}], "layoutOrientation": 1})
    write("performance_opt_info.json", {"manual_cancle_precombine_segs": None, "need_auto_precombine_segs": None})
    write("draft_agency_config.json", {"is_auto_agency_enabled": False, "is_auto_agency_popup": False, "is_single_agency_mode": False, "marterials": None, "use_converter": True, "video_resolution": 720})
    write("draft_biz_config.json", {})
    (draft_dir / "draft_cover.jpg").write_bytes(b"")
    return draft_dir


def build_draft(
    video_path: Path,
    silences: list[dict],
    total_duration_sec: float,
    output_dir: Path,
    draft_name: str = "CapCut_Agent_Draft",
    subtitles: list[dict] | None = None,
    ratio: str = "9:16",
    max_sub_chars: int = MAX_SUBTITLE_CHARS,
    script_text: str = "",
    append_files: list[Path] | None = None,
    image_dur_sec: float = DEFAULT_IMAGE_DUR_SEC,
    title_text: str = "",
    date_text: str = "",
    effect_dur_sec: float = 0.0,
    bg_files: set[str] | None = None,
    bg_stroke: bool = True,
    stroke_size: float = 0.15,
    stroke_alpha: float = 0.6,
    unify_place: bool = True,
    head_trim: float = 0.0,
    tail_trim: float = 0.0,
    min_clip_sec: float = 0.0,
    drop_clips: set[int] | None = None,
    cutouts: dict[str, str] | None = None,
) -> Path:
    """
    subtitles:     Whisper 원본 인식 결과 (원본 영상 타임스탬프 + 단어별 시각 기준).
                   발화가 있는 모든 영상 클립 위에 자막 클립을 텍스트 트랙으로 삽입한다.
    ratio:         "9:16" (숏츠 1080x1920, 기본) 또는 "16:9" (가로 1920x1080)
    max_sub_chars: 자막 한 조각 최대 글자 수 (±3자 허용)
    script_text:   대본. 있으면 정답 텍스트로 정렬해 오탈자 교정 + 반복 제거
    append_files:  컷편집 영상 뒤에 순서대로 이어붙일 영상/이미지 파일들 (통합 모드)
    image_dur_sec: 이어붙일 이미지 1장의 노출 시간(초)
    title_text:    상단 제목 (첫 줄 빨강 + 나머지 흰색, 템플릿 스타일)
    date_text:     하단 날짜 (노란색, 보통 오늘 날짜 MM-DD)
    effect_dur_sec: 0보다 크면 모든 자료화면에 랜덤 등장 효과를 이 길이(초)로 부여
                   (메인 컷편집 영상에는 적용하지 않음)
    bg_files:      배경제거를 적용할 파일 경로 집합 (선택한 것만)
    bg_stroke:     배경제거 후 흰색 발광 획 적용 여부
    stroke_size:   획 굵기 (0~1)
    unify_place:   이어붙이는 자료화면의 크기·위치를 MEDIA_PLACE로 통일
    """
    bg_files = bg_files or set()
    canvas = CANVAS_PRESETS.get(ratio, CANVAS_PRESETS["9:16"])
    src_width, src_height = get_video_resolution(video_path)

    video_str   = str(video_path).replace("\\", "/")
    material_id = str(uuid.uuid4()).upper()
    timeline_id = str(uuid.uuid4()).upper()
    project_id  = str(uuid.uuid4()).upper()
    draft_id    = str(uuid.uuid4()).upper()
    track_id    = str(uuid.uuid4()).upper()
    total_us    = sec_to_us(total_duration_sec)
    ts          = now_us()

    # ── keep 구간 (자막 리매핑과 동일한 함수 공유 → 타이밍 완전 일치) ──
    keep_ranges = compute_keep_ranges(silences, total_duration_sec,
                                      head_trim, tail_trim, min_clip_sec, drop_clips)

    # ── 비디오 세그먼트 ──────────────────────────────────
    segments = []
    timeline_cursor_us = 0
    for ks, ke, tl_s, tl_e in keep_ranges:
        src_start = sec_to_us(snap_to_frame(ks))
        src_end   = sec_to_us(snap_to_frame(ke))
        dur = src_end - src_start
        segments.append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": material_id,
            "source_timerange": {"start": src_start, "duration": dur},
            "target_timerange": {"start": timeline_cursor_us, "duration": dur},
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
            "cartoon": False,
            "enable_adjust": True,
            "enable_color_correct_adjust": False,
            "enable_color_curves": True,
            "enable_lut": True,
            "enable_smart_color_adjust": False,
            "extra_material_refs": [],
            "is_placeholder": False,
            "is_tone_modify": False,
            "last_nonzero_volume": 1.0,
            "render_index": 0,
            "reverse": False,
            "speed": 1.0,
            "template_id": "",
            "template_scene": "default",
            "track_attribute": 0,
            "track_render_index": 0,
            "type": "video",
            "uniform_scale": {"on": True, "value": 1.0},
            "visible": True,
            "volume": 1.0,
            "hdr_settings": None,
            "intensifies_audio": False,
            "loop": False,
        })
        timeline_cursor_us += dur

    # ── 이어붙일 파일들(통합 모드): 컷편집 영상 뒤에 순서대로 배치 ──
    extra_materials = []
    appended_segments = []          # 효과는 이 클립들에만 적용
    cutouts = cutouts or {}
    for f in (append_files or []):
        mat_id = str(uuid.uuid4()).upper()
        # 배경제거를 고른 이미지는 미리 만들어 둔 투명 PNG로 바꿔 넣는다
        # (캡컷 배경제거 버튼을 안 눌러도 처음부터 제대로 보이게)
        src_file = Path(cutouts[str(f)]) if str(f) in cutouts else f
        w, h, dsec, is_img = get_media_info(src_file)
        if is_img:
            src_dur_us = IMAGE_SOURCE_DUR_US
            seg_dur = sec_to_us(image_dur_sec)
        else:
            if not dsec:
                continue
            src_dur_us = sec_to_us(dsec)
            seg_dur = src_dur_us
        if seg_dur < 100_000:
            continue
        mat = _media_material_dict(mat_id, src_file, w, h, src_dur_us, is_img)
        extra_materials.append(mat)
        seg = _media_segment_dict(
            str(uuid.uuid4()).upper(), mat_id, 0, seg_dur, timeline_cursor_us, is_img,
            place=_place_for(is_img, canvas) if unify_place else None)
        segments.append(seg)
        appended_segments.append(seg)
        timeline_cursor_us += seg_dur

    final_duration = timeline_cursor_us

    # ── 랜덤 등장 효과 ───────────────────────────────────
    # 메인(컷편집) 영상에는 넣지 않고, 뒤에 추가한 자료화면(영상·이미지)에만 적용.
    # 자료화면 하나하나에 전부 효과를 넣되, 서로 다른 효과가 나오도록 무작위로 고른다.
    anim_materials = assign_random_effects(appended_segments, effect_dur_sec, draft_name)

    # ── 자막 텍스트 트랙 ─────────────────────────────────
    # 단어별 발화 시각 기준으로 각 영상 클립에 자막을 배분 →
    # 발화가 있는 모든 클립 위에 자막 클립이 올라간다.
    # max_sub_chars(±3) 초과 텍스트는 같은 클립 위에서 여러 자막 클립으로 균형 분할.
    text_materials = []
    text_segments = []
    if subtitles:
        sub_chunks = subtitle_chunks_for_timeline(subtitles, keep_ranges, max_sub_chars, script_text)
        for idx, chunk in enumerate(sub_chunks):
            text_mat_id = str(uuid.uuid4()).upper()
            text_seg_id = str(uuid.uuid4()).upper()
            text_materials.append(_make_text_material(text_mat_id, chunk["text"]))
            text_segments.append(_make_text_segment(
                text_seg_id, text_mat_id, chunk["start_us"], chunk["end_us"],
                transform_y=canvas["subtitle_y"],
                render_index=15000 + idx,
            ))

    # ── 상단 제목 / 하단 날짜 (템플릿 스타일, 영상 전체 길이) ──
    overlay_tracks = []
    for txt, style, make_mat in (
        (date_text.strip(),  DATE_STYLE,  _make_date_material),
        (title_text.strip(), TITLE_STYLE, _make_title_material),
    ):
        if not txt or final_duration <= 0:
            continue
        mat_id = str(uuid.uuid4()).upper()
        anim_id = str(uuid.uuid4()).upper()
        text_materials.append(make_mat(mat_id, txt))
        anim_materials.append(_make_sticker_animation(anim_id))
        overlay_tracks.append({
            "attribute": 0, "flag": 0, "id": str(uuid.uuid4()).upper(),
            "is_default_name": True, "name": "", "type": "text",
            "segments": [_make_overlay_text_segment(
                str(uuid.uuid4()).upper(), mat_id, 0, final_duration, style, anim_id)],
        })

    # ── tracks 구성 ──────────────────────────────────────
    tracks = [
        {"attribute": 0, "flag": 0, "id": track_id,
         "is_default_name": True, "name": "", "segments": segments, "type": "video"}
    ]
    if text_segments:
        tracks.append({
            "attribute": 0, "flag": 0, "id": str(uuid.uuid4()).upper(),
            "is_default_name": True, "name": "", "segments": text_segments, "type": "text"
        })
    tracks.extend(overlay_tracks)

    # ── platform ─────────────────────────────────────────
    platform = {
        "os": "windows", "os_version": "10.0.26200",
        "app_id": 359289, "app_version": "8.7.0", "app_source": "cc",
        "device_id": "", "hard_disk_id": "", "mac_address": ""
    }

    # ── draft_content.json ───────────────────────────────
    draft_content = {
        "id": timeline_id,
        "version": 360000,
        "new_version": "171.0.0",
        "name": "",
        "duration": final_duration,
        "create_time": 0,
        "update_time": 0,
        "fps": 30.0,
        "is_drop_frame_timecode": False,
        "color_space": -1,
        "config": {
            "video_mute": False, "record_audio_last_index": 1,
            "extract_audio_last_index": 1, "original_sound_last_index": 1,
            "subtitle_recognition_id": "", "subtitle_taskinfo": [],
            "lyrics_recognition_id": "", "lyrics_taskinfo": [],
            "subtitle_sync": True, "lyrics_sync": True, "voice_change_sync": False,
            "sticker_max_index": 1, "adjust_max_index": 1, "material_save_mode": 0,
            "export_range": None, "maintrack_adsorb": True, "combination_max_index": 1,
            "attachment_info": [], "zoom_info_params": None, "system_font_list": [],
            "multi_language_mode": "none", "multi_language_main": "none",
            "multi_language_current": "none", "multi_language_list": [],
            "subtitle_keywords_config": None, "use_float_render": False
        },
        "canvas_config": {"ratio": ratio, "width": canvas["width"], "height": canvas["height"], "background": None},
        "tracks": tracks,
        "group_container": None,
        "materials": {
            "flowers": [],
            "videos": [{
                "id": material_id, "type": "video",
                "path": video_str, "media_path": video_str,
                "duration": total_us, "width": src_width, "height": src_height,
                "material_name": video_path.name,
                "category_id": "", "category_name": "local",
                "has_audio": True, "source": "none", "source_platform": 0,
                "is_ai_generate_content": False, "is_copyright": False,
                "is_text_edit_overdub": False, "is_unified_beauty_mode": False,
                "local_id": "", "local_material_id": "", "origin_material_id": "",
                "request_id": "", "team_id": "", "text_task_id": "",
                "audio_fade": None, "cartoon_path": "", "check_flag": 63,
                "crop": {
                    "lower_left_x": 0.0, "lower_left_y": 1.0,
                    "lower_right_x": 1.0, "lower_right_y": 1.0,
                    "upper_left_x": 0.0, "upper_left_y": 0.0,
                    "upper_right_x": 1.0, "upper_right_y": 0.0
                },
                "crop_ratio": "free", "crop_scale": 1.0,
                "extra_type_option": 0, "formula_id": "", "freeze": None,
                "intensifies_audio_path": "", "material_id": material_id,
                "material_url": video_str,
                "matting": {"flag": 0, "has_use_quick_brush": False,
                            "has_use_quick_eraser": False,
                            "interactiveTime": [], "path": "", "strokes": []},
                "object_locked": None,
                "picture_from": "none", "picture_set_category_id": "",
                "picture_set_category_name": "",
                "reverse_intensifies_path": "", "reverse_path": "",
                "smart_motion": None,
                "stable": {"matrix_path": "", "stable_level": 0,
                           "time_range": {"duration": 0, "start": 0}},
                "text_camera_move": [],
                "video_algorithm": {"algorithms": [], "deflicker": None,
                                    "motion_blur_config": None, "noise_reduction": None,
                                    "path": "", "time_range": None}
            }] + extra_materials,
            "texts": text_materials,
            "tail_leaders": [], "audios": [], "images": [],
            "effects": [], "stickers": [], "canvases": [], "transitions": [],
            "audio_effects": [], "audio_fades": [], "beats": [],
            "material_animations": anim_materials, "placeholders": [], "placeholder_infos": [],
            "speeds": [], "common_mask": [], "chromas": [], "text_templates": [],
            "realtime_denoises": [], "audio_pannings": [], "audio_pitch_shifts": [],
            "video_trackings": [], "hsl": [], "drafts": [], "color_curves": [],
            "hsl_curves": [], "primary_color_wheels": [], "log_color_wheels": [],
            "video_effects": [], "audio_balances": [], "handwrites": [],
            "manual_deformations": [], "manual_beautys": [], "plugin_effects": [],
            "sound_channel_mappings": [], "green_screens": [], "shapes": [],
            "material_colors": [], "digital_humans": [], "digital_human_model_dressing": [],
            "smart_crops": [], "ai_translates": [], "audio_track_indexes": [],
            "loudnesses": [], "vocal_beautifys": [], "vocal_separations": [],
            "smart_relights": [], "time_marks": [], "multi_language_refs": [],
            "video_shadows": [], "video_strokes": [], "video_radius": []
        },
        "keyframes": {"videos": [], "audios": [], "texts": [], "stickers": [],
                      "filters": [], "adjusts": [], "handwrites": [], "effects": []},
        "keyframe_graph_list": [],
        "platform": platform,
        "last_modified_platform": platform,
        "mutable_config": None, "cover": None, "retouch_cover": None,
        "extra_info": None, "relationships": [],
        "render_index_track_mode_on": True, "free_render_index_mode_on": False,
        "static_cover_image_path": "", "source": "default", "time_marks": None,
        "path": "", "lyrics_effects": [],
        "uneven_animation_template_info": {"composition": "", "content": "", "order": "", "sub_template_info_list": []},
        "draft_type": "video",
        "smart_ads_info": {"page_from": "", "routine": "", "draft_url": ""},
        "function_assistant_info": {
            "smart_rec_applied": False, "fixed_rec_applied": False,
            "auto_adjust": False, "auto_adjust_segid_list": [],
            "color_correction": False, "color_correction_segid_list": [],
            "enhance_quality": False, "smooth_slow_motion": False,
            "deflicker_segid_list": [], "video_noise_segid_list": [],
            "enhance_quality_segid_list": [], "smart_segid_list": [],
            "retouch": False, "retouch_segid_list": [],
            "enhande_voice": False, "enhance_voice_segid_list": [],
            "audio_noise_segid_list": [], "auto_caption": False,
            "auto_caption_segid_list": [], "auto_caption_template_id": "",
            "caption_opt": False, "caption_opt_segid_list": [],
            "eye_correction": False, "eye_correction_segid_list": [],
            "normalize_loudness": False, "normalize_loudness_segid_list": [],
            "normalize_loudness_audio_denoise_segid_list": [],
            "auto_adjust_fixed": False, "auto_adjust_fixed_value": 50.0,
            "color_correction_fixed": False, "color_correction_fixed_value": 50.0,
            "normalize_loudness_fixed": False, "enhande_voice_fixed": False,
            "retouch_fixed": False, "enhance_quality_fixed": False,
            "smooth_slow_motion_fixed": False,
            "fps": {"num": 0, "den": 1}
        }
    }

    # ── project.json ─────────────────────────────────────
    project_json = {
        "config": {"color_space": -1, "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": ts, "id": project_id, "main_timeline_id": timeline_id,
        "timelines": [{"create_time": ts, "id": timeline_id, "is_marked_delete": False, "name": "타임라인 01", "update_time": ts}],
        "update_time": ts, "version": 0
    }

    # ── draft_meta_info.json ─────────────────────────────
    capcut_root = Path(os.environ.get("USERPROFILE", "")) / \
        "AppData" / "Local" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    draft_folder_path = capcut_root / draft_name

    draft_meta_info = {
        "cloud_draft_cover": False, "cloud_draft_sync": False,
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "", "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "", "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg", "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "", "draft_enterprise_name": "", "enterprise_material": []},
        "draft_fold_path": str(draft_folder_path).replace("\\", "/"),
        "draft_id": draft_id,
        "draft_is_ae_produce": False, "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False, "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False, "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": "false", "draft_is_invisible": False,
        "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
        "draft_materials": [
            {"type": 0, "value": [video_str] + [str(f).replace("\\", "/") for f in (append_files or [])]},
            {"type": 1, "value": []}, {"type": 2, "value": []},
            {"type": 3, "value": []}, {"type": 6, "value": []},
            {"type": 7, "value": []}, {"type": 8, "value": []}
        ],
        "draft_materials_copied_info": [], "draft_name": draft_name,
        "draft_need_rename_folder": False, "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": str(capcut_root).replace("/", "\\"),
        "draft_segment_extra_info": [], "draft_timeline_materials_size_": 0,
        "draft_type": "", "draft_web_article_video_enter_from": "",
        "tm_draft_cloud_completed": "", "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0, "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1, "tm_draft_cloud_user_id": -1,
        "tm_draft_create": ts, "tm_draft_modified": ts,
        "tm_draft_removed": 0, "tm_duration": final_duration
    }

    # ── 폴더 저장 ─────────────────────────────────────────
    draft_dir = output_dir / draft_name
    if draft_dir.exists():
        shutil.rmtree(draft_dir)
    draft_dir.mkdir(parents=True)

    def write(name, data):
        (draft_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    write("draft_content.json", draft_content)
    write("draft_meta_info.json", draft_meta_info)
    write("project.json", project_json)
    write("attachment_pc_common.json", {
        "ai_packaging_infos": [], "ai_packaging_report_info": {"caption_id_list": [], "commercial_material": "", "material_source": "", "method": "", "page_from": "", "style": "", "task_id": "", "text_style": "", "tos_id": "", "video_category": ""},
        "broll": {"ai_packaging_infos": [], "ai_packaging_report_info": {"caption_id_list": [], "commercial_material": "", "material_source": "", "method": "", "page_from": "", "style": "", "task_id": "", "text_style": "", "tos_id": "", "video_category": ""}},
        "commercial_music_category_ids": [], "pc_feature_flag": 0, "recognize_tasks": [],
        "reference_lines_config": {"horizontal_lines": [], "is_lock": False, "is_visible": False, "vertical_lines": []},
        "safe_area_type": 0, "template_item_infos": [], "unlock_template_ids": []
    })
    write("attachment_pc_timeline.json", {"reference_lines_config": {"horizontal_lines": [], "is_lock": False, "is_visible": False, "vertical_lines": []}, "safe_area_type": 0})
    write("timeline_layout.json", {"dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [timeline_id], "timelineNames": ["타임라인 01"]}], "layoutOrientation": 1})
    write("performance_opt_info.json", {"manual_cancle_precombine_segs": None, "need_auto_precombine_segs": None})
    write("draft_agency_config.json", {"is_auto_agency_enabled": False, "is_auto_agency_popup": False, "is_single_agency_mode": False, "marterials": None, "use_converter": True, "video_resolution": 720})
    write("draft_biz_config.json", {})
    (draft_dir / "draft_cover.jpg").write_bytes(b"")

    return draft_dir


def sort_files_by_download_time(paths: list[Path]) -> list[Path]:
    """다운로드(저장) 시간 순 정렬 — 파일 생성/수정 시각 중 이른 값 기준, 오름차순."""
    def key(p: Path):
        try:
            st = p.stat()
            # Windows는 st_ctime이 생성시각, mtime과 함께 더 이른 쪽을 다운로드 시각으로
            return min(getattr(st, "st_ctime", st.st_mtime), st.st_mtime)
        except Exception:
            return 0.0
    return sorted(paths, key=key)


def build_sequence_draft(
    files: list[Path],
    output_dir: Path,
    draft_name: str = "CapCut_Sequence",
    ratio: str = "9:16",
    image_dur_sec: float = DEFAULT_IMAGE_DUR_SEC,
    bg_files: set[str] | None = None,
    stroke_size: float = 0.15,
    unify_place: bool = True,
    cutouts: dict[str, str] | None = None,
    effect_dur_sec: float = 0.0,
    title_text: str = "",
    date_text: str = "",
) -> tuple[Path, list[str]]:
    """
    선택한 영상/이미지 파일들을 다운로드(저장) 시간 순으로 메인 트랙에 이어붙인 draft 생성.
    배경제거를 고른 이미지는 미리 만들어 둔 투명 PNG(cutouts)로 바꿔 넣는다.
    반환: (draft 폴더, 배치된 파일명 순서 목록)
    """
    bg_files = bg_files or set()
    canvas = CANVAS_PRESETS.get(ratio, CANVAS_PRESETS["9:16"])
    ordered = sort_files_by_download_time(list(files))

    timeline_id = str(uuid.uuid4()).upper()
    project_id  = str(uuid.uuid4()).upper()
    draft_id    = str(uuid.uuid4()).upper()
    track_id    = str(uuid.uuid4()).upper()
    ts = now_us()

    segments = []
    videos_materials = []
    placed = []
    cursor = 0
    cutouts = cutouts or {}
    for f in ordered:
        if not f.exists():
            continue
        src_file = Path(cutouts[str(f)]) if str(f) in cutouts else f
        w, h, dsec, is_img = get_media_info(src_file)
        if is_img:
            src_dur_us = IMAGE_SOURCE_DUR_US
            seg_dur = sec_to_us(image_dur_sec)
        else:
            if not dsec:
                continue
            src_dur_us = sec_to_us(dsec)
            seg_dur = src_dur_us
        if seg_dur < 100_000:
            continue
        mat_id = str(uuid.uuid4()).upper()
        mat = _media_material_dict(mat_id, src_file, w, h, src_dur_us, is_img)
        videos_materials.append(mat)
        segments.append(_media_segment_dict(
            str(uuid.uuid4()).upper(), mat_id, 0, seg_dur, cursor, is_img,
            place=_place_for(is_img, canvas) if unify_place else None))
        cursor += seg_dur
        placed.append(f.name)

    if not segments:
        raise ValueError("배치할 수 있는 영상/이미지 파일이 없습니다.")

    # 자료화면 하나하나에 빠짐없이 랜덤 등장 효과
    anim_materials = assign_random_effects(segments, effect_dur_sec, draft_name)

    final_duration = cursor
    tracks = [{"attribute": 0, "flag": 0, "id": track_id,
               "is_default_name": True, "name": "", "segments": segments, "type": "video"}]

    # 상단 제목 / 하단 날짜 (컷편집 모드와 똑같은 스타일·위치)
    text_materials: list = []
    for txt, style, make_mat in (
        (date_text.strip(),  DATE_STYLE,  _make_date_material),
        (title_text.strip(), TITLE_STYLE, _make_title_material),
    ):
        if not txt or final_duration <= 0:
            continue
        mat_id = str(uuid.uuid4()).upper()
        anim_id = str(uuid.uuid4()).upper()
        text_materials.append(make_mat(mat_id, txt))
        anim_materials.append(_make_sticker_animation(anim_id))
        tracks.append({
            "attribute": 0, "flag": 0, "id": str(uuid.uuid4()).upper(),
            "is_default_name": True, "name": "", "type": "text",
            "segments": [_make_overlay_text_segment(
                str(uuid.uuid4()).upper(), mat_id, 0, final_duration, style, anim_id)],
        })

    draft_dir = _finalize_draft(output_dir, draft_name, ratio, canvas,
                                videos_materials, text_materials, tracks, final_duration,
                                ordered, timeline_id, project_id, draft_id, ts,
                                anim_materials)
    return draft_dir, placed


# ══════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════

@app.get("/")
async def index():
    # 화면 파일을 새로 덮어썼는데 브라우저가 예전 걸 캐시해서 쓰는 일이 없도록
    # 항상 새로 받게 한다 (예전 화면이 남아 있으면 없는 항목을 읽어 오류가 난다)
    return FileResponse(STATIC_DIR / "index.html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    })


@app.post("/api/process")
async def process_video(
    request: Request,
    video_path: str,
    noise_db: float = -40.0,
    min_silence: float = 0.3,
    head_trim: float = 0.0,      # 클립 앞을 더 깎는 양 (ms)
    tail_trim: float = 0.0,      # 클립 뒤를 더 깎는 양 (ms)
    min_clip: float = 0.0,       # 이보다 짧은 클립은 버림 (초, 0이면 끄기)
    ng_short: float = 0.0,       # 같은 말 반복 중 이보다 짧은 테이크 삭제 (초, 0이면 끄기)
    auto_noise: bool = True,     # 무음 기준(dB)을 영상에 맞춰 자동으로 고름
    use_subtitle: bool = False,
    ratio: str = "9:16",
    max_sub_chars: int = MAX_SUBTITLE_CHARS,
):
    # POST body(JSON)로 대본 + 이어붙일 파일 목록을 받는다:
    #   {"script": "...", "append_files": ["C:/a.mp4", "C:/b.jpg"], "image_dur": 3.0}
    script_text = ""
    append_files: list[Path] = []
    image_dur = DEFAULT_IMAGE_DUR_SEC
    title_text = ""
    date_text = ""
    effect_dur = 0.0
    bg_files: set[str] = set()
    stroke_size = 0.15
    unify_place = True
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw.decode("utf-8"))
            script_text = (body.get("script") or "").strip()
            append_files = [Path(p) for p in (body.get("append_files") or []) if p]
            image_dur = float(body.get("image_dur") or DEFAULT_IMAGE_DUR_SEC)
            title_text = (body.get("title") or "").strip()
            date_text = (body.get("date") or "").strip()
            effect_dur = float(body.get("effect_dur") or 0)
            bg_files = {str(Path(p)) for p in (body.get("bg_files") or []) if p}
            stroke_size = float(body.get("stroke_size") or 0.15)
            unify_place = body.get("unify_place", True)
    except Exception:
        script_text = ""

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            video = Path(video_path)
            if not video.exists():
                yield f"data: {json.dumps({'step': 'error', 'msg': f'파일을 찾을 수 없습니다: {video_path}'})}\n\n"
                return

            yield f"data: {json.dumps({'step': 'upload', 'msg': f'파일 확인: {video.name}'})}\n\n"

            yield f"data: {json.dumps({'step': 'probe', 'msg': '영상 정보 분석 중...'})}\n\n"
            duration = get_video_duration(video)

            # ── 무음 기준(dB) 자동 선택 ──────────────────────
            # 방 소음 크기가 영상마다 달라서 고정 -40dB로는 무음이 안 잘리는 경우가 많다.
            # 오디오만 뽑아 여러 기준으로 실제 재보고, 말을 토막내기 직전 값을 고른다.
            level_wav = None
            db_used = noise_db          # 클로저 안에서 바꿀 값은 지역 변수로 복사
            if auto_noise:
                yield f"data: {json.dumps({'step': 'silence', 'msg': '소리 크기 분석 중... (무음 기준 자동 선택)'})}\n\n"
                loop0 = asyncio.get_event_loop()
                level_wav = await loop0.run_in_executor(None, extract_audio_wav, video)
                if level_wav:
                    picked, lines = await loop0.run_in_executor(
                        None, auto_noise_db, level_wav, min_silence, duration,
                        head_trim / 1000.0, tail_trim / 1000.0, min_clip)
                    for ln in lines:
                        yield f"data: {json.dumps({'step': 'silence', 'msg': ln})}\n\n"
                    db_used = picked
                else:
                    yield f"data: {json.dumps({'step': 'silence', 'msg': f'⚠ 소리 분석 실패 → 설정값 {db_used}dB 사용'})}\n\n"

            yield f"data: {json.dumps({'step': 'silence', 'msg': f'무음 구간 감지 중... ({db_used}dB / {min_silence}s)'})}\n\n"
            silences = detect_silence(level_wav or video, db_used, min_silence,
                                      total_duration=duration)
            if level_wav:
                try:
                    level_wav.unlink()
                except OSError:
                    pass
            yield f"data: {json.dumps({'step': 'silence_done', 'msg': f'무음 구간 {len(silences)}개 발견', 'silences': silences})}\n\n"

            keep_ranges = compute_keep_ranges(silences, duration,
                                              head_trim / 1000.0, tail_trim / 1000.0, min_clip)
            tight = []
            if head_trim or tail_trim:
                tight.append(f"앞 여유 {-head_trim:+.0f}ms / 뒤 여유 {-tail_trim:+.0f}ms")
            if min_clip:
                tight.append(f"{min_clip}초 미만 클립 제외")
                n_all = len(compute_keep_ranges(silences, duration,
                                                head_trim / 1000.0, tail_trim / 1000.0, 0.0))
                n_cut = n_all - len(keep_ranges)
                if n_cut > 0:
                    tight.append(f"이 옵션으로 {n_cut}개 삭제됨")
                if n_all and n_cut / n_all > 0.3:
                    warn = (f'⚠ 클립의 {n_cut/n_all*100:.0f}%가 짧다고 삭제됐습니다. '
                            f'말이 통째로 빠질 수 있으니 “짧은 클립 버리기”를 끄거나 '
                            f'최소 무음 길이를 올려보세요.')
                    yield f"data: {json.dumps({'step': 'silence', 'msg': warn})}\n\n"
            if tight:
                yield f"data: {json.dumps({'step': 'silence', 'msg': '컷 조정: ' + ', '.join(tight)})}\n\n"
            yield f"data: {json.dumps({'step': 'silence', 'msg': f'컷 클립 {len(keep_ranges)}개'})}\n\n"

            # 자막 인식
            raw_subs = None
            subtitle_count = 0
            drop_set: set[int] = set()   # 삭제한 반복 엔지컷 번호
            want_sub = use_subtitle   # 클로저 안에서 끄기 위해 지역 변수로 복사
            if ng_short > 0 and not want_sub:
                yield f"data: {json.dumps({'step': 'silence', 'msg': '⚠ 반복 엔지컷 삭제는 자막(음성인식)이 켜져 있어야 합니다. 이번엔 건너뜁니다.'})}\n\n"
            if want_sub:
                # 자막 패키지가 없으면 자동 설치 (포터블 runtime 포함)
                loop = asyncio.get_event_loop()
                yield f"data: {json.dumps({'step': 'asr', 'msg': '자막 기능 확인 중... (없으면 자동 설치, 수 분 소요)'})}\n\n"
                ok, msg = await loop.run_in_executor(None, ensure_faster_whisper)
                if not ok:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': f'⚠ 자막 기능을 준비하지 못했습니다: {msg}'})}\n\n"
                    yield f"data: {json.dumps({'step': 'asr', 'msg': '⚠ 자막 없이 컷편집만 진행합니다. (인터넷 연결 확인 후 다시 시도하세요)'})}\n\n"
                    want_sub = False
                else:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': msg})}\n\n"

            if want_sub:
                yield f"data: {json.dumps({'step': 'asr', 'msg': f'클립 {len(keep_ranges)}개를 하나씩 인식합니다. (첫 실행 시 모델 다운로드 ~3GB)'})}\n\n"
                async with _asr_lock:
                    loop = asyncio.get_event_loop()
                    prog: dict = {"done": 0, "total": len(keep_ranges)}
                    task = loop.run_in_executor(
                        None, transcribe_all_clips, video, keep_ranges, script_text, prog)
                    last = -1
                    n_total = len(keep_ranges)
                    while not task.done():
                        await asyncio.sleep(2)
                        d = prog.get("done", 0)
                        if d != last:
                            last = d
                            yield f"data: {json.dumps({'step': 'asr', 'msg': f'자막 인식 {d}/{n_total} 클립...'})}\n\n"
                    raw_subs = await task
                yield f"data: {json.dumps({'step': 'asr', 'msg': f'클립별 인식 완료 ({len(raw_subs)}개)'})}\n\n"
                n_halluc = prog.get("halluc", 0)
                if n_halluc:
                    msg_h = f"상투 문구 환각 제거 {n_halluc}개 (시청해주셔서 감사합니다 등)"
                    yield f"data: {json.dumps({'step': 'asr', 'msg': msg_h})}\n\n"

                n_rare = drop_rare_latin(raw_subs, {w.lower() for w in script_text.split()})
                if n_rare:
                    msg_r = f"한 번만 나온 영문 환각 제거 {n_rare}개 (QUES 등)"
                    yield f"data: {json.dumps({'step': 'asr', 'msg': msg_r})}\n\n"
                n_uni = unify_latin_words(raw_subs)
                if n_uni:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': f'영어 표기 통일 {n_uni}개 (NVIDIA/Nvidia/MVDIIA → 한 가지로)'})}\n\n"

                # ── 같은 말 반복(엔지컷) 중 짧은 테이크 삭제 ──
                if ng_short > 0:
                    texts = [s["text"] for s in raw_subs]
                    dropped = find_repeat_ng_clips(keep_ranges, texts, ng_short)
                    if dropped:
                        for k in dropped[:12]:
                            d_sec = (keep_ranges[k][3] - keep_ranges[k][2]) / 1e6
                            line = f'  - 삭제 {k + 1}번 클립 ({d_sec:.2f}초) “{texts[k][:20]}”'
                            yield f"data: {json.dumps({'step': 'asr', 'msg': line})}\n\n"
                        if len(dropped) > 12:
                            yield f"data: {json.dumps({'step': 'asr', 'msg': f'  ... 외 {len(dropped) - 12}개'})}\n\n"
                        drop_set = set(dropped)
                        raw_subs = [s for i, s in enumerate(raw_subs) if i not in drop_set]  # 지운 클립 말은 옆 클립으로 새지 않게
                        keep_ranges = compute_keep_ranges(
                            silences, duration, head_trim / 1000.0, tail_trim / 1000.0,
                            min_clip, drop_set)
                        yield f"data: {json.dumps({'step': 'asr', 'msg': f'반복 엔지컷 {len(dropped)}개 삭제 → 클립 {len(keep_ranges)}개'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'step': 'asr', 'msg': '반복 엔지컷: 삭제할 짧은 중복 없음'})}\n\n"

                if script_text:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': f'대본 {len(script_text)}자 수신 → 문맥 대조 교정 중...'})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': '대본 없음 → Whisper 인식 그대로 사용'})}\n\n"
                _stats: dict = {}
                sub_chunks = subtitle_chunks_for_timeline(raw_subs, keep_ranges, max_sub_chars,
                                                          script_text, _stats)
                if script_text:
                    n_fixed = _stats.get("corrected", 0)
                    yield f"data: {json.dumps({'step': 'asr', 'msg': f'대본 참고 철자 교정 {n_fixed}개 적용'})}\n\n"
                subtitle_count = len(sub_chunks)

                # 자막이 붙은 클립 수 + 영상 클립 길이와 자막 길이가 일치하는지 확인
                with_sub = sum(1 for _k, _e, ts, te in keep_ranges
                               if any(ts <= c["start_us"] < te for c in sub_chunks))
                fit, n_clips = subtitle_coverage_report(sub_chunks, keep_ranges)
                yield f"data: {json.dumps({'step': 'asr', 'msg': f'자막 적용 클립: {with_sub}/{len(keep_ranges)}개'})}\n\n"
                yield f"data: {json.dumps({'step': 'asr', 'msg': f'영상 클립 = 자막 길이 일치: {fit}/{n_clips}개'})}\n\n"

                srt_content = make_srt(sub_chunks)
                srt_path = OUTPUT_DIR / f"{video.stem}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")
                srt_name = video.stem + ".srt"
                yield f"data: {json.dumps({'step': 'asr_done', 'msg': f'자막 {subtitle_count}개 생성 완료', 'srt_name': srt_name})}\n\n"

            valid_appends = [f for f in append_files if f.exists()]
            if valid_appends:
                ordered = sort_files_by_download_time(valid_appends)
                names = ", ".join(f.name for f in ordered)
                yield f"data: {json.dumps({'step': 'draft', 'msg': f'뒤에 이어붙일 파일 {len(ordered)}개 (다운로드 순): {names}'})}\n\n"
                valid_appends = ordered

            extras = []
            if title_text:
                extras.append("제목")
            if date_text:
                extras.append(f"날짜({date_text})")
            if effect_dur > 0:
                extras.append(f"자료화면 전체 랜덤효과 {effect_dur}초")
            if bg_files:
                extras.append(f"배경제거 {len(bg_files)}개 파일")
            if unify_place:
                extras.append("자료화면 크기·위치 통일")
            if extras:
                yield f"data: {json.dumps({'step': 'draft', 'msg': '적용: ' + ', '.join(extras)})}\n\n"

            # ── 배경제거: 우리가 직접 투명 PNG로 만들어 넣는다 ──
            # 캡컷 배경제거는 캡컷이 만든 마스크 파일이 있어야 해서, draft에 켜두기만 하면
            # 검은 화면이 된다. 그래서 이미지는 미리 배경을 지워 넣는다.
            cutouts: dict[str, str] = {}
            bg_imgs = [f for f in valid_appends
                       if str(f) in bg_files and f.suffix.lower() in IMAGE_EXTS]
            bg_vids = [f for f in valid_appends
                       if str(f) in bg_files and f.suffix.lower() not in IMAGE_EXTS]
            if bg_imgs:
                yield f"data: {json.dumps({'step': 'draft', 'msg': f'배경제거 준비 중... (이미지 {len(bg_imgs)}개, 처음 한 번은 모델 176MB 다운로드)'})}\n\n"
                loop = asyncio.get_event_loop()
                ok, msg = await loop.run_in_executor(None, ensure_rembg)
                if not ok:
                    yield f"data: {json.dumps({'step': 'draft', 'msg': f'⚠ 배경제거 준비 실패: {msg} → 원본 이미지 그대로 넣습니다'})}\n\n"
                else:
                    cprog: dict = {"done": 0, "total": len(bg_imgs)}
                    ctask = loop.run_in_executor(
                        None, build_cutouts, bg_imgs, CUTOUT_DIR, True, stroke_size, cprog)
                    last_c = -1
                    while not ctask.done():
                        await asyncio.sleep(1)
                        dc = cprog.get("done", 0)
                        if dc != last_c:
                            last_c = dc
                            yield f"data: {json.dumps({'step': 'draft', 'msg': f'배경제거 {dc}/{len(bg_imgs)} ...'})}\n\n"
                    cutouts = await ctask
                    fail = len(bg_imgs) - len(cutouts)
                    done_msg = f'배경제거 완료 {len(cutouts)}개 (흰색 발광 테두리 포함)'
                    if fail:
                        done_msg += f' / 실패 {fail}개는 원본 그대로'
                    yield f"data: {json.dumps({'step': 'draft', 'msg': done_msg})}\n\n"
            if bg_vids:
                yield f"data: {json.dumps({'step': 'draft', 'msg': f'⚠ 영상 {len(bg_vids)}개는 배경제거를 캡컷에서 직접 켜주세요 (영상은 미리 처리하지 않습니다)'})}\n\n"

            yield f"data: {json.dumps({'step': 'draft', 'msg': f'CapCut draft 생성 중... ({ratio})'})}\n\n"
            name = video.stem
            draft_dir = build_draft(video, silences, duration, OUTPUT_DIR, name, raw_subs, ratio,
                                    max_sub_chars, script_text, valid_appends, image_dur,
                                    title_text, date_text, effect_dur, bg_files,
                                    True, stroke_size, 0.6, bool(unify_place),
                                    head_trim / 1000.0, tail_trim / 1000.0, min_clip, drop_set,
                                    cutouts)

            yield f"data: {json.dumps({'step': 'done', 'msg': 'draft 생성 완료!', 'draft_dir': str(draft_dir), 'silence_count': len(silences), 'clip_count': len(keep_ranges), 'subtitle_count': subtitle_count, 'append_count': len(valid_appends)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'msg': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _pick_files_windows(multi: bool, title: str, filt: str) -> list[str]:
    """
    Windows 기본 파일 열기 대화상자(.NET WinForms)를 PowerShell로 띄워 경로를 받는다.
    tkinter가 없는 포터블 Python에서도 동작한다. 비Windows/실패 시 빈 리스트.
    filt 형식: "영상·이미지|*.mp4;*.png|모든 파일|*.*"
    """
    ps = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$f=New-Object System.Windows.Forms.OpenFileDialog;"
        f"$f.Multiselect=${'true' if multi else 'false'};"
        f"$f.Title='{title}';"
        f"$f.Filter='{filt}';"
        "$f.RestoreDirectory=$true;"
        "$owner=New-Object System.Windows.Forms.Form; $owner.TopMost=$true;"
        "if($f.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK)"
        "{[Console]::Out.Write($f.FileNames -join \"`n\")}"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        out = (r.stdout or "").strip()
        return [ln.strip() for ln in out.splitlines() if ln.strip()]
    except Exception:
        return []


_VIDEO_FILTER = "영상 파일|*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v|모든 파일|*.*"
_MEDIA_FILTER = ("영상·이미지|*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v;"
                 "*.jpg;*.jpeg;*.png;*.webp;*.bmp;*.gif;*.heic|모든 파일|*.*")


@app.get("/api/browse")
async def browse_file():
    try:
        picked = _pick_files_windows(False, "영상 파일 선택", _VIDEO_FILTER)
        return JSONResponse({"path": picked[0] if picked else ""})
    except Exception as e:
        return JSONResponse({"path": "", "error": str(e)})


@app.get("/api/thumb")
async def get_thumb(path: str):
    """
    선택한 파일의 미리보기 이미지 (목록에서 마우스 올렸을 때 사용).
    이미지는 원본을 그대로, 영상은 첫 프레임을 ffmpeg으로 뽑아 반환한다.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise HTTPException(404, "파일을 찾을 수 없습니다.")

    if p.suffix.lower() in IMAGE_EXTS:
        return FileResponse(p)

    import tempfile
    out = Path(tempfile.gettempdir()) / f"_capcut_thumb_{abs(hash(str(p)))}.jpg"
    if not out.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(p),
             "-frames:v", "1", "-vf", "scale=320:-1", str(out)],
            capture_output=True)
    if not out.exists():
        raise HTTPException(404, "미리보기를 만들 수 없습니다.")
    return FileResponse(out, media_type="image/jpeg")


@app.get("/api/browse-multi")
async def browse_files():
    """파일 여러 개 선택 (영상+이미지). 다운로드(저장) 시간 순으로 정렬해서 반환."""
    try:
        picked = _pick_files_windows(True, "영상·이미지 파일 여러 개 선택", _MEDIA_FILTER)
        ordered = sort_files_by_download_time([Path(p) for p in picked])
        return JSONResponse({"files": [str(p) for p in ordered]})
    except Exception as e:
        return JSONResponse({"files": [], "error": str(e)})


@app.post("/api/build-sequence")
async def build_sequence(request: Request):
    """선택 파일들만으로 순서대로 배치한 새 캡컷 draft 생성 (별도 기능)."""
    body = await request.json()
    files = [Path(p) for p in (body.get("files") or []) if p]
    ratio = body.get("ratio") or "9:16"
    image_dur = float(body.get("image_dur") or DEFAULT_IMAGE_DUR_SEC)
    name = (body.get("name") or "CapCut_Sequence").strip() or "CapCut_Sequence"
    files = [f for f in files if f.exists()]
    if not files:
        raise HTTPException(400, "선택된 파일이 없습니다.")
    bg_set = {str(Path(p)) for p in (body.get("bg_files") or []) if p}
    stroke_sz = float(body.get("stroke_size") or 0.15)
    cutouts: dict[str, str] = {}
    bg_imgs = [f for f in files if str(f) in bg_set and f.suffix.lower() in IMAGE_EXTS]
    if bg_imgs:
        ok, _msg = await asyncio.get_event_loop().run_in_executor(None, ensure_rembg)
        if ok:
            cutouts = await asyncio.get_event_loop().run_in_executor(
                None, build_cutouts, bg_imgs, CUTOUT_DIR, True, stroke_sz, None)
    try:
        draft_dir, placed = build_sequence_draft(
            files, OUTPUT_DIR, name, ratio, image_dur, bg_set, stroke_sz,
            bool(body.get("unify_place", True)), cutouts,
            float(body.get("effect_dur") or 0),
            (body.get("title") or "").strip(), (body.get("date") or "").strip())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"success": True, "draft_dir": str(draft_dir),
                         "draft_name": name, "count": len(placed), "order": placed})


@app.post("/api/copy-to-capcut/{draft_name}")
async def copy_to_capcut(draft_name: str):
    capcut_dir = Path(os.environ.get("USERPROFILE", "")) / \
        "AppData" / "Local" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    if not capcut_dir.exists():
        raise HTTPException(404, f"CapCut 폴더를 찾을 수 없습니다: {capcut_dir}")
    src = OUTPUT_DIR / draft_name
    if not src.exists():
        raise HTTPException(404, "draft를 찾을 수 없습니다.")
    dst = capcut_dir / draft_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return JSONResponse({"success": True, "dst": str(dst)})


@app.get("/api/download-srt/{srt_name}")
async def download_srt(srt_name: str):
    srt_path = OUTPUT_DIR / srt_name
    if not srt_path.exists():
        raise HTTPException(404, "SRT 파일을 찾을 수 없습니다.")
    return FileResponse(srt_path, filename=srt_name, media_type="text/plain")


@app.get("/api/outputs")
async def list_outputs():
    dirs = [d.name for d in OUTPUT_DIR.iterdir() if d.is_dir()]
    return JSONResponse({"drafts": sorted(dirs, reverse=True)})


@app.get("/api/download/{draft_name}")
async def download_draft(draft_name: str):
    draft_dir = OUTPUT_DIR / draft_name
    if not draft_dir.exists():
        raise HTTPException(404, "draft를 찾을 수 없습니다.")
    zip_path = OUTPUT_DIR / f"{draft_name}.zip"
    shutil.make_archive(str(OUTPUT_DIR / draft_name), "zip", str(draft_dir))
    return FileResponse(zip_path, filename=f"{draft_name}.zip", media_type="application/zip")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def _find_free_port(host: str, start: int, tries: int = 20) -> int:
    """start 포트부터 비어 있는 포트를 찾는다 (이전 서버가 떠 있어도 실행되게)."""
    import socket
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return start


if __name__ == "__main__":
    # 포터블(embeddable) Python은 격리 모드라 스크립트 폴더가 sys.path에 없다.
    # 앱 객체를 직접 넘기고(문자열 "server:app" 금지), 경로도 넣어 둔다.
    sys.path.insert(0, str(BASE_DIR))
    try:
        host = "127.0.0.1"
        port = _find_free_port(host, 8765)
        url = f"http://localhost:{port}"

        import threading, webbrowser
        def _open():
            time.sleep(1.5)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

        print("=" * 52)
        print("  CapCut Agent 실행 중")
        print(f"  브라우저에서 접속: {url}")
        print("  종료: 이 창에서 Ctrl+C")
        print("=" * 52, flush=True)

        uvicorn.run(app, host=host, port=port, log_level="info")

    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            (BASE_DIR / "error_log.txt").write_text(err, encoding="utf-8")
        except Exception:
            pass
        print("\n" + "=" * 52)
        print("  실행 오류가 발생했습니다.")
        print("  아래 내용과 error_log.txt 파일을 알려주세요.")
        print("=" * 52)
        print(err)
        try:
            input("\n닫으려면 Enter를 누르세요...")
        except Exception:
            pass
