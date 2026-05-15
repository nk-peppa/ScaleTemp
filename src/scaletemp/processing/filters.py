from __future__ import annotations

import numpy as np
from scipy import signal


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    values = values.astype(float)
    if window == 1 or values.size == 0:
        return values
    left = window // 2
    right = window - 1 - left
    padded = np.pad(values, (left, right), mode="edge")
    kernel = np.ones(window, dtype=float) / window
    return np.convolve(padded, kernel, mode="valid")


def median_filter(values: np.ndarray, window: int) -> np.ndarray:
    window = max(1, int(window))
    if window % 2 == 0:
        window += 1
    return signal.medfilt(values, kernel_size=window)


def ema_filter(values: np.ndarray, alpha: float) -> np.ndarray:
    alpha = min(max(float(alpha), 0.01), 1.0)
    out = np.empty_like(values, dtype=float)
    if values.size == 0:
        return out
    out[0] = values[0]
    for i in range(1, values.size):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def reject_outliers(values: np.ndarray, z_limit: float = 3.5) -> np.ndarray:
    if values.size < 3:
        return values
    median = np.median(values)
    mad = np.median(np.abs(values - median)) or 1.0
    z = 0.6745 * (values - median) / mad
    return values[np.abs(z) <= z_limit]


def is_stable(values: np.ndarray, std_limit: float, slope_limit: float) -> bool:
    if values.size < 8:
        return False
    x = np.arange(values.size, dtype=float)
    slope = float(np.polyfit(x, values, 1)[0])
    return float(np.std(values)) <= std_limit and abs(slope) <= slope_limit
