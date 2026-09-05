"""Brand tokens, as the single Python source of truth.

assets/css/brand.css carries the same values as CSS custom properties for
the page; this module carries them for anything that has to emit a colour
from Python - chart specs above all. If a value changes it changes in both
places, and `tests/test_render.py` asserts the two agree so the chart bars
cannot drift from the page they sit on.

The reasoning behind each value is in BRAND.md. The short version:

- Aqua Blue replaces the v1 navy #1F4E79 as primary ink. Their relative
  luminance is 0.0710 vs 0.0712 - visually the same weight on white, so
  every v1 contrast decision carries over unchanged.
- Yellow is 1.49:1 against white. It is never text on a light surface; it is
  a filled chip, or a highlight on the Deep Blue band, or the one emphasis
  bar in a chart.
- The flag colours (red / amber / green / blue) are deliberately NOT brand
  colours. They encode status, and a reader must never confuse "this is our
  blue" with "this is a change we made, result pending".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

__all__ = ["BRAND", "Brand", "TERMS"]


@dataclass(frozen=True)
class Brand:
    # palette
    aqua: str = "#014C8D"          # primary ink; replaces v1 #1F4E79
    deep: str = "#051732"          # dark band, body text
    grey: str = "#465564"          # muted text, axis labels
    sandstone: str = "#F8F8F8"     # page ground
    yellow: str = "#FBCF20"        # highlight ONLY on dark surfaces / filled chip / emphasis bar
    white: str = "#FFFFFF"
    # single-hue chart ramp: Aqua at descending alpha, so multi-series charts
    # stay one hue. Chart.js accepts rgba() strings.
    aqua_rgb: tuple[int, int, int] = (1, 76, 141)
    # status encodings - off-palette on purpose
    flag_red: str = "#dc2626"
    flag_amber: str = "#ca8a04"
    flag_green: str = "#16a34a"
    flag_blue: str = "#0284c7"
    # type
    font_stack: str = (
        "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif"
    )
    headline_weight: int = 900
    headline_tracking: str = "0.02em"
    subhead_tracking: str = "0.025em"
    # logo exclusion zone, px, from BRAND.md
    logo_clear_lockup: int = 60
    logo_clear_tight: int = 40

    def aqua_alpha(self, alpha: str) -> str:
        r, g, b = self.aqua_rgb
        return f"rgba({r},{g},{b},{alpha})"

    @property
    def series_ramp(self) -> tuple[str, ...]:
        """Fill colours for successive series: same hue, stepping down in alpha.

        Three steps is the ceiling. A chart that needs a fourth series needs
        a second chart.
        """
        return (self.aqua, self.aqua_alpha("0.55"), self.aqua_alpha("0.30"))

    @property
    def flag_colors(self) -> Mapping[str, str]:
        return MappingProxyType({
            "red": self.flag_red, "amber": self.flag_amber,
            "green": self.flag_green, "blue": self.flag_blue,
        })

    def as_css_vars(self) -> dict[str, str]:
        """The tokens brand.css must declare, name -> value.

        The test suite reads brand.css and checks each of these is present
        with exactly this value.
        """
        return {
            "--vhpc-aqua": self.aqua,
            "--vhpc-deep": self.deep,
            "--vhpc-grey": self.grey,
            "--vhpc-sandstone": self.sandstone,
            "--vhpc-yellow": self.yellow,
            "--flag-red": self.flag_red,
            "--flag-amber": self.flag_amber,
            "--flag-green": self.flag_green,
            "--flag-blue": self.flag_blue,
        }


BRAND = Brand()

# Methodology terms that legitimately contain digits. Templates write them as
# {{ term('m1') }} -> <dfn data-term="m1">M1</dfn>, so the bare-digit scan can
# exempt them by a closed vocabulary rather than by guesswork. Adding a term
# here is a methodology decision; do it deliberately.
TERMS: Mapping[str, str] = MappingProxyType({
    "m1": "M1",
    "first90": "first 90 days",
    "ga4": "GA4",
    "m13_sql": "M1–3",
})
