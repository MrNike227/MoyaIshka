# -*- coding: utf-8 -*-
"""Adaptive low-priority resource governor for MoyaIshka background work.

The 5 GiB setting is a soft ceiling, not a hard OS memory limit. Work is paused
when MoyaIshka/Ollama memory or system pressure gets close to the configured
budget. UI and interactive reply threads are not throttled by this module.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil is bundled in release builds
    psutil = None


@dataclass
class ResourceSnapshot:
    app_family_rss_mb: float | None
    available_ram_mb: float | None
    cpu_percent: float | None
    throttled: bool
    reason: str
    system_pressure: bool = False


class ResourceGovernor:
    def __init__(
        self,
        max_ram_mb: int = 5120,
        min_available_ram_mb: int = 8192,
        cpu_pause_percent: int = 85,
        *,
        sample_interval: float = 0.35,
        sleep_interval: float = 2.0,
    ) -> None:
        self.max_ram_mb = max(1024, min(int(max_ram_mb), 5120))
        self.min_available_ram_mb = max(1024, min(int(min_available_ram_mb), 16384))
        self.cpu_pause_percent = max(60, min(int(cpu_pause_percent), 98))
        self.sample_interval = max(0.1, float(sample_interval))
        self.sleep_interval = max(0.25, float(sleep_interval))
        self._foreground_busy = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last_snapshot = ResourceSnapshot(None, None, None, False, "Мониторинг нагрузки недоступен", False)
        self._last_cpu = 0.0
        self._monitor_thread: threading.Thread | None = None
        self._ollama_original_priorities: dict[int, int] = {}
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

    def configure(self, max_ram_mb: int | None = None, min_available_ram_mb: int | None = None, cpu_pause_percent: int | None = None) -> None:
        if max_ram_mb is not None:
            self.max_ram_mb = max(1024, min(int(max_ram_mb), 5120))
        if min_available_ram_mb is not None:
            self.min_available_ram_mb = max(1024, min(int(min_available_ram_mb), 16384))
        if cpu_pause_percent is not None:
            self.cpu_pause_percent = max(60, min(int(cpu_pause_percent), 98))

    def set_foreground_busy(self, busy: bool) -> None:
        if busy:
            self._foreground_busy.set()
        else:
            self._foreground_busy.clear()

    def start_monitor(self, interval_seconds: float = 1.5) -> None:
        """Continuously watch system pressure and politely lower Ollama priority if needed."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop.clear()
        interval = max(0.75, min(float(interval_seconds), 10.0))
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True, name="MoyaResourceGovernor")
        self._monitor_thread.start()

    def _monitor_loop(self, interval: float) -> None:
        while not self._stop.is_set():
            try:
                self.snapshot()
            except Exception:
                pass
            self._stop.wait(interval)

    def _adjust_ollama_priority(self, system_pressure: bool) -> None:
        """On Windows, make Ollama yield CPU under pressure and restore its prior class later."""
        if os.name != "nt" or psutil is None:
            return
        below_normal = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)
        try:
            processes = list(psutil.process_iter(["pid", "name"]))
        except Exception:
            return
        active_pids: set[int] = set()
        for proc in processes:
            try:
                name = str(proc.info.get("name") or "").lower()
                if "ollama" not in name and "llama_server" not in name:
                    continue
                pid = int(proc.info.get("pid") or proc.pid)
                active_pids.add(pid)
                current = int(proc.nice())
                if system_pressure and current != below_normal:
                    self._ollama_original_priorities.setdefault(pid, current)
                    proc.nice(below_normal)
                elif not system_pressure and pid in self._ollama_original_priorities:
                    original = self._ollama_original_priorities.pop(pid)
                    if current == below_normal:
                        proc.nice(original)
            except Exception:
                continue
        # Forget processes that exited while pressure was active.
        for pid in list(self._ollama_original_priorities):
            if pid not in active_pids:
                self._ollama_original_priorities.pop(pid, None)

    def stop(self) -> None:
        self._stop.set()
        self._adjust_ollama_priority(False)

    @staticmethod
    def set_current_thread_low_priority() -> None:
        """Lower the current worker thread priority without lowering the UI thread."""
        try:
            if os.name == "nt":
                import ctypes
                THREAD_PRIORITY_LOWEST = -2
                ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), THREAD_PRIORITY_LOWEST)
            elif hasattr(os, "nice"):
                os.nice(5)
        except Exception:
            pass

    def _memory_usage_mb(self) -> tuple[float | None, float | None]:
        if psutil is None:
            return None, None
        try:
            proc = psutil.Process(os.getpid())
            rss = float(proc.memory_info().rss)
            seen = {proc.pid}
            # Count Ollama children too, because its model memory is part of the assistant workload.
            candidates = list(proc.children(recursive=True))
            try:
                for candidate in psutil.process_iter(["pid", "name"]):
                    name = str(candidate.info.get("name") or "").lower()
                    if "ollama" in name or "llama_server" in name:
                        candidates.append(candidate)
            except Exception:
                pass
            for child in candidates:
                try:
                    if child.pid not in seen and child.is_running():
                        seen.add(child.pid)
                        rss += float(child.memory_info().rss)
                except Exception:
                    continue
            available = float(psutil.virtual_memory().available)
            return rss / (1024 * 1024), available / (1024 * 1024)
        except Exception:
            return None, None

    def snapshot(self) -> ResourceSnapshot:
        rss_mb, available_mb = self._memory_usage_mb()
        cpu = None
        if psutil is not None:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
            except Exception:
                cpu = None
        reasons: list[str] = []
        system_pressure = False
        if self._foreground_busy.is_set():
            reasons.append("выполняется пользовательский запрос")
        if rss_mb is not None and rss_mb >= self.max_ram_mb * 0.90:
            reasons.append(f"ИИ-процессы используют {rss_mb:.0f} МБ из бюджета {self.max_ram_mb} МБ")
            system_pressure = True
        if available_mb is not None and available_mb < self.min_available_ram_mb:
            reasons.append(f"свободно мало ОЗУ ({available_mb:.0f} МБ)")
            system_pressure = True
        if cpu is not None and cpu >= self.cpu_pause_percent:
            reasons.append(f"загрузка процессора {cpu:.0f}%")
            system_pressure = True
        self._adjust_ollama_priority(system_pressure)
        snap = ResourceSnapshot(rss_mb, available_mb, cpu, bool(reasons), "; ".join(reasons), system_pressure)
        with self._lock:
            self._last_snapshot = snap
        return snap

    @property
    def last_snapshot(self) -> ResourceSnapshot:
        with self._lock:
            return self._last_snapshot

    def wait_for_background_slot(self, cancel_event: threading.Event | None = None, max_wait_seconds: int = 900) -> bool:
        """Wait politely under pressure; return False when cancelled or wait expires."""
        self.set_current_thread_low_priority()
        started = time.monotonic()
        stable_samples = 0
        while not self._stop.is_set():
            if cancel_event and cancel_event.is_set():
                return False
            snap = self.snapshot()
            if not snap.throttled:
                stable_samples += 1
                if stable_samples >= 2:
                    return True
            else:
                stable_samples = 0
            if time.monotonic() - started > max_wait_seconds:
                return False
            # During a foreground request, poll less frequently to avoid fighting for CPU.
            time.sleep(self.sleep_interval if not snap.throttled else min(5.0, self.sleep_interval * 2.0))
        return False

    def background_call(self, function, *args: Any, **kwargs: Any):
        if not self.wait_for_background_slot():
            return None
        return function(*args, **kwargs)
