#!/usr/bin/env python3
"""
유튜브 / 틱톡 자동 업로드 프로그램 - GUI

  [상단]  제목 바 + 진행 표시 + 상태 배지
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

# ==================== 색 ====================

BG        = "#eaedf2"   # 창 배경
CARD      = "#ffffff"   # 카드
FIELD     = "#f6f8fa"   # 입력칸 배경
SHADE     = "#eef1f5"   # 미리보기 자리
INK       = "#0f172a"   # 본문 글자
MUTED     = "#64748b"   # 설명 글자
FAINT     = "#94a3b8"   # 더 흐린 글자
LINE      = "#dde3ea"   # 테두리
SHADOW    = "#d2d9e3"   # 카드 그림자
FOCUS     = "#2563eb"   # 입력칸 포커스

HEAD_BG   = "#0d1424"   # 상단 바
HEAD_SUB  = "#7c8ba5"   # 상단 바 보조 글자
PILL_BG   = "#1c2740"   # 상태 배지 배경

CHIP      = "#eef1f5"   # 작은 버튼
CHIP_ON   = "#dfe5ec"
CHIP_INK  = "#334155"

YT        = "#ff0033"   # 유튜브 (숏)
YT_ON     = "#e0002d"
YTL       = "#9d1230"   # 유튜브 (롱)
YTL_ON    = "#851028"
TT        = "#15161a"   # 틱톡
TT_ON     = "#2c2e36"
BOTH      = "#0d9488"   # 동시 업로드
BOTH_ON   = "#0b7f75"

DIM_BTN   = "#cbd5e1"   # 비활성 버튼
DIM_INK   = "#eef2f7"

OK_TXT    = "#15803d"
WARN_TXT  = "#b45309"

LOG_BG    = "#0f1523"
LOG_FG    = "#c7d0dd"
C_OK      = "#4ade80"
C_ERR     = "#f87171"
C_WARN    = "#fbbf24"
C_STEP    = "#60a5fa"
C_DIM     = "#55637a"

FONT  = "맑은 고딕"
MONO  = "Consolas"

# ==================== 섹션 정의 ====================
# 세 칸은 서로 완전히 독립이다. 내용도 따로, 버튼도 따로.

SPECS = [
    {
        "key": "yts", "name": "유튜브 (숏)", "save": "youtube_short", "glyph": "▶",
        "tag": "숏폼", "accent": YT, "accent_on": YT_ON, "site": "youtube",
        "thumb_label": "썸네일", "has_tags": True, "has_pin": True,
        "has_kids": True, "privacy": None, "ratio": (16, 9), "box": (116, 65),
        "note": "일부공개 게시 → 댓글 고정 → 예약 전환 · 태그는 쉼표로 구분",
        "btn": "숏 업로드",
    },
    {
        "key": "ytl", "name": "유튜브 (롱)", "save": "youtube_long", "glyph": "▶",
        "tag": "롱폼", "accent": YTL, "accent_on": YTL_ON, "site": "youtube",
        "thumb_label": "썸네일", "has_tags": True, "has_pin": True,
        "has_kids": True, "privacy": None, "ratio": (16, 9), "box": (116, 65),
        "note": "진행 방식은 숏과 같고 내용만 따로 · 태그는 쉼표로 구분",
        "btn": "롱 업로드",
    },
    {
        "key": "tt", "name": "틱톡", "save": "tiktok", "glyph": "♪",
        "tag": "숏폼", "accent": TT, "accent_on": TT_ON, "site": "tiktok",
        "thumb_label": "커버 이미지", "has_tags": False, "has_pin": False,
        "has_kids": False,
        "privacy": [("전체 공개", "public"), ("친구만", "friends"), ("나만 보기", "private")],
        "ratio": (9, 16), "box": (61, 108),
        "note": "해시태그는 제목·상세정보에 #태그로 직접 · 예: 카페투어 #카페",
        "btn": "틱톡 업로드",
    },
]

SPEC_BY_KEY = {sp["key"]: sp for sp in SPECS}

# 업로드 시 반드시 채워져 있어야 하는 칸
REQUIRED = [("title", "제목"), ("desc", "상세정보"), ("video", "영상 파일")]


# ==================== 둥근 버튼 ====================

class RoundButton(tk.Canvas):
    """모서리가 둥글고 마우스를 올리면 색이 바뀌는 버튼."""

    def __init__(self, parent, text, command=None, *, fill, hover,
                 fg="#ffffff", behind=CARD, font=(FONT, 10, "bold"),
                 radius=9, height=40, width=0):
        super().__init__(parent, height=height, width=width,
                         highlightthickness=0, bd=0, bg=behind, takefocus=0)
        self._text = text
        self._cmd = command
        self._fill, self._hover_fill, self._fg = fill, hover, fg
        self._radius, self._font = radius, font
        self._state = "normal"
        self._over = False

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._click)
        super().configure(cursor="hand2")

    # 둥근 사각형 (폴리곤 + smooth 로 흉내)
    def _round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        if self._state == "disabled":
            fill, fg = DIM_BTN, DIM_INK
        else:
            fill = self._hover_fill if self._over else self._fill
            fg = self._fg
        self._round_rect(1, 1, w - 1, h - 1, self._radius, fill=fill, outline=fill)
        self.create_text(w // 2, h // 2 + 1, text=self._text, fill=fg, font=self._font)

    def _enter(self, _=None):
        if self._state == "normal":
            self._over = True
            self._draw()

    def _leave(self, _=None):
        self._over = False
        self._draw()

    def _click(self, _=None):
        if self._state == "normal" and self._cmd:
            self._cmd()

    # tk 위젯처럼 state / text 를 바꿀 수 있게
    def config(self, **kw):
        state = kw.pop("state", None)
        text = kw.pop("text", None)
        if state is not None:
            self._state = state
            super().configure(cursor="hand2" if state == "normal" else "arrow")
        if text is not None:
            self._text = text
        if kw:
            super().configure(**kw)
        if state is not None or text is not None:
            self._draw()

    configure = config


class StatusPill(tk.Canvas):
    """상단 바 오른쪽의 상태 배지."""

    def __init__(self, parent, bg=HEAD_BG):
        super().__init__(parent, height=30, width=190, highlightthickness=0,
                         bd=0, bg=bg, takefocus=0)
        self._text, self._color = "준비됨", C_OK
        self.bind("<Configure>", lambda e: self._draw())

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        r = h // 2
        pts = [r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
               w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0]
        self.create_polygon(pts, smooth=True, fill=PILL_BG, outline=PILL_BG)
        self.create_oval(14, h // 2 - 4, 22, h // 2 + 4,
                         fill=self._color, outline=self._color)
        self.create_text(30, h // 2 + 1, text=self._text, anchor="w",
                         fill="#dbe3ef", font=(FONT, 9, "bold"))

    def set(self, text, color):
        self._text, self._color = text, color
        self._draw()


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
        style.configure("TRadiobutton", background=CARD, foreground=INK, font=(FONT, 9))
        style.configure("TCheckbutton", background=CARD, foreground=INK, font=(FONT, 9))
        style.map("TRadiobutton", background=[("active", CARD)])
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("App.Horizontal.TProgressbar", troughcolor=HEAD_BG,
                        background=BOTH, bordercolor=HEAD_BG,
                        lightcolor=BOTH, darkcolor=BOTH, thickness=3)
        style.configure("Card.Vertical.TScrollbar", troughcolor=BG, background="#c7cfda",
                        bordercolor=BG, arrowcolor=MUTED)

        self._build_header()
        self._build_toolbar()
        # 아래쪽부터 자리를 잡아야 공간이 모자랄 때 찌그러지지 않는다
        self._build_log()
        self._build_combo()
        self._build_sections()

    # ---------- 상단 바 ----------

    def _build_header(self):
        bar = tk.Frame(self, bg=HEAD_BG, height=66)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=HEAD_BG)
        left.pack(side="left", padx=22)
        tk.Label(left, text="유튜브 / 틱톡 자동 업로드", bg=HEAD_BG, fg="#ffffff",
                 font=(FONT, 15, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Label(left, text="제목 · 상세정보 · 태그 · 파일 · 예약까지 한 번에",
                 bg=HEAD_BG, fg=HEAD_SUB, font=(FONT, 9)).pack(anchor="w")

        self.status = StatusPill(bar)
        self.status.pack(side="right", padx=22)

        # 진행 막대는 업로드 중에만 보여준다
        self.prog = ttk.Progressbar(bar, mode="indeterminate",
                                    style="App.Horizontal.TProgressbar")

    def _set_status(self, text, color):
        self.status.set(text, color)

    def _prog_off(self):
        self.prog.stop()
        self.prog.pack_forget()

    # ---------- 작은 부품 ----------

    def _card(self, parent, pack=None, grid=None):
        """흰 카드. 오른쪽·아래에 얇은 그림자를 둬서 평평해 보이지 않게 한다."""
        holder = tk.Frame(parent, bg=SHADOW)
        if grid:
            holder.grid(**grid)
        if pack:
            holder.pack(**pack)
        c = tk.Frame(holder, bg=CARD, highlightbackground=LINE,
                     highlightcolor=LINE, highlightthickness=1)
        c.pack(fill="both", expand=True, padx=(0, 2), pady=(0, 2))
        return c

    def _chip(self, parent, text, cmd, behind=CARD, width=0):
        return RoundButton(parent, text, cmd, fill=CHIP, hover=CHIP_ON,
                           fg=CHIP_INK, behind=behind, font=(FONT, 9, "bold"),
                           radius=8, height=30, width=width)

    def _title(self, parent, text, bg=CARD):
        return tk.Label(parent, text=text, bg=bg, fg=INK, font=(FONT, 10, "bold"))

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=(FONT, 9))

    def _hint(self, parent, text, bg=CARD, **kw):
        return tk.Label(parent, text=text, bg=bg, fg=MUTED,
                        font=(FONT, 8), justify="left", **kw)

    def _field(self, parent, widget_maker):
        """입력칸을 테두리·포커스 표시가 있는 상자로 감싼다."""
        wrap = tk.Frame(parent, bg=FIELD, highlightthickness=1,
                        highlightbackground=LINE, highlightcolor=LINE)
        w = widget_maker(wrap)
        w.pack(fill="both", expand=True, padx=8, pady=5)
        w.bind("<FocusIn>", lambda e: wrap.config(highlightbackground=FOCUS,
                                                  highlightcolor=FOCUS))
        w.bind("<FocusOut>", lambda e: wrap.config(highlightbackground=LINE,
                                                   highlightcolor=LINE))
        return wrap, w

    def _entry(self, parent, var, width=0):
        def make(p):
            return tk.Entry(p, textvariable=var, font=(FONT, 9), bd=0,
                            relief="flat", bg=FIELD, fg=INK, insertbackground=INK,
                            width=width or 1, highlightthickness=0)
        return self._field(parent, make)

    def _textbox(self, parent, height):
        def make(p):
            return tk.Text(p, width=1, height=height, wrap="word", font=(FONT, 9),
                           relief="flat", bd=0, bg=FIELD, fg=INK,
                           insertbackground=INK, highlightthickness=0)
        return self._field(parent, make)

    # ---------- 준비 단계 ----------

    def _build_toolbar(self):
        card = self._card(self, pack=dict(fill="x", padx=16, pady=(14, 8)))

        self._title(card, "1단계 · 브라우저 준비").pack(anchor="w", padx=18, pady=(10, 6))

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="x", padx=18)
        for text, cmd, w in [
            ("①  크롬 로그인 정보 가져오기", self.on_copy_profile, 210),
            ("②  업로드용 크롬 열기", self.on_open_chrome, 175),
            ("③  화면 진단하기", self.on_check, 140),
            ("④  연습 진단 (자동)", self.on_dryrun, 155),
        ]:
            self._chip(row, text, cmd, width=w).pack(side="left", padx=(0, 9))

        self._hint(
            card,
            "①은 크롬을 완전히 종료한 뒤 한 번만 · ③은 열려 있는 화면 하나를, "
            "④는 업로드 과정을 따라가며 여러 화면을 자동 진단합니다 (게시는 하지 않음)",
        ).pack(anchor="w", padx=18, pady=(8, 10))

    # ---------- 동시 업로드 ----------

    def _build_combo(self):
        card = self._card(self, pack=dict(side="bottom", fill="x", padx=16, pady=(0, 8)))

        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=18, pady=10)

        self.combo_btn = RoundButton(
            inner, "⚡   유튜브(숏)  +  틱톡   동시 업로드",
            self.on_upload_both, fill=BOTH, hover=BOTH_ON,
            font=(FONT, 12, "bold"), radius=10, height=42)
        self.combo_btn.pack(side="left", fill="x", expand=True)

        self._hint(inner, "유튜브 먼저 → 틱톡 순서\n하나라도 덜 채워지면 둘 다 안 올림").pack(
            side="left", padx=(14, 0))

    # ---------- 세 칸 ----------

    def _build_sections(self):
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=8)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview,
                            style="Card.Vertical.TScrollbar")
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
            pad = (0 if i == 0 else 6, 0 if i == len(SPECS) - 1 else 6)
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
        card = self._card(parent, grid=dict(row=0, column=col, sticky="nsew", padx=pad))

        # ---- 색 띠 제목 ----
        strip = tk.Frame(card, bg=accent, height=38)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        tk.Label(strip, text=spec["glyph"], bg=accent, fg="#ffffff",
                 font=(FONT, 12, "bold")).pack(side="left", padx=(16, 8))
        tk.Label(strip, text=spec["name"], bg=accent, fg="#ffffff",
                 font=(FONT, 12, "bold")).pack(side="left")
        tk.Label(strip, text=spec["tag"], bg=accent, fg="#ffffff",
                 font=(FONT, 9)).pack(side="right", padx=16)

        self._hint(card, spec["note"], wraplength=420).pack(
            anchor="w", padx=16, pady=(6, 0))

        # 업로드 버튼은 내용이 길어도 잘리면 안 되므로 카드 아래쪽에 먼저 고정
        btn = RoundButton(card, "▶   " + spec["btn"],
                          lambda k=spec["key"]: self.on_upload(k),
                          fill=accent, hover=accent_on,
                          font=(FONT, 11, "bold"), radius=9, height=42)
        btn.pack(side="bottom", fill="x", padx=16, pady=(6, 12))

        extra = tk.Frame(card, bg=CARD)
        extra.pack(side="bottom", fill="x", padx=16)

        f = tk.Frame(card, bg=CARD)
        f.pack(fill="both", expand=True, padx=16, pady=(6, 0))
        f.columnconfigure(1, weight=1)

        s = {"spec": spec, "button": btn, "extra": extra}
        r = 0

        def row_label(text, sticky="w"):
            self._label(f, text).grid(row=r, column=0, sticky=sticky, pady=3, padx=(0, 10))

        # 제목
        row_label("제목")
        s["title"] = tk.StringVar()
        self._entry(f, s["title"])[0].grid(row=r, column=1, columnspan=2,
                                           sticky="ew", pady=3)
        r += 1

        # 상세정보
        row_label("상세정보", "nw")
        wrap, s["desc"] = self._textbox(f, 3)
        wrap.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
        r += 1

        # 태그 (유튜브만)
        if spec["has_tags"]:
            row_label("태그")
            s["tags"] = tk.StringVar()
            self._entry(f, s["tags"])[0].grid(row=r, column=1, columnspan=2,
                                              sticky="ew", pady=3)
            r += 1

        # 고정 댓글 (유튜브만)
        if spec["has_pin"]:
            row_label("고정 댓글", "nw")
            wrap, s["pin"] = self._textbox(f, 2)
            wrap.grid(row=r, column=1, columnspan=2, sticky="ew", pady=3)
            r += 1

        # 영상 파일
        row_label("영상 파일")
        s["video"] = tk.StringVar()
        self._entry(f, s["video"])[0].grid(row=r, column=1, sticky="ew", pady=3)
        self._chip(f, "찾기", lambda v=s["video"]: self._pick(v, VIDEO_TYPES),
                   width=52).grid(row=r, column=2, padx=(8, 0))
        r += 1

        # 썸네일 / 커버 + 미리보기
        row_label(spec["thumb_label"])
        s["thumb"] = tk.StringVar()
        self._entry(f, s["thumb"])[0].grid(row=r, column=1, sticky="ew", pady=3)
        self._chip(f, "찾기", lambda v=s["thumb"]: self._pick(v, IMAGE_TYPES),
                   width=52).grid(row=r, column=2, padx=(8, 0))
        r += 1

        self._build_preview(f, s, r, spec)
        r += 1

        # 공개 설정 (틱톡만)
        if spec["privacy"]:
            row_label("공개 설정")
            s["privacy"] = tk.StringVar(value=spec["privacy"][0][1])
            pf = tk.Frame(f, bg=CARD)
            pf.grid(row=r, column=1, columnspan=2, sticky="w", pady=3)
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
        ttk.Checkbutton(sf, text="예약", variable=s["sched_on"]).pack(side="left", padx=(0, 8))
        self._entry(sf, s["sched_date"], width=11)[0].pack(side="left", padx=(0, 5))
        self._entry(sf, s["sched_time"], width=6)[0].pack(side="left", padx=(0, 8))
        self._chip(sf, "오늘", lambda d=s: self._set_today(d), width=50).pack(side="left", padx=2)
        self._chip(sf, "+1일", lambda v=s["sched_date"]: self._shift_day(v, 1),
                   width=50).pack(side="left", padx=2)
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

        img = tk.Label(box, bg=SHADE, fg=FAINT, font=(FONT, 8),
                       text="이미지를 고르면\n미리보기")
        img.pack(expand=True)

        info = tk.Label(wrap, bg=CARD, fg=MUTED, font=(FONT, 8), justify="left")
        info.pack(side="left", padx=(12, 0), anchor="n")

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
            return show("이미지를 고르면\n미리보기", "", FAINT)
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
        card = self._card(self, pack=dict(side="bottom", fill="x", padx=16, pady=(8, 16)))

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=18, pady=(8, 5))
        self._title(head, "진행 상황").pack(side="left")
        self._chip(head, "지우기", self._clear_log, width=58).pack(side="right")

        box = tk.Frame(card, bg=CARD)
        box.pack(fill="both", expand=True, padx=18, pady=(0, 12))

        self.log_box = tk.Text(box, width=1, height=5, wrap="word",
                               bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
                               font=(MONO, 9), state="disabled",
                               relief="flat", bd=0, padx=14, pady=10,
                               highlightthickness=1, highlightbackground="#1c2434")
        self.log_box.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(box, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=sb.set)

        for tag, color in (("ok", C_OK), ("err", C_ERR), ("warn", C_WARN),
                           ("step", C_STEP), ("dim", C_DIM)):
            self.log_box.tag_config(tag, foreground=color)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
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
        self.prog.pack(side="bottom", fill="x")
        self.prog.start(14)

        names = " + ".join(n for n, _, _ in jobs)
        self._set_status(f"{names} 업로드 중", C_WARN)

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
            self.after(0, self._prog_off)
            self.after(0, lambda: self._set_buttons(True))

        threading.Thread(target=work, daemon=True).start()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
