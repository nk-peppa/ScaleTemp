from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time

import numpy as np

from scaletemp.hardware.hx711 import HX711Sampler
from scaletemp.processing.calibration import CalibrationModel, fit_piecewise_overlapping, piecewise_predict
from scaletemp.processing.filters import ema_filter, is_stable


@dataclass
class ScaleReading:
    timestamp: float
    raw_adc: int
    filtered_raw: float
    grams: float | None
    stable: bool


class ScaleService:
    def __init__(self, data_dir: Path = Path("data"), sampler: HX711Sampler | None = None) -> None:
        self.data_dir = data_dir
        self.sampler = sampler or HX711Sampler()
        self.zero_offset = 0.0
        self.filter_alpha = 0.22
        self.filter_window_limit = 10000.0
        self.calibration_path = data_dir / "calibration" / "current_calibration.json"
        self.calibration = self._load_calibration()
        self.calibration_version = 0
        self.auto_zero_enabled = False
        self.auto_zero_candidate_since: float | None = None

    def _default_calibration(self) -> CalibrationModel:
        return CalibrationModel([], [], [0.0], 0)

    def _load_calibration(self) -> CalibrationModel:
        if self.calibration_path.exists():
            return CalibrationModel.load(self.calibration_path)
        return self._default_calibration()

    def is_calibrated(self) -> bool:
        return len(self.calibration.raw_points) > 0

    def _save_calibration(self) -> None:
        self.calibration.save(self.calibration_path)
        self.calibration_version += 1

    def start(self) -> None:
        self.sampler.start()

    def stop(self) -> None:
        self.sampler.stop()

    def set_filter_strength(self, strength: float) -> None:
        strength = min(max(strength, 0.0), 2.0)
        self.filter_alpha = 0.55 - 0.27 * strength
        self.filter_alpha = min(max(self.filter_alpha, 0.01), 0.55)

    def set_filter_window_limit(self, limit: float) -> None:
        self.filter_window_limit = min(max(float(limit), 0.0), 10000.0)

    def set_auto_zero(self, enabled: bool) -> bool:
        self.auto_zero_enabled = bool(enabled)
        self.auto_zero_candidate_since = None
        return self.auto_zero_enabled

    def toggle_auto_zero(self) -> bool:
        return self.set_auto_zero(not self.auto_zero_enabled)

    def _limited_ema_series(self, raw: np.ndarray) -> np.ndarray:
        if raw.size == 0:
            return np.asarray([])
        if self.filter_window_limit >= 10000.0:
            return ema_filter(raw, self.filter_alpha)
        out = np.empty_like(raw, dtype=float)
        for i in range(raw.size):
            window = raw[: i + 1]
            kept = window[np.abs(window - raw[i]) <= self.filter_window_limit]
            if kept.size == 0:
                kept = np.asarray([raw[i]], dtype=float)
            out[i] = ema_filter(kept, self.filter_alpha)[-1]
        return out

    def _current_filtered_raw(self, sample_count: int = 180) -> float | None:
        samples = self.sampler.snapshot(sample_count)
        if not samples:
            return None
        raw = np.asarray([s.raw_adc for s in samples], dtype=float)
        filtered = self._limited_ema_series(raw)
        return float(filtered[-1]) if filtered.size else None

    def tare(self) -> float:
        filtered_raw = self._current_filtered_raw(180)
        if filtered_raw is None:
            return self.zero_offset
        old_offset = self.zero_offset
        new_offset = filtered_raw
        delta = old_offset - new_offset

        raw_points = [float(raw + delta) for raw in self.calibration.raw_points]
        gram_points = list(self.calibration.gram_points)

        # Remove any previous 0 g anchors, then add exactly one new 0 g point at
        # the current filtered reading in the new corrected coordinate system.
        kept = [(raw, gram) for raw, gram in zip(raw_points, gram_points) if abs(float(gram)) > 1e-9]
        kept.append((0.0, 0.0))
        self.zero_offset = new_offset
        self.calibration = fit_piecewise_overlapping([raw for raw, _ in kept], [gram for _, gram in kept])
        self._save_calibration()
        return self.zero_offset

    def add_calibration_point(self, grams: float) -> CalibrationModel:
        filtered_raw = self._current_filtered_raw(180)
        if filtered_raw is None:
            raise ValueError("no samples available for calibration")
        corrected_raw = filtered_raw - self.zero_offset
        raw_points = list(self.calibration.raw_points) + [corrected_raw]
        gram_points = list(self.calibration.gram_points) + [grams]
        self.calibration = fit_piecewise_overlapping(raw_points, gram_points)
        self._save_calibration()
        return self.calibration

    def remove_calibration_point(self, index: int) -> CalibrationModel:
        raw_points = list(self.calibration.raw_points)
        gram_points = list(self.calibration.gram_points)
        if index < 0 or index >= len(raw_points):
            raise IndexError("calibration point index out of range")
        del raw_points[index]
        del gram_points[index]
        self.calibration = fit_piecewise_overlapping(raw_points, gram_points) if raw_points else self._default_calibration()
        self._save_calibration()
        return self.calibration

    def clear_calibration_points(self) -> CalibrationModel:
        self.calibration = self._default_calibration()
        self._save_calibration()
        return self.calibration

    def reading(self) -> ScaleReading:
        samples = self.sampler.snapshot(180)
        if not samples:
            return ScaleReading(time.time(), 0, 0.0, None, False)
        raw = np.asarray([s.raw_adc for s in samples], dtype=float)
        filtered = self._limited_ema_series(raw)
        grams = None
        if self.is_calibrated():
            grams = float(piecewise_predict(self.calibration, filtered[-1] - self.zero_offset))
        stable = is_stable(filtered[-60:], std_limit=90.0, slope_limit=2.0)

        if self.auto_zero_enabled and grams is not None:
            now = time.time()
            if abs(grams) <= 2.0:
                if self.auto_zero_candidate_since is None:
                    self.auto_zero_candidate_since = now
                elif now - self.auto_zero_candidate_since >= 3.0:
                    self.tare()
                    self.auto_zero_candidate_since = None
                    grams = 0.0
                    filtered = self._limited_ema_series(np.asarray([s.raw_adc for s in self.sampler.snapshot(180)], dtype=float))
            else:
                self.auto_zero_candidate_since = None

        return ScaleReading(time.time(), int(raw[-1]), float(filtered[-1]), grams, stable)

    def chart_payload(self) -> dict:
        samples = self.sampler.snapshot(6000)
        if samples:
            newest_s = samples[-1].unix_time_ns / 1e9
            samples = [s for s in samples if newest_s - (s.unix_time_ns / 1e9) <= 30.0]
        raw = np.asarray([s.raw_adc for s in samples], dtype=float)
        timestamps = [(s.unix_time_ns / 1e9) for s in samples]
        filtered = self._limited_ema_series(raw) if raw.size else np.asarray([])
        if self.is_calibrated():
            grams = [piecewise_predict(self.calibration, v - self.zero_offset) for v in filtered]
            conversion_x, conversion_y = self.conversion_curve(raw, filtered)
        else:
            grams = []
            conversion_x, conversion_y = [], []

        # Downsample chart payloads to keep the dashboard responsive while keeping
        # the processing window at 30 seconds.
        max_points = 720
        if len(timestamps) > max_points:
            idx = np.linspace(0, len(timestamps) - 1, max_points).astype(int)
            timestamps = [timestamps[i] for i in idx]
            raw = raw[idx]
            filtered = filtered[idx]
            if grams:
                grams = [grams[i] for i in idx]

        return {
            "t": timestamps,
            "raw": raw.tolist(),
            "filtered": filtered.tolist(),
            "grams": grams,
            "calibrated": self.is_calibrated(),
            "conversion_curve": {"raw": conversion_x, "grams": conversion_y},
            "calibration_points": self.calibration_points(),
            "calibration_version": self.calibration_version,
            "reading": self.reading().__dict__,
            "sensor": {"mode": self.sampler.mode, "command": self.sampler.command, "error": self.sampler.last_error},
            "auto_zero_enabled": self.auto_zero_enabled,
        }

    def calibration_points(self) -> list[dict[str, float]]:
        return [
            {"raw": float(raw + self.zero_offset), "corrected_raw": float(raw), "grams": float(grams)}
            for raw, grams in zip(self.calibration.raw_points, self.calibration.gram_points)
        ]

    def conversion_curve(self, raw: np.ndarray, filtered: np.ndarray) -> tuple[list[float], list[float]]:
        if not self.is_calibrated():
            return [], []
        point_candidates = [point + self.zero_offset for point in self.calibration.raw_points]
        if raw.size:
            point_candidates.extend(raw.tolist())
        if filtered.size:
            point_candidates.append(float(filtered[-1]))
        lo = float(min(point_candidates))
        hi = float(max(point_candidates))
        span = max(hi - lo, 1000.0)
        xs = np.linspace(lo - 0.08 * span, hi + 0.08 * span, 180)
        ys = [piecewise_predict(self.calibration, x - self.zero_offset) for x in xs]
        return xs.tolist(), ys

    def metadata(self) -> dict:
        return {
            "zero_offset": self.zero_offset,
            "filter_alpha": self.filter_alpha,
            "filter_window_limit": self.filter_window_limit,
            "calibration_degree": self.calibration.degree,
            "calibration_points": len(self.calibration.raw_points),
            "calibration_point_cards": self.calibration_points(),
            "calibration_version": self.calibration_version,
            "calibrated": self.is_calibrated(),
            "sensor_mode": self.sampler.mode,
            "sensor_command": self.sampler.command,
            "sensor_error": self.sampler.last_error,
            "auto_zero_enabled": self.auto_zero_enabled,
        }

    def save_metadata(self, path: Path, extra: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"scale": self.metadata(), **extra}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
