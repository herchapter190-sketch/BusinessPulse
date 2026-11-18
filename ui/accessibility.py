"""Colour accessibility checks for the BusinessPulse design system (pure Python + numpy).

- Contrast ratio: WCAG 2.x relative-luminance formula.
- Colour difference: OKLab distance × 100 (a "ΔE" where ~2 is barely noticeable).
- Colour-vision deficiency: Machado, Oliveira & Fernandes (2009) simulation
  matrices at full severity for protanopia, deuteranopia and tritanopia,
  applied in linear RGB.

Targets used by the tests (from the project's data-visualisation guidelines):
- Text: contrast ≥ 4.5:1 (≥ 3:1 for large text and graphical marks).
- Chart series next to each other: ΔE ≥ 15 for normal vision, ≥ 8 under each
  simulated colour-vision deficiency.
"""

from __future__ import annotations

import numpy as np

_CVD_MATRICES = {
    "protanopia": np.array([[0.152286, 1.052583, -0.204868],
                            [0.114503, 0.786281, 0.099216],
                            [-0.003882, -0.048116, 1.051998]]),
    "deuteranopia": np.array([[0.367322, 0.860646, -0.227968],
                              [0.280085, 0.672501, 0.047413],
                              [-0.011820, 0.042940, 0.968881]]),
    "tritanopia": np.array([[1.255528, -0.076749, -0.178779],
                            [-0.078411, 0.930809, 0.147602],
                            [0.004733, 0.691367, 0.303900]]),
}


def hex_to_rgb(color: str) -> np.ndarray:
    color = color.lstrip("#")
    return np.array([int(color[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) / 255.0


def _to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def blend(foreground: str, background: str, alpha: float) -> str:
    """The solid colour a see-through (glass) layer shows over a background,
    as the browser composites it (sRGB, `alpha` = opacity of the foreground)."""
    mixed = alpha * hex_to_rgb(foreground) + (1 - alpha) * hex_to_rgb(background)
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in mixed)


def relative_luminance(color: str) -> float:
    r, g, b = _to_linear(hex_to_rgb(color))
    return float(0.2126 * r + 0.7152 * g + 0.0722 * b)


def contrast_ratio(foreground: str, background: str) -> float:
    lighter, darker = sorted((relative_luminance(foreground), relative_luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _oklab(linear_rgb: np.ndarray) -> np.ndarray:
    r, g, b = linear_rgb
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = np.cbrt([l, m, s])
    return np.array([
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    ])


def _linear(color: str, deficiency: str | None = None) -> np.ndarray:
    linear = _to_linear(hex_to_rgb(color))
    if deficiency:
        linear = np.clip(_CVD_MATRICES[deficiency] @ linear, 0.0, 1.0)
    return linear


def color_difference(first: str, second: str, deficiency: str | None = None) -> float:
    """OKLab distance × 100 between two colours, optionally as seen with a colour-vision deficiency."""
    return float(np.linalg.norm(_oklab(_linear(first, deficiency)) - _oklab(_linear(second, deficiency))) * 100)


def worst_cvd_difference(first: str, second: str) -> tuple[float, str]:
    results = [(color_difference(first, second, name), name) for name in _CVD_MATRICES]
    return min(results)


def palette_report(colors: list[str], surface: str) -> list[dict]:
    """Adjacent-pair report for an ordered palette: normal and worst-case CVD difference."""
    rows = []
    for first, second in zip(colors, colors[1:]):
        worst, deficiency = worst_cvd_difference(first, second)
        rows.append({"pair": f"{first} / {second}", "normal": round(color_difference(first, second), 1),
                     "worst_cvd": round(worst, 1), "deficiency": deficiency})
    for color in colors:
        rows.append({"pair": f"{color} on {surface}", "contrast": round(contrast_ratio(color, surface), 2)})
    return rows
