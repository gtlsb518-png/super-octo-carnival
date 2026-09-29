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

def attach(playwright, log=print, port=DEBUG_PORT, auto_launch=False):
    """
    이미 켜져 있는 크롬 창에 붙는다.

    새 창을 띄우지 않는다. 화면에 띄워둔 그 창을 그대로 쓴다.
    (auto_launch=True 로 부르면 없을 때만 새로 켠다)

    반환: (browser, context)
    """
    if not is_port_open(port):
        if not auto_launch:
            raise RuntimeError(
                f"연결할 크롬 창이 없습니다 (포트 {port}).\n\n"
                "'② 업로드용 크롬 열기' 를 한 번 눌러 창을 띄운 뒤,\n"
                "그 창에서 유튜브·틱톡에 로그인해두고 다시 실행하세요.\n"
                "(그 창은 계속 열어두시면 됩니다)"
            )
        launch_chrome(log=log, port=port)
    else:
        log(f"켜져 있는 크롬 창에 연결합니다 (포트 {port})")

    browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")

    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context()

    context.set_default_timeout(60000)
    log("✅ 브라우저 연결 완료")
    return browser, context


# ==================== 파일 넣기 (용량 제한 우회) ====================

def set_file(page, locator, path, log=print):
    """
    파일 선택칸에 파일을 넣는다.

    켜져 있는 크롬에 붙어 있으면(CDP 연결) 플레이라이트는 크롬을 '원격'으로 보고
    파일을 통째로 전송하려 한다. 그래서 50MB 가 넘으면 이렇게 거부한다.
        Cannot transfer files larger than 50Mb to a browser not co-located with the server

    하지만 크롬은 같은 컴퓨터에 있으므로 전송할 필요가 없다.
    크롬에게 "이 경로의 파일을 쓰라"고 직접 알려주면(DOM.setFileInputFiles)
    용량 제한 없이 들어간다. 그게 안 되면 원래 방식으로 넘어간다.
    """
    path = os.path.abspath(path)
    size_mb = os.path.getsize(path) / (1024 * 1024)

    marked = False
    try:
        locator.evaluate("el => el.setAttribute('data-up-target', '1')")
        marked = True
    except Exception as e:
        log(f"  ! 파일칸 표시 실패: {e}")

    if marked:
        session = None
        try:
            session = page.context.new_cdp_session(page)
            session.send("DOM.enable")
            session.send("DOM.getDocument", {"depth": -1, "pierce": True})

            # 그림자 DOM 안쪽까지 찾아 들어간다
            js = """(() => {
              const find = (root) => {
                const el = root.querySelector('[data-up-target]');
                if (el) return el;
                for (const e of root.querySelectorAll('*')) {
                  if (e.shadowRoot) { const f = find(e.shadowRoot); if (f) return f; }
                }
                return null;
              };
              return find(document);
            })()"""
            res = session.send("Runtime.evaluate", {"expression": js, "returnByValue": False})
            obj_id = (res.get("result") or {}).get("objectId")
            if not obj_id:
                raise RuntimeError("크롬에서 파일칸을 찾지 못했습니다")

            node = session.send("DOM.requestNode", {"objectId": obj_id})
            session.send("DOM.setFileInputFiles",
                         {"files": [path], "nodeId": node["nodeId"]})
            log(f"  - 파일 지정 완료 ({size_mb:.1f}MB · 크롬에 경로로 전달)")
            return True
        except Exception as e:
            log(f"  - 경로 전달 실패, 일반 방식으로 시도합니다: {e}")
        finally:
            if session is not None:
                try:
                    session.detach()
                except Exception:
                    pass
            try:
                locator.evaluate("el => el.removeAttribute('data-up-target')")
            except Exception:
                pass

    # 일반 방식 (50MB 미만만 가능)
    if size_mb > 50:
        raise RuntimeError(
            f"파일이 {size_mb:.0f}MB 인데 크롬에 경로로 전달하지 못했습니다.\n"
            "크롬을 '② 업로드용 크롬 열기' 로 다시 켠 뒤 시도해 주세요."
        )
    locator.set_input_files(path)
    log(f"  - 파일 지정 완료 ({size_mb:.1f}MB)")
    return True


# 건드리면 안 되는 주소 (크롬 내부 페이지)
_SKIP_URLS = ("chrome://", "devtools://", "chrome-extension://", "edge://", "about:blank")


def get_page(context, log=print, reuse=True):
    """
    작업할 탭을 고른다.

    reuse=True (기본): 화면에 띄워둔 그 창의 탭을 그대로 쓴다.
      새 탭을 만들지 않으므로, 로그인된 창 하나로 계속 작업하게 된다.
      보이는 탭이 여럿이면 가장 최근 것을 쓴다.
    """
    if reuse:
        usable = [p for p in context.pages
                  if not (p.url or "").startswith(_SKIP_URLS)]

        # 지금 화면에 보이는 탭을 먼저 찾는다 (뒤쪽 탭은 hidden 으로 나온다)
        visible = []
        for pg in usable:
            try:
                if pg.evaluate("document.visibilityState") == "visible":
                    visible.append(pg)
            except Exception:
                continue

        page = (visible[-1] if visible else (usable[-1] if usable else None))
        if page is not None:
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.set_default_timeout(60000)
            log(f"  - 열려 있는 탭을 사용합니다 ({(page.url or '')[:50]})")
            return page

        log("  - 쓸 수 있는 탭이 없어 새 탭을 엽니다")

    page = context.new_page()
    page.set_default_timeout(60000)
    return page
