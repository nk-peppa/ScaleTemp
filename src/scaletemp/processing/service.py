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
    grams: float
    stable: bool


class ScaleService:
    def __init__(self, data_dir: Path = Path("data"), sampler: HX711Sampler | None = None) -> None:
        self.data_dir = data_dir
        self.sampler = sampler or HX711Sampler()
        self.zero_offset = 0.0
        self.filter_alpha = 0.22
        self.calibration_path = data_dir / "calibration" / "current_calibration.json"
        self.calibration = self._load_calibration()

    def _load_calibration(self) -> CalibrationModel:
        if self.calibration_path.exists():
            return CalibrationModel.load(self.calibration_path)
        return CalibrationModel([0.0, 100000.0], [0.0, 1000.0], [0.01, 0.0], 1)

    def start(self) -> None:
        self.sampler.start()

    def stop(self) -> None:
        self.sampler.stop()

    def set_filter_strength(self, strength: float) -> None:
        # Extended range: 0.0 is fastest response, 2.0 is strongest smoothing.
        strength = min(max(strength, 0.0), 2.0)
        self.filter_alpha = 0.55 - 0.27 * strength
        self.filter_alpha = min(max(self.filter_alpha, 0.01), 0.55)

    def tare(self) -> float:
        samples = self.sampler.snapshot(120)
        if samples:
            self.zero_offset = float(np.mean([s.raw_adc for s in samples]))
        return self.zero_offset

    def add_calibration_point(self, grams: float) -> CalibrationModel:
        samples = self.sampler.snapshot(160)
        raw = float(np.mean([s.raw_adc for s in samples])) if samples else 0.0
        corrected_raw = raw - self.zero_offset
        raw_points = list(self.calibration.raw_points) + [corrected_raw]
        gram_points = list(self.calibration.gram_points) + [grams]
        self.calibration = fit_piecewise_overlapping(raw_points, gram_points)
        self.calibration.save(self.calibration_path)
        return self.calibration

    def reading(self) -> ScaleReading:
        samples = self.sampler.snapshot(180)
        if not samples:
            return ScaleReading(time.time(), 0, 0.0, 0.0, False)
        raw = np.asarray([s.raw_adc for s in samples], dtype=float)
        filtered = ema_filter(raw, self.filter_alpha)
        corrected = filtered[-1] - self.zero_offset
        grams = piecewise_predict(self.calibration, corrected)
        stable = is_stable(filtered[-60:], std_limit=90.0, slope_limit=2.0)
        return ScaleReading(time.time(), int(raw[-1]), float(filtered[-1]), float(grams), stable)

    def chart_payload(self) -> dict:
        samples = self.sampler.snapshot(6000)
        if samples:
            newest_s = samples[-1].unix_time_ns / 1e9
            samples = [s for s in samples if newest_s - (s.unix_time_ns / 1e9) <= 30.0]
        raw = np.asarray([s.raw_adc for s in samples], dtype=float)
        timestamps = [(s.unix_time_ns / 1e9) for s in samples]
        filtered = ema_filter(raw, self.filter_alpha) if raw.size else np.asarray([])
        grams = [piecewise_predict(self.calibration, v - self.zero_offset) for v in filtered]
        conversion_x, conversion_y = self.conversion_curve(raw, filtered)
        return {
            "t": timestamps,
            "raw": raw.tolist(),
            "filtered": filtered.tolist(),
            "grams": grams,
            "conversion_curve": {"raw": conversion_x, "grams": conversion_y},
            "calibration_points": self.calibration_points(),
            "reading": self.reading().__dict__,
        }

    def calibration_points(self) -> list[dict[str, float]]:
        return [
            {"raw": float(raw + self.zero_offset), "corrected_raw": float(raw), "grams": float(grams)}
            for raw, grams in zip(self.calibration.raw_points, self.calibration.gram_points)
        ]

    def conversion_curve(self, raw: np.ndarray, filtered: np.ndarray) -> tuple[list[float], list[float]]:
        point_candidates = [point + self.zero_offset for point in self.calibration.raw_points]
        if raw.size:
            point_candidates.extend(raw.tolist())
        if filtered.size:
            point_candidates.append(float(filtered[-1]))
        if not point_candidates:
            point_candidates = [0.0, 100000.0]
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
            "calibration_degree": self.calibration.degree,
            "calibration_points": len(self.calibration.raw_points),
            "calibration_point_cards": self.calibration_points(),
        }

    def save_metadata(self, path: Path, extra: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"scale": self.metadata(), **extra}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
