from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def setup_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "figure.dpi": 140,
        "savefig.dpi": 300,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.frameon": True,
        "grid.alpha": 0.28,
    })


def save_figure(fig: plt.Figure, folder: Path, name: str) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    svg = folder / f"{name}.svg"
    pdf = folder / f"{name}.pdf"
    fig.tight_layout()
    fig.savefig(svg)
    fig.savefig(pdf)
    plt.close(fig)
    return {"svg": str(svg), "pdf": str(pdf)}


def line_plot(x: Iterable[float], series: dict[str, Iterable[float]], title: str, xlabel: str, ylabel: str, folder: Path, name: str) -> dict[str, str]:
    setup_style()
    fig, ax = plt.subplots(figsize=(8.0, 4.5))
    for label, y in series.items():
        ax.plot(list(x), list(y), label=label, linewidth=1.6)
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    ax.legend()
    ax.grid(True)
    return save_figure(fig, folder, name)


def bar_plot(labels: list[str], values: list[float], title: str, ylabel: str, folder: Path, name: str) -> dict[str, str]:
    setup_style()
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    ax.bar(labels, values, color=["#4f8cff", "#8b5cf6", "#14b8a6", "#f97316"][: len(labels)])
    ax.set(title=title, ylabel=ylabel)
    ax.grid(True, axis="y")
    return save_figure(fig, folder, name)


def scatter_with_fit(raw: np.ndarray, grams: np.ndarray, fits: dict[str, np.ndarray], folder: Path, name: str) -> dict[str, str]:
    setup_style()
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.scatter(raw, grams, label="Calibration samples", color="#f97316", zorder=3)
    xs = np.linspace(raw.min(), raw.max(), 300)
    for label, coeffs in fits.items():
        ax.plot(xs, np.polyval(coeffs, xs), label=label)
    ax.set(title="Calibration fitting comparison", xlabel="Raw ADC count", ylabel="Mass (g)")
    ax.legend()
    ax.grid(True)
    return save_figure(fig, folder, name)
