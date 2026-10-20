"""Read Codex account limits through the documented app-server RPC.

No inference requests, login changes, reset credits, or direct credential reads.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass


class QuotaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Window:
    remaining: float
    duration_mins: int
    resets_at: int | None

    def expired(self, now: float) -> bool:
        return self.resets_at is not None and now >= self.resets_at


@dataclass(frozen=True)
class Snapshot:
    short: Window | None
    week: Window | None
    captured_at: float
    bucket: str = "codex"

    def to_dict(self) -> dict:
        return asdict(self)


def numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_window(raw) -> Window | None:
    if not isinstance(raw, dict) or not numeric(raw.get("usedPercent")):
        return None
    duration = raw.get("windowDurationMins")
    if not numeric(duration) or duration <= 0:
        return None
    reset = raw.get("resetsAt")
    return Window(
        remaining=max(0.0, min(100.0, 100.0 - raw["usedPercent"])),
        duration_mins=int(duration),
        resets_at=int(reset) if numeric(reset) and reset > 0 else None,
    )


def parse_limits(result: dict, now: float | None = None) -> Snapshot:
    buckets = result.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    if not isinstance(bucket, dict):
        legacy = result.get("rateLimits")
        if isinstance(legacy, dict) and legacy.get("limitId") in (None, "codex"):
            bucket = legacy
    if not isinstance(bucket, dict):
        raise QuotaError("未返回 Codex 通用额度")
    windows = [parse_window(bucket.get(name)) for name in ("primary", "secondary")]
    short = next((w for w in windows if w and w.duration_mins == 300), None)
    week = next((w for w in windows if w and w.duration_mins == 10080), None)
    if not short and not week:
        raise QuotaError("未返回可识别的 5 小时或周额度")
    return Snapshot(short, week, time.time() if now is None else now)


def mood(snapshot: Snapshot | None, now: float | None = None, stale_after=180) -> str:
    now = time.time() if now is None else now
    if snapshot is None or now - snapshot.captured_at > stale_after:
        return "unknown"
    windows = [snapshot.short, snapshot.week]
    valid = [w.remaining for w in windows if w is not None and not w.expired(now)]
    if not valid:
        return "unknown"
    remaining = min(valid)
    if remaining <= 0:
        return "empty"
    if remaining <= 25:
        return "low"
    if remaining > 60:
        return "full"
    return "steady"


def discover_codex() -> Path:
    override = os.environ.get("PET_ENERGY_CODEX")
    if override:
        path = Path(override)
        if not path.is_file() or path.suffix.lower() != ".exe":
            raise QuotaError("PET_ENERGY_CODEX 应指向有效的 codex.exe")
        return path
    candidates: list[Path] = []
    bundled = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    if bundled.is_dir():
        candidates.extend(sorted(bundled.glob("*/codex.exe"), key=lambda p:p.stat().st_mtime, reverse=True))
    npm = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex/node_modules/@openai"
    candidates.extend(npm.glob("codex-win32-*/vendor/*/bin/codex.exe"))
    on_path = shutil.which("codex.exe")
    if on_path:
        candidates.append(Path(on_path))
    for path in candidates:
        if path.is_file():
            return path
    raise QuotaError("未找到 Codex，请先安装并登录桌面应用或 CLI")


class RpcClient:
    def __init__(self, executable: Path, timeout=25):
        self.timeout = timeout
        self.next_id = 1
        self.events: queue.Queue = queue.Queue()
        self.process = subprocess.Popen(
            [str(executable), "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=Path(__file__).resolve().parent,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            self.events.put(None)

    def _send(self, message):
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise QuotaError("额度查询进程连接已断开") from exc

    def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        message = {"id":request_id, "method":method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                event = self.events.get(timeout=max(0.01, deadline-time.monotonic()))
            except queue.Empty as exc:
                raise QuotaError("额度查询超时，请稍后重试") from exc
            if event is None:
                raise QuotaError("额度查询进程已结束")
            if event.get("id") == request_id and ("result" in event or "error" in event):
                if event.get("error"):
                    # Do not surface upstream response bodies or credential-bearing URLs.
                    raise QuotaError("额度查询未成功，请确认 Codex 登录和网络连接")
                return event.get("result")
            if "id" in event and "method" in event:
                self._send({"id":event["id"], "error":{"code":-32601,"message":"Unsupported request"}})
            if time.monotonic() >= deadline:
                raise QuotaError("额度查询超时，请稍后重试")

    def initialize(self):
        self.request("initialize", {"clientInfo":{"name":"pet_energy","title":"Pet Energy","version":"0.1.0"}})
        self._send({"method":"initialized", "params":{}})

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        self.reader.join(timeout=1)
        self.process.stdout.close()


def read_snapshot(executable: Path | None = None, timeout=25) -> Snapshot:
    client = RpcClient(executable or discover_codex(), timeout)
    try:
        client.initialize()
        return parse_limits(client.request("account/rateLimits/read"))
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="只读查询 Codex 额度")
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    try:
        print(json.dumps({"ok":True, **read_snapshot(timeout=args.timeout).to_dict()}, ensure_ascii=False))
        return 0
    except (QuotaError, OSError) as exc:
        print(json.dumps({"ok":False, "error":str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
