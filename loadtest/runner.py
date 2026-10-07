"""Запуск k6 и парсинг метрик в реальном времени."""

import asyncio
import json
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(exist_ok=True)
METRICS_FILE = DATA_DIR / "metrics.jsonl"


class LoadTestRunner:
    """Один активный k6 процесс. Только для одного теста за раз."""

    def __init__(self, script_path: str = "/app/loadtest/script.js"):
        self.script_path = script_path
        self.process: subprocess.Popen | None = None
        self.running = False
        self.listeners: list[Callable] = []
        self._task: asyncio.Task | None = None

    def add_listener(self, callback: Callable) -> None:
        self.listeners.append(callback)

    async def start(self) -> bool:
        if self.running:
            return False

        METRICS_FILE.write_text("")
        self.running = True

        try:
            self.process = subprocess.Popen(
                ["k6", "run", "--out", "json=/tmp/k6_stream.json", self.script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
            )
        except FileNotFoundError:
            self.running = False
            await self._emit({"STOP": "k6 binary not found"})
            return False

        self._task = asyncio.create_task(self._watch_output())
        return True

    async def _watch_output(self) -> None:
        buffer = {
            "requests": 0,
            "errors": 0,
            "durations": [],
            "vu": 0,
            "last_emit": datetime.utcnow(),
        }

        try:
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") != "Point":
                    continue

                metric = event.get("metric")
                value = event.get("data", {}).get("value", 0)

                if metric == "http_reqs":
                    buffer["requests"] += 1
                elif metric == "http_req_failed":
                    if value == 1:
                        buffer["errors"] += 1
                elif metric == "http_req_duration":
                    buffer["durations"].append(value)
                    if len(buffer["durations"]) > 5000:
                        buffer["durations"] = buffer["durations"][-5000:]
                elif metric == "vus":
                    buffer["vu"] = int(value)

                now = datetime.utcnow()
                if (now - buffer["last_emit"]).total_seconds() >= 5:
                    metrics = self._aggregate(buffer)
                    await self._emit(metrics)
                    buffer["last_emit"] = now

                    # Автостоп
                    if buffer["requests"] >= 30:
                        err_rate = buffer["errors"] / buffer["requests"]
                        if err_rate > 0.02:
                            await self._emit({
                                "STOP": f"error rate {err_rate:.1%}",
                                "vu": buffer["vu"],
                                "requests": buffer["requests"],
                            })
                            await self.stop()
                            return
                        if metrics.get("p95", 0) > 1500:
                            await self._emit({
                                "STOP": f"p95 {metrics['p95']:.0f}ms",
                                "vu": buffer["vu"],
                                "requests": buffer["requests"],
                            })
                            await self.stop()
                            return
        except Exception as e:
            await self._emit({"STOP": f"runner error: {e}"})

        self.running = False
        await self._emit({"STOP": "completed"})

    def _aggregate(self, buffer: dict) -> dict:
        durations = sorted(buffer["durations"])
        n = len(durations)
        if n == 0:
            return {
                "ts": datetime.utcnow().isoformat(),
                "vu": buffer["vu"],
                "requests": buffer["requests"],
                "errors": buffer["errors"],
                "error_rate": 0,
                "avg": 0, "p50": 0, "p95": 0, "p99": 0,
            }
        return {
            "ts": datetime.utcnow().isoformat(),
            "vu": buffer["vu"],
            "requests": buffer["requests"],
            "errors": buffer["errors"],
            "error_rate": buffer["errors"] / max(1, buffer["requests"]),
            "avg": sum(durations) / n,
            "p50": durations[int(n * 0.50)],
            "p95": durations[int(n * 0.95)],
            "p99": durations[min(n - 1, int(n * 0.99))],
        }

    async def _emit(self, metrics: dict) -> None:
        with open(METRICS_FILE, "a") as f:
            f.write(json.dumps(metrics, ensure_ascii=False) + "\n")
        for cb in self.listeners:
            try:
                await cb(metrics)
            except Exception:
                pass

    async def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.running = False
