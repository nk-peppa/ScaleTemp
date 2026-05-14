from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
import json

import numpy as np


@dataclass
class CalibrationModel:
    """Polynomial calibration from raw ADC counts to grams."""

    raw_points: list[float]
    gram_points: list[float]
    coefficients: list[float]
    degree: int

    def predict(self, raw: float | np.ndarray) -> float | np.ndarray:
        return np.polyval(np.asarray(self.coefficients), raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CalibrationModel":
        return cls(**json.loads(path.read_text(encoding="utf-8")))


def _finite_unique_points(raw: Iterable[float], grams: Iterable[float]) -> tuple[np.ndarray, np.ndarray]:
    x0 = np.asarray(list(raw), dtype=float)
    y0 = np.asarray(list(grams), dtype=float)
    if x0.size != y0.size or x0.size == 0:
        raise ValueError("raw and gram calibration points must have the same non-zero length")
    finite = np.isfinite(x0) & np.isfinite(y0)
    x0 = x0[finite]
    y0 = y0[finite]
    if x0.size == 0:
        raise ValueError("calibration points must contain finite values")

    order = np.argsort(x0)
    x0 = x0[order]
    y0 = y0[order]
    unique_x: list[float] = []
    unique_y: list[float] = []
    i = 0
    while i < x0.size:
        same = np.isclose(x0, x0[i], rtol=0.0, atol=1e-6)
        idx = np.where(same & (np.arange(x0.size) >= i))[0]
        idx = idx[np.isclose(x0[idx], x0[i], rtol=0.0, atol=1e-6)]
        if idx.size == 0:
            idx = np.asarray([i])
        unique_x.append(float(np.mean(x0[idx])))
        unique_y.append(float(np.mean(y0[idx])))
        i = int(idx[-1]) + 1
    return np.asarray(unique_x, dtype=float), np.asarray(unique_y, dtype=float)


def _safe_polyfit(x: np.ndarray, y: np.ndarray, degree: int) -> list[float]:
    degree = min(degree, max(0, x.size - 1))
    if x.size == 1 or degree <= 0:
        return [float(y[0])]
    try:
        return np.polyfit(x, y, degree).tolist()
    except (np.linalg.LinAlgError, ValueError, FloatingPointError):
        if degree > 1:
            return _safe_polyfit(x, y, degree - 1)
        dx = float(x[-1] - x[0])
        slope = float((y[-1] - y[0]) / dx) if abs(dx) > 1e-12 else 0.0
        intercept = float(np.mean(y) - slope * np.mean(x))
        return [slope, intercept]


def fit_piecewise_overlapping(raw: Iterable[float], grams: Iterable[float]) -> CalibrationModel:
    """Fit the required overlapping polynomial calibration.

    For n < 4, the degree is n-1. For n >= 4, every adjacent four distinct raw
    points can be fitted with a cubic and predictions over overlapping spans are
    averaged by :func:`piecewise_predict`. Duplicate/invalid raw readings are
    merged so calibration cannot crash when the platform returns unchanged data.
    """

    x, y = _finite_unique_points(raw, grams)
    degree = min(3, max(0, x.size - 1))
    coeffs = _safe_polyfit(x, y, degree)
    return CalibrationModel(x.tolist(), y.tolist(), coeffs, min(degree, len(coeffs) - 1))


def piecewise_predict(model: CalibrationModel, raw_value: float) -> float:
    x, y = _finite_unique_points(model.raw_points, model.gram_points)
    if x.size < 4:
        return float(np.polyval(model.coefficients, raw_value))
    predictions: list[float] = []
    for start in range(0, x.size - 3):
        xs = x[start : start + 4]
        ys = y[start : start + 4]
        lo, hi = xs.min(), xs.max()
        if lo <= raw_value <= hi or not predictions:
            predictions.append(float(np.polyval(_safe_polyfit(xs, ys, 3), raw_value)))
    return float(np.mean(predictions)) if predictions else float(np.polyval(model.coefficients, raw_value))


def polynomial_rmse(raw: Iterable[float], grams: Iterable[float], max_order: int = 5) -> dict[int, float]:
    x, y = _finite_unique_points(raw, grams)
    rmses: dict[int, float] = {}
    for degree in range(1, min(max_order, x.size - 1) + 1):
        coeffs = _safe_polyfit(x, y, degree)
        err = np.polyval(coeffs, x) - y
        rmses[degree] = float(np.sqrt(np.mean(err**2)))
    return rmses
