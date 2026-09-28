#!/usr/bin/env python3
"""
유튜브 / 틱톡 자동 업로드 프로그램 - GUI

  [상단]   프로그램 제목 + 현재 상태
  [준비]   크롬 열기 / 로그인 정보 복사 / 화면 진단
  [왼쪽]   유튜브 섹션  -> [유튜브 업로드] 버튼
  [오른쪽] 틱톡 섹션    -> [틱톡 업로드]  버튼
  [하단]   진행 로그
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

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "uploader_settings.json")

VIDEO_TYPES = [("영상 파일", "*.mp4 *.mov *.avi *.mkv *.webm *.flv"), ("모든 파일", "*.*")]
IMAGE_TYPES = [("이미지 파일", "*.jpg *.jpeg *.png *.webp *.bmp"), ("모든 파일", "*.*")]

# ==================== 색·글꼴 ====================

BG        = "#eef0f4"   # 창 배경
CARD      = "#ffffff"   # 카드 배경
INK       = "#16191d"   # 본문 글자
MUTED     = "#6b7280"   # 설명 글자
LINE      = "#dfe3e8"   # 테두리
HEAD_BG   = "#14171c"   # 상단 바
CHIP      = "#eceff3"   # 작은 버튼
CHIP_ON   = "#dde2e8"

YT        = "#ff0033"   # 유튜브
YT_ON     = "#d4002a"
TT        = "#111111"   # 틱톡
TT_ON     = "#333333"

LOG_BG    = "#11141a"
LOG_FG    = "#ccd1d9"
C_OK      = "#4ade80"
C_ERR     = "#f87171"
C_WARN    = "#fbbf24"
C_STEP    = "#60a5fa"
C_DIM     = "#5b6472"

FONT  = "맑은 고딕"
MONO  = "Consolas"


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("유튜브 / 틱톡 자동 업로드")
        self.geometry("1240x980")
        self.minsize(1060, 800)
        self.configure(bg=BG)

        self.log_q = queue.Queue()
        self.busy = False          # 업로드 중복 실행 방지

        self._build_ui()
        self._load_settings()
        self._prefill_schedule()
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
        # 로그를 먼저 아래쪽에 고정해야 공간이 모자랄 때 찌그러지지 않는다
        self._build_log()
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

    # ---------- 카드 / 버튼 만들기 ----------

    def _card(self, parent, **grid):
        """흰 카드 한 장."""
        c = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                     highlightcolor=LINE, highlightthickness=1)
        if grid:
            c.grid(**grid)
        return c

    def _chip(self, parent, text, cmd):
        """작은 회색 버튼."""
        return tk.Button(parent, text=text, command=cmd,
                         bg=CHIP, fg=INK, activebackground=CHIP_ON, activeforeground=INK,
                         font=(FONT, 9, "bold"), relief="flat", bd=0,
                         padx=14, pady=7, cursor="hand2")

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

        self._title(card, "1단계 · 브라우저 준비").pack(anchor="w", padx=16, pady=(12, 8))

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
            "①은 크롬을 완전히 종료한 뒤 한 번만 누르면 됩니다. 이후 ②로 연 창에서 로그인이 유지됩니다.\n"
            "③은 지금 열려 있는 화면 하나를, ④는 업로드 과정을 따라가며 여러 화면을 자동 진단합니다 (게시는 하지 않음).",
        ).pack(anchor="w", padx=16, pady=(10, 12))

    # ---------- 유튜브 / 틱톡 ----------

    def _build_sections(self):
        # 창이 작아도 예약칸·버튼이 잘리지 않도록 스크롤 가능한 영역에 넣는다
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=14, pady=7)

        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)

        mid = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=mid, anchor="nw")

        def sync_scrollbar():
            """내용이 다 보이면 스크롤바를 숨긴다."""
            need = mid.winfo_reqheight() > canvas.winfo_height() + 2
            if need and not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y")
            elif not need and vsb.winfo_ismapped():
                vsb.pack_forget()

        def on_canvas(e):
            canvas.itemconfig(win, width=e.width)
            sync_scrollbar()

        def on_inner(e):
            # 카드가 필요한 만큼 커지도록 높이는 건드리지 않는다
            canvas.configure(scrollregion=canvas.bbox("all"))
            sync_scrollbar()

        canvas.bind("<Configure>", on_canvas)
        mid.bind("<Configure>", on_inner)
        self._scroll_canvas = canvas
        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Button-4>", self._on_wheel)
        self.bind_all("<Button-5>", self._on_wheel)

        mid.columnconfigure(0, weight=1, uniform="col")
        mid.columnconfigure(1, weight=1, uniform="col")
        mid.rowconfigure(0, weight=1)

        self.yt = self._build_section(
            mid, col=0, name="유튜브", accent=YT, accent_on=YT_ON,
            thumb_label="썸네일",
            privacy_opts=None,          # 흐름이 정해져 있어 공개설정 선택 없음
            has_pin=True,
            note="업로드 → 일부공개 게시 → 댓글 작성·고정 → 예약 전환 순서로 진행됩니다\n고정 댓글을 비우면 댓글 단계를 건너뜁니다",
            btn_text="▶   유튜브 업로드",
            btn_cmd=self.on_upload_youtube,
        )

        self.tt = self._build_section(
            mid, col=1, name="틱톡", accent=TT, accent_on=TT_ON,
            thumb_label="커버 이미지",
            privacy_opts=[("전체 공개", "public"), ("친구만", "friends"), ("나만 보기", "private")],
            has_pin=False,
            note="예약은 최소 20분 뒤 ~ 최대 10일 뒤, 5분 단위만 가능합니다\n커버 이미지는 틱톡 화면에 따라 적용이 안 될 수 있습니다",
            btn_text="▶   틱톡 업로드",
            btn_cmd=self.on_upload_tiktok,
        )

        # 유튜브에만 있는 옵션: 아동용 여부
        self.yt["kids"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.yt["extra"], text="아동용 동영상입니다",
                        variable=self.yt["kids"]).pack(side="left")

    def _on_wheel(self, event):
        """마우스 휠: 글상자 위에서는 그 글상자를, 그 밖에서는 화면 전체를 스크롤."""
        w = event.widget
        if isinstance(w, tk.Text):
            return None                       # 글상자는 스스로 처리
        step = -1 if getattr(event, "delta", 0) > 0 or event.num == 4 else 1
        try:
            self._scroll_canvas.yview_scroll(step, "units")
        except Exception:
            pass
        return "break"

    def _build_section(self, parent, col, name, accent, accent_on, thumb_label,
                       privacy_opts, has_pin, note, btn_text, btn_cmd):
        """유튜브/틱톡 공통 입력 섹션 생성."""
        card = self._card(parent, row=0, column=col, sticky="nsew",
                          padx=(0, 7) if col == 0 else (7, 0))

        tk.Frame(card, bg=accent, height=4).pack(fill="x")      # 상단 포인트 줄

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(head, text=name, bg=CARD, fg=accent,
                 font=(FONT, 15, "bold")).pack(anchor="w")
        self._hint(head, note, wraplength=470).pack(anchor="w", pady=(2, 0))

        # 업로드 버튼은 내용이 길어도 잘리면 안 되므로 카드 아래쪽에 먼저 고정한다
        btn = tk.Button(card, text=btn_text, command=btn_cmd,
                        bg=accent, fg="#ffffff",
                        activebackground=accent_on, activeforeground="#ffffff",
                        disabledforeground="#c8ccd2",
                        font=(FONT, 12, "bold"), relief="flat", bd=0,
                        cursor="hand2", pady=12)
        btn.pack(side="bottom", fill="x", padx=16, pady=(8, 16))

        extra = tk.Frame(card, bg=CARD)
        extra.pack(side="bottom", fill="x", padx=16)

        f = tk.Frame(card, bg=CARD)
        f.pack(fill="both", expand=True, padx=16, pady=(10, 0))
        f.columnconfigure(1, weight=1)

        s = {}
        r = 0

        def row_label(text, sticky="w"):
            self._label(f, text).grid(row=r, column=0, sticky=sticky, pady=4, padx=(0, 10))

        # 제목
        row_label("제목")
        s["title"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["title"], font=(FONT, 9)).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 상세정보(설명)
        row_label("상세정보", "nw")
        s["desc"] = tk.Text(f, width=1, height=4, wrap="word", font=(FONT, 9),
                            relief="flat", bd=0, padx=8, pady=6,
                            highlightbackground=LINE, highlightcolor="#9aa4b2",
                            highlightthickness=1)
        s["desc"].grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 태그
        row_label("태그")
        s["tags"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["tags"], font=(FONT, 9)).grid(
            row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        self._hint(f, "쉼표(,)로 구분 · 예: 브이로그, 일상, 맛집").grid(
            row=r, column=1, columnspan=2, sticky="w")
        r += 1

        # 고정 댓글 (유튜브 전용)
        if has_pin:
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
            row=r, column=2, padx=(8, 0))
        r += 1

        # 썸네일 / 커버
        row_label(thumb_label)
        s["thumb"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["thumb"], font=(FONT, 9)).grid(
            row=r, column=1, sticky="ew", pady=4)
        self._chip(f, "찾기", lambda v=s["thumb"]: self._pick(v, IMAGE_TYPES)).grid(
            row=r, column=2, padx=(8, 0))
        r += 1

        # 공개 설정 (틱톡만)
        if privacy_opts:
            row_label("공개 설정")
            s["privacy"] = tk.StringVar(value=privacy_opts[0][1])
            pf = tk.Frame(f, bg=CARD)
            pf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
            for lb, val in privacy_opts:
                ttk.Radiobutton(pf, text=lb, value=val,
                                variable=s["privacy"]).pack(side="left", padx=(0, 12))
            r += 1

        # ---- 예약 게시 ----
        tk.Frame(f, bg=LINE, height=1).grid(row=r, column=0, columnspan=3,
                                            sticky="ew", pady=(10, 8))
        r += 1

        s["sched_on"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="예약 게시", variable=s["sched_on"]).grid(
            row=r, column=0, sticky="w", pady=4)

        sf = tk.Frame(f, bg=CARD)
        sf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)

        s["sched_date"] = tk.StringVar()
        s["sched_time"] = tk.StringVar()
        self._label(sf, "날짜").pack(side="left")
        ttk.Entry(sf, textvariable=s["sched_date"], width=12,
                  font=(FONT, 9)).pack(side="left", padx=(5, 12))
        self._label(sf, "시간").pack(side="left")
        ttk.Entry(sf, textvariable=s["sched_time"], width=7,
                  font=(FONT, 9)).pack(side="left", padx=(5, 12))
        self._chip(sf, "오늘", lambda d=s: self._set_today(d)).pack(side="left", padx=2)
        self._chip(sf, "+1일", lambda v=s["sched_date"]: self._shift_day(v, 1)).pack(side="left", padx=2)
        r += 1

        self._hint(f, "YYYY-MM-DD / HH:MM  (예: 2026-09-20 / 09:00) · 체크를 끄면 바로 게시").grid(
            row=r, column=1, columnspan=2, sticky="w")
        r += 1

        # 남는 공간은 위로 밀어 올린다
        f.rowconfigure(r, weight=1)

        s["extra"] = extra
        s["button"] = btn
        return s

    # ---------- 로그 ----------

    def _build_log(self):
        card = self._card(self)
        card.pack(side="bottom", fill="x", padx=14, pady=(7, 14))

        self._title(card, "진행 상황").pack(anchor="w", padx=16, pady=(10, 6))

        box = tk.Frame(card, bg=CARD)
        box.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self.log_box = tk.Text(box, width=1, height=8, wrap="word",
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
        """날짜칸을 하루 뒤로 (비어 있으면 오늘 기준)."""
        try:
            base = datetime.strptime(var.get().strip(), "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
        var.set(f"{base + timedelta(days=days):%Y-%m-%d}")

    def _set_today(self, s):
        """예약칸을 오늘 기본값으로 되돌린다."""
        d, t = self._default_schedule()
        s["sched_date"].set(d)
        s["sched_time"].set(t)

    @staticmethod
    def _default_schedule():
        """
        기본 예약 시각 = 오늘.

        시간은 09:00 을 쓰되, 이미 지났으면 '지금부터 1시간 뒤'(5분 단위)로 잡는다.
        (지난 시각을 넣어두면 업로드 버튼에서 바로 막히기 때문)
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

        for s in (self.yt, self.tt):
            cur = s["sched_date"].get().strip()
            valid = False
            if cur:
                try:
                    valid = datetime.strptime(cur, "%Y-%m-%d").date() >= today
                except ValueError:
                    valid = False

            if not valid:
                # 저장된 날짜가 없거나 이미 지났으면 날짜·시간을 함께 되돌린다
                s["sched_date"].set(d)
                s["sched_time"].set(t)
            elif not s["sched_time"].get().strip():
                s["sched_time"].set(t)

    # ==================== 로그 ====================

    def log(self, msg):
        """다른 스레드에서도 안전하게 로그 남기기."""
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
        if head[:1] in ("[", "🚀", "🔍", "🧪") or head.startswith("→"):
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
            "tags": s["tags"].get(),
            "video": s["video"].get(),
            "thumb": s["thumb"].get(),
            "sched_on": bool(s["sched_on"].get()),
            "sched_date": s["sched_date"].get().strip(),
            "sched_time": s["sched_time"].get().strip(),
        }
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
        s["tags"].set(data.get("tags", ""))
        s["video"].set(data.get("video", ""))
        s["thumb"].set(data.get("thumb", ""))
        s["sched_on"].set(bool(data.get("sched_on", True)))
        s["sched_date"].set(data.get("sched_date", ""))
        s["sched_time"].set(data.get("sched_time", ""))
        if "privacy" in s and data.get("privacy"):
            s["privacy"].set(data["privacy"])
        if "pin" in s:
            s["pin"].delete("1.0", "end")
            s["pin"].insert("1.0", data.get("pin_comment", ""))
        if "kids" in s:
            s["kids"].set(bool(data.get("kids", False)))

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as fp:
                json.dump({"youtube": self._collect(self.yt),
                           "tiktok": self._collect(self.tt)},
                          fp, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"! 설정 저장 실패: {e}")

    def _load_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            return
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fp:
                data = json.load(fp)
            self._apply(self.yt, data.get("youtube", {}))
            self._apply(self.tt, data.get("tiktok", {}))
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
        """업로드가 멈춘 화면을 진단해서 '진단결과' 폴더에 파일로 남긴다."""
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
        """업로드 과정을 따라가며 여러 화면을 자동 진단 (게시/저장은 안 함)."""
        if self.busy:
            messagebox.showwarning("대기", "업로드가 진행 중입니다. 끝난 뒤에 눌러주세요.")
            return

        yt_video = self.yt["video"].get().strip()
        tt_video = self.tt["video"].get().strip()
        if not yt_video and not tt_video:
            messagebox.showerror("입력 오류",
                                 "연습용 영상 파일을 먼저 선택하세요.\n"
                                 "(실제로 게시되지는 않습니다)")
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

    # ==================== 예약 시간 검사 ====================

    @staticmethod
    def _schedule_text(cfg):
        """
        예약 입력을 'YYYY-MM-DD HH:MM' 문자열로 만든다.
        예약을 끈 경우 빈 문자열. 형식이 틀리면 ValueError.
        """
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
                "예약 시간 형식이 잘못됐습니다.\n\n"
                "날짜: YYYY-MM-DD (예: 2026-09-20)\n"
                "시간: HH:MM  24시간제 (예: 21:30)"
            )

        if dt <= datetime.now():
            raise ValueError(
                f"예약 시간이 이미 지났습니다: {dt:%Y-%m-%d %H:%M}\n\n"
                "'오늘' 버튼을 누르면 가까운 시각으로 다시 채워집니다."
            )

        return f"{dt:%Y-%m-%d %H:%M}"

    # ==================== 업로드 버튼 ====================

    def on_upload_youtube(self):
        cfg = self._collect(self.yt)
        cfg["thumbnail"] = cfg.pop("thumb", "")
        self._run_upload("유튜브", up_youtube.upload, cfg)

    def on_upload_tiktok(self):
        cfg = self._collect(self.tt)
        cfg["cover"] = cfg.pop("thumb", "")
        self._run_upload("틱톡", up_tiktok.upload, cfg)

    def _set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        for s in (self.yt, self.tt):
            s["button"].config(state=state)

    def _run_upload(self, name, func, cfg):
        if self.busy:
            messagebox.showwarning("대기", "이미 업로드가 진행 중입니다.")
            return

        if not cfg.get("video"):
            messagebox.showerror("입력 오류", "영상 파일을 선택하세요.")
            return
        if not os.path.exists(cfg["video"]):
            messagebox.showerror("입력 오류", f"영상 파일이 없습니다:\n{cfg['video']}")
            return
        if not cfg.get("title", "").strip():
            messagebox.showerror("입력 오류", "제목을 입력하세요.")
            return

        try:
            cfg["schedule"] = self._schedule_text(cfg)
        except ValueError as e:
            messagebox.showerror("예약 시간 오류", str(e))
            return

        self._save_settings()
        self.busy = True
        self._set_buttons(False)
        self._set_status(f"{name} 업로드 중...", C_WARN)

        def work():
            try:
                from playwright.sync_api import sync_playwright

                self.log("=" * 60)
                self.log(f"🚀 {name} 업로드 시작"
                         + (f"  (예약: {cfg['schedule']})" if cfg["schedule"] else "  (바로 게시)"))
                self.log("=" * 60)

                with sync_playwright() as p:
                    browser, context = up_browser.attach(p, log=self.log)
                    page = up_browser.get_page(context, log=self.log)
                    func(page, cfg, self.log)

                self.after(0, lambda: self._set_status("완료", C_OK))

            except Exception as e:
                self.log(f"❌ {name} 업로드 실패: {e}")
                self.log(traceback.format_exc())
                self.after(0, lambda: self._set_status("실패", C_ERR))
            finally:
                # 결과 확인용으로 탭은 일부러 닫지 않는다
                self.busy = False
                self.after(0, lambda: self._set_buttons(True))

        threading.Thread(target=work, daemon=True).start()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
