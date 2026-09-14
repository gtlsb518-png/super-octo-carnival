#!/usr/bin/env python3
"""
화면 진단 도구

유튜브/틱톡이 화면을 바꿔서 업로드가 중간에 멈출 때,
'어떤 요소를 못 찾는지'를 정확히 알아내기 위한 도구입니다.

사용법:
  1. [② 업로드용 크롬 열기] 로 연 창에서
     문제가 생긴 화면을 직접 띄워둡니다.
     (예: 틱톡 업로드 화면에서 '예약 게시'를 켠 상태)
  2. python up_check.py   실행
  3. '진단결과' 폴더에 생긴 파일 3개를 개발자에게 전달

만들어지는 파일:
  화면_*.png   - 그 순간 화면 사진
  요소_*.txt   - 어떤 선택자가 먹고 안 먹는지 + 후보 요소 목록
  전체_*.html  - 페이지 전체 HTML (정확한 수정용)
"""

import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import up_browser
import up_youtube
import up_tiktok

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "진단결과")

# 후보 요소를 찾을 때 쓸 단어
KEYWORDS = {
    "youtube": ["예약", "Schedule", "공개", "고정", "Pin", "저장", "게시"],
    "tiktok": ["예약", "Schedule", "게시", "Post", "커버", "Cover", "공개", "업로드"],
}

# 페이지에서 눈에 띄는 요소를 훑어오는 스크립트
JS_CANDIDATES = """
(keywords) => {
  const want = ['id','class','data-e2e','role','aria-label','name','type','placeholder','contenteditable'];
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    const own = Array.from(el.childNodes)
        .filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (!own || own.length > 40) continue;
    if (!keywords.some(k => own.includes(k))) continue;
    const attrs = Array.from(el.attributes)
        .filter(a => want.includes(a.name))
        .map(a => a.name + '="' + a.value.slice(0, 80) + '"').join(' ');
    out.push('<' + el.tagName.toLowerCase() + (attrs ? ' ' + attrs : '') + '>   "' + own + '"');
  }
  return out.slice(0, 150);
}
"""

JS_INPUTS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('input, textarea, [contenteditable="true"]')) {
    const want = ['id','class','data-e2e','type','name','placeholder','aria-label','value','readonly'];
    const attrs = Array.from(el.attributes)
        .filter(a => want.includes(a.name))
        .map(a => a.name + '="' + a.value.slice(0, 80) + '"').join(' ');
    out.push('<' + el.tagName.toLowerCase() + (attrs ? ' ' + attrs : '') + '>');
  }
  return out.slice(0, 100);
}
"""


def _count(fr, sel, cap=20):
    """선택자에 걸리는 요소 개수 (전체, 화면에 보이는 것).

    숨어 있는 요소도 DOM 에는 잡히므로 둘을 나눠 센다.
    (파일 입력칸처럼 원래 숨어 있는 게 정상인 것도 있다)
    """
    try:
        loc = fr.locator(sel)
        n = loc.count()
    except Exception:
        return 0, 0

    vis = 0
    for i in range(min(n, cap)):
        try:
            if loc.nth(i).is_visible(timeout=300):
                vis += 1
        except Exception:
            pass
    return n, vis


def _site_of(url):
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u:
        return "youtube"
    return None


def check_page(page, log=print, label=None):
    """열려 있는 탭 하나를 진단해서 파일 3개를 만든다."""
    site = _site_of(page.url)
    if not site:
        log(f"  건너뜀 (유튜브/틱톡 화면이 아님): {page.url[:70]}")
        return None

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    base = f"{site}_{label}_{stamp}" if label else f"{site}_{stamp}"

    lines = []
    lines.append("=" * 70)
    lines.append(f"진단 시각 : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"대상      : {site}" + (f"  ({label})" if label else ""))
    lines.append(f"주소      : {page.url}")
    lines.append("=" * 70)

    # ---------- 1. 선택자별 매칭 결과 ----------
    sel_map = up_tiktok.SEL if site == "tiktok" else up_youtube.SEL
    frames = [page] + [f for f in page.frames if f is not page.main_frame]

    lines.append("")
    lines.append("[ 선택자 점검 ]  ✔=화면에 보임  △=있지만 안 보임  ✘=아예 없음")
    lines.append("")

    missing = []       # 아예 없는 항목
    hidden_only = []   # DOM 에는 있는데 화면에 안 보이는 항목

    for name, selectors in sel_map.items():
        tot_all, vis_all = 0, 0
        rows = []
        for sel in selectors:
            n, v = 0, 0
            for fr in frames:
                a, b = _count(fr, sel)
                n += a
                v += b
            tot_all += n
            vis_all += v
            mark = "✔" if v else ("△" if n else "✘")
            rows.append(f"    {mark} {n:>3}개(보임 {v})  {sel}")

        mark = "✔" if vis_all else ("△" if tot_all else "✘")
        note = ""
        if not tot_all:
            missing.append(name)
        elif not vis_all:
            hidden_only.append(name)
            note = "   ← 있긴 한데 화면에 안 보임 (이 화면이 아닐 수 있음)"

        lines.append(f"  {mark} {name}{note}")
        lines.extend(rows)
        lines.append("")

    # ---------- 2. 후보 요소 ----------
    lines.append("=" * 70)
    lines.append("[ 화면에 있는 요소들 (선택자 고칠 때 참고) ]")
    lines.append("")
    try:
        cands = page.evaluate(JS_CANDIDATES, KEYWORDS[site])
        lines.extend("  " + c for c in cands) if cands else lines.append("  (없음)")
    except Exception as e:
        lines.append(f"  조회 실패: {e}")

    lines.append("")
    lines.append("[ 입력칸 목록 ]")
    lines.append("")
    try:
        inputs = page.evaluate(JS_INPUTS)
        lines.extend("  " + c for c in inputs) if inputs else lines.append("  (없음)")
    except Exception as e:
        lines.append(f"  조회 실패: {e}")

    # ---------- 3. 저장 ----------
    txt_path = os.path.join(OUT_DIR, f"요소_{base}.txt")
    png_path = os.path.join(OUT_DIR, f"화면_{base}.png")
    html_path = os.path.join(OUT_DIR, f"전체_{base}.html")

    with open(txt_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))

    try:
        page.screenshot(path=png_path, full_page=False)
    except Exception as e:
        log(f"  ! 화면 사진 실패: {e}")
        png_path = None

    try:
        with open(html_path, "w", encoding="utf-8") as fp:
            fp.write(page.content())
    except Exception as e:
        log(f"  ! HTML 저장 실패: {e}")
        html_path = None

    # ---------- 4. 요약 ----------
    log(f"  [{label or site}] {page.url[:60]}")
    if missing:
        log(f"    ✘ 없는 항목 {len(missing)}개: {', '.join(missing)}")
    if hidden_only:
        log(f"    △ 안 보이는 항목 {len(hidden_only)}개: {', '.join(hidden_only)}")
    if not missing and not hidden_only:
        log("    ✔ 모든 항목을 찾았습니다")
    log(f"    저장: {os.path.basename(txt_path)}")

    return {"site": site, "label": label, "missing": missing,
            "hidden_only": hidden_only, "txt": txt_path,
            "png": png_path, "html": html_path}


def run(log=print):
    """열려 있는 크롬의 모든 탭 중 유튜브/틱톡 화면을 진단한다."""
    from playwright.sync_api import sync_playwright

    log("=" * 60)
    log("🔍 화면 진단 시작")
    log("=" * 60)

    results = []
    with sync_playwright() as p:
        browser, context = up_browser.attach(p, log=log)

        pages = [pg for pg in context.pages if _site_of(pg.url)]
        if not pages:
            log("")
            log("❌ 유튜브/틱톡 화면이 열려 있는 탭이 없습니다.")
            log("   [② 업로드용 크롬 열기] 로 연 창에서")
            log("   문제가 생긴 화면을 먼저 띄워둔 뒤 다시 실행하세요.")
            return []

        log(f"진단할 탭 {len(pages)}개")
        log("")
        for pg in pages:
            try:
                r = check_page(pg, log=log)
                if r:
                    results.append(r)
            except Exception as e:
                log(f"  ! 진단 실패: {e}")

    log("")
    log("=" * 60)
    log(f"완료! '{os.path.basename(OUT_DIR)}' 폴더의 파일을 전달해주세요.")
    log("=" * 60)
    return results




# ==================== 연습 진단 (자동으로 여러 화면 돌기) ====================
#
# 화면마다 있는 요소가 다르므로, 원래는 화면을 하나씩 띄워놓고 진단해야 한다.
# 아래 기능은 실제 업로드 과정을 그대로 따라가되
# **게시/저장 버튼만 누르지 않고** 각 화면을 자동으로 진단한다.

def dryrun_tiktok(page, video, log=print):
    """틱톡 업로드 화면들을 순서대로 돌며 진단 (게시는 하지 않음)."""
    results = []
    log("")
    log("─" * 50)
    log("틱톡 연습 진단 (게시하지 않습니다)")
    log("─" * 50)

    page.goto(up_tiktok.UPLOAD_URL, wait_until="domcontentloaded", timeout=90000)
    time.sleep(4)
    if "/login" in page.url:
        log("  ❌ 틱톡 로그인이 안 되어 있습니다")
        return results
    results.append(check_page(page, log, "1_업로드화면"))

    if not video or not os.path.exists(video):
        log("  ! 영상 파일이 없어 여기까지만 진단합니다")
        return [r for r in results if r]

    log("  영상 올리는 중... (게시는 안 함)")
    try:
        fin, _ = up_tiktok._find_any(page, up_tiktok.SEL["영상 파일 입력칸"],
                                     timeout=40000, state="attached")
        fin.set_input_files(video)
        time.sleep(8)
    except Exception as e:
        log(f"  ❌ 영상 선택 실패: {e}")
        return [r for r in results if r]

    results.append(check_page(page, log, "2_캡션화면"))

    # 예약 켜보기
    log("  '예약 게시' 켜보는 중...")
    if not up_tiktok._schedule_inputs(page, timeout=3000):
        up_tiktok._click_if(page, up_tiktok.SEL["예약 게시 스위치"], timeout=10000)
        time.sleep(3)
    results.append(check_page(page, log, "3_예약켜짐"))

    found = up_tiktok._schedule_inputs(page, timeout=5000)
    if found:
        date_in, time_in = found
        try:
            time_in.click()
            time.sleep(2)
            results.append(check_page(page, log, "4_시간선택창"))
            page.keyboard.press("Escape")
            time.sleep(1)
        except Exception as e:
            log(f"  ! 시간 선택창 열기 실패: {e}")
        try:
            date_in.click()
            time.sleep(2)
            results.append(check_page(page, log, "5_달력"))
            page.keyboard.press("Escape")
        except Exception as e:
            log(f"  ! 달력 열기 실패: {e}")
    else:
        log("  ! 예약 날짜/시간 칸이 안 나타남 (3_예약켜짐 파일을 확인해주세요)")

    log("  ✔ 틱톡 연습 진단 끝 — 게시 버튼은 누르지 않았습니다")
    return [r for r in results if r]


def dryrun_youtube(page, video, existing_url=None, log=print):
    """유튜브 업로드 화면들을 순서대로 돌며 진단 (게시/저장은 하지 않음)."""
    results = []
    log("")
    log("─" * 50)
    log("유튜브 연습 진단 (게시하지 않습니다)")
    log("─" * 50)

    page.goto("https://studio.youtube.com/", wait_until="domcontentloaded", timeout=90000)
    time.sleep(3)
    if "accounts.google.com" in page.url or "signin" in page.url:
        log("  ❌ 구글 로그인이 안 되어 있습니다")
        return results

    if video and os.path.exists(video):
        log("  업로드 창 여는 중...")
        try:
            page.locator("#create-icon").first.click(timeout=15000)
            time.sleep(1)
            page.locator("tp-yt-paper-item#text-item-0, ytcp-text-menu-item#text-item-0").first.click(timeout=8000)
        except Exception:
            page.goto("https://www.youtube.com/upload", wait_until="domcontentloaded", timeout=90000)
        time.sleep(2)
        results.append(check_page(page, log, "1_업로드창"))

        log("  영상 올리는 중... (게시는 안 함)")
        try:
            fin = up_youtube._first(page, up_youtube.SEL["영상 파일 입력칸"],
                                    timeout=30000, state="attached")
            fin.set_input_files(video)
            up_youtube._first(page, up_youtube.SEL["제목 입력칸"], timeout=120000)
            time.sleep(2)
        except Exception as e:
            log(f"  ❌ 영상 선택 실패: {e}")
            return [r for r in results if r]

        results.append(check_page(page, log, "2_세부정보"))

        up_youtube._click_if(page, up_youtube.SEL["자세히 보기 버튼"], timeout=8000)
        time.sleep(1.5)
        results.append(check_page(page, log, "3_태그"))

        log("  공개 설정 단계로 이동 중...")
        for _ in range(4):
            try:
                nxt = page.locator("#next-button").first
                if nxt.is_visible(timeout=3000):
                    nxt.click()
                    time.sleep(1.5)
            except Exception:
                break
        results.append(check_page(page, log, "4_공개설정"))

        log("  ⚠ 스튜디오에 '임시저장' 영상이 하나 생깁니다. 나중에 삭제하세요.")
    else:
        log("  ! 영상 파일이 없어 업로드 화면들은 건너뜁니다")

    # ---- 댓글·예약 화면은 '이미 올려둔 영상'이 있어야 볼 수 있다 ----
    if not existing_url:
        log("  ! 이미 올려둔 영상 주소를 안 줘서 댓글·예약 화면은 건너뜁니다")
        return [r for r in results if r]

    m = re.search(r"(?:youtu\.be/|v=|video/)([A-Za-z0-9_-]{11})", existing_url)
    if not m:
        log(f"  ! 영상 주소에서 ID를 못 찾음: {existing_url}")
        return [r for r in results if r]
    vid = m.group(1)

    log(f"  댓글 화면 확인 중... (영상 {vid})")
    try:
        page.goto(f"https://www.youtube.com/watch?v={vid}",
                  wait_until="domcontentloaded", timeout=90000)
        time.sleep(5)
        for _ in range(8):
            page.mouse.wheel(0, 900)
            time.sleep(1.0)
            if page.locator("#simplebox-placeholder").count():
                break
        results.append(check_page(page, log, "5_댓글"))
    except Exception as e:
        log(f"  ! 댓글 화면 진단 실패: {e}")

    log("  예약 설정 화면 확인 중... (저장은 안 함)")
    try:
        page.goto(f"https://studio.youtube.com/video/{vid}/edit",
                  wait_until="domcontentloaded", timeout=90000)
        time.sleep(5)
        up_youtube._click_if(page, up_youtube.SEL["공개 상태 드롭다운"], timeout=20000)
        time.sleep(2.5)
        up_youtube._click_if(page, up_youtube.SEL["예약 라디오"], timeout=10000)
        time.sleep(2)
        results.append(check_page(page, log, "6_예약설정"))
        page.keyboard.press("Escape")
        log("  ✔ 저장 버튼은 누르지 않았습니다 (공개 상태 그대로)")
    except Exception as e:
        log(f"  ! 예약 화면 진단 실패: {e}")

    return [r for r in results if r]


def run_dryrun(yt_video=None, tt_video=None, yt_existing=None, log=print):
    """연습 진단을 실행한다. 게시/저장은 절대 하지 않는다."""
    from playwright.sync_api import sync_playwright

    log("=" * 60)
    log("🧪 연습 진단 시작 — 게시/저장은 하지 않습니다")
    log("=" * 60)

    results = []
    with sync_playwright() as p:
        browser, context = up_browser.attach(p, log=log)
        page = up_browser.get_page(context, log=log)

        if yt_video or yt_existing:
            try:
                results += dryrun_youtube(page, yt_video, yt_existing, log=log)
            except Exception as e:
                log(f"❌ 유튜브 연습 진단 중단: {e}")

        if tt_video:
            try:
                results += dryrun_tiktok(page, tt_video, log=log)
            except Exception as e:
                log(f"❌ 틱톡 연습 진단 중단: {e}")

    bad = [r for r in results if r and r["missing"]]
    log("")
    log("=" * 60)
    log(f"진단한 화면 {len(results)}개 / 문제 있는 화면 {len(bad)}개")
    for r in bad:
        log(f"  ✘ {r['label']}: {', '.join(r['missing'])}")
    log(f"'{os.path.basename(OUT_DIR)}' 폴더를 통째로 전달해주세요.")
    log("=" * 60)
    return results


if __name__ == "__main__":
    run()
    input("\n엔터를 누르면 종료합니다...")
