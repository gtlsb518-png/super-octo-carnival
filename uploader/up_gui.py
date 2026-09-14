#!/usr/bin/env python3
"""
유튜브 / 틱톡 자동 업로드 프로그램 - GUI

  [상단]  브라우저 준비 (크롬 열기 / 로그인 정보 복사)
  [왼쪽]  유튜브 섹션  -> [유튜브 업로드] 버튼
  [오른쪽] 틱톡 섹션   -> [틱톡 업로드]  버튼
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

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "uploader_settings.json")

VIDEO_TYPES = [("영상 파일", "*.mp4 *.mov *.avi *.mkv *.webm *.flv"), ("모든 파일", "*.*")]
IMAGE_TYPES = [("이미지 파일", "*.jpg *.jpeg *.png *.webp *.bmp"), ("모든 파일", "*.*")]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("유튜브 / 틱톡 자동 업로드")
        self.geometry("1240x980")
        self.minsize(1040, 820)

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
        style.configure("Big.TButton", font=("맑은 고딕", 12, "bold"), padding=8)

        # ---------- 상단: 브라우저 준비 ----------
        top = ttk.LabelFrame(self, text=" 1단계 · 브라우저 준비 ", padding=10)
        top.pack(fill="x", padx=12, pady=(12, 6))

        ttk.Button(top, text="① 크롬 로그인 정보 가져오기",
                   command=self.on_copy_profile).pack(side="left", padx=4)
        ttk.Button(top, text="② 업로드용 크롬 열기",
                   command=self.on_open_chrome).pack(side="left", padx=4)
        ttk.Button(top, text="③ 화면 진단하기",
                   command=self.on_check).pack(side="left", padx=4)
        ttk.Button(top, text="④ 연습 진단(자동)",
                   command=self.on_dryrun).pack(side="left", padx=4)
        ttk.Label(
            top,
            text="  ※ ①은 크롬을 완전히 종료한 뒤 한 번만 누르면 됩니다. "
                 "이후 ②로 연 창에서 로그인이 유지됩니다.\n"
                 "  ※ ③은 지금 열려 있는 화면 하나를, ④는 업로드 과정을 따라가며 "
                 "여러 화면을 자동으로 진단합니다 (게시는 하지 않음).",
            foreground="#555",
            justify="left",
        ).pack(side="left", padx=8)

        # ---------- 가운데: 유튜브 / 틱톡 ----------
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=12, pady=6)
        mid.columnconfigure(0, weight=1, uniform="col")
        mid.columnconfigure(1, weight=1, uniform="col")
        mid.rowconfigure(0, weight=1)

        self.yt = self._build_section(
            mid, col=0, name="유튜브", color="#c4302b",
            thumb_label="썸네일",
            privacy_opts=None,          # 흐름이 정해져 있어 공개설정 선택 없음
            has_pin=True,
            note="업로드 → 일부공개 게시 → 댓글 작성·고정 → 예약 전환 순서로 진행됩니다",
            btn_text="▶  유튜브 업로드",
            btn_cmd=self.on_upload_youtube,
        )

        self.tt = self._build_section(
            mid, col=1, name="틱톡", color="#010101",
            thumb_label="커버 이미지",
            privacy_opts=[("전체 공개", "public"), ("친구만", "friends"), ("나만 보기", "private")],
            has_pin=False,
            note="예약은 최소 20분 뒤 ~ 최대 10일 뒤, 5분 단위만 가능합니다",
            btn_text="▶  틱톡 업로드",
            btn_cmd=self.on_upload_tiktok,
        )

        # 유튜브에만 있는 옵션: 아동용 여부
        self.yt["kids"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.yt["extra"], text="아동용 동영상입니다",
                        variable=self.yt["kids"]).pack(side="left")

        # ---------- 하단: 로그 ----------
        bottom = ttk.LabelFrame(self, text=" 진행 상황 ", padding=6)
        bottom.pack(fill="both", padx=12, pady=(6, 12))

        self.log_box = tk.Text(bottom, height=11, wrap="word",
                               bg="#1e1e1e", fg="#d4d4d4",
                               font=("Consolas", 9), state="disabled")
        self.log_box.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(bottom, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=sb.set)

    def _build_section(self, parent, col, name, color, thumb_label,
                       privacy_opts, has_pin, note, btn_text, btn_cmd):
        """유튜브/틱톡 공통 입력 섹션 생성."""
        f = ttk.LabelFrame(parent, text=f"  {name}  ", padding=12)
        f.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col == 0 else (8, 0))
        f.columnconfigure(1, weight=1)

        s = {}
        r = 0

        tk.Label(f, text=name, font=("맑은 고딕", 14, "bold"), fg=color).grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Label(f, text=note, foreground="#777", wraplength=480).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 8))
        r += 1

        # 제목
        ttk.Label(f, text="제목").grid(row=r, column=0, sticky="w", pady=4)
        s["title"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["title"]).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 상세정보(설명)
        ttk.Label(f, text="상세정보").grid(row=r, column=0, sticky="nw", pady=4)
        s["desc"] = tk.Text(f, height=6, wrap="word", font=("맑은 고딕", 9))
        s["desc"].grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 태그
        ttk.Label(f, text="태그").grid(row=r, column=0, sticky="w", pady=4)
        s["tags"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["tags"]).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="쉼표(,)로 구분 · 예: 브이로그, 일상, 맛집",
                  foreground="#777").grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1

        # 고정 댓글 (유튜브 전용)
        if has_pin:
            ttk.Label(f, text="고정 댓글").grid(row=r, column=0, sticky="nw", pady=4)
            s["pin"] = tk.Text(f, height=3, wrap="word", font=("맑은 고딕", 9))
            s["pin"].grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
            r += 1
            ttk.Label(f, text="비워두면 댓글 단계를 건너뜁니다",
                      foreground="#777").grid(row=r, column=1, columnspan=2, sticky="w")
            r += 1

        # 영상 파일
        ttk.Label(f, text="영상 파일").grid(row=r, column=0, sticky="w", pady=4)
        s["video"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["video"]).grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="찾기", width=6,
                   command=lambda v=s["video"]: self._pick(v, VIDEO_TYPES)).grid(row=r, column=2, padx=(6, 0))
        r += 1

        # 썸네일 / 커버
        ttk.Label(f, text=thumb_label).grid(row=r, column=0, sticky="w", pady=4)
        s["thumb"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["thumb"]).grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(f, text="찾기", width=6,
                   command=lambda v=s["thumb"]: self._pick(v, IMAGE_TYPES)).grid(row=r, column=2, padx=(6, 0))
        r += 1

        # 공개 설정 (틱톡만)
        if privacy_opts:
            ttk.Label(f, text="공개 설정").grid(row=r, column=0, sticky="w", pady=4)
            s["privacy"] = tk.StringVar(value=privacy_opts[0][1])
            pf = ttk.Frame(f)
            pf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
            for lb, val in privacy_opts:
                ttk.Radiobutton(pf, text=lb, value=val, variable=s["privacy"]).pack(side="left", padx=(0, 10))
            r += 1

        # ---- 예약 게시 ----
        ttk.Separator(f, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="ew", pady=8)
        r += 1

        s["sched_on"] = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="예약 게시", variable=s["sched_on"]).grid(
            row=r, column=0, sticky="w", pady=4)

        sf = ttk.Frame(f)
        sf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)

        s["sched_date"] = tk.StringVar()
        s["sched_time"] = tk.StringVar()
        ttk.Label(sf, text="날짜").pack(side="left")
        ttk.Entry(sf, textvariable=s["sched_date"], width=12).pack(side="left", padx=(4, 10))
        ttk.Label(sf, text="시간").pack(side="left")
        ttk.Entry(sf, textvariable=s["sched_time"], width=7).pack(side="left", padx=(4, 10))
        ttk.Button(sf, text="+1일", width=5,
                   command=lambda d=s["sched_date"]: self._shift_day(d, 1)).pack(side="left", padx=2)
        r += 1

        ttk.Label(f, text="YYYY-MM-DD / HH:MM  (예: 2026-09-20 / 09:00) · 체크를 끄면 바로 게시",
                  foreground="#777").grid(row=r, column=1, columnspan=2, sticky="w")
        r += 1

        # 업로드 버튼
        f.rowconfigure(r, weight=1)
        r += 1
        btn = ttk.Button(f, text=btn_text, style="Big.TButton", command=btn_cmd)
        btn.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        s["button"] = btn
        s["extra"] = ttk.Frame(f)
        s["extra"].grid(row=r - 1, column=1, columnspan=2, sticky="w")

        return s

    def _pick(self, var, types):
        path = filedialog.askopenfilename(filetypes=types)
        if path:
            var.set(path)

    def _shift_day(self, var, days):
        """날짜칸을 하루 뒤로 (비어 있으면 내일로)."""
        try:
            base = datetime.strptime(var.get().strip(), "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
        var.set(f"{base + timedelta(days=days):%Y-%m-%d}")

    def _prefill_schedule(self):
        """예약칸이 비어 있으면 '내일 09:00' 으로 채워둔다."""
        tomorrow = f"{datetime.now() + timedelta(days=1):%Y-%m-%d}"
        for s in (self.yt, self.tt):
            if not s["sched_date"].get().strip():
                s["sched_date"].set(tomorrow)
            if not s["sched_time"].get().strip():
                s["sched_time"].set("09:00")

    # ==================== 로그 ====================

    def log(self, msg):
        """다른 스레드에서도 안전하게 로그 남기기."""
        self.log_q.put(str(msg))

    def _drain_log(self):
        while True:
            try:
                msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
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
            raise ValueError(f"예약 시간이 이미 지났습니다: {dt:%Y-%m-%d %H:%M}")

        return f"{dt:%Y-%m-%d %H:%M}"

    # ==================== 업로드 버튼 ====================

    def on_upload_youtube(self):
        cfg = self._collect(self.yt)
        cfg["thumbnail"] = cfg.pop("thumb", "")
        self._run_upload("유튜브", up_youtube.upload, cfg, self.yt["button"])

    def on_upload_tiktok(self):
        cfg = self._collect(self.tt)
        cfg["cover"] = cfg.pop("thumb", "")
        self._run_upload("틱톡", up_tiktok.upload, cfg, self.tt["button"])

    def _run_upload(self, name, func, cfg, button):
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
        button.state(["disabled"])

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

            except Exception as e:
                self.log(f"❌ {name} 업로드 실패: {e}")
                self.log(traceback.format_exc())
            finally:
                # 결과 확인용으로 탭은 일부러 닫지 않는다
                self.busy = False
                self.after(0, lambda: button.state(["!disabled"]))

        threading.Thread(target=work, daemon=True).start()


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
