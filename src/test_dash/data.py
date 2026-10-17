"""Synthetic field-trial data for the example Dash app.

The table is generated in-process from a fixed random seed so that the example
runs without any external data files and always produces the same figures.
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np
import pandas as pd

#: Crop categories present in the dataset.
CROPS: tuple[str, ...] = ("Wheat", "Maize", "Barley", "Rice", "Soy")

#: Human readable labels for every numeric column (used as axis titles / hovers).
METRICS: dict[str, str] = {
    "rainfall_mm": "Rainfall (mm)",
    "temperature_c": "Mean temperature (°C)",
    "soil_ph": "Soil pH",
    "yield_t_per_ha": "Yield (t/ha)",
}

#: ``(mean, standard deviation)`` of one growing condition.
MeanSd = tuple[float, float]


class Profile(TypedDict):
    """Growing conditions and yield-model coefficients of a single crop."""

    rain: MeanSd
    temp: MeanSd
    ph: MeanSd
    base: float
    k_rain: float
    k_temp: float
    noise: float


# Per-crop growing conditions, plus coefficients of the simple yield model used
# to synthesise a believable rainfall / temperature / soil-pH relationship.
_PROFILES: dict[str, Profile] = {
    "Wheat": {
        "rain": (260, 55), "temp": (16.0, 3.5), "ph": (6.6, 0.35),
        "base": 5.2, "k_rain": 0.011, "k_temp": 0.09, "noise": 0.45,
    },
    "Maize": {
        "rain": (420, 80), "temp": (23.0, 3.0), "ph": (6.2, 0.40),
        "base": 8.4, "k_rain": 0.008, "k_temp": 0.14, "noise": 0.60,
    },
    "Barley": {
        "rain": (180, 45), "temp": (14.0, 3.0), "ph": (7.1, 0.30),
        "base": 4.1, "k_rain": 0.009, "k_temp": 0.07, "noise": 0.35,
    },
    "Rice": {
        "rain": (620, 90), "temp": (26.0, 2.5), "ph": (5.9, 0.35),
        "base": 6.8, "k_rain": 0.006, "k_temp": 0.10, "noise": 0.50,
    },
    "Soy": {
        "rain": (330, 60), "temp": (21.0, 3.0), "ph": (6.4, 0.30),
        "base": 3.6, "k_rain": 0.007, "k_temp": 0.11, "noise": 0.30,
    },
}


def build_dataset(plots_per_crop: int = 60, seed: int = 20261017) -> pd.DataFrame:
    """Return one tidy row per field plot with simulated measurements."""
    rng = np.random.default_rng(seed)
    frames: list[pd.DataFrame] = []

    for crop in CROPS:
        profile = _PROFILES[crop]
        rain_mean, rain_sd = profile["rain"]
        temp_mean, temp_sd = profile["temp"]
        ph_mean, ph_sd = profile["ph"]

        rainfall = np.maximum(rng.normal(rain_mean, rain_sd, plots_per_crop), 40.0)
        temperature = rng.normal(temp_mean, temp_sd, plots_per_crop)
        soil_ph = np.clip(rng.normal(ph_mean, ph_sd, plots_per_crop), 4.0, 8.5)

        # Simple yield model: gains from rainfall and heat, quadratic soil pH
        # penalty for moving away from the crop's preferred acidity.
        yield_t = (
            profile["base"]
            + profile["k_rain"] * (rainfall - rain_mean)
            + profile["k_temp"] * (temperature - temp_mean)
            - 1.6 * (soil_ph - ph_mean) ** 2
            + rng.normal(0.0, profile["noise"], plots_per_crop)
        )

        frames.append(
            pd.DataFrame(
                {
                    "crop": crop,
                    "rainfall_mm": np.round(rainfall, 0),
                    "temperature_c": np.round(temperature, 1),
                    "soil_ph": np.round(soil_ph, 2),
                    "yield_t_per_ha": np.round(np.clip(yield_t, 0.4, None), 2),
                }
            )
        )

    data = pd.concat(frames, ignore_index=True)
    data.insert(0, "plot_id", [f"PLT-{i:04d}" for i in range(1, len(data) + 1)])
    return data
