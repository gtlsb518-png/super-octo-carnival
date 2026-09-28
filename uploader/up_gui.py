#!/usr/bin/env python3
"""
유튜브 / 틱톡 자동 업로드 프로그램 - GUI

  [상단]  프로그램 제목 + 현재 상태
  [준비]  크롬 열기 / 로그인 정보 복사 / 화면 진단
  [본문]  유튜브(숏) · 유튜브(롱) · 틱톡  세 칸, 각각 업로드 버튼
  [묶음]  유튜브(숏) + 틱톡 동시 업로드 버튼
  [하단]  진행 로그
"""

import os
import json
import queue
import threading
import traceback
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, filedialog, messagebox, simpledialog

import up_browser
import up_youtube
import up_tiktok
import up_check

try:
    from PIL import Image, ImageTk
except ImportError:                      # 미리보기만 못 쓰고 나머지는 정상 동작
    Image = ImageTk = None

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "uploader_settings.json")

VIDEO_TYPES = [("영상 파일", "*.mp4 *.mov *.avi *.mkv *.webm *.flv"), ("모든 파일", "*.*")]
IMAGE_TYPES = [("이미지 파일", "*.jpg *.jpeg *.png *.webp *.bmp"), ("모든 파일", "*.*")]

# ==================== 색·글꼴 ====================

BG        = "#eef0f4"
CARD      = "#ffffff"
INK       = "#16191d"
MUTED     = "#6b7280"
LINE      = "#dfe3e8"
HEAD_BG   = "#14171c"
CHIP      = "#eceff3"
CHIP_ON   = "#dde2e8"
SHADE     = "#f1f3f6"

YT        = "#ff0033"   # 유튜브 (숏)
YT_ON     = "#d4002a"
YTL       = "#8b0020"   # 유튜브 (롱) - 구분용으로 조금 어둡게
YTL_ON    = "#6d0019"
TT        = "#111111"   # 틱톡
TT_ON     = "#333333"
BOTH      = "#0f766e"   # 동시 업로드
BOTH_ON   = "#0b5b55"

OK_TXT    = "#15803d"
WARN_TXT  = "#b45309"

LOG_BG    = "#11141a"
LOG_FG    = "#ccd1d9"
C_OK      = "#4ade80"
C_ERR     = "#f87171"
C_WARN    = "#fbbf24"
C_STEP    = "#60a5fa"
C_DIM     = "#5b6472"

FONT  = "맑은 고딕"
MONO  = "Consolas"

# ==================== 섹션 정의 ====================
# 세 칸은 서로 완전히 독립이다. 내용도 따로, 버튼도 따로.

SPECS = [
    {
        "key": "yts", "name": "유튜브 (숏)", "save": "youtube_short",
        "accent": YT, "accent_on": YT_ON, "site": "youtube",
        "thumb_label": "썸네일", "has_tags": True, "has_pin": True,
        "has_kids": True, "privacy": None, "ratio": (16, 9), "box": (128, 72),
        "note": "숏폼용 · 일부공개 게시 → 댓글 고정 → 예약 전환 · 태그는 쉼표로 구분",
        "btn": "▶   숏 업로드",
    },
    {
        "key": "ytl", "name": "유튜브 (롱)", "save": "youtube_long",
        "accent": YTL, "accent_on": YTL_ON, "site": "youtube",
        "thumb_label": "썸네일", "has_tags": True, "has_pin": True,
        "has_kids": True, "privacy": None, "ratio": (16, 9), "box": (128, 72),
        "note": "롱폼용 · 진행 방식은 숏과 같고 내용만 따로 · 태그는 쉼표로 구분",
        "btn": "▶   롱 업로드",
    },
    {
        "key": "tt", "name": "틱톡", "save": "tiktok",
        "accent": TT, "accent_on": TT_ON, "site": "tiktok",
        "thumb_label": "커버 이미지", "has_tags": False, "has_pin": False,
        "has_kids": False,
        "privacy": [("전체 공개", "public"), ("친구만", "friends"), ("나만 보기", "private")],
        "ratio": (9, 16), "box": (68, 121),
        "note": "해시태그는 제목·상세정보에 #태그로 직접 · 예: 카페투어 #카페 #브이로그",
        "btn": "▶   틱톡 업로드",
    },
]

SPEC_BY_KEY = {sp["key"]: sp for sp in SPECS}

# 업로드 시 반드시 채워져 있어야 하는 칸
REQUIRED = [("title", "제목"), ("desc", "상세정보"), ("video", "영상 파일")]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("유튜브 / 틱톡 자동 업로드")
        self.geometry("1560x1000")
        self.minsize(1180, 800)
        self.configure(bg=BG)

        self.log_q = queue.Queue()
        self.busy = False          # 업로드 중복 실행 방지
        self.secs = {}             # key -> 위젯 모음

        self._build_ui()
        self._load_settings()
        self._prefill_schedule()
        for s in self.secs.values():
            self._update_preview(s)
        self.after(100, self._drain_log)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ==================== 화면 구성 ====================

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=5)
        style.configure("TRadiobutton", background=CARD, foreground=INK, font=(FONT, 9))
        style.configure("TCheckbutton", background=CARD, foreground=INK, font=(FONT, 9))
        style.map("TRadiobutton", background=[("active", CARD)])
        style.map("TCheckbutton", background=[("active", CARD)])

        self._build_header()
        self._build_toolbar()
        # 아래쪽부터 자리를 잡아야 공간이 모자랄 때 찌그러지지 않는다
        self._build_log()
        self._build_combo()
        self._build_sections()

    # ---------- 상단 바 ----------

    def _build_header(self):
        bar = tk.Frame(self, bg=HEAD_BG, height=58)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        tk.Label(bar, text="🎬  유튜브 / 틱톡 자동 업로드", bg=HEAD_BG, fg="#ffffff",
                 font=(FONT, 15, "bold")).pack(side="left", padx=20)

        self.status = tk.Label(bar, text="● 준비됨", bg=HEAD_BG, fg=C_OK,
                               font=(FONT, 10, "bold"))
        self.status.pack(side="right", padx=20)

    def _set_status(self, text, color):
        self.status.config(text=f"● {text}", fg=color)

    # ---------- 작은 부품 ----------

    def _card(self, parent, **grid):
        c = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                     highlightcolor=LINE, highlightthickness=1)
        if grid:
            c.grid(**grid)
        return c

    def _chip(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd,
                         bg=CHIP, fg=INK, activebackground=CHIP_ON, activeforeground=INK,
                         font=(FONT, 9, "bold"), relief="flat", bd=0,
                         padx=12, pady=6, cursor="hand2")

    def _title(self, parent, text):
        return tk.Label(parent, text=text, bg=CARD, fg=INK, font=(FONT, 10, "bold"))

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=CARD, fg=INK, font=(FONT, 9))

    def _hint(self, parent, text, **kw):
        return tk.Label(parent, text=text, bg=CARD, fg=MUTED,
                        font=(FONT, 8), justify="left", **kw)

    # ---------- 준비 단계 ----------

    def _build_toolbar(self):
        card = self._card(self)
        card.pack(fill="x", padx=14, pady=(12, 7))

        self._title(card, "1단계 · 브라우저 준비").pack(anchor="w", padx=16, pady=(10, 6))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=16)
        for text, cmd in [
            ("① 크롬 로그인 정보 가져오기", self.on_copy_profile),
            ("② 업로드용 크롬 열기", self.on_open_chrome),
            ("③ 화면 진단하기", self.on_check),
            ("④ 연습 진단(자동)", self.on_dryrun),
        ]:
            self._chip(row, text, cmd).pack(side="left", padx=(0, 8))

        self._hint(
            card,
            "①은 크롬을 완전히 종료한 뒤 한 번만 · ③은 열려 있는 화면 하나를, "
            "④는 업로드 과정을 따라가며 여러 화면을 자동 진단합니다 (게시는 하지 않음)",
        ).pack(anchor="w", padx=16, pady=(8, 10))

    # ---------- 동시 업로드 ----------

    def _build_combo(self):
        card = self._card(self)
        card.pack(side="bottom", fill="x", padx=14, pady=(0, 7))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=16, pady=10)

        self.combo_btn = tk.Button(
            inner, text="⚡   유튜브(숏)  +  틱톡   동시 업로드",
            command=self.on_upload_both,
            bg=BOTH, fg="#ffffff", activebackground=BOTH_ON, activeforeground="#ffffff",
            disabledforeground="#c8ccd2",
            font=(FONT, 12, "bold"), relief="flat", bd=0, cursor="hand2", pady=10)
        self.combo_btn.pack(side="left", fill="x", expand=True)

        self._hint(
            inner,
            "유튜브 먼저 → 틱톡 순서\n하나라도 덜 채워지면 둘 다 안 올림",
        ).pack(side="left", padx=(12, 0))

    # ---------- 세 칸 ----------

    def _build_sections(self):
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=7)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)

        mid = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=mid, anchor="nw")

        def sync_scrollbar():
            need = mid.winfo_reqheight() > canvas.winfo_height() + 2
            if need and not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y")
            elif not need and vsb.winfo_ismapped():
                vsb.pack_forget()

        def on_canvas(e):
            canvas.itemconfig(win, width=e.width)
            sync_scrollbar()

        def on_inner(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            sync_scrollbar()

        canvas.bind("<Configure>", on_canvas)
        mid.bind("<Configure>", on_inner)
        self._scroll_canvas = canvas
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Button-4>", self._on_wheel)
        self.bind_all("<Button-5>", self._on_wheel)

        for i in range(len(SPECS)):
            mid.columnconfigure(i, weight=1, uniform="col")
        mid.rowconfigure(0, weight=1)

        for i, spec in enumerate(SPECS):
            pad = (0 if i == 0 else 5, 0 if i == len(SPECS) - 1 else 5)
            self.secs[spec["key"]] = self._build_section(mid, i, spec, pad)

    def _on_wheel(self, event):
        """마우스 휠: 글상자 위에서는 그 글상자를, 그 밖에서는 화면 전체를 스크롤."""
        if isinstance(event.widget, tk.Text):
            return None
        step = -1 if getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4 else 1
        try:
            self._scroll_canvas.yview_scroll(step, "units")
        except Exception:
            pass
        return "break"

    def _build_section(self, parent, col, spec, pad):
        accent, accent_on = spec["accent"], spec["accent_on"]
        card = self._card(parent, row=0, column=col, sticky="nsew", padx=pad)

        tk.Frame(card, bg=accent, height=4).pack(fill="x")

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(head, text=spec["name"], bg=CARD, fg=accent,
                 font=(FONT, 14, "bold")).pack(anchor="w")
        self._hint(head, spec["note"], wraplength=400).pack(anchor="w", pady=(2, 0))

        # 업로드 버튼은 내용이 길어도 잘리면 안 되므로 카드 아래쪽에 먼저 고정
        btn = tk.Button(card, text=spec["btn"], command=lambda k=spec["key"]: self.on_upload(k),
                        bg=accent, fg="#ffffff",
                        activebackground=accent_on, activeforeground="#ffffff",
                        disabledforeground="#c8ccd2",
                        font=(FONT, 11, "bold"), relief="flat", bd=0,
                        cursor="hand2", pady=11)
        btn.pack(side="bottom", fill="x", padx=14, pady=(8, 14))

        extra = tk.Frame(card, bg=CARD)
        extra.pack(side="bottom", fill="x", padx=14)

        f = tk.Frame(card, bg=CARD)
        f.pack(fill="both", expand=True, padx=14, pady=(10, 0))
        f.columnconfigure(1, weight=1)

        s = {"spec": spec, "button": btn, "extra": extra}
        r = 0

        def row_label(text, sticky="w"):
            self._label(f, text).grid(row=r, column=0, sticky=sticky, pady=4, padx=(0, 8))

        # 제목
        row_label("제목")
        s["title"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["title"], font=(FONT, 9)).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1


        # 상세정보
        row_label("상세정보", "nw")
        s["desc"] = tk.Text(f, width=1, height=3, wrap="word", font=(FONT, 9),
                            relief="flat", bd=0, padx=8, pady=6,
                            highlightbackground=LINE, highlightcolor="#9aa4b2",
                            highlightthickness=1)
        s["desc"].grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 태그 (유튜브만)
        if spec["has_tags"]:
            row_label("태그")  # 쉼표로 구분
            s["tags"] = tk.StringVar()
            ttk.Entry(f, textvariable=s["tags"], font=(FONT, 9)).grid(
                row=r, column=1, columnspan=2, sticky="ew", pady=4)
            r += 1

        # 고정 댓글 (유튜브만)
        if spec["has_pin"]:
            row_label("고정 댓글", "nw")
            s["pin"] = tk.Text(f, width=1, height=2, wrap="word", font=(FONT, 9),
                               relief="flat", bd=0, padx=8, pady=6,
                               highlightbackground=LINE, highlightcolor="#9aa4b2",
                               highlightthickness=1)
            s["pin"].grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
            r += 1

        # 영상 파일
        row_label("영상 파일")
        s["video"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["video"], font=(FONT, 9)).grid(
            row=r, column=1, sticky="ew", pady=4)
        self._chip(f, "찾기", lambda v=s["video"]: self._pick(v, VIDEO_TYPES)).grid(
            row=r, column=2, padx=(6, 0))
        r += 1

        # 썸네일 / 커버 + 미리보기
        row_label(spec["thumb_label"])
        s["thumb"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["thumb"], font=(FONT, 9)).grid(
            row=r, column=1, sticky="ew", pady=4)
        self._chip(f, "찾기", lambda v=s["thumb"]: self._pick(v, IMAGE_TYPES)).grid(
            row=r, column=2, padx=(6, 0))
        r += 1

        self._build_preview(f, s, r, spec)
        r += 1

        # 공개 설정 (틱톡만)
        if spec["privacy"]:
            row_label("공개 설정")
            s["privacy"] = tk.StringVar(value=spec["privacy"][0][1])
            pf = tk.Frame(f, bg=CARD)
            pf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
            for lb, val in spec["privacy"]:
                ttk.Radiobutton(pf, text=lb, value=val,
                                variable=s["privacy"]).pack(side="left", padx=(0, 10))
            r += 1

        # ---- 예약 게시 ----
        tk.Frame(f, bg=LINE, height=1).grid(row=r, column=0, columnspan=3,
                                            sticky="ew", pady=(10, 8))
        r += 1

        s["sched_on"] = tk.BooleanVar(value=True)
        s["sched_date"] = tk.StringVar()
        s["sched_time"] = tk.StringVar()

        sf = tk.Frame(f, bg=CARD)
        sf.grid(row=r, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(sf, text="예약", variable=s["sched_on"]).pack(side="left", padx=(0, 6))
        ttk.Entry(sf, textvariable=s["sched_date"], width=11,
                  font=(FONT, 9)).pack(side="left", padx=(0, 4))
        ttk.Entry(sf, textvariable=s["sched_time"], width=6,
                  font=(FONT, 9)).pack(side="left", padx=(0, 6))
        self._chip(sf, "오늘", lambda d=s: self._set_today(d)).pack(side="left", padx=2)
        self._chip(sf, "+1일", lambda v=s["sched_date"]: self._shift_day(v, 1)).pack(side="left", padx=2)
        r += 1

        # 아동용 (유튜브만)
        if spec["has_kids"]:
            s["kids"] = tk.BooleanVar(value=False)
            ttk.Checkbutton(extra, text="아동용 동영상입니다",
                            variable=s["kids"]).pack(anchor="w", pady=(6, 0))

        f.rowconfigure(r, weight=1)

        # 썸네일 경로가 바뀌면 미리보기 갱신
        s["thumb"].trace_add("write", lambda *a, sec=s: self._update_preview(sec))
        return s

    # ---------- 썸네일 미리보기 ----------

    def _build_preview(self, parent, s, row, spec):
        bw, bh = spec["box"]
        wrap = tk.Frame(parent, bg=CARD)
        wrap.grid(row=row, column=1, columnspan=2, sticky="w", pady=(2, 6))

        box = tk.Frame(wrap, bg=SHADE, width=bw, height=bh,
                       highlightbackground=LINE, highlightthickness=1)
        box.pack(side="left")
        box.pack_propagate(False)

        img = tk.Label(box, bg=SHADE, fg=MUTED, font=(FONT, 8),
                       text="이미지를 고르면\n여기에 미리보기")
        img.pack(expand=True)

        info = tk.Label(wrap, bg=CARD, fg=MUTED, font=(FONT, 8), justify="left")
        info.pack(side="left", padx=(10, 0), anchor="n")

        s["_preview"] = (img, info, bw, bh, spec["ratio"])

    def _update_preview(self, s):
        """썸네일/커버 파일을 읽어 실제 모습과 비율을 보여준다."""
        img, info, bw, bh, (rw, rh) = s["_preview"]

        def show(text, note="", color=MUTED):
            img.config(image="", text=text)
            img.image = None
            info.config(text=note, fg=color)

        path = s["thumb"].get().strip()
        if not path:
            return show("이미지를 고르면\n여기에 미리보기")
        if not os.path.exists(path):
            return show("파일 없음", "경로를 확인하세요", WARN_TXT)
        if Image is None:
            return show("미리보기 불가", "pillow 가 설치되지 않았습니다\n(업로드에는 지장 없음)", WARN_TXT)

        try:
            with Image.open(path) as im:
                w, h = im.size
                small = im.copy()
                small.thumbnail((bw - 2, bh - 2))
                photo = ImageTk.PhotoImage(small)
        except Exception as e:
            return show("읽기 실패", str(e)[:36], WARN_TXT)

        img.config(image=photo, text="")
        img.image = photo                       # 참조를 붙들어야 안 사라진다

        want, got = rw / rh, w / h
        if abs(got - want) / want < 0.06:
            info.config(text=f"{w}×{h}\n{rw}:{rh} ✔", fg=OK_TXT)
        else:
            info.config(text=f"{w}×{h}\n{rw}:{rh} 권장\n(잘릴 수 있음)", fg=WARN_TXT)

    # ---------- 로그 ----------

    def _build_log(self):
        card = self._card(self)
        card.pack(side="bottom", fill="x", padx=14, pady=(7, 14))

        self._title(card, "진행 상황").pack(anchor="w", padx=16, pady=(10, 6))

        box = tk.Frame(card, bg=CARD)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self.log_box = tk.Text(box, width=1, height=6, wrap="word",
                               bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
                               font=(MONO, 9), state="disabled",
                               relief="flat", bd=0, padx=12, pady=10)
        self.log_box.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(box, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=sb.set)

        for tag, color in (("ok", C_OK), ("err", C_ERR), ("warn", C_WARN),
                           ("step", C_STEP), ("dim", C_DIM)):
            self.log_box.tag_config(tag, foreground=color)

    # ==================== 입력 도우미 ====================

    def _pick(self, var, types):
        path = filedialog.askopenfilename(filetypes=types)
        if path:
            var.set(path)

    def _shift_day(self, var, days):
        try:
            base = datetime.strptime(var.get().strip(), "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
        var.set(f"{base + timedelta(days=days):%Y-%m-%d}")

    def _set_today(self, s):
        d, t = self._default_schedule()
        s["sched_date"].set(d)
        s["sched_time"].set(t)

    @staticmethod
    def _default_schedule():
        """
        기본 예약 시각 = 오늘.

        시간은 09:00 을 쓰되, 이미 지났으면 '지금부터 1시간 뒤'(5분 단위)로 잡는다.
        밤늦게 눌러 1시간 뒤가 자정을 넘으면 그 날짜가 된다.
        """
        now = datetime.now()
        nine = now.replace(hour=9, minute=0, second=0, microsecond=0)
        target = nine if now < nine - timedelta(minutes=30) else now + timedelta(hours=1)
        target = target.replace(minute=(target.minute // 5) * 5, second=0, microsecond=0)
        return f"{target:%Y-%m-%d}", f"{target:%H:%M}"

    def _prefill_schedule(self):
        """예약칸을 채운다. 비어 있거나 지난 날짜면 오늘 기본값으로."""
        d, t = self._default_schedule()
        today = datetime.now().date()

        for s in self.secs.values():
            cur = s["sched_date"].get().strip()
            valid = False
            if cur:
                try:
                    valid = datetime.strptime(cur, "%Y-%m-%d").date() >= today
                except ValueError:
                    valid = False

            if not valid:
                s["sched_date"].set(d)
                s["sched_time"].set(t)
            elif not s["sched_time"].get().strip():
                s["sched_time"].set(t)

    # ==================== 로그 ====================

    def log(self, msg):
        self.log_q.put(str(msg))

    @staticmethod
    def _tag_for(msg):
        head = msg.lstrip()
        if msg.startswith("❌") or head.startswith("!") or head.startswith("✘"):
            return "err"
        if "✅" in msg or "🎉" in msg or head.startswith("✔"):
            return "ok"
        if head.startswith("⚠") or "※" in msg or head.startswith("△"):
            return "warn"
        if head[:1] in ("[", "🚀", "🔍", "🧪", "⚡") or head.startswith("→"):
            return "step"
        if head[:1] in ("=", "─"):
            return "dim"
        return None

    def _drain_log(self):
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            self.log_box.configure(state="normal")
            tag = self._tag_for(msg)
            self.log_box.insert("end", msg + "\n", tag or ())
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(100, self._drain_log)

    # ==================== 설정 저장/불러오기 ====================

    def _collect(self, s):
        d = {
            "title": s["title"].get(),
            "desc": s["desc"].get("1.0", "end").rstrip("\n"),
            "video": s["video"].get(),
            "thumb": s["thumb"].get(),
            "sched_on": bool(s["sched_on"].get()),
            "sched_date": s["sched_date"].get().strip(),
            "sched_time": s["sched_time"].get().strip(),
        }
        if "tags" in s:
            d["tags"] = s["tags"].get()
        if "privacy" in s:
            d["privacy"] = s["privacy"].get()
        if "pin" in s:
            d["pin_comment"] = s["pin"].get("1.0", "end").rstrip("\n")
        if "kids" in s:
            d["kids"] = bool(s["kids"].get())
        return d

    def _apply(self, s, data):
        s["title"].set(data.get("title", ""))
        s["desc"].delete("1.0", "end")
        s["desc"].insert("1.0", data.get("desc", ""))
        s["video"].set(data.get("video", ""))
        s["thumb"].set(data.get("thumb", ""))
        s["sched_on"].set(bool(data.get("sched_on", True)))
        s["sched_date"].set(data.get("sched_date", ""))
        s["sched_time"].set(data.get("sched_time", ""))
        if "tags" in s:
            s["tags"].set(data.get("tags", ""))
        if "privacy" in s and data.get("privacy"):
            s["privacy"].set(data["privacy"])
        if "pin" in s:
            s["pin"].delete("1.0", "end")
            s["pin"].insert("1.0", data.get("pin_comment", ""))
        if "kids" in s:
            s["kids"].set(bool(data.get("kids", False)))

    def _save_settings(self):
        try:
            data = {SPEC_BY_KEY[k]["save"]: self._collect(s) for k, s in self.secs.items()}
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"! 설정 저장 실패: {e}")

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fp:
                data = json.load(fp)
            # 예전 설정("youtube")은 숏폼 칸으로 옮겨준다
            if "youtube" in data and "youtube_short" not in data:
                data["youtube_short"] = data["youtube"]
            for key, s in self.secs.items():
                self._apply(s, data.get(SPEC_BY_KEY[key]["save"], {}))
            self.log("이전 입력 내용을 불러왔습니다.")
        except Exception as e:
            self.log(f"! 설정 불러오기 실패: {e}")

    def _on_close(self):
        self._save_settings()
        self.destroy()

    # ==================== 브라우저 버튼 ====================

    def on_copy_profile(self):
        if not messagebox.askyesno(
            "확인",
            "크롬을 완전히 종료했나요?\n\n"
            "크롬이 켜져 있으면 로그인 정보가 잠겨 있어 복사할 수 없습니다.\n"
            "작업 표시줄/트레이의 크롬도 모두 닫은 뒤 '예'를 누르세요.",
        ):
            return

        def work():
            try:
                self.log("크롬 로그인 정보 복사 중...")
                up_browser.copy_login_profile(log=self.log)
                self.log("→ 이제 '② 업로드용 크롬 열기'를 누르세요.")
            except Exception as e:
                self.log(f"❌ 복사 실패: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_open_chrome(self):
        def work():
            try:
                up_browser.launch_chrome(log=self.log)
                self.log("→ 열린 창에서 유튜브/틱톡 로그인 상태를 확인하세요.")
            except Exception as e:
                self.log(f"❌ 크롬 실행 실패: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_check(self):
        if self.busy:
            messagebox.showwarning("대기", "업로드가 진행 중입니다. 끝난 뒤에 눌러주세요.")
            return

        def work():
            try:
                results = up_check.run(log=self.log)
                if results:
                    self.after(0, lambda: messagebox.showinfo(
                        "진단 완료",
                        f"'진단결과' 폴더에 파일이 만들어졌습니다.\n\n{up_check.OUT_DIR}"))
            except Exception as e:
                self.log(f"❌ 진단 실패: {e}")

        threading.Thread(target=work, daemon=True).start()

    def on_dryrun(self):
        if self.busy:
            messagebox.showwarning("대기", "업로드가 진행 중입니다. 끝난 뒤에 눌러주세요.")
            return

        yt_video = self.secs["yts"]["video"].get().strip()
        tt_video = self.secs["tt"]["video"].get().strip()
        if not yt_video and not tt_video:
            messagebox.showerror("입력 오류",
                                 "연습용 영상 파일을 먼저 선택하세요.\n"
                                 "(유튜브(숏) 또는 틱톡 칸 · 실제로 게시되지는 않습니다)")
            return

        if not messagebox.askyesno(
            "연습 진단",
            "업로드 과정을 따라가며 화면들을 자동으로 진단합니다.\n\n"
            "· 게시/저장 버튼은 절대 누르지 않습니다\n"
            "· 다만 유튜브에 '임시저장' 영상이 하나 생깁니다\n"
            "  (스튜디오에서 직접 삭제하세요)\n\n"
            "진행할까요?",
        ):
            return

        yt_existing = ""
        if yt_video:
            yt_existing = simpledialog.askstring(
                "유튜브 - 이미 올려둔 영상",
                "댓글·예약 화면도 진단하려면\n"
                "이미 올려둔 아무 영상 주소나 넣어주세요 (선택).\n\n"
                "예: https://youtu.be/XXXXXXXXXXX\n"
                "비워두면 그 두 화면은 건너뜁니다.",
                parent=self,
            ) or ""

        def work():
            try:
                up_check.run_dryrun(yt_video=yt_video or None,
                                    tt_video=tt_video or None,
                                    yt_existing=yt_existing.strip() or None,
                                    log=self.log)
                self.after(0, lambda: messagebox.showinfo(
                    "연습 진단 완료",
                    f"'진단결과' 폴더를 통째로 전달해주세요.\n\n{up_check.OUT_DIR}"))
            except Exception as e:
                self.log(f"❌ 연습 진단 실패: {e}")

        threading.Thread(target=work, daemon=True).start()

    # ==================== 입력값 검사 ====================

    @staticmethod
    def _schedule_text(cfg):
        """예약 입력을 'YYYY-MM-DD HH:MM' 로. 예약을 껐으면 빈 문자열."""
        if not cfg.get("sched_on"):
            return ""

        d = (cfg.get("sched_date") or "").strip()
        t = (cfg.get("sched_time") or "").strip()
        if not d or not t:
            raise ValueError("예약 날짜와 시간을 모두 입력하세요.")

        try:
            dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError(
                "예약 시간 형식이 잘못됐습니다.\n"
                "날짜: YYYY-MM-DD (예: 2026-09-20) · 시간: HH:MM 24시간제 (예: 21:30)"
            )

        if dt <= datetime.now():
            raise ValueError(
                f"예약 시간이 이미 지났습니다: {dt:%Y-%m-%d %H:%M}\n"
                "'오늘' 버튼을 누르면 가까운 시각으로 다시 채워집니다."
            )

        return f"{dt:%Y-%m-%d %H:%M}"

    @staticmethod
    def _missing(cfg, spec):
        """안 채워진 칸 이름들을 돌려준다."""
        out = []
        for key, label in REQUIRED:
            if not (cfg.get(key) or "").strip():
                out.append(label)
        if spec["has_tags"] and not (cfg.get("tags") or "").strip():
            out.append("태그")
        return out

    def _prepare(self, key):
        """한 칸의 입력값을 검사해서 업로드 준비물을 만든다. 문제가 있으면 ValueError."""
        spec = SPEC_BY_KEY[key]
        s = self.secs[key]
        cfg = self._collect(s)
        name = spec["name"]

        missing = self._missing(cfg, spec)
        if missing:
            raise ValueError(f"[{name}] 다음 칸이 비어 있습니다:\n\n· " + "\n· ".join(missing))

        video = cfg["video"].strip()
        if not os.path.exists(video):
            raise ValueError(f"[{name}] 영상 파일이 없습니다:\n{video}")

        thumb = (cfg.get("thumb") or "").strip()
        if thumb and not os.path.exists(thumb):
            raise ValueError(f"[{name}] {spec['thumb_label']} 파일이 없습니다:\n{thumb}")

        try:
            cfg["schedule"] = self._schedule_text(cfg)
        except ValueError as e:
            raise ValueError(f"[{name}] {e}")

        if spec["site"] == "youtube":
            cfg["thumbnail"] = cfg.pop("thumb", "")
            func = up_youtube.upload
        else:
            cfg["cover"] = cfg.pop("thumb", "")
            func = up_tiktok.upload

        return name, func, cfg

    # ==================== 업로드 ====================

    def on_upload(self, key):
        try:
            jobs = [self._prepare(key)]
        except ValueError as e:
            messagebox.showerror("업로드할 수 없습니다", str(e))
            return
        self._start(jobs)

    def on_upload_both(self):
        """숏폼 + 틱톡 동시 업로드. 하나라도 덜 채워졌으면 아무것도 올리지 않는다."""
        jobs, problems = [], []
        for key in ("yts", "tt"):
            try:
                jobs.append(self._prepare(key))
            except ValueError as e:
                problems.append(str(e))

        if problems:
            messagebox.showerror(
                "동시 업로드를 할 수 없습니다",
                "아래 문제 때문에 둘 다 올리지 않았습니다.\n\n" + "\n\n".join(problems))
            return

        self._start(jobs)

    def _set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for s in self.secs.values():
            s["button"].config(state=state)
        self.combo_btn.config(state=state)

    def _start(self, jobs):
        if self.busy:
            messagebox.showwarning("대기", "이미 업로드가 진행 중입니다.")
            return

        self._save_settings()
        self.busy = True
        self._set_buttons(False)

        names = " + ".join(n for n, _, _ in jobs)
        self._set_status(f"{names} 업로드 중...", C_WARN)

        def work():
            done, failed = [], []
            try:
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    browser, context = up_browser.attach(p, log=self.log)

                    for name, func, cfg in jobs:
                        self.log("=" * 60)
                        self.log(f"🚀 {name} 업로드 시작"
                                 + (f"  (예약: {cfg['schedule']})" if cfg["schedule"] else "  (바로 게시)"))
                        self.log("=" * 60)
                        try:
                            page = up_browser.get_page(context, log=self.log)
                            func(page, cfg, self.log)
                            done.append(name)
                        except Exception as e:
                            failed.append(name)
                            self.log(f"❌ {name} 업로드 실패: {e}")
                            self.log(traceback.format_exc())

            except Exception as e:
                failed.append("브라우저")
                self.log(f"❌ 브라우저 연결 실패: {e}")
                self.log(traceback.format_exc())

            if len(jobs) > 1 or failed:
                self.log("─" * 60)
                if done:
                    self.log(f"✔ 완료: {', '.join(done)}")
                if failed:
                    self.log(f"✘ 실패: {', '.join(failed)}")

            if failed:
                self.after(0, lambda: self._set_status("실패", C_ERR))
            else:
                self.after(0, lambda: self._set_status("완료", C_OK))

            # 결과 확인용으로 탭은 일부러 닫지 않는다
            self.busy = False
            self.after(0, lambda: self._set_buttons(True))

        threading.Thread(target=work, daemon=True).start()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
