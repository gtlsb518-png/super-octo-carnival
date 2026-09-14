#!/usr/bin/env python3
"""
유튜브 업로드 자동화 (유튜브 스튜디오)

전체 흐름:
  1. 영상 업로드 + 제목/설명/태그/썸네일 입력
  2. **일부공개**로 먼저 게시        <- 댓글을 달려면 영상이 올라가 있어야 함
  3. 영상 페이지에서 고정 댓글 작성 -> 고정
  4. 스튜디오로 돌아가 **예약**으로 변경 (예약 시간은 사용자 지정)

안전장치:
  3·4단계가 실패해도 영상은 '일부공개' 상태로 남습니다.
  (실수로 전체공개 되는 일은 없습니다)
"""

import os
import re
import time
from datetime import datetime

# ==================== 날짜/시간 입력 형식 ====================
# 유튜브 스튜디오 언어 설정에 따라 받아들이는 형식이 다르다.
# 아래 순서대로 넣어보고, 입력칸이 값을 그대로 유지하는 형식을 사용한다.
# (형식이 틀리면 유튜브가 원래 값으로 되돌려버림 -> 그걸로 성공/실패를 판단)

DATE_FORMATS = [
    "{Y}. {m}. {d}.",        # 한국어: 2026. 9. 20.
    "{Y}. {m:02d}. {d:02d}.",
    "{b} {d}, {Y}",          # 영어: Sep 20, 2026 / Jan 2, 2026
    "{b} {d:02d}, {Y}",      # 영어(0 채움): Jan 02, 2026
    "{m}/{d}/{Y}",           # 미국식: 9/20/2026
    "{Y}-{m:02d}-{d:02d}",   # 2026-09-20
]

TIME_FORMATS = [
    "{ampm_ko} {h12}:{M:02d}",   # 한국어: 오후 11:00
    "{h12}:{M:02d} {ampm_en}",   # 영어: 11:00 PM
    "{H:02d}:{M:02d}",           # 24시간: 23:00
]

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _date_candidates(dt):
    out = []
    for f in DATE_FORMATS:
        v = f.format(Y=dt.year, m=dt.month, d=dt.day, b=MONTH_ABBR[dt.month - 1])
        if v not in out:
            out.append(v)
    return out


def _time_candidates(dt):
    h12 = dt.hour % 12 or 12
    out = []
    for f in TIME_FORMATS:
        out.append(f.format(
            h12=h12, H=dt.hour, M=dt.minute,
            ampm_ko="오전" if dt.hour < 12 else "오후",
            ampm_en="AM" if dt.hour < 12 else "PM",
        ))
    return out


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


def _wait_enabled(page, selector, log, max_wait=600):
    """버튼이 눌릴 수 있는 상태가 될 때까지 대기 (비활성 상태 클릭 방지)."""
    end = time.time() + max_wait
    while time.time() < end:
        try:
            el = page.locator(selector).first
            if el.get_attribute("disabled") is None and el.get_attribute("aria-disabled") != "true":
                return True
        except Exception:
            pass
        time.sleep(2)
    log(f"  ! {selector} 가 계속 비활성 상태입니다")
    return False


def _nums(text):
    """문자열에서 숫자만 뽑아낸다."""
    return [int(x) for x in re.findall(r"\d+", text or "")]


def _date_ok(got, when):
    """입력칸에 남은 값이 원하는 날짜인지 확인 (표기 형식은 달라도 됨)."""
    ns = _nums(got)
    if when.year not in ns or when.day not in ns:
        return False
    if when.month in ns:
        return True
    # 영어 표기: 'Sep 20, 2026'
    return MONTH_ABBR[when.month - 1].lower() in (got or "").lower()


def _time_ok(got, when):
    """입력칸에 남은 값이 원하는 시각인지 확인 (12/24시간 표기 모두 허용)."""
    ns = _nums(got)
    if when.minute not in ns:
        return False
    return when.hour in ns or (when.hour % 12 or 12) in ns


def _fill_verify(page, loc, value, check, log):
    """
    입력칸에 값을 넣고, 유튜브가 그 값을 받아들였는지 확인한다.
    형식이 틀리면 유튜브가 원래 값으로 되돌리므로, 되돌아온 값을 보고 판단한다.
    (유튜브가 형식을 바꿔서 다시 표시해도 숫자가 맞으면 성공으로 본다)
    """
    try:
        loc.click()
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        page.keyboard.type(value, delay=30)
        page.keyboard.press("Enter")
        time.sleep(1.2)
        got = (loc.input_value(timeout=3000) or "").strip()
    except Exception as e:
        log(f"    입력 실패({value}): {e}")
        return False, ""

    return check(got), got


# ==================== 메인 ====================

def upload(page, cfg, log):
    """
    cfg 키:
      video       : 영상 파일 경로 (필수)
      title       : 제목
      desc        : 설명
      tags        : 태그 (쉼표 구분)
      thumbnail   : 썸네일 이미지 경로
      pin_comment : 고정할 댓글 내용 (비우면 건너뜀)
      schedule    : 'YYYY-MM-DD HH:MM' (비우면 일부공개로 유지)
      kids        : True = 아동용
    """
    video = cfg.get("video", "").strip()
    if not video or not os.path.exists(video):
        raise RuntimeError(f"영상 파일을 찾을 수 없습니다: {video}")

    # ---------- 1. 스튜디오 접속 ----------
    log("[1/10] 유튜브 스튜디오 접속 중...")
    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=90000)
    time.sleep(2)

    if "accounts.google.com" in page.url or "signin" in page.url:
        raise RuntimeError(
            "구글 로그인이 안 되어 있습니다.\n"
            "열린 크롬 창에서 유튜브에 먼저 로그인한 뒤 다시 실행하세요."
        )

    # ---------- 2. 업로드 창 열기 ----------
    log("[2/10] 업로드 창 여는 중...")
    opened = False
    try:
        page.locator("#create-icon").first.click(timeout=15000)
        time.sleep(1)
        page.locator("tp-yt-paper-item#text-item-0, ytcp-text-menu-item#text-item-0").first.click(timeout=8000)
        opened = True
    except Exception:
        log("  - 업로드 버튼을 못 찾아 주소로 직접 이동합니다")

    if not opened:
        page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=90000)
    time.sleep(2)

    # ---------- 3. 영상 파일 선택 ----------
    log(f"[3/10] 영상 업로드 시작: {os.path.basename(video)}")
    file_input = _first(page, [
        "ytcp-uploads-file-picker input[type=file]",
        "input[type=file]#content-file-picker",
        "input[type=file]",
    ], timeout=30000, state="attached")
    file_input.set_input_files(video)

    log("  - 업로드 시작됨, 상세정보 창 대기 중...")
    title_box = _first(page, [
        "ytcp-social-suggestions-textbox#title-textarea #textbox",
        "#title-textarea #textbox",
        "ytcp-mention-textbox#title-textarea div#textbox",
        "div#textbox[contenteditable='true']",
    ], timeout=120000)

    # ---------- 4. 제목 / 설명 ----------
    log("[4/10] 제목·설명 입력 중...")
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
    if thumb and os.path.exists(thumb):
        log("[5/10] 썸네일 업로드 중...")
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
        if thumb:
            log(f"[5/10] 썸네일 파일 없음, 건너뜀: {thumb}")
        else:
            log("[5/10] 썸네일 없음, 건너뜀")

    # ---------- 6. 아동용 여부 ----------
    log("[6/10] 시청자층 설정 중...")
    kids = bool(cfg.get("kids", False))
    name = "VIDEO_MADE_FOR_KIDS_MFK" if kids else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
    if not _click_if(page, [f"tp-yt-paper-radio-button[name='{name}']"], timeout=10000):
        log("  ! 시청자층 선택 실패 (수동 확인 필요)")

    # ---------- 7. 태그 ----------
    tags = cfg.get("tags", "").strip()
    if tags:
        log("[7/10] 태그 입력 중...")
        try:
            _click_if(page, [
                "ytcp-button#toggle-button", "#toggle-button",
                "button:has-text('자세히 보기')", "button:has-text('SHOW MORE')",
            ], timeout=8000)
            time.sleep(1)
            tag_input = _first(page, [
                "ytcp-form-input-container#tags-container input#text-input",
                "#tags-container input",
                "input[aria-label*='태그']", "input[aria-label*='tag']",
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
        log("[7/10] 태그 없음, 건너뜀")

    # ---------- 8. '일부공개'로 게시 ----------
    log("[8/10] 다음 단계 진행 중...")
    for _ in range(4):
        try:
            nxt = page.locator("#next-button").first
            if nxt.is_visible(timeout=3000):
                nxt.click()
                time.sleep(1.5)
        except Exception:
            break

    log("  - 공개 설정: 일부공개 (댓글 작성용)")
    if not _click_if(page, [
        "tp-yt-paper-radio-button[name='UNLISTED']", "[name='UNLISTED']",
    ], timeout=15000):
        raise RuntimeError("'일부공개' 선택에 실패했습니다. 브라우저에서 직접 확인하세요.")
    time.sleep(1)

    _wait_processing(page, log)

    # 게시 전에 영상 주소를 먼저 확보 (게시 후엔 창이 닫힐 수 있음)
    vid = _get_video_id(page, log)

    log("  - 게시 버튼 클릭")
    _wait_enabled(page, "#done-button", log)
    try:
        page.locator("#done-button").first.click(timeout=30000)
    except Exception as e:
        raise RuntimeError(f"게시 버튼 클릭 실패: {e}")
    time.sleep(4)

    if not vid:
        vid = _get_video_id(page, log)

    _click_if(page, [
        "ytcp-button#close-button", "#close-button", "button:has-text('닫기')",
    ], timeout=8000)

    log(f"✅ 일부공개로 게시 완료 (영상 ID: {vid or '확인 실패'})")

    if not vid:
        log("  ! 영상 주소를 못 찾아 댓글·예약 단계를 건너뜁니다.")
        log("  ! 영상은 '일부공개' 상태입니다. 댓글/예약은 직접 설정하세요.")
        return True

    # ---------- 9. 댓글 작성 + 고정 ----------
    pin_text = (cfg.get("pin_comment") or "").strip()
    if pin_text:
        log("[9/10] 고정 댓글 작성 중...")
        try:
            _post_and_pin_comment(page, vid, pin_text, log)
        except Exception as e:
            log(f"  ! 댓글 작성/고정 실패: {e}")
            log("  ! 영상은 '일부공개' 상태입니다. 댓글은 직접 달아주세요.")
    else:
        log("[9/10] 고정 댓글 없음, 건너뜀")

    # ---------- 10. 예약으로 변경 ----------
    sched = (cfg.get("schedule") or "").strip()
    if sched:
        log(f"[10/10] 예약으로 변경 중... ({sched})")
        try:
            when = datetime.strptime(sched, "%Y-%m-%d %H:%M")
        except ValueError:
            raise RuntimeError(f"예약 시간 형식이 잘못됐습니다: {sched} (예: 2026-09-20 09:00)")
        _set_schedule(page, vid, when, log)
    else:
        log("[10/10] 예약 시간 없음 - '일부공개' 상태로 둡니다")

    log("🎉 유튜브 작업 완료!")
    return True


# ==================== 영상 ID 찾기 ====================

def _get_video_id(page, log):
    """업로드 창에 표시된 영상 링크에서 영상 ID(11자)를 뽑아낸다."""
    selectors = [
        "a#share-url",
        ".video-url-fadeable a",
        "ytcp-video-info a",
        "a[href*='youtu.be/']",
        "a[href*='/watch?v=']",
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            href = el.get_attribute("href", timeout=1500) or ""
            txt = el.inner_text(timeout=1000) or ""
        except Exception:
            continue
        for cand in (href, txt):
            m = re.search(r"(?:youtu\.be/|v=|video/)([A-Za-z0-9_-]{11})", cand)
            if m:
                return m.group(1)
    return None


# ==================== 댓글 작성 + 고정 ====================

def _post_and_pin_comment(page, vid, text, log):
    page.goto(f"https://www.youtube.com/watch?v={vid}", wait_until="domcontentloaded", timeout=90000)
    time.sleep(5)

    # 댓글창은 스크롤을 내려야 로드된다
    log("  - 댓글창 여는 중...")
    box = None
    for _ in range(10):
        page.mouse.wheel(0, 900)
        time.sleep(1.2)
        try:
            box = page.locator("#simplebox-placeholder").first
            box.wait_for(state="visible", timeout=1500)
            break
        except Exception:
            box = None

    if box is None:
        raise RuntimeError("댓글 입력창을 찾지 못했습니다 (댓글이 꺼져 있거나 아직 처리 중)")

    box.click()
    time.sleep(1)

    editor = _first(page, [
        "#contenteditable-root",
        "ytd-commentbox #contenteditable-root",
        "div#contenteditable-root[contenteditable='true']",
    ], timeout=15000)
    editor.click()
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            page.keyboard.press("Shift+Enter")
        page.keyboard.type(line, delay=10)
    time.sleep(0.5)

    log("  - 댓글 등록 중...")
    if not _click_if(page, [
        "ytd-commentbox #submit-button button",
        "ytd-commentbox #submit-button",
        "#submit-button button",
    ], timeout=15000):
        raise RuntimeError("댓글 등록 버튼을 찾지 못했습니다")
    time.sleep(6)

    # 내 댓글이 목록에 뜰 때까지 새로고침하며 대기
    log("  - 등록된 댓글 확인 중...")
    key = text.strip().split("\n")[0][:25]
    thread = None
    for attempt in range(4):
        page.reload(wait_until="domcontentloaded", timeout=90000)
        time.sleep(4)
        for _ in range(10):
            page.mouse.wheel(0, 900)
            time.sleep(1.0)
            try:
                if page.locator("ytd-comment-thread-renderer").count() > 0:
                    break
            except Exception:
                pass

        n = page.locator("ytd-comment-thread-renderer").count()
        for i in range(min(n, 15)):
            t = page.locator("ytd-comment-thread-renderer").nth(i)
            try:
                if key in t.inner_text(timeout=2000):
                    thread = t
                    break
            except Exception:
                continue
        if thread:
            break
        log(f"    아직 안 보임, 다시 확인 ({attempt + 1}/4)")

    if thread is None:
        raise RuntimeError("등록한 댓글을 목록에서 찾지 못했습니다")

    # ⋮ 메뉴 -> 고정
    log("  - 댓글 고정 중...")
    thread.scroll_into_view_if_needed()
    thread.hover()
    time.sleep(1)

    menu = None
    for sel in ["#action-menu button", "ytd-menu-renderer #button", "#action-menu yt-icon-button"]:
        try:
            m = thread.locator(sel).first
            m.wait_for(state="visible", timeout=2000)
            menu = m
            break
        except Exception:
            continue
    if menu is None:
        raise RuntimeError("댓글 메뉴(⋮) 버튼을 찾지 못했습니다")
    menu.click()
    time.sleep(1.5)

    pinned = False
    for sel in [
        "ytd-menu-service-item-renderer:has-text('고정')",
        "tp-yt-paper-item:has-text('고정')",
        "ytd-menu-service-item-renderer:has-text('Pin')",
        "tp-yt-paper-item:has-text('Pin')",
    ]:
        try:
            page.locator(sel).first.click(timeout=2500)
            pinned = True
            break
        except Exception:
            continue
    if not pinned:
        page.keyboard.press("Escape")
        raise RuntimeError("'고정' 메뉴를 찾지 못했습니다")

    time.sleep(1.5)
    # 확인 창
    _click_if(page, [
        "yt-confirm-dialog-renderer #confirm-button",
        "#confirm-button button",
        "#confirm-button",
        "button:has-text('고정')",
    ], timeout=8000)
    time.sleep(2)
    log("  ✅ 댓글 작성 + 고정 완료")
    return True


# ==================== 예약으로 변경 ====================

def _set_schedule(page, vid, when, log):
    """스튜디오 편집 화면에서 공개 상태를 '예약'으로 바꾼다."""
    if when <= datetime.now():
        raise RuntimeError(f"예약 시간이 현재보다 과거입니다: {when:%Y-%m-%d %H:%M}")

    page.goto(f"https://studio.youtube.com/video/{vid}/edit",
              wait_until="domcontentloaded", timeout=90000)
    time.sleep(5)

    # 공개 상태 드롭다운 열기
    log("  - 공개 상태 창 여는 중...")
    opened = _click_if(page, [
        "ytcp-video-metadata-visibility ytcp-dropdown-trigger",
        "ytcp-video-metadata-visibility",
        "#visibility-container ytcp-dropdown-trigger",
        "ytcp-form-select#privacy-form",
        "#privacy-form ytcp-dropdown-trigger",
    ], timeout=20000)
    if not opened:
        raise RuntimeError("공개 상태 드롭다운을 찾지 못했습니다")
    time.sleep(2.5)

    # '예약' 선택
    log("  - '예약' 선택 중...")
    if not _click_if(page, [
        "tp-yt-paper-radio-button[name='SCHEDULE']",
        "ytcp-video-visibility-scheduler tp-yt-paper-radio-button",
        "#second-container tp-yt-paper-radio-button",
        "tp-yt-paper-radio-button:has-text('예약')",
        "tp-yt-paper-radio-button:has-text('Schedule')",
    ], timeout=15000):
        raise RuntimeError("'예약' 항목을 찾지 못했습니다")
    time.sleep(2)

    # 날짜 입력
    log(f"  - 날짜 입력: {when:%Y-%m-%d}")
    date_in = _first(page, [
        "ytcp-date-picker input",
        "#datepicker-trigger input",
        "ytcp-text-dropdown-trigger#datepicker-trigger input",
        "input[aria-label*='날짜']",
        "input[aria-label*='date']",
    ], timeout=15000)

    ok = False
    for cand in _date_candidates(when):
        ok, got = _fill_verify(page, date_in, cand, lambda g: _date_ok(g, when), log)
        log(f"    '{cand}' -> 입력칸: '{got}' {'✔' if ok else '✘'}")
        if ok:
            break
    if not ok:
        raise RuntimeError(
            "날짜 형식을 유튜브가 받아들이지 않았습니다.\n"
            "up_youtube.py 의 DATE_FORMATS 에 스튜디오 화면에 보이는 형식을 추가하세요."
        )

    # 시간 입력
    log(f"  - 시간 입력: {when:%H:%M}")
    try:
        time_in = _first(page, [
            "ytcp-time-of-day input",
            "#time-of-day-container input",
            "input[aria-label*='시간']",
            "input[aria-label*='time']",
        ], timeout=10000)

        ok = False
        for cand in _time_candidates(when):
            ok, got = _fill_verify(page, time_in, cand, lambda g: _time_ok(g, when), log)
            log(f"    '{cand}' -> 입력칸: '{got}' {'✔' if ok else '✘'}")
            if ok:
                break
        if not ok:
            raise RuntimeError(
                "시간 형식을 유튜브가 받아들이지 않았습니다.\n"
                "up_youtube.py 의 TIME_FORMATS 에 스튜디오 화면에 보이는 형식을 추가하세요."
            )
    except RuntimeError:
        raise
    except Exception as e:
        log(f"  ! 시간 입력칸을 못 찾음 (날짜만 적용됨): {e}")

    time.sleep(1)

    # 저장
    log("  - 저장 중...")
    if not _click_if(page, [
        "ytcp-button#save-button", "#save-button",
        "ytcp-button#done-button", "#done-button",
        "button:has-text('예약')", "button:has-text('저장')",
    ], timeout=15000):
        raise RuntimeError("저장 버튼을 찾지 못했습니다")
    time.sleep(4)

    log(f"  ✅ 예약 설정 완료: {when:%Y-%m-%d %H:%M}")
    return True


# ==================== 업로드 진행률 대기 ====================

def _wait_processing(page, log, max_wait=3600):
    """영상 업로드(전송)가 끝날 때까지 대기. 처리(인코딩)는 기다리지 않음."""
    log("  - 영상 전송 대기 중...")
    end = time.time() + max_wait
    last = ""
    blank = 0

    while time.time() < end:
        try:
            txt = page.locator(
                "ytcp-video-upload-progress .progress-label, .progress-label"
            ).first.inner_text(timeout=3000)
            txt = " ".join(txt.split())
        except Exception:
            txt = ""

        if not txt:
            blank += 1
            if blank >= 6:
                log("  - 진행률 표시 없음, 다음 단계로 진행합니다")
                return True
        else:
            blank = 0
            if txt != last:
                log(f"    {txt}")
                last = txt
            if "업로드 중" not in txt and "Uploading" not in txt:
                log("  - 전송 완료")
                return True

        time.sleep(5)

    log("  ! 전송 대기 시간 초과 - 그대로 진행합니다")
    return False
