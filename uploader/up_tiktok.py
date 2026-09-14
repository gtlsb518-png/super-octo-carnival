#!/usr/bin/env python3
"""
틱톡 업로드 자동화 (틱톡 스튜디오 웹 업로드)

동작 순서:
  1. tiktok.com/tiktokstudio/upload 접속 (로그인 확인)
  2. 영상 파일 선택
  3. 설명(캡션) + 해시태그 입력
  4. 커버(썸네일) 지정  ※ 틱톡 UI 상 실패할 수 있어 실패 시 건너뜀
  5. 공개 범위 설정
  6. 게시
"""

import os
import time

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=ko"


# ==================== 공통 도우미 ====================

def _frames(page):
    """메인 페이지 + iframe 전부 (틱톡은 버전에 따라 iframe 안에 업로드 UI가 있음)."""
    out = [page]
    for f in page.frames:
        if f is not page.main_frame:
            out.append(f)
    return out


def _find_any(page, selectors, timeout=20000, state="visible"):
    """메인/iframe 어디에 있든 먼저 찾아지는 요소를 반환. (locator, frame) 반환."""
    end = time.time() + timeout / 1000.0
    while time.time() < end:
        for fr in _frames(page):
            for sel in selectors:
                try:
                    loc = fr.locator(sel).first
                    loc.wait_for(state=state, timeout=400)
                    return loc, fr
                except Exception:
                    continue
        time.sleep(0.3)
    raise RuntimeError(f"요소를 찾지 못했습니다: {selectors}")


def _click_if(page, selectors, timeout=4000):
    try:
        loc, _ = _find_any(page, selectors, timeout=timeout)
        loc.click()
        return True
    except Exception:
        return False


# ==================== 메인 ====================

def upload(page, cfg, log):
    """
    cfg 키:
      video   : 영상 파일 경로 (필수)
      title   : 제목 (캡션 첫 줄로 들어감)
      desc    : 상세 설명
      tags    : 태그 (쉼표 구분, 자동으로 #붙임)
      cover   : 커버 이미지 경로 (선택)
      privacy : 'public' | 'friends' | 'private'
    """
    video = cfg.get("video", "").strip()
    if not video or not os.path.exists(video):
        raise RuntimeError(f"영상 파일을 찾을 수 없습니다: {video}")

    # ---------- 1. 업로드 페이지 접속 ----------
    log("[1/6] 틱톡 업로드 페이지 접속 중...")
    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(4)

    if "/login" in page.url:
        raise RuntimeError(
            "틱톡 로그인이 안 되어 있습니다.\n"
            "열린 크롬 창에서 틱톡에 먼저 로그인한 뒤 다시 실행하세요."
        )

    # ---------- 2. 영상 파일 선택 ----------
    log(f"[2/6] 영상 업로드 시작: {os.path.basename(video)}")
    fin, _ = _find_any(page, [
        "input[type=file][accept*='video']",
        "input[type=file]",
    ], timeout=40000, state="attached")
    fin.set_input_files(video)

    # 업로드가 붙을 때까지 대기
    time.sleep(5)

    # ---------- 3. 캡션(제목+설명+태그) ----------
    log("[3/6] 제목·설명·태그 입력 중...")

    parts = []
    if cfg.get("title", "").strip():
        parts.append(cfg["title"].strip())
    if cfg.get("desc", "").strip():
        parts.append(cfg["desc"].strip())
    caption = "\n".join(parts)

    tags = [t.strip().lstrip("#") for t in cfg.get("tags", "").split(",") if t.strip()]

    try:
        cap, fr = _find_any(page, [
            "div[contenteditable='true'].public-DraftEditor-content",
            ".public-DraftEditor-content",
            "div[contenteditable='true'][role='combobox']",
            "div[contenteditable='true']",
        ], timeout=60000)

        cap.click()
        time.sleep(0.5)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        time.sleep(0.3)

        # 줄바꿈은 shift+enter 로 (enter 는 자동완성 선택이라 위험)
        for i, line in enumerate(caption.split("\n")):
            if i > 0:
                page.keyboard.press("Shift+Enter")
            page.keyboard.type(line, delay=12)

        # 해시태그: 입력 후 자동완성 창을 ESC 로 닫고 공백
        for t in tags:
            page.keyboard.type(" #" + t, delay=25)
            time.sleep(1.2)          # 자동완성 뜨는 시간
            page.keyboard.press("Escape")
            time.sleep(0.3)
        if tags:
            page.keyboard.type(" ", delay=10)

        log("  - 캡션 입력 완료")
    except Exception as e:
        log(f"  ! 캡션 입력 실패: {e}")

    # ---------- 4. 커버(썸네일) ----------
    cover = cfg.get("cover", "").strip()
    if cover and os.path.exists(cover):
        log("[4/6] 커버 이미지 설정 시도 중...")
        try:
            _click_if(page, [
                "div:has-text('커버 편집')",
                "button:has-text('커버 편집')",
                "div:has-text('Edit cover')",
                "button:has-text('Edit cover')",
            ], timeout=8000)
            time.sleep(2)
            _click_if(page, [
                "div[role='tab']:has-text('업로드')",
                "div[role='tab']:has-text('Upload')",
                "button:has-text('업로드')",
            ], timeout=6000)
            time.sleep(1)

            cin, _ = _find_any(page, [
                "input[type=file][accept*='image']",
            ], timeout=10000, state="attached")
            cin.set_input_files(cover)
            time.sleep(3)

            _click_if(page, [
                "button:has-text('확인')",
                "button:has-text('저장')",
                "button:has-text('Confirm')",
                "button:has-text('Save')",
            ], timeout=8000)
            time.sleep(2)
            log("  - 커버 설정 완료")
        except Exception as e:
            log(f"  ! 커버 설정 실패 (틱톡 UI가 바뀌었을 수 있음, 건너뜀): {e}")
    else:
        if cover:
            log(f"[4/6] 커버 파일 없음, 건너뜀: {cover}")
        else:
            log("[4/6] 커버 없음, 건너뜀")

    # ---------- 5. 공개 범위 ----------
    privacy = (cfg.get("privacy") or "public").lower()
    label = {"public": "전체 공개", "friends": "친구", "private": "나만 보기"}.get(privacy, "전체 공개")
    log(f"[5/6] 공개 범위 설정: {label}")
    try:
        _click_if(page, [
            "div[class*='select-container']",
            "div[class*='visibility'] div[class*='select']",
            "div:has-text('공개 범위') + div",
        ], timeout=6000)
        time.sleep(1)
        if not _click_if(page, [
            f"div[role='option']:has-text('{label}')",
            f"li:has-text('{label}')",
            f"div:has-text('{label}')",
        ], timeout=6000):
            log("  ! 공개 범위 항목을 못 찾음 (기본값 사용)")
            page.keyboard.press("Escape")
    except Exception as e:
        log(f"  ! 공개 범위 설정 실패 (기본값 사용): {e}")

    # ---------- 6. 업로드 완료 대기 후 게시 ----------
    log("[6/6] 영상 전송 완료 대기 중...")
    _wait_upload(page, log)

    log("  - 게시 버튼 클릭")
    clicked = _click_if(page, [
        "button[data-e2e='post_video_button']",
        "button:has-text('게시')",
        "button:has-text('Post')",
        "div[role='button']:has-text('게시')",
    ], timeout=30000)

    if not clicked:
        raise RuntimeError("게시 버튼을 찾지 못했습니다. 브라우저에서 직접 확인하세요.")

    time.sleep(6)

    # '게시 완료' 안내창 닫기
    _click_if(page, [
        "button:has-text('확인')",
        "button:has-text('닫기')",
        "button:has-text('OK')",
    ], timeout=8000)

    log("🎉 틱톡 업로드 완료!")
    return True


def _wait_upload(page, log, max_wait=3600):
    """전송 중 표시가 사라질 때까지 대기."""
    end = time.time() + max_wait
    last = ""
    blank = 0          # '업로드 중' 표시가 안 보인 횟수

    while time.time() < end:
        txt = ""
        for fr in _frames(page):
            try:
                el = fr.locator("div:has-text('업로드 중'), div:has-text('Uploading')").last
                txt = " ".join(el.inner_text(timeout=1500).split())[:60]
                break
            except Exception:
                continue

        if not txt:
            # 한 번 안 보인다고 바로 끝내면 안 됨 (표시가 늦게 뜰 수 있음)
            blank += 1
            if blank >= 3:
                log("  - 전송 완료")
                return True
        else:
            blank = 0
            if txt != last:
                log(f"    {txt}")
                last = txt

        time.sleep(5)

    log("  ! 전송 대기 시간 초과 - 그대로 진행합니다")
    return False
