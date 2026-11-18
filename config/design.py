"""BusinessPulse design system: "Emerald Galaxy".

The single source of truth for colours, typography, spacing and chart styling.
Everything visual reads from here: the CSS (ui/styles.css placeholders), the
chart builder (tools/charts.py), the shared components (ui/components.py) and
the exported reports. .streamlit/config.toml repeats the theme colours and font
files because Streamlit can't read Python; tests/test_design_system.py checks
that the two stay identical.

Character
- Futuristic but professional analytics: a calm space-inspired backdrop, glass
  panels with a light top edge and soft depth, one turquoise action colour, mint
  highlights and rare gold. Not a gaming or NFT interface.
- Glass is see-through enough to show the backdrop but solid enough for text:
  GLASS_ALPHA is tested so text still meets WCAG on glass laid over the
  brightest point of the backdrop ("backdrop_glow").
- No hover lifts, no animated backgrounds.

Two themes, designed separately
- Dark: cosmic black with deep emerald glass. Light: frosted mint glass over a
  pale teal sky. LIGHT and DARK hold the same keys; the CSS follows the active
  theme because every colour token is written as CSS `light-dark(<light>, <dark>)`.

Colour rules
- Ink = structure and text. Primary (turquoise) = the one action per view, focus
  and the selected navigation item. Gold accent = rare detail.
- Status colours (success / warning / error / info) are only used for status and
  always come with an icon and a label, never colour alone.
- Chart colours follow the entity, never its rank: revenue azure, expenses gold,
  profit emerald, loss coral. The same chart colours work on both themes (>= 3:1
  on both card surfaces) and stay distinguishable for colour-blind readers.
- Text meets WCAG 4.5:1 on the surfaces it is used on in each theme.

Typography (bundled, open-licence fonts in static/fonts; nothing is downloaded)
- IBM Plex Sans: interface, headings, body text and KPI values.
- IBM Plex Mono: smaller numbers and technical details (table figures, changes,
  axis ticks, timestamps, row counts, file names).
- Source Serif 4 is kept only for the printed PDF report (unchanged).
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Theme palettes (same keys in both)
# ---------------------------------------------------------------------------
LIGHT = {
    "canvas": "#EAF4F0",              # pale teal-mint sky
    "surface": "#FAFDFC",             # frosted glass: cards, popovers (opaque equivalent)
    "surface_subtle": "#E1EEEA",      # inputs, filter bar, table header
    "border": "#C6DDD6",              # 1px glass edge around cards and controls
    "divider": "#D9EAE5",             # rows inside tables and lists
    "rule": "#0B3B34",                # strong rule under page headers
    "ink": "#08241F",                 # deep emerald-black text
    "ink_secondary": "#2B4842",
    "ink_muted": "#48635D",
    "primary": "#00705F",             # turquoise (deepened for light backgrounds)
    "primary_hover": "#005A4C",
    "on_primary": "#FAFDFC",          # text on a primary button
    "link": "#00705F",
    "accent_text": "#83600F",         # gold: rare detail
    "icon": "#2B4842",
    "success": "#1B6A44", "success_bg": "#DDF1E6",
    "warning": "#7A560C", "warning_bg": "#F6EBCF",
    "error": "#A6302A", "error_bg": "#F8E1DE",
    "info": "#1D5A88", "info_bg": "#DCEAF5",
    # Sidebar (follows the theme)
    "sidebar": "#E0EFEA",
    "sidebar_surface": "#CFE6DF",     # hover and active navigation item
    "sidebar_border": "#BCD6CE",
    "sidebar_text": "#2B4842",
    "sidebar_muted": "#3F5A54",       # a step darker than ink_muted: also sits on sidebar_surface
    "backdrop_glow": "#BFE9DD",       # brightest point of the backdrop (glass contrast is tested on it)
    "shadow": "rgba(8, 36, 31, 0.14)",
}

DARK = {
    "canvas": "#050B0C",              # cosmic black, green undertone
    "surface": "#0B1918",             # deep emerald glass (opaque equivalent)
    "surface_subtle": "#112423",
    "border": "#1E3936",
    "divider": "#152D2B",
    "rule": "#2B5A54",
    "ink": "#E6F4F0",
    "ink_secondary": "#B2CBC5",
    "ink_muted": "#8AA7A0",
    "primary": "#2FE0C2",             # turquoise
    "primary_hover": "#72EED8",
    "on_primary": "#03201B",
    "link": "#5CE6D0",                # mint
    "accent_text": "#E2C26A",         # gold: rare detail
    "icon": "#9FD6CA",
    "success": "#6EE0A4", "success_bg": "#0D2A1D",
    "warning": "#F0C66A", "warning_bg": "#2A220F",
    "error": "#FF8D84", "error_bg": "#2E1416",
    "info": "#7EC6F0", "info_bg": "#0E2230",
    "sidebar": "#040A0A",
    "sidebar_surface": "#0F2120",
    "sidebar_border": "#17302E",
    "sidebar_text": "#B2CBC5",
    "sidebar_muted": "#8AA7A0",
    "backdrop_glow": "#0F3F38",       # emerald planet glow
    "shadow": "rgba(0, 0, 0, 0.55)",
}

THEMES = {"light": LIGHT, "dark": DARK}

# Opacity of glass panels (cards, sidebar, top bar) over the backdrop. Lower = more
# see-through. tests/test_design_system.py checks text contrast on glass over "backdrop_glow".
GLASS_ALPHA = {"light": 0.80, "dark": 0.78}


def _rgba(color: str, alpha: float) -> str:
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red}, {green}, {blue}, {alpha})"


# 3D glass lighting: a soft turquoise light rising from inside the lower edge of a
# panel, and the one headline highlight (lead KPI). Kept faint: tests check text on
# glass with this light added at full strength still meets WCAG 4.5:1.
GLASS_LIGHT_ALPHA = {"light": 0.10, "dark": 0.09}
HEADLINE_LIGHT_ALPHA = {"light": 0.12, "dark": 0.11}

# Backdrop and glass effects (same keys in both themes; used by ui/styles.css only).
_EFFECTS = {
    "light": {
        "backdrop_aurora": "#D3ECF2",                  # second, cooler glow (bottom left)
        "planet": "#DCF1EB",                           # large planet disc in the top-right corner
        "star": "rgba(11, 59, 52, 0.06)",              # stars barely show on the light sky
        "glass_edge": "rgba(255, 255, 255, 0.85)",     # light catching the top edge of a panel
        "glass_shade": "rgba(8, 36, 31, 0.06)",        # darker lower inner edge: the panel's thickness
        "glass_rim": _rgba(LIGHT["primary"], 0.18),    # emerald-tinted 1px edge of panels and controls
        "glass_light": _rgba(LIGHT["primary"], GLASS_LIGHT_ALPHA["light"]),
        "headline_light": _rgba(LIGHT["primary"], HEADLINE_LIGHT_ALPHA["light"]),
        "shadow_contact": "rgba(8, 36, 31, 0.10)",     # tight shadow right under a panel
        "primary_glow": _rgba(LIGHT["primary"], 0.28),
    },
    "dark": {
        "backdrop_aurora": "#0A2A35",
        "planet": "#0A2522",
        "star": "rgba(214, 255, 244, 0.32)",
        "glass_edge": "rgba(150, 255, 228, 0.14)",
        "glass_shade": "rgba(0, 0, 0, 0.32)",
        "glass_rim": _rgba(DARK["primary"], 0.16),
        "glass_light": _rgba(DARK["primary"], GLASS_LIGHT_ALPHA["dark"]),
        "headline_light": _rgba(DARK["primary"], HEADLINE_LIGHT_ALPHA["dark"]),
        "shadow_contact": "rgba(0, 0, 0, 0.45)",
        "primary_glow": _rgba(DARK["primary"], 0.40),
    },
}
for _mode, _palette in THEMES.items():
    _palette["glass"] = _rgba(_palette["surface"], GLASS_ALPHA[_mode])
    _palette["glass_sidebar"] = _rgba(_palette["sidebar"], GLASS_ALPHA[_mode])
    _palette.update(_EFFECTS[_mode])

# Light-theme names kept for code that needs one fixed value.
CANVAS, SURFACE, SURFACE_SUBTLE = LIGHT["canvas"], LIGHT["surface"], LIGHT["surface_subtle"]
BORDER, DIVIDER = LIGHT["border"], LIGHT["divider"]
INK, INK_SECONDARY, INK_MUTED = LIGHT["ink"], LIGHT["ink_secondary"], LIGHT["ink_muted"]
SUCCESS, SUCCESS_BG = LIGHT["success"], LIGHT["success_bg"]
WARNING, WARNING_BG = LIGHT["warning"], LIGHT["warning_bg"]
ERROR, ERROR_BG = LIGHT["error"], LIGHT["error_bg"]
INFO, INFO_BG = LIGHT["info"], LIGHT["info_bg"]

STATUS = {
    "success": (SUCCESS, SUCCESS_BG, ":material/check_circle:"),
    "warning": (WARNING, WARNING_BG, ":material/warning:"),
    "error": (ERROR, ERROR_BG, ":material/error:"),
    "info": (INFO, INFO_BG, ":material/info:"),
}

# Translucent tints that read on both themes (table cell highlights).
STATUS_TINT = {
    "success": "rgba(35, 139, 104, 0.24)",
    "warning": "rgba(196, 146, 40, 0.24)",
    "error": "rgba(224, 71, 41, 0.24)",
    "info": "rgba(50, 152, 226, 0.22)",
}

# ---------------------------------------------------------------------------
# Charts: one palette for both themes
# (every mark has >= 3:1 on both card surfaces; pairs that share a chart stay
# distinguishable for colour-blind readers — checked by the tests)
# ---------------------------------------------------------------------------
AZURE, GOLD, EMERALD, CORAL = "#3298E2", "#B88A1E", "#238B68", "#E04729"
REVENUE_COLOR = AZURE
EXPENSES_COLOR = GOLD
PROFIT_COLOR = EMERALD
LOSS_COLOR = CORAL
OTHERS_COLOR = "#7F9590"      # "All others": cool grey that recedes but keeps 3:1
# General categories (Streamlit's native charts); neighbours pass the colour-blind check.
SERIES = [AZURE, GOLD, "#8A7BE0", CORAL, "#1F948E", "#B66BCE"]
CHART_HIGHLIGHT = OTHERS_COLOR                   # outline of the compared bars
CHART_GRID = "rgba(127, 149, 144, 0.22)"        # horizontal gridlines, both themes
CHART_AXIS = "rgba(127, 149, 144, 0.70)"        # zero line and axis line
CHART_TOOLTIP_BG = "#06201C"                     # deep emerald tooltip in both themes
CHART_TOOLTIP_BORDER = "#1F8F7D"                 # turquoise rim, like the glass panels
CHART_TOOLTIP_TEXT = "#E6F4F0"
CHART_HATCH = "rgba(250, 253, 252, 0.70)"       # hatching on incomplete periods
CHART_LINE_WIDTH = 2
CHART_MARKER_SIZE = 5
CHART_MARKER_MAX_POINTS = 24                     # markers only on short series
CHART_BAR_RADIUS = 0                             # square bar ends: the length is the value
# Chart text is recoloured per theme by ui/styles.css (chart_text tokens).

# ---------------------------------------------------------------------------
# Typography (bundled fonts, served locally from static/fonts)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = PROJECT_ROOT / "static" / "fonts"
FONT_URL_PREFIX = "app/static/fonts/"            # where Streamlit serves static/fonts
# (family, weight, file) loaded by the app. Repeated as [[theme.fontFaces]] in .streamlit/config.toml.
# The Source Serif 4 files stay in static/fonts for the PDF report only (not loaded by the app).
FONT_FACES = [
    ("IBM Plex Sans", 400, "IBMPlexSans-Regular.ttf"),
    ("IBM Plex Sans", 500, "IBMPlexSans-Medium.ttf"),
    ("IBM Plex Sans", 600, "IBMPlexSans-SemiBold.ttf"),
    ("IBM Plex Mono", 400, "IBMPlexMono-Regular.ttf"),
    ("IBM Plex Mono", 500, "IBMPlexMono-Medium.ttf"),
]
FONT_LICENCES = ["OFL-IBM-Plex.txt", "OFL-Source-Serif-4.md"]

FONT_STACK = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif'
DISPLAY_FONT_STACK = FONT_STACK      # page titles: Plex Sans semibold, tight tracking
HEADING_FONT_STACK = FONT_STACK      # section headings
MONO_FONT_STACK = '"IBM Plex Mono", Consolas, monospace'
# Same fonts without quotation marks, for Plotly (quotes break its inline SVG styles).
CHART_FONT_STACK = "IBM Plex Sans, Segoe UI, sans-serif"
CHART_MONO_STACK = "IBM Plex Mono, Consolas, monospace"

FONT_SIZE_BASE = 15          # body text and interface, px
FONT_SIZE_LEAD = 16          # page introduction
FONT_SIZE_PAGE_TITLE = 32    # Plex Sans semibold
FONT_SIZE_SECTION = 20       # Plex Sans semibold
FONT_SIZE_CARD_TITLE = 15
FONT_SIZE_CAPTION = 13
FONT_SIZE_LABEL = 12
FONT_SIZE_KPI = 26           # IBM Plex Sans, tabular figures
FONT_SIZE_KPI_LEAD = 34      # the one headline figure on a page
FONT_SIZE_KPI_COMPACT = 22   # secondary KPI strips (e.g. Dashboard sales activity)
FONT_SIZE_MONO = 13          # table figures, changes, timestamps
FONT_SIZE_CHART_TICK = 11
FONT_WEIGHT_HEADING = 600
FONT_WEIGHT_KPI = 600
LINE_HEIGHT_BODY = 1.55
MEASURE = "62ch"             # longest comfortable line for introductions

# ---------------------------------------------------------------------------
# Spacing, shape and layout (4px grid)
# ---------------------------------------------------------------------------
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "2xl": 32, "3xl": 48, "4xl": 64}
RADIUS_CARD = 16            # glass panels
RADIUS_CONTROL = 10
RADIUS_BADGE = 999           # pill chips
CONTENT_MAX_WIDTH = 1280


# ---------------------------------------------------------------------------
# Exported reports (tools/excel_report.py, tools/pdf_report.py)
# Printed documents keep their own fixed print palette (white paper, ink text,
# ivory header fills, copper eyebrows, mineral chart pigments). It is
# deliberately independent of the app themes, so restyling the app never
# changes an exported report.
# ---------------------------------------------------------------------------
REPORT_PALETTE = {
    "paper": "#FFFFFF",
    "ink": "#1B1F24",                       # text, logo, strong rules
    "ink_secondary": "#454A52",
    "ink_muted": "#5F636A",
    "accent_text": "#8C4A2B",               # copper section eyebrows (rare detail)
    "primary": "#1F4A70",                   # links in the workbook's contents list
    "rule": "#1B1F24",                      # 1 pt rule under the title and table headers
    "border": "#DDD5C6",                    # hairlines around groups
    "divider": "#E6DFD2",                   # hairlines between table rows
    "header_fill": "#ECE6DA",               # Excel table header rows
    "panel": "#F4EFE6",                     # quiet ivory panels (report details, labels)
    "grid": "#E6DFD2",                      # chart gridlines
    "axis": "#B8B0A2",                      # chart zero line
}
# Status colours on paper: (text, fill, icon), same shape as STATUS.
REPORT_STATUS = {
    "success": ("#2D6A47", "#E2ECE3", ":material/check_circle:"),
    "warning": ("#80561A", "#F3E7CF", ":material/warning:"),
    "error": ("#A2342C", "#F5E0DB", ":material/error:"),
    "info": ("#2B5884", "#E0E8F0", ":material/info:"),
}
REPORT_ERROR = REPORT_STATUS["error"][0]
# Chart colours on paper (follow the entity, as in the app).
REPORT_ENTITY_COLORS = {"revenue": "#3B76AF", "expenses": "#A8761C", "profit": "#23807A",
                        "loss": "#C8452F", "others": "#8A857B"}
# PDF fonts: the bundled TrueType files, registered with ReportLab under these names.
REPORT_FONT = "IBMPlexSans"
REPORT_FONT_BOLD = "IBMPlexSans-SemiBold"
REPORT_FONT_SERIF = "SourceSerif4Subhead-Semibold"       # section headings
REPORT_FONT_DISPLAY = "SourceSerif4Display-Semibold"     # the report title only
REPORT_FONT_MONO = "IBMPlexMono"                         # table figures, axis numbers, timestamps
REPORT_FONT_FILES = {
    REPORT_FONT: "IBMPlexSans-Regular.ttf",
    REPORT_FONT_BOLD: "IBMPlexSans-SemiBold.ttf",
    REPORT_FONT_SERIF: "SourceSerif4Subhead-Semibold.ttf",
    REPORT_FONT_DISPLAY: "SourceSerif4Display-Semibold.ttf",
    REPORT_FONT_MONO: "IBMPlexMono-Regular.ttf",
}
# Points. Nothing smaller than 7.5 pt; body text at 9.5 pt for comfortable reading on paper.
REPORT_FONT_SIZES = {"title": 26, "section": 15, "subsection": 10.5, "body": 9.5, "small": 8, "label": 7.5,
                     "table": 8.5, "kpi": 17, "topic": 12}
REPORT_MARGIN_MM = 18
EXCEL_FONT = "Calibri"           # installed with Excel on Windows and Mac (bundled fonts usually aren't)
EXCEL_HEADING_FONT = "Cambria"   # serif sheet and section titles; also installed with Excel everywhere
EXCEL_COLUMN_MAX_WIDTH = 60


# ---------------------------------------------------------------------------
# Values substituted into ui/styles.css as {{name}}
# ---------------------------------------------------------------------------
CSS_TOKENS = {
    # Theme-aware colours: the browser picks the side that matches the active theme.
    **{name: f"light-dark({LIGHT[name]}, {DARK[name]})" for name in LIGHT},
    "chart_text": f"light-dark({LIGHT['ink_secondary']}, {DARK['ink_secondary']})",
    "chart_title": f"light-dark({LIGHT['ink']}, {DARK['ink']})",
    "font_stack": FONT_STACK, "display_font_stack": DISPLAY_FONT_STACK,
    "heading_font_stack": HEADING_FONT_STACK, "mono_font_stack": MONO_FONT_STACK,
    "font_lead": f"{FONT_SIZE_LEAD}px", "font_page_title": f"{FONT_SIZE_PAGE_TITLE}px",
    "font_section": f"{FONT_SIZE_SECTION}px", "font_card_title": f"{FONT_SIZE_CARD_TITLE}px",
    "font_label": f"{FONT_SIZE_LABEL}px", "font_caption": f"{FONT_SIZE_CAPTION}px",
    "font_kpi": f"{FONT_SIZE_KPI}px", "font_kpi_lead": f"{FONT_SIZE_KPI_LEAD}px",
    "font_kpi_compact": f"{FONT_SIZE_KPI_COMPACT}px",
    "font_mono": f"{FONT_SIZE_MONO}px",
    "weight_heading": str(FONT_WEIGHT_HEADING), "weight_kpi": str(FONT_WEIGHT_KPI),
    "line_height_body": str(LINE_HEIGHT_BODY), "measure": MEASURE,
    "radius_card": f"{RADIUS_CARD}px", "radius_control": f"{RADIUS_CONTROL}px", "radius_badge": f"{RADIUS_BADGE}px",
    "content_max_width": f"{CONTENT_MAX_WIDTH}px",
    **{f"space_{name}": f"{value}px" for name, value in SPACE.items()},
}
