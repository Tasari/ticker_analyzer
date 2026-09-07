from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ticker_analyzer.ranking import DEFAULT_RANKING_PATH, load_ranking, save_ranking
from ticker_analyzer.ranking.quality import build_ranking_quality_report


def refresh_large_cap_ranking(
    output_path: Path = DEFAULT_RANKING_PATH,
    *,
    limit: int = 1000,
    market_limit: int = 100,
    workers: int = 3,
    timeout: int = 3600,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    restart_running: bool = False,
) -> tuple[bool, str, dict[str, Any]]:
    if os.getenv("APP_MODE", "local").strip().lower() == "production" and os.getenv(
        "ALLOW_RANKING_REFRESH", ""
    ).strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "Ranking refresh is disabled in production.", {}
    project_root = Path(__file__).resolve().parents[2]
    resolved_output = output_path if output_path.is_absolute() else project_root / output_path
    refresh_path = resolved_output.with_suffix(".refresh.json")
    lock_path = resolved_output.with_suffix(".refresh.lock")
    log_path = resolved_output.with_suffix(".refresh.log")
    cancel_path = resolved_output.with_suffix(".refresh.cancel")
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    if restart_running:
        if not request_running_refresh_stop(lock_path, cancel_path):
            return False, "The running ranking update could not be stopped safely.", {"restart_failed": True}
        refresh_path.unlink(missing_ok=True)
    if not acquire_refresh_lock(lock_path):
        return False, "A ranking update is already running.", {}
    cancel_path.unlink(missing_ok=True)

    command = [
        sys.executable,
        "-m",
        "scripts.build_large_cap_ranking",
        "--limit",
        str(limit),
        "--workers",
        str(workers),
        "--market-limit",
        str(market_limit),
        "--ranges",
        "3Y",
        "--output",
        str(refresh_path),
        "--public-fallback",
        "--retry-insufficient",
    ]
    if restart_running:
        command.append("--restart")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        previous_payload = load_ranking(resolved_output)
        with log_path.open("w", encoding="utf-8") as log_handle:
            process = subprocess.Popen(
                command,
                cwd=project_root,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
            write_refresh_lock(lock_path, worker_pid=process.pid)
            started_at = time.monotonic()
            last_progress: tuple[int, int, int] | None = None
            last_checkpoint: tuple[int, int] | None = None
            while process.poll() is None:
                if cancel_path.exists():
                    terminate_refresh_process(process)
                    return False, "Ranking update stopped; a fresh run is starting.", {"cancelled": True}
                if time.monotonic() - started_at > timeout:
                    terminate_refresh_process(process)
                    return False, "Ranking update timed out; the checkpoint was preserved.", {}
                if progress_callback and refresh_path.exists():
                    stat = refresh_path.stat()
                    checkpoint = (stat.st_mtime_ns, stat.st_size)
                    if checkpoint != last_checkpoint:
                        progress = load_ranking(refresh_path).get("metadata", {})
                        marker = (
                            int(progress.get("processed", progress.get("analyzed", 0)) or 0),
                            int(progress.get("failed", 0) or 0),
                            int(progress.get("requested", 0) or 0),
                        )
                        if marker != last_progress:
                            progress_callback(progress)
                            last_progress = marker
                        last_checkpoint = checkpoint
                time.sleep(0.5)
        if process.returncode != 0:
            detail = read_log_tail(log_path) or "Unknown generator error"
            checkpoint_metadata = load_ranking(refresh_path).get("metadata", {}) if refresh_path.exists() else {}
            return False, f"Ranking update failed: {detail}", checkpoint_metadata
        payload = load_ranking(refresh_path)
        metadata = payload.get("metadata", {})
        if progress_callback:
            progress_callback(metadata)
        if not ranking_refresh_is_complete(payload, expected_limit=limit):
            return False, "Ranking update stopped before all companies were processed; the checkpoint was preserved.", metadata
        payload["metadata"]["quality_report"] = build_ranking_quality_report(payload, previous_payload)
        save_ranking(payload, refresh_path)
        refresh_path.replace(resolved_output)
        return True, (
            f"Ranking updated: {metadata.get('scored', 0)} scored, "
            f"{metadata.get('insufficient_data', 0)} insufficient data, "
            f"{metadata.get('failed', 0)} failed."
        ), metadata
    finally:
        if process is not None and process.poll() is None:
            terminate_refresh_process(process)
        log_path.unlink(missing_ok=True)
        cancel_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def acquire_refresh_lock(lock_path: Path) -> bool:
    for attempt in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                json.dump({"owner_pid": os.getpid(), "worker_pid": None}, handle)
            return True
        except FileExistsError:
            if attempt == 0 and refresh_lock_is_stale(lock_path):
                lock_path.unlink(missing_ok=True)
                continue
            return False
    return False


def refresh_lock_is_stale(lock_path: Path) -> bool:
    details = read_refresh_lock(lock_path)
    if details is None:
        return True
    pid = details.get("owner_pid")
    if not isinstance(pid, int):
        return True
    return not process_is_running(pid)


def read_refresh_lock(lock_path: Path) -> dict[str, Any] | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
        if raw.startswith("{"):
            value = json.loads(raw)
            return value if isinstance(value, dict) else None
        return {"owner_pid": int(raw), "worker_pid": None}
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def write_refresh_lock(lock_path: Path, *, worker_pid: int | None) -> None:
    lock_path.write_text(
        json.dumps({"owner_pid": os.getpid(), "worker_pid": worker_pid}),
        encoding="utf-8",
    )


def process_is_running(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def ranking_refresh_is_running(output_path: Path = DEFAULT_RANKING_PATH) -> bool:
    project_root = Path(__file__).resolve().parents[2]
    resolved_output = output_path if output_path.is_absolute() else project_root / output_path
    lock_path = resolved_output.with_suffix(".refresh.lock")
    if not lock_path.exists():
        return False
    if refresh_lock_is_stale(lock_path):
        lock_path.unlink(missing_ok=True)
        return False
    return True


def request_running_refresh_stop(lock_path: Path, cancel_path: Path, timeout: float = 20.0) -> bool:
    if not lock_path.exists() or refresh_lock_is_stale(lock_path):
        lock_path.unlink(missing_ok=True)
        return True
    cancel_path.write_text("restart", encoding="utf-8")
    deadline = time.monotonic() + timeout
    while lock_path.exists() and time.monotonic() < deadline:
        if refresh_lock_is_stale(lock_path):
            lock_path.unlink(missing_ok=True)
            break
        time.sleep(0.1)
    if not lock_path.exists():
        return True

    details = read_refresh_lock(lock_path) or {}
    worker_pid = details.get("worker_pid")
    if not isinstance(worker_pid, int):
        worker_pid = find_ranking_worker(lock_path.with_suffix(".json"))
    if isinstance(worker_pid, int) and worker_pid != os.getpid() and process_is_running(worker_pid):
        try:
            os.kill(worker_pid, signal.SIGTERM)
        except OSError:
            return False
        for _ in range(50):
            if not process_is_running(worker_pid):
                for _ in range(50):
                    if not lock_path.exists():
                        return True
                    time.sleep(0.1)
                return False
            time.sleep(0.1)
    return False


def find_ranking_worker(refresh_path: Path) -> int | None:
    """Find a legacy generator by its exact module and output path on Linux."""
    proc_root = Path("/proc")
    if os.name == "nt" or not proc_root.is_dir():
        return None
    expected_output = refresh_path.resolve()
    for process_dir in proc_root.iterdir():
        if not process_dir.name.isdigit():
            continue
        try:
            arguments = [
                item.decode(errors="replace")
                for item in (process_dir / "cmdline").read_bytes().split(b"\0")
                if item
            ]
            module_index = arguments.index("scripts.build_large_cap_ranking")
            output_index = arguments.index("--output")
            candidate_output = Path(arguments[output_index + 1]).resolve()
        except (OSError, ValueError, IndexError):
            continue
        if module_index > 0 and candidate_output == expected_output:
            pid = int(process_dir.name)
            if pid != os.getpid():
                return pid
    return None


def terminate_refresh_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def read_log_tail(path: Path, max_bytes: int = 16_384) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            lines = handle.read().decode("utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""


def ranking_refresh_is_complete(payload: dict[str, Any], *, expected_limit: int) -> bool:
    metadata = payload.get("metadata", {})
    if not metadata.get("complete"):
        return False
    requested = int(metadata.get("requested", expected_limit) or 0)
    processed = len(payload.get("companies", [])) + len(payload.get("errors", []))
    return requested > 0 and processed >= requested
