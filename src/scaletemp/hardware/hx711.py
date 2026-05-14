from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import csv
import os
import subprocess
import threading
import time
from typing import Deque


@dataclass
class RawSample:
    unix_time_ns: int
    sequence: int
    raw_adc: int
    status: str = "OK"


class HX711Sampler:
    """Thin Python wrapper around the C sampler.

    Bottom-layer acquisition remains in C. Python only supervises the process and
    forwards raw samples to processing/UI layers.
    """

    def __init__(self, command: list[str] | None = None, history_size: int = 6000) -> None:
        root = Path(__file__).resolve().parents[3]
        default_binary = root / "build" / "hx711_sampler"
        self.mode = os.getenv("SCALETEMP_SENSOR_MODE", "wiringpi")
        self.last_error = ""
        if command is None and self.mode == "sysfs":
            data_gpio = os.environ["SCALETEMP_DATA_GPIO"]
            sck_gpio = os.environ["SCALETEMP_SCK_GPIO"]
            command = [str(default_binary), "--sysfs", data_gpio, sck_gpio, os.getenv("SCALETEMP_GAIN_PULSES", "1")]
        elif command is None and self.mode == "mock":
            command = [str(default_binary), "--mock", os.getenv("SCALETEMP_MOCK_HZ", "80")]
        elif command is None:
            data_pin = os.getenv("SCALETEMP_DATA_PIN", "5")
            sck_pin = os.getenv("SCALETEMP_SCK_PIN", "1")
            command = [str(default_binary), "--wiringpi", data_pin, sck_pin, os.getenv("SCALETEMP_GAIN_PULSES", "1")]
        self.command = command
        self.mode = "sysfs" if "--sysfs" in self.command else "mock" if "--mock" in self.command else "wiringpi" if "--wiringpi" in self.command else self.mode
        self.history: Deque[RawSample] = deque(maxlen=history_size)
        self.process: subprocess.Popen[str] | None = None
        self.thread: threading.Thread | None = None
        self.error_thread: threading.Thread | None = None
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()
        self.error_thread = threading.Thread(target=self._stderr_reader, daemon=True)
        self.error_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _reader(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            if not self.running:
                break
            parts = line.strip().split(",")
            if len(parts) != 4:
                continue
            try:
                self.history.append(RawSample(int(parts[0]), int(parts[1]), int(parts[2]), parts[3]))
            except ValueError:
                continue

    def _stderr_reader(self) -> None:
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.last_error = line.strip()

    def snapshot(self, n: int = 200) -> list[RawSample]:
        return list(self.history)[-n:]

    def collect(self, duration_s: float) -> list[RawSample]:
        start = time.monotonic()
        baseline_seq = self.history[-1].sequence if self.history else -1
        while time.monotonic() - start < duration_s:
            time.sleep(0.02)
        return [s for s in self.history if s.sequence > baseline_seq]


def save_raw_csv(samples: list[RawSample], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["unix_time_ns", "sequence", "raw_adc", "status"])
        writer.writeheader()
        writer.writerows([s.__dict__ for s in samples])
