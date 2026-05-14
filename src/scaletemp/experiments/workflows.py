from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time

import numpy as np
import pandas as pd

from scaletemp.hardware.hx711 import save_raw_csv
from scaletemp.processing.calibration import polynomial_rmse, fit_piecewise_overlapping
from scaletemp.processing.filters import moving_average, median_filter, ema_filter, reject_outliers
from scaletemp.processing.plots import bar_plot, line_plot, scatter_with_fit
from scaletemp.processing.service import ScaleService


@dataclass
class ExperimentResult:
    name: str
    raw_csv: str
    processed_csv: str
    figures: list[dict[str, str]]
    metadata: dict


class ExperimentRunner:
    """Interactive and web-callable experimental workflow assistant."""

    def __init__(self, service: ScaleService) -> None:
        self.service = service
        self.root = service.data_dir
        for folder in ["raw_data", "processed_data", "figures", "calibration", "logs"]:
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def _stamp(self, prefix: str) -> str:
        return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}"

    def _samples_to_frame(self, samples) -> pd.DataFrame:
        df = pd.DataFrame([s.__dict__ for s in samples])
        if not df.empty:
            df["time_s"] = (df["unix_time_ns"] - df["unix_time_ns"].iloc[0]) / 1e9
        return df

    def calibration(self, masses: list[float], duration_s: float = 3.0) -> ExperimentResult:
        raw_means: list[float] = []
        grams: list[float] = []
        all_samples = []
        for mass in masses:
            samples = self.service.sampler.collect(duration_s)
            values = reject_outliers(np.asarray([s.raw_adc for s in samples], dtype=float))
            raw_means.append(float(np.mean(values)))
            grams.append(float(mass))
            all_samples.extend(samples)
        stamp = self._stamp("calibration")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        save_raw_csv(all_samples, raw_path)
        processed = pd.DataFrame({"mass_g": grams, "raw_adc_mean": raw_means})
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        processed.to_csv(processed_path, index=False)
        model = fit_piecewise_overlapping(raw_means, grams)
        model.save(self.root / "calibration" / "current_calibration.json")
        self.service.calibration = model
        x = np.asarray(raw_means)
        y = np.asarray(grams)
        fits = {f"Order {d}": np.polyfit(x, y, d) for d in range(1, min(5, len(x) - 1) + 1)}
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(grams, {"Raw ADC mean": raw_means}, "Raw ADC vs weight", "Mass (g)", "Raw ADC count", fig_dir, "raw_adc_vs_weight"),
            scatter_with_fit(x, y, fits, fig_dir, "calibration_fitting_comparison"),
            bar_plot([str(k) for k in polynomial_rmse(x, y).keys()], list(polynomial_rmse(x, y).values()), "Polynomial order vs RMSE", "RMSE (g)", fig_dir, "polynomial_order_rmse"),
        ]
        for d, coeff in fits.items():
            residual = np.polyval(coeff, x) - y
            figures.append(line_plot(grams, {d: residual}, f"Residual plot - {d}", "Mass (g)", "Residual (g)", fig_dir, f"residual_{d.replace(' ', '_').lower()}"))
        meta = {"masses_g": grams, "polynomial_coefficients": model.coefficients, "duration_s": duration_s}
        (self.root / "logs" / f"{stamp}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return ExperimentResult("calibration", str(raw_path), str(processed_path), figures, meta)

    def filtering(self, duration_s: float = 10.0, window: int = 9) -> ExperimentResult:
        samples = self.service.sampler.collect(duration_s)
        df = self._samples_to_frame(samples)
        raw = df["raw_adc"].to_numpy(dtype=float)
        processed = pd.DataFrame({
            "time_s": df["time_s"],
            "raw_adc": raw,
            "moving_average": moving_average(raw, window),
            "median": median_filter(raw, window),
            "ema": ema_filter(raw, 0.18),
        })
        stamp = self._stamp("filtering")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(samples, raw_path)
        processed.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(processed["time_s"], {"Raw": raw, "Moving average": processed["moving_average"], "Median": processed["median"], "EMA": processed["ema"]}, "Raw vs filtered comparison", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_filtered"),
            bar_plot(["Raw", "Moving average", "Median", "EMA"], [float(np.std(processed[c])) for c in ["raw_adc", "moving_average", "median", "ema"]], "Noise STD comparison", "STD (ADC counts)", fig_dir, "noise_std_comparison"),
            line_plot(processed["time_s"], {"Raw noise": raw - np.mean(raw), "EMA noise": processed["ema"] - np.mean(processed["ema"])}, "Time-domain noise plots", "Time (s)", "Noise (ADC counts)", fig_dir, "time_domain_noise"),
        ]
        meta = {"duration_s": duration_s, "window": window, "sampling_rate_hz_est": len(samples) / duration_s if duration_s else 0}
        return ExperimentResult("filtering", str(raw_path), str(processed_path), figures, meta)

    def dynamic(self, duration_s: float = 8.0) -> ExperimentResult:
        samples = self.service.sampler.collect(duration_s)
        df = self._samples_to_frame(samples)
        raw = df["raw_adc"].to_numpy(dtype=float)
        t = df["time_s"].to_numpy(dtype=float)
        y0, y1 = float(np.percentile(raw[: max(3, len(raw)//5)], 10)), float(np.percentile(raw[-max(3, len(raw)//5):], 90))
        amp = y1 - y0 if y1 != y0 else 1.0
        norm = (raw - y0) / amp
        rise_idx = np.where((norm >= 0.1) & (norm <= 0.9))[0]
        rise_time = float(t[rise_idx[-1]] - t[rise_idx[0]]) if rise_idx.size > 1 else 0.0
        settling_idx = np.where(np.abs(norm - 1.0) < 0.02)[0]
        settling_time = float(t[settling_idx[0]]) if settling_idx.size else 0.0
        overshoot = float((np.max(norm) - 1.0) * 100.0)
        tau_idx = np.where(norm >= 0.632)[0]
        tau = float(t[tau_idx[0]]) if tau_idx.size else 0.0
        processed = pd.DataFrame({"time_s": t, "raw_adc": raw, "normalized_response": norm, "dynamic_error": 1.0 - norm})
        stamp = self._stamp("dynamic")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(samples, raw_path)
        processed.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(t, {"Step response": norm}, "Step response plot", "Time (s)", "Normalized output", fig_dir, "step_response"),
            line_plot(t, {"Removal/transition response": raw}, "Removal response plot", "Time (s)", "Raw ADC count", fig_dir, "removal_response"),
            line_plot(t, {"63.2% time constant target": np.full_like(norm, 0.632), "Response": norm}, "Time constant analysis", "Time (s)", "Normalized output", fig_dir, "time_constant_analysis"),
            line_plot(t, {"Dynamic error": 1.0 - norm}, "Dynamic error plot", "Time (s)", "Error", fig_dir, "dynamic_error"),
        ]
        meta = {"rise_time_s": rise_time, "settling_time_s": settling_time, "overshoot_percent": overshoot, "time_constant_s": tau, "duration_s": duration_s}
        return ExperimentResult("dynamic", str(raw_path), str(processed_path), figures, meta)

    def repeatability(self, trials: int = 5, duration_s: float = 2.0) -> ExperimentResult:
        rows = []
        all_samples = []
        for trial in range(1, trials + 1):
            samples = self.service.sampler.collect(duration_s)
            all_samples.extend(samples)
            values = reject_outliers(np.asarray([s.raw_adc for s in samples], dtype=float))
            rows.append({"trial": trial, "raw_mean": float(np.mean(values)), "raw_std": float(np.std(values))})
        stamp = self._stamp("repeatability")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(all_samples, raw_path)
        processed = pd.DataFrame(rows)
        processed.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(processed["trial"], {"Trial mean": processed["raw_mean"]}, "Repeatability scatter plot", "Trial", "Mean raw ADC", fig_dir, "repeatability_scatter"),
            bar_plot([str(i) for i in processed["trial"]], processed["raw_std"].tolist(), "Repeatability statistical summary", "STD (ADC counts)", fig_dir, "repeatability_stats"),
        ]
        return ExperimentResult("repeatability", str(raw_path), str(processed_path), figures, {"trials": trials, "duration_s": duration_s})

    def drift(self, duration_s: float = 600.0) -> ExperimentResult:
        samples = self.service.sampler.collect(duration_s)
        df = self._samples_to_frame(samples)
        stamp = self._stamp("drift")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(samples, raw_path)
        df.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(df["time_s"], {"Long-term drift": df["raw_adc"]}, "Long-term drift plot", "Time (s)", "Raw ADC count", fig_dir, "long_term_drift"),
            line_plot(df["time_s"], {"Creep from initial": df["raw_adc"] - df["raw_adc"].iloc[0]}, "Creep analysis plot", "Time (s)", "ADC count change", fig_dir, "creep_analysis"),
        ]
        return ExperimentResult("drift", str(raw_path), str(processed_path), figures, {"duration_s": duration_s})

    def auto_zero(self, duration_s: float = 20.0) -> ExperimentResult:
        samples = self.service.sampler.collect(duration_s)
        df = self._samples_to_frame(samples)
        stamp = self._stamp("auto_zero")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(samples, raw_path)
        df.to_csv(processed_path, index=False)
        figures = [line_plot(df["time_s"], {"Zero recovery": df["raw_adc"] - df["raw_adc"].iloc[-1]}, "Return-to-zero response plot", "Time (s)", "ADC count from final zero", self.root / "figures" / stamp, "return_to_zero")]
        return ExperimentResult("auto_zero", str(raw_path), str(processed_path), figures, {"duration_s": duration_s})
