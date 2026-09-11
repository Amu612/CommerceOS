"""
Forecast Engine for Orders Agent.
Dynamically chooses appropriate forecasting methods based on data availability and characteristics:
- Ordinary Least Squares regression for trend estimation
- Non-parametric rate velocity
- None with explicit limitation when insufficient data exists.
"""
import math
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.intelligence.confidence.calculator import ConfidenceCalculator
from app.intelligence.statistics.profiler import StatisticalProfiler


class ForecastResult(BaseModel):
    prediction: float
    horizon: str
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    model_method: str
    training_observations: int
    data_quality: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    limitations: Optional[str] = None


class ForecastEngine:
    """
    Adaptive forecasting engine.
    Derives model dynamically according to time-series length and empirical variance.
    Never uses artificial cutoffs: uses OLS regression for n >= 5, empirical baseline for 2 <= n < 5,
    and single-point projection for n = 1.
    """

    @classmethod
    def forecast(
        cls,
        time_series: List[float],
        horizon: str = "7 days",
        steps_ahead: int = 1,
        data_quality: float = 1.0,
    ) -> Optional[ForecastResult]:
        """
        Calculates forecast with empirical error bounds derived from the observed data.
        """
        cleaned = [float(x) for x in time_series if x is not None and not math.isnan(x)]
        n = len(cleaned)

        if n == 0:
            return None

        conf_eval = ConfidenceCalculator.evaluate(sample_size=n, data_quality=data_quality)
        profile = StatisticalProfiler.profile(cleaned)
        std = profile.std_dev if profile else 0.0

        if n == 1:
            val = cleaned[0]
            return ForecastResult(
                prediction=round(val, 2),
                horizon=horizon,
                lower_bound=round(max(0.0, val * 0.5), 2),
                upper_bound=round(val * 1.5, 2),
                model_method="single_observation_point_projection",
                training_observations=1,
                data_quality=data_quality * 0.5,
                confidence=conf_eval.confidence_score,
                limitations="Single observation point available. Variance and statistical prediction intervals are NOT_ESTIMABLE.",
            )

        if n >= 5:

            x_vals = list(range(n))
            x_mean = sum(x_vals) / n
            y_mean = sum(cleaned) / n
            numerator = sum((x_vals[i] - x_mean) * (cleaned[i] - y_mean) for i in range(n))
            denominator = sum((x_vals[i] - x_mean) ** 2 for i in range(n))

            slope = numerator / denominator if denominator != 0 else 0.0
            intercept = y_mean - slope * x_mean

            target_x = n - 1 + steps_ahead
            pred = max(0.0, intercept + slope * target_x)

            error_margin = 1.96 * std * math.sqrt(1.0 + (1.0 / n))
            lower_b = max(0.0, pred - error_margin)
            upper_b = pred + error_margin

            return ForecastResult(
                prediction=round(pred, 2),
                horizon=horizon,
                lower_bound=round(lower_b, 2),
                upper_bound=round(upper_b, 2),
                model_method="ordinary_least_squares_trend",
                training_observations=n,
                data_quality=data_quality,
                confidence=conf_eval.confidence_score,
                limitations=None if n >= 30 else f"Short observation window ({n} data points). High sensitivity to trend volatility.",
            )

        # Non-parametric median rate fallback for small n (2 <= n < 5)
        median_val = profile.median if profile else sum(cleaned) / n
        error_margin = std if std > 0 else median_val * 0.20
        return ForecastResult(
            prediction=round(median_val, 2),
            horizon=horizon,
            lower_bound=round(max(0.0, median_val - error_margin), 2),
            upper_bound=round(median_val + error_margin, 2),
            model_method="empirical_median_baseline",
            training_observations=n,
            data_quality=data_quality * 0.8,
            confidence=round(conf_eval.confidence_score * 0.85, 3),
            limitations=f"Small sample size ({n} < 5). Median baseline with standard error margin used instead of regression.",
        )

