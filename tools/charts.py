"""Plotly charts for the dashboard (no Streamlit code here).

All colours, fonts and line weights come from config/design.py, so every chart
matches the BusinessPulse design system.

Design rules (from the data-visualisation guidelines):
- One y-axis per chart. Revenue and expenses share one axis because they are the
  same unit (money); profit gets its own chart.
- Colour follows the entity, never its rank (mineral pigments): revenue lapis,
  expenses ochre, profit verdigris, loss cinnabar, with a neutral zero line.
- Restrained marks: 2px straight lines, small markers only on short series,
  square bar ends (the bar's length is the value), horizontal hairline grid,
  no fills or decoration. Labels in IBM Plex Sans, axis numbers in IBM Plex Mono,
  text in ink colours (never the series colour), tooltips on every mark.
- Every chart has a table view on the page; tooltips never gate a value.
- Works on both themes: marks, grid and tooltip colours are chosen to read on
  light and dark surfaces; ui/styles.css recolours the chart text per theme.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from config import design
from tools.business_metrics import FREQUENCIES

FONT = design.CHART_FONT_STACK
MONO = design.CHART_MONO_STACK
TICK_SIZE = design.FONT_SIZE_CHART_TICK
LINE_WIDTH = design.CHART_LINE_WIDTH
MARKER_SIZE = design.CHART_MARKER_SIZE
MARKER_MAX_POINTS = design.CHART_MARKER_MAX_POINTS
SURFACE = design.SURFACE
INK = design.INK
INK_SECONDARY = design.INK_SECONDARY
INK_MUTED = design.INK_MUTED
GRID = design.CHART_GRID
AXIS = design.CHART_AXIS
SERIES_COLORS = {"Revenue": design.REVENUE_COLOR, "Expenses": design.EXPENSES_COLOR}
PROFIT_COLOR = design.PROFIT_COLOR
LOSS_COLOR = design.LOSS_COLOR
OTHERS_COLOR = design.OTHERS_COLOR
BAR_COLOR = design.REVENUE_COLOR
HIGHLIGHT = design.CHART_HIGHLIGHT
HATCH = design.CHART_HATCH
BAR_RADIUS = design.CHART_BAR_RADIUS


def money_axis_title(currency: str | None) -> str:
    return f"Amount ({currency})" if currency else "Amount (currency not specified)"


def _money_format(currency: str | None) -> str:
    return f"{currency} %{{y:,.2f}}" if currency else "%{y:,.2f}"


def _base(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=24, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",   # the chart card provides the surface
        font=dict(family=FONT, size=13, color=INK_SECONDARY),
        hoverlabel=dict(bgcolor=design.CHART_TOOLTIP_BG, bordercolor=design.CHART_TOOLTIP_BORDER,
                        font=dict(family=FONT, size=13, color=design.CHART_TOOLTIP_TEXT)),
        showlegend=False,
    )
    # automargin: grow the margins to fit the tick labels. It is off by default here
    # (checked in the browser), which cut category names and dates off the charts.
    # Axis numbers and dates use the mono face so digits line up.
    ticks = dict(family=MONO, size=TICK_SIZE, color=INK_MUTED)
    fig.update_xaxes(showgrid=False, linecolor=AXIS, ticks="", tickfont=ticks, automargin=True)
    fig.update_yaxes(gridcolor=GRID, gridwidth=1, zeroline=False, showline=False, tickfont=ticks, automargin=True)
    return fig


def _period_format(frequency: str) -> str:
    return {"D": "%d %b %Y", "W": "Week of %d %b %Y", "M": "%b %Y", "Q": "Q%q %Y"}.get(frequency, "%d %b %Y")


def time_chart(table: pd.DataFrame, frequency: str, currency: str | None) -> go.Figure | None:
    """Revenue and/or expenses per period on one money axis."""
    series = [name for name in ("Revenue", "Expenses") if name in table.columns]
    if table.empty or not series:
        return None
    fig = go.Figure()
    show_markers = len(table) <= MARKER_MAX_POINTS
    for name in series:
        fig.add_trace(go.Scatter(
            x=table["Period"], y=table[name], name=name, mode="lines+markers" if show_markers else "lines",
            line=dict(color=SERIES_COLORS[name], width=LINE_WIDTH),   # straight segments: smoothing would invent peaks
            marker=dict(size=MARKER_SIZE, color=SERIES_COLORS[name], line=dict(width=0)),
            hovertemplate=f"<b>{name}</b>: {_money_format(currency)}<extra></extra>",
        ))
    # Two series: a legend names them (end-of-line labels collided when the lines finish close together).
    # One series: the chart title on the page names it, so no legend is needed.
    _base(fig, 360)
    fig.update_layout(hovermode="x unified", showlegend=len(series) > 1,
                      legend=dict(orientation="h", y=1.1, x=0, font=dict(color=INK_SECONDARY)))
    fig.update_xaxes(hoverformat=_period_format(frequency), showspikes=True, spikecolor=AXIS, spikethickness=1,
                     spikedash="solid", spikemode="across")
    fig.update_yaxes(title=dict(text=money_axis_title(currency), font=dict(color=INK_MUTED, size=12)),
                     rangemode="tozero", tickformat=",.0f")
    return fig


def profit_chart(table: pd.DataFrame, frequency: str, currency: str | None) -> go.Figure | None:
    """Profit per period: verdigris bars for profit, cinnabar bars for a loss."""
    if table.empty or "Profit" not in table.columns or table["Profit"].dropna().empty:
        return None
    data = table.dropna(subset=["Profit"])
    colors = [PROFIT_COLOR if value >= 0 else LOSS_COLOR for value in data["Profit"]]
    labels = ["Profit" if value >= 0 else "Loss" for value in data["Profit"]]
    fig = go.Figure(go.Bar(
        x=data["Period"], y=data["Profit"], marker=dict(color=colors, cornerradius=BAR_RADIUS), customdata=labels,
        hovertemplate=f"<b>%{{customdata}}</b>: {_money_format(currency)}<br>%{{x|{_period_format(frequency)}}}"
                      "<extra></extra>",
    ))
    _base(fig, 320)
    fig.update_layout(bargap=0.35)
    fig.update_yaxes(title=dict(text=money_axis_title(currency), font=dict(color=INK_MUTED, size=12)),
                     zeroline=True, zerolinecolor=AXIS, zerolinewidth=1, tickformat=",.0f")
    return fig


def ranked_bar_chart(table: pd.DataFrame, value_label: str, currency: str | None,
                     color: str = BAR_COLOR) -> go.Figure | None:
    """Horizontal bars, largest at the top, in the colour of the entity being
    measured (revenue or expenses). The combined 'All others' bar is grey so it
    doesn't compete with real items."""
    if table.empty:
        return None
    colors = [OTHERS_COLOR if str(name).startswith("All others") else color for name in table["Name"]]
    custom = table[["Share", "Rows"]].copy()
    custom["Units"] = table["Units"] if "Units" in table else float("nan")
    units_line = "<br>Units: %{customdata[2]:,.0f}" if "Units" in table else ""
    money = f"{currency} %{{x:,.2f}}" if currency else "%{x:,.2f}"
    fig = go.Figure(go.Bar(
        x=table["Amount"], y=table["Name"], orientation="h", marker=dict(color=colors, cornerradius=BAR_RADIUS),
        customdata=custom.to_numpy(),
        hovertemplate=f"<b>%{{y}}</b><br>{value_label}: {money}<br>Share: %{{customdata[0]:.1f}}%"
                      f"<br>Rows: %{{customdata[1]:,.0f}}{units_line}<extra></extra>",
    ))
    _base(fig, 70 + 34 * len(table))
    fig.update_layout(bargap=0.3, margin=dict(l=8, r=24, t=16, b=8))
    # Names are words, not numbers: interface font, with a little space before the bar.
    fig.update_yaxes(autorange="reversed", showgrid=False, title=None, ticklabelstandoff=8,
                     tickfont=dict(family=FONT, size=12, color=INK_SECONDARY))
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformat=",.0f", rangemode="tozero",
                     title=dict(text=money_axis_title(currency), font=dict(color=INK_MUTED, size=12)))
    return fig


def period_chart(table: pd.DataFrame, value_label: str, kind: str, currency: str | None, color: str | None,
                 marked: dict[str, str] | None = None) -> go.Figure | None:
    """One bar per period (Comparisons page). `table` has columns Period, Value,
    Status and Complete (bool).

    - Colour follows the entity (revenue lapis, expenses ochre...). `color=None`
      means profit: verdigris for a profit, cinnabar for a loss.
    - Periods that are not complete (partial, not finished, possible missing data)
      are hatched as well as labelled, so the difference never relies on colour.
    - Periods without data get no bar (never a zero bar); the tooltip says why.
    - `marked` ({period label: text}) labels the two periods being compared."""
    if table.empty or table["Value"].dropna().empty:
        return None
    values = table["Value"].astype(float)
    if color is None:
        colors = [PROFIT_COLOR if (pd.isna(v) or v >= 0) else LOSS_COLOR for v in values]
    else:
        colors = [color] * len(table)
    marked = marked or {}
    shapes = ["" if complete else "/" for complete in table["Complete"]]
    if kind == "money":
        value_format = f"{currency} %{{y:,.2f}}" if currency else "%{y:,.2f}"
        axis_title, tick_format = money_axis_title(currency), ",.0f"
    elif kind == "percent":
        value_format, axis_title, tick_format = "%{y:.1f}%", f"{value_label} (%)", ".1f"
    else:
        value_format, axis_title, tick_format = "%{y:,.0f}", value_label, ",.0f"
    status = [s if pd.notna(v) else f"{s} (no figure)" for s, v in zip(table["Status"], values)]
    fig = go.Figure(go.Bar(
        x=table["Period"], y=values,
        marker=dict(color=colors, cornerradius=BAR_RADIUS,
                    line=dict(color=[HIGHLIGHT if p in marked else "rgba(0,0,0,0)" for p in table["Period"]],
                              width=2),
                    pattern=dict(shape=shapes, fgcolor=HATCH, size=7, solidity=0.35, fillmode="overlay")),
        customdata=status,
        hovertemplate=f"<b>%{{x}}</b><br>{value_label}: {value_format}<br>%{{customdata}}<extra></extra>",
    ))
    # Labels sit above the bars; the second one is raised further so the two never overlap
    # when the compared periods are next to each other at a similar height.
    # Annotations are placed by the bar's position, not its name: on a category axis Plotly
    # reads a name that looks like a number (a year such as "2025") as a position, which
    # stretched the axis to thousands of empty slots (found in the browser).
    periods = list(table["Period"])
    for position, (period, text) in enumerate(marked.items()):
        if period in periods:
            index = periods.index(period)
            y = values.iloc[index]
            fig.add_annotation(x=index, y=0 if pd.isna(y) else max(y, 0), text=text, showarrow=False,
                               yshift=12 + 18 * position, font=dict(color=INK, size=12))
    _base(fig, 340)
    fig.update_layout(bargap=0.3)
    fig.update_xaxes(type="category")
    fig.update_yaxes(title=dict(text=axis_title, font=dict(color=INK_MUTED, size=12)), tickformat=tick_format,
                     zeroline=True, zerolinecolor=AXIS, zerolinewidth=1,
                     rangemode="tozero" if values.dropna().min() >= 0 else "normal")
    return fig


def trend_chart(table: pd.DataFrame, column: str, kind: str, currency: str | None, frequency: str,
                color: str = BAR_COLOR) -> go.Figure | None:
    """One metric per period as a line (Performance page). Missing figures (NaN)
    leave a gap in the line; they are never drawn as 0."""
    if table.empty or column not in table or table[column].dropna().empty:
        return None
    if kind == "money":
        value_format, axis_title, tick_format = _money_format(currency), money_axis_title(currency), ",.0f"
    else:
        value_format, axis_title, tick_format = "%{y:,.0f}", column, ",.0f"
    fig = go.Figure(go.Scatter(
        x=table["Period"], y=table[column], mode="lines+markers" if len(table) <= MARKER_MAX_POINTS else "lines",
        line=dict(color=color, width=LINE_WIDTH), marker=dict(size=MARKER_SIZE, color=color, line=dict(width=0)),
        connectgaps=False,
        hovertemplate=f"<b>{column}</b>: {value_format}<br>%{{x|{_period_format(frequency)}}}<extra></extra>",
    ))
    _base(fig, 340)
    fig.update_yaxes(title=dict(text=axis_title, font=dict(color=INK_MUTED, size=12)), rangemode="tozero",
                     tickformat=tick_format)
    return fig


def comparison_bars(items: list[tuple[str, float | None, float | None, str]], current_label: str,
                    comparison_label: str, currency: str | None) -> go.Figure | None:
    """Money figures of two periods side by side (Comparisons page), on one money axis.
    items: (figure name, this period, comparison period, entity colour). The current
    period is a solid bar, the comparison period an outlined bar in the same colour, so
    the two are told apart by shape and the legend, never by colour alone. A negative
    profit is drawn in the loss colour. Missing values get no bar (never a zero bar)."""
    items = [item for item in items if item[1] is not None or item[2] is not None]
    if not items:
        return None
    names = [name for name, *_ in items]

    def colour(value, base):
        return LOSS_COLOR if base == PROFIT_COLOR and value is not None and value < 0 else base

    money = f"{currency} %{{x:,.2f}}" if currency else "%{x:,.2f}"
    fig = go.Figure()
    for label, index, solid in ((current_label, 1, True), (comparison_label, 2, False)):
        values = [item[index] for item in items]
        colours = [colour(value, item[3]) for value, item in zip(values, items)]
        fig.add_trace(go.Bar(
            x=values, y=names, orientation="h", name=label,
            marker=dict(color=colours if solid else "rgba(0,0,0,0)", cornerradius=BAR_RADIUS,
                        line=dict(color=colours, width=0 if solid else 2)),
            hovertemplate=f"<b>%{{y}}</b> · {label}<br>{money}<extra></extra>",
        ))
    _base(fig, 110 + 62 * len(items))
    # The legend sits above the plot area (anchored by its bottom edge), so it never covers a bar.
    fig.update_layout(barmode="group", bargap=0.32, bargroupgap=0.12, showlegend=True,
                      legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, traceorder="normal",
                                  font=dict(color=INK_SECONDARY)),
                      margin=dict(l=8, r=24, t=44, b=8))
    fig.update_yaxes(autorange="reversed", showgrid=False, title=None, ticklabelstandoff=8,
                     tickfont=dict(family=FONT, size=12, color=INK_SECONDARY))
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformat=",.0f", zeroline=True, zerolinecolor=AXIS,
                     zerolinewidth=1, title=dict(text=money_axis_title(currency), font=dict(color=INK_MUTED, size=12)))
    return fig


def period_label(frequency: str) -> str:
    return FREQUENCIES.get(frequency, "Period")
