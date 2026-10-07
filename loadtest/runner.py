"""Запуск k6 с логированием каждого шага."""

import asyncio
import json
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.logger import setup_logger

logger = setup_logger("updo.runner")

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(exist_ok=True)
METRICS_FILE = DATA_DIR / "metrics.jsonl"


def _find_k6() -> str | None:
    logger.debug(f"🔍 Ищу k6 в PATH={os.environ.get('PATH', '')[:200]}")
    for path in os.environ.get("PATH", "").split(":"):
        candidate = Path(path) / "k6"
        if candidate.exists() and os.access(candidate, os.X_OK):
            logger.info(f"✅ k6 найден: {candidate}")
            return str(candidate)
    for p in ("/usr/bin/k6", "/usr/local/bin/k6", "/bin/k6"):
        if Path(p).exists():
            logger.info(f"✅ k6 найден fallback: {p}")
            return p
    logger.error("❌ k6 не найден")
    return None


class LoadTestRunner:
    def __init__(self, script_path: str = "/app/loadtest/script.js"):
        self.script_path = script_path
        self.process: subprocess.Popen | None = None
        self.running = False
        self.listeners: list[Callable] = []
        self._task: asyncio.Task | None = None

    def add_listener(self, callback: Callable) -> None:
        self.listeners.append(callback)
        logger.debug(f"📎 Добавлен listener, всего: {len(self.listeners)}")

    async def start(self) -> bool:
        if self.running:
            logger.warning("⚠️ Тест уже идёт")
            return False

        k6_path = _find_k6()
        if k6_path is None:
            await self._emit({"STOP": "k6 binary not found"})
            return False

        if not Path(self.script_path).exists():
            logger.error(f"❌ Скрипт не найден: {self.script_path}")
            await self._emit({"STOP": f"script not found: {self.script_path}"})
            return False

        METRICS_FILE.write_text("")
        self.running = True
        logger.info(f"🚀 Старт k6: {k6_path} run {self.script_path}")

        try:
            self.process = subprocess.Popen(
                [
                    k6_path, "run",
                    "--out", "json=/tmp/k6_stream.json",
                    "--no-color",
                    self.script_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            logger.info(f"✅ k6 PID={self.process.pid}")
        except Exception as e:
            logger.exception(f"❌ Не смог запустить k6: {e}")
            self.running = False
            await self._emit({"STOP": f"k6 start failed: {e}"})
            return False

        self._task = asyncio.create_task(self._watch_output())
        return True

    async def _watch_output(self) -> None:
        buffer = {
            "requests": 0, "errors": 0, "durations": [],
            "vu": 0, "last_emit": datetime.utcnow(),
        }
        line_count = 0

        try:
            assert self.process and self.process.stdout
            for line in self.process.stdout:
                line_count += 1
                line = line.strip()
                if not line:
                    continue

                if line_count <= 20:
                    logger.debug(f"k6[{line_count}]: {line[:200]}")

                if not line.startswith("{"):
                    continue

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
                elif metric == "http_req_failed" and value == 1:
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
                    logger.info(
                        f"📊 VU={metrics['vu']} req={metrics['requests']} "
                        f"err={metrics['errors']} p95={metrics.get('p95', 0):.0f}ms"
                    )
                    await self._emit(metrics)
                    buffer["last_emit"] = now

                    if buffer["requests"] >= 30:
                        err_rate = buffer["errors"] / buffer["requests"]
                        if err_rate > 0.02:
                            logger.warning(f"🛑 Стоп: errors={err_rate:.1%}")
                            await self._emit({
                                "STOP": f"error rate {err_rate:.1%}",
                                "vu": buffer["vu"],
                                "requests": buffer["requests"],
                            })
                            await self.stop()
                            return
                        if metrics.get("p95", 0) > 1500:
                            logger.warning(f"🛑 Стоп: p95={metrics['p95']:.0f}ms")
                            await self._emit({
                                "STOP": f"p95 {metrics['p95']:.0f}ms",
                                "vu": buffer["vu"],
                                "requests": buffer["requests"],
                            })
                            await self.stop()
                            return
        except Exception as e:
            logger.exception(f"❌ runner watch: {e}")
            await self._emit({"STOP": f"runner error: {e}"})

        logger.info("🏁 k6 завершился")
        self.running = False
        await self._emit({"STOP": "completed"})

    def _aggregate(self, buffer: dict) -> dict:
        durations = sorted(buffer["durations"])
        n = len(durations)
        if n == 0:
            return {
                "ts": datetime.utcnow().isoformat(),
                "vu": buffer["vu"], "requests": buffer["requests"],
                "errors": buffer["errors"], "error_rate": 0,
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
        logger.debug(f"→ emit: {metrics}")
        for cb in self.listeners:
            try:
                await cb(metrics)
            except Exception as e:
                logger.exception(f"listener error: {e}")

    async def stop(self) -> None:
        logger.info("⏹ Останавливаю k6...")
        if self.process and self.process.poll() is None:
            try:
                self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("k6 не остановился — kill")
                self.process.kill()
        self.running = False
        logger.info("⏹ k6 остановлен")
