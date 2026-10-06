"""Light and dark palettes for the dashboard, and the CSS that binds them.

The stylesheet used to declare a single dark palette in ``:root`` and nothing else,
so switching Streamlit to its light theme repainted only the widgets Streamlit owns:
the cards, hero, legends and plots stayed dark and the page came out half-and-half.

Two things are needed to fix that properly, and they live together here so they
cannot drift apart:

* :func:`tokens_css` emits both token sets. Dark is the base; light overrides it,
  selected by a sentinel element whose ``data-theme`` the app sets from the live
  Streamlit theme (see :func:`sentinel_html`).
* :data:`DARK` / :data:`LIGHT` carry the same values as raw strings for Plotly,
  which cannot resolve CSS custom properties.

The light palette is chosen for contrast on a white ground rather than derived by
inverting the dark one. Inverting an amber accent collapses its luminance and it
stops reading as the accent at all.
"""
from __future__ import annotations

from typing import Any, Dict

#: Dark is the default. A dashboard that opens dark still matches an OS set to dark
#: before any script has run, so there is no flash of the wrong palette.
DARK: Dict[str, str] = {
    # Warm charcoal rather than blue-black. On a blue-leaning ground the amber accent
    # reads as a neon smear; neutralising the ground lets it read as a warm metal,
    # and the grey surfaces that follow are the same hue at different lightness, so
    # the whole ramp stays one colour instead of drifting between two.
    "bg_app": "#14161A",
    "bg_elevated": "#1B1E24",
    "bg_hero": "#23272F",
    "surface": "rgba(27,30,36,0.72)",
    "surface_hover": "rgba(36,40,48,0.85)",
    "surface_sunken": "rgba(10,11,14,0.55)",
    "line": "rgba(255,255,255,0.09)",
    "line_strong": "rgba(255,255,255,0.17)",
    "text": "#E6EBF2",
    "text_muted": "#9AA7B8",
    "text_dim": "#5B6B7E",
    "accent": "#F5B942",
    "accent_strong": "#E5A92E",
    "accent_wash": "rgba(245,185,66,0.10)",
    "on_accent": "#1A1200",
    "ok": "#34D399",
    "warn": "#F5B942",
    "danger": "#E0645A",
    "violet": "#8FA6C7",
    # Data-viz hues, one per stream. Solar owns the accent; the rest stay cool.
    "c_solar": "#F5B942",
    "c_muni": "#7FA8C9",
    "c_traction": "#8FA6C7",
    "c_charge": "#34D399",
    "c_grid": "#E0645A",
    # Plotly cannot read CSS variables, so the chart chrome is repeated here.
    "chart_template": "plotly_dark",
    "chart_plot_bg": "rgba(27,30,36,0.55)",
    "chart_grid": "rgba(148,163,184,0.10)",
    "chart_title": "#E6EBF2",
    "shadow": "0 1px 2px rgba(0,0,0,.34), 0 10px 28px rgba(0,0,0,.30)",
}

#: Light. Every text pair clears 4.5:1 on --bg-app and every non-text pair 3:1.
LIGHT: Dict[str, str] = {
    "bg_app": "#F4F6F9",
    "bg_elevated": "#FFFFFF",
    "bg_hero": "#E9EEF5",
    "surface": "rgba(255,255,255,0.88)",
    "surface_hover": "rgba(255,255,255,1)",
    "surface_sunken": "rgba(15,23,42,0.045)",
    "line": "rgba(15,23,42,0.12)",
    "line_strong": "rgba(15,23,42,0.24)",
    "text": "#10161F",
    "text_muted": "#475569",
    "text_dim": "#64748B",
    # 6.0:1 on --bg-app, so the accent still reads as the accent rather than
    # washing out, while staying legible as text.
    "accent": "#8A5A08",
    "accent_strong": "#B5760B",
    "accent_wash": "rgba(180,120,10,0.12)",
    # Near-black, not white: the light amber fill is far too light to carry a white
    # label (3.8:1), while black on it reaches 4.8:1 and reads better on amber anyway.
    "on_accent": "#10161F",
    "ok": "#047857",
    "warn": "#A16207",
    "danger": "#B42318",
    "violet": "#475569",
    "c_solar": "#A76A00",
    "c_muni": "#1F6FB2",
    "c_traction": "#4B5C7A",
    "c_charge": "#047857",
    "c_grid": "#B42318",
    "chart_template": "plotly_white",
    "chart_plot_bg": "rgba(15,23,42,0.035)",
    "chart_grid": "rgba(15,23,42,0.10)",
    "chart_title": "#10161F",
    "shadow": "0 1px 2px rgba(15,23,42,.06), 0 8px 24px rgba(15,23,42,.10)",
}

THEMES: Dict[str, Dict[str, str]] = {"dark": DARK, "light": LIGHT}

#: Keys that are emitted as CSS custom properties, mapped to their token names.
_TOKEN_NAMES = {
    "bg_app": "bg-app", "bg_elevated": "bg-elevated", "bg_hero": "bg-hero",
    "surface": "surface", "surface_hover": "surface-hover",
    "surface_sunken": "surface-sunken",
    "line": "line", "line_strong": "line-strong",
    "text": "text", "text_muted": "text-muted", "text_dim": "text-dim",
    "accent": "accent", "accent_strong": "accent-strong",
    "accent_wash": "accent-wash", "on_accent": "on-accent",
    "ok": "ok", "warn": "warn", "danger": "danger", "violet": "violet",
    "c_solar": "c-solar", "c_muni": "c-muni", "c_traction": "c-traction",
    "c_charge": "c-charge", "c_grid": "c-grid", "shadow": "shadow",
}


def resolve_theme() -> str:
    """Return the theme Streamlit is currently showing, as ``"light"`` or ``"dark"``.

    Read from the runtime rather than hard-coded, because the user controls this
    from the Streamlit menu and the page has to follow it. Falls back to dark when
    the runtime has no theme yet, which is the case under ``AppTest``.
    """
    import streamlit as st

    theme = getattr(getattr(st.context, "theme", None), "type", None)
    return "light" if str(theme).lower() == "light" else "dark"


def sentinel_html(theme: str) -> str:
    """The hidden element the CSS keys the light palette off.

    Streamlit does not put a theme attribute anywhere the stylesheet can select, so
    the app publishes the resolved theme itself. ``:has()`` then matches it from
    the root, which is what lets the token override apply to every element.
    """
    return (f'<div class="theme-sentinel" data-theme="{"light" if theme == "light" else "dark"}"'
            " hidden></div>")


def _tokens(palette: Dict[str, str], indent: str) -> str:
    body = "\n".join(f"{indent}--{name}:{palette[key]};" for key, name in _TOKEN_NAMES.items())
    return body


def tokens_css() -> str:
    """Emit the base dark palette and the light override.

    Dark lives in ``:root`` so the page is correct before any script runs. The
    light block is guarded twice: once by the app's sentinel for an explicit
    choice, and once by the OS preference for the auto setting. Both selectors
    carry the same declarations because plain CSS has no mixin to share them, and
    the explicit case has to beat the OS case when they disagree.
    """
    return f"""
:root {{
{_tokens(DARK, "  ")}
}}
/* Light palette. Two triggers, one set of values: an explicit light choice, and
   the OS preference when the app has not published a theme of its own. The second
   selector also carries a higher specificity, so an explicit choice wins. */
:root:has(.theme-sentinel[data-theme="light"]) {{
{_tokens(LIGHT, "  ")}
}}
@media (prefers-color-scheme: light) {{
  :root:not(:has(.theme-sentinel)) {{
{_tokens(LIGHT, "    ")}
  }}
}}
"""


def palette(theme: str) -> Dict[str, str]:
    """The raw colour strings for ``theme``, for use by Plotly."""
    return THEMES.get(theme, DARK)


class Palette:
    """Attribute access to one theme's colours.

    ``C.accent`` reads better at a call site than ``palette("dark")["accent"]`` and
    keeps the palette name in one place, so switching theme is a single rebind
    rather than an edit at every colour literal.
    """

    def __init__(self, theme: str) -> None:
        self.theme = theme
        self._p = palette(theme)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._p[name]
        except KeyError:  # pragma: no cover - a typo in a colour name
            raise AttributeError(name) from None
