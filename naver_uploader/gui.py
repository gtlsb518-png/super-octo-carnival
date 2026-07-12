# -*- coding: utf-8 -*-
"""
네이버 스마트스토어 자동 상품등록 - GUI 실행기

실행:  python gui.py

엑셀 파일을 선택하고 [시작] 을 누르면 uploader.py 의 자동입력이 실행된다.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import config
import uploader


class App:
    def __init__(self, root):
        self.root = root
        root.title("네이버 스마트스토어 자동 상품등록")
        root.geometry("720x520")

        self.running = False
        self.stop_requested = False

        # --- 엑셀 파일 선택 ---
        top = tk.Frame(root)
        top.pack(fill="x", padx=10, pady=(10, 5))

        tk.Label(top, text="엑셀 파일:").pack(side="left")
        self.path_var = tk.StringVar(value=os.path.abspath(config.EXCEL_PATH))
        tk.Entry(top, textvariable=self.path_var).pack(side="left", fill="x",
                                                       expand=True, padx=5)
        tk.Button(top, text="찾아보기", command=self.browse).pack(side="left")

        # --- 옵션 + 버튼 ---
        mid = tk.Frame(root)
        mid.pack(fill="x", padx=10, pady=5)

        self.auto_submit_var = tk.BooleanVar(value=config.AUTO_SUBMIT)
        tk.Checkbutton(
            mid,
            text="자동 저장 (입력 후 [저장하기]까지 자동 클릭 — 처음엔 끄고 확인하세요)",
            variable=self.auto_submit_var,
        ).pack(side="left")

        self.stop_btn = tk.Button(mid, text="중지", state="disabled",
                                  command=self.request_stop)
        self.stop_btn.pack(side="right", padx=(5, 0))
        self.start_btn = tk.Button(mid, text="시작", width=10, command=self.start)
        self.start_btn.pack(side="right")

        tk.Button(mid, text="엑셀 양식 만들기", command=self.make_template)\
            .pack(side="right", padx=5)

        # --- 로그 창 ---
        self.log_box = ScrolledText(root, state="disabled", font=("맑은 고딕", 10))
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.log("1) [엑셀 양식 만들기]로 양식을 만들고 상품을 채워 넣으세요.")
        self.log("2) 엑셀 파일을 선택하고 [시작]을 누르세요.")
        self.log("3) 크롬이 열리면 (처음 한 번만) 네이버 로그인을 해주세요.\n")

    # ---------- UI 동작 ----------
    def browse(self):
        path = filedialog.askopenfilename(
            title="상품 엑셀 파일 선택",
            filetypes=[("엑셀 파일", "*.xlsx"), ("모든 파일", "*.*")],
        )
        if path:
            self.path_var.set(path)

    def make_template(self):
        try:
            import make_template
            make_template.main()
            path = os.path.abspath(config.EXCEL_PATH)
            self.path_var.set(path)
            self.log(f"엑셀 양식 생성 완료: {path}")
        except Exception as e:
            messagebox.showerror("오류", f"양식 생성 실패: {e}")

    def log(self, msg):
        def append():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", str(msg) + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, append)

    def confirm(self, message):
        """작업 스레드에서 호출되는 사용자 확인. 메인 스레드로 넘겨서 처리."""
        event = threading.Event()

        def ask():
            messagebox.showinfo("확인", message)
            event.set()
        self.root.after(0, ask)
        event.wait()

    def request_stop(self):
        self.stop_requested = True
        self.log("중지 요청됨 - 현재 상품까지만 처리하고 멈춥니다...")

    def start(self):
        if self.running:
            return
        excel_path = self.path_var.get().strip()
        if not os.path.isfile(excel_path):
            messagebox.showwarning("알림", "엑셀 파일이 없습니다.\n"
                                   "[엑셀 양식 만들기]로 먼저 양식을 만들어 주세요.")
            return

        self.running = True
        self.stop_requested = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self.worker, args=(excel_path,), daemon=True).start()

    def worker(self, excel_path):
        try:
            uploader.run(
                excel_path=excel_path,
                auto_submit=self.auto_submit_var.get(),
                log_fn=self.log,
                confirm_fn=self.confirm,
                stop_flag=lambda: self.stop_requested,
            )
        except Exception as e:
            self.log(f"오류 발생: {e}")
        finally:
            self.running = False
            self.root.after(0, lambda: (
                self.start_btn.configure(state="normal"),
                self.stop_btn.configure(state="disabled"),
            ))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
