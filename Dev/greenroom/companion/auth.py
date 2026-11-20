# @module greenroom.companion.auth
# @purpose Authentication, HMAC session tokens, public path guards, and LAN/Tailscale URL discovery.
# @inputs Starlette Request, GreenroomConfig, signing secret bytes, target port
# @outputs Session tokens, authorization verdicts, PUBLIC_EXACT_PATHS, network URLs
"""Authentication, HMAC session tokens, and LAN/Tailscale URL discovery for Greenroom Companion."""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from starlette.requests import Request

from greenroom.core.config import PROJECT_ROOT, GreenroomConfig

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "gr_companion_session"
SESSION_MAX_AGE_SECONDS = 365 * 24 * 3600

PUBLIC_EXACT_PATHS = {
    "/",
    "/api/health",
    "/api/status",
    "/api/scheduler/status",
    "/api/scheduler/defer",
    "/api/v1/health",
    "/api/v1/status",
    "/api/v1/scheduler/status",
    "/api/system/shutdown",
    "/api/auth/status",
    "/api/auth/unlock",
    "/api/auth/logout",
    "/manifest.webmanifest",
    "/sw.js",
    "/favicon.ico",
}

PUBLIC_PREFIXES = (
    "/static/",
)


def get_or_create_signing_secret(
    secret_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> bytes:
    """Load or generate a persistent 32-byte HMAC secret for device session cookies."""
    base_dir = data_dir or (PROJECT_ROOT / "data")
    path = secret_path or (base_dir / "companion_secret.key")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if len(raw) >= 32:
            return raw.encode("utf-8")

    generated = secrets.token_hex(32)
    path.write_text(generated, encoding="utf-8")
    return generated.encode("utf-8")


def create_session_token(secret: bytes, device_id: str = "device", issued_at: Optional[int] = None) -> str:
    """Create an HMAC-SHA256 signed session token bound to an issuance timestamp and device ID."""
    ts = issued_at if issued_at is not None else int(time.time())
    safe_device = "".join(ch for ch in (device_id or "device") if ch.isalnum() or ch in "-_")[:48] or "device"
    payload = f"{ts}:{safe_device}"
    sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token: Optional[str], secret: bytes, max_age_seconds: int = SESSION_MAX_AGE_SECONDS) -> bool:
    """Verify HMAC signature and expiry of a companion session token."""
    if not token:
        return False
    parts = token.strip().split(":")
    if len(parts) != 3:
        return False
    ts_str, device_id, provided_sig = parts
    try:
        issued_at = int(ts_str)
    except ValueError:
        return False

    now = int(time.time())
    if issued_at > now + 300 or (now - issued_at) > max_age_seconds:
        return False

    payload = f"{ts_str}:{device_id}"
    expected_sig = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_sig, expected_sig)


def verify_pin(candidate_pin: str, configured_pin: str) -> bool:
    """Constant-time PIN comparison."""
    if not configured_pin:
        return True
    return hmac.compare_digest(candidate_pin.strip().encode("utf-8"), configured_pin.strip().encode("utf-8"))


def is_localhost_request(request: Request) -> bool:
    """Return True if the HTTP client connected directly from loopback on this PC."""
    if not request.client:
        return False
    host = (request.client.host or "").strip().lower()
    return host in ("127.0.0.1", "::1", "localhost")


def extract_valid_non_cookie_token(request: Request, secret: bytes) -> Optional[str]:
    """Return a validated session token from Authorization: Bearer or ?token= if present."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
        if verify_session_token(bearer_token, secret):
            return bearer_token

    query_token = (request.query_params.get("token") or "").strip()
    if query_token and verify_session_token(query_token, secret):
        return query_token

    return None


def is_request_authorized(request: Request, config: GreenroomConfig, secret: bytes) -> bool:
    """Evaluate whether a request satisfies optional PIN / localhost trust / remembered token."""
    configured_pin = (config.companion.pin or "").strip()
    if not configured_pin:
        return True

    if config.companion.trust_localhost and is_localhost_request(request):
        return True

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if verify_session_token(cookie_token, secret):
        return True

    if extract_valid_non_cookie_token(request, secret) is not None:
        return True

    return False


_NETWORK_CACHE: Dict[int, Tuple[float, Dict[str, Any]]] = {}
_NETWORK_TTL_SECONDS = 60.0


def _detect_tailscale_https_urls(target_port: int) -> List[str]:
    """Check `tailscale serve status --json` for active HTTPS reverse-proxy endpoints targeting `target_port`."""
    ts_exe = Path(r"C:\Program Files\Tailscale\tailscale.exe")
    cmd = [str(ts_exe) if ts_exe.exists() else "tailscale", "serve", "status", "--json"]
    port_suffix = f":{target_port}"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=False)
        if proc.returncode == 0 and (proc.stdout or "").strip().startswith("{"):
            data = json.loads(proc.stdout)
            web_map = data.get("Web") or {}
            urls: List[str] = []
            for host_port, web_cfg in web_map.items():
                handlers = (web_cfg or {}).get("Handlers") or {} if isinstance(web_cfg, dict) else {}
                matches_port = any(
                    str((h_cfg or {}).get("Proxy") or "").rstrip("/").endswith(port_suffix)
                    for h_cfg in handlers.values()
                    if isinstance(h_cfg, dict)
                )
                if not matches_port:
                    continue
                if host_port.endswith(":443"):
                    urls.append(f"https://{host_port[:-4]}")
                else:
                    urls.append(f"https://{host_port}")
            return sorted(set(urls))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("Tailscale HTTPS status probe skipped: %s", exc)
    return []


def detect_network_urls(port: int, force_refresh: bool = False) -> Dict[str, Any]:
    """Discover localhost, Wi-Fi/LAN, and Tailscale HTTP/HTTPS URLs for phone PWA pairing."""
    now = time.monotonic()
    cached = _NETWORK_CACHE.get(port)
    if not force_refresh and cached is not None and (now - cached[0]) < _NETWORK_TTL_SECONDS:
        return cached[1]

    hostname = socket.gethostname()
    lan_ips: List[str] = []
    tailscale_ips: List[str] = []
    tailscale_net = ipaddress.ip_network("100.64.0.0/10")

    discovered: set[str] = set()
    try:
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            ip_str = info[4][0]
            if ip_str and not ip_str.startswith("127."):
                discovered.add(ip_str)
    except OSError as exc:
        logger.debug("Hostname IPv4 enumeration warning: %s", exc)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            primary_ip = probe.getsockname()[0]
            if primary_ip and not primary_ip.startswith("127."):
                discovered.add(primary_ip)
    except OSError as exc:
        logger.debug("Primary interface probe skipped: %s", exc)

    for ip_str in sorted(discovered):
        try:
            addr = ipaddress.ip_address(ip_str)
            if addr in tailscale_net:
                tailscale_ips.append(ip_str)
            elif addr.is_private:
                lan_ips.append(ip_str)
        except ValueError:
            continue

    lan_urls = [f"http://{ip}:{port}" for ip in lan_ips]
    tailscale_urls = [f"http://{ip}:{port}" for ip in tailscale_ips]
    https_urls = _detect_tailscale_https_urls(port)
    primary_phone_url = (
        https_urls[0]
        if https_urls
        else (tailscale_urls[0] if tailscale_urls else (lan_urls[0] if lan_urls else f"http://{hostname}:{port}"))
    )

    result = {
        "hostname": hostname,
        "port": port,
        "localhost_url": f"http://127.0.0.1:{port}",
        "hostname_url": f"http://{hostname.lower()}:{port}",
        "lan_urls": lan_urls,
        "tailscale_urls": tailscale_urls,
        "https_urls": https_urls,
        "recommended_phone_url": primary_phone_url,
    }
    _NETWORK_CACHE[port] = (now, result)
    return result
