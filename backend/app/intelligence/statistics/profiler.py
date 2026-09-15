"""
Pure Mathematical and Statistical Profiler.
Derives distributions, central tendencies, spreads, percentiles, IQR, MAD, and rolling baselines.
Zero hardcoded business thresholds.
"""

import math

from pydantic import BaseModel


class DistributionProfile(BaseModel):
    count: int
    mean: float
    median: float
    variance: float
    std_dev: float
    min_val: float
    max_val: float
    q25: float
    q75: float
    iqr: float
    mad: float  # Median Absolute Deviation
    skewness: float
    lower_outer_fence: float  # q25 - 1.5 * iqr
    upper_outer_fence: float  # q75 + 1.5 * iqr

    @property
    def q1(self) -> float:
        return self.q25

    @property
    def q3(self) -> float:
        return self.q75


class StatisticalProfiler:
    """Computes robust statistical metrics without assuming normality."""

    @staticmethod
    def profile(values: list[float]) -> DistributionProfile | None:
        """Calculates non-parametric and parametric distribution metrics."""
        cleaned = [float(x) for x in values if x is not None and not math.isnan(x)]
        n = len(cleaned)
        if n == 0:
            return None

        cleaned.sort()
        mean_val = sum(cleaned) / n
        median_val = cleaned[n // 2] if n % 2 != 0 else (cleaned[n // 2 - 1] + cleaned[n // 2]) / 2.0

        # Variance & Std Dev
        var = sum((x - mean_val) ** 2 for x in cleaned) / (n - 1) if n > 1 else 0.0
        std = math.sqrt(var)

        # Quartiles
        def _percentile(data: list[float], p: float) -> float:
            k = (len(data) - 1) * p
            f = math.floor(k)
            c = math.ceil(k)
            if f == c:
                return data[int(k)]
            d0 = data[int(f)] * (c - k)
            d1 = data[int(c)] * (k - f)
            return d0 + d1

        q25 = _percentile(cleaned, 0.25)
        q75 = _percentile(cleaned, 0.75)
        iqr = max(0.0, q75 - q25)

        # Median Absolute Deviation (MAD)
        abs_deviations = sorted([abs(x - median_val) for x in cleaned])
        mad = (
            abs_deviations[n // 2]
            if n % 2 != 0
            else (abs_deviations[n // 2 - 1] + abs_deviations[n // 2]) / 2.0
        )

        # Skewness
        skew = (sum((x - mean_val) ** 3 for x in cleaned) / n) / (std**3) if std > 1e-6 else 0.0

        # Fences (Tukey's IQR method)
        lower_fence = q25 - 1.5 * iqr
        upper_fence = q75 + 1.5 * iqr

        return DistributionProfile(
            count=n,
            mean=round(mean_val, 4),
            median=round(median_val, 4),
            variance=round(var, 4),
            std_dev=round(std, 4),
            min_val=round(cleaned[0], 4),
            max_val=round(cleaned[-1], 4),
            q25=round(q25, 4),
            q75=round(q75, 4),
            iqr=round(iqr, 4),
            mad=round(mad, 4),
            skewness=round(skew, 4),
            lower_outer_fence=round(lower_fence, 4),
            upper_outer_fence=round(upper_fence, 4),
        )

    @staticmethod
    def rolling_baselines(series: list[float], window: int = 7) -> list[dict[str, float]]:
        """Calculates rolling mean and rolling standard deviation over time series observations."""
        results = []
        for i in range(len(series)):
            start_idx = max(0, i - window + 1)
            sub = series[start_idx : i + 1]
            if not sub:
                continue
            mean_v = sum(sub) / len(sub)
            var_v = sum((x - mean_v) ** 2 for x in sub) / (len(sub) - 1) if len(sub) > 1 else 0.0
            results.append(
                {
                    "index": i,
                    "value": series[i],
                    "rolling_mean": round(mean_v, 4),
                    "rolling_std": round(math.sqrt(var_v), 4),
                }
            )
        return results
