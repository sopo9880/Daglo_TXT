"""
Daglo Transcript Collector
- GUI app for collecting Daglo transcripts from folders the user can access.
- Uses Playwright with a persistent Chrome/Edge profile so login is retained.
- Saves transcripts as txt files and optionally zips the output.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import customtkinter as ctk
from tkinter import filedialog, messagebox
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

APP_TITLE = "Daglo Transcript Collector"
from version import APP_VERSION
from app_paths import data_dir, resource_dir, atomic_json, configure_browser_runtime
from updater import check_update, download_update, launch_installer, RELEASES_URL
configure_browser_runtime()
DAGLO_ORIGIN = "https://daglo.ai"


def app_base_dir() -> Path:
    """Directory where config/source lives when running as script or exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_config() -> Dict[str, Any]:
    base = data_dir()
    cfg_path = base / "config.json"
    default = {
        "start_url": "https://daglo.ai/board",
        "login_success_url": "https://daglo.ai/home",
        "browser_channel": "chromium",
        "headless": True,
        "max_pages_per_folder": 20,
        "delay_seconds_between_boards": 1.0,
        "output_dir": "output",
        "profile_dir": "profiles/daglo_chrome_profile",
        "zip_after_collect": True,
        "save_html_debug_on_failure": True,
        "clean_ui_lines": False,
        "copy_fallback_enabled": True,
        "extraction_mode": "copy_button_only",
        "min_transcript_chars": 100,
        "validate_extracted_text": True,
        "login_auto_close_on_success": False,
        "low_focus_mode": True,
        "interactive_hover_fallback": False,
        "manifest_sync_zip_after": True,
        "auto_check_updates": True,
        "skipped_update": "",
    }
    if not cfg_path.exists():
        # Import only config from an adjacent legacy installation. Never delete it.
        legacy = app_base_dir() / "config.json"
        if legacy.exists():
            try:
                legacy_cfg = json.loads(legacy.read_text(encoding="utf-8-sig"))
                for key in ("output_dir", "profile_dir"):
                    if key in legacy_cfg and not Path(legacy_cfg[key]).is_absolute():
                        legacy_cfg[key] = str((app_base_dir() / legacy_cfg[key]).resolve())
                default.update(legacy_cfg)
            except (OSError, ValueError, TypeError):
                pass
    if cfg_path.exists():
        try:
            user_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            default.update(user_cfg)
        except Exception:
            pass
    return default


def save_config(cfg: Dict[str, Any]) -> None:
    atomic_json(data_dir() / "config.json", cfg)


def safe_filename(name: str, fallback: str = "untitled", limit: int = 120) -> str:
    name = html_unescape(name or "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    name = name[:limit].strip(" .") or fallback
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "_" + name
    return name


def normalize_board_title(title: str) -> str:
    """Return only the actual Daglo board title for file naming.

    Source of truth is #board-board-title-button-name on the detail page.
    This helper protects against accidental board-list/card text, where Daglo can
    concatenate title + keywords into one long string.
    """
    title = html_unescape(title or "").strip()
    title = title.replace("다글로 |", "").replace("다글로 -", "").strip()
    title = re.sub(r"\s+", " ", title).strip(" .")
    if not title:
        return ""

    # Prefer a date-prefixed lecture title. Stop after the subject-like part, not
    # after appended keyword chips from a board card.
    m = re.search(r"(20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*[^\n\r]+)", title)
    if m:
        candidate = m.group(1).strip()
    else:
        # When multi-line text slips in, the first meaningful line is normally the title.
        parts = [p.strip() for p in re.split(r"[\r\n]+|\s{3,}", title) if p.strip()]
        candidate = parts[0] if parts else title

    # If a board-card string appended common keyword chips with no separator, keep
    # only the date + course/title prefix. This is intentionally conservative and
    # only runs when the candidate is abnormally long.
    if len(candidate) > 55:
        m2 = re.match(r"^(20\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*[가-힣A-Za-z0-9 _\-ⅠⅡⅢⅣⅤ]+?)(?:\s+(?:기능|모델|서버|서비스|시험|테스트|평가|케이스).*)?$", candidate)
        if m2:
            candidate = m2.group(1).strip()

    return candidate.strip(" .")


def html_unescape(text: str) -> str:
    import html
    return html.unescape(text or "")


def normalize_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if href.startswith("/"):
        return DAGLO_ORIGIN + href
    return href


def folder_id_from_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ("folderIds", "checkedFolder"):
            if qs.get(key):
                return qs[key][0]
    except Exception:
        pass
    m = re.search(r"(?:folderIds|checkedFolder)=([^&]+)", url)
    return urllib.parse.unquote(m.group(1)) if m else ""


def board_id_from_url(url: str) -> str:
    m = re.search(r"/board/([^?/#]+)", url)
    return m.group(1) if m else safe_filename(url, "board")


def board_key_from_url(url: str) -> str:
    """Stable key for comparing already-collected Daglo boards.

    Daglo board URLs can be re-ordered or re-created with different query
    parameters during scanning. For manifest sync, the actual identity is the
    /board/<id> path plus fileMetaId when present.
    """
    url = normalize_url(url or "")
    try:
        parsed = urllib.parse.urlparse(url)
        board_id = board_id_from_url(url)
        qs = urllib.parse.parse_qs(parsed.query)
        file_meta_id = (qs.get("fileMetaId") or [""])[0]
        if board_id and board_id != "board":
            return f"{board_id}|{file_meta_id}"
    except Exception:
        pass
    return url.strip()


@dataclass
class FolderInfo:
    name: str
    url: str
    folder_id: str


@dataclass
class BoardInfo:
    title: str
    url: str
    folder_name: str = ""


def parse_folders_from_html(html_text: str) -> List[FolderInfo]:
    """Fallback parser for Next.js/analytics-heavy Daglo HTML.

    It extracts folder URLs even when normal DOM anchors are hidden or virtualized.
    Folder names may not always be available in raw HTML, so IDs are used as fallback.
    """
    results: Dict[str, FolderInfo] = {}
    decoded = urllib.parse.unquote(html_text or "")
    for m in re.finditer(r"https://daglo\.ai/board\?[^\"'<>\s]+", decoded):
        url = m.group(0).replace("&amp;", "&")
        fid = folder_id_from_url(url)
        if fid:
            results[fid] = FolderInfo(name=f"folder_{fid}", url=url, folder_id=fid)
    # Also catch relative folder URLs.
    for m in re.finditer(r"/board\?[^\"'<>\s]+", decoded):
        url = DAGLO_ORIGIN + m.group(0).replace("&amp;", "&")
        fid = folder_id_from_url(url)
        if fid and fid not in results:
            results[fid] = FolderInfo(name=f"folder_{fid}", url=url, folder_id=fid)
    return list(results.values())


def parse_boards_from_html(html_text: str, folder_name: str = "") -> List[BoardInfo]:
    """Fallback parser that uses URL/title data visible in Daglo page HTML.

    The user-provided HTML contained Google conversion URLs with both:
    - url=https%3A%2F%2Fdaglo.ai%2Fboard%2F...
    - tiba=다글로 | 2026. ...
    This parser decodes those and creates board items.
    """
    text = html_text or ""
    found: Dict[str, BoardInfo] = {}

    # Pattern for encoded board URL, optionally followed by title metadata.
    url_pattern = re.compile(r"https%3A%2F%2Fdaglo\.ai%2Fboard%2F[^&\"'<>\s]+")
    for match in url_pattern.finditer(text):
        encoded_url = match.group(0)
        url = urllib.parse.unquote(encoded_url).replace("&amp;", "&")
        if "fileMetaId=" not in url:
            continue
        # Search nearby analytics segment for the title parameter.
        nearby = text[match.start(): match.start() + 1800]
        title = ""
        tm = re.search(r"tiba=([^&\"'<>]+)", nearby)
        if tm:
            title = urllib.parse.unquote(tm.group(1)).replace("다글로 |", "").strip()
        if not title:
            title = board_id_from_url(url)
        found[url] = BoardInfo(title=title, url=url, folder_name=folder_name)

    # Relative board links.
    decoded = urllib.parse.unquote(text)
    for m in re.finditer(r"/board/[A-Za-z0-9_\-]+\?fileMetaId=[A-Za-z0-9\-]+", decoded):
        url = DAGLO_ORIGIN + m.group(0).replace("&amp;", "&")
        found.setdefault(url, BoardInfo(title=board_id_from_url(url), url=url, folder_name=folder_name))

    return list(found.values())


def clean_transcript_text(raw: str, clean_ui_lines: bool = True) -> str:
    raw = html_unescape(raw or "")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    # copy_button_only mode should preserve Daglo's own clipboard result.
    # Only normalize line endings and trim surrounding whitespace.
    if not clean_ui_lines:
        return raw.strip()

    raw = re.sub(r"\n{3,}", "\n\n", raw)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.split("\n")]

    drop_exact = {
        "다글로", "홈", "보드", "폴더 목록", "내 보드", "새 보드", "공유", "다운로드",
        "요약", "스크립트", "AI 채팅", "번역", "퀴즈", "로그인", "회원가입", "설정",
        "북마크", "단락 복사", "스크립트 전체 복사", "전체 복사", "복사", "닫기",
        "이전", "다음", "검색", "전체", "선택", "취소", "확인", "슬라이드 만들기", "퀴즈 만들기",
    }
    drop_contains = [
        "대한민국 1등 AI 플랫폼", "개인정보처리방침", "서비스 이용약관",
        "스크립트가 전체 복사", "복사되었어요", "파일을 다운로드",
        "로그인이 필요", "브라우저", "Daglo",
    ]
    cleaned: List[str] = []
    seen_short = set()
    for line in lines:
        if not line:
            if cleaned and cleaned[-1] != "":
                cleaned.append("")
            continue
        if line in drop_exact:
            continue
        if any(x in line for x in drop_contains):
            continue
        # Drop repetitive tiny UI labels, but keep short transcript words when time-coded.
        if len(line) <= 3 and not re.search(r"\d{1,2}:\d{2}", line):
            key = line.lower()
            if key in seen_short:
                continue
            seen_short.add(key)
        cleaned.append(line)
    return "\n".join(cleaned).strip()


BAD_EXTRACTION_MARKERS = [
    "Python virtual environment preparing",
    "Installing dependencies",
    "Requirement already satisfied",
    "Collecting pip",
    "Using cached pip",
    "Successfully installed pip",
    "PyInstaller",
    "No matching distribution found",
    "Building Windows EXE",
    "Downloading playwright",
    "ERROR: Could not find a version",
]


def looks_like_bad_clipboard_or_log(text: str) -> bool:
    sample = text or ""
    return any(marker in sample for marker in BAD_EXTRACTION_MARKERS)


def looks_like_transcript(text: str, min_chars: int = 500) -> Tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if looks_like_bad_clipboard_or_log(text):
        return False, "build/install log detected"
    compact = re.sub(r"\s+", "", text)
    if len(compact) < min_chars:
        return False, f"too short: {len(compact)} chars"
    hangul = len(re.findall(r"[가-힣]", text))
    timestamps = len(re.findall(r"(?m)^\s*\d{1,2}:\d{2}(?::\d{2})?\s*$", text))
    loose_times = len(re.findall(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", text))
    if hangul < 80:
        return False, f"too little Korean text: {hangul} chars"
    if timestamps < 1 and loose_times < 3:
        return False, "no transcript timestamps"
    return True, "ok"


class DagloCollector:
    def __init__(self, cfg: Dict[str, Any], log: Callable[[str], None]):
        self.cfg = cfg
        self.log = log
        self.base_dir = data_dir()
        self.profile_dir = self._abs_path(cfg.get("profile_dir", "profiles/daglo_chrome_profile"))
        self.output_dir = self._abs_path(cfg.get("output_dir", "output"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.profile_dir.mkdir(parents=True, exist_ok=True)

    def _abs_path(self, p: str | Path) -> Path:
        p = Path(p)
        if p.is_absolute():
            return p
        return self.base_dir / p

    def _launch_context(self, p, headless: Optional[bool] = None):
        channel = self.cfg.get("browser_channel", "chrome") or "chrome"
        headless = self.cfg.get("headless", False) if headless is None else headless
        kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=bool(headless),
            viewport={"width": 1450, "height": 920},
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            accept_downloads=True,
        )

        channels_to_try: List[Optional[str]] = []
        if channel == "chromium":
            channels_to_try = [None, "chrome", "msedge"]
        elif channel == "msedge":
            channels_to_try = ["msedge", "chrome", None]
        else:
            channels_to_try = ["chrome", "msedge", None]

        last_error: Optional[Exception] = None
        for candidate in channels_to_try:
            launch_kwargs = dict(kwargs)
            if candidate:
                launch_kwargs["channel"] = candidate
            try:
                if candidate:
                    self.log(f"브라우저 실행 시도: {candidate}")
                else:
                    self.log("브라우저 실행 시도: playwright chromium")
                context = p.chromium.launch_persistent_context(**launch_kwargs)
                try:
                    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=DAGLO_ORIGIN)
                except Exception:
                    pass
                return context
            except PlaywrightError as e:
                last_error = e
                self.log(f"브라우저 실행 실패: {candidate or 'chromium'}")

        raise RuntimeError(
            "브라우저 실행에 실패했습니다. 로그인 브라우저 창을 모두 닫은 뒤 다시 시도하세요. "
            "계속 실패하면 Chrome 또는 Edge를 선택하거나 앱을 재설치하세요. "
            f"마지막 오류: {last_error}"
        )

    def open_login_browser(self) -> None:
        """Open a real browser for login and return immediately when possible.

        v1.0.6: Do not keep polling a stale Playwright page during login.
        Daglo can navigate through several intermediate pages or reuse an existing
        /board page, so URL-based auto-close was unreliable. The login browser is
        now opened as an external Chrome/Edge window using the same user-data-dir
        that Playwright later uses for collection. The user logs in, closes that
        browser window, then presses Folder Scan.
        """
        self.log("로그인용 브라우저를 엽니다.")
        self.log("이번 버전은 URL 자동 감지로 창을 닫지 않습니다.")
        self.log("열린 브라우저에서 로그인한 뒤, 브라우저 창을 직접 닫고 '폴더 스캔'을 누르세요.")

        if self.cfg.get("browser_channel") != "chromium" and self._open_external_login_browser():
            self.log("로그인 브라우저를 열었습니다.")
            self.log("로그인 완료 후 이 브라우저 창을 닫고, 앱에서 '폴더 스캔'을 누르세요.")
            return

        self.log("외부 Chrome/Edge 실행에 실패하여 Playwright 창으로 엽니다.")
        self.log("이 경우에도 URL 감지는 하지 않습니다. 로그인 후 창을 직접 닫으면 됩니다.")
        with sync_playwright() as p:
            context = self._launch_context(p, headless=False)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.cfg.get("start_url", "https://daglo.ai/board"), wait_until="domcontentloaded", timeout=60000)
            self.log("다글로 화면이 열렸습니다. 로그인 완료 후 이 브라우저 창을 직접 닫아주세요.")
            start = time.time()
            while True:
                try:
                    pages = [pg for pg in context.pages if not pg.is_closed()]
                    if not pages:
                        self.log("로그인 브라우저 창 닫힘을 감지했습니다.")
                        break
                    if time.time() - start > 45 * 60:
                        self.log("로그인 대기 시간이 45분을 넘어 브라우저를 닫습니다.")
                        break
                    pages[0].wait_for_timeout(500)
                except PlaywrightError:
                    self.log("로그인 브라우저가 닫힌 것으로 감지했습니다.")
                    break
            try:
                context.close()
            except Exception:
                pass
        self.log("로그인 세션 저장 단계가 끝났습니다. 로그인했다면 이제 '폴더 스캔'을 누르세요.")

    def _open_external_login_browser(self) -> bool:
        """Launch Chrome/Edge outside Playwright with the collector profile.

        This avoids stale Playwright Page objects during OAuth/login redirects.
        The same profile_dir is reused by _launch_context during collection.
        """
        start_url = self.cfg.get("start_url", "https://daglo.ai/board")
        profile = str(self.profile_dir)
        channel = (self.cfg.get("browser_channel", "chrome") or "chrome").lower()

        if channel == "msedge":
            browsers = ["msedge", "chrome"]
        elif channel == "chromium":
            browsers = ["chrome", "msedge"]
        else:
            browsers = ["chrome", "msedge"]

        candidates = []
        if os.name == "nt":
            roots = [Path(os.environ.get(key, "C:/")) for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
            locations = {"chrome": "Google/Chrome/Application/chrome.exe", "msedge": "Microsoft/Edge/Application/msedge.exe"}
            for browser in browsers:
                for root in roots:
                    executable = root / locations[browser]
                    if executable.is_file():
                        candidates.append([str(executable), f"--user-data-dir={profile}", "--no-first-run", "--new-window", start_url])
        elif sys.platform == "darwin":
            app_names = ["Google Chrome", "Microsoft Edge"] if channel != "msedge" else ["Microsoft Edge", "Google Chrome"]
            for app in app_names:
                candidates.append([
                    "open", "-na", app, "--args",
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--new-window",
                    start_url,
                ])
        else:
            for exe in ["google-chrome", "chrome", "chromium", "microsoft-edge", "msedge"]:
                candidates.append([
                    exe,
                    f"--user-data-dir={profile}",
                    "--no-first-run",
                    "--new-window",
                    start_url,
                ])

        for cmd in candidates:
            try:
                label = cmd[0]
                self.log(f"외부 브라우저 실행 시도: {label}")
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception as e:
                self.log(f"외부 브라우저 실행 실패: {e}")
        return False

    def _page_looks_authenticated(self, page) -> bool:
        """Return True only when Daglo has reached the confirmed home URL.

        v1.0.5: Do not infer login success from page text or intermediate OAuth/login
        screens. The browser must be on https://daglo.ai/home (query string or a
        trailing slash is allowed) before auto-closing the login window.
        """
        try:
            url = page.url or ""
        except Exception:
            url = ""

        ok = self._is_confirmed_login_success_url(url)
        if ok:
            self.log(f"로그인 성공 URL 확인: {url}")
        else:
            self.log(f"로그인 대기 중: 현재 URL={url}")
        return ok

    def _is_confirmed_login_success_url(self, url: str) -> bool:
        success_url = self.cfg.get("login_success_url", "https://daglo.ai/home")
        try:
            current = urllib.parse.urlparse(url)
            expected = urllib.parse.urlparse(success_url)
        except Exception:
            return False

        current_host = (current.netloc or "").lower()
        expected_host = (expected.netloc or "daglo.ai").lower()
        current_path = (current.path or "/").rstrip("/") or "/"
        expected_path = (expected.path or "/home").rstrip("/") or "/"

        return (
            current.scheme in {"http", "https"}
            and current_host == expected_host
            and current_path == expected_path
        )

    def scan_folders(self) -> List[FolderInfo]:
        self.log("폴더 목록 스캔을 시작합니다.")
        with sync_playwright() as p:
            context = self._launch_context(p, headless=bool(self.cfg.get("headless", False)))
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(self.cfg.get("start_url", "https://daglo.ai/board"), wait_until="domcontentloaded", timeout=60000)
            self._soft_wait(page)
            self._detect_login_needed(page)

            folders = self._extract_folders_from_dom(page)
            if not folders:
                folders = parse_folders_from_html(self._page_content(page))
            context.close()

        # Deduplicate and sort by name.
        unique: Dict[str, FolderInfo] = {}
        for f in folders:
            key = f.folder_id or f.url
            if key and key not in unique:
                unique[key] = f
        final = sorted(unique.values(), key=lambda x: x.name)
        self.log(f"폴더 {len(final)}개를 찾았습니다.")
        return final

    def _page_content(self, page):
        try:
            if page.is_closed():
                raise RuntimeError("브라우저 창이 닫혔습니다. 작업 중에는 브라우저를 닫지 마세요.")
            return page.content()
        except PlaywrightError as error:
            raise RuntimeError("브라우저가 종료되었습니다. 로그인 창을 닫은 뒤 다시 실행하세요.") from error

    def _unique_transcript_path(self, folder, title, board_url):
        path = folder / (safe_filename(title, board_id_from_url(board_url)) + ".txt")
        if path.exists():
            stem = path.stem + "_" + safe_filename(board_id_from_url(board_url), "board", 24)
            path = folder / (stem + ".txt")
            index = 2
            while path.exists():
                path = folder / (stem + f"_{index}.txt")
                index += 1
        return path

    def _extract_folders_from_dom(self, page) -> List[FolderInfo]:
        js = r"""
        () => {
          const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
          const items = [];
          const candidates = Array.from(document.querySelectorAll('a[href*="folderIds="], a[href*="checkedFolder="]'));
          for (const a of candidates) {
            const href = a.href || a.getAttribute('href') || '';
            let text = clean(a.innerText || a.textContent || '');
            let node = a;
            for (let i=0; i<4 && (!text || text.length < 2) && node.parentElement; i++) {
              node = node.parentElement;
              text = clean(node.innerText || node.textContent || '');
            }
            items.push({name: text, url: href});
          }
          return items;
        }
        """
        try:
            raw = page.evaluate(js)
        except Exception:
            raw = []
        folders: List[FolderInfo] = []
        for item in raw or []:
            url = normalize_url(item.get("url", ""))
            fid = folder_id_from_url(url)
            if not fid:
                continue
            name = item.get("name") or f"folder_{fid}"
            # If the text is huge, keep the likely folder line.
            if len(name) > 80:
                parts = [p.strip() for p in re.split(r"\s{2,}|\n", name) if p.strip()]
                name = parts[0] if parts else f"folder_{fid}"
            folders.append(FolderInfo(name=name, url=url, folder_id=fid))
        return folders

    def collect_folders(self, folders: List[FolderInfo]) -> Path:
        if not folders:
            raise ValueError("수집할 폴더가 없습니다.")
        started = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.output_dir / f"daglo_transcripts_{started}"
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest: List[Dict[str, Any]] = []

        with sync_playwright() as p:
            context = self._launch_context(p, headless=bool(self.cfg.get("headless", False)))
            page = context.pages[0] if context.pages else context.new_page()
            self._detect_login_by_navigation(page)

            for folder_idx, folder in enumerate(folders, start=1):
                self.log(f"[{folder_idx}/{len(folders)}] 폴더 스캔: {folder.name}")
                boards = self.scan_boards_in_folder(page, folder)
                self.log(f"  - 보드 {len(boards)}개 발견")
                folder_dir = run_dir / safe_filename(folder.name, folder.folder_id)
                folder_dir.mkdir(parents=True, exist_ok=True)

                for board_idx, board in enumerate(boards, start=1):
                    self.log(f"    [{board_idx}/{len(boards)}] 녹취록 수집: {board.title}")
                    status = "ok"
                    error_msg = ""
                    saved_path = ""
                    text = ""
                    try:
                        text, real_title = self.extract_transcript(page, board.url)
                        # IMPORTANT: the file name must come from the board detail title
                        # element (#board-board-title-button-name), not from the board-list
                        # card text. Board-list text can include keywords/search terms and
                        # produce noisy names such as "... 기능모델서버서비스...".
                        title_for_file = normalize_board_title(real_title) or normalize_board_title(board.title)
                        board.title = title_for_file or board.title
                        if not text or len(text.strip()) < 30:
                            raise RuntimeError("추출된 텍스트가 너무 짧습니다. 상세 페이지 HTML 구조 확인이 필요합니다.")
                        file_name = safe_filename(title_for_file, board_id_from_url(board.url)) + ".txt"
                        file_path = self._unique_transcript_path(folder_dir, title_for_file, board.url)
                        # v1.0.3: TXT file must contain only the exact text produced by Daglo's
                        # "스크립트 전체 복사" button. Metadata is kept in manifest.json only.
                        file_path.write_text(text.strip() + "\n", encoding="utf-8")
                        saved_path = str(file_path.relative_to(run_dir))
                    except Exception as e:
                        status = "error"
                        error_msg = f"{type(e).__name__}: {e}"
                        self.log(f"      실패: {error_msg}")
                        if self.cfg.get("save_html_debug_on_failure", True):
                            try:
                                debug_dir = run_dir / "_debug_failed_html"
                                debug_dir.mkdir(exist_ok=True)
                                debug_path = debug_dir / (safe_filename(board.title, board_id_from_url(board.url)) + ".html")
                                debug_path.write_text(self._page_content(page), encoding="utf-8")
                                saved_path = str(debug_path.relative_to(run_dir))
                            except Exception:
                                pass
                    manifest.append({
                        "folder": asdict(folder),
                        "board": asdict(board),
                        "status": status,
                        "saved_path": saved_path,
                        "error": error_msg,
                    })
                    atomic_json(run_dir / "manifest.json", manifest)
                    time.sleep(float(self.cfg.get("delay_seconds_between_boards", 0.6)))
            context.close()

        atomic_json(run_dir / "manifest.json", manifest)
        if self.cfg.get("zip_after_collect", True):
            zip_path = self.zip_output(run_dir)
            self.log(f"ZIP 생성 완료: {zip_path}")
            return zip_path
        self.log(f"수집 완료: {run_dir}")
        return run_dir

    def retry_missing_from_manifest(self, manifest_path: str | Path) -> Path:
        """Scan the current Daglo folders from an existing manifest and collect only new boards.

        v1.0.10 semantics:
        - Use the selected manifest.json as the baseline of already-known boards.
        - Re-scan the same Daglo folders that appear in the manifest.
        - If Daglo now contains boards whose /board/<id> + fileMetaId key is not
          in the manifest, collect only those newly-added boards.
        - Existing manifest entries are not re-collected just because they failed
          in an older run. This button is for "site has new transcripts now" sync.
        """
        return self.sync_new_from_manifest(manifest_path)

    def sync_new_from_manifest(self, manifest_path: str | Path) -> Path:
        manifest_path = Path(manifest_path).expanduser().resolve()
        if manifest_path.is_dir():
            manifest_path = manifest_path / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest.json 파일을 찾을 수 없습니다: {manifest_path}")
        if manifest_path.name.lower() != "manifest.json":
            self.log("선택한 파일명이 manifest.json은 아니지만 JSON으로 읽어봅니다.")

        run_dir = manifest_path.parent
        manifest_raw = manifest_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_raw)
        if not isinstance(manifest, list):
            raise RuntimeError("manifest.json 형식이 올바르지 않습니다. 최상위 값은 리스트여야 합니다.")

        known_keys: set[str] = set()
        folders_by_key: Dict[str, FolderInfo] = {}
        folder_dir_by_key: Dict[str, str] = {}

        for entry in manifest:
            if not isinstance(entry, dict):
                continue
            board_dict = entry.get("board") or {}
            folder_dict = entry.get("folder") or {}
            board_url = board_dict.get("url") or entry.get("url") or ""
            if board_url:
                known_keys.add(board_key_from_url(board_url))

            folder_url = folder_dict.get("url") or ""
            folder_id = folder_dict.get("folder_id") or folder_id_from_url(folder_url)
            folder_name = folder_dict.get("name") or board_dict.get("folder_name") or entry.get("folder_name") or "manifest_folder"
            if folder_url or folder_id:
                fkey = folder_id or folder_url
                folders_by_key[fkey] = FolderInfo(
                    name=str(folder_name),
                    url=str(folder_url or f"{DAGLO_ORIGIN}/board?folderIds={urllib.parse.quote(str(folder_id))}&checkedFolder={urllib.parse.quote(str(folder_id))}"),
                    folder_id=str(folder_id or ""),
                )
                saved_path = str(entry.get("saved_path") or "")
                if saved_path:
                    try:
                        first_part = Path(saved_path).parts[0]
                        if first_part not in {".", "..", "\\", "/"} and not Path(first_part).is_absolute() and not first_part.startswith("_"):
                            folder_dir_by_key.setdefault(fkey, first_part)
                    except Exception:
                        pass

        folders = list(folders_by_key.values())
        if not folders:
            raise RuntimeError("manifest에서 다시 스캔할 폴더 정보를 찾지 못했습니다.")

        self.log(f"manifest 기준 동기화 시작: 기존 보드 {len(known_keys)}개 / 폴더 {len(folders)}개")
        backup_path = run_dir / f"manifest.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        backup_path.write_text(manifest_raw, encoding="utf-8")
        self.log(f"기존 manifest 백업 완료: {backup_path.name}")

        new_boards: List[Tuple[FolderInfo, BoardInfo]] = []
        scanned_board_count = 0
        synced_at = datetime.now().isoformat(timespec="seconds")

        with sync_playwright() as p:
            context = self._launch_context(p, headless=bool(self.cfg.get("headless", False)))
            page = context.pages[0] if context.pages else context.new_page()
            self._detect_login_by_navigation(page)

            for folder_idx, folder in enumerate(folders, start=1):
                self.log(f"[{folder_idx}/{len(folders)}] 현재 사이트 폴더 재스캔: {folder.name}")
                boards = self.scan_boards_in_folder(page, folder)
                scanned_board_count += len(boards)
                folder_new = 0
                for board in boards:
                    key = board_key_from_url(board.url)
                    if not key or key in known_keys:
                        continue
                    known_keys.add(key)
                    new_boards.append((folder, board))
                    folder_new += 1
                self.log(f"  - 현재 보드 {len(boards)}개 / manifest에 없던 신규 {folder_new}개")

            if not new_boards:
                context.close()
                self.log(f"신규 녹취록이 없습니다. 현재 사이트 스캔 보드 {scanned_board_count}개")
                if self.cfg.get("manifest_sync_zip_after", self.cfg.get("manifest_retry_zip_after", True)) and self.cfg.get("zip_after_collect", True):
                    return self.zip_output(run_dir)
                return run_dir

            self.log(f"신규 녹취록 수집 시작: {len(new_boards)}개")
            for order, (folder, board) in enumerate(new_boards, start=1):
                self.log(f"  [{order}/{len(new_boards)}] 신규 수집: {board.title or board_id_from_url(board.url)}")
                status = "ok"
                error_msg = ""
                saved_path = ""
                try:
                    text, real_title = self.extract_transcript(page, board.url)
                    title_for_file = normalize_board_title(real_title) or normalize_board_title(board.title) or board_id_from_url(board.url)
                    board.title = title_for_file
                    fkey = folder.folder_id or folder.url
                    folder_dir_name = folder_dir_by_key.get(fkey) or safe_filename(folder.name, folder.folder_id or "folder")
                    folder_dir = run_dir / folder_dir_name
                    folder_dir.mkdir(parents=True, exist_ok=True)
                    file_path = self._unique_transcript_path(folder_dir, title_for_file, board.url)
                    file_path.write_text(text.strip() + "\n", encoding="utf-8")
                    saved_path = str(file_path.relative_to(run_dir))
                    self.log(f"      저장 완료: {saved_path}")
                except Exception as e:
                    status = "error"
                    error_msg = f"{type(e).__name__}: {e}"
                    self.log(f"      신규 수집 실패: {error_msg}")
                    if self.cfg.get("save_html_debug_on_failure", True):
                        try:
                            debug_dir = run_dir / "_debug_failed_html"
                            debug_dir.mkdir(exist_ok=True)
                            debug_path = debug_dir / (safe_filename(board.title, board_id_from_url(board.url)) + ".sync_new.html")
                            debug_path.write_text(self._page_content(page), encoding="utf-8")
                            saved_path = str(debug_path.relative_to(run_dir))
                        except Exception:
                            pass

                manifest.append({
                    "folder": asdict(folder),
                    "board": asdict(board),
                    "status": status,
                    "saved_path": saved_path,
                    "error": error_msg,
                    "sync_reason": "new_board_on_site",
                    "synced_at": synced_at,
                })
                atomic_json(manifest_path, manifest)
                time.sleep(float(self.cfg.get("delay_seconds_between_boards", 1.0)))
            context.close()

        atomic_json(manifest_path, manifest)
        self.log(f"manifest.json 업데이트 완료: 신규 항목 {len(new_boards)}개 추가")

        if self.cfg.get("manifest_sync_zip_after", self.cfg.get("manifest_retry_zip_after", True)) and self.cfg.get("zip_after_collect", True):
            zip_path = self.zip_output(run_dir)
            self.log(f"ZIP 재생성 완료: {zip_path}")
            return zip_path
        return run_dir

    def _manifest_missing_reason(self, entry: Dict[str, Any], run_dir: Path) -> str:
        status = str(entry.get("status") or "").lower()
        saved_path = str(entry.get("saved_path") or "").strip()
        if status != "ok":
            return f"status={entry.get('status') or 'empty'}"
        if not saved_path:
            return "saved_path empty"

        path = (run_dir / saved_path).resolve()
        try:
            path.relative_to(run_dir.resolve())
        except Exception:
            return "saved_path outside run_dir"
        if not path.exists():
            return "saved file missing"
        if path.suffix.lower() != ".txt":
            return f"saved file is not txt: {path.suffix or 'no suffix'}"

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"cannot read saved txt: {type(e).__name__}"

        if looks_like_bad_clipboard_or_log(text):
            return "saved txt is build/install log"
        if bool(self.cfg.get("validate_extracted_text", True)):
            ok, reason = looks_like_transcript(text, min_chars=int(self.cfg.get("min_transcript_chars", 100)))
            if not ok:
                return f"saved txt validation failed: {reason}"
        return ""

    def _detect_login_by_navigation(self, page) -> None:
        try:
            page.goto(self.cfg.get("start_url", "https://daglo.ai/board"), wait_until="domcontentloaded", timeout=60000)
            self._soft_wait(page)
            self._detect_login_needed(page)
        except PlaywrightError as error:
            raise RuntimeError("브라우저가 종료되었거나 페이지 이동에 실패했습니다. 로그인 창을 닫고 다시 시도하세요.") from error

    def _detect_login_needed(self, page) -> None:
        txt = ""
        try:
            txt = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return
        if "로그인" in txt and ("이메일" in txt or "비밀번호" in txt or "회원가입" in txt):
            raise RuntimeError("다글로 로그인이 필요합니다. 먼저 '로그인 브라우저 열기' 버튼으로 로그인 세션을 저장하세요.")

    def _soft_wait(self, page, seconds: float = 1.2) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        time.sleep(seconds)

    def scan_boards_in_folder(self, page, folder: FolderInfo) -> List[BoardInfo]:
        max_pages = int(self.cfg.get("max_pages_per_folder", 20))
        found: Dict[str, BoardInfo] = {}
        empty_streak = 0
        base_url = folder.url or self.cfg.get("start_url", "https://daglo.ai/board")

        for page_no in range(1, max_pages + 1):
            url = self._folder_page_url(base_url, folder.folder_id, page_no)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._soft_wait(page, seconds=0.8)
            except Exception as e:
                self.log(f"    page={page_no} 이동 실패: {e}")
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue

            boards = self._extract_boards_from_dom(page, folder.name)
            if not boards:
                boards = parse_boards_from_html(self._page_content(page), folder.name)

            before = len(found)
            for b in boards:
                if "fileMetaId=" not in b.url:
                    continue
                if b.url not in found:
                    found[b.url] = b
            added = len(found) - before
            self.log(f"    page={page_no}: 신규 {added}개")
            if added == 0:
                empty_streak += 1
                if empty_streak >= 2:
                    break
            else:
                empty_streak = 0

        return list(found.values())

    def _folder_page_url(self, base_url: str, folder_id: str, page_no: int) -> str:
        if folder_id:
            return f"{DAGLO_ORIGIN}/board?page={page_no}&folderIds={urllib.parse.quote(folder_id)}&checkedFolder={urllib.parse.quote(folder_id)}"
        parsed = urllib.parse.urlparse(base_url)
        qs = urllib.parse.parse_qs(parsed.query)
        qs["page"] = [str(page_no)]
        query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=query))

    def _extract_boards_from_dom(self, page, folder_name: str) -> List[BoardInfo]:
        js = r"""
        () => {
          const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
          const anchors = Array.from(document.querySelectorAll('a[href*="/board/"]'));
          const items = [];
          for (const a of anchors) {
            const href = a.href || a.getAttribute('href') || '';
            if (!/\/board\/[A-Za-z0-9_-]+\?fileMetaId=/.test(href)) continue;
            let text = clean(a.innerText || a.textContent || '');
            let node = a;
            for (let i=0; i<5 && (!text || text.length < 6) && node.parentElement; i++) {
              node = node.parentElement;
              text = clean(node.innerText || node.textContent || '');
            }
            items.push({title: text, url: href});
          }
          return items;
        }
        """
        try:
            raw = page.evaluate(js)
        except Exception:
            raw = []
        boards: List[BoardInfo] = []
        for item in raw or []:
            url = normalize_url(item.get("url", ""))
            title = item.get("title") or board_id_from_url(url)
            if len(title) > 120:
                # Keep the line that looks like a lecture title/date.
                bits = [b.strip() for b in re.split(r"\s{2,}|\n", title) if b.strip()]
                candidate = next((b for b in bits if re.search(r"20\d{2}|\d{1,2}\.\s*\d{1,2}", b)), bits[0] if bits else title)
                title = candidate[:120]
            boards.append(BoardInfo(title=title, url=url, folder_name=folder_name))
        return boards

    def extract_transcript(self, page, board_url: str) -> Tuple[str, str]:
        page.goto(board_url, wait_until="domcontentloaded", timeout=60000)
        self._reduce_page_motion(page)
        self._soft_wait(page, seconds=1.2)
        title = self._extract_title(page)

        min_chars = int(self.cfg.get("min_transcript_chars", 100))
        clean_ui = bool(self.cfg.get("clean_ui_lines", False))

        # v1.0.3: use Daglo's own "스크립트 전체 복사" result as the source of truth.
        # Do not save DOM-guessed text, because the user's requirement is exactly the
        # clipboard text generated by the rightmost copy-all button on a script paragraph.
        copied = self._try_copy_all(page)
        text = clean_transcript_text(copied, clean_ui_lines=clean_ui)
        ok, reason = looks_like_transcript(text, min_chars=min_chars)
        if ok:
            return text, title

        raise RuntimeError(
            "스크립트 전체 복사 버튼 결과를 얻지 못했습니다. "
            f"검증 사유: {reason}"
        )

    def _extract_title(self, page) -> str:
        # v1.0.3: use the exact board title element observed in Daglo HTML first.
        selectors = [
            "#board-board-title-button-name",
            "[id='board-board-title-button-name']",
            "header [role='button']",
            "h1",
        ]
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                txt = (loc.inner_text(timeout=2500) or "").strip()
                if txt:
                    return txt
            except Exception:
                pass
        try:
            title = page.title().replace("다글로 |", "").replace("다글로 -", "").strip()
            if title:
                return title
        except Exception:
            pass
        return "untitled"

    def _try_copy_all(self, page) -> str:
        """Use Daglo's own full-script-copy button without moving the OS mouse.

        v1.0.8: The previous implementation used Playwright hover/click fallbacks
        on paragraph elements. In a visible browser that can steal focus, make the
        UI feel sluggish, and increase CPU usage while Daglo repeatedly renders
        hover menus. This version defaults to a JS-only, low-focus path:
        reveal the hidden hover button in the DOM, dispatch the click event inside
        the page, and then read the clipboard result produced by Daglo.
        """
        previous = self._read_clipboard(page)

        def attempt_click(description: str, clicker, timeout_seconds: float = 2.5) -> str:
            sentinel = f"__DAGLO_COLLECTOR_SENTINEL_{time.time_ns()}__"
            self._write_clipboard(page, sentinel)
            try:
                clicker()
            except Exception as e:
                self.log(f"      copy-all 클릭 실패({description}): {type(e).__name__}")
                return ""
            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                time.sleep(0.18)
                txt = self._read_clipboard(page)
                if not txt or txt == sentinel:
                    continue
                if previous and txt == previous and looks_like_bad_clipboard_or_log(txt):
                    continue
                if looks_like_bad_clipboard_or_log(txt):
                    continue
                return txt
            return ""

        # 1) Low-focus DOM event path. No Playwright hover(), no real UI mouse movement.
        txt = attempt_click(
            "low-focus DOM #board-bookmark-button-copy-all",
            lambda: page.evaluate(
                """
                () => {
                  const editor = document.querySelector('#board-script-editor-content-editable-editor') || document;
                  const styleId = 'daglo-collector-low-focus-style';
                  if (!document.getElementById(styleId)) {
                    const style = document.createElement('style');
                    style.id = styleId;
                    style.textContent = `
                      * { scroll-behavior: auto !important; animation-duration: 0.001s !important; transition-duration: 0.001s !important; }
                      .bookmark { display: grid !important; opacity: 1 !important; visibility: visible !important; pointer-events: auto !important; }
                      #board-bookmark-button-copy-all { pointer-events: auto !important; }
                    `;
                    document.head.appendChild(style);
                  }

                  const candidates = Array.from(editor.querySelectorAll('#board-bookmark-button-copy-all'));
                  if (!candidates.length) throw new Error('copy-all button not found');

                  // Prefer the first paragraph copy-all button inside the script editor.
                  const btn = candidates[0];
                  btn.scrollIntoView({block: 'nearest', inline: 'nearest'});
                  const opts = {bubbles: true, cancelable: true, composed: true, view: window};
                  for (const type of ['pointerover','mouseover','pointerdown','mousedown','pointerup','mouseup','click']) {
                    let ev;
                    if (type.startsWith('pointer')) {
                      ev = new PointerEvent(type, opts);
                    } else {
                      ev = new MouseEvent(type, opts);
                    }
                    btn.dispatchEvent(ev);
                  }
                  return true;
                }
                """
            ),
        )
        if txt:
            return txt

        # 2) Optional interactive fallback. Disabled by default because it can steal
        # focus and make mouse/keyboard input feel broken during long collection.
        if not bool(self.cfg.get("interactive_hover_fallback", False)):
            return ""

        paragraph_selectors = [
            "#board-script-editor-content-editable-editor p.lexical__paragraph",
            "#board-script-editor-content-editable-editor p",
            "p.lexical__paragraph",
            "[data-lexical-editor='true'] p",
        ]
        for psel in paragraph_selectors:
            try:
                paragraphs = page.locator(psel)
                count = min(paragraphs.count(), 8)
            except Exception:
                continue
            for i in range(count):
                def click_paragraph_button(psel=psel, i=i):
                    p_loc = page.locator(psel).nth(i)
                    p_loc.hover(timeout=1200)
                    time.sleep(0.12)
                    btn = p_loc.locator("#board-bookmark-button-copy-all").first
                    btn.click(timeout=1500, force=True)
                txt = attempt_click(f"interactive hover paragraph {psel}[{i}]", click_paragraph_button)
                if txt:
                    return txt

        return ""

    def _reduce_page_motion(self, page) -> None:
        """Reduce rendering overhead on Daglo pages during collection."""
        try:
            page.add_style_tag(content="""
              * { scroll-behavior: auto !important; animation-duration: 0.001s !important; transition-duration: 0.001s !important; }
              video, canvas { visibility: hidden !important; }
            """)
        except Exception:
            pass

    def _read_clipboard(self, page) -> str:
        try:
            return page.evaluate("navigator.clipboard.readText()") or ""
        except Exception:
            return ""

    def _write_clipboard(self, page, text: str) -> bool:
        try:
            page.evaluate("txt => navigator.clipboard.writeText(txt)", text)
            return True
        except Exception:
            return False

    def _extract_transcript_dom(self, page) -> str:
        js = r"""
        () => {
          const clean = (s) => (s || '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
          const isVisible = (el) => {
            const st = window.getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return st && st.display !== 'none' && st.visibility !== 'hidden' && r.width > 10 && r.height > 5;
          };
          const scoreText = (txt) => {
            txt = clean(txt);
            if (txt.length < 80) return 0;
            const hangul = (txt.match(/[가-힣]/g) || []).length;
            const lines = (txt.match(/\n/g) || []).length;
            const times = (txt.match(/\b\d{1,2}:\d{2}(?::\d{2})?\b/g) || []).length;
            const uiBad = (txt.match(/로그인|회원가입|다운로드|공유|설정|폴더 목록|새 보드/g) || []).length;
            return txt.length + hangul * 1.2 + lines * 50 + times * 180 - uiBad * 500;
          };
          const selectors = [
            '[class*="script" i]', '[class*="transcript" i]', '[id*="script" i]', '[id*="transcript" i]',
            'main', 'article', 'section', '[role="main"]'
          ];
          const candidates = [];
          for (const sel of selectors) {
            for (const el of Array.from(document.querySelectorAll(sel))) {
              if (!isVisible(el)) continue;
              const txt = clean(el.innerText || el.textContent || '');
              const score = scoreText(txt);
              if (score > 0) candidates.push({score, txt, sel});
            }
          }
          // Also assemble paragraph-like visible text lines.
          const blocks = [];
          for (const el of Array.from(document.querySelectorAll('p, [data-slate-node], div, span'))) {
            if (!isVisible(el)) continue;
            const txt = clean(el.innerText || el.textContent || '');
            if (txt.length >= 12 && txt.length <= 1000) blocks.push(txt);
          }
          if (blocks.length) {
            const joined = blocks.join('\n');
            candidates.push({score: scoreText(joined) + 500, txt: joined, sel: 'assembled_blocks'});
          }
          candidates.sort((a,b) => b.score - a.score);
          if (candidates.length) return candidates[0].txt;
          return clean(document.body.innerText || document.body.textContent || '');
        }
        """
        try:
            return page.evaluate(js) or ""
        except Exception:
            try:
                return page.locator("body").inner_text(timeout=8000)
            except Exception:
                return ""

    def zip_output(self, run_dir: Path) -> Path:
        zip_path = run_dir.with_suffix(".zip")
        temporary = zip_path.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in run_dir.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(run_dir.parent))
        temporary.replace(zip_path)
        return zip_path


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1100x810")
        self.minsize(1000, 750)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.folders: List[FolderInfo] = []
        self.folder_vars: List[Tuple[ctk.BooleanVar, FolderInfo]] = []
        self.worker: Optional[threading.Thread] = None

        self.ui_queue = queue.Queue()
        self.update_busy = False
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll_logs)
        if self.cfg.get("auto_check_updates", True):
            self.after(2000, lambda: self.check_updates(manual=False))

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        sidebar = ctk.CTkFrame(self, width=260, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(12, weight=1)

        title = ctk.CTkLabel(sidebar, text="Daglo Collector", font=ctk.CTkFont(size=24, weight="bold"))
        title.grid(row=0, column=0, padx=20, pady=(24, 4), sticky="w")
        sub = ctk.CTkLabel(sidebar, text="녹취록 자동 수집기", text_color=("gray50", "gray70"))
        sub.grid(row=1, column=0, padx=20, pady=(0, 20), sticky="w")

        self.login_btn = ctk.CTkButton(sidebar, text="1. 로그인 브라우저 열기", command=self.login_browser)
        self.login_btn.grid(row=2, column=0, padx=20, pady=8, sticky="ew")
        self.scan_btn = ctk.CTkButton(sidebar, text="2. 폴더 스캔", command=self.scan_folders)
        self.scan_btn.grid(row=3, column=0, padx=20, pady=8, sticky="ew")
        self.collect_btn = ctk.CTkButton(sidebar, text="3. 선택 폴더 수집", command=self.collect_selected)
        self.collect_btn.grid(row=4, column=0, padx=20, pady=8, sticky="ew")
        self.retry_btn = ctk.CTkButton(sidebar, text="4. manifest 신규 동기화", command=self.retry_missing_from_manifest)
        self.retry_btn.grid(row=5, column=0, padx=20, pady=8, sticky="ew")

        ctk.CTkLabel(sidebar, text="브라우저", anchor="w").grid(row=6, column=0, padx=20, pady=(18, 4), sticky="ew")
        self.browser_var = ctk.StringVar(value=self.cfg.get("browser_channel", "chrome"))
        self.browser_menu = ctk.CTkOptionMenu(sidebar, values=["chrome", "msedge", "chromium"], variable=self.browser_var, command=self._on_browser_change)
        self.browser_menu.grid(row=7, column=0, padx=20, pady=(0, 8), sticky="ew")

        self.headless_var = ctk.BooleanVar(value=bool(self.cfg.get("headless", False)))
        self.headless_check = ctk.CTkCheckBox(sidebar, text="수집 시 브라우저 숨김", variable=self.headless_var, command=self._on_headless_change)
        self.headless_check.grid(row=8, column=0, padx=20, pady=8, sticky="w")

        ctk.CTkLabel(sidebar, text="저장 폴더", anchor="w").grid(row=9, column=0, padx=20, pady=(18, 4), sticky="ew")
        out_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        out_row.grid(row=10, column=0, padx=20, pady=4, sticky="ew")
        out_row.grid_columnconfigure(0, weight=1)
        self.out_entry = ctk.CTkEntry(out_row)
        self.out_entry.insert(0, str(self.cfg.get("output_dir", "output")))
        self.out_entry.grid(row=0, column=0, sticky="ew")
        self.browse_btn = ctk.CTkButton(out_row, text="...", width=42, command=self.pick_output_dir)
        self.browse_btn.grid(row=0, column=1, padx=(6, 0))

        self.zip_var = ctk.BooleanVar(value=bool(self.cfg.get("zip_after_collect", True)))
        self.zip_check = ctk.CTkCheckBox(sidebar, text="완료 후 ZIP 생성", variable=self.zip_var, command=self._on_zip_change)
        self.zip_check.grid(row=11, column=0, padx=20, pady=8, sticky="w")

        self.status_label = ctk.CTkLabel(sidebar, text="대기 중", anchor="w", text_color=("gray40", "gray70"))
        self.status_label.grid(row=13, column=0, padx=20, pady=(10, 8), sticky="ew")
        self.update_btn = ctk.CTkButton(sidebar, text=f"업데이트 확인 · v{APP_VERSION}", command=self.check_updates)
        self.update_btn.grid(row=14, column=0, padx=20, pady=6, sticky="ew")
        self.auto_update_var = ctk.BooleanVar(value=self.cfg.get("auto_check_updates", True))
        self.auto_update_check = ctk.CTkCheckBox(sidebar, text="시작할 때 새 버전 확인", variable=self.auto_update_var, command=self._save_cfg_from_ui)
        self.auto_update_check.grid(row=15, column=0, padx=20, pady=(4, 16), sticky="w")

        main = ctk.CTkFrame(self)
        main.grid(row=0, column=1, padx=18, pady=18, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=1)
        main.grid_rowconfigure(5, weight=1)

        header = ctk.CTkLabel(main, text="폴더 선택", font=ctk.CTkFont(size=20, weight="bold"), anchor="w")
        header.grid(row=0, column=0, padx=18, pady=(18, 6), sticky="ew")
        guide = ctk.CTkLabel(
            main,
            text="1번 로그인 후 폴더를 수집하세요. 4번은 기존 manifest를 기준으로 사이트에 새로 추가된 녹취록만 가져옵니다.",
            text_color=("gray45", "gray70"), anchor="w"
        )
        guide.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="ew")

        self.folder_frame = ctk.CTkScrollableFrame(main, height=210)
        self.folder_frame.grid(row=2, column=0, padx=18, pady=8, sticky="nsew")

        btn_row = ctk.CTkFrame(main, fg_color="transparent")
        btn_row.grid(row=3, column=0, padx=18, pady=(4, 4), sticky="ew")
        self.select_all_btn = ctk.CTkButton(btn_row, text="전체 선택", width=100, command=self.select_all)
        self.select_all_btn.pack(side="left", padx=(0, 8))
        self.clear_btn = ctk.CTkButton(btn_row, text="선택 해제", width=100, command=self.clear_selection)
        self.clear_btn.pack(side="left")

        log_label = ctk.CTkLabel(main, text="작업 로그", font=ctk.CTkFont(size=18, weight="bold"), anchor="w")
        log_label.grid(row=4, column=0, padx=18, pady=(16, 4), sticky="sw")
        self.log_box = ctk.CTkTextbox(main, height=230)
        self.log_box.grid(row=5, column=0, padx=18, pady=(0, 18), sticky="nsew")
        self.log_box.insert("end", "준비 완료.\n")
        self.log_box.configure(state="disabled")

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{ts}] {msg}")

    def _poll_logs(self):
        while True:
            try:
                callback = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            callback()
        changed = False
        while True:
            try:
                msg = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
            self.status_label.configure(text=msg[-38:])
            changed = True
        self.after(150, self._poll_logs)

    def _run_worker(self, name: str, target: Callable[[], None]):
        if self.update_busy or (self.worker and self.worker.is_alive()):
            messagebox.showinfo("작업 진행 중", "이미 작업이 진행 중입니다.")
            return
        self._save_cfg_from_ui()
        self._set_buttons(False)

        def wrapped():
            self._log(f"{name} 시작")
            try:
                target()
                self._log(f"{name} 완료")
            except Exception as e:
                tb = traceback.format_exc()
                self._log(f"오류: {type(e).__name__}: {e}")
                debug = data_dir() / "last_error.log"
                debug.write_text(tb, encoding="utf-8")
                self._log(f"상세 오류 저장: {debug}")
            finally:
                self.ui_queue.put(lambda: self._set_buttons(True))

        self.worker = threading.Thread(target=wrapped, daemon=True)
        self.worker.start()

    def _set_buttons(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for b in [self.login_btn, self.scan_btn, self.collect_btn, self.retry_btn, self.select_all_btn, self.clear_btn, self.browse_btn, self.browser_menu, self.headless_check, self.zip_check, self.out_entry, self.update_btn]:
            b.configure(state=state)

    def _save_cfg_from_ui(self):
        self.cfg["browser_channel"] = self.browser_var.get()
        self.cfg["headless"] = bool(self.headless_var.get())
        self.cfg["output_dir"] = self.out_entry.get().strip() or "output"
        self.cfg["zip_after_collect"] = bool(self.zip_var.get())
        self.cfg["auto_check_updates"] = bool(self.auto_update_var.get())
        save_config(self.cfg)

    def _on_browser_change(self, _=None):
        self._save_cfg_from_ui()

    def _on_headless_change(self):
        self._save_cfg_from_ui()

    def _on_zip_change(self):
        self._save_cfg_from_ui()

    def pick_output_dir(self):
        path = filedialog.askdirectory(title="녹취록 저장 폴더 선택")
        if path:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, path)
            self._save_cfg_from_ui()

    def _on_close(self):
        if self.update_busy:
            messagebox.showinfo("업데이트 진행 중", "업데이트 확인 또는 다운로드가 끝난 뒤 종료하세요.")
            return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("작업 진행 중", "현재 작업이 끝난 뒤 종료하세요. 완료된 항목은 manifest에 저장됩니다.")
            return
        self.destroy()

    def check_updates(self, manual=True):
        if self.update_busy or (self.worker and self.worker.is_alive()):
            if manual:
                messagebox.showinfo("작업 진행 중", "현재 작업이 끝난 뒤 업데이트를 확인하세요.")
            return
        self.update_busy = True
        self._set_buttons(False)
        self._log("새 버전을 확인합니다.")

        def task():
            try:
                update = check_update()
                self.ui_queue.put(lambda: self._update_checked(update, manual))
            except Exception as error:
                self.ui_queue.put(lambda error=error: self._update_error(error, manual))
        threading.Thread(target=task, daemon=True).start()

    def _update_error(self, error, show=True):
        self.update_busy = False
        self._set_buttons(True)
        self._log(f"업데이트 실패: {error}")
        if show:
            messagebox.showerror("업데이트 실패", f"{error}\n\n다시 확인하거나 GitHub 릴리스에서 직접 설치할 수 있습니다.\n{RELEASES_URL}")

    def _update_checked(self, update, manual):
        self.update_busy = False
        self._set_buttons(True)
        if not update:
            self._log("현재 공개된 최신 버전을 사용 중입니다.")
            if manual:
                messagebox.showinfo("업데이트 확인", f"현재 버전: {APP_VERSION}\n새 정식 버전이 없습니다.")
            return
        if not manual and self.cfg.get("skipped_update") == update.version:
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("새 버전이 있습니다")
        dialog.geometry("620x460")
        dialog.transient(self)
        dialog.grab_set()
        ctk.CTkLabel(dialog, text=f"v{APP_VERSION} → v{update.version}", font=ctk.CTkFont(size=22, weight="bold")).pack(padx=20, pady=15)
        notes = ctk.CTkTextbox(dialog, height=240)
        notes.pack(fill="both", expand=True, padx=20)
        notes.insert("end", update.notes)
        notes.configure(state="disabled")
        ctk.CTkLabel(dialog, text="설정·로그인·녹취록은 유지됩니다. 검증 후 설치 화면이 열립니다.").pack(pady=10)
        row = ctk.CTkFrame(dialog, fg_color="transparent")
        row.pack(padx=20, pady=(0, 20))
        def install():
            dialog.destroy()
            self._download_update(update)
        def skip():
            self.cfg["skipped_update"] = update.version
            save_config(self.cfg)
            dialog.destroy()
        ctk.CTkButton(row, text="다운로드 및 설치", command=install).pack(side="left", padx=4)
        ctk.CTkButton(row, text="이 버전 건너뛰기", command=skip).pack(side="left", padx=4)
        ctk.CTkButton(row, text="나중에", command=dialog.destroy, width=80).pack(side="left", padx=4)

    def _download_update(self, update):
        self.update_busy = True
        self._set_buttons(False)
        def task():
            last_percent = -1
            def progress(done, total):
                nonlocal last_percent
                percent = int(done * 100 / total)
                if percent // 10 != last_percent // 10:
                    self._log(f"업데이트 다운로드 {percent}%")
                    last_percent = percent
            try:
                path = download_update(update, data_dir() / "updates", progress)
                self.ui_queue.put(lambda: self._install_update(path))
            except Exception as error:
                self.ui_queue.put(lambda error=error: self._update_error(error))
        threading.Thread(target=task, daemon=True).start()

    def _install_update(self, path):
        try:
            launch_installer(path)
        except Exception as error:
            self._update_error(error)
            return
        self.destroy()

    def login_browser(self):
        def task():
            DagloCollector(self.cfg, self._log).open_login_browser()
        self._run_worker("로그인 세션 저장", task)

    def scan_folders(self):
        def task():
            folders = DagloCollector(self.cfg, self._log).scan_folders()
            self.folders = folders
            self.ui_queue.put(self._render_folders)
        self._run_worker("폴더 스캔", task)

    def _render_folders(self):
        for widget in self.folder_frame.winfo_children():
            widget.destroy()
        self.folder_vars.clear()
        if not self.folders:
            ctk.CTkLabel(self.folder_frame, text="폴더가 없습니다. 로그인 상태를 확인하세요.").pack(anchor="w", padx=10, pady=10)
            return
        for f in self.folders:
            var = ctk.BooleanVar(value=True)
            label = f"{f.name}  ({f.folder_id})" if f.folder_id and f.folder_id not in f.name else f.name
            cb = ctk.CTkCheckBox(self.folder_frame, text=label, variable=var)
            cb.pack(anchor="w", padx=12, pady=6)
            self.folder_vars.append((var, f))

    def select_all(self):
        for var, _ in self.folder_vars:
            var.set(True)

    def clear_selection(self):
        for var, _ in self.folder_vars:
            var.set(False)

    def collect_selected(self):
        selected = [f for var, f in self.folder_vars if var.get()]
        if not selected:
            messagebox.showwarning("선택 필요", "수집할 폴더를 먼저 선택하세요.")
            return

        def task():
            result = DagloCollector(self.cfg, self._log).collect_folders(selected)
            self._log(f"결과 위치: {result}")
        self._run_worker("녹취록 수집", task)


    def retry_missing_from_manifest(self):
        path = filedialog.askopenfilename(
            title="신규 동기화할 output/manifest.json 선택",
            filetypes=[("manifest.json", "manifest.json"), ("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return

        def task():
            result = DagloCollector(self.cfg, self._log).retry_missing_from_manifest(path)
            self._log(f"신규 동기화 결과 위치: {result}")
        self._run_worker("manifest 신규 동기화", task)


def main():
    lock = None
    if os.name == "nt" and "--self-test" not in sys.argv:
        import msvcrt
        lock = (data_dir() / "app.lock").open("a+b")
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            messagebox.showinfo(APP_TITLE, "앱이 이미 실행 중입니다. 기존 창을 사용하세요.")
            lock.close()
            return
    try:
        app = App()
        if "--self-test" in sys.argv:
            app.update()
            if "--browser-test" in sys.argv:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    page = browser.new_page()
                    page.set_content("<h1>Daglo smoke test</h1>")
                    assert page.locator("h1").inner_text() == "Daglo smoke test"
                    browser.close()
            atomic_json(data_dir() / "self-test.json", {"version": APP_VERSION, "gui": "ok", "browser": "ok" if "--browser-test" in sys.argv else "not requested"})
            app.destroy()
        else:
            app.mainloop()
    finally:
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
