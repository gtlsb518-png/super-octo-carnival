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


def _site_of(url):
    u = (url or "").lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u:
        return "youtube"
    return None


def check_page(page, log=print):
    """열려 있는 탭 하나를 진단해서 파일 3개를 만든다."""
    site = _site_of(page.url)
    if not site:
        log(f"  건너뜀 (유튜브/틱톡 화면이 아님): {page.url[:70]}")
        return None

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    base = f"{site}_{stamp}"

    lines = []
    lines.append("=" * 70)
    lines.append(f"진단 시각 : {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"대상      : {site}")
    lines.append(f"주소      : {page.url}")
    lines.append("=" * 70)

    # ---------- 1. 선택자별 매칭 결과 ----------
    sel_map = up_tiktok.SEL if site == "tiktok" else up_youtube.SEL
    frames = [page] + [f for f in page.frames if f is not page.main_frame]

    lines.append("")
    lines.append("[ 선택자 점검 ]  ✔=찾음  ✘=못찾음")
    lines.append("")

    missing = []
    for name, selectors in sel_map.items():
        found_any = False
        rows = []
        for sel in selectors:
            total = 0
            for fr in frames:
                try:
                    total += fr.locator(sel).count()
                except Exception:
                    pass
            if total:
                found_any = True
            rows.append(f"    {'✔' if total else '✘'} {total:>3}개  {sel}")

        lines.append(f"  {'✔' if found_any else '✘'} {name}")
        lines.extend(rows)
        lines.append("")
        if not found_any:
            missing.append(name)

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
    log(f"  [{site}] {page.url[:60]}")
    if missing:
        log(f"    ✘ 못 찾은 항목 {len(missing)}개: {', '.join(missing)}")
    else:
        log("    ✔ 모든 항목을 찾았습니다")
    log(f"    저장: {os.path.basename(txt_path)}")

    return {"site": site, "missing": missing, "txt": txt_path,
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


if __name__ == "__main__":
    run()
    input("\n엔터를 누르면 종료합니다...")
