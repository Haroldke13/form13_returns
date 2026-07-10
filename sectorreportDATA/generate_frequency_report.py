from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scope_operation_utils import normalize_scope_value_for_report


WORKBOOK_PATH = BASE_DIR / "NGO-Registered-Entities-2026 February_split_by_date.xlsx"
TARGET_SHEET = "01 Jul 24-30 Jun 25"
OUTPUT_DIR = BASE_DIR / "reports"


def split_multi_value(value, mode: str) -> list[str]:
    raw = "" if pd.isna(value) else str(value).strip()
    if not raw:
        return []

    values: list[str] = []
    if mode == "json_list":
        parsed = None
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(raw)
                break
            except Exception:
                continue
        if isinstance(parsed, (list, tuple, set)):
            values = [str(item).strip() for item in parsed if str(item).strip()]
        else:
            values = [item.strip() for item in re.split(r"\s*,\s*", raw) if item.strip()]
    else:
        values = [item.strip() for item in re.split(r"\s*,\s*", raw) if item.strip()]

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized = item.strip().strip('"').strip("'")
        if not normalized:
            continue
        if mode == "json_list":
            normalized = normalized.upper()
        elif mode == "comma_list":
            normalized = normalized.lower()
        if normalized not in seen:
            seen.add(normalized)
            cleaned.append(normalized)
    return cleaned


def single_value_frequency(series: pd.Series, total_entities: int) -> pd.DataFrame:
    cleaned = series.fillna("").astype(str).str.strip()
    cleaned = cleaned[cleaned != ""]
    counts = cleaned.value_counts(dropna=False)
    table = counts.rename_axis("value").reset_index(name="frequency")
    table["percentage"] = (table["frequency"] / total_entities * 100).round(2)
    return table


def multi_value_frequency(series: pd.Series, total_entities: int, mode: str) -> pd.DataFrame:
    entity_counts: dict[str, int] = {}
    total_mentions = 0
    for value in series:
        if mode == "scope_labels":
            items = normalize_scope_value_for_report("" if pd.isna(value) else str(value))
        else:
            items = split_multi_value(value, mode=mode)
        total_mentions += len(items)
        for item in items:
            entity_counts[item] = entity_counts.get(item, 0) + 1

    table = pd.DataFrame(
        [{"value": key, "frequency": value} for key, value in entity_counts.items()]
    )
    if table.empty:
        return pd.DataFrame(
            columns=["value", "frequency", "percentage_of_ngos", "percentage_of_mentions"]
        )
    table = table.sort_values(["frequency", "value"], ascending=[False, True]).reset_index(drop=True)
    table["percentage_of_ngos"] = (table["frequency"] / total_entities * 100).round(2)
    divisor = total_mentions if total_mentions else 1
    table["percentage_of_mentions"] = (table["frequency"] / divisor * 100).round(2)
    return table


def bar_chart(
    table: pd.DataFrame,
    title: str,
    percentage_column: str,
    top_n: int = 20,
    color: str = "#1f4e79",
):
    plot_df = table.head(top_n).copy()
    if plot_df.empty:
        return px.bar(title=title)
    plot_df = plot_df.iloc[::-1]
    fig = px.bar(
        plot_df,
        x="frequency",
        y="value",
        orientation="h",
        text=percentage_column,
        title=title,
    )
    fig.update_traces(marker_color=color, texttemplate="%{text}%", textposition="outside")
    fig.update_layout(
        height=max(420, 30 * len(plot_df) + 180),
        xaxis_title="Frequency",
        yaxis_title="Category",
        margin=dict(l=40, r=40, t=70, b=40),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    fig.update_xaxes(showgrid=True, gridcolor="#d9e2f3")
    fig.update_yaxes(categoryorder="total ascending")
    return fig


def build_html_report(
    total_entities: int,
    scope_table: pd.DataFrame,
    scope_of_operation_table: pd.DataFrame,
    counties_table: pd.DataFrame,
    scope_fig,
    scope_of_operation_fig,
    counties_fig,
) -> str:
    tables_payload = {
        "scope_table": scope_table.to_dict(orient="records"),
        "scope_of_operation_table": scope_of_operation_table.to_dict(orient="records"),
        "counties_table": counties_table.to_dict(orient="records"),
    }

    excel_download_name = "ngo_frequency_tables_01_jul_24_30_jun_25.xlsx"
    summary_cards = f"""
    <div class="cards">
      <div class="card"><strong>Sheet</strong><span>{TARGET_SHEET}</span></div>
      <div class="card"><strong>Total NGOs</strong><span>{total_entities}</span></div>
      <div class="card"><strong>Distinct Scope Values</strong><span>{len(scope_table)}</span></div>
      <div class="card"><strong>Distinct Scope Of Operation Tags</strong><span>{len(scope_of_operation_table)}</span></div>
      <div class="card"><strong>Distinct Counties Of Operation</strong><span>{len(counties_table)}</span></div>
    </div>
    """

    def table_html(df: pd.DataFrame, title: str, table_id: str) -> str:
        if df.empty:
            return f"<h3>{title}</h3><p>No data available.</p>"
        return f"<h3>{title}</h3>{df.to_html(index=False, classes='report-table', table_id=table_id)}"

    def section_html(title: str, note: str, chart_id: str, chart_html: str, table_id: str, table_key: str, table_markup: str, image_name: str, csv_name: str) -> str:
        return f"""
    <div class="section">
      <div class="section-head">
        <div>
          <h2>{title}</h2>
          <p class="note">{note}</p>
        </div>
        <div class="actions">
          <button class="btn" onclick="downloadPlot('{chart_id}', '{image_name}')">Download Chart PNG</button>
          <button class="btn btn-secondary" onclick="downloadTableCsv('{table_key}', '{csv_name}')">Download Table CSV</button>
        </div>
      </div>
      {chart_html}
      {table_markup}
    </div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Frequency Analysis Report</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 24px;
      background: #f4f7fb;
      color: #10233f;
    }}
    .wrap {{
      max-width: 1400px;
      margin: 0 auto;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
      margin: 20px 0 28px;
    }}
    .card {{
      background: white;
      border: 1px solid #d9e2f3;
      border-radius: 12px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .card strong {{
      font-size: 0.85rem;
      color: #365072;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .card span {{
      font-size: 1.5rem;
      font-weight: 700;
    }}
    .section {{
      background: white;
      border: 1px solid #d9e2f3;
      border-radius: 14px;
      padding: 18px;
      margin-bottom: 22px;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      margin-bottom: 12px;
      flex-wrap: wrap;
    }}
    .section h2 {{
      margin: 0 0 6px;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .btn {{
      border: 1px solid #1f4e79;
      background: #1f4e79;
      color: white;
      border-radius: 8px;
      padding: 9px 14px;
      cursor: pointer;
      font-size: 0.92rem;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .btn:hover {{
      background: #163a5c;
      border-color: #163a5c;
    }}
    .btn-secondary {{
      background: white;
      color: #1f4e79;
    }}
    .btn-secondary:hover {{
      background: #eef4fb;
      border-color: #1f4e79;
      color: #1f4e79;
    }}
    .top-actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 14px 0 24px;
    }}
    .report-table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 0.92rem;
      margin-top: 10px;
    }}
    .report-table th, .report-table td {{
      border: 1px solid #d9e2f3;
      padding: 8px 10px;
      text-align: left;
    }}
    .report-table th {{
      background: #1f4e79;
      color: white;
    }}
    .note {{
      color: #4b5f7a;
      margin-bottom: 10px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Frequency Analysis Report</h1>
    <p class="note">This report covers the <strong>{TARGET_SHEET}</strong> sheet from the NGO workbook. For multi-value variables, percentages are shown as the share of NGOs mentioning each category, and the detailed tables also include the share of total mentions.</p>
    <div class="top-actions">
      <a class="btn" href="{excel_download_name}" download>Download All Tables Workbook</a>
    </div>
    {summary_cards}
    {section_html(
        title="Scope Frequency",
        note="Distribution of NGO registration scope values for the target sheet.",
        chart_id="scope-chart",
        chart_html=scope_fig.to_html(full_html=False, include_plotlyjs='cdn', div_id='scope-chart'),
        table_id="scope-table",
        table_key="scope_table",
        table_markup=table_html(scope_table, "Scope Frequency Table", "scope-table"),
        image_name="scope_frequency_01_jul_24_30_jun_25",
        csv_name="scope_frequency_01_jul_24_30_jun_25.csv",
    )}
    {section_html(
        title="Scope Of Operation Frequency",
        note="Sector labels extracted from the objective text and standardized to the approved sector list.",
        chart_id="scope-operation-chart",
        chart_html=scope_of_operation_fig.to_html(full_html=False, include_plotlyjs=False, div_id='scope-operation-chart'),
        table_id="scope-operation-table",
        table_key="scope_of_operation_table",
        table_markup=table_html(scope_of_operation_table, "Scope Of Operation Frequency Table", "scope-operation-table"),
        image_name="scope_of_operation_frequency_01_jul_24_30_jun_25",
        csv_name="scope_of_operation_frequency_01_jul_24_30_jun_25.csv",
    )}
    {section_html(
        title="Counties Of Operation Frequency",
        note="Frequency of counties mentioned in the counties of operation field.",
        chart_id="counties-chart",
        chart_html=counties_fig.to_html(full_html=False, include_plotlyjs=False, div_id='counties-chart'),
        table_id="counties-table",
        table_key="counties_table",
        table_markup=table_html(counties_table, "Counties Of Operation Frequency Table", "counties-table"),
        image_name="counties_of_operation_frequency_01_jul_24_30_jun_25",
        csv_name="counties_of_operation_frequency_01_jul_24_30_jun_25.csv",
    )}
  </div>
  <script>
    const reportTables = {json.dumps(tables_payload)};

    function downloadPlot(chartId, filename) {{
      const plot = document.getElementById(chartId);
      if (!plot || typeof Plotly === 'undefined') {{
        return;
      }}
      Plotly.downloadImage(plot, {{
        format: 'png',
        filename: filename,
        width: 1600,
        height: 900,
        scale: 2
      }});
    }}

    function csvEscape(value) {{
      if (value === null || value === undefined) {{
        return '';
      }}
      const text = String(value);
      if (/[",\\n]/.test(text)) {{
        return '"' + text.replace(/"/g, '""') + '"';
      }}
      return text;
    }}

    function downloadTableCsv(tableKey, filename) {{
      const rows = reportTables[tableKey] || [];
      if (!rows.length) {{
        return;
      }}
      const headers = Object.keys(rows[0]);
      const csvLines = [
        headers.map(csvEscape).join(','),
        ...rows.map((row) => headers.map((header) => csvEscape(row[header])).join(','))
      ];
      const csvContent = csvLines.join('\\n');
      const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(link.href);
    }}
  </script>
</body>
</html>
"""


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_excel(WORKBOOK_PATH, sheet_name=TARGET_SHEET)
    total_entities = len(df)

    scope_table = single_value_frequency(df["scope"], total_entities=total_entities)
    scope_of_operation_table = multi_value_frequency(
        df["SCOPE OF OPERATION"],
        total_entities=total_entities,
        mode="scope_labels",
    )
    counties_table = multi_value_frequency(
        df["counties_of_operation"],
        total_entities=total_entities,
        mode="json_list",
    )

    scope_fig = bar_chart(
        scope_table,
        title="Scope Frequency and Percentage",
        percentage_column="percentage",
        top_n=len(scope_table),
        color="#1f4e79",
    )
    scope_of_operation_fig = bar_chart(
        scope_of_operation_table,
        title="Top Scope Of Operation Tags",
        percentage_column="percentage_of_ngos",
        top_n=20,
        color="#2f7d4a",
    )
    counties_fig = bar_chart(
        counties_table,
        title="Top Counties Of Operation",
        percentage_column="percentage_of_ngos",
        top_n=20,
        color="#b25d1e",
    )

    html_path = OUTPUT_DIR / "ngo_frequency_report_01_jul_24_30_jun_25.html"
    html_path.write_text(
        build_html_report(
            total_entities=total_entities,
            scope_table=scope_table,
            scope_of_operation_table=scope_of_operation_table,
            counties_table=counties_table,
            scope_fig=scope_fig,
            scope_of_operation_fig=scope_of_operation_fig,
            counties_fig=counties_fig,
        ),
        encoding="utf-8",
    )

    excel_path = OUTPUT_DIR / "ngo_frequency_tables_01_jul_24_30_jun_25.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        scope_table.to_excel(writer, index=False, sheet_name="scope")
        scope_of_operation_table.to_excel(writer, index=False, sheet_name="scope_of_operation")
        counties_table.to_excel(writer, index=False, sheet_name="counties_of_operation")

    print(html_path)
    print(excel_path)
    print("Top scope values:")
    print(scope_table.head(10).to_string(index=False))
    print("Top scope of operation values:")
    print(scope_of_operation_table.head(10).to_string(index=False))
    print("Top counties of operation values:")
    print(counties_table.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
