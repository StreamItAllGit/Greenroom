# @module scripts.run_stage2_e2e
# @purpose Four-drive Sandbox Two-Build Stage 2 E2E orchestrator for Greenroom v0.3.0 (Offline+Online Setup, Companion GUI, Release ExitCode=2).
# @inputs CLI flags (--sandbox-root, --version, --test-automation-dir, --releases-dir, --artifacts-dir) (@depends-on sandbox_ctl, run_sandbox_e2e, ctypes, http.server)
# @outputs Stage 2 JSON receipt, validated non-blank PNG screenshots, exebox-stream telemetry, and hostDiffCount=0 verification
"""Four-drive Sandbox Two-Build Stage 2 E2E runner for Greenroom v0.3.0."""

from __future__ import annotations

import argparse, base64, ctypes, hashlib, json, os, re, socket, struct, subprocess, sys, threading, time, urllib.error, urllib.request, uuid, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = DEV_ROOT.parent
FOUNDATION_TOOLS = Path(r"D:\Projects\SharedLibs\Foundation\v0.5.2\tools")
EXEBOX_STREAM_PS1 = Path.home() / ".gemini/config/skills/exebox-stream/scripts/start_exebox_vlc_stream.ps1"
if str(FOUNDATION_TOOLS) not in sys.path and FOUNDATION_TOOLS.is_dir():
    sys.path.insert(0, str(FOUNDATION_TOOLS))
import run_sandbox_e2e, sandbox_ctl  # noqa: E402

EXTRA_OS_ENV_KEYS = ("SystemDrive", "ProgramData", "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "CommonProgramFiles(x86)", "ALLUSERSPROFILE", "PUBLIC")


def _read_version() -> str: return m.group(1).strip() if (m := re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', (DEV_ROOT / "greenroom/__init__.py").read_text(encoding="utf-8"))) else "0.3.0"
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s: s.bind(("127.0.0.1", 0)); return int(s.getsockname()[1])


def get_thread_desktop_name() -> str:
    """Return active Windows station\\desktop truthfully (e.g. WinSta0\\Default or WinSta0\\exebox-*)."""
    if os.name != "nt":
        return "non-windows"
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    def _uoi(handle: int) -> str:
        buf, needed = ctypes.create_unicode_buffer(256), ctypes.c_uint(0)
        return buf.value if u32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)) and buf.value else ""
    return f"{_uoi(u32.GetProcessWindowStation()) or 'WinSta0'}\\{_uoi(u32.GetThreadDesktop(k32.GetCurrentThreadId())) or 'Default'}"


def preflight_exebox_stream(desktop_name: str, skip_viewer: bool = False) -> dict[str, object]:
    """Check UDP 5004 / VLC viewer state without ever launching Test Viewer inside exebox-*."""
    is_exebox = "exebox" in desktop_name.lower()
    ps_probe = "if (Get-NetUDPEndpoint -LocalPort 5004 -EA SilentlyContinue) { 'UDP5004' } elseif (Get-Process vlc -EA SilentlyContinue) { 'VLC' } else { 'NONE' }"
    viewer = (subprocess.run(["powershell", "-NoProfile", "-Command", ps_probe], capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL).stdout or "").strip()
    if viewer == "NONE" and not is_exebox and not skip_viewer:
        lnk = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Test Viewer.lnk"
        if lnk.is_file():
            subprocess.Popen(["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{lnk}'"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            viewer = "LAUNCHED_ON_DEFAULT"
    return {"desktop": desktop_name, "isExebox": is_exebox, "viewerState": viewer, "streamUrl": "udp://@127.0.0.1:5004"}


def start_exebox_streamer(snapshot_dir: Path, *, auto_port: int = 0, token: str = "", pid: int = 0, title: str = "") -> subprocess.Popen[str] | None:
    if not EXEBOX_STREAM_PS1.is_file() or os.name != "nt":
        return None
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EXEBOX_STREAM_PS1), "-SnapshotDir", str(snapshot_dir), "-UdpPort", "5004", "-Fps", "20"]
    for flag, val in (("-AutomationPort", str(auto_port) if auto_port > 0 else ""), ("-AutomationToken", token if auto_port > 0 else ""), ("-TargetPid", str(pid) if pid > 0 else ""), ("-WindowTitle", title)):
        if val:
            cmd.extend([flag, val])
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def stop_proc(proc: subprocess.Popen | None) -> None:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()


def build_sandbox_env(cfg: dict, *, offline_no_proxy: bool = False, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build sandbox environment using Foundation seam while preserving required Windows OS path env vars."""
    env = run_sandbox_e2e.build_sandbox_child_env(cfg)
    env.pop("APP_ISOLATED_TEST", None)
    for key in EXTRA_OS_ENV_KEYS:
        if os.environ.get(key):
            env[key] = os.environ[key]
    if offline_no_proxy:
        env.update({"NO_PROXY": "*", "PIP_NO_INDEX": "1"})
    if extra:
        env.update(extra)
    return env


def validate_png(path: Path) -> Path:
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 3000 and len(set(raw[:2048])) > 16, f"Blank/invalid PNG: {path} ({len(raw)} bytes)"
    return path


def _wait_http_ok(url: str, headers: dict[str, str] | None = None, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}, method="GET"), timeout=1.5).status == 200:
                return
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Endpoint did not become ready: {url}")


class AutomationClient:
    def __init__(self, port: int, token: str, snap_dir: Path | None = None) -> None:
        self.base, self.headers, self.snap_dir = f"http://127.0.0.1:{port}", {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, snap_dir

    def req(self, method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
        r = urllib.request.Request(f"{self.base}{path}", data=(json.dumps(body).encode("utf-8") if body is not None else None), headers=self.headers, method=method)
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, resp.read()

    def wait_ready(self) -> None: _wait_http_ok(f"{self.base}/v1/health", headers=self.headers)
    def tree(self) -> dict: return json.loads(self.req("GET", "/v1/tree")[1].decode("utf-8")).get("data") or {}

    def click(self, target_id: str) -> None:
        st, raw = self.req("POST", "/v1/pointer", {"action": "click", "targetId": target_id})
        if st != 200 or not json.loads(raw.decode("utf-8")).get("isPassed"):
            raise RuntimeError(f"Click failed on '{target_id}': {raw.decode('utf-8', errors='replace')}")

    def snapshot(self, out_png: Path) -> Path:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        try:
            out_png.write_bytes(self.req("GET", "/v1/snapshot.png")[1])
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            staged = (self.snap_dir or out_png.parent) / f"snap_{uuid.uuid4().hex[:8]}.png"
            staged.parent.mkdir(parents=True, exist_ok=True)
            st, raw = self.req("POST", "/v1/snapshot", {"filePath": str(staged), "snapshotFormat": "png"})
            if st != 200 or not staged.is_file():
                raise RuntimeError(f"POST /v1/snapshot failed: {raw.decode('utf-8', errors='replace')}") from exc
            out_png.write_bytes(staged.read_bytes())
        return validate_png(out_png)


def find_node(node: dict, node_id: str) -> dict | None:
    return node if node.get("id") == node_id else next((hit for ch in (node.get("children") or []) if (hit := find_node(ch, node_id))), None)


def _run_wizard_session(exe: Path, extra_args: list[str], env: dict[str, str], stream_tmp: Path, skip_stream: bool, expected_badge: str, pre_install_cb=None, done_png: Path | None = None) -> list[str]:
    port, tok = _free_port(), uuid.uuid4().hex
    proc = subprocess.Popen([str(exe), "--automation-port", str(port), "--automation-token", tok, "--no-launch", "--no-daemon-restart", *extra_args], env=env, stdin=subprocess.DEVNULL)
    stream_p = None if skip_stream else start_exebox_streamer(stream_tmp, auto_port=port, token=tok, pid=proc.pid)
    try:
        ac = AutomationClient(port, tok, snap_dir=stream_tmp)
        ac.wait_ready()
        badge = find_node(ac.tree(), "lblDeliveryBadge")
        assert badge and expected_badge in badge.get("text", "").upper(), f"Expected '{expected_badge}' badge, got {badge}"
        if pre_install_cb is not None:
            pre_install_cb(ac)
        ac.click("btnPrimary")
        statuses: list[str] = []
        deadline = time.monotonic() + 240.0
        while proc.poll() is None and time.monotonic() < deadline:
            try:
                tree = ac.tree()
                if (st := find_node(tree, "lblStatus")) and st.get("text") and (not statuses or statuses[-1] != st["text"]):
                    statuses.append(st["text"])
                if (btn := find_node(tree, "btnPrimary")) and btn.get("isEnabled") and btn.get("text", "").lower() in ("finish", "launch", "close"):
                    if btn.get("text", "").lower() == "launch" and (la := find_node(tree, "launchAfter") or find_node(tree, "chkLaunchAfter")):
                        ac.click(la["id"]); time.sleep(0.15)
                    if done_png is not None:
                        ac.snapshot(done_png)
                    ac.click("btnPrimary")
                    break
            except Exception as poll_exc:
                _ = str(poll_exc)
            time.sleep(0.5)
        assert proc.wait(timeout=30) == 0, f"Wizard {exe.name} exited {proc.returncode}"
        return statuses
    finally:
        stop_proc(stream_p); stop_proc(proc)


class _LocalPayloadHandler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None: self._serve(head_only=True)
    def do_GET(self) -> None: self._serve(head_only=False)
    def _serve(self, *, head_only: bool) -> None:
        srv, path = self.server, self.path.split("?", 1)[0].lstrip("/")
        if path == "missing.zip":
            srv.missing_hits += 1  # type: ignore[attr-defined]
        data = srv.tampered_bytes if path == "tampered.zip" else (srv.portable_bytes if path == srv.portable_name else None)  # type: ignore[attr-defined]
        if data is None:
            self.send_response(404); self.end_headers(); return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            s_str, _, e_str = rng[6:].partition("-")
            s_idx, e_idx = int(s_str or 0), int(e_str) if e_str else len(data) - 1
            chunk = data[s_idx : e_idx + 1]
            self.send_response(206); self.send_header("Content-Range", f"bytes {s_idx}-{s_idx + len(chunk) - 1}/{len(data)}")
        else:
            chunk = data; self.send_response(200)
        for k, v in (("Accept-Ranges", "bytes"), ("Content-Length", str(len(chunk))), ("Content-Type", "application/zip")):
            self.send_header(k, v)
        self.end_headers()
        if not head_only:
            self.wfile.write(chunk)
    def log_message(self, fmt: str, *args) -> None: return


class _CdpClient:
    """Minimal stdlib Chromium CDP WebSocket client for localhost UI evaluation & PNG capture."""
    def __init__(self, cdp_port: int) -> None:
        deadline, ws_url = time.monotonic() + 20.0, ""
        while time.monotonic() < deadline and not ws_url:
            try:
                pages = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list", timeout=2).read().decode("utf-8"))
                ws_url = next((p.get("webSocketDebuggerUrl", "") for p in pages if p.get("type") == "page" and "127.0.0.1" in p.get("url", "")), "")
            except Exception:
                time.sleep(0.25)
        if not ws_url:
            raise TimeoutError("Timed out finding Companion page in Edge CDP.")
        host_port, _, path = ws_url.removeprefix("ws://").partition("/")
        host, _, port_s = host_port.partition(":")
        self.sock = socket.create_connection((host, int(port_s)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(1024)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1; mid = self._id
        raw, mask = json.dumps({"id": mid, "method": method, "params": params or {}}).encode("utf-8"), os.urandom(4)
        ln, hdr = len(raw), bytearray([0x81])
        hdr.extend([0x80 | ln] if ln < 126 else ([0x80 | 126, *struct.pack("!H", ln)] if ln < 65536 else [0x80 | 127, *struct.pack("!Q", ln)]))
        self.sock.sendall(bytes(hdr) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(raw)))
        while True:
            b1, b2 = self.sock.recv(2)
            rlen = b2 & 0x7F
            rlen = struct.unpack("!H", self.sock.recv(2))[0] if rlen == 126 else (struct.unpack("!Q", self.sock.recv(8))[0] if rlen == 127 else rlen)
            data = bytearray()
            while len(data) < rlen:
                data.extend(self.sock.recv(rlen - len(data)))
            if (b1 & 0x0F) == 0x8:
                raise EOFError("WebSocket closed")
            if (b1 & 0x0F) in (0x1, 0x2) and (obj := json.loads(data.decode("utf-8", errors="replace"))).get("id") == mid:
                if "error" in obj:
                    raise RuntimeError(f"CDP {method} failed: {obj['error']}")
                return obj.get("result") or {}

    def eval_js(self, expr: str):
        return self.call("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True}).get("result", {}).get("value")

    def screenshot(self, out_png: Path) -> Path:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        out_png.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"}).get("data", "")))
        return validate_png(out_png)


def _find_browser() -> Path:
    for root in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")):
        for rel in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
            if root and (p := Path(root) / rel).is_file():
                return p
    raise FileNotFoundError("Edge or Chrome executable not found.")


def _run_offline_and_suite_wizard(exe: Path, cfg: dict, artifacts: Path, stream_tmp: Path, skip_stream: bool) -> Path:
    def _pre(ac: AutomationClient) -> None:
        ac.snapshot(artifacts / "setup_standalone.png")
        ac.click("chkFullSuiteMother"); time.sleep(0.35); ac.snapshot(artifacts / "setup_suite.png")
        ac.click("chkFullSuiteMother"); time.sleep(0.25)

    statuses = _run_wizard_session(exe, [], build_sandbox_env(cfg, offline_no_proxy=True), stream_tmp, skip_stream, "OFFLINE", _pre, artifacts / "setup_offline_complete.png")
    assert len(statuses) >= 2, f"Expected granular progressive status updates, got {statuses}"
    install_dir = Path(cfg["sandboxProfileDir"]) / "AppData/Local/Programs/Greenroom"
    log_file = install_dir / "logs/installer.log"
    assert log_file.is_file() and log_file.stat().st_size > 0, f"Missing installer.log at {log_file}"
    assert (install_dir / "app/.venv/Scripts/python.exe").is_file() and not (install_dir / "AGENTS.md").exists()
    return install_dir


def _run_online_setup_checks(exe: Path, portable_zip: Path, cfg: dict, artifacts: Path, stream_tmp: Path, skip_stream: bool) -> None:
    p_bytes = portable_zip.read_bytes()
    p_sha = hashlib.sha256(p_bytes).hexdigest()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LocalPayloadHandler)
    srv.portable_name, srv.portable_bytes, srv.tampered_bytes, srv.missing_hits = portable_zip.name, p_bytes, p_bytes[:-16] + b"TAMPERED_BYTES!!", 0  # type: ignore[attr-defined]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base_url = f"http://127.0.0.1:{srv.server_address[1]}"
        d_root = Path(f"{cfg['drives']['SANDBOX_D_DRIVE']}\\") if cfg["drives"].get("SANDBOX_D_DRIVE") else cfg["dBackingDir"]
        env = build_sandbox_env(cfg, extra={"GREENROOM_ALLOW_LOOPBACK_HTTP": "1"})
        _run_wizard_session(exe, ["--suite-dir", str(d_root / "Programs/StreamItAll/Mother"), "--payload-url", f"{base_url}/{portable_zip.name}", "--payload-sha256", p_sha], env, stream_tmp, skip_stream, "ONLINE", lambda ac: ac.snapshot(artifacts / "setup_online.png"))
        bundle_dir, staged_bundle = d_root / "Programs/Greenroom_Bundle", d_root / portable_zip.name
        staged_bundle.write_bytes(portable_zip.read_bytes())
        r_b = subprocess.run([str(exe), "--offline-bundle", str(staged_bundle), "--install-dir", str(bundle_dir), "--silent", "--no-daemon-restart"], env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        assert r_b.returncode == 0, f"--offline-bundle failed ({r_b.returncode}): {r_b.stdout}\n{r_b.stderr}"
        assert all(not (bundle_dir / doc_name).exists() for doc_name in ("README.md", "FEATURES.md", "RELEASE_NOTES.md", "CHANGELOG.md")), "Portable doc leaked into clean root"
        r_404 = subprocess.run([str(exe), "--install-dir", str(d_root / "Programs/Fail404"), "--payload-url", f"{base_url}/missing.zip", "--payload-sha256", p_sha, "--silent", "--no-daemon-restart"], env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        assert r_404.returncode != 0 and srv.missing_hits == 1, f"404 must fail with 0 retries (hits={srv.missing_hits})"  # type: ignore[attr-defined]
        r_sha = subprocess.run([str(exe), "--install-dir", str(d_root / "Programs/FailSha"), "--payload-url", f"{base_url}/tampered.zip", "--payload-sha256", p_sha, "--silent", "--no-daemon-restart"], env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
        assert r_sha.returncode != 0 and not (d_root / "Programs/FailSha/app").exists(), "Tampered SHA-256 must fail closed."
    finally:
        srv.shutdown()


def _run_companion_live_workflow(install_dir: Path, cfg: dict, artifacts: Path, stream_tmp: Path, skip_stream: bool) -> None:
    py_exe = install_dir / "app/.venv/Scripts/python.exe"
    comp_port, auto_port, cdp_port = _free_port(), _free_port(), _free_port()
    env = build_sandbox_env(cfg, extra={"GREENROOM_TEST_AUTOMATION": "1", "PYTHONPATH": str(install_dir / "app")})
    base_cmd = [str(py_exe), "-m", "greenroom.cli", "serve", "--data-dir", str(install_dir / "data"), "--log-dir", str(install_dir / "logs"), "--host", "127.0.0.1", "--no-tray"]
    srv_p = subprocess.Popen([*base_cmd, "--port", str(comp_port), "--automation-port", str(auto_port), "--automation-token", uuid.uuid4().hex], cwd=str(install_dir), env=env, stdin=subprocess.DEVNULL)
    edge_p, stream_p = None, None
    try:
        _wait_http_ok(f"http://127.0.0.1:{comp_port}/api/v1/health")
        edge_p = subprocess.Popen([str(_find_browser()), f"--app=http://127.0.0.1:{comp_port}", f"--user-data-dir={cfg['sandboxRoot'] / 'browser_profile'}", "--new-window", "--window-size=1280,840", f"--remote-debugging-port={cdp_port}"], stdin=subprocess.DEVNULL)
        stream_p = None if skip_stream else start_exebox_streamer(stream_tmp, title="Greenroom")
        cdp = _CdpClient(cdp_port)
        try:
            cdp.call("Page.enable"); time.sleep(1.5); cdp.screenshot(artifacts / "companion_onboarding.png")
            cdp.eval_js("(document.querySelector('#wizNextStepBtn, [data-onboarding-next]') || Array.from(document.querySelectorAll('dialog button,.modal-dialog button')).find(b => /next|continue/i.test(b.textContent)))?.click(); true;")
            time.sleep(0.4)
            cdp.eval_js("(document.querySelector('#wizCompleteBtn, [data-onboarding-finish]') || Array.from(document.querySelectorAll('dialog button,.modal-dialog button')).find(b => /finish|complete|save/i.test(b.textContent)))?.click(); true;")
            time.sleep(0.6)
            cdp.eval_js("document.querySelector('[data-view=\"settings\"]')?.click(); true;"); time.sleep(0.4)
            cdp.eval_js("(document.querySelector('[data-settings-tab=\"modules\"]') || Array.from(document.querySelectorAll('[data-settings-tab], .pill-tab, .pill-btn')).find(b => /modules/i.test(b.textContent)))?.click(); true;"); time.sleep(0.4)
            cdp.eval_js("document.querySelectorAll('input[type=\"checkbox\"][data-mod-domain], [data-settings-panel=\"modules\"] input[type=\"checkbox\"], input[type=\"checkbox\"][data-domain], input[type=\"checkbox\"][data-domain-key]').forEach(cb => { const k = (cb.dataset.modDomain || cb.dataset.domain || cb.dataset.domainKey || cb.name || cb.id || '').toLowerCase(); if ((k.includes('kitchen') || k.includes('studio')) && !cb.checked) cb.click(); }); true;"); time.sleep(0.3)
            cdp.eval_js("document.querySelector('#saveModulesDomainsBtn, [data-settings-panel=\"modules\"] [data-save-modules], #btnSaveModules')?.click(); true;"); time.sleep(0.4)
            cdp.screenshot(artifacts / "companion_settings_modules.png")
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{comp_port}/api/games", data=json.dumps({"title": "Chrono Sandbox E2E", "platform": "SNES"}).encode(), headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
            cdp.eval_js("document.querySelector('[data-view=\"catalog\"]')?.click(); true;"); time.sleep(0.7)
            cdp.screenshot(artifacts / "companion_catalog.png")
        finally:
            cdp.sock.close()
    finally:
        stop_proc(stream_p); stop_proc(edge_p); stop_proc(srv_p)
    comp_port2 = _free_port()
    srv2 = subprocess.Popen([*base_cmd, "--port", str(comp_port2)], cwd=str(install_dir), env=env, stdin=subprocess.DEVNULL)
    try:
        _wait_http_ok(f"http://127.0.0.1:{comp_port2}/api/v1/health")
        st2 = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{comp_port2}/api/config/settings", timeout=5).read().decode("utf-8"))
        assert st2["isOnboardingCompleted"] is True
        games2 = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{comp_port2}/api/catalog/games?q=Chrono+Sandbox+E2E", timeout=5).read().decode("utf-8"))
        assert any(g["title"] == "Chrono Sandbox E2E" for g in games2.get("rows", []))
    finally:
        stop_proc(srv2)


def _verify_release_exit_code_2(releases_dir: Path, portable_zip: Path, offline_install_dir: Path, cfg: dict) -> None:
    rel_online, rel_offline = next(releases_dir.glob("Greenroom-Setup-v*-win-x64.exe")), next(releases_dir.glob("Greenroom-Offline-Setup-v*-win-x64.exe"))
    env = build_sandbox_env(cfg)
    for exe in (rel_online, rel_offline):
        p = subprocess.run([str(exe), "--automation-port", "19999", "--silent"], env=env, capture_output=True, text=True, timeout=15, check=False, stdin=subprocess.DEVNULL)
        assert p.returncode == 2, f"Release {exe.name} must exit 2 on --automation-port, got {p.returncode}"
    rel_extract = cfg["dBackingDir"] / "ReleaseProbe"
    with zipfile.ZipFile(portable_zip, "r") as zf:
        zf.extractall(rel_extract)
    app_pkg = next(rel_extract.rglob("greenroom/__init__.py")).parent.parent
    assert not (app_pkg / "greenroom/_test_automation.py").exists()
    py_exe = offline_install_dir / "app/.venv/Scripts/python.exe"
    comp_env = build_sandbox_env(cfg, extra={"PYTHONPATH": f"{app_pkg}{os.pathsep}{app_pkg / 'lib'}"})
    p_comp = subprocess.run([str(py_exe), "-m", "greenroom.cli", "serve", "--data-dir", str(rel_extract / "data"), "--log-dir", str(rel_extract / "logs"), "--automation-port", "19999"], env=comp_env, capture_output=True, text=True, timeout=15, check=False, stdin=subprocess.DEVNULL)
    assert p_comp.returncode == 2, f"Release Companion must exit 2 on --automation-port, got {p_comp.returncode}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Greenroom v0.3.0 Two-Build 4-Drive Sandbox Stage 2 E2E Runner")
    ap.add_argument("--version", default=_read_version()); ap.add_argument("--test-automation-dir", default="")
    ap.add_argument("--releases-dir", default=""); ap.add_argument("--artifacts-dir", default="")
    ap.add_argument("--skip-stream", action="store_true"); ap.add_argument("--no-viewer", action="store_true")
    for fl in ("--sandbox-root", "--c-drive", "--d-drive", "--removable-drive", "--disc-drive", "--profile-dir", "--env-file"):
        ap.add_argument(fl, default=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    cfg = sandbox_ctl.resolve_sandbox_config(args)
    sandbox_ctl.init_sandbox(cfg)
    snap_name = f"greenroom-s2-{uuid.uuid4().hex[:6]}"
    sandbox_ctl.take_host_snapshot(cfg, name=snap_name)

    ver = args.version.lstrip("v")
    ta_dir = Path(args.test_automation_dir) if args.test_automation_dir else (DEV_ROOT / ".build_cache/test-automation" / f"v{ver}")
    rel_dir = Path(args.releases_dir) if args.releases_dir else (REPO_ROOT / "Releases" / f"v{ver}")
    artifacts = Path(args.artifacts_dir) if args.artifacts_dir else (cfg["sandboxRoot"] / "runs" / f"artifacts_v{ver}")
    artifacts.mkdir(parents=True, exist_ok=True)
    desk = preflight_exebox_stream(get_thread_desktop_name(), skip_viewer=args.no_viewer)

    install_dir = _run_offline_and_suite_wizard(next(ta_dir.glob("Greenroom-Offline-Setup-v*-win-x64.exe")), cfg, artifacts, cfg["sandboxRoot"] / "stream_tmp", args.skip_stream)
    ta_portable_zip = next(ta_dir.glob("Greenroom-v*-win-x64-Portable.zip"))
    _run_online_setup_checks(next(ta_dir.glob("Greenroom-Setup-v*-win-x64.exe")), ta_portable_zip, cfg, artifacts, cfg["sandboxRoot"] / "stream_tmp", args.skip_stream)
    _run_companion_live_workflow(install_dir, cfg, artifacts, cfg["sandboxRoot"] / "stream_tmp", args.skip_stream)
    _verify_release_exit_code_2(rel_dir, next(rel_dir.glob("Greenroom-v*-win-x64-Portable.zip")), install_dir, cfg)

    diff = sandbox_ctl.diff_host_snapshots(cfg, name=snap_name)
    _ign = (".tmp.driveupload", "desktop.ini", "thumbs.db")
    host_files = [p for k in ("addedFiles", "removedFiles", "modifiedFiles") for p in diff.get(k, []) if not any(t in p.lower() for t in _ign)]
    host_reg = sum(len(diff.get(k, [])) for k in ("addedRegistryEntries", "removedRegistryEntries", "modifiedRegistryEntries"))
    assert len(host_files) == 0 and host_reg == 0, f"Host mutation leaked outside sandbox: {diff}"
    receipt = {"version": ver, "desktop": desk, "hostDiffCount": len(host_files) + host_reg, "driveTempSkipped": diff["hostDiffCount"] - (len(host_files) + host_reg), "artifacts": sorted(p.name for p in artifacts.glob("*.png")), "verifiedAtUtc": sandbox_ctl.utc_now_iso()}
    sandbox_ctl.atomic_write_json(cfg["sandboxRoot"] / "runs" / f"greenroom-v{ver}-stage2-e2e.json", receipt)
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
