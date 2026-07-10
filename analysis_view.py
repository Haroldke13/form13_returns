from collections import Counter
from datetime import datetime
import re

from flask import Blueprint, current_app, render_template, request, session
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from models import Asset, Donation, Official, Payment, PBOReport, ProjectImplementation, db

analysis_bp = Blueprint('analysis', __name__)

_ANALYSIS_CACHE = {
    'key': None,
    'payload': None,
    'cached_at': None,
}
_ANALYSIS_CACHE_TTL_SECONDS = 180
_ANALYSIS_CACHE_VERSION = 4
KNOWN_COUNTIES = {
    'BARINGO', 'BOMET', 'BUNGOMA', 'BUSIA', 'ELGEYO MARAKWET', 'EMBU', 'GARISSA', 'HOMA BAY',
    'ISIOLO', 'KAJIADO', 'KAKAMEGA', 'KERICHO', 'KIAMBU', 'KILIFI', 'KIRINYAGA', 'KISII',
    'KISUMU', 'KITUI', 'KWALE', 'LAIKIPIA', 'LAMU', 'MACHAKOS', 'MAKUENI', 'MANDERA', 'MARSABIT',
    'MERU', 'MIGORI', 'MOMBASA', 'MURANGA', 'NAIROBI', 'NAKURU', 'NANDI', 'NAROK', 'NYAMIRA',
    'NYANDARUA', 'NYERI', 'SAMBURU', 'SIAYA', 'TAITA TAVETA', 'TANA RIVER', 'THARAKA NITHI',
    'TRANS NZOIA', 'TURKANA', 'UASIN GISHU', 'VIHIGA', 'WAJIR', 'WEST POKOT',
}


def get_analysis_dependencies():
    import plotly.graph_objects as go_module
    return go_module


def format_date(value):
    return value.strftime('%d/%m/%Y') if value else ''


def format_currency(value):
    return f"KES {float(value or 0):,.2f}"


def user_can_manage_all_records(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    role_value = str(getattr(user, 'role', '') or '').strip().lower()
    return bool(
        getattr(user, 'is_superadmin', False)
        or getattr(user, 'can_manage_all_records', False)
        or role_value == 'admin'
    )


def scoped_reports_query():
    query = PBOReport.query
    if not user_can_manage_all_records(current_user):
        user_id = getattr(current_user, 'id', None)
        if user_id is None:
            return query.filter(PBOReport.id == -1)
        query = query.filter(PBOReport.user_id == user_id)
    return query


def scoped_report_ids_subquery():
    return scoped_reports_query().with_entities(PBOReport.id.label('report_id')).subquery()


def split_counties(raw_value):
    if not raw_value:
        return []
    return [part.strip() for part in str(raw_value).split(',') if part.strip()]


def truncate_label(value, max_len=22):
    text = str(value or '').strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + '…'


def clean_scope_name(raw_scope):
    text = str(raw_scope or '').strip().lower()
    if not text:
        return 'Unspecified'
    if text in {'1', '1.0', 'national', 'n'}:
        return 'National'
    if text in {'2', '2.0', 'international', 'i'}:
        return 'International'
    if text in {'nan', 'none', 'null'}:
        return 'Unspecified'
    if 'international' in text:
        return 'International'
    if 'national' in text:
        return 'National'
    if 'sub' in text and 'county' in text:
        return 'Sub County'
    if 'county' in text:
        return 'County'
    if 'constituency' in text:
        return 'Constituency'
    if 'ward' in text:
        return 'Ward'
    return truncate_label(text.title(), 24) or 'Unspecified'


def clean_county_name(raw_county):
    text = str(raw_county or '').strip().upper()
    if not text:
        return None
    text = re.sub(r'[^A-Z\\-\\s]', ' ', text)
    text = re.sub(r'\\s+', ' ', text).strip()
    if not text or len(text) > 28:
        return None
    if text in KNOWN_COUNTIES:
        return text.title()
    return None


def clean_sector_name(raw_sector):
    text = str(raw_sector or '').strip()
    if not text:
        return 'Unspecified'
    text = re.sub(r'\\s+', ' ', text)
    if len(text) > 40:
        return truncate_label(text, 40)
    return text


def resolve_project_spending_amount(project_row):
    """Prefer B6 spending_per_county; fallback to Kenya+Other spend when B6 is null."""
    if project_row is None:
        return 0.0
    spending_value = getattr(project_row, 'spending_per_county', None)
    if spending_value is not None:
        try:
            return float(spending_value)
        except (TypeError, ValueError):
            pass
    kenya_amount = getattr(project_row, 'amount_spent_kenya', None)
    other_amount = getattr(project_row, 'amount_spent_other', None)
    try:
        kenya_value = float(kenya_amount or 0)
    except (TypeError, ValueError):
        kenya_value = 0.0
    try:
        other_value = float(other_amount or 0)
    except (TypeError, ValueError):
        other_value = 0.0
    return kenya_value + other_value


def build_plotly_chart_html(fig, include_js=False):
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config={
            'displayModeBar': False,
            'responsive': True,
            'scrollZoom': False,
        },
    )


def build_donut_chart(go, labels, values, title, colors, include_js=False):
    if not labels or not values:
        return None

    cleaned_values = []
    for raw_value in values:
        try:
            numeric_value = float(raw_value or 0)
        except (TypeError, ValueError):
            numeric_value = 0.0
        cleaned_values.append(max(numeric_value, 0.0))
    if sum(cleaned_values) <= 0:
        return None

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=cleaned_values,
                hole=0.45,
                marker={'colors': colors},
                textinfo='none',
                textfont={'size': 12, 'color': '#0b2239'},
                hovertemplate=(
                    '<b>%{label}</b><br>'
                    'Count: %{value:,.0f}<br>'
                    'Share: %{percent}<extra></extra>'
                ),
            )
        ]
    )
    fig.update_layout(
        title={'text': title, 'x': 0.04, 'font': {'size': 15, 'color': '#0b2239'}},
        height=420,
        margin={'l': 18, 'r': 18, 't': 50, 'b': 50},
        paper_bgcolor='white',
        plot_bgcolor='white',
        autosize=True,
        showlegend=True,
        legend={'orientation': 'h', 'y': -0.15, 'x': 0.02, 'font': {'size': 10}},
    )
    return build_plotly_chart_html(fig, include_js=include_js)


def build_bar_chart(go, labels, values, title, color='#1f4e79', horizontal=False, include_js=False):
    if not labels or not values:
        return None

    display_labels = [truncate_label(label, 20) for label in labels]
    if horizontal:
        trace = go.Bar(
            y=display_labels,
            x=values,
            orientation='h',
            marker={'color': color},
            text=[f'{int(value):,}' for value in values],
            textposition='outside',
            customdata=[[int(value), labels[idx]] for idx, value in enumerate(values)],
            hovertemplate='<b>%{customdata[1]}</b><br>Value: %{customdata[0]:,}<extra></extra>',
        )
        layout = {
            'xaxis': {'showgrid': True, 'gridcolor': '#d9e4ef', 'zeroline': False, 'title': ''},
            'yaxis': {'showgrid': False, 'title': ''},
        }
    else:
        trace = go.Bar(
            x=display_labels,
            y=values,
            marker={'color': color},
            text=[f'{int(value):,}' for value in values],
            textposition='outside',
            customdata=[[int(value), labels[idx]] for idx, value in enumerate(values)],
            hovertemplate='<b>%{customdata[1]}</b><br>Value: %{customdata[0]:,}<extra></extra>',
        )
        layout = {
            'xaxis': {'tickangle': -25, 'showgrid': False, 'title': ''},
            'yaxis': {'showgrid': True, 'gridcolor': '#d9e4ef', 'zeroline': False, 'title': ''},
        }

    fig = go.Figure(data=[trace])
    fig.update_layout(
        title={'text': title, 'x': 0.04, 'font': {'size': 15, 'color': '#0b2239'}},
        height=420,
        margin={'l': 28, 'r': 20, 't': 52, 'b': 45},
        paper_bgcolor='white',
        plot_bgcolor='white',
        autosize=True,
        **layout,
    )
    return build_plotly_chart_html(fig, include_js=include_js)


def build_dashboard_payload():
    go = get_analysis_dependencies()
    scoped_report_ids = scoped_report_ids_subquery()

    total_reports = int(
        scoped_reports_query()
        .with_entities(func.count(PBOReport.id))
        .scalar()
        or 0
    )
    scope_counter = Counter()
    workflow_counter = Counter()
    county_counter = Counter()
    sector_counter = Counter()

    for raw_scope, count in (
        scoped_reports_query()
        .with_entities(PBOReport.scope, func.count(PBOReport.id))
        .group_by(PBOReport.scope)
        .all()
    ):
        scope_counter[clean_scope_name(raw_scope)] += int(count or 0)

    for raw_status, count in (
        scoped_reports_query()
        .with_entities(PBOReport.workflow_status, func.count(PBOReport.id))
        .group_by(PBOReport.workflow_status)
        .all()
    ):
        workflow_counter[(raw_status or 'draft').strip().lower() or 'draft'] += int(count or 0)

    for (raw_counties,) in scoped_reports_query().with_entities(PBOReport.counties).all():
        for county in split_counties(raw_counties):
            clean_county = clean_county_name(county)
            if clean_county:
                county_counter[clean_county] += 1

    assets_rows = int(
        db.session.query(func.count(Asset.id))
        .join(scoped_report_ids, Asset.report_id == scoped_report_ids.c.report_id)
        .scalar()
        or 0
    )

    donations_rows, donations_total = (
        db.session.query(
            func.count(Donation.id),
            func.coalesce(func.sum(Donation.amount), 0.0),
        )
        .join(scoped_report_ids, Donation.report_id == scoped_report_ids.c.report_id)
        .first()
        or (0, 0.0)
    )
    donations_rows = int(donations_rows or 0)
    donations_total = float(donations_total or 0.0)

    payments_rows, kenya_payments_total, other_payments_total = (
        db.session.query(
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.kenya_amount), 0.0),
            func.coalesce(func.sum(Payment.other_amount), 0.0),
        )
        .join(scoped_report_ids, Payment.report_id == scoped_report_ids.c.report_id)
        .first()
        or (0, 0.0, 0.0)
    )
    payments_rows = int(payments_rows or 0)
    payments_total = float(kenya_payments_total or 0.0) + float(other_payments_total or 0.0)

    officials_rows = int(
        db.session.query(func.count(Official.id))
        .join(scoped_report_ids, Official.report_id == scoped_report_ids.c.report_id)
        .scalar()
        or 0
    )

    project_rows = (
        db.session.query(
            ProjectImplementation.sector,
            ProjectImplementation.beneficiaries_no,
            ProjectImplementation.spending_per_county,
            ProjectImplementation.amount_spent_kenya,
            ProjectImplementation.amount_spent_other,
        )
        .join(scoped_report_ids, ProjectImplementation.report_id == scoped_report_ids.c.report_id)
        .all()
    )
    projects_rows = len(project_rows)
    project_beneficiaries = 0
    project_spending = 0.0
    for sector, beneficiaries_no, spending_per_county, amount_spent_kenya, amount_spent_other in project_rows:
        sector_counter[clean_sector_name(sector)] += 1
        project_beneficiaries += int(beneficiaries_no or 0)
        if spending_per_county is not None:
            try:
                project_spending += float(spending_per_county)
                continue
            except (TypeError, ValueError):
                pass
        try:
            project_spending += float(amount_spent_kenya or 0)
        except (TypeError, ValueError):
            pass
        try:
            project_spending += float(amount_spent_other or 0)
        except (TypeError, ValueError):
            pass

    total_funding = donations_total + payments_total
    compliance_ready = workflow_counter.get('approved', 0) + workflow_counter.get('validated', 0)
    compliance_pct = (compliance_ready / total_reports * 100.0) if total_reports else 0.0

    top_counties = county_counter.most_common(8)
    top_sectors = sector_counter.most_common(5)

    kpi_cards = [
        {'label': 'Reports In Scope', 'value': f'{total_reports:,}', 'sub': 'Active dataset rows'},
        {'label': 'Funding Captured', 'value': format_currency(total_funding), 'sub': 'Donations + payments'},
        {'label': 'Donations Captured', 'value': format_currency(donations_total), 'sub': f'{donations_rows:,} donation rows'},
        {'label': 'Payments Captured', 'value': format_currency(payments_total), 'sub': f'{payments_rows:,} payment rows'},
        {'label': 'Projects Logged', 'value': f'{projects_rows:,}', 'sub': 'Implementation rows'},
        {'label': 'Beneficiaries', 'value': f'{project_beneficiaries:,}', 'sub': 'From project entries'},
        {'label': 'Governance Entries', 'value': f'{officials_rows:,}', 'sub': 'Officials records'},
        {'label': 'Compliance Ready', 'value': f'{compliance_pct:.1f}%', 'sub': 'Validated + approved'},
    ]

    charts = []
    scope_items = scope_counter.most_common(6)
    scope_labels, scope_values = zip(*scope_items) if scope_items else ([], [])
    include_plotly_js = True
    scope_html = build_donut_chart(
        go,
        list(scope_labels),
        list(scope_values),
        'Scope Distribution',
        colors=['#0b3d91', '#2f5fa7', '#5485be', '#83add4', '#b8d0e6', '#dde9f5'],
        include_js=include_plotly_js,
    )
    if scope_html:
        charts.append({'title': 'Scope Distribution', 'subtitle': 'Share of reporting entities by scope', 'html': scope_html})
        include_plotly_js = False

    funding_html = build_bar_chart(
        go,
        ['Donations', 'Payments', 'Project Spend'],
        [int(donations_total), int(payments_total), int(project_spending)],
        'Financial Totals (KES)',
        color='#155e63',
        include_js=include_plotly_js,
    )
    if funding_html:
        charts.append({'title': 'Financial Totals', 'subtitle': 'Comparative funding and spend levels', 'html': funding_html})
        include_plotly_js = False

    county_html = build_bar_chart(
        go,
        [item[0] for item in top_counties],
        [item[1] for item in top_counties],
        'Top Counties by Coverage',
        color='#4c7d2f',
        horizontal=True,
        include_js=include_plotly_js,
    )
    if county_html:
        charts.append({'title': 'Geographic Coverage', 'subtitle': 'Counties appearing in report records', 'html': county_html})
        include_plotly_js = False

    workflow_labels = [status.replace('_', ' ').title() for status, _ in workflow_counter.most_common(6)]
    workflow_values = [count for _, count in workflow_counter.most_common(6)]
    workflow_html = build_bar_chart(
        go,
        workflow_labels,
        workflow_values,
        'Workflow / Compliance Status',
        color='#7c3a00',
        include_js=include_plotly_js,
    )
    if workflow_html:
        charts.append({'title': 'Compliance Pipeline', 'subtitle': 'Workflow state distribution', 'html': workflow_html})

    quant_rows = [
        {'metric': 'Asset rows captured', 'value': f'{assets_rows:,}', 'note': 'Quantitative'},
        {'metric': 'Donation rows captured', 'value': f'{donations_rows:,}', 'note': 'Quantitative'},
        {'metric': 'Payment rows captured', 'value': f'{payments_rows:,}', 'note': 'Quantitative'},
        {'metric': 'Top project sectors', 'value': ', '.join([f"{name} ({count})" for name, count in top_sectors[:3]]) or 'No data', 'note': 'Quantitative'},
    ]

    qualitative_rows = []
    if top_sectors:
        qualitative_rows.append(
            'Emerging focus area: ' + ', '.join([name for name, _ in top_sectors[:2]]) + '.'
        )
    else:
        qualitative_rows.append('Emerging focus area: No sector data available.')

    if top_counties:
        qualitative_rows.append(
            f"Geographic footprint concentrates around {top_counties[0][0]} ({top_counties[0][1]} records)."
        )
    else:
        qualitative_rows.append('Geographic footprint: No county data available.')

    if total_funding > 0 and project_beneficiaries > 0:
        qualitative_rows.append(
            f"Funding-to-impact signal: {format_currency(total_funding)} supporting {project_beneficiaries:,} beneficiaries."
        )
    else:
        qualitative_rows.append('Funding-to-impact signal: No complete funding/beneficiary pair available.')

    if compliance_pct >= 60:
        qualitative_rows.append('Governance readiness: compliance status is strong for regulatory review.')
    else:
        qualitative_rows.append('Governance readiness: compliance status is still maturing and needs follow-up.')

    operational_brief = [
        f"Reports included in this dashboard: {total_reports:,}.",
        f"Workflow-ready records: {compliance_ready:,} of {total_reports:,}.",
        f"Top county in scope: {top_counties[0][0]}." if top_counties else 'Top county in scope: no county data yet.',
    ]

    return {
        'kpi_cards': kpi_cards,
        'charts': charts,
        'quant_rows': quant_rows,
        'qualitative_rows': qualitative_rows,
        'operational_brief': operational_brief,
        'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
    }


def load_dashboard_payload_cached():
    if user_can_manage_all_records(current_user):
        scope_key = 'all'
    else:
        scope_key = f"user:{getattr(current_user, 'id', 'anon')}"
    db_tuple = (
        scoped_reports_query()
        .with_entities(func.count(PBOReport.id), func.max(PBOReport.updated_at))
        .first()
    )
    report_count = int(db_tuple[0] or 0)
    max_updated = db_tuple[1].isoformat() if db_tuple[1] else 'none'

    cache_key = (_ANALYSIS_CACHE_VERSION, scope_key, report_count, max_updated)
    now_ts = datetime.utcnow().timestamp()

    cached_key = _ANALYSIS_CACHE.get('key')
    cached_at = _ANALYSIS_CACHE.get('cached_at') or 0
    if cached_key == cache_key and (now_ts - cached_at) < _ANALYSIS_CACHE_TTL_SECONDS:
        return _ANALYSIS_CACHE.get('payload')

    payload = build_dashboard_payload()
    _ANALYSIS_CACHE['key'] = cache_key
    _ANALYSIS_CACHE['payload'] = payload
    _ANALYSIS_CACHE['cached_at'] = now_ts
    return payload


@analysis_bp.route('/analysis')
@login_required
def analysis():
    page = request.args.get('page', 1, type=int)
    per_page_options = [10, 25, 50, 100]
    requested_per_page = request.args.get('per_page', type=int)
    if requested_per_page in per_page_options:
        per_page = requested_per_page
        session['analysis_per_page'] = per_page
    else:
        per_page = session.get('analysis_per_page', 25)
        if per_page not in per_page_options:
            per_page = 25

    pagination = (
        scoped_reports_query()
        .options(
            selectinload(PBOReport.assets),
            selectinload(PBOReport.donations),
            selectinload(PBOReport.staff_biodata),
            selectinload(PBOReport.project_implementations),
        )
        .order_by(PBOReport.updated_at.desc(), PBOReport.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    table_rows = []
    for report in pagination.items:
        table_rows.append({
            'id': report.id,
            'pbo_name': report.pbo_name or 'Unnamed PBO',
            'registration_number': report.pbo_registration_number or 'N/A',
            'reporting_period': f"{format_date(report.reporting_period_start) or 'N/A'} to {format_date(report.reporting_period_end) or 'N/A'}",
            'scope': (report.scope or 'N/A').title() if report.scope else 'N/A',
            'assets_count': len(report.assets or []),
            'donations_count': len(report.donations or []),
            'staff_count': len(report.staff_biodata or []),
            'projects_count': len(report.project_implementations or []),
            'workflow_status': (report.workflow_status or 'draft').replace('_', ' ').title(),
        })

    try:
        dashboard_payload = load_dashboard_payload_cached()
    except Exception:
        current_app.logger.exception('Failed to build /analysis dashboard payload')
        dashboard_payload = {
            'kpi_cards': [],
            'charts': [],
            'quant_rows': [],
            'qualitative_rows': ['Dashboard visuals are temporarily unavailable.'],
            'operational_brief': ['The report register remains available below while analytics recover.'],
            'generated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
        }

    return render_template(
        'analysis.html',
        reports=table_rows,
        pagination=pagination,
        dashboard=dashboard_payload,
        per_page=per_page,
        per_page_options=per_page_options,
    )
