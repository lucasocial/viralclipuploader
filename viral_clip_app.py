#!/usr/bin/env python3
"""
Viral Clip Generator - GUI App
Run with: python viral_clip_app.py
"""

import subprocess
import sys
import os
import json
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
from pathlib import Path

NO_WINDOW    = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
CONFIG_FILE  = Path.home() / ".viral_clip_config.json"
TOKEN_FILE   = Path.home() / ".viral_clip_yt_token.json"
TT_TOKEN_FILE= Path.home() / ".viral_clip_tt_token.json"
SECRET_FILE  = Path("C:/Users/Luca/Tradingview/client_secret.json")
YT_SCOPES    = ["https://www.googleapis.com/auth/youtube.upload"]

# ── Colors ────────────────────────────────────────────────────────────────────
BG       = "#0d1117"
CARD     = "#161b22"
CARD2    = "#1c2128"
ACCENT   = "#58a6ff"
RED      = "#f85149"
GREEN    = "#3fb950"
YELLOW   = "#d29922"
TEXT     = "#e6edf3"
MUTED    = "#7d8590"
BORDER   = "#30363d"
ENTRY_BG = "#0d1117"
FONT     = ("Segoe UI", 10)
FONT_B   = ("Segoe UI", 10, "bold")
FONT_S   = ("Segoe UI", 9)
MONO     = ("Consolas", 9)


def send_telegram(token: str, chat_id: str, message: str):
    import urllib.request, urllib.parse
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": message, "parse_mode": "HTML"
    }).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)


def parse_schedule_time(time_str: str):
    """Parse HH:MM local time → UTC-aware datetime. Returns tomorrow if time already passed."""
    import datetime
    h, m = map(int, time_str.strip().split(":"))
    local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
    now_local = datetime.datetime.now(local_tz)
    scheduled = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
    if scheduled <= now_local:
        scheduled += datetime.timedelta(days=1)
    return scheduled.astimezone(datetime.timezone.utc)


def save_config(data: dict):
    existing = load_config()
    existing.update(data)
    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def install_deps_if_needed(upload_yt: bool = False):
    packages = {"yt_dlp": "yt-dlp", "whisper": "openai-whisper", "anthropic": "anthropic"}
    if upload_yt:
        packages["googleapiclient"] = "google-api-python-client"
        packages["google_auth_oauthlib"] = "google-auth-oauthlib"
    for module, pkg in packages.items():
        try:
            __import__(module)
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"],
                           check=True, creationflags=NO_WINDOW)


def get_tiktok_access_token() -> str:
    import urllib.request, urllib.parse, webbrowser, http.server, threading
    import hashlib, base64, secrets, time

    cfg = load_config()
    client_key    = cfg.get("tiktok_client_key", "")
    client_secret = cfg.get("tiktok_client_secret", "")

    if TT_TOKEN_FILE.exists():
        token_data = json.loads(TT_TOKEN_FILE.read_text())
        if token_data.get("expires_at", 0) > time.time() + 60:
            return token_data["access_token"]

    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    redirect_uri = "https://lucasocial.github.io/viralclipuploader/callback.html"
    auth_url = (
        f"https://www.tiktok.com/v2/auth/authorize/"
        f"?client_key={client_key}"
        f"&scope=video.upload,video.publish"
        f"&response_type=code"
        f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
        f"&code_challenge={code_challenge}"
        f"&code_challenge_method=S256"
    )

    code_holder = {}
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"] = params.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Auth successful! You can close this tab.</h2></body></html>")
        def log_message(self, *args): pass

    server = http.server.HTTPServer(("localhost", 8888), Handler)
    t = threading.Thread(target=server.handle_request)
    t.start()
    try:
        ff_path = r"C:\Program Files\Mozilla Firefox\firefox.exe"
        if os.path.exists(ff_path):
            subprocess.Popen([ff_path, auth_url], creationflags=NO_WINDOW)
        else:
            webbrowser.open(auth_url)
    except Exception:
        webbrowser.open(auth_url)
    t.join(timeout=300)
    server.server_close()

    code = code_holder.get("code", "")
    if not code:
        raise Exception("TikTok auth timed out or was cancelled.")

    token_url = "https://open.tiktokapis.com/v2/oauth/token/"
    data = urllib.parse.urlencode({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }).encode()
    req = urllib.request.Request(token_url, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = json.loads(urllib.request.urlopen(req).read())
    access_token = resp.get("access_token", "")
    if not access_token:
        raise Exception(f"TikTok token error: {resp}")

    import time as _time
    TT_TOKEN_FILE.write_text(json.dumps({
        "access_token": access_token,
        "expires_at": _time.time() + resp.get("expires_in", 86400),
    }))
    return access_token


def upload_to_tiktok(video_path: str, title: str, log_fn, schedule_time=None) -> str:
    import urllib.request
    log_fn("  Authenticating with TikTok ...")
    access_token = get_tiktok_access_token()
    log_fn("  Initializing TikTok upload ...")
    file_size = os.path.getsize(video_path)
    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    post_info = {"title": title[:150], "privacy_level": "SELF_ONLY",
                 "disable_duet": False, "disable_comment": False, "disable_stitch": False}
    if schedule_time:
        import math
        post_info["scheduled_publish_time"] = math.floor(schedule_time.timestamp())
        log_fn(f"  Scheduled for: {schedule_time.strftime('%Y-%m-%d %H:%M UTC')}")
    init_body = json.dumps({
        "post_info": post_info,
        "source_info": {"source": "FILE_UPLOAD", "video_size": file_size,
                        "chunk_size": file_size, "total_chunk_count": 1}
    }).encode()
    req = urllib.request.Request(init_url, data=init_body, headers={
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8"})
    init_resp = json.loads(urllib.request.urlopen(req).read())
    if init_resp.get("error", {}).get("code", "ok") != "ok":
        raise Exception(f"TikTok init error: {init_resp}")
    publish_id = init_resp["data"]["publish_id"]
    upload_url = init_resp["data"]["upload_url"]
    log_fn("  Uploading video to TikTok ...")
    with open(video_path, "rb") as f:
        video_data = f.read()
    upload_req = urllib.request.Request(upload_url, data=video_data, method="PUT", headers={
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{file_size-1}/{file_size}",
        "Content-Length": str(file_size)})
    urllib.request.urlopen(upload_req)
    log_fn(f"  Uploaded! Publish ID: {publish_id}")
    return publish_id


def get_youtube_service():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), YT_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_FILE), YT_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload_to_youtube(video_path: str, title: str, description: str, tags: list, log_fn,
                      schedule_time=None) -> str:
    from googleapiclient.http import MediaFileUpload
    log_fn("  Authenticating with YouTube ...")
    youtube = get_youtube_service()
    log_fn("  Uploading to YouTube ...")
    status_body = {"privacyStatus": "private"}
    if schedule_time:
        status_body["publishAt"] = schedule_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        log_fn(f"  Scheduled for: {schedule_time.strftime('%Y-%m-%d %H:%M UTC')}")
    body = {
        "snippet": {"title": title, "description": description,
                    "tags": tags + ["shorts", "viral"], "categoryId": "22"},
        "status": status_body,
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log_fn(f"  Upload progress: {int(status.progress() * 100)}%")
    return f"https://www.youtube.com/watch?v={response.get('id', '')}"


# ── Custom Widgets ─────────────────────────────────────────────────────────────

class TabBar(tk.Frame):
    def __init__(self, parent, tabs, on_change, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.buttons = []
        self.active = 0
        self.on_change = on_change
        for i, name in enumerate(tabs):
            btn = tk.Label(self, text=name, font=FONT_B, bg=BG, fg=MUTED,
                           padx=18, pady=10, cursor="hand2")
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, idx=i: self._select(idx))
            self.buttons.append(btn)
        self._select(0)

    def _select(self, idx):
        for i, btn in enumerate(self.buttons):
            if i == idx:
                btn.configure(fg=TEXT)
                # underline effect
                btn.configure(font=("Segoe UI", 10, "bold"))
            else:
                btn.configure(fg=MUTED, font=FONT)
        self.active = idx
        self.on_change(idx)


class FlatEntry(tk.Entry):
    def __init__(self, parent, **kw):
        defaults = dict(bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
                        relief="flat", highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=ACCENT,
                        font=FONT)
        defaults.update(kw)
        super().__init__(parent, **defaults)


class FlatCheck(tk.Checkbutton):
    def __init__(self, parent, **kw):
        defaults = dict(bg=CARD, fg=TEXT, activebackground=CARD,
                        activeforeground=TEXT, selectcolor=ENTRY_BG,
                        relief="flat", font=FONT)
        defaults.update(kw)
        super().__init__(parent, **defaults)


class Section(tk.Frame):
    def __init__(self, parent, title="", **kw):
        super().__init__(parent, bg=CARD, bd=0, highlightthickness=1,
                         highlightbackground=BORDER, **kw)
        if title:
            tk.Label(self, text=title, font=("Segoe UI", 8, "bold"),
                     bg=CARD, fg=MUTED).pack(anchor="w", padx=14, pady=(10, 2))


# ── Main App ───────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Viral Clip Generator")
        self.geometry("700x720")
        self.minsize(700, 720)
        self.configure(bg=BG)
        self.running = False
        self._pages = []
        self._build_ui()
        self._load_saved_config()

    def _build_ui(self):
        # ── Header ──
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=0, pady=0)
        tk.Frame(hdr, bg=BORDER, height=1).pack(fill="x", side="bottom")

        left = tk.Frame(hdr, bg=BG)
        left.pack(side="left", padx=20, pady=14)
        tk.Label(left, text="⚡ Viral Clip Generator", font=("Segoe UI", 13, "bold"),
                 bg=BG, fg=TEXT).pack(side="left")
        tk.Label(left, text="  AI-powered", font=FONT_S,
                 bg=BG, fg=MUTED).pack(side="left", pady=(2, 0))

        self.status_dot = tk.Label(hdr, text="● Ready", font=FONT_S, bg=BG, fg=GREEN)
        self.status_dot.pack(side="right", padx=20)

        # ── Tab Bar (placeholder frame — TabBar added after pages) ──
        self._tab_frame = tk.Frame(self, bg=BG)
        self._tab_frame.pack(fill="x")
        tk.Frame(self._tab_frame, bg=BORDER, height=1).pack(fill="x", side="bottom")

        self.content = tk.Frame(self, bg=BG)
        self.content.pack(fill="both", expand=True)

        pages = ["  Generate  ", "  Settings  ", "  Upload  ", "  Log  "]

        # ── Page 0: Generate ──
        p0 = tk.Frame(self.content, bg=BG)
        self._pages.append(p0)

        url_sec = Section(p0, "SOURCE VIDEO")
        url_sec.pack(fill="x", padx=20, pady=(16, 8))
        tk.Label(url_sec, text="YouTube URL", font=FONT_S, bg=CARD, fg=MUTED)\
            .pack(anchor="w", padx=14, pady=(4, 2))
        self.url_var = tk.StringVar()
        FlatEntry(url_sec, textvariable=self.url_var, width=60)\
            .pack(fill="x", padx=14, pady=(0, 12), ipady=7)

        title_sec = Section(p0, "CUSTOM TITLE  (optional — leave empty for AI-generated)")
        title_sec.pack(fill="x", padx=20, pady=(0, 8))
        self.yt_title_var = tk.StringVar()
        FlatEntry(title_sec, textvariable=self.yt_title_var, width=60)\
            .pack(fill="x", padx=14, pady=(4, 12), ipady=6)

        upload_sec = Section(p0, "UPLOAD TO")
        upload_sec.pack(fill="x", padx=20, pady=(0, 8))
        yt_row = tk.Frame(upload_sec, bg=CARD)
        yt_row.pack(fill="x", padx=14, pady=(6, 2))
        self.yt_var = tk.BooleanVar()
        FlatCheck(yt_row, text="YouTube  (private)", variable=self.yt_var).pack(side="left")
        self.yt_shorts_var = tk.BooleanVar(value=True)
        FlatCheck(yt_row, text="Crop 9:16 for Shorts", variable=self.yt_shorts_var,
                  fg=MUTED, font=FONT_S).pack(side="left", padx=(20, 0))

        yt_sched_row = tk.Frame(upload_sec, bg=CARD)
        yt_sched_row.pack(fill="x", padx=30, pady=(0, 6))
        self.yt_sched_var = tk.BooleanVar()
        FlatCheck(yt_sched_row, text="Schedule:", variable=self.yt_sched_var,
                  font=FONT_S, fg=MUTED).pack(side="left")
        self.yt_time_var = tk.StringVar(value="12:00")
        FlatEntry(yt_sched_row, textvariable=self.yt_time_var, width=6,
                  font=FONT_S).pack(side="left", padx=(6, 0), ipady=3)
        tk.Label(yt_sched_row, text="  HH:MM  (local time — today or tomorrow)",
                 font=FONT_S, bg=CARD, fg=MUTED).pack(side="left")

        tt_row = tk.Frame(upload_sec, bg=CARD)
        tt_row.pack(fill="x", padx=14, pady=(4, 2))
        self.tt_var = tk.BooleanVar()
        FlatCheck(tt_row, text="TikTok  (private)", variable=self.tt_var).pack(side="left")

        tt_sched_row = tk.Frame(upload_sec, bg=CARD)
        tt_sched_row.pack(fill="x", padx=30, pady=(0, 12))
        self.tt_sched_var = tk.BooleanVar()
        FlatCheck(tt_sched_row, text="Schedule:", variable=self.tt_sched_var,
                  font=FONT_S, fg=MUTED).pack(side="left")
        self.tt_time_var = tk.StringVar(value="12:00")
        FlatEntry(tt_sched_row, textvariable=self.tt_time_var, width=6,
                  font=FONT_S).pack(side="left", padx=(6, 0), ipady=3)
        tk.Label(tt_sched_row, text="  HH:MM  (local time — today or tomorrow)",
                 font=FONT_S, bg=CARD, fg=MUTED).pack(side="left")

        # Generate button
        self.btn = tk.Button(p0, text="▶  Generate Viral Clip",
                             command=self._start,
                             font=("Segoe UI", 11, "bold"),
                             bg=ACCENT, fg=BG,
                             activebackground="#79b8ff", activeforeground=BG,
                             relief="flat", cursor="hand2", padx=24, pady=11,
                             bd=0)
        self.btn.pack(pady=14)

        # Progress bar
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("flat.Horizontal.TProgressbar",
                        background=ACCENT, troughcolor=CARD2,
                        bordercolor=CARD2, lightcolor=ACCENT, darkcolor=ACCENT)
        self.progress = ttk.Progressbar(p0, style="flat.Horizontal.TProgressbar",
                                        mode="determinate", length=640, value=0, maximum=100)
        self.progress.pack(padx=20, pady=(0, 6))

        self.status_var = tk.StringVar(value="Ready to generate")
        tk.Label(p0, textvariable=self.status_var, font=FONT_S,
                 bg=BG, fg=MUTED).pack(pady=(0, 4))

        # ── Page 1: Settings ──
        p1 = tk.Frame(self.content, bg=BG)
        self._pages.append(p1)

        api_sec = Section(p1, "ANTHROPIC API KEY")
        api_sec.pack(fill="x", padx=20, pady=(16, 8))
        self.key_var = tk.StringVar()
        key_entry = FlatEntry(api_sec, textvariable=self.key_var, show="•", width=60)
        key_entry.pack(fill="x", padx=14, pady=(4, 4), ipady=7)
        show_row = tk.Frame(api_sec, bg=CARD)
        show_row.pack(anchor="w", padx=14, pady=(0, 10))
        self.show_var = tk.BooleanVar()
        FlatCheck(show_row, text="Show key", variable=self.show_var, font=FONT_S,
                  command=lambda: key_entry.config(show="" if self.show_var.get() else "•"))\
            .pack(side="left")
        tk.Label(show_row, text="  Saved locally — never transmitted",
                 font=FONT_S, bg=CARD, fg=MUTED).pack(side="left")

        tt_sec = Section(p1, "TIKTOK CREDENTIALS")
        tt_sec.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(tt_sec, text="Client Key", font=FONT_S, bg=CARD, fg=MUTED)\
            .pack(anchor="w", padx=14, pady=(4, 2))
        self.tt_key_var = tk.StringVar()
        FlatEntry(tt_sec, textvariable=self.tt_key_var, width=60)\
            .pack(fill="x", padx=14, pady=(0, 6), ipady=6)
        tk.Label(tt_sec, text="Client Secret", font=FONT_S, bg=CARD, fg=MUTED)\
            .pack(anchor="w", padx=14, pady=(0, 2))
        self.tt_secret_var = tk.StringVar()
        FlatEntry(tt_sec, textvariable=self.tt_secret_var, show="•", width=60)\
            .pack(fill="x", padx=14, pady=(0, 10), ipady=6)

        tg_sec = Section(p1, "TELEGRAM NOTIFICATIONS")
        tg_sec.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(tg_sec, text="Bot Token", font=FONT_S, bg=CARD, fg=MUTED)\
            .pack(anchor="w", padx=14, pady=(4, 2))
        self.tg_token_var = tk.StringVar()
        FlatEntry(tg_sec, textvariable=self.tg_token_var, show="•", width=60)\
            .pack(fill="x", padx=14, pady=(0, 6), ipady=6)
        tk.Label(tg_sec, text="Chat ID", font=FONT_S, bg=CARD, fg=MUTED)\
            .pack(anchor="w", padx=14, pady=(0, 2))
        self.tg_chat_var = tk.StringVar()
        FlatEntry(tg_sec, textvariable=self.tg_chat_var, width=60)\
            .pack(fill="x", padx=14, pady=(0, 6), ipady=6)
        tg_row = tk.Frame(tg_sec, bg=CARD)
        tg_row.pack(fill="x", padx=14, pady=(0, 10))
        tk.Button(tg_row, text="Send Test Message", font=FONT_S, bg=CARD2, fg=TEXT,
                  activebackground=BORDER, activeforeground=TEXT,
                  relief="flat", cursor="hand2", padx=12, pady=4,
                  command=self._test_telegram).pack(side="left")
        tk.Label(tg_row, text="  Create bot via @BotFather · Chat ID via @userinfobot",
                 font=FONT_S, bg=CARD, fg=MUTED).pack(side="left")

        save_btn = tk.Button(p1, text="Save Settings",
                             command=self._save_settings,
                             font=FONT_B, bg=GREEN, fg=BG,
                             activebackground="#56d364", activeforeground=BG,
                             relief="flat", cursor="hand2", padx=20, pady=8)
        save_btn.pack(pady=14, anchor="w", padx=20)

        # ── Page 2: Upload ──
        p2 = tk.Frame(self.content, bg=BG)
        self._pages.append(p2)

        tk.Label(p2, text="Upload Settings", font=("Segoe UI", 12, "bold"),
                 bg=BG, fg=TEXT).pack(anchor="w", padx=20, pady=(16, 8))

        yt_sec = Section(p2, "YOUTUBE")
        yt_sec.pack(fill="x", padx=20, pady=(0, 8))
        tk.Label(yt_sec, text="client_secret.json path:",
                 font=FONT_S, bg=CARD, fg=MUTED).pack(anchor="w", padx=14, pady=(4, 2))
        tk.Label(yt_sec, text=str(SECRET_FILE),
                 font=FONT_S, bg=CARD, fg=ACCENT).pack(anchor="w", padx=14, pady=(0, 4))
        yt_status = "✓  Found" if SECRET_FILE.exists() else "✗  Not found"
        yt_color  = GREEN if SECRET_FILE.exists() else RED
        tk.Label(yt_sec, text=yt_status, font=FONT_S, bg=CARD, fg=yt_color)\
            .pack(anchor="w", padx=14, pady=(0, 10))

        tt_sec2 = Section(p2, "TIKTOK")
        tt_sec2.pack(fill="x", padx=20, pady=(0, 8))
        cfg = load_config()
        tt_ok = bool(cfg.get("tiktok_client_key"))
        tt_status = "✓  Credentials saved" if tt_ok else "✗  No credentials — set in Settings tab"
        tt_color  = GREEN if tt_ok else RED
        tk.Label(tt_sec2, text=tt_status, font=FONT_S, bg=CARD, fg=tt_color)\
            .pack(anchor="w", padx=14, pady=(4, 10))

        # ── Page 3: Log ──
        p3 = tk.Frame(self.content, bg=BG)
        self._pages.append(p3)

        log_hdr = tk.Frame(p3, bg=BG)
        log_hdr.pack(fill="x", padx=20, pady=(12, 4))
        tk.Label(log_hdr, text="Process Log", font=FONT_B, bg=BG, fg=TEXT).pack(side="left")
        tk.Button(log_hdr, text="Clear", font=FONT_S, bg=CARD2, fg=MUTED,
                  relief="flat", cursor="hand2", padx=10, pady=3,
                  command=lambda: (self.log.configure(state="normal"),
                                   self.log.delete("1.0", "end"),
                                   self.log.configure(state="disabled")))\
            .pack(side="right")

        self.log = scrolledtext.ScrolledText(
            p3, font=MONO, bg="#010409", fg="#58a6ff",
            insertbackground="white", relief="flat",
            state="disabled", wrap="word",
            selectbackground=CARD2
        )
        self.log.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        # Now init TabBar after all pages are built
        self.tab_bar = TabBar(self._tab_frame, pages, self._show_tab)
        self.tab_bar.pack(fill="x", side="top")
        self._show_tab(0)

    def _show_tab(self, idx):
        for p in self._pages:
            p.pack_forget()
        self._pages[idx].pack(fill="both", expand=True)

    def _load_saved_config(self):
        cfg = load_config()
        if cfg.get("api_key"):
            self.key_var.set(cfg["api_key"])
        if cfg.get("tiktok_client_key"):
            self.tt_key_var.set(cfg["tiktok_client_key"])
        if cfg.get("tiktok_client_secret"):
            self.tt_secret_var.set(cfg["tiktok_client_secret"])
        if cfg.get("telegram_token"):
            self.tg_token_var.set(cfg["telegram_token"])
        if cfg.get("telegram_chat_id"):
            self.tg_chat_var.set(cfg["telegram_chat_id"])

    def _save_settings(self):
        save_config({
            "api_key": self.key_var.get().strip(),
            "tiktok_client_key": self.tt_key_var.get().strip(),
            "tiktok_client_secret": self.tt_secret_var.get().strip(),
            "telegram_token": self.tg_token_var.get().strip(),
            "telegram_chat_id": self.tg_chat_var.get().strip(),
        })

    def _arm_live_notification(self, platform: str, title: str, link: str, schedule_time):
        """Start a background timer that fires a Telegram 'now live' message at the scheduled time."""
        import datetime
        tg_token   = self.tg_token_var.get().strip()
        tg_chat_id = self.tg_chat_var.get().strip()
        if not tg_token or not tg_chat_id:
            return
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        delay = (schedule_time - now_utc).total_seconds()
        if delay <= 0:
            return
        def fire():
            try:
                send_telegram(tg_token, tg_chat_id,
                    f"🔴 <b>{platform} — Jetzt live!</b>\n"
                    f"<b>{title}</b>\n"
                    f"{link}")
            except Exception:
                pass
        t = threading.Timer(delay, fire)
        t.daemon = True
        t.start()
        self._log(f"  Live-Benachrichtigung gesetzt für {schedule_time.strftime('%H:%M UTC')} ({platform})")

    def _test_telegram(self):
        try:
            send_telegram(self.tg_token_var.get().strip(),
                          self.tg_chat_var.get().strip(),
                          "✅ <b>Viral Clip Generator</b>\nTelegram notifications are working!")
            messagebox.showinfo("Telegram", "Test message sent successfully.")
        except Exception as e:
            messagebox.showerror("Telegram Error", str(e))
        os.environ["ANTHROPIC_API_KEY"] = self.key_var.get().strip()
        messagebox.showinfo("Saved", "Settings saved successfully.")

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.update_idletasks()

    def _set_status(self, msg: str, color=None):
        self.status_var.set(msg)
        self.status_dot.configure(text=f"● {msg}", fg=color or YELLOW)
        self.update_idletasks()

    def _start(self):
        if self.running:
            return

        api_key   = self.key_var.get().strip()
        url       = self.url_var.get().strip()
        upload_yt = self.yt_var.get()

        if not api_key:
            messagebox.showerror("Missing Key", "Enter your Anthropic API key in the Settings tab.")
            return
        if not url:
            messagebox.showerror("Missing URL", "Please enter a YouTube URL.")
            return
        if upload_yt and not SECRET_FILE.exists():
            messagebox.showerror("Missing File",
                f"client_secret.json not found:\n{SECRET_FILE}")
            return

        save_config({"api_key": api_key})
        os.environ["ANTHROPIC_API_KEY"] = api_key

        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self.running = True
        self.btn.configure(state="disabled", text="⏳  Running...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._show_tab(3)  # switch to log tab
        threading.Thread(target=self._run, args=(url, upload_yt), daemon=True).start()

    def _run(self, url: str, upload_yt: bool):
        try:
            self._set_status("Installing dependencies...")
            self._log("Installing dependencies if needed ...")
            install_deps_if_needed(upload_yt)

            import tempfile, re
            import yt_dlp, whisper, anthropic

            CLIP_DURATION = 60
            WHISPER_MODEL = "base"
            SUBTITLE_STYLE = (
                "FontName=Arial,FontSize=16,Bold=1,"
                "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                "BorderStyle=3,Outline=2,Shadow=0,Alignment=2,MarginV=40"
            )

            self._log(f"\nURL: {url}")

            with tempfile.TemporaryDirectory() as tmp_dir:
                self._log("\n[1/5] Downloading video ...")
                self._set_status("Downloading...")
                output_template = os.path.join(tmp_dir, "video.%(ext)s")

                video_title = url
                info_cmd = [
                    "yt-dlp", "--js-runtimes", "node", "--remote-components", "ejs:github",
                    "--cookies-from-browser", "firefox",
                    "--no-playlist", "--print", "title", url
                ]
                info_result = subprocess.run(info_cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
                if info_result.returncode == 0:
                    video_title = info_result.stdout.strip()

                dl_cmd = [
                    "yt-dlp", "--js-runtimes", "node", "--remote-components", "ejs:github",
                    "--cookies-from-browser", "firefox",
                    "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
                    "--merge-output-format", "mp4",
                    "--no-playlist", "-o", output_template, url
                ]
                dl_result = subprocess.run(dl_cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
                if dl_result.returncode != 0:
                    raise Exception(dl_result.stderr.split("\n")[-2] if dl_result.stderr else "Download failed")

                video_path = None
                for f in os.listdir(tmp_dir):
                    if f.startswith("video."):
                        video_path = os.path.join(tmp_dir, f)
                        break
                if not video_path:
                    raise FileNotFoundError("Download failed.")
                self._log(f"      Downloaded OK")
                self._log(f"      Title: {video_title}")

                result = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
                    capture_output=True, text=True, creationflags=NO_WINDOW
                )
                duration = float(json.loads(result.stdout)["format"]["duration"])
                self._log(f"      Duration: {duration:.0f}s ({duration/60:.1f} min)")

                self._log("\n[2/5] Extracting audio ...")
                self._set_status("Extracting audio...")
                audio_path = os.path.join(tmp_dir, "audio.mp3")
                subprocess.run(
                    ["ffmpeg", "-i", video_path, "-q:a", "0", "-map", "a",
                     audio_path, "-y", "-loglevel", "quiet"],
                    check=True, creationflags=NO_WINDOW
                )

                self._log("[3/5] Transcribing with Whisper ...")
                self._set_status("Transcribing...")
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._log(f"      Using device: {device.upper()}")
                model = whisper.load_model(WHISPER_MODEL, device=device)
                result = model.transcribe(audio_path, verbose=None, fp16=(device == "cuda"))
                segments = result["segments"]
                self._log(f"      Transcribed {len(segments)} segments")

                self._log("\n[4/5] Finding best viral moment with Claude ...")
                self._set_status("Analyzing with Claude...")
                lines = []
                for seg in segments:
                    m, s = divmod(int(seg["start"]), 60)
                    lines.append(f"[{m:02d}:{s:02d}] {seg['text'].strip()}")
                transcript_text = "\n".join(lines)

                max_start = duration - CLIP_DURATION
                client = anthropic.Anthropic()
                resp = client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=300,
                    messages=[{"role": "user", "content":
                        f"""You are a viral short-form video editor. Find the best {CLIP_DURATION}-second window for a viral clip.

Look for: strong hooks, high emotion, valuable insights, humor, memorable moments.

Video duration: {duration:.1f}s — max start time: {max_start:.1f}s

Transcript:
{transcript_text}

Reply ONLY with valid JSON, no markdown:
{{"start": <float>, "reason": "<one sentence>", "suggested_title": "<catchy short title>", "description": "<2-3 engaging sentences>", "tags": ["tag1","tag2","tag3","tag4","tag5","tag6","tag7","tag8","tag9","tag10"]}}"""}]
                )
                raw = resp.content[0].text.strip()
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)
                data = json.loads(raw)
                clip_start      = max(0.0, min(float(data["start"]), max_start))
                reason          = data.get("reason", "")
                suggested_title = data.get("suggested_title", video_title[:80])
                suggested_desc  = data.get("description", "")
                suggested_tags  = data.get("tags", ["shorts", "viral", "clip"])
                self._log(f"      Best moment at {clip_start:.1f}s")
                self._log(f"      Why: {reason}")
                self._log(f"      Title: {suggested_title}")

                self._log("\n[5/5] Cutting clip & burning subtitles ...")
                self._set_status("Rendering clip...")

                srt_path = os.path.join(tmp_dir, "subs.srt")
                clip_end = clip_start + CLIP_DURATION

                def fmt(t):
                    t = max(0.0, min(t, CLIP_DURATION))
                    h = int(t // 3600); m2 = int((t % 3600) // 60)
                    s2 = int(t % 60);   ms = int(round(t % 1 * 1000))
                    return f"{h:02d}:{m2:02d}:{s2:02d},{ms:03d}"

                with open(srt_path, "w", encoding="utf-8") as f:
                    idx = 1
                    for seg in segments:
                        if seg["end"] <= clip_start or seg["start"] >= clip_end:
                            continue
                        a = seg["start"] - clip_start
                        b = seg["end"] - clip_start
                        f.write(f"{idx}\n{fmt(a)} --> {fmt(b)}\n{seg['text'].strip()}\n\n")
                        idx += 1

                safe_url = re.sub(r"[^a-zA-Z0-9]", "_", url.split("/")[-1])[:30]
                output_path = os.path.join(
                    os.path.expanduser("~/Desktop/Youtube Uploads_04_26"),
                    f"viral_{safe_url}_{int(clip_start)}s.mp4"
                )

                safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
                crop_for_shorts = upload_yt and self.yt_shorts_var.get()
                vf = (f"crop=ih*9/16:ih,subtitles='{safe_srt}':force_style='{SUBTITLE_STYLE}'"
                      if crop_for_shorts else
                      f"subtitles='{safe_srt}':force_style='{SUBTITLE_STYLE}'")
                subprocess.run([
                    "ffmpeg", "-ss", str(clip_start), "-i", video_path,
                    "-t", str(CLIP_DURATION), "-vf", vf,
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                    "-c:a", "aac", "-b:a", "320k", "-y", output_path,
                ], check=True, capture_output=True, creationflags=NO_WINDOW)

                self._log(f"\n✓ Clip saved:")
                self._log(f"  {output_path}")

                final_title = self.yt_title_var.get().strip() or suggested_title
                results = []

                # Telegram helper
                tg_token   = self.tg_token_var.get().strip()
                tg_chat_id = self.tg_chat_var.get().strip()

                # Parse schedule times
                yt_schedule = None
                if upload_yt and self.yt_sched_var.get() and self.yt_time_var.get().strip():
                    try:
                        yt_schedule = parse_schedule_time(self.yt_time_var.get().strip())
                    except Exception:
                        self._log("  [!] Invalid YouTube schedule time — posting immediately")

                tt_schedule = None
                if self.tt_var.get() and self.tt_sched_var.get() and self.tt_time_var.get().strip():
                    try:
                        tt_schedule = parse_schedule_time(self.tt_time_var.get().strip())
                    except Exception:
                        self._log("  [!] Invalid TikTok schedule time — posting immediately")

                if upload_yt:
                    self._log("\n[+] Uploading to YouTube ...")
                    self._set_status("Uploading to YouTube...")
                    final_desc = f"{suggested_desc}\n\nOriginal video: {url}\n\n{chr(35)} {chr(35).join(suggested_tags)}"
                    yt_url = upload_to_youtube(output_path, title=final_title,
                                               description=final_desc, tags=suggested_tags,
                                               log_fn=self._log, schedule_time=yt_schedule)
                    self._log(f"  YouTube → {yt_url}")
                    label = f"YouTube (scheduled {yt_schedule.strftime('%H:%M UTC')}):\n{yt_url}" if yt_schedule else f"YouTube (private):\n{yt_url}"
                    results.append(label)
                    try:
                        if yt_schedule:
                            send_telegram(tg_token, tg_chat_id,
                                f"📅 <b>YouTube — Scheduled</b>\n"
                                f"<b>{final_title}</b>\n"
                                f"Wird veröffentlicht um {yt_schedule.strftime('%H:%M UTC')} ({yt_schedule.strftime('%Y-%m-%d')})\n"
                                f"{yt_url}")
                            self._arm_live_notification("YouTube", final_title, yt_url, yt_schedule)
                        else:
                            send_telegram(tg_token, tg_chat_id,
                                f"✅ <b>YouTube — Hochgeladen (privat)</b>\n"
                                f"<b>{final_title}</b>\n"
                                f"{yt_url}")
                    except Exception:
                        pass

                if self.tt_var.get():
                    self._log("\n[+] Uploading to TikTok ...")
                    self._set_status("Uploading to TikTok...")
                    tt_id = upload_to_tiktok(output_path, title=final_title,
                                             log_fn=self._log, schedule_time=tt_schedule)
                    self._log(f"  TikTok publish ID: {tt_id}")
                    label = f"TikTok (scheduled {tt_schedule.strftime('%H:%M UTC')}): {tt_id}" if tt_schedule else f"TikTok (private): {tt_id}"
                    results.append(label)
                    try:
                        if tt_schedule:
                            send_telegram(tg_token, tg_chat_id,
                                f"📅 <b>TikTok — Scheduled</b>\n"
                                f"<b>{final_title}</b>\n"
                                f"Wird veröffentlicht um {tt_schedule.strftime('%H:%M UTC')} ({tt_schedule.strftime('%Y-%m-%d')})\n"
                                f"Publish ID: {tt_id}")
                            self._arm_live_notification("TikTok", final_title, f"Publish ID: {tt_id}", tt_schedule)
                        else:
                            send_telegram(tg_token, tg_chat_id,
                                f"✅ <b>TikTok — Hochgeladen (privat)</b>\n"
                                f"<b>{final_title}</b>\n"
                                f"Publish ID: {tt_id}")
                    except Exception:
                        pass

            self._set_status("Done!", GREEN)
            self.status_dot.configure(fg=GREEN)
            msg = f"Clip saved:\n{os.path.basename(output_path)}"
            if results:
                msg += "\n\nUploaded to:\n" + "\n".join(results)
            messagebox.showinfo("✓ Done!", msg)

        except Exception as e:
            self._log(f"\n[ERROR] {e}")
            self._set_status("Error", RED)
            messagebox.showerror("Error", str(e))
        finally:
            self.running = False
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.btn.configure(state="normal", text="▶  Generate Viral Clip")


if __name__ == "__main__":
    app = App()
    app.mainloop()
