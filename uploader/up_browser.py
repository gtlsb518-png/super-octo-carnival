#!/usr/bin/env python3
"""
브라우저 연결 모듈

핵심 아이디어:
  크롬은 보안상 "그냥 켜져 있는 창"에 외부 프로그램이 붙을 수 없습니다.
  (--remote-debugging-port 옵션으로 켜진 창만 제어 가능)

  그래서 이 프로그램은 두 가지 방법을 지원합니다.

  [방법 1] 이미 디버그 모드로 켜진 크롬에 붙기 (포트 9222)
  [방법 2] 전용 프로필로 크롬을 켜기  <-- 기본 / 추천
           + 기존 크롬 로그인 정보(쿠키)를 복사해오면 로그인 필요 없음

  전용 프로필은 폴더에 그대로 남으므로, 한 번 로그인해두면
  다음부터는 계속 로그인 상태가 유지됩니다.
"""

import os
import sys
import time
import socket
import shutil
import subprocess

# 디버그 포트 (크롬 제어용)
DEBUG_PORT = 9222

# 전용 프로필 폴더 (이 파일과 같은 폴더 안에 생성)
PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")


# ==================== 크롬 실행 파일 찾기 ====================

def find_chrome():
    """설치된 크롬 실행 파일 경로를 찾는다."""
    candidates = []

    if sys.platform.startswith("win"):
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
                candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    elif sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    else:
        candidates += [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
        ]

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def find_default_profile():
    """사용자가 평소 쓰는 크롬 프로필 폴더(User Data)를 찾는다."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA", "")
        path = os.path.join(base, "Google", "Chrome", "User Data")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    else:
        path = os.path.expanduser("~/.config/google-chrome")

    return path if os.path.isdir(path) else None


# ==================== 로그인 정보(쿠키) 복사 ====================

# 복사하지 않을 폴더 (캐시류 - 용량만 크고 필요 없음)
SKIP_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
    "Service Worker", "CacheStorage", "Media Cache", "Application Cache",
    "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "component_crx_cache",
    "optimization_guide_model_store", "Crashpad", "Safe Browsing",
    "extensions_crx_cache", "Download Service", "Extension State",
}

# 로그인 유지에 꼭 필요한 항목
LOGIN_FILES = [
    "Cookies", "Cookies-journal",
    "Login Data", "Login Data-journal",
    "Web Data", "Web Data-journal",
    "Preferences", "Secure Preferences",
    "Local Storage", "Session Storage", "IndexedDB",
    "Network",  # 최신 크롬은 Network/Cookies 위치에 쿠키 저장
]


def copy_login_profile(log=print, profile_name="Default"):
    """
    평소 쓰는 크롬 프로필의 로그인 정보를 전용 프로필로 복사한다.

    ※ 반드시 크롬을 완전히 종료한 상태에서 실행해야 한다.
      (크롬이 켜져 있으면 쿠키 파일이 잠겨 있어서 복사가 실패/불완전함)
    """
    src_base = find_default_profile()
    if not src_base:
        raise RuntimeError("크롬 프로필 폴더를 찾을 수 없습니다. 크롬이 설치되어 있나요?")

    src = os.path.join(src_base, profile_name)
    if not os.path.isdir(src):
        raise RuntimeError(f"프로필 폴더가 없습니다: {src}")

    dst = os.path.join(PROFILE_DIR, profile_name)
    os.makedirs(dst, exist_ok=True)

    # 크롬 전체 설정 파일 (프로필 인식에 필요)
    local_state = os.path.join(src_base, "Local State")
    if os.path.exists(local_state):
        try:
            shutil.copy2(local_state, os.path.join(PROFILE_DIR, "Local State"))
        except Exception as e:
            log(f"  ! Local State 복사 실패 (무시 가능): {e}")

    copied = 0
    for name in LOGIN_FILES:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if not os.path.exists(s):
            continue
        try:
            if os.path.isdir(s):
                if os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)
                shutil.copytree(
                    s, d,
                    ignore=shutil.ignore_patterns(*SKIP_DIRS),
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(s, d)
            copied += 1
            log(f"  - 복사 완료: {name}")
        except Exception as e:
            log(f"  ! 복사 실패({name}): {e}")

    if copied == 0:
        raise RuntimeError("복사된 파일이 없습니다. 크롬이 켜져 있는지 확인하세요.")

    log(f"✅ 로그인 정보 복사 완료 ({copied}개 항목)")
    return dst


# ==================== 포트 확인 / 크롬 실행 ====================

def is_port_open(port=DEBUG_PORT, host="127.0.0.1"):
    """디버그 포트가 열려 있는지 확인."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def launch_chrome(log=print, port=DEBUG_PORT, headless=False):
    """전용 프로필로 크롬을 디버그 모드로 켠다."""
    if is_port_open(port):
        log(f"이미 디버그 크롬이 켜져 있습니다 (포트 {port})")
        return None

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "크롬을 찾을 수 없습니다.\n"
            "크롬을 설치하거나, up_browser.py 의 find_chrome() 에 경로를 추가하세요."
        )

    os.makedirs(PROFILE_DIR, exist_ok=True)

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
    ]
    if headless:
        args.append("--headless=new")

    log(f"크롬 실행 중... ({os.path.basename(chrome)})")
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 포트가 열릴 때까지 최대 20초 대기
    for _ in range(40):
        if is_port_open(port):
            log(f"✅ 크롬 준비 완료 (포트 {port})")
            return proc
        time.sleep(0.5)

    raise RuntimeError("크롬이 디버그 모드로 뜨지 않았습니다. 기존 크롬 창을 모두 닫고 다시 시도하세요.")


# ==================== Playwright 연결 ====================

def attach(playwright, log=print, port=DEBUG_PORT, auto_launch=True):
    """
    켜져 있는 크롬에 붙는다. 없으면 전용 프로필로 새로 켠다.

    반환: (browser, context)
    """
    if not is_port_open(port):
        if not auto_launch:
            raise RuntimeError(f"디버그 크롬이 켜져 있지 않습니다 (포트 {port})")
        launch_chrome(log=log, port=port)
    else:
        log(f"켜져 있는 크롬에 연결합니다 (포트 {port})")

    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")

    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()

    context.set_default_timeout(60000)
    log("✅ 브라우저 연결 완료")
    return browser, context


def get_page(context, log=print):
    """새 탭을 연다 (기존 탭은 건드리지 않음)."""
    page = context.new_page()
    page.set_default_timeout(60000)
    return page
