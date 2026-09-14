#!/usr/bin/env python3
"""
유튜브 업로드 자동화 (유튜브 스튜디오)

동작 순서:
  1. studio.youtube.com 접속 (로그인 확인)
  2. 업로드 창 열기 -> 영상 파일 선택
  3. 제목 / 설명 입력
  4. 썸네일 업로드
  5. 아동용 여부 선택
  6. 태그 입력 ('자세히 보기' 안에 있음)
  7. 다음 x3 -> 공개 설정 -> 게시
"""

import os
import time


# ==================== 공통 도우미 ====================

def _first(page, selectors, timeout=15000, state="visible"):
    """여러 후보 선택자 중 먼저 나타나는 것을 반환 (유튜브 UI가 자주 바뀌므로)."""
    end = time.time() + timeout / 1000.0
    last_err = None
    while time.time() < end:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                loc.wait_for(state=state, timeout=500)
                return loc
            except Exception as e:
                last_err = e
        time.sleep(0.2)
    raise RuntimeError(f"요소를 찾지 못했습니다: {selectors} ({last_err})")


def _type_into(page, loc, text, log):
    """contenteditable 박스에 기존 내용을 지우고 새로 입력."""
    loc.click()
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    if text:
        # 줄바꿈은 Enter 가 아니라 직접 눌러야 함 (자동완성 오작동 방지)
        for i, line in enumerate(text.split("\n")):
            if i > 0:
                page.keyboard.press("Enter")
            page.keyboard.type(line, delay=8)


def _click_if(page, selectors, timeout=3000):
    """있으면 누르고, 없으면 조용히 넘어간다."""
    try:
        loc = _first(page, selectors, timeout=timeout)
        loc.click()
        return True
    except Exception:
        return False


# ==================== 메인 ====================

def upload(page, cfg, log):
    """
    cfg 키:
      video     : 영상 파일 경로 (필수)
      title     : 제목
      desc      : 설명
      tags      : 태그 (쉼표 구분 문자열)
      thumbnail : 썸네일 이미지 경로
      privacy   : 'public' | 'unlisted' | 'private'
      kids      : False = 아동용 아님 (기본)
    """
    video = cfg.get("video", "").strip()
    if not video or not os.path.exists(video):
        raise RuntimeError(f"영상 파일을 찾을 수 없습니다: {video}")

    # ---------- 1. 스튜디오 접속 ----------
    log("[1/8] 유튜브 스튜디오 접속 중...")
    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=90000)
    time.sleep(2)

    if "accounts.google.com" in page.url or "signin" in page.url:
        raise RuntimeError(
            "구글 로그인이 안 되어 있습니다.\n"
            "열린 크롬 창에서 유튜브에 먼저 로그인한 뒤 다시 실행하세요."
        )

    # ---------- 2. 업로드 창 열기 ----------
    log("[2/8] 업로드 창 여는 중...")
    opened = False
    try:
        page.locator("#create-icon").first.click(timeout=15000)
        time.sleep(1)
        # 메뉴 첫 항목 = '동영상 업로드'
        page.locator("tp-yt-paper-item#text-item-0, ytcp-text-menu-item#text-item-0").first.click(timeout=8000)
        opened = True
    except Exception:
        log("  - 업로드 버튼을 못 찾아 주소로 직접 이동합니다")

    if not opened:
        page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=90000)

    time.sleep(2)

    # ---------- 3. 영상 파일 선택 ----------
    log(f"[3/8] 영상 업로드 시작: {os.path.basename(video)}")
    file_input = _first(page, [
        "ytcp-uploads-file-picker input[type=file]",
        "input[type=file]#content-file-picker",
        "input[type=file]",
    ], timeout=30000, state="attached")
    file_input.set_input_files(video)

    # 상세정보 입력창이 뜰 때까지 대기
    log("  - 업로드 시작됨, 상세정보 창 대기 중...")
    title_box = _first(page, [
        "ytcp-social-suggestions-textbox#title-textarea #textbox",
        "#title-textarea #textbox",
        "ytcp-mention-textbox#title-textarea div#textbox",
        "div#textbox[contenteditable='true']",
    ], timeout=120000)

    # ---------- 4. 제목 / 설명 ----------
    log("[4/8] 제목·설명 입력 중...")
    _type_into(page, title_box, cfg.get("title", ""), log)
    time.sleep(0.5)

    desc = cfg.get("desc", "")
    if desc:
        try:
            desc_box = _first(page, [
                "ytcp-social-suggestions-textbox#description-textarea #textbox",
                "#description-textarea #textbox",
            ], timeout=15000)
            _type_into(page, desc_box, desc, log)
        except Exception as e:
            log(f"  ! 설명 입력 실패 (건너뜀): {e}")

    # ---------- 5. 썸네일 ----------
    thumb = cfg.get("thumbnail", "").strip()
    if thumb:
        if not os.path.exists(thumb):
            log(f"  ! 썸네일 파일 없음, 건너뜀: {thumb}")
        else:
            log("[5/8] 썸네일 업로드 중...")
            try:
                tin = _first(page, [
                    "ytcp-thumbnail-uploader input[type=file]",
                    "input#file-loader",
                    "input[type=file][accept*='image']",
                ], timeout=20000, state="attached")
                tin.set_input_files(thumb)
                time.sleep(3)
                log("  - 썸네일 등록 완료")
            except Exception as e:
                log(f"  ! 썸네일 업로드 실패 (건너뜀): {e}")
    else:
        log("[5/8] 썸네일 없음, 건너뜀")

    # ---------- 6. 아동용 여부 ----------
    log("[6/8] 시청자층 설정 중...")
    kids = bool(cfg.get("kids", False))
    name = "VIDEO_MADE_FOR_KIDS_MFK" if kids else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    if not _click_if(page, [f"tp-yt-paper-radio-button[name='{name}']"], timeout=10000):
        log("  ! 시청자층 선택 실패 (수동 확인 필요)")

    # ---------- 7. 태그 ----------
    tags = cfg.get("tags", "").strip()
    if tags:
        log("[7/8] 태그 입력 중...")
        try:
            # '자세히 보기' 펼치기
            _click_if(page, [
                "ytcp-button#toggle-button",
                "#toggle-button",
                "button:has-text('자세히 보기')",
                "button:has-text('SHOW MORE')",
            ], timeout=8000)
            time.sleep(1)

            tag_input = _first(page, [
                "ytcp-form-input-container#tags-container input#text-input",
                "#tags-container input",
                "input[aria-label*='태그']",
                "input[aria-label*='tag']",
            ], timeout=15000)
            tag_input.click()
            for t in [x.strip() for x in tags.split(",") if x.strip()]:
                page.keyboard.type(t, delay=10)
                page.keyboard.press("Enter")
                time.sleep(0.2)
            log("  - 태그 등록 완료")
        except Exception as e:
            log(f"  ! 태그 입력 실패 (건너뜀): {e}")
    else:
        log("[7/8] 태그 없음, 건너뜀")

    # ---------- 8. 다음 -> 공개설정 -> 게시 ----------
    log("[8/8] 다음 단계 진행 중...")
    for i in range(4):
        try:
            nxt = page.locator("#next-button").first
            if nxt.is_visible(timeout=3000):
                nxt.click()
                time.sleep(1.5)
        except Exception:
            break

    # 공개 설정
    privacy = (cfg.get("privacy") or "private").lower()
    pname = {"public": "PUBLIC", "unlisted": "UNLISTED", "private": "PRIVATE"}.get(privacy, "PRIVATE")
    log(f"  - 공개 설정: {privacy}")
    if not _click_if(page, [
        f"tp-yt-paper-radio-button[name='{pname}']",
        f"[name='{pname}']",
    ], timeout=15000):
        log("  ! 공개 설정 선택 실패 (수동 확인 필요)")
    time.sleep(1)

    # 업로드/처리 완료 대기
    _wait_processing(page, log)

    # 게시
    log("  - 게시 버튼 클릭")
    _wait_enabled(page, "#done-button", log)
    try:
        done = page.locator("#done-button").first
        done.click(timeout=30000)
    except Exception as e:
        raise RuntimeError(f"게시 버튼 클릭 실패: {e}")

    time.sleep(4)

    # 마지막 안내창 닫기
    _click_if(page, [
        "ytcp-button#close-button",
        "#close-button",
        "button:has-text('닫기')",
    ], timeout=8000)

    log("🎉 유튜브 업로드 완료!")
    return True


def _wait_processing(page, log, max_wait=3600):
    """영상 업로드(전송)가 끝날 때까지 대기. 처리(인코딩)는 기다리지 않음."""
    log("  - 영상 전송 대기 중...")
    end = time.time() + max_wait
    last = ""
    blank = 0          # 진행률 표시를 못 찾은 횟수

    while time.time() < end:
        try:
            txt = page.locator(
                "ytcp-video-upload-progress .progress-label, .progress-label"
            ).first.inner_text(timeout=3000)
            txt = " ".join(txt.split())
        except Exception:
            txt = ""

        if not txt:
            # 진행률 표시 자체가 없으면(=이미 끝났거나 UI 변경) 30초 후 진행
            blank += 1
            if blank >= 6:
                log("  - 진행률 표시 없음, 다음 단계로 진행합니다")
                return True
        else:
            blank = 0
            if txt != last:
                log(f"    {txt}")
                last = txt
            # '업로드 중 ...%' 가 사라지면 전송 완료
            if "업로드 중" not in txt and "Uploading" not in txt:
                log("  - 전송 완료")
                return True

        time.sleep(5)

    log("  ! 전송 대기 시간 초과 - 그대로 진행합니다")
    return False


def _wait_enabled(page, selector, log, max_wait=600):
    """버튼이 눌릴 수 있는 상태가 될 때까지 대기 (비활성 상태 클릭 방지)."""
    end = time.time() + max_wait
    while time.time() < end:
        try:
            el = page.locator(selector).first
            disabled = el.get_attribute("disabled")
            aria = el.get_attribute("aria-disabled")
            if disabled is None and aria != "true":
                return True
        except Exception:
            pass
        time.sleep(2)
    log(f"  ! {selector} 가 계속 비활성 상태입니다")
    return False
