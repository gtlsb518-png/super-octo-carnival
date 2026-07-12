# -*- coding: utf-8 -*-
"""
도매꾹(domeggook.com) 제품 자동 크롤러 — 완전 독립 실행형 프로그램

실행:
    python domeggook_crawler.py            # tkinter GUI 실행
    python domeggook_crawler.py 양말       # CLI 모드 (키워드 검색 후 엑셀 저장)
    python domeggook_crawler.py 양말 -p 3  # 1~3페이지 크롤링

기능:
    - 키워드 검색 결과 크롤링 (상품번호/상품명/가격/최소구매수량/URL/이미지)
    - 여러 키워드 동시 등록, 페이지 범위 지정
    - 자동(주기) 크롤링: N분마다 재수집 → 신규 상품 / 가격 변동 감지
    - 결과 엑셀(xlsx)/CSV 저장, 수집 이력은 dmg_history.json 에 보관
    - 수집 방식 2가지:
        1) 웹 크롤링 (기본) — 로그인/키 불필요, 요청 간 딜레이로 서버 부담 최소화
        2) 도매꾹 공식 오픈API — https://openapi.domeggook.com 에서 발급한
           API 키를 입력하면 안정적인 JSON 응답 사용 (권장)

주의: 웹 페이지 구조는 예고 없이 바뀔 수 있습니다. 파서가 여러 전략을
순차 시도하지만, 수집이 안 되면 오픈API 모드를 사용하세요.
필요한 라이브러리(requests, beautifulsoup4, pandas, openpyxl)는 실행 시 자동 설치됩니다.
"""

import os
import re
import sys
import json
import time
import argparse
import datetime
import threading
import subprocess

# ────────────────────────────────────────────────
# 라이브러리 자동 설치
# ────────────────────────────────────────────────
def _ensure(pkg, import_name=None):
    name = import_name or pkg
    try:
        __import__(name)
    except ImportError:
        print(f"[설치] {pkg} 설치 중...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

for _pkg, _imp in [("requests", None), ("beautifulsoup4", "bs4"),
                   ("pandas", None), ("openpyxl", None)]:
    _ensure(_pkg, _imp)

import requests
import pandas as pd
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(BASE_DIR, "dmg_settings.json")
HISTORY_FILE = os.path.join(BASE_DIR, "dmg_history.json")

DEFAULT_SETTINGS = {
    "keywords": ["양말"],          # 검색 키워드 목록
    "pages": 1,                    # 키워드당 크롤링 페이지 수
    "delay_sec": 2.0,              # 요청 간 딜레이(초) — 서버 부담 방지
    "interval_min": 30,            # 자동 크롤링 주기(분)
    "api_key": "",                 # 도매꾹 오픈API 키 (비우면 웹 크롤링)
    "market": "dome",              # dome=도매꾹, supply=도매매
    "save_dir": BASE_DIR,          # 엑셀 저장 폴더
}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
    "Referer": "https://domeggook.com/",
}

SEARCH_URLS = [
    # 파라미터 형식이 바뀌는 경우를 대비해 여러 URL 패턴을 순차 시도
    "https://domeggook.com/main/item/itemList.php?sf=totSearch&sfIdx=&sw={kw}&pg={page}",
    "https://domeggook.com/main/item/itemList.php?sf=totSearch&sw={kw}&page={page}",
    "https://domeggook.com/s/{kw}?pg={page}",
]

ITEM_URL_RE = re.compile(r"^(?:https?://(?:www\.)?domeggook\.com)?/(\d{6,12})(?:[/?#]|$)")
PRICE_RE = re.compile(r"([\d,]{2,})\s*원")
MINQTY_RE = re.compile(r"최소\s*구매\s*수량?\s*:?\s*([\d,]+)|([\d,]+)\s*개\s*(?:이상|부터|단위)")


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s.update(json.load(f))
        except Exception:
            pass
    return s


def save_settings(s):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


# ────────────────────────────────────────────────
# 크롤러
# ────────────────────────────────────────────────
class DomeggookCrawler:
    def __init__(self, settings, log=print):
        self.s = settings
        self.log = log
        self.sess = requests.Session()
        self.sess.headers.update(HEADERS)

    # ---------- 공통 진입점 ----------
    def search(self, keyword, pages=1):
        """키워드 검색 → 상품 dict 리스트 반환"""
        if self.s.get("api_key"):
            return self._search_api(keyword, pages)
        return self._search_web(keyword, pages)

    # ---------- 1) 공식 오픈API ----------
    def _search_api(self, keyword, pages):
        items = []
        for pg in range(1, pages + 1):
            params = {
                "ver": "4.1",
                "mode": "getItemList",
                "aid": self.s["api_key"],
                "market": self.s.get("market", "dome"),
                "om": "json",
                "sz": 100,
                "pg": pg,
                "kw": keyword,
            }
            try:
                r = self.sess.get("https://domeggook.com/ssl/api/",
                                  params=params, timeout=15)
                data = r.json()
            except Exception as e:
                self.log(f"[API 오류] {keyword} p{pg}: {e}")
                break
            err = data.get("errors") or data.get("error")
            if err:
                self.log(f"[API 오류] {err}")
                break
            item_list = (data.get("domeggook", {})
                             .get("list", {})
                             .get("item", []))
            if isinstance(item_list, dict):
                item_list = [item_list]
            if not item_list:
                break
            for it in item_list:
                items.append({
                    "상품번호": str(it.get("no", "")),
                    "상품명": it.get("title", ""),
                    "가격": self._to_int(it.get("price", 0)),
                    "최소구매수량": self._to_int(it.get("unitQty", 0)),
                    "판매자": it.get("id", ""),
                    "URL": it.get("url", f"https://domeggook.com/{it.get('no','')}"),
                    "이미지": it.get("thumb", ""),
                    "키워드": keyword,
                })
            self.log(f"[API] '{keyword}' {pg}페이지: {len(item_list)}개")
            time.sleep(max(0.5, float(self.s.get("delay_sec", 1))))
        return items

    # ---------- 2) 웹 크롤링 ----------
    def _search_web(self, keyword, pages):
        items = []
        for pg in range(1, pages + 1):
            html = self._fetch_page(keyword, pg)
            if html is None:
                break
            page_items = self._parse_page(html, keyword)
            if not page_items:
                self.log(f"[크롤링] '{keyword}' {pg}페이지: 상품을 찾지 못했습니다"
                         " (페이지 구조 변경 가능성 — 오픈API 모드 권장)")
                break
            self.log(f"[크롤링] '{keyword}' {pg}페이지: {len(page_items)}개")
            items.extend(page_items)
            time.sleep(max(1.0, float(self.s.get("delay_sec", 2))))
        return items

    def _fetch_page(self, keyword, page):
        from urllib.parse import quote
        for tpl in SEARCH_URLS:
            url = tpl.format(kw=quote(keyword), page=page)
            try:
                r = self.sess.get(url, timeout=15)
                if r.status_code == 200 and len(r.text) > 2000:
                    if not r.encoding or r.encoding.lower() in ("iso-8859-1",):
                        r.encoding = r.apparent_encoding
                    return r.text
            except Exception as e:
                self.log(f"[요청 실패] {url}: {e}")
        self.log(f"[요청 실패] '{keyword}' {page}페이지: 모든 URL 패턴 실패")
        return None

    def _parse_page(self, html, keyword):
        soup = BeautifulSoup(html, "html.parser")
        # 전략 1: JSON-LD 구조화 데이터
        items = self._parse_jsonld(soup, keyword)
        if items:
            return items
        # 전략 2: 상품번호 링크 기반 범용 파싱
        return self._parse_links(soup, keyword)

    def _parse_jsonld(self, soup, keyword):
        items = []
        for sc in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(sc.string or "")
            except Exception:
                continue
            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                elems = obj.get("itemListElement") or []
                for el in elems:
                    prod = el.get("item", el) if isinstance(el, dict) else {}
                    url = prod.get("url", "")
                    m = ITEM_URL_RE.match(url.replace("https://domeggook.com", ""))
                    no = m.group(1) if m else ""
                    offer = prod.get("offers", {}) or {}
                    items.append({
                        "상품번호": no,
                        "상품명": prod.get("name", ""),
                        "가격": self._to_int(offer.get("price", 0)),
                        "최소구매수량": 0,
                        "판매자": "",
                        "URL": url,
                        "이미지": prod.get("image", ""),
                        "키워드": keyword,
                    })
        return [i for i in items if i["상품번호"]]

    def _parse_links(self, soup, keyword):
        """상품 링크(/숫자)를 기준으로 주변 블록에서 정보 추출 — 레이아웃 변경에 강함"""
        found = {}
        for a in soup.find_all("a", href=True):
            m = ITEM_URL_RE.match(a["href"])
            if not m:
                continue
            no = m.group(1)
            # 제목: 링크 텍스트 / img alt / title 속성 중 가장 긴 것
            cands = [a.get_text(" ", strip=True), a.get("title", "")]
            img = a.find("img")
            img_src = ""
            if img:
                cands.append(img.get("alt", ""))
                img_src = img.get("src") or img.get("data-src") or ""
            title = max(cands, key=len) if cands else ""
            title = re.sub(r"\s+", " ", title).strip()

            prev = found.get(no)
            if prev is None:
                found[no] = {
                    "상품번호": no,
                    "상품명": title,
                    "가격": 0,
                    "최소구매수량": 0,
                    "판매자": "",
                    "URL": f"https://domeggook.com/{no}",
                    "이미지": img_src,
                    "키워드": keyword,
                    "_node": a,
                }
            else:
                if len(title) > len(prev["상품명"]):
                    prev["상품명"] = title
                if img_src and not prev["이미지"]:
                    prev["이미지"] = img_src

        # 가격/최소수량: 링크의 조상 블록 텍스트에서 정규식 추출
        for it in found.values():
            node = it.pop("_node")
            block = node
            for _ in range(4):  # 최대 4단계 부모까지 탐색
                if block.parent is None:
                    break
                block = block.parent
                text = block.get_text(" ", strip=True)
                if it["가격"] == 0:
                    pm = PRICE_RE.search(text)
                    if pm:
                        it["가격"] = self._to_int(pm.group(1))
                if it["최소구매수량"] == 0:
                    qm = MINQTY_RE.search(text)
                    if qm:
                        it["최소구매수량"] = self._to_int(qm.group(1) or qm.group(2))
                if it["가격"] and it["최소구매수량"]:
                    break

        # 상품명이 아예 없는 항목(배너 등) 제외
        return [i for i in found.values() if len(i["상품명"]) >= 2]

    @staticmethod
    def _to_int(v):
        try:
            return int(str(v).replace(",", "").split(".")[0] or 0)
        except Exception:
            return 0


# ────────────────────────────────────────────────
# 이력 비교 (신규 상품 / 가격 변동 감지)
# ────────────────────────────────────────────────
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def diff_and_update_history(items):
    """이전 수집분과 비교해 각 상품에 '상태' 컬럼 추가 후 이력 갱신"""
    hist = load_history()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    for it in items:
        no = it["상품번호"]
        prev = hist.get(no)
        if prev is None:
            it["상태"] = "신규"
        elif it["가격"] and prev.get("가격") and it["가격"] != prev["가격"]:
            sign = "▲" if it["가격"] > prev["가격"] else "▼"
            it["상태"] = f"가격{sign} {prev['가격']:,}→{it['가격']:,}"
        else:
            it["상태"] = ""
        hist[no] = {"가격": it["가격"], "상품명": it["상품명"], "최근수집": now}
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    return items


# ────────────────────────────────────────────────
# 저장
# ────────────────────────────────────────────────
COLUMNS = ["키워드", "상품번호", "상품명", "가격", "최소구매수량",
           "판매자", "상태", "URL", "이미지"]


def save_excel(items, save_dir):
    if not items:
        return None
    df = pd.DataFrame(items)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS].drop_duplicates(subset=["상품번호"])
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"도매꾹_수집결과_{ts}.xlsx")
    try:
        df.to_excel(path, index=False)
    except Exception:
        path = path.replace(".xlsx", ".csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# ────────────────────────────────────────────────
# 크롤링 1회 실행 (CLI/GUI 공용)
# ────────────────────────────────────────────────
def run_once(settings, log=print, stop_flag=None):
    crawler = DomeggookCrawler(settings, log=log)
    all_items, seen = [], set()
    for kw in settings["keywords"]:
        if stop_flag and stop_flag.is_set():
            break
        kw = kw.strip()
        if not kw:
            continue
        for it in crawler.search(kw, int(settings.get("pages", 1))):
            if it["상품번호"] not in seen:
                seen.add(it["상품번호"])
                all_items.append(it)
    if all_items:
        diff_and_update_history(all_items)
        path = save_excel(all_items, settings.get("save_dir", BASE_DIR))
        new_cnt = sum(1 for i in all_items if i.get("상태") == "신규")
        chg_cnt = sum(1 for i in all_items if i.get("상태", "").startswith("가격"))
        log(f"[완료] 총 {len(all_items)}개 수집 (신규 {new_cnt}, 가격변동 {chg_cnt})")
        if path:
            log(f"[저장] {path}")
    else:
        log("[완료] 수집된 상품이 없습니다")
    return all_items


# ────────────────────────────────────────────────
# GUI (tkinter)
# ────────────────────────────────────────────────
def run_gui(settings):
    import tkinter as tk
    from tkinter import ttk, scrolledtext, messagebox

    root = tk.Tk()
    root.title("도매꾹 자동 크롤러")
    root.geometry("1000x680")

    stop_flag = threading.Event()
    auto_running = [False]
    worker = [None]

    # ── 상단 설정 영역 ──
    frm = ttk.LabelFrame(root, text="설정")
    frm.pack(fill="x", padx=8, pady=6)

    def row(r, label, default, width=28):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="e", padx=4, pady=2)
        var = tk.StringVar(value=str(default))
        ttk.Entry(frm, textvariable=var, width=width).grid(
            row=r, column=1, sticky="w", padx=4, pady=2)
        return var

    v_kw = row(0, "키워드(쉼표 구분)", ", ".join(settings["keywords"]), 45)
    v_pages = row(1, "페이지 수", settings["pages"], 8)
    v_delay = row(2, "요청 딜레이(초)", settings["delay_sec"], 8)

    def row_right(r, label, default, width):
        ttk.Label(frm, text=label).grid(row=r, column=2, sticky="e", padx=(20, 4))
        var = tk.StringVar(value=str(default))
        ttk.Entry(frm, textvariable=var, width=width).grid(
            row=r, column=3, sticky="w", pady=2)
        return var

    v_interval = row_right(0, "자동 주기(분)", settings["interval_min"], 10)
    v_api = row_right(1, "오픈API 키(선택)", settings["api_key"], 34)

    v_market = tk.StringVar(value=settings.get("market", "dome"))
    ttk.Label(frm, text="마켓").grid(row=2, column=2, sticky="e", padx=(20, 4))
    ttk.Combobox(frm, textvariable=v_market, width=8, state="readonly",
                 values=["dome", "supply"]).grid(row=2, column=3, sticky="w")

    # ── 버튼 영역 ──
    btn_frm = ttk.Frame(root)
    btn_frm.pack(fill="x", padx=8)

    # ── 결과 테이블 ──
    cols = ("키워드", "상품번호", "상품명", "가격", "최소구매수량", "상태", "URL")
    tree = ttk.Treeview(root, columns=cols, show="headings", height=16)
    widths = {"키워드": 80, "상품번호": 90, "상품명": 340, "가격": 80,
              "최소구매수량": 90, "상태": 130, "URL": 160}
    for c in cols:
        tree.heading(c, text=c)
        tree.column(c, width=widths[c], anchor="w")
    tree.pack(fill="both", expand=True, padx=8, pady=4)
    tree.tag_configure("new", background="#e8f5e9")
    tree.tag_configure("chg", background="#fff3e0")

    def open_url(event):
        sel = tree.selection()
        if sel:
            url = tree.item(sel[0], "values")[-1]
            if url:
                import webbrowser
                webbrowser.open(url)
    tree.bind("<Double-1>", open_url)

    # ── 로그 ──
    log_box = scrolledtext.ScrolledText(root, height=8, state="disabled")
    log_box.pack(fill="x", padx=8, pady=(0, 8))

    def log(msg):
        def _append():
            log_box.configure(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            log_box.insert("end", f"[{ts}] {msg}\n")
            log_box.see("end")
            log_box.configure(state="disabled")
        root.after(0, _append)

    def collect_settings():
        try:
            settings["keywords"] = [k.strip() for k in v_kw.get().split(",") if k.strip()]
            settings["pages"] = max(1, int(float(v_pages.get())))
            settings["delay_sec"] = max(0.5, float(v_delay.get()))
            settings["interval_min"] = max(1, int(float(v_interval.get())))
            settings["api_key"] = v_api.get().strip()
            settings["market"] = v_market.get()
        except ValueError:
            messagebox.showerror("입력 오류", "숫자 입력값을 확인하세요")
            return False
        if not settings["keywords"]:
            messagebox.showerror("입력 오류", "키워드를 1개 이상 입력하세요")
            return False
        save_settings(settings)
        return True

    def show_items(items):
        def _show():
            tree.delete(*tree.get_children())
            for it in items:
                status = it.get("상태", "")
                tag = "new" if status == "신규" else ("chg" if status.startswith("가격") else "")
                tree.insert("", "end", values=(
                    it["키워드"], it["상품번호"], it["상품명"],
                    f"{it['가격']:,}" if it["가격"] else "",
                    it["최소구매수량"] or "", status, it["URL"]),
                    tags=(tag,) if tag else ())
        root.after(0, _show)

    def crawl_job():
        items = run_once(settings, log=log, stop_flag=stop_flag)
        show_items(items)

    def start_once():
        if worker[0] and worker[0].is_alive():
            return
        if not collect_settings():
            return
        stop_flag.clear()
        worker[0] = threading.Thread(target=crawl_job, daemon=True)
        worker[0].start()

    def auto_loop():
        while auto_running[0] and not stop_flag.is_set():
            crawl_job()
            wait = int(settings["interval_min"]) * 60
            log(f"[자동] {settings['interval_min']}분 후 다시 수집합니다")
            for _ in range(wait):
                if not auto_running[0] or stop_flag.is_set():
                    return
                time.sleep(1)

    def start_auto():
        if auto_running[0]:
            return
        if not collect_settings():
            return
        stop_flag.clear()
        auto_running[0] = True
        btn_auto.configure(text="자동 실행 중...", state="disabled")
        worker[0] = threading.Thread(target=auto_loop, daemon=True)
        worker[0].start()
        log("[자동] 주기 크롤링 시작")

    def stop_all():
        stop_flag.set()
        auto_running[0] = False
        btn_auto.configure(text="자동 크롤링 시작", state="normal")
        log("[중지] 요청됨 — 진행 중인 요청 종료 후 멈춥니다")

    ttk.Button(btn_frm, text="1회 크롤링", command=start_once).pack(side="left", padx=4, pady=4)
    btn_auto = ttk.Button(btn_frm, text="자동 크롤링 시작", command=start_auto)
    btn_auto.pack(side="left", padx=4)
    ttk.Button(btn_frm, text="중지", command=stop_all).pack(side="left", padx=4)
    ttk.Label(btn_frm, text="※ 더블클릭: 상품 페이지 열기 / 결과는 엑셀로 자동 저장"
              ).pack(side="left", padx=12)

    log("도매꾹 자동 크롤러 시작 — 키워드 입력 후 [1회 크롤링] 또는 [자동 크롤링 시작]")
    if not settings.get("api_key"):
        log("TIP: openapi.domeggook.com 에서 무료 API 키를 발급받아 입력하면 더 안정적입니다")

    def on_close():
        stop_flag.set()
        auto_running[0] = False
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────
def main():
    settings = load_settings()
    parser = argparse.ArgumentParser(description="도매꾹 제품 자동 크롤러")
    parser.add_argument("keywords", nargs="*", help="검색 키워드 (없으면 GUI 실행)")
    parser.add_argument("-p", "--pages", type=int, default=None, help="페이지 수")
    parser.add_argument("-d", "--delay", type=float, default=None, help="요청 딜레이(초)")
    parser.add_argument("-i", "--interval", type=int, default=None,
                        help="자동 반복 주기(분). 지정 시 계속 반복 실행")
    parser.add_argument("-k", "--api-key", default=None, help="도매꾹 오픈API 키")
    args = parser.parse_args()

    if args.pages is not None:
        settings["pages"] = args.pages
    if args.delay is not None:
        settings["delay_sec"] = args.delay
    if args.api_key is not None:
        settings["api_key"] = args.api_key
    if args.keywords:
        settings["keywords"] = args.keywords

    if not args.keywords:
        # 인자 없으면 GUI
        try:
            run_gui(settings)
            return
        except Exception as e:
            print(f"GUI 실행 실패({e}) — CLI 모드로 전환합니다")
            print("사용법: python domeggook_crawler.py 키워드 [-p 페이지수]")
            return

    save_settings(settings)
    if args.interval:
        print(f"자동 모드: {args.interval}분마다 수집 (Ctrl+C로 종료)")
        try:
            while True:
                run_once(settings)
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            print("\n종료")
    else:
        run_once(settings)


if __name__ == "__main__":
    main()
