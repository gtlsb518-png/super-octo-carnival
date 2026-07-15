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

for d in [UPLOAD_DIR, OUTPUT_DIR]:
    d.mkdir(exist_ok=True)

_draft_cache: dict[str, Path] = {}
_asr_lock = asyncio.Lock()

# Whisper 모델 lazy init
_whisper_model = None

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

# 자막 한 조각의 목표 글자 수 (기본값, UI에서 조절 가능).
# 어절 경계로 이 길이 안팎에서 끊는다. 완성본 참고 자막 기준 조각당 평균 9자.
MAX_SUBTITLE_CHARS = 10

def snap_to_frame(sec: float) -> float:
    """초 단위 시간을 가장 가까운 프레임 경계로 스냅 (30fps 기준)"""
    frame = round(sec * FPS)
    return frame / FPS


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

def get_video_duration(video_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return float(json.loads(r.stdout)["format"]["duration"])


def compute_keep_ranges(silences: list[dict], total_duration: float) -> list[tuple[float, float, int, int]]:
    """
    무음을 제외하고 남기는(keep) 구간 목록.
    반환: [(원본_start_sec, 원본_end_sec, 타임라인_start_us, 타임라인_end_us), ...]
    영상 세그먼트와 자막(draft 텍스트/SRT)이 모두 이 함수를 공유하므로
    타이밍이 마이크로초 단위로 완전히 일치한다.
    """
    ranges: list[tuple[float, float, int, int]] = []
    tl_us = 0

    def add(ks: float, ke: float):
        nonlocal tl_us
        src_start = sec_to_us(snap_to_frame(ks))
        src_end   = sec_to_us(snap_to_frame(ke))
        dur = src_end - src_start
        if dur < 100_000:  # 0.1초 미만 클립은 버림
            return
        ranges.append((ks, ke, tl_us, tl_us + dur))
        tl_us += dur

    cursor = 0.0
    for sil in sorted(silences, key=lambda x: x["start"]):
        s, e = snap_to_frame(sil["start"]), snap_to_frame(sil["end"])
        if s > cursor:
            add(cursor, s)
        cursor = max(cursor, e)
    if cursor < total_duration:
        add(cursor, total_duration)
    return ranges


# ══════════════════════════════════════════════════════════
# Whisper 자막 인식
# ══════════════════════════════════════════════════════════

def transcribe(video_path: Path, script_text: str = "") -> list[dict]:
    """
    반환: [{"start": float, "end": float, "text": str}, ...]
    타임스탬프는 원본 영상 기준.
    script_text가 있으면 initial_prompt로 넣어 어휘/고유명사 인식을 바이어스.
    (Whisper는 프롬프트 마지막 224토큰만 사용하므로 앞부분 위주로 잘라서 전달)
    """
    model = get_whisper_model()
    kwargs = {}
    if script_text.strip():
        kwargs["initial_prompt"] = script_text.strip()[:700]
    segments, _ = model.transcribe(
        str(video_path),
        language="ko",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        word_timestamps=True,          # 단어별 발화 시각 → 클립별 자막 배분에 사용
        condition_on_previous_text=False,  # 직전 문장 반복(할루시네이션 루프) 방지
        no_repeat_ngram_size=3,        # 같은 3-gram 반복 금지
        repetition_penalty=1.2,        # 반복 토큰에 페널티
        **kwargs,
    )
    return [{
        "start": s.start, "end": s.end, "text": s.text.strip(),
        "words": [{"start": w.start, "end": w.end, "word": w.word.strip()} for w in (s.words or [])],
    } for s in segments]



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


def spell_correct_words(words: list[dict], script_words: list[str],
                        script_norm: list[str]) -> list[dict]:
    """
    각 단어의 '철자만' 대본 단어로 교정 (내용·순서·개수는 그대로 유지).
    - 단어별로 대본 전체에서 가장 비슷한 단어를 찾아 유사도가 충분하면 교체
      (실하냐→실화냐, 대표선수→대표지수, 1회를→1위를 등)
    - 비슷한 대본 단어가 없으면 원래 인식 단어를 그대로 둠 (버리지 않음)
    - 단어를 소모하지 않으므로 중복 클립의 같은 단어도 똑같이 교정됨
    """
    import difflib
    if not script_norm:
        return words
    sm = difflib.SequenceMatcher(None, autojunk=False)
    out = []
    for wd in words:
        wnorm = _norm_token(wd["word"])
        if not wnorm:
            out.append(wd)
            continue
        sm.set_seq1(wnorm)
        best_r, best_j = 0.0, -1
        for j, sn in enumerate(script_norm):
            if not sn:
                continue
            sm.set_seq2(sn)
            if sm.quick_ratio() <= best_r:
                continue
            r = sm.ratio()
            if r > best_r:
                best_r, best_j = r, j
        if best_j >= 0 and best_r >= 0.55:
            out.append({**wd, "word": script_words[best_j]})
        else:
            out.append(wd)
    return out


def build_word_stream(segments: list[dict]) -> list[dict]:
    """
    Whisper 세그먼트 → 단어 스트림 [{"time": 발화_중간(초), "start": 발화_시작(초), "word": str}, ...].
    - 단어별 타임스탬프(word_timestamps)가 있으면 실제 발화 시각 사용
    - 없으면(대본 보정으로 텍스트가 교체된 경우 등) 세그먼트 구간에 균등 배분
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
                stream.append({"time": (w["start"] + w["end"]) / 2, "start": w["start"], "end": w["end"], "word": tw})
        else:
            n = len(text_words)
            seg_dur = max(seg["end"] - seg["start"], 0.01)
            for wi, tw in enumerate(text_words):
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
        mapped.append({"word": w["word"], "tl_start": s_us, "tl_end": max(e_us, s_us)})
    mapped.sort(key=lambda x: x["tl_start"])
    return mapped


def _ends_clause(word: str) -> bool:
    """한국어 구절 끝(구두점)인지 — 여기서 끊으면 자연스럽다."""
    return word[-1:] in ",.?!…\"”)"


def chunk_words_korean(words: list[dict], max_chars: int, tolerance: int = 3,
                       gap_break_us: int = 700_000) -> list[list[dict]]:
    """
    연속된 단어열을 한국어 자막 조각으로 분할.
    - 단어(어절) 경계에서만 끊음 → 한국어 어미로 자연스럽게 끝남
    - 조각 길이 목표 max_chars, 최대 max_chars+tolerance
    - 구두점(쉼표/마침표 등)으로 끝나면 조금 이르게 끊어 구절 유지
    - 단어 사이 시간 간격이 gap_break_us 이상이면(말 사이 쉼) 무조건 끊음
      → 시간상 멀리 떨어진 단어가 한 자막에 뭉치지 않음
    - 너무 짧은 꼬리 조각은 직전 조각에 합침
    """
    hard = max_chars + tolerance
    groups: list[list[dict]] = []
    cur: list[dict] = []
    cl = 0
    for wd in words:
        # 직전 단어와 시간 간격이 크면 먼저 끊기
        if cur and "tl_start" in wd and "tl_end" in cur[-1]:
            if wd["tl_start"] - cur[-1]["tl_end"] >= gap_break_us:
                groups.append(cur)
                cur, cl = [], 0
        wl = len(wd["word"])
        add = wl + (1 if cur else 0)
        if cur and cl + add > hard:
            groups.append(cur)
            cur, cl = [wd], wl
        else:
            cur.append(wd)
            cl += add
        last = cur[-1]["word"]
        if cl >= hard or cl >= max_chars or (_ends_clause(last) and cl >= max_chars - tolerance):
            groups.append(cur)
            cur, cl = [], 0
    if cur:
        if groups and cl <= tolerance + 2:
            prev = groups[-1]
            plen = sum(len(x["word"]) for x in prev) + len(prev) - 1
            # 시간 간격이 크지 않을 때만 꼬리 합침
            close = "tl_start" not in cur[0] or (cur[0]["tl_start"] - prev[-1].get("tl_end", cur[0]["tl_start"]) < gap_break_us)
            if plen + 1 + cl <= hard and close:
                groups[-1] = prev + cur
                cur = []
        if cur:
            groups.append(cur)
    return groups


def subtitle_chunks_for_timeline(segments: list[dict],
                                 keep_ranges: list[tuple[float, float, int, int]],
                                 max_chars: int = MAX_SUBTITLE_CHARS,
                                 script_text: str = "") -> list[dict]:
    """
    최종 자막 목록 생성 (draft 텍스트 트랙 + SRT 공용).
    참고 완성본처럼, 클립 경계와 무관하게 '말 전체'를 연속 자막으로 만든다.
    - 발화된 모든 단어가 순서대로 자막에 들어감 (중복 단어·문장도 그대로, 누락 없음)
    - 한국어 어절 경계에서 max_chars(±3) 길이로 끊음
    - 각 조각 시각 = 그 조각 단어들의 실제 발화 시각(타임라인 매핑)
    - 자막끼리는 끊김 없이 이어지되(작은 틈은 다음 자막까지 연장),
      말이 없는 큰 공백(무발화 구간)은 자막 없이 비움
    - 대본(script_text)이 있으면 단어 '철자만' 교정 (내용·개수·순서 유지)
    반환: [{"start_us": int, "end_us": int, "text": str}, ...]
    """
    MIN_DUR_US = 66_667      # 자막 최소 길이 (2프레임)
    BRIDGE_US = 500_000      # 이보다 작은 자막 사이 틈은 이어붙임(연속), 크면 공백 유지

    stream = build_word_stream(segments)
    if script_text.strip():
        script_words = script_text.split()
        script_norm = [_norm_token(w) for w in script_words]
        stream = spell_correct_words(stream, script_words, script_norm)

    mapped = map_words_to_timeline(stream, keep_ranges)
    if not mapped:
        return []

    groups = chunk_words_korean(mapped, max_chars)

    out = []
    prev_end = 0
    for gi, g in enumerate(groups):
        s = max(g[0]["tl_start"], prev_end)
        e = g[-1]["tl_end"]
        # 다음 조각 시작까지 틈이 작으면 이어붙여 연속되게, 크면 그대로(공백 유지)
        if gi + 1 < len(groups):
            nxt = groups[gi + 1][0]["tl_start"]
            if nxt > e and nxt - e <= BRIDGE_US:
                e = nxt
            elif nxt < e:  # 겹치면 다음 시작 전까지로 제한
                e = nxt
        e = max(e, s + MIN_DUR_US)
        # 프레임 스냅
        s = sec_to_us(snap_to_frame(s / 1_000_000))
        e = sec_to_us(snap_to_frame(e / 1_000_000))
        if e <= s:
            e = s + MIN_DUR_US
        text = " ".join(w["word"] for w in g)
        out.append({"start_us": s, "end_us": e, "text": text})
        prev_end = e
    return out

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
) -> Path:
    """
    subtitles:     Whisper 원본 인식 결과 (원본 영상 타임스탬프 + 단어별 시각 기준).
                   발화가 있는 모든 영상 클립 위에 자막 클립을 텍스트 트랙으로 삽입한다.
    ratio:         "9:16" (숏츠 1080x1920, 기본) 또는 "16:9" (가로 1920x1080)
    max_sub_chars: 자막 한 조각 최대 글자 수 (±3자 허용)
    script_text:   대본. 있으면 정답 텍스트로 정렬해 오탈자 교정 + 반복 제거
    """
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
    keep_ranges = compute_keep_ranges(silences, total_duration_sec)

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

    final_duration = timeline_cursor_us

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
            }],
            "texts": text_materials,
            "tail_leaders": [], "audios": [], "images": [],
            "effects": [], "stickers": [], "canvases": [], "transitions": [],
            "audio_effects": [], "audio_fades": [], "beats": [],
            "material_animations": [], "placeholders": [], "placeholder_infos": [],
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
            {"type": 0, "value": [video_str]},
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


# ══════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/process")
async def process_video(
    request: Request,
    video_path: str,
    noise_db: float = -40.0,
    min_silence: float = 0.5,
    use_subtitle: bool = False,
    ratio: str = "9:16",
    max_sub_chars: int = MAX_SUBTITLE_CHARS,
):
    # 대본은 길이 제한 없는 POST body(JSON)로 받는다: {"script": "..."}
    # FastAPI Body 파싱 대신 raw body를 직접 읽어 파싱 문제를 원천 차단
    script_text = ""
    try:
        raw = await request.body()
        if raw:
            script_text = (json.loads(raw.decode("utf-8")).get("script") or "").strip()
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

            yield f"data: {json.dumps({'step': 'silence', 'msg': f'무음 구간 감지 중... ({noise_db}dB / {min_silence}s)'})}\n\n"
            silences = detect_silence(video, noise_db, min_silence, total_duration=duration)
            yield f"data: {json.dumps({'step': 'silence_done', 'msg': f'무음 구간 {len(silences)}개 발견', 'silences': silences})}\n\n"

            keep_ranges = compute_keep_ranges(silences, duration)

            # 자막 인식
            raw_subs = None
            subtitle_count = 0
            if use_subtitle:
                yield f"data: {json.dumps({'step': 'asr', 'msg': 'Whisper 자막 인식 중... (GPU large-v3)'})}\n\n"
                async with _asr_lock:
                    loop = asyncio.get_event_loop()
                    raw_subs = await loop.run_in_executor(None, transcribe, video, script_text)
                if script_text:
                    # 진단: 대본이 실제로 수신됐는지 + 철자 교정이 몇 개 걸리는지 표시
                    _sw = script_text.split()
                    _sn = [_norm_token(w) for w in _sw]
                    _all = build_word_stream(raw_subs)
                    _corr = spell_correct_words(_all, _sw, _sn)
                    _nfix = sum(1 for a, b in zip(_all, _corr) if a["word"] != b["word"])
                    yield f"data: {json.dumps({'step': 'asr', 'msg': f'대본 {len(script_text)}자 수신 → 철자 교정 {_nfix}개 적용'})}\n\n"
                else:
                    yield f"data: {json.dumps({'step': 'asr', 'msg': '대본 없음 → Whisper 인식 그대로 사용'})}\n\n"
                sub_chunks = subtitle_chunks_for_timeline(raw_subs, keep_ranges, max_sub_chars, script_text)
                subtitle_count = len(sub_chunks)
                srt_content = make_srt(sub_chunks)
                srt_path = OUTPUT_DIR / f"{video.stem}.srt"
                srt_path.write_text(srt_content, encoding="utf-8")
                srt_name = video.stem + ".srt"
                yield f"data: {json.dumps({'step': 'asr_done', 'msg': f'자막 {subtitle_count}개 생성 완료', 'srt_name': srt_name})}\n\n"

            yield f"data: {json.dumps({'step': 'draft', 'msg': f'CapCut draft 생성 중... ({ratio})'})}\n\n"
            name = video.stem
            draft_dir = build_draft(video, silences, duration, OUTPUT_DIR, name, raw_subs, ratio, max_sub_chars, script_text)

            yield f"data: {json.dumps({'step': 'done', 'msg': 'draft 생성 완료!', 'draft_dir': str(draft_dir), 'silence_count': len(silences), 'clip_count': len(keep_ranges), 'subtitle_count': subtitle_count})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'step': 'error', 'msg': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/browse")
async def browse_file():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        path = filedialog.askopenfilename(
            title="영상 파일 선택",
            filetypes=[("영상 파일", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("모든 파일", "*.*")]
        )
        root.destroy()
        return JSONResponse({"path": path or ""})
    except Exception as e:
        return JSONResponse({"path": "", "error": str(e)})


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

if __name__ == "__main__":
    import threading, webbrowser
    def _open(): 
        time.sleep(1.5)
        webbrowser.open("http://localhost:8765")
    threading.Thread(target=_open, daemon=True).start()
    uvicorn.run("server:app", host="127.0.0.1", port=8765, reload=False)
