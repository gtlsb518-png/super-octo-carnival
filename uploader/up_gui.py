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
from tkinter import ttk, filedialog, messagebox

import up_browser
import up_youtube
import up_tiktok

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(HERE, "uploader_settings.json")

VIDEO_TYPES = [("영상 파일", "*.mp4 *.mov *.avi *.mkv *.webm *.flv"), ("모든 파일", "*.*")]
IMAGE_TYPES = [("이미지 파일", "*.jpg *.jpeg *.png *.webp *.bmp"), ("모든 파일", "*.*")]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("유튜브 / 틱톡 자동 업로드")
        self.geometry("1180x860")
        self.minsize(1000, 760)

        self.log_q = queue.Queue()
        self.busy = False          # 업로드 중복 실행 방지

        self._build_ui()
        self._load_settings()
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

        ttk.Label(
            top,
            text="  ※ ①은 크롬을 완전히 종료한 뒤 한 번만 누르면 됩니다. "
                 "이후 ②로 연 창에서 로그인이 유지됩니다.",
            foreground="#555",
        ).pack(side="left", padx=8)

        # ---------- 가운데: 유튜브 / 틱톡 ----------
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=12, pady=6)
        mid.columnconfigure(0, weight=1, uniform="col")
        mid.columnconfigure(1, weight=1, uniform="col")
        mid.rowconfigure(0, weight=1)

        self.yt = self._build_section(
            mid, col=0, name="유튜브", color="#c4302b",
            privacy_opts=[("공개", "public"), ("일부공개", "unlisted"), ("비공개", "private")],
            thumb_label="썸네일 이미지",
            btn_text="▶  유튜브 업로드",
            btn_cmd=self.on_upload_youtube,
        )

        self.tt = self._build_section(
            mid, col=1, name="틱톡", color="#010101",
            privacy_opts=[("전체 공개", "public"), ("친구만", "friends"), ("나만 보기", "private")],
            thumb_label="커버 이미지",
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

        self.log_box = tk.Text(bottom, height=12, wrap="word",
                               bg="#1e1e1e", fg="#d4d4d4",
                               font=("Consolas", 9), state="disabled")
        self.log_box.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(bottom, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=sb.set)

    def _build_section(self, parent, col, name, color, privacy_opts,
                       thumb_label, btn_text, btn_cmd):
        """유튜브/틱톡 공통 입력 섹션 생성."""
        f = ttk.LabelFrame(parent, text=f"  {name}  ", padding=12)
        f.grid(row=0, column=col, sticky="nsew", padx=(0, 8) if col == 0 else (8, 0))
        f.columnconfigure(1, weight=1)

        s = {}
        r = 0

        tk.Label(f, text=name, font=("맑은 고딕", 14, "bold"), fg=color).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 8))
        r += 1

        # 제목
        ttk.Label(f, text="제목").grid(row=r, column=0, sticky="w", pady=4)
        s["title"] = tk.StringVar()
        ttk.Entry(f, textvariable=s["title"]).grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        r += 1

        # 상세정보(설명)
        ttk.Label(f, text="상세정보").grid(row=r, column=0, sticky="nw", pady=4)
        s["desc"] = tk.Text(f, height=8, wrap="word", font=("맑은 고딕", 9))
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

        # 공개 설정
        ttk.Label(f, text="공개 설정").grid(row=r, column=0, sticky="w", pady=4)
        s["privacy"] = tk.StringVar(value=privacy_opts[0][1])
        pf = ttk.Frame(f)
        pf.grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
        for label, val in privacy_opts:
            ttk.Radiobutton(pf, text=label, value=val, variable=s["privacy"]).pack(side="left", padx=(0, 10))
        r += 1

        # 추가 옵션 자리
        s["extra"] = ttk.Frame(f)
        s["extra"].grid(row=r, column=1, columnspan=2, sticky="w", pady=4)
        r += 1

        # 업로드 버튼
        f.rowconfigure(r, weight=1)
        r += 1
        btn = ttk.Button(f, text=btn_text, style="Big.TButton", command=btn_cmd)
        btn.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        s["button"] = btn

        return s

    def _pick(self, var, types):
        path = filedialog.askopenfilename(filetypes=types)
        if path:
            var.set(path)

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
        return {
            "title": s["title"].get(),
            "desc": s["desc"].get("1.0", "end").rstrip("\n"),
            "tags": s["tags"].get(),
            "video": s["video"].get(),
            "thumb": s["thumb"].get(),
            "privacy": s["privacy"].get(),
            "kids": bool(s["kids"].get()) if "kids" in s else False,
        }

    def _apply(self, s, data):
        s["title"].set(data.get("title", ""))
        s["desc"].delete("1.0", "end")
        s["desc"].insert("1.0", data.get("desc", ""))
        s["tags"].set(data.get("tags", ""))
        s["video"].set(data.get("video", ""))
        s["thumb"].set(data.get("thumb", ""))
        if data.get("privacy"):
            s["privacy"].set(data["privacy"])
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

        self._save_settings()
        self.busy = True
        button.state(["disabled"])

        def work():
            page = None
            browser = None
            try:
                from playwright.sync_api import sync_playwright

                self.log("=" * 60)
                self.log(f"🚀 {name} 업로드 시작")
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
