# @module tests.test_v021_burnin_and_discord_hardening
# @purpose Isolated regression tests for Greenroom v0.2.1 Mother burn-in parity, JobLock contention, and Discord webhook hardening.
# @inputs Temporary directories, mocked HTTP responses, and SQLite Database instances under APP_ISOLATED_TEST=1
# @outputs Verification of T1-T5 deliverables (log-path parity, lock contention/recovery, 429 retry, system_runs audit, dual webhook sync)

import io
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.error

import pytest

from greenroom.cli_collectors import cmd_tick
from greenroom.collectors.discord import DiscordNotifier
from greenroom.core.config import (
    CompanionConfig,
    GreenroomConfig,
    apply_runtime_overrides,
    get_log_dir,
)
from greenroom.core.db import Database
from greenroom.core.notifications import default_notification_settings, notify_event
from greenroom.core.notifications_channels import (
    clear_recent_notifications,
    get_recent_notifications,
    send_discord_webhook,
)
from greenroom.core.runs import JobLock, ProcessLockError


def test_t1_data_dir_and_log_dir_parity(monkeypatch, tmp_path: Path) -> None:
    """Verify get_log_dir() and CompanionConfig.resolve_log_path() never fall back to app\\logs under --data-dir/--log-dir."""
    data_root = tmp_path / "mother_instance" / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    # Note: sibling logs/ does NOT exist yet; get_log_dir() must still resolve to sibling logs/ rather than app/logs
    monkeypatch.delenv("LOG_DIR", raising=False)
    monkeypatch.delenv("GREENROOM_LOG_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data_root))
    monkeypatch.setenv("GREENROOM_DATA_DIR", str(data_root))

    resolved_log_dir = get_log_dir()
    assert resolved_log_dir == (tmp_path / "mother_instance" / "logs").resolve()

    comp = CompanionConfig()
    assert comp.resolve_log_path() == (resolved_log_dir / "companion.log")

    # Explicit --log-dir override takes precedence for both greenroom.log and companion.log
    custom_logs = tmp_path / "explicit_logs"
    apply_runtime_overrides(data_dir=str(data_root), log_dir=str(custom_logs), isolated_test=True)
    assert get_log_dir() == custom_logs.resolve()
    assert comp.resolve_log_path() == (custom_logs.resolve() / "companion.log")


def test_t2_job_lock_contention_and_stale_recovery(tmp_path: Path) -> None:
    """Verify concurrent JobLock contention raises ProcessLockError, unacquired release() is safe, and stale locks recover."""
    lock_path = tmp_path / "data" / "greenroom.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    holder = JobLock(lock_path=lock_path, timeout_seconds=1, operation="tick")
    assert holder.acquire() is True
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["operation"] == "tick"
    assert payload["ownerAppId"] == "greenroom"

    contender = JobLock(lock_path=lock_path, timeout_seconds=0, operation="verify_hashes")
    with pytest.raises(ProcessLockError):
        contender.acquire()

    # Unacquired contender.release() must NOT remove holder's active lockfile
    contender.release()
    assert lock_path.exists()
    holder.release()
    assert not lock_path.exists()

    # Stale dead-PID + expired-TTL envelope must be reclaimed atomically
    stale_envelope = {
        "schemaVersion": 1,
        "lockType": "job-runner",
        "suiteId": "mother",
        "ownerAppId": "greenroom",
        "pid": 999999,
        "processStartTimeUtc": "2020-01-01T00:00:00Z",
        "hostname": payload["hostname"],
        "targetPath": str(lock_path),
        "operation": "verify_hashes",
        "acquiredAtUtc": "2020-01-01T00:00:00Z",
        "heartbeatAtUtc": "2020-01-01T00:00:00Z",
        "expiresAtUtc": "2020-01-01T01:00:00Z",
    }
    lock_path.write_text(json.dumps(stale_envelope), encoding="utf-8")

    reclaimer = JobLock(lock_path=lock_path, timeout_seconds=1, operation="tick")
    assert reclaimer.acquire() is True
    reclaimed = json.loads(lock_path.read_text(encoding="utf-8"))
    assert reclaimed["operation"] == "tick"
    reclaimer.release()


def test_t2_cmd_tick_skips_cleanly_when_locked(monkeypatch, tmp_path: Path) -> None:
    """Verify cmd_tick catches ProcessLockError cleanly without crashing when another job holds greenroom.lock."""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    apply_runtime_overrides(data_dir=str(data_dir), log_dir=str(log_dir), isolated_test=True)

    lock_path = data_dir / "greenroom.lock"
    holder = JobLock(lock_path=lock_path, timeout_seconds=1, operation="verify_hashes")
    assert holder.acquire() is True
    try:
        logged = []
        monkeypatch.setattr("greenroom.cli_collectors._log", lambda lvl, job, msg: logged.append((lvl, job, msg)))
        monkeypatch.setattr("greenroom.core.runs.time.sleep", lambda _s: None)
        cmd_tick(SimpleNamespace(force=False))
        assert any(lvl == "WARN" and job == "tick" and "Skipping tick" in msg for lvl, job, msg in logged)
    finally:
        holder.release()


class _FakeResp:
    def __init__(self, status: int = 204):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def test_t3_discord_webhook_429_retry_and_system_runs_failure_audit(tmp_path: Path) -> None:
    """Verify 429 Retry-After backoff succeeds on retry and exhausted failures persist sanitized system_runs rows."""
    clear_recent_notifications()
    db = Database(tmp_path / "t3.db")
    db.init_schema()
    secret_url = "https://discord.com/api/webhooks/123456789012345678/SuperSecretWebhookTokenXYZ"

    # Case 1: 429 with Retry-After header -> sleep -> 204 success
    sleeps: list[float] = []
    attempts = {"count": 0}

    def _opener_429_then_ok(req, timeout=8):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.HTTPError(
                secret_url, 429, "Too Many Requests", {"Retry-After": "0.25"}, io.BytesIO(b'{"retry_after": 0.25}')
            )
        return _FakeResp(204)

    ok = send_discord_webhook(
        secret_url, "Burn-In", "Recovered after 429", level="info",
        db=db, max_retries=2, sleep_fn=sleeps.append, urlopen_fn=_opener_429_then_ok, allow_test_dispatch=True,
    )
    assert ok is True
    assert sleeps == [0.25]
    assert attempts["count"] == 2

    # Case 2: Exhausted 429s -> returns False and logs sanitized failure to system_runs
    sleeps_fail: list[float] = []

    def _opener_always_429(req, timeout=8):
        raise urllib.error.HTTPError(
            secret_url, 429, f"Rate limited on {secret_url}", {}, io.BytesIO(b'{"retry_after": 0.5}')
        )

    ok_fail = send_discord_webhook(
        secret_url, "Burn-In", "Exhausted 429", level="error",
        db=db, max_retries=2, sleep_fn=sleeps_fail.append, urlopen_fn=_opener_always_429, allow_test_dispatch=True,
    )
    assert ok_fail is False
    assert sleeps_fail == [0.5, 0.5]

    with db.session() as conn:
        row = conn.execute(
            "SELECT job_name, started_at, started_at_utc, finished_at, completed_at_utc, status, error_message "
            "FROM system_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["job_name"] == "discord_webhook"
    assert row["status"] == "failed"
    assert row["started_at"] and row["started_at_utc"]
    assert row["finished_at"] and row["completed_at_utc"]
    assert "SuperSecretWebhookTokenXYZ" not in (row["error_message"] or "")
    assert "<redacted>" in (row["error_message"] or "")
    assert get_recent_notifications()[-1]["delivered"] is False


def test_t4_t5_dual_webhook_unification_and_active_state_contract(monkeypatch, tmp_path: Path) -> None:
    """Verify discord.webhook_url <-> notifications.webhook_url unification and active.state discordPost parity."""
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    sample_url = "https://discord.com/api/webhooks/111222333/UnifiedToken"

    cfg_from_discord = GreenroomConfig.model_validate({"discord": {"webhook_url": sample_url}})
    assert cfg_from_discord.notifications.webhook_url == sample_url
    assert default_notification_settings(cfg_from_discord)["discord_webhook_url"] == sample_url

    cfg_from_notif = GreenroomConfig.model_validate({"notifications": {"webhook_url": sample_url}})
    assert cfg_from_notif.discord.webhook_url == sample_url
    notifier = DiscordNotifier(config=cfg_from_notif)
    assert notifier.is_configured is True
    assert notifier.webhook_url == sample_url

    # notify_event under APP_ISOLATED_TEST=1 simulates cleanly without network calls
    db = Database(tmp_path / "t4.db")
    db.init_schema()
    cfg_from_notif.notifications.discord_enabled = True
    res = notify_event("task_failure", "Test", "Body", level="error", db=db, config=cfg_from_notif)
    assert res["channels"].get("discord_webhook") is True

    active_state_path = Path(__file__).resolve().parent.parent.parent / "active.state"
    state_data = json.loads(active_state_path.read_text(encoding="utf-8"))
    dp = state_data.get("discordPost", {})
    assert dp.get("isEnabled") is True
    assert dp.get("secretEnvKey") == "DISCORD_WEBHOOK_FOUNDATION"
    assert dp.get("autoPostOnRelease") is True
