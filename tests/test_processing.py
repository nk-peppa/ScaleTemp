from pathlib import Path
import tempfile

import numpy as np

from scaletemp.processing.calibration import fit_piecewise_overlapping, piecewise_predict, polynomial_rmse
from scaletemp.processing.filters import ema_filter, moving_average, median_filter, reject_outliers


def test_calibration_degree_rules_and_prediction():
    model = fit_piecewise_overlapping([0, 10, 20], [0, 100, 200])
    assert model.degree == 2
    assert abs(float(model.predict(10)) - 100) < 1e-6

    model4 = fit_piecewise_overlapping([0, 10, 20, 30, 40], [0, 100, 200, 300, 400])
    assert model4.degree == 3
    assert abs(piecewise_predict(model4, 25) - 250) < 1e-6


def test_rmse_orders():
    rmses = polynomial_rmse([0, 1, 2, 3], [0, 1, 4, 9])
    assert 1 in rmses and 2 in rmses
    assert rmses[2] < 1e-10


def test_filters_return_expected_shapes():
    values = np.arange(9, dtype=float)
    assert moving_average(values, 3).shape == values.shape
    assert median_filter(values, 3).shape == values.shape
    assert ema_filter(values, 0.2).shape == values.shape
    assert reject_outliers(np.asarray([1.0, 1.1, 0.9, 99.0])).max() < 99.0


def test_duplicate_raw_points_do_not_break_fit():
    model = fit_piecewise_overlapping([100.0, 100.0, 100.0], [0.0, 50.0, 100.0])
    assert model.degree == 0
    assert np.isfinite(piecewise_predict(model, 100.0))
