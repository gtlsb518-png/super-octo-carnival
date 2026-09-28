#!/usr/bin/env python3
"""
틱톡 업로드 자동화 (틱톡 스튜디오 웹 업로드)

전체 흐름:
  1. tiktok.com/tiktokstudio/upload 접속 (로그인 확인)
  2. 영상 파일 선택
  3. 설명(캡션) 입력 — 해시태그는 제목/상세정보에 직접 적어둔 그대로 들어감
  4. 커버(썸네일) 지정   ※ 실패해도 업로드는 계속
  5. 공개 범위 설정
  6. **예약 게시** 날짜·시간 설정 (사용자 지정)
  7. 게시

안전장치:
  6단계(예약)가 실패하면 게시 버튼을 누르지 않고 중단합니다.
  (예약하려던 영상이 실수로 지금 바로 올라가는 일을 막기 위함)
"""

import os
import re
import time
from datetime import datetime, timedelta

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=ko"

# 틱톡 예약 가능 범위 (대략 20분 뒤 ~ 10일 뒤)
MIN_AHEAD_MIN = 20
MAX_AHEAD_DAYS = 10


# ==================== 화면 요소 선택자 ====================
# 틱톡이 화면을 바꾸면 여기만 고치면 된다.
# up_check.py 로 어떤 선택자가 실제로 잡히는지 확인할 수 있다.

SEL = {
    "영상 파일 입력칸": [
        "input[type=file][accept*='video']",
        "input[type=file]",
    ],
    "캡션 입력창": [
        "div[contenteditable='true'].public-DraftEditor-content",
        ".public-DraftEditor-content",
        "div[contenteditable='true'][role='combobox']",
        "div[contenteditable='true']",
    ],
    "커버 편집 버튼": [
        "button:has-text('커버 편집')", "text='커버 편집'",
        "button:has-text('Edit cover')", "text='Edit cover'",
    ],
    # 캡처 화면: 커버 편집 창 아래쪽의 '커버 업로드' 버튼 (탭이 아님)
    "커버 업로드 버튼": [
        "text='커버 업로드'",
        "text='Upload cover'",
        "button:has-text('커버 업로드')",
        "div[role='tab']:has-text('업로드')",
        "button:has-text('업로드')",
    ],
    "커버 저장 버튼": [
        "button:has-text('저장')",
        "text='저장'",
        "button:has-text('Save')",
    ],
    # 캡처 화면: '이 게시물을 볼 수 있는 사람' 아래 드롭다운 (기본값 '모두')
    "공개 범위 선택": [
        "div[class*='visibility'] div[class*='select']",
        "div[class*='select-container']",
    ],
    # 캡처 화면: '게시 시기'의 지금 / 예약 라디오 버튼
    "예약 라디오": [
        "text='예약'",
        "text='Schedule'",
        "input[type='radio'][value='schedule']",
        "[data-e2e='schedule_switch']",
        "div[class*='schedule'] input[type='radio']",
        "div[class*='switch'][class*='schedule']",
    ],
    "예약 날짜 입력칸": [
        "div[class*='date-picker'] input",
        "div[class*='DatePicker'] input",
        "input[class*='date']",
        "div[class*='schedule'] input[placeholder*='-']",
    ],
    "예약 시간 입력칸": [
        "div[class*='time-picker'] input",
        "div[class*='TimePicker'] input",
        "input[class*='time']",
        "div[class*='schedule'] input[placeholder*=':']",
    ],
    "시간 선택 패널": [
        "div[class*='timepicker']",
        "div[class*='TimePicker'][class*='panel']",
        "div[class*='time-picker-container']",
    ],
    "달력 패널": [
        "div[class*='calendar']",
        "div[class*='datepicker']",
        "div[class*='DatePicker'][class*='panel']",
    ],
    # 예약을 켜면 버튼 글자가 '게시' -> '예약' 으로 바뀐다.
    # 바로 옆의 '초안 저장' 을 누르면 안 되므로 글자를 정확히 본다.
    "게시 버튼": [
        "button:has-text('예약')",
        "button[data-e2e='post_video_button']",
        "button:has-text('게시')",
        "button:has-text('Schedule')",
        "button:has-text('Post')",
        "div[role='button']:has-text('게시')",
    ],
}


# ==================== 예약 시간 검사 ====================

def parse_schedule(sched, log=print, now=None):
    """
    'YYYY-MM-DD HH:MM' 문자열을 검사해서 datetime 으로 바꾼다.
    비어 있으면 None (= 지금 바로 게시).
    틱톡 제약(20분 뒤 ~ 10일 뒤, 5분 단위)에 맞춰 검사/보정한다.
    """
    sched = (sched or "").strip()
    if not sched:
        return None

    try:
        when = datetime.strptime(sched, "%Y-%m-%d %H:%M")
    except ValueError:
        raise RuntimeError(f"예약 시간 형식이 잘못됐습니다: {sched} (예: 2026-09-20 09:00)")

    now = now or datetime.now()
    if when < now + timedelta(minutes=MIN_AHEAD_MIN):
        raise RuntimeError(
            f"예약 시간이 너무 가깝습니다: {when:%Y-%m-%d %H:%M}\n"
            f"틱톡은 최소 {MIN_AHEAD_MIN}분 뒤부터 예약할 수 있습니다."
        )
    if when > now + timedelta(days=MAX_AHEAD_DAYS):
        raise RuntimeError(
            f"예약 시간이 너무 멉니다: {when:%Y-%m-%d %H:%M}\n"
            f"틱톡은 최대 {MAX_AHEAD_DAYS}일 뒤까지만 예약할 수 있습니다."
        )

    if when.minute % 5:
        fixed = when.replace(minute=(when.minute // 5) * 5)
        log(f"  ※ 틱톡은 5분 단위만 지원 → {when:%H:%M} 을 {fixed:%H:%M} 으로 맞춥니다")
        when = fixed

    return when


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


def _read(loc):
    """input 이면 값을, 아니면 화면 텍스트를 읽는다."""
    try:
        v = loc.input_value(timeout=2000)
        if v:
            return v.strip()
    except Exception:
        pass
    try:
        return (loc.inner_text(timeout=2000) or "").strip()
    except Exception:
        return ""


# ==================== 메인 ====================

def upload(page, cfg, log):
    """
    cfg 키:
      video    : 영상 파일 경로 (필수)
      title    : 제목 (캡션 첫 줄로 들어감, #해시태그를 여기에 직접 적음)
      desc     : 상세 설명
      cover    : 커버 이미지 경로 (선택)
      privacy  : 'public' | 'friends' | 'private'
      schedule : 'YYYY-MM-DD HH:MM' (비우면 지금 바로 게시)
    """
    video = cfg.get("video", "").strip()
    if not video or not os.path.exists(video):
        raise RuntimeError(f"영상 파일을 찾을 수 없습니다: {video}")

    # 예약 시간은 업로드 시작 전에 미리 검사 (올려놓고 실패하면 곤란하므로)
    when = parse_schedule(cfg.get("schedule"), log)

    # ---------- 1. 업로드 페이지 접속 ----------
    log("[1/7] 틱톡 업로드 페이지 접속 중...")
    page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(4)

    if "/login" in page.url:
        raise RuntimeError(
            "틱톡 로그인이 안 되어 있습니다.\n"
            "열린 크롬 창에서 틱톡에 먼저 로그인한 뒤 다시 실행하세요."
        )

    # ---------- 2. 영상 파일 선택 ----------
    log(f"[2/7] 영상 업로드 시작: {os.path.basename(video)}")
    fin, _ = _find_any(page, SEL["영상 파일 입력칸"], timeout=40000, state="attached")
    fin.set_input_files(video)
    time.sleep(5)

    # ---------- 3. 캡션(제목+설명+태그) ----------
    log("[3/7] 제목·설명 입력 중...")
    parts = []
    if cfg.get("title", "").strip():
        parts.append(cfg["title"].strip())
    if cfg.get("desc", "").strip():
        parts.append(cfg["desc"].strip())
    caption = "\n".join(parts)

    try:
        cap, fr = _find_any(page, SEL["캡션 입력창"], timeout=60000)

        cap.click()
        time.sleep(0.5)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        time.sleep(0.3)

        for i, line in enumerate(caption.split("\n")):
            if i > 0:
                page.keyboard.press("Shift+Enter")
            page.keyboard.type(line, delay=12)

        # 해시태그는 제목/상세정보 안에 직접 적어 넣는다.
        # '#' 을 치면 틱톡이 자동완성 목록을 띄우는데, 그대로 두면
        # 다음 클릭을 가로채므로 ESC 로 닫아준다.
        time.sleep(1.2)
        page.keyboard.press("Escape")
        time.sleep(0.4)
        page.keyboard.press("Escape")

        log("  - 캡션 입력 완료")
    except Exception as e:
        log(f"  ! 캡션 입력 실패: {e}")

    # ---------- 4. 커버(썸네일) ----------
    cover = cfg.get("cover", "").strip()
    if cover and os.path.exists(cover):
        log("[4/7] 커버 이미지 설정 시도 중...")
        try:
            _click_if(page, SEL["커버 편집 버튼"], timeout=8000)
            time.sleep(2)
            _click_if(page, SEL["커버 업로드 버튼"], timeout=6000)
            time.sleep(1)

            cin, _ = _find_any(page, ["input[type=file][accept*='image']"],
                               timeout=10000, state="attached")
            cin.set_input_files(cover)
            time.sleep(3)

            _click_if(page, SEL["커버 저장 버튼"], timeout=8000)
            time.sleep(2)
            log("  - 커버 설정 완료")
        except Exception as e:
            log(f"  ! 커버 설정 실패 (틱톡 UI가 바뀌었을 수 있음, 건너뜀): {e}")
    else:
        log("[4/7] 커버 없음, 건너뜀" if not cover else f"[4/7] 커버 파일 없음, 건너뜀: {cover}")

    # ---------- 5. 공개 범위 ----------
    privacy = (cfg.get("privacy") or "public").lower()
    # 캡처 화면의 라벨은 '모두' 다. 예전 표기도 같이 넣어 어느 쪽이든 찾게 한다.
    labels = {"public": ["모두", "전체 공개", "Everyone"],
              "friends": ["친구", "Friends"],
              "private": ["나만", "나만 보기", "Only you"]}.get(privacy, ["모두"])
    label = labels[0]
    log(f"[5/7] 공개 범위 설정: {label}")
    try:
        _click_if(page, SEL["공개 범위 선택"], timeout=6000)
        time.sleep(1)
        opts = []
        for lb in labels:
            opts += [f"div[role='option']:has-text('{lb}')",
                     f"li:has-text('{lb}')",
                     f"text='{lb}'"]
        if not _click_if(page, opts, timeout=6000):
            log("  ! 공개 범위 항목을 못 찾음 (기본값 사용)")
            page.keyboard.press("Escape")
    except Exception as e:
        log(f"  ! 공개 범위 설정 실패 (기본값 사용): {e}")

    # ---------- 6. 예약 게시 ----------
    if when:
        log(f"[6/7] 예약 게시 설정 중... ({when:%Y-%m-%d %H:%M})")
        # 실패하면 여기서 예외 -> 게시 버튼을 누르지 않고 중단
        _set_schedule(page, when, log)
    else:
        log("[6/7] 예약 없음 - 지금 바로 게시합니다")

    # ---------- 7. 업로드 완료 대기 후 게시 ----------
    log("[7/7] 영상 전송 완료 대기 중...")
    _wait_upload(page, log)

    log("  - 게시 버튼 클릭")
    clicked = _click_if(page, SEL["게시 버튼"], timeout=30000)

    if not clicked:
        raise RuntimeError("게시 버튼을 찾지 못했습니다. 브라우저에서 직접 확인하세요.")

    time.sleep(6)
    _click_if(page, [
        "button:has-text('확인')", "button:has-text('닫기')", "button:has-text('OK')",
    ], timeout=8000)

    if when:
        log(f"🎉 틱톡 예약 완료! ({when:%Y-%m-%d %H:%M} 게시 예정)")
    else:
        log("🎉 틱톡 업로드 완료!")
    return True


# ==================== 예약 설정 ====================

# 캡처 화면의 날짜/시간 칸은 '2026-09-28', '21:00' 글자가 적힌 드롭다운 버튼이다.
# (input 태그가 아닐 수 있어서 글자 모양으로도 찾는다)
RE_DATE = re.compile(r"^\s*\d{4}[-./]\s?\d{1,2}[-./]\s?\d{1,2}\.?\s*$")
RE_TIME = re.compile(r"^\s*\d{1,2}\s*:\s*\d{2}\s*$")


def _by_text(page, pattern, timeout=6000):
    """화면에서 그 글자 모양을 가진 요소를 찾는다."""
    end = time.time() + timeout / 1000.0
    while time.time() < end:
        for fr in _frames(page):
            try:
                loc = fr.get_by_text(pattern).first
                loc.wait_for(state="visible", timeout=400)
                return loc
            except Exception:
                continue
        time.sleep(0.3)
    return None


def _schedule_inputs(page, timeout=10000):
    """
    예약 날짜/시간 칸을 찾는다. 없으면 None (=예약이 아직 꺼져 있음).

    ① input 태그로 먼저 찾고
    ② 없으면 '2026-09-28' / '21:00' 같은 글자가 적힌 드롭다운을 찾는다.
    """
    try:
        date_in, _ = _find_any(page, SEL["예약 날짜 입력칸"], timeout=timeout // 2)
        time_in, _ = _find_any(page, SEL["예약 시간 입력칸"], timeout=timeout // 2)
        return date_in, time_in
    except Exception:
        pass

    date_el = _by_text(page, RE_DATE, timeout=timeout // 2)
    time_el = _by_text(page, RE_TIME, timeout=timeout // 2)
    if date_el is not None and time_el is not None:
        return date_el, time_el
    return None


def _set_schedule(page, when, log):
    """'예약 게시'를 켜고 날짜·시간을 지정한다. 실패하면 예외를 던진다."""

    # --- 예약 스위치 켜기 ---
    # 이미 켜져 있는데 또 누르면 꺼지므로, 날짜/시간칸이 이미 보이는지 먼저 확인한다.
    found = _schedule_inputs(page, timeout=3000)

    if found:
        log("  - '예약 게시'가 이미 켜져 있습니다")
    else:
        log("  - '예약 게시' 켜는 중...")
        turned_on = _click_if(page, SEL["예약 라디오"], timeout=15000)
        if not turned_on:
            raise RuntimeError(
                "'예약 게시' 스위치를 찾지 못했습니다.\n"
                "틱톡 화면이 바뀌었을 수 있습니다. 영상은 게시되지 않았습니다."
            )
        time.sleep(3)
        found = _schedule_inputs(page, timeout=15000)

    if not found:
        raise RuntimeError(
            "예약 날짜/시간 입력칸을 찾지 못했습니다.\n"
            "'예약 게시'가 켜지지 않았을 수 있습니다. 영상은 게시되지 않았습니다."
        )
    date_in, time_in = found

    # --- 시간 먼저 (날짜를 바꾸면 시간 선택지가 달라질 수 있음) ---
    log(f"  - 시간 선택: {when:%H:%M}")
    _pick_time(page, time_in, when, log)

    # --- 날짜 ---
    log(f"  - 날짜 선택: {when:%Y-%m-%d}")
    _pick_date(page, date_in, when, log)

    # --- 확인 ---
    d, t = _read(date_in), _read(time_in)
    log(f"  - 화면 표시값 → 날짜 '{d}' / 시간 '{t}'")

    if str(when.day) not in d and f"{when.day:02d}" not in d:
        raise RuntimeError(
            f"예약 날짜가 제대로 들어가지 않았습니다 (화면: '{d}').\n"
            "영상은 게시되지 않았습니다. 브라우저에서 직접 확인하세요."
        )
    # 시와 분을 함께 본다 (시만 보면 21:40 같은 값도 통과해버림)
    tnums = re.findall(r"\d{1,2}", t)
    want_h = {f"{when.hour:02d}", str(when.hour), f"{when.hour % 12 or 12:02d}", str(when.hour % 12 or 12)}
    want_m = {f"{when.minute:02d}", str(when.minute)}
    if not (any(x in want_h for x in tnums[:1] or tnums) and any(x in want_m for x in tnums)):
        raise RuntimeError(
            f"예약 시간이 제대로 들어가지 않았습니다 (화면: '{t}', 원하는 값: {when:%H:%M}).\n"
            "영상은 게시되지 않았습니다. 브라우저에서 직접 확인하세요."
        )

    log("  ✅ 예약 설정 완료")
    return True


def _pick_time(page, time_in, when, log):
    """
    틱톡 시간 선택기에서 시·분을 고른다.

    캡처 화면 기준: 패널 안에 [시] : [분] 두 줄이 나란히 있고
    분은 5분 단위(00, 05, 10 ...)다.
    클래스 이름은 자주 바뀌므로 '화면상의 위치'로 어느 줄인지 판단한다.
    """
    time_in.click()
    time.sleep(2)

    panel = None
    for sel in SEL["시간 선택 패널"]:
        try:
            loc, _ = _find_any(page, [sel], timeout=3000)
            panel = loc
            break
        except Exception:
            continue
    if panel is None:
        panel = page

    hh, mm = f"{when.hour:02d}", f"{when.minute:02d}"

    def click_in_column(value, want_left):
        """
        패널 안에서 그 숫자가 적힌 칸을 찾아, 시 칸인지 분 칸인지 가려서 누른다.

        판단 순서
          ① 부모 클래스 이름에 left/hour, right/minute 가 있으면 그걸로
          ② 두 줄이 좌우로 떨어져 있으면 x 좌표로
          ③ 둘 다 아니면 문서 순서로 (앞쪽=시, 뒤쪽=분)
        """
        try:
            items = panel.get_by_text(re.compile(rf"^\s*{value}\s*$"))
            n = items.count()
        except Exception:
            return False
        if n == 0:
            return False

        found = []
        for i in range(min(n, 24)):
            it = items.nth(i)
            try:
                box = it.bounding_box()
                cls = (it.evaluate("e => (e.className || '') + ' ' + "
                                   "((e.parentElement && e.parentElement.className) || '')") or "").lower()
            except Exception:
                continue
            if box:
                found.append((box["x"] + box["width"] / 2, cls, it))

        if not found:
            return False

        # ① 클래스 이름으로
        key = ("left", "hour") if want_left else ("right", "minute")
        named = [it for _, cls, it in found if any(k in cls for k in key)]
        if named:
            target = named[0]
        else:
            xs = [x for x, _, _ in found]
            if len(found) > 1 and max(xs) - min(xs) >= 20:
                # ② 좌우로 떨어져 있으면 위치로
                mid = (min(xs) + max(xs)) / 2
                side = [it for x, _, it in found if (x <= mid if want_left else x > mid)]
                target = side[0] if side else found[0][2]
            else:
                # ③ 문서 순서로 (시가 먼저, 분이 나중)
                target = found[0][2] if want_left else found[-1][2]

        try:
            target.scroll_into_view_if_needed(timeout=3000)
            target.click(timeout=3000)
            return True
        except Exception:
            return False

    ok_h = click_in_column(hh, want_left=True)
    time.sleep(1)

    # 시를 고르면 패널이 닫히는 경우가 있다. 닫혔으면 다시 연다.
    try:
        if panel is not page and not panel.is_visible(timeout=1500):
            time_in.click()
            time.sleep(1.5)
    except Exception:
        try:
            time_in.click()
            time.sleep(1.5)
        except Exception:
            pass

    ok_m = click_in_column(mm, want_left=False)
    time.sleep(1)

    if not (ok_h and ok_m):
        log(f"    선택기 클릭 실패(시:{ok_h} 분:{ok_m}) → 직접 입력 시도")
        try:
            time_in.click()
            page.keyboard.press("Control+a")
            page.keyboard.type(f"{hh}:{mm}", delay=40)
            page.keyboard.press("Enter")
        except Exception:
            pass

    page.keyboard.press("Escape")
    time.sleep(1)


def _pick_date(page, date_in, when, log):
    """틱톡 달력에서 날짜를 고른다 (필요하면 다음 달로 넘김)."""
    date_in.click()
    time.sleep(2)

    cal, fr = _find_any(page, SEL["달력 패널"], timeout=10000)

    # 목표 달이 나올 때까지 '다음 달' 화살표 클릭 (최대 12번)
    for _ in range(12):
        header = ""
        try:
            header = " ".join(cal.inner_text(timeout=2000).split())[:60]
        except Exception:
            pass

        # 헤더에 목표 연·월이 보이면 멈춤 (한국어/영어 모두 대응)
        month_names = {
            1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
            7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
        }
        hit = (
            (str(when.year) in header and f"{when.month}월" in header)
            or (str(when.year) in header and month_names[when.month] in header)
            or (f"{when.year}-{when.month:02d}" in header)
        )
        if hit:
            break

        moved = False
        for sel in ["[class*='arrow-right']", "[class*='next']", "button:nth-of-type(2)"]:
            try:
                cal.locator(sel).first.click(timeout=1500)
                moved = True
                break
            except Exception:
                continue
        if not moved:
            log(f"    달 이동 화살표를 못 찾음 (헤더: '{header}')")
            break
        time.sleep(1)

    # 날짜 칸 클릭
    day = str(when.day)
    clicked = False
    for sel in [
        "[class*='day']:not([class*='disable']):not([class*='other'])",
        "td:not([class*='disable'])",
        "[class*='date-item']",
    ]:
        try:
            cells = cal.locator(sel)
            n = cells.count()
            for i in range(n):
                c = cells.nth(i)
                try:
                    if (c.inner_text(timeout=800) or "").strip() == day:
                        c.click(timeout=2000)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
        except Exception:
            continue

    if not clicked:
        log("    달력에서 날짜를 못 찾음 → 직접 입력 시도")
        try:
            date_in.click()
            page.keyboard.press("Control+a")
            page.keyboard.type(f"{when:%Y-%m-%d}", delay=40)
            page.keyboard.press("Enter")
        except Exception:
            pass

    page.keyboard.press("Escape")
    time.sleep(1)


# ==================== 업로드 진행 대기 ====================

def _wait_upload(page, log, max_wait=3600):
    """전송 중 표시가 사라질 때까지 대기."""
    end = time.time() + max_wait
    last = ""
    blank = 0

    while time.time() < end:
        txt = ""
        # 'text=A, text=B' 는 한 덩어리로 취급되어 동작하지 않으므로 따로 시도한다
        for fr in _frames(page):
            for sel in ("text=업로드 중", "text=Uploading"):
                try:
                    el = fr.locator(sel).last
                    txt = " ".join(el.inner_text(timeout=1500).split())[:60]
                    if txt:
                        break
                except Exception:
                    continue
            if txt:
                break

        if not txt:
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
