"""Design-system checks: tokens, theme file, CSS, accessibility and shared components."""

import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from config import design
from ui import components as ui
from ui.accessibility import blend, color_difference, contrast_ratio, worst_cvd_difference

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
THEME = CONFIG["theme"]
STREAMLIT_JS = list((PROJECT_ROOT / ".venv" / "Lib" / "site-packages" / "streamlit" / "static" / "static" / "js")
                    .glob("*.js"))
MODES = ["light", "dark"]


# ---------------------------------------------------------------------------
# Theme file matches the design tokens (both themes)
# ---------------------------------------------------------------------------
_THEME_OPTIONS = [
    ("primaryColor", "primary"), ("backgroundColor", "canvas"), ("secondaryBackgroundColor", "surface_subtle"),
    ("textColor", "ink"), ("linkColor", "link"), ("borderColor", "border"), ("dataframeBorderColor", "divider"),
    ("dataframeHeaderBackgroundColor", "surface_subtle"),
    ("redColor", "error"), ("redTextColor", "error"), ("redBackgroundColor", "error_bg"),
    ("orangeColor", "warning"), ("orangeTextColor", "warning"), ("orangeBackgroundColor", "warning_bg"),
    ("greenColor", "success"), ("greenTextColor", "success"), ("greenBackgroundColor", "success_bg"),
    ("blueColor", "info"), ("blueTextColor", "info"), ("blueBackgroundColor", "info_bg"),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("option, token", _THEME_OPTIONS)
def test_theme_matches_tokens(mode, option, token):
    assert "base" not in THEME[mode]  # not a valid option in theme.light / theme.dark (Streamlit warns)
    assert THEME[mode][option].upper() == design.THEMES[mode][token].upper()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("option, token", [
    ("backgroundColor", "sidebar"), ("secondaryBackgroundColor", "sidebar_surface"), ("textColor", "sidebar_text"),
    ("primaryColor", "primary"), ("borderColor", "sidebar_border"),
])
def test_sidebar_theme_matches_tokens(mode, option, token):
    """The sidebar follows the theme (frosted mint in light, cosmic black in dark)."""
    assert THEME[mode]["sidebar"][option].upper() == design.THEMES[mode][token].upper()


def test_palettes_define_the_same_tokens():
    assert design.LIGHT.keys() == design.DARK.keys()


def test_theme_chart_colors_and_typography():
    assert [c.upper() for c in THEME["chartCategoricalColors"]] == [c.upper() for c in design.SERIES]
    assert "base" not in THEME  # no fixed base, so viewers can switch between the light and dark themes
    assert THEME["font"].startswith("IBM Plex Sans") and THEME["font"].endswith("sans-serif")
    assert THEME["headingFont"].startswith("IBM Plex Sans") and THEME["headingFont"].endswith("sans-serif")
    assert THEME["codeFont"].startswith("IBM Plex Mono")
    for option in ("font", "headingFont", "codeFont"):
        assert "http" not in THEME[option]  # no external fonts
    assert THEME["headingFontSizes"][0] == f"{design.FONT_SIZE_PAGE_TITLE}px"   # st.title
    assert THEME["headingFontSizes"][2] == f"{design.FONT_SIZE_SECTION}px"      # st.subheader
    assert THEME["metricValueFontSize"] == f"{design.FONT_SIZE_KPI}px"
    assert THEME["metricValueFontWeight"] == design.FONT_WEIGHT_KPI
    assert CONFIG["client"]["toolbarMode"] == "minimal"


def test_font_faces_match_tokens_and_are_bundled_with_licences():
    """Fonts are served from static/fonts on this computer (never downloaded)."""
    assert CONFIG["server"]["enableStaticServing"] is True
    faces = [(face["family"], face["weight"], face["url"]) for face in THEME["fontFaces"]]
    assert faces == [(family, weight, design.FONT_URL_PREFIX + file) for family, weight, file in design.FONT_FACES]
    for _, _, file in design.FONT_FACES:
        assert (design.FONT_DIR / file).read_bytes()[:4] == b"\x00\x01\x00\x00", f"{file} is not a TrueType font"
    for licence in design.FONT_LICENCES:
        assert "SIL Open Font License, Version 1.1" in (design.FONT_DIR / licence).read_text(encoding="utf-8")
    # The PDF's serif files stay bundled (with their licence) even though the app no longer loads them.
    for file in design.REPORT_FONT_FILES.values():
        assert (design.FONT_DIR / file).read_bytes()[:4] == b"\x00\x01\x00\x00", f"{file} is not a TrueType font"


def test_exported_reports_keep_their_own_print_palette():
    """Restyling the app must never change an exported report: the exporters only
    read the fixed REPORT_* colours, never the app theme or chart colours."""
    app_names = re.compile(r"design\.(STATUS|SUCCESS|WARNING|ERROR|INFO|LIGHT|DARK|THEMES|SERIES|"
                           r"REVENUE_COLOR|EXPENSES_COLOR|PROFIT_COLOR|LOSS_COLOR|OTHERS_COLOR)\b")
    for name in ("excel_report.py", "pdf_report.py"):
        text = (PROJECT_ROOT / "tools" / name).read_text(encoding="utf-8")
        assert not app_names.search(text), f"{name} reads an app theme colour"
    assert design.REPORT_STATUS.keys() == design.STATUS.keys()
    assert set(design.REPORT_ENTITY_COLORS) == {"revenue", "expenses", "profit", "loss", "others"}


def test_report_fonts_register_with_reportlab():
    from reportlab.pdfbase import pdfmetrics

    from tools import pdf_report  # registers the bundled fonts on import

    registered = pdfmetrics.getRegisteredFontNames()
    for name in design.REPORT_FONT_FILES:
        assert name in registered
    face = pdfmetrics.getFont(design.REPORT_FONT).face
    assert all(ord(ch) in face.charToGlyph for ch in "−÷–€£≤≥")   # symbols Helvetica couldn't show
    assert pdf_report.FONT == design.REPORT_FONT


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
def test_css_tokens_all_resolve():
    css = ui.render_css()
    assert "{{" not in css and "}}" not in css
    assert css.count("{") == css.count("}")
    # Theme-aware colours resolve per theme in the browser (sidebar included).
    assert f"light-dark({design.LIGHT['ink']}, {design.DARK['ink']})" in css
    assert f"light-dark({design.LIGHT['sidebar_surface']}, {design.DARK['sidebar_surface']})" in css
    assert css.count("data:image/svg+xml;base64,") >= 2   # logo and icon masks
    # No hover lifts and no motion (the backdrop is static).
    for retired in ("translateY(", "@keyframes", "animation:"):
        assert retired not in css
    # No images from anywhere: the backdrop is drawn with CSS gradients only.
    assert "url(http" not in css and "url('http" not in css and 'url("http' not in css


def test_glass_has_a_solid_fallback_for_phones_and_reduced_transparency():
    """Blur is costly on slow devices and can hurt readability: wherever it is used,
    phones and 'reduce transparency' get solid surfaces instead."""
    css = re.sub(r"\s+", " ", ui.render_css())
    blurred = [part.split("{")[0].strip() for part in css.split("}") if "backdrop-filter: blur" in part]
    assert blurred, "expected glass panels"
    fallback = css.split("@media (max-width: 700px), (prefers-reduced-transparency: reduce) {", 1)
    assert len(fallback) == 2, "missing the solid-surface fallback"
    block = fallback[1].split("} }", 1)[0]
    assert "backdrop-filter: none" in block
    for selector in ('[data-testid="stHeader"]', '[data-testid="stSidebar"]', 'div[class*="st-key-bp_kpi_"]'):
        assert selector in block


def test_theme_switch_hooks_exist_in_installed_streamlit():
    """The sidebar switch (ui/theme_switch.js) relies on Streamlit following the
    'system' colour scheme and re-reading it on 'afterprint'. If a Streamlit update
    removes that, this fails instead of the switch silently doing nothing."""
    bundle = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in STREAMLIT_JS)
    assert "window.addEventListener(`afterprint`," in bundle
    assert "(prefers-color-scheme: dark)" in bundle and "Custom Theme Auto" in bundle
    script = ui.THEME_SWITCH_FILE.read_text(encoding="utf-8")
    assert '"(prefers-color-scheme: dark)"' in script and '"afterprint"' in script
    assert "location.reload" not in script   # a reload would lose the uploaded data


def _theme_switch_page():
    from ui import components as ui

    ui.theme_switch()


def test_theme_switch_renders_two_labelled_buttons():
    markup = ui.theme_switch_html()
    assert 'role="group" aria-label="Theme"' in markup
    for mode, label in (("light", "Light"), ("dark", "Dark")):
        assert f'<button type="button" data-mode="{mode}" aria-pressed="false"' in markup
        assert f"</span>{label}</button>" in markup   # a visible text label, not an icon alone
    assert ui.THEME_SWITCH_FILE.read_text(encoding="utf-8") in markup
    app = AppTest.from_function(_theme_switch_page, default_timeout=30)
    app.run()
    assert not app.exception, app.exception


def test_button_hooks_exist_in_installed_streamlit():
    """Buttons get data-testid `stBaseButton-<kind>` (built from a template string)."""
    bundle = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in STREAMLIT_JS)
    assert "stBaseButton-${" in bundle
    assert "colorScheme" in bundle  # light-dark() tokens rely on Streamlit setting color-scheme


def test_css_uses_no_hardcoded_colours_except_white():
    raw = ui.STYLES_FILE.read_text(encoding="utf-8")
    colours = set(re.findall(r"#[0-9A-Fa-f]{3,8}\b", raw))
    assert colours <= {"#FFFFFF"}


def test_css_hooks_exist_in_installed_streamlit():
    """Every data-testid the CSS relies on must exist in the installed front end."""
    raw = ui.STYLES_FILE.read_text(encoding="utf-8")
    test_ids = set(re.findall(r'data-testid="([A-Za-z]+)"', raw))
    assert test_ids, "expected some data-testid hooks"
    bundle = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in STREAMLIT_JS)
    missing = [test_id for test_id in sorted(test_ids) if test_id not in bundle]
    assert not missing, f"Streamlit no longer provides: {missing}"
    assert "st-key-" in bundle  # keyed-container classes used by every bp_ component


def test_kpi_cards_fill_their_column_for_equal_heights():
    """KPI cards in a row stay the same height when footnotes differ in length
    (checked in the browser: the keyed container's wrapper must grow)."""
    css = re.sub(r"\s+", " ", ui.render_css())
    assert '[data-testid="stLayoutWrapper"]:has(> div[class*="st-key-bp_kpi_"]) { flex: 1 1 auto; }' in css
    assert 'div[class*="st-key-bp_kpi_"] { height: 100%; }' in css


def test_pages_have_no_hardcoded_colours_or_emoji():
    files = list((PROJECT_ROOT / "views").glob("*.py")) + [PROJECT_ROOT / "app.py"]
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"['\"]#[0-9A-Fa-f]{6}['\"]", text), f"hard-coded colour in {path.name}"
        assert not emoji.search(text), f"emoji in {path.name}"


# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------
_TEXT_PAIRS = [
    ("ink", "surface"), ("ink", "canvas"), ("ink", "surface_subtle"),
    ("ink_secondary", "surface"), ("ink_secondary", "canvas"),
    ("ink_muted", "surface"), ("ink_muted", "canvas"), ("ink_muted", "surface_subtle"),
    ("link", "surface"), ("link", "canvas"), ("accent_text", "surface"), ("accent_text", "canvas"),
    ("icon", "surface"),
    ("on_primary", "primary"), ("on_primary", "primary_hover"),
    ("success", "success_bg"), ("warning", "warning_bg"), ("error", "error_bg"), ("info", "info_bg"),
    ("success", "surface"), ("warning", "surface"), ("error", "surface"), ("info", "surface"),
    ("sidebar_text", "sidebar"), ("sidebar_muted", "sidebar"), ("sidebar_text", "sidebar_surface"),
    ("ink", "sidebar"), ("ink", "sidebar_surface"), ("sidebar_muted", "sidebar_surface"),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("foreground, background", _TEXT_PAIRS)
def test_text_contrast_meets_wcag_aa(mode, foreground, background):
    palette = design.THEMES[mode]
    assert contrast_ratio(palette[foreground], palette[background]) >= 4.5


_GLASS_TEXT = ["ink", "ink_secondary", "ink_muted", "link", "accent_text", "icon",
               "success", "warning", "error", "info"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("foreground", _GLASS_TEXT)
def test_text_on_glass_meets_wcag_aa_over_the_brightest_backdrop(mode, foreground):
    """Glass cards and the glass sidebar are see-through: text must still pass
    where the panel sits over the brightest point of the space backdrop."""
    palette, alpha = design.THEMES[mode], design.GLASS_ALPHA[mode]
    assert 0.6 <= alpha < 1
    card = blend(palette["surface"], palette["backdrop_glow"], alpha)
    assert contrast_ratio(palette[foreground], card) >= 4.5, (foreground, card)
    sidebar = blend(palette["sidebar"], palette["backdrop_glow"], alpha)
    for text in ("ink", "sidebar_text", "sidebar_muted"):
        assert contrast_ratio(palette[text], sidebar) >= 4.5, (text, sidebar)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("foreground", _GLASS_TEXT)
def test_text_on_lit_glass_meets_wcag_aa(mode, foreground):
    """The 3D-glass lighting (inner turquoise light, headline highlight) tints the
    card; text must still pass where that light is strongest, over the brightest
    point of the backdrop."""
    palette = design.THEMES[mode]
    card = blend(palette["surface"], palette["backdrop_glow"], design.GLASS_ALPHA[mode])
    strongest = max(design.GLASS_LIGHT_ALPHA[mode], design.HEADLINE_LIGHT_ALPHA[mode])
    assert strongest <= 0.15
    lit = blend(palette["primary"], card, strongest)
    assert contrast_ratio(palette[foreground], lit) >= 4.5, (foreground, lit)


def test_glass_lighting_uses_shadows_and_the_one_headline_gradient():
    """Gradients only in the backdrop, the logo mask and the one headline highlight."""
    raw = ui.STYLES_FILE.read_text(encoding="utf-8")
    gradient_rules = [part.rsplit("}", 1)[-1].split("{")[0].strip()
                      for part in raw.split("gradient(")[:-1] if "{" in part.rsplit("}", 1)[-1]]
    allowed = ('[data-testid="stApp"]', 'st-key-bp_kpi_lead_', 'stSidebarLogo')
    assert all(any(name in rule for name in allowed) for rule in gradient_rules), gradient_rules


@pytest.mark.parametrize("mode", MODES)
def test_text_directly_on_the_backdrop_meets_wcag_aa(mode):
    """Page titles and captions sit on the backdrop itself, without a panel."""
    palette = design.THEMES[mode]
    for backdrop in ("backdrop_glow", "backdrop_aurora", "planet"):
        for text in ("ink", "ink_secondary", "ink_muted"):
            assert contrast_ratio(palette[text], palette[backdrop]) >= 4.5, (text, backdrop)


def test_blend_reference_values():
    assert blend("#FFFFFF", "#000000", 1.0) == "#FFFFFF"
    assert blend("#FFFFFF", "#000000", 0.0) == "#000000"
    assert blend("#FFFFFF", "#000000", 0.5) == "#808080"


@pytest.mark.parametrize("mode", MODES)
def test_focus_and_selection_marks_have_3_to_1_contrast(mode):
    """The primary colour marks focus rings and the selected navigation item."""
    palette = design.THEMES[mode]
    for background in ("canvas", "surface", "sidebar_surface"):
        assert contrast_ratio(palette["primary"], palette[background]) >= 3.0


@pytest.mark.parametrize("foreground, background", [
    (design.CHART_TOOLTIP_TEXT, design.CHART_TOOLTIP_BG),
    # Exported reports: muted and copper text on white paper and on the ivory fills.
    (design.REPORT_PALETTE["ink_muted"], design.REPORT_PALETTE["paper"]),
    (design.REPORT_PALETTE["accent_text"], design.REPORT_PALETTE["paper"]),
    (design.REPORT_PALETTE["ink_muted"], design.REPORT_PALETTE["header_fill"]),
    (design.REPORT_PALETTE["ink"], design.REPORT_PALETTE["header_fill"]),
    (design.REPORT_PALETTE["primary"], design.REPORT_PALETTE["paper"]),
    *[(fg, bg) for fg, bg, _ in design.STATUS.values()],
    *[(fg, bg) for fg, bg, _ in design.REPORT_STATUS.values()],
    *[(fg, design.REPORT_PALETTE["paper"]) for fg, _, _ in design.REPORT_STATUS.values()],
])
def test_fixed_text_contrast_meets_wcag_aa(foreground, background):
    assert contrast_ratio(foreground, background) >= 4.5


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("colour", design.SERIES + [design.PROFIT_COLOR, design.LOSS_COLOR, design.OTHERS_COLOR,
                                                    design.CHART_HIGHLIGHT])
def test_chart_marks_have_3_to_1_contrast(mode, colour):
    """One chart palette serves both themes, so every mark must read on both surfaces."""
    assert contrast_ratio(colour, design.THEMES[mode]["surface"]) >= 3.0


def test_adjacent_chart_series_are_distinguishable():
    for first, second in zip(design.SERIES, design.SERIES[1:]):
        assert color_difference(first, second) >= 15, (first, second)
        worst, deficiency = worst_cvd_difference(first, second)
        assert worst >= 8, (first, second, deficiency)


@pytest.mark.parametrize("first, second", [
    (design.PROFIT_COLOR, design.LOSS_COLOR),
    (design.REVENUE_COLOR, design.EXPENSES_COLOR),
    (design.REVENUE_COLOR, design.PROFIT_COLOR),   # both can share a Dashboard view
    (design.REPORT_ENTITY_COLORS["profit"], design.REPORT_ENTITY_COLORS["loss"]),
    (design.REPORT_ENTITY_COLORS["revenue"], design.REPORT_ENTITY_COLORS["expenses"]),
])
def test_paired_chart_colours_are_distinguishable(first, second):
    assert color_difference(first, second) >= 15
    assert worst_cvd_difference(first, second)[0] >= 8


def test_contrast_formula_reference_values():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Assets and components
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [ui.LOGO_FILE, ui.ICON_FILE])
def test_logo_files_are_valid_local_svg(path):
    root = ET.parse(path).getroot()
    assert root.tag.endswith("svg")
    assert "http" not in path.read_text(encoding="utf-8").replace("http://www.w3.org/2000/svg", "")


def test_status_badges_always_have_icon_and_label():
    for kind in ("success", "warning", "error", "info"):
        badge = ui.status_badge(kind, "Label")
        assert ":material/" in badge and "Label" in badge


def _component_gallery():
    import streamlit as st

    from ui import components as ui

    actions = ui.page_header("Gallery", "Every component once.", eyebrow="Test", actions=1)
    actions.button("Action", key="header_action")
    ui.section_header("Section", "A caption")
    ui.kpi_card("one", "Revenue", "1,000.00", help="Help", footnote="Note", status=("success", "Profit"))
    ui.kpi_card("two", "Profit", "500.00", change="+5.0%", direction="up", lead=True)
    with ui.card("demo"):
        ui.card_title("Card", "Text", ":material/info:")
    with ui.chart_card("demo"):
        st.write("chart")
    with ui.filter_bar():
        st.selectbox("Filter", ["a", "b"], key="filter")
    empty_actions = ui.empty_state("demo", ":material/inbox:", "Nothing here", "Do something first.")
    empty_actions.button("Start", key="empty_action")
    ui.notes_panel([("warning", "Watch out"), ("info", "For information"), ("warning", "Watch out")])
    ui.notes_panel([])


def test_components_render_without_errors():
    app = AppTest.from_function(_component_gallery, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert app.title[0].value == "Gallery"
    assert app.metric[0].label == "Revenue" and app.metric[1].label == "Profit"
    assert [w.value for w in app.warning] == ["Watch out"]  # duplicates are shown once
    assert len(app.expander) == 1 and "1 warning, 1 note" in app.expander[0].label


def test_app_shell_shows_logo_navigation_and_dataset_card():
    app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    assert any("No data loaded" in block.value for block in app.sidebar.markdown)
