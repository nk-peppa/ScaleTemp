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


def fit_piecewise_overlapping(raw: Iterable[float], grams: Iterable[float]) -> CalibrationModel:
    """Fit the required overlapping polynomial calibration.

    For n < 4, the degree is n-1. For n >= 4, every adjacent four points are
    fitted with a cubic and predictions over overlapping spans are averaged.
    The returned coefficients are a global polynomial approximation for live
    conversion, while :func:`piecewise_predict` performs exact overlap averaging.
    """

    x = np.asarray(list(raw), dtype=float)
    y = np.asarray(list(grams), dtype=float)
    if x.size != y.size or x.size == 0:
        raise ValueError("raw and gram calibration points must have the same non-zero length")
    degree = min(3, max(0, x.size - 1))
    coeffs = np.polyfit(x, y, degree).tolist() if x.size > 1 else [float(y[0])]
    return CalibrationModel(x.tolist(), y.tolist(), coeffs, degree)


def piecewise_predict(model: CalibrationModel, raw_value: float) -> float:
    x = np.asarray(model.raw_points, dtype=float)
    y = np.asarray(model.gram_points, dtype=float)
    if x.size < 4:
        return float(np.polyval(model.coefficients, raw_value))
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    predictions: list[float] = []
    for start in range(0, x.size - 3):
        xs = x[start : start + 4]
        ys = y[start : start + 4]
        lo, hi = xs.min(), xs.max()
        if lo <= raw_value <= hi or not predictions:
            predictions.append(float(np.polyval(np.polyfit(xs, ys, 3), raw_value)))
    return float(np.mean(predictions))


def polynomial_rmse(raw: Iterable[float], grams: Iterable[float], max_order: int = 5) -> dict[int, float]:
    x = np.asarray(list(raw), dtype=float)
    y = np.asarray(list(grams), dtype=float)
    rmses: dict[int, float] = {}
    for degree in range(1, min(max_order, x.size - 1) + 1):
        coeffs = np.polyfit(x, y, degree)
        err = np.polyval(coeffs, x) - y
        rmses[degree] = float(np.sqrt(np.mean(err**2)))
    return rmses
