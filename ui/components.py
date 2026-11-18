"""Shared BusinessPulse interface components.

Every page builds its layout from these, so headers, cards, notes, empty states
and status indicators look the same everywhere. Styling comes from the native
theme (.streamlit/config.toml) plus ui/styles.css; all values come from
config/design.py.

Containers get keys starting with `bp_`, which Streamlit turns into CSS classes
(`st-key-bp_...`) that ui/styles.css styles. Keys must be unique on a page.
"""

from __future__ import annotations

import base64
import html
import re
from collections import Counter
from pathlib import Path

import streamlit as st

from config import design

UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
STYLES_FILE = UI_DIR / "styles.css"
THEME_SWITCH_FILE = UI_DIR / "theme_switch.js"
LOGO_FILE = PROJECT_ROOT / "assets" / "logo.svg"
ICON_FILE = PROJECT_ROOT / "assets" / "icon.svg"

# Status kind -> native badge colour used by Streamlit's ":<colour>-badge[...]" syntax.
# The badge colours come from the theme (redColor, orangeColor, ...), which are the
# design system's status colours.
_BADGE_COLORS = {"success": "green", "warning": "orange", "error": "red", "info": "blue",
                 "neutral": "gray", "brand": "primary"}
_ALERTS = {"success": st.success, "warning": st.warning, "error": st.error, "info": st.info}
_KIND_ORDER = ("error", "warning", "info", "success")


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _svg_mask(path: Path) -> str:
    """A CSS url() holding an SVG file inline, used as a mask so the logo can be
    painted in the active theme's ink colour (an <img> can't follow the theme)."""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'url("data:image/svg+xml;base64,{encoded}")'


def render_css() -> str:
    """ui/styles.css with every {{token}} replaced by its value from config/design.py
    (plus the logo masks, built from the files in assets/)."""
    text = STYLES_FILE.read_text(encoding="utf-8")
    tokens = {**design.CSS_TOKENS, "logo_mask": _svg_mask(LOGO_FILE), "icon_mask": _svg_mask(ICON_FILE)}

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in tokens:
            raise KeyError(f"ui/styles.css uses an unknown design token: {name}")
        return tokens[name]

    return re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", substitute, text)


def inject_styles() -> None:
    """Add the design-system CSS to the page (called once per run, in app.py).

    Checked in a real browser with Streamlit 1.67: st.html strips <style> when other
    HTML is present, and a style-only st.html block goes to a temporary area where
    it doesn't persist. st.markdown with HTML allowed keeps the <style> element.
    The marker lets the CSS hide the (empty) element so it takes no space."""
    st.markdown(f'<style>{render_css()}</style><span class="bp-styles-marker"></span>', unsafe_allow_html=True)


def theme_switch_html() -> str:
    """Markup and script of the light / dark switch (see theme_switch)."""
    buttons = "".join(
        f'<button type="button" data-mode="{mode}" aria-pressed="false" title="{label} theme">'
        f'<span class="bp-theme-icon" aria-hidden="true">{icon}</span>{label}</button>'
        for mode, label, icon in (("light", "Light", "light_mode"), ("dark", "Dark", "dark_mode")))
    script = THEME_SWITCH_FILE.read_text(encoding="utf-8")
    return f'<div class="bp-theme-switch" role="group" aria-label="Theme">{buttons}</div><script>{script}</script>'


def theme_switch() -> None:
    """Light / dark switch for the sidebar. It changes Streamlit's real theme
    (widgets, tables, charts and sidebar) in the browser, without a rerun or
    reload, so uploaded data stays loaded. See ui/theme_switch.js."""
    with st.container(key="bp_theme_switch"):
        st.html(theme_switch_html(), unsafe_allow_javascript=True)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def _escape(text: str) -> str:
    return html.escape(str(text), quote=False)


def status_badge(kind: str, text: str, icon: str | None = None) -> str:
    """Markdown for a status indicator: icon + label, never colour alone.
    kind: success / warning / error / info / neutral / brand."""
    color = _BADGE_COLORS[kind]
    symbol = icon or (design.STATUS[kind][2] if kind in design.STATUS else "")
    label = f"{symbol} {text}".strip()
    return f":{color}-badge[{label}]"


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------
def page_header(title: str, description: str = "", eyebrow: str = "", actions: float = 0):
    """Page title block: serif title, one-line introduction, one ink rule below.
    With `actions` > 0, returns a column on the right (that many parts wide
    against 3 for the text) for the page's main buttons.

    `eyebrow` is accepted so existing pages keep working, but it is no longer
    shown: the navigation already says which section a page belongs to."""
    del eyebrow
    with st.container(key="bp_page_header"):
        if actions:
            text_column, action_column = st.columns([3, actions], vertical_alignment="bottom")
        else:
            text_column, action_column = st.container(), None
        with text_column:
            st.title(title, anchor=False)
            if description:
                st.markdown(f'<p class="bp-description">{_escape(description)}</p>', unsafe_allow_html=True)
    return action_column


def section_header(title: str, caption: str = "") -> None:
    st.subheader(title, anchor=False)
    if caption:
        st.caption(caption)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def kpi_card(key: str, label: str, value: str, help: str | None = None, footnote: str = "",
             status: tuple[str, str] | None = None, change: str | None = None, direction: str = "off",
             lead: bool = False) -> None:
    """A KPI tile: label, value, optional change, status badge and footnote.
    Uses Streamlit's native metric, so the help tooltip and accessibility work.
    `lead=True` marks the one headline figure of a page (ink top rule, larger value).

    `change` (e.g. "+17.4% (+12,516.24)") is shown under the value with an arrow
    (`direction`: "up", "down" or "off") in a neutral colour: a change is not
    automatically good or bad (rising expenses, falling returns...), so it is not
    coloured green or red."""
    with st.container(key=f"bp_kpi_lead_{key}" if lead else f"bp_kpi_{key}"):
        if change is None:
            st.metric(label, value, help=help)
        else:
            st.metric(label, value, delta=change, delta_color="off", delta_arrow=direction, help=help)
        if status:
            st.markdown(status_badge(*status))
        if footnote:
            st.caption(footnote)


def card(key: str):
    """A paper content card with a hairline border. Use as a context manager."""
    return st.container(key=f"bp_card_{key}")


def chart_card(key: str):
    """A card that holds one chart (and its table view)."""
    return st.container(key=f"bp_chart_{key}")


def _feature_icon(icon: str) -> None:
    """A decorative icon (e.g. ":material/upload_file:"). The marker span lets the
    CSS enlarge this icon only, not icons inside buttons or text."""
    st.markdown(f'<span class="bp-feature-icon"></span>{icon}', unsafe_allow_html=True)


def card_title(title: str, text: str = "", icon: str = "", mono: bool = False) -> None:
    """Title and one line of text inside a card. `mono=True` sets the title in the
    mono font (for file names and other technical details)."""
    if icon:
        _feature_icon(icon)
    title_class = "bp-card-title bp-mono-title" if mono else "bp-card-title"
    st.markdown(f'<p class="{title_class}">{_escape(title)}</p>'
                + (f'<p class="bp-card-text">{_escape(text)}</p>' if text else ""), unsafe_allow_html=True)


def card_label(text: str) -> None:
    """The small uppercase title at the top of a card."""
    st.markdown(f'<p class="bp-card-label">{_escape(text)}</p>', unsafe_allow_html=True)


def list_row(key: str, icon: str, title: str, text: str = ""):
    """One row of a list inside a card: icon tile, title and one line of text.
    Returns a narrow column on the right for the row's action (usually a
    chevron button). The CSS keeps the three parts side by side on phones."""
    with st.container(key=f"bp_row_{key}"):
        icon_column, text_column, action_column = st.columns([0.6, 6, 1.6], vertical_alignment="center",
                                                             gap="small")
        with icon_column:
            _feature_icon(icon)
        with text_column:
            st.markdown(f'<p class="bp-row-title">{_escape(title)}</p>'
                        + (f'<p class="bp-row-text">{_escape(text)}</p>' if text else ""), unsafe_allow_html=True)
    return action_column


def filter_bar():
    """The single row of filters above everything they affect."""
    return st.container(key="bp_filter_bar")


# ---------------------------------------------------------------------------
# Empty states and notes
# ---------------------------------------------------------------------------
def empty_state(key: str, icon: str, title: str, message: str):
    """A centred card for 'nothing here yet', with one sentence of guidance.
    Returns a centred column for the action buttons."""
    with st.container(key=f"bp_empty_{key}"):
        _feature_icon(icon)
        st.markdown(f'<p class="bp-empty-title">{_escape(title)}</p>'
                    f'<p class="bp-empty-message">{_escape(message)}</p>', unsafe_allow_html=True)
        _, middle, _ = st.columns([1, 2, 1])
    return middle


def notes_panel(items: list[tuple[str, str]], title: str = "Data notes", expanded: bool | None = None,
                quality_link: bool = True) -> None:
    """All warnings and notes for a view in one short, collapsed indicator (counts
    in its title) instead of a stack of boxes; the full texts are one click away.
    Opens by itself only for errors. items: (kind, text) with kind = error /
    warning / info / success. `quality_link` adds a button to the Data quality page."""
    items = [(kind, text) for kind, text in dict.fromkeys(items) if text]
    if not items:
        return
    counts = Counter(kind for kind, _ in items)
    names = {"error": "error", "warning": "warning", "info": "note", "success": "confirmation"}
    summary = ", ".join(f"{counts[kind]} {names[kind]}{'s' if counts[kind] != 1 else ''}"
                        for kind in _KIND_ORDER if counts[kind])
    open_panel = expanded if expanded is not None else bool(counts["error"])
    icon = ":material/report:" if counts["error"] or counts["warning"] else ":material/info:"
    with st.expander(f"{title} · {summary}", icon=icon, expanded=open_panel):
        for kind in _KIND_ORDER:
            for item_kind, text in items:
                if item_kind == kind:
                    _ALERTS[kind](text, icon=design.STATUS[kind][2])
        if quality_link and (counts["error"] or counts["warning"]):
            if st.button("Open Data quality", icon=":material/fact_check:", type="tertiary",
                         key="notes_open_quality"):
                st.switch_page("views/data_quality.py")
