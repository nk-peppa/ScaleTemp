from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time

import numpy as np
import pandas as pd

from scaletemp.hardware.hx711 import save_raw_csv
from scaletemp.processing.calibration import polynomial_rmse, fit_piecewise_overlapping
from scaletemp.processing.filters import moving_average, median_filter, ema_filter, trimmed_mean_filter, window_limited_ema, reject_outliers
from scaletemp.processing.plots import bar_plot, line_plot, scatter_with_fit, save_figure, setup_style
import matplotlib.pyplot as plt
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
        groups = [(mass, self.service.sampler.collect(duration_s)) for mass in masses]
        return self.calibration_from_groups(groups, duration_s)

    def calibration_from_groups(self, groups, duration_s: float = 3.0) -> ExperimentResult:
        raw_means: list[float] = []
        grams: list[float] = []
        all_samples = []
        for mass, samples in groups:
            values = reject_outliers(np.asarray([s.raw_adc for s in samples], dtype=float))
            if values.size == 0:
                values = np.asarray([0.0])
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
        x = np.asarray(raw_means, dtype=float)
        y = np.asarray(grams, dtype=float)
        fits = {}
        for d in range(1, min(5, len(np.unique(x)) - 1) + 1):
            try:
                fits[f"Order {d}"] = np.polyfit(x, y, d)
            except np.linalg.LinAlgError:
                continue
        fig_dir = self.root / "figures" / stamp
        rmse = polynomial_rmse(x, y)
        rmse_base = rmse.get(1, next(iter(rmse.values()), 1.0)) or 1.0
        rmse_relative = {order: value / rmse_base for order, value in rmse.items()}
        figures = [
            line_plot(grams, {"Raw ADC mean": raw_means}, "Raw ADC vs weight", "Mass (g)", "Raw ADC count", fig_dir, "raw_adc_vs_weight"),
            scatter_with_fit(x, y, fits or {"Current model": np.asarray(model.coefficients)}, fig_dir, "calibration_fitting_comparison_order_1_to_5"),
            bar_plot([str(k) for k in rmse.keys()] or ["0"], list(rmse.values()) or [0.0], "Polynomial order vs RMSE (1st order baseline)", "RMSE (g)", fig_dir, "polynomial_order_rmse"),
            bar_plot([str(k) for k in rmse_relative.keys()] or ["0"], list(rmse_relative.values()) or [0.0], "Polynomial order relative error (Order 1 = 1.0)", "RMSE / 1st-order RMSE", fig_dir, "polynomial_order_relative_rmse"),
        ]
        if "Order 5" in fits:
            figures.append(scatter_with_fit(x, y, {"Order 5": fits["Order 5"]}, fig_dir, "fifth_order_calibration_fit"))
        for d, coeff in fits.items():
            residual = np.polyval(coeff, x) - y
            figures.append(line_plot(grams, {d: residual}, f"Residual plot - {d}", "Mass (g)", "Residual (g)", fig_dir, f"residual_{d.replace(' ', '_').lower()}"))
        meta = {"masses_g": grams, "polynomial_coefficients": model.coefficients, "duration_s": duration_s, "rmse": rmse, "rmse_relative_to_first_order": rmse_relative}
        (self.root / "logs" / f"{stamp}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return ExperimentResult("calibration", str(raw_path), str(processed_path), figures, meta)

    def filtering(self, duration_s: float = 10.0, window: int = 18) -> ExperimentResult:
        samples = self.service.sampler.collect(duration_s)
        df = self._samples_to_frame(samples)
        raw = df["raw_adc"].to_numpy(dtype=float)
        if raw.size == 0:
            raw = np.asarray([0.0])
            df = pd.DataFrame({"time_s": [0.0], "raw_adc": raw})
        raw_std = float(np.std(raw))
        raw_window_limit = max(raw_std * 2.0, 1.0)
        processed = pd.DataFrame({
            "time_s": df["time_s"],
            "raw_adc": raw,
            "moving_average": moving_average(raw, window),
            "median": median_filter(raw, window),
            "ema": ema_filter(raw, 0.18),
            "trimmed_mean": trimmed_mean_filter(raw, window),
            "window_limited_ema": window_limited_ema(raw, 0.18, raw_window_limit),
        })
        stamp = self._stamp("filtering")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(samples, raw_path)
        processed.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp
        figures = [
            line_plot(processed["time_s"], {"Raw": raw}, "Raw ADC output", "Time (s)", "Raw ADC count", fig_dir, "raw_adc_output"),
            line_plot(processed["time_s"], {"Raw": raw, "Moving average": processed["moving_average"]}, "Raw vs Moving Average", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_moving_average"),
            line_plot(processed["time_s"], {"Raw": raw, "Median": processed["median"]}, "Raw vs Median Filter", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_median"),
            line_plot(processed["time_s"], {"Raw": raw, "EMA": processed["ema"]}, "Raw vs EMA Filter", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_ema"),
            line_plot(processed["time_s"], {"Raw": raw, "Trimmed mean": processed["trimmed_mean"]}, "Raw vs Trimmed Mean Filter", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_trimmed_mean"),
            line_plot(processed["time_s"], {"Raw": raw, "Window-limited EMA": processed["window_limited_ema"]}, "Raw vs Window-limited EMA", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_window_limited_ema"),
            line_plot(processed["time_s"], {"Raw": raw, "Moving average": processed["moving_average"], "Median": processed["median"], "EMA": processed["ema"], "Trimmed mean": processed["trimmed_mean"], "Window-limited EMA": processed["window_limited_ema"]}, "Multiple Filtering Algorithms Comparison", "Time (s)", "Raw ADC count", fig_dir, "raw_vs_filtered"),
            bar_plot(["Raw", "Moving average", "Median", "EMA", "Trimmed mean", "Window-limited EMA"], [float(np.std(processed[c])) for c in ["raw_adc", "moving_average", "median", "ema", "trimmed_mean", "window_limited_ema"]], "Noise STD comparison", "STD (ADC counts)", fig_dir, "noise_std_comparison"),
            line_plot(processed["time_s"], {"Raw noise": raw - np.mean(raw), "Window-limited EMA noise": processed["window_limited_ema"] - np.mean(processed["window_limited_ema"])}, "Time-domain noise plots", "Time (s)", "Noise (ADC counts)", fig_dir, "time_domain_noise"),
        ]
        meta = {"duration_s": duration_s, "window": window, "raw_window_limit_adc": raw_window_limit, "sampling_rate_hz_est": len(samples) / duration_s if duration_s else 0}
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
        setup_style()
        fig, ax = plt.subplots(figsize=(8.0, 4.5))
        ax.plot(t, raw, label="Raw ADC response", linewidth=1.6)
        tau_raw = y0 + 0.632 * amp
        ax.axhline(tau_raw, color="#8b5cf6", linestyle="--", label="63.2% level")
        ax.axvline(tau, color="#f97316", linestyle="--", label=f"tau = {tau:.2f}s")
        ax.annotate(f"τ = {tau:.2f}s", xy=(tau, tau_raw), xytext=(tau, tau_raw + 0.08 * amp if amp else tau_raw), arrowprops={"arrowstyle": "->", "color": "#f97316"})
        ax.set(title="Time constant analysis (raw ADC)", xlabel="Time (s)", ylabel="Raw ADC count")
        ax.legend()
        ax.grid(True)
        tau_fig = save_figure(fig, fig_dir, "time_constant_analysis")
        figures = [
            line_plot(t, {"Raw step response": raw}, "Step response plot", "Time (s)", "Raw ADC count", fig_dir, "step_response"),
            line_plot(t, {"Raw removal/transition response": raw}, "Removal response plot", "Time (s)", "Raw ADC count", fig_dir, "removal_response"),
            tau_fig,
            line_plot(t, {"Raw dynamic error from final": y1 - raw}, "Dynamic error plot", "Time (s)", "Raw ADC error", fig_dir, "dynamic_error"),
        ]
        meta = {"rise_time_s": rise_time, "settling_time_s": settling_time, "overshoot_percent": overshoot, "time_constant_s": tau, "duration_s": duration_s}
        return ExperimentResult("dynamic", str(raw_path), str(processed_path), figures, meta)

    def repeatability(self, trials: int = 5, duration_s: float = 2.0) -> ExperimentResult:
        groups = [self.service.sampler.collect(duration_s) for _ in range(trials)]
        return self.repeatability_from_groups(groups, duration_s)

    def repeatability_from_groups(self, groups, duration_s: float = 2.0) -> ExperimentResult:
        rows = []
        all_samples = []
        for trial, samples in enumerate(groups, start=1):
            all_samples.extend(samples)
            values = reject_outliers(np.asarray([s.raw_adc for s in samples], dtype=float))
            if values.size == 0:
                values = np.asarray([0.0])
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
        return ExperimentResult("repeatability", str(raw_path), str(processed_path), figures, {"trials": len(groups), "duration_s": duration_s})

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
        return self.auto_zero_from_groups([samples], load_mass=0.0, duration_s=duration_s)

    def auto_zero_from_groups(self, groups, load_mass: float = 500.0, duration_s: float = 5.0) -> ExperimentResult:
        zero_samples = groups[0] if len(groups) > 0 else []
        loaded_samples = groups[1] if len(groups) > 1 else []
        removal_samples = groups[2] if len(groups) > 2 else (groups[-1] if groups else [])
        all_samples = []
        for samples in groups:
            all_samples.extend(samples)
        df = self._samples_to_frame(all_samples)
        if df.empty:
            df = pd.DataFrame({"unix_time_ns": [0], "sequence": [0], "raw_adc": [0], "status": ["EMPTY"], "time_s": [0.0]})

        zero_raw = float(np.mean([s.raw_adc for s in zero_samples])) if zero_samples else float(df["raw_adc"].iloc[0])
        loaded_raw = float(np.mean([s.raw_adc for s in loaded_samples])) if loaded_samples else zero_raw + max(load_mass, 1.0)
        scale = (loaded_raw - zero_raw) / load_mass if abs(load_mass) > 1e-9 and abs(loaded_raw - zero_raw) > 1e-9 else 1.0
        grams = (df["raw_adc"].to_numpy(dtype=float) - zero_raw) / scale
        auto_zero_grams = grams.copy()

        trigger_time = None
        removal_start_index = max(0, len(zero_samples) + len(loaded_samples))
        candidate_start = None
        t = df["time_s"].to_numpy(dtype=float)
        for i in range(removal_start_index, len(auto_zero_grams)):
            if abs(auto_zero_grams[i]) <= 2.0:
                if candidate_start is None:
                    candidate_start = t[i]
                elif t[i] - candidate_start >= 3.0:
                    trigger_time = t[i]
                    auto_zero_grams[i:] -= auto_zero_grams[i]
                    break
            else:
                candidate_start = None

        processed = df.copy()
        processed["grams_before_auto_zero"] = grams
        processed["grams_after_auto_zero"] = auto_zero_grams
        stamp = self._stamp("auto_zero")
        raw_path = self.root / "raw_data" / f"{stamp}.csv"
        processed_path = self.root / "processed_data" / f"{stamp}.csv"
        save_raw_csv(all_samples, raw_path)
        processed.to_csv(processed_path, index=False)
        fig_dir = self.root / "figures" / stamp

        setup_style()
        fig, ax = plt.subplots(figsize=(8.0, 4.5))
        ax.plot(t, grams, label="Weight before auto-zero", linewidth=1.2, alpha=0.75)
        ax.plot(t, auto_zero_grams, label="Weight after auto-zero", linewidth=1.8)
        if trigger_time is not None:
            ax.axvline(trigger_time, color="#f97316", linestyle="--", label=f"auto-zero at {trigger_time:.2f}s")
            ax.annotate("auto-zero turn", xy=(trigger_time, 0), xytext=(trigger_time, max(load_mass * 0.2, 5)), arrowprops={"arrowstyle": "->", "color": "#f97316"})
        ax.set(title="Auto-zero performance (weight curve)", xlabel="Time (s)", ylabel="Weight (g)")
        ax.legend()
        ax.grid(True)
        auto_zero_fig = save_figure(fig, fig_dir, "auto_zero_weight_curve")

        figures = [
            line_plot(t, {"Raw ADC": df["raw_adc"]}, "Auto-zero raw ADC trace", "Time (s)", "Raw ADC count", fig_dir, "auto_zero_raw_trace"),
            auto_zero_fig,
        ]
        meta = {"duration_s": duration_s, "load_mass_g": load_mass, "zero_raw": zero_raw, "loaded_raw": loaded_raw, "raw_per_g": scale, "auto_zero_trigger_time_s": trigger_time}
        return ExperimentResult("auto_zero", str(raw_path), str(processed_path), figures, meta)
