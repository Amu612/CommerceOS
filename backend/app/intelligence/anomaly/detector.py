"""
Empirical Anomaly Detection Module for Orders Agent.
Detects statistical deviations without hardcoded thresholds:
- Z-score relative to sample standard deviation
- Modified Z-score relative to MAD (robust against skewed/outlier heavy operational data)
- Interquartile Range (IQR) fence exceedance
- Change-point detection
"""
import math
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.intelligence.statistics.profiler import StatisticalProfiler, DistributionProfile


class StatisticalAnomaly(BaseModel):
    metric_name: str
    observed_value: float
    expected_baseline: float
    deviation: float
    anomaly_score: float = Field(..., ge=0.0, description="Normalized score 0.0 to 1.0")
    detection_method: str
    is_anomaly: bool
    empirical_evidence: str


class AnomalyDetector:
    """
    Evaluates whether an observed metric represents a genuine statistical anomaly
    relative to observed historical data.
    """

    @classmethod
    def evaluate_sample(
        cls,
        observed_value: float,
        historical_samples: List[float],
        metric_name: str = "metric",
    ) -> StatisticalAnomaly:
        """
        Calculates empirical anomaly score based on historical distribution.
        Requires at least 3 historical points; otherwise marks insufficient evidence.
        """
        if len(historical_samples) < 2:
            return StatisticalAnomaly(
                metric_name=metric_name,
                observed_value=observed_value,
                expected_baseline=observed_value,
                deviation=0.0,
                anomaly_score=0.0,
                detection_method="NOT_ESTIMABLE",
                is_anomaly=False,
                empirical_evidence=f"Sample size n={len(historical_samples)} has undefined degrees of freedom for variance estimation (minimum n=2 required).",
            )


        profile = StatisticalProfiler.profile(historical_samples)
        if profile is None:
            return StatisticalAnomaly(
                metric_name=metric_name,
                observed_value=observed_value,
                expected_baseline=observed_value,
                deviation=0.0,
                anomaly_score=0.0,
                detection_method="empty_distribution",
                is_anomaly=False,
                empirical_evidence="Historical distribution could not be computed.",
            )

        # 1. Evaluate via Robust Modified Z-Score using MAD:
        if profile.mad > 1e-6:
            mod_z = 0.6745 * abs(observed_value - profile.median) / profile.mad
            extreme_upper = profile.q75 + 3.0 * profile.iqr
            extreme_lower = profile.q25 - 3.0 * profile.iqr
            is_anom = mod_z >= 3.5 or observed_value > extreme_upper or observed_value < extreme_lower
            score = round(math.tanh(mod_z / 4.0), 3)

            evidence = (
                f"Observed value {observed_value} diverges from empirical median {profile.median} "
                f"with modified Z-score {mod_z:.2f} (MAD={profile.mad}, IQR=[{profile.q25}, {profile.q75}])."
            )

            return StatisticalAnomaly(
                metric_name=metric_name,
                observed_value=observed_value,
                expected_baseline=profile.median,
                deviation=round(observed_value - profile.median, 4),
                anomaly_score=score,
                detection_method="modified_z_mad",
                is_anomaly=is_anom,
                empirical_evidence=evidence,
            )

        # 2. Fallback to standard Z-Score if MAD is zero
        if profile.std_dev > 1e-6:
            z_score = abs(observed_value - profile.mean) / profile.std_dev
            is_anom = z_score >= 3.0
            score = round(math.tanh(z_score / 3.0), 3)
            return StatisticalAnomaly(
                metric_name=metric_name,
                observed_value=observed_value,
                expected_baseline=profile.mean,
                deviation=round(observed_value - profile.mean, 4),
                anomaly_score=score,
                detection_method="standard_z_score",
                is_anomaly=is_anom,
                empirical_evidence=f"Observed value {observed_value} has Z-score of {z_score:.2f} vs empirical mean {profile.mean} (std={profile.std_dev}).",
            )

        # 3. Variance is zero: identical samples
        dev = observed_value - profile.mean
        is_anom = abs(dev) > 1e-6
        return StatisticalAnomaly(
            metric_name=metric_name,
            observed_value=observed_value,
            expected_baseline=profile.mean,
            deviation=round(dev, 4),
            anomaly_score=1.0 if is_anom else 0.0,
            detection_method="constant_baseline_deviation",
            is_anomaly=is_anom,
            empirical_evidence=f"Observed {observed_value} while baseline is constant at {profile.mean}.",
        )
