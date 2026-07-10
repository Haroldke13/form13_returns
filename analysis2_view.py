from flask import render_template, Blueprint
from flask_login import login_required
from models import PBOReport
import plotly.express as px
import plotly
from collections import defaultdict
from typing import Any, Dict

analysis2_bp = Blueprint('analysis2', __name__)


def format_date(value):
    return value.strftime("%d/%m/%y") if value else ""


def get_pandas():
    import pandas as pandas_module
    return pandas_module


def fetch_pbo_data():
    """Fetch all PBO reports and convert to DataFrame including expanded fields."""
    pd = get_pandas()
    reports = PBOReport.query.all()
    data = []
    for r in reports:
        # Assets, donations, staff counts
        assets_total = sum((a.value or 0) for a in r.assets)
        donations_total = sum((d.amount or 0) for d in r.donations)
        staff_count = (r.staff_kenyan_current or 0) + (r.staff_foreign_current or 0)
        projects_count = len(r.project_implementations)
        officials_count = len(r.officials)

        row = {
            'id': r.id,
            'pbo_name': r.pbo_name,
            'scope': r.scope.title() if r.scope else 'N/A',
            'counties': r.counties,
            'categories': r.scope.title() if r.scope else 'N/A',
            'reporting_period_start': format_date(r.reporting_period_start),
            'reporting_period_end': format_date(r.reporting_period_end),
            'registration_date': format_date(r.pbo_registration_date or r.date_of_registration),
            'assets': assets_total,
            'cash_balance_previous_year': r.cash_balance_previous_year or 0,
            'donations': donations_total,
            'audited': r.audited,
            'staff_count': staff_count,
            'projects_count': projects_count,
            'officials_count': officials_count,
            'number_of_directors': r.membership_number_of_directors or 0,
            'number_of_registered_members': r.membership_number_of_registered_members or 0,
            'number_of_board_meetings': r.membership_number_of_board_meetings or 0,
        }
        data.append(row)

    df = pd.DataFrame(data)
    return df

# -------------------------
# Analysis Functions
# -------------------------

def analyze_counties(df):
    """Analyze PBO reports by county and return chart HTML."""
    pd = get_pandas()
    county_counts = defaultdict(int)
    for counties in df['counties'].dropna():
        for county in str(counties).split(','):
            county_counts[county.strip()] += 1

    county_df = pd.DataFrame(list(county_counts.items()), columns=['County', 'Count']).sort_values('Count', ascending=False)
    fig = px.bar(county_df, x='County', y='Count', title='PBO Reports by County', text='Count')
    fig.update_traces(marker_color='royalblue', textposition='outside')
    return county_df, plotly.io.to_html(fig, full_html=False)

def analyze_categories(df) -> str:
    """Visualize category distribution."""
    # Flatten categories
    all_categories = df['categories'].dropna().str.split(', ').explode()
    cat_counts = all_categories.value_counts().reset_index()
    cat_counts.columns = ['Category', 'Count']
    fig = px.pie(cat_counts, names='Category', values='Count', title='PBOs by Category')
    return plotly.io.to_html(fig, full_html=False)

def financial_analysis(df) -> str:
    """Generate financial overview Plotly figure HTML."""
    fin_df = df[['pbo_name', 'assets', 'cash_balance_previous_year', 'donations']]
    fig = px.bar(fin_df, x='pbo_name', y=['assets', 'cash_balance_previous_year', 'donations'],
                 title='Financial Overview by PBO', barmode='group', text_auto=True)
    fig.update_layout(xaxis_title='PBO Name', yaxis_title='Amount', legend_title='Financials')
    return plotly.io.to_html(fig, full_html=False)

def governance_analysis(df) -> str:
    """Visualize governance metrics (directors, members, board meetings)."""
    gov_df = df[['pbo_name', 'number_of_directors', 'number_of_registered_members', 'number_of_board_meetings']]
    fig = px.bar(gov_df, x='pbo_name', y=['number_of_directors', 'number_of_registered_members', 'number_of_board_meetings'],
                 title='Governance Metrics by PBO', barmode='group', text_auto=True)
    return plotly.io.to_html(fig, full_html=False)

def summarize(df, county_df) -> Dict[str, Any]:
    """Generate summary statistics including totals and top performers."""
    top_county = county_df.iloc[0]['County'] if not county_df.empty else 'N/A'
    return {
        'total_pbos': len(df),
        'total_assets': df['assets'].sum(),
        'total_donations': df['donations'].sum(),
        'total_cash': df['cash_balance_previous_year'].sum(),
        'total_staff': df['staff_count'].sum(),
        'total_projects': df['projects_count'].sum(),
        'top_county': top_county
    }

# -------------------------
# Route
# -------------------------

@analysis2_bp.route('/analysis2')
@login_required
def analysis2():
    df = fetch_pbo_data()

    county_df, county_graph = analyze_counties(df)
    category_graph = analyze_categories(df)
    fin_graph = financial_analysis(df)
    gov_graph = governance_analysis(df)
    summary = summarize(df, county_df)

    return render_template(
        'analysis2.html',
        summary=summary,
        county_graph=county_graph,
        category_graph=category_graph,
        fin_graph=fin_graph,
        gov_graph=gov_graph,
        table=df.to_html(classes='table table-striped', index=False)
    )
