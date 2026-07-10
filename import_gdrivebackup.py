#!/usr/bin/env python3
"""Import backup SQLite table metadata into data-analysis JSON catalogs.

This script merges (does not drop) existing JSON rows so any entries that are
not present in the backup are preserved.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple


TARGET_JSON_FILES = {
    'filename_questions': 'form14_data_analysis_filename_questions.json',
    'keyword_schema': 'form14_data_analysis_keyword_schema.json',
    'live_keyword_schema': 'form14_data_analysis_live_keyword_schema.json',
    'live_questions': 'form14_data_analysis_live_questions.json',
    'training_dataset': 'form14_data_analysis_training_dataset.json',
}


DEFAULT_BACKUP_CANDIDATES = [
       Path('/home/harold-coder/Downloads/returnsform14_org_backup.sqlite'),
]


IGNORED_SIGNATURE_COLUMNS = {
    'id',
    'created_at',
    'updated_at',
    'submitted_at',
    'last_activity_at',
    'last_viewed_at',
    'reviewed_at',
}


NATURAL_KEY_BY_TABLE = {
    'pbo_reports': ('pbo_name_normalized', 'reporting_period_start', 'reporting_period_end'),
    'pbo_assets': ('report_id', 'item'),
    'pbo_donations': ('report_id', 'name', 'category', 'country'),
    'pbo_payments': ('report_id', 'description'),
    'pbo_officials': ('report_id', 'role', 'name'),
    'pbo_project_implementations': ('report_id', 'sector', 'county', 'vulnerable_group'),
    'pbo_projects_carried_out': ('report_id', 'sector'),
    'pbo_training_records': ('report_id', 'training_type', 'topic'),
    'pbo_volunteer_privileges': ('report_id', 'privilege_name'),
    'pbo_collaboration_networking': ('report_id', 'name', 'category'),
    'pbo_bank_accounts': ('report_id', 'bank_name', 'account_number'),
    'pbo_auditors': ('report_id', 'name'),
}

COUNTY_NAME_HINTS = [
    'Nairobi City County', 'Mombasa County', 'Kisumu County', 'Nakuru County', 'Kiambu County',
    'Machakos County', 'Uasin Gishu County', 'Kakamega County', 'Meru County', 'Nyeri County',
    'Kajiado County', 'Kilifi County', 'Bungoma County', 'Busia County', 'Narok County',
    'Baringo County', 'Kericho County', 'Nandi County', 'Siaya County', 'Vihiga County',
    'Bomet County', 'Elgeyo Marakwet County', 'Embu County', 'Garissa County', 'Homa Bay County',
    'Isiolo County', 'Kirinyaga County', 'Kisii County', 'Kitui County', 'Kwale County',
    'Laikipia County', 'Lamu County', 'Makueni County', 'Mandera County', 'Marsabit County',
    'Migori County', 'Muranga County', 'Nyamira County', 'Nyandarua County', 'Samburu County',
    'Taita Taveta County', 'Tana River County', 'Tharaka Nithi County', 'Trans Nzoia County',
    'Turkana County', 'Wajir County', 'West Pokot County',
]

NLP_STYLE_PREFIXES = [
    'Show', 'List', 'Give me', 'Break down', 'Analyze', 'Summarize', 'Explain', 'Compare', 'Rank',
    'Tell me', 'Compute', 'Evaluate', 'Review', 'Profile',
]

NLP_STYLE_SUFFIXES = [
    'for decision-making.',
    'for policy reporting.',
    'for a regulator-friendly dashboard.',
    'for executive summary use.',
    'with clear statistical interpretation.',
    'and highlight key anomalies.',
    'and include confidence checks.',
    'and make the output presentation-ready.',
]


def normalize_name(value: str) -> str:
    text = (value or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return re.sub(r'_+', '_', text).strip('_')


def title_from_normalized(value: str) -> str:
    text = (value or '').strip().replace('_', ' ')
    return re.sub(r'\s+', ' ', text).strip().title()


def column_semantic_tags(column_name: str) -> List[str]:
    normalized = normalize_name(column_name)
    tags = set()
    if any(token in normalized for token in ('county', 'counties', 'country', 'location', 'region', 'scope')):
        tags.add('location')
    if any(token in normalized for token in ('name', 'organisation', 'organization', 'pbo', 'ngo')):
        tags.add('entity')
    if any(token in normalized for token in ('amount', 'value', 'total', 'spending', 'cost', 'budget', 'fee')):
        tags.add('finance')
    if any(token in normalized for token in ('date', 'period', 'year', 'month', 'created', 'updated')):
        tags.add('time')
    if any(token in normalized for token in ('status', 'state', 'flag', 'workflow', 'review')):
        tags.add('status')
    if any(token in normalized for token in ('count', 'number', 'qty', 'quantity')):
        tags.add('count')
    if any(token in normalized for token in ('email', 'phone', 'telephone', 'contact', 'address')):
        tags.add('contact')
    if any(token in normalized for token in ('risk', 'score', 'compliance', 'validation', 'duplicate')):
        tags.add('risk')
    if not tags:
        tags.add('general')
    return sorted(tags)


def expand_column_aliases(column_name: str) -> List[str]:
    normalized = normalize_name(column_name)
    if not normalized:
        return []
    aliases = {
        normalized,
        normalized.replace('_', ' '),
    }
    tokens = [token for token in normalized.split('_') if token]
    if tokens:
        aliases.add(' '.join(tokens))
    if 'county' in normalized or 'counties' in normalized:
        aliases.update({'county', 'counties', 'location', 'geographic area', 'region'})
    if any(token in normalized for token in ('organisation', 'organization', 'pbo', 'ngo', 'name')):
        aliases.update({'organization', 'organizations', 'organisation', 'organisations', 'entity', 'entities'})
    if any(token in normalized for token in ('amount', 'value', 'spending', 'total', 'cost', 'budget')):
        aliases.update({'amount', 'total', 'value', 'financial metric', 'money'})
    if any(token in normalized for token in ('count', 'number', 'qty', 'quantity')):
        aliases.update({'count', 'counts', 'number', 'totals'})
    if any(token in normalized for token in ('date', 'period', 'year', 'month', 'created', 'updated')):
        aliases.update({'date', 'time', 'period', 'timeline'})
    if any(token in normalized for token in ('status', 'state', 'flag', 'workflow', 'review')):
        aliases.update({'status', 'state', 'workflow'})
    if any(token in normalized for token in ('email', 'phone', 'telephone', 'contact')):
        aliases.update({'contact', 'communication', 'email', 'phone'})
    if any(token in normalized for token in ('risk', 'score', 'compliance', 'validation', 'duplicate')):
        aliases.update({'risk', 'score', 'compliance', 'quality'})
    return sorted(alias for alias in aliases if alias)


def infer_intent_for_column(column_name: str, variant_index: int) -> str:
    tags = set(column_semantic_tags(column_name))
    if 'location' in tags:
        cycle = ['distribution', 'aggregation', 'ranking', 'compare_groups']
        return cycle[variant_index % len(cycle)]
    if 'finance' in tags:
        cycle = ['aggregation', 'ranking', 'trend_analysis', 'anomaly_detection']
        return cycle[variant_index % len(cycle)]
    if 'time' in tags:
        cycle = ['trend_analysis', 'distribution', 'validation_check', 'aggregation']
        return cycle[variant_index % len(cycle)]
    if 'status' in tags or 'risk' in tags:
        cycle = ['validation_check', 'distribution', 'anomaly_detection', 'compare_groups']
        return cycle[variant_index % len(cycle)]
    if 'count' in tags:
        cycle = ['aggregation', 'ranking', 'compare_groups', 'trend_analysis']
        return cycle[variant_index % len(cycle)]
    if 'entity' in tags:
        cycle = ['distribution', 'ranking', 'aggregation', 'validation_check']
        return cycle[variant_index % len(cycle)]
    return ['aggregation', 'distribution', 'validation_check', 'compare_groups'][variant_index % 4]


def build_column_query_variants(source_name: str, column_name: str, companion_columns: List[str], max_queries: int = 1100) -> List[dict]:
    source_pretty = title_from_normalized(source_name)
    column_pretty = title_from_normalized(column_name)
    companion = title_from_normalized(companion_columns[0]) if companion_columns else 'Related Fields'
    companion_two = title_from_normalized(companion_columns[1]) if len(companion_columns) > 1 else companion
    tags = column_semantic_tags(column_name)
    scope_contexts = [
        'across all records',
        'for the current reporting periods',
        'for trend-ready analysis',
        'for executive dashboard use',
        'for validation and quality checks',
    ]
    row_views = [
        'at row level',
        'using row-level interactions',
        'with row and column cross-checks',
        'using row-level filters for non-empty values',
    ]
    analysis_lenses = [
        'with a count summary',
        'with mean or average where numeric',
        'with grouped rankings',
        'with anomaly checks',
        'with completeness checks',
        'with top-vs-bottom comparisons',
    ]
    prompts = []
    idx = 0
    for prefix in NLP_STYLE_PREFIXES:
        for context in scope_contexts:
            for row_view in row_views:
                for lens in analysis_lenses:
                    for suffix in NLP_STYLE_SUFFIXES:
                        intent = infer_intent_for_column(column_name, idx)
                        question = (
                            f"{prefix} {column_pretty} in {source_pretty} {context}, "
                            f"{row_view}, correlating with {companion} and {companion_two}, {lens}, {suffix}"
                        )
                        prompts.append({
                            'question': re.sub(r'\s+', ' ', question).strip(),
                            'intent': intent,
                            'fields_hint': [normalize_name(column_name)] + [normalize_name(col) for col in companion_columns[:3]],
                            'answer_style': 'brief_analytic',
                            'tags': tags,
                        })
                        idx += 1
                        if len(prompts) >= max_queries:
                            return prompts
    return prompts[:max_queries]


def read_json(path: Path, fallback: dict) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else fallback.copy()
    except Exception:
        return fallback.copy()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + '\n', encoding='utf-8')


def find_source_path(explicit_source: str | None) -> Path:
    if explicit_source:
        path = Path(explicit_source).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f'Source backup not found: {path}')

    for candidate in DEFAULT_BACKUP_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError('No backup SQLite file found in expected locations.')


def get_sqlite_tables_with_columns(source_path: Path) -> Dict[str, List[str]]:
    conn = sqlite3.connect(str(source_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row['name'] for row in cur.fetchall() if row['name'] and not row['name'].startswith('sqlite_')]
        result: Dict[str, List[str]] = {}
        for table_name in tables:
            cur.execute(f"PRAGMA table_info({table_name})")
            cols = [row['name'] for row in cur.fetchall() if row['name']]
            result[table_name] = cols
        return result
    finally:
        conn.close()


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def get_table_names(conn: sqlite3.Connection) -> List[str]:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row[0] for row in cur.fetchall() if row[0] and not row[0].startswith('sqlite_')]


def get_table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f'PRAGMA table_info({quote_ident(table_name)})')
    return [row[1] for row in cur.fetchall() if row[1]]


def fetch_rows(conn: sqlite3.Connection, table_name: str, columns: List[str]) -> List[sqlite3.Row]:
    cur = conn.cursor()
    select_cols = ', '.join(quote_ident(col) for col in columns)
    cur.execute(f'SELECT {select_cols} FROM {quote_ident(table_name)}')
    return cur.fetchall()


def parse_table_list(raw_tables: str | None) -> List[str]:
    if raw_tables is None:
        return []
    values: List[str] = []
    for item in raw_tables.split(','):
        clean = normalize_name(item)
        if clean:
            values.append(clean)
    seen = set()
    result = []
    for table in values:
        if table in seen:
            continue
        seen.add(table)
        result.append(table)
    return result


def resolve_selected_tables(raw_tables: str | None, source_tables: List[str]) -> List[str]:
    normalized_source = [normalize_name(name) for name in source_tables if normalize_name(name)]
    if raw_tables is None:
        # Import every source table by default, preserving source order.
        seen = set()
        ordered = []
        for name in normalized_source:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    raw = raw_tables.strip().lower()
    if raw in {'all', '*'}:
        seen = set()
        ordered = []
        for name in normalized_source:
            if name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    return parse_table_list(raw_tables)


def resolve_target_db_path(project_root: Path, explicit_target: str | None) -> Path:
    if explicit_target:
        return Path(explicit_target).expanduser().resolve()

    candidates = [
        project_root / 'form14.db',
        project_root / 'instance' / 'form14.db',
        project_root / 'form14.sqlite',
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def clean_string(value: Any) -> Any:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed != '' else None
    return value


def build_where_clause(values: Dict[str, Any]) -> Tuple[str, List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    for column, value in values.items():
        if value is None:
            clauses.append(f'{quote_ident(column)} IS NULL')
        else:
            clauses.append(f'{quote_ident(column)} = ?')
            params.append(value)
    return ' AND '.join(clauses), params


def candidate_natural_key(table_name: str, available_columns: List[str]) -> List[str]:
    preferred = NATURAL_KEY_BY_TABLE.get(table_name, ())
    key_cols = [col for col in preferred if col in available_columns]
    if key_cols:
        return key_cols

    # Generic fallback: use report_id + first textual identifier column if available.
    generic: List[str] = []
    if 'report_id' in available_columns:
        generic.append('report_id')
    for col in ('name', 'item', 'description', 'sector', 'county', 'category', 'role'):
        if col in available_columns and col not in generic:
            generic.append(col)
            break
    return generic


def dedupe_signature_columns(columns: List[str], natural_key: List[str]) -> List[str]:
    preferred = [col for col in columns if col not in IGNORED_SIGNATURE_COLUMNS]
    # Keep report linkage and key fields at the front, then remaining value fields.
    ordered = natural_key + [col for col in preferred if col not in natural_key]
    seen = set()
    result: List[str] = []
    for col in ordered:
        if col in seen:
            continue
        seen.add(col)
        result.append(col)
    return result


def report_lookup_key(row: Dict[str, Any], columns: List[str]) -> Dict[str, Any]:
    key_sets = [
        ('pbo_name_normalized', 'reporting_period_start', 'reporting_period_end'),
        ('pbo_name', 'reporting_period_start', 'reporting_period_end'),
        ('pbo_registration_number', 'reporting_period_start', 'reporting_period_end'),
    ]
    for key_cols in key_sets:
        if not all(col in columns for col in key_cols):
            continue
        key_values = {col: clean_string(row.get(col)) for col in key_cols}
        if all(value is not None for value in key_values.values()):
            return key_values
    return {}

def build_aliases(name: str, columns: List[str]) -> List[str]:
    aliases = {name, name.replace('_', ' ')}
    if name.startswith('pbo_'):
        aliases.add(name[4:])
        aliases.add(name[4:].replace('_', ' '))
    for col in columns:
        norm = normalize_name(col)
        if not norm:
            continue
        for alias in expand_column_aliases(norm):
            aliases.add(alias)
    return sorted(a for a in aliases if a)


def source_to_intent(source_name: str) -> str:
    mapping = {
        'pbo_payments': 'aggregation',
        'pbo_donations': 'ranking',
        'pbo_project_implementations': 'distribution',
        'pbo_projects_carried_out': 'compare_groups',
        'pbo_assets': 'aggregation',
        'pbo_bank_accounts': 'distribution',
        'pbo_auditors': 'distribution',
        'pbo_officials': 'distribution',
        'pbo_training_records': 'trend_analysis',
        'pbo_volunteer_privileges': 'compare_groups',
        'pbo_collaboration_networking': 'distribution',
        'pbo_reports': 'validation_check',
        'sector_report_data': 'aggregation',
        'returnsform14_org_backup_manifest': 'validation_check',
    }
    return mapping.get(source_name, 'aggregation')


def pick_metric_col(columns: List[str]) -> str:
    preferred_tokens = ('amount', 'total', 'count', 'value', 'number', 'spending', 'beneficiaries', 'score')
    for col in columns:
        if any(tok in col for tok in preferred_tokens):
            return col
    return columns[0] if columns else 'id'


def pick_group_col(columns: List[str]) -> str:
    preferred_tokens = ('name', 'sector', 'county', 'category', 'scope', 'role', 'country', 'partner', 'training', 'currency', 'status')
    for col in columns:
        if any(tok in col for tok in preferred_tokens):
            return col
    return columns[0] if columns else 'id'


def build_questions_for_source(source_name: str, normalized_columns: List[str], start_id: int) -> Tuple[List[dict], int]:
    top_cols = normalized_columns[:6]
    rows: List[dict] = []
    next_id = start_id

    # Keep a compact strategic set for high-signal intents.
    metric_col = pick_metric_col(normalized_columns)
    group_col = pick_group_col(normalized_columns)
    base_prompts = [
        (f"Show summary statistics for {title_from_normalized(source_name)} using key columns.", 'aggregation'),
        (f"List top {group_col.replace('_', ' ')} by {metric_col.replace('_', ' ')} in {title_from_normalized(source_name)}.", 'ranking'),
        (f"Show distribution patterns for {group_col.replace('_', ' ')} in {title_from_normalized(source_name)}.", 'distribution'),
        (f"Detect anomalies in {title_from_normalized(source_name)} using {metric_col.replace('_', ' ')}.", 'anomaly_detection'),
        (f"Validate missing and inconsistent values in {title_from_normalized(source_name)}.", 'validation_check'),
        (f"Run trend analysis on {title_from_normalized(source_name)} over available periods.", 'trend_analysis'),
    ]
    for question_text, intent in base_prompts:
        rows.append({
            'id': next_id,
            'question': question_text,
            'intent': intent,
            'target_domain': source_name,
            'fields_hint': top_cols,
            'answer_style': 'brief_analytic',
        })
        next_id += 1

    # Add 1100 human-like prompts per column.
    for idx, column_name in enumerate(normalized_columns):
        companions = [col for col in normalized_columns if col != column_name]
        column_prompts = build_column_query_variants(
            source_name=source_name,
            column_name=column_name,
            companion_columns=companions,
            max_queries=1100,
        )
        for prompt in column_prompts:
            rows.append({
                'id': next_id,
                'question': prompt['question'],
                'intent': prompt['intent'],
                'target_domain': source_name,
                'fields_hint': prompt['fields_hint'],
                'answer_style': prompt['answer_style'],
            })
            next_id += 1

    return rows, next_id


def build_global_county_source() -> dict:
    columns = [
        'county',
        'counties',
        'project_county',
        'project_row_counties',
        'counties_of_operation',
        'county_count',
    ]
    normalized_columns = [normalize_name(column) for column in columns if normalize_name(column)]
    return {
        'filename': 'all_database_county_scan',
        'normalized_name': 'all_county_sources',
        'sheet_name': 'sqlite_virtual',
        'aliases': [
            'all county sources',
            'all counties',
            'cross table county scan',
            'county',
            'counties',
            'county distribution',
            'organizations by county',
            'organizations per county',
        ],
        'columns': columns,
        'normalized_columns': normalized_columns,
    }


def build_global_county_questions(start_id: int) -> Tuple[List[dict], int]:
    prompt_specs = [
        ('How many organizations are in Nairobi City County across all tables with county fields?', 'aggregation'),
        ('Count organizations per county using all database tables with county or counties columns.', 'distribution'),
        ('Show county distribution across the whole database, not one table.', 'distribution'),
        ('List top counties by organization count across all county/counties fields.', 'ranking'),
        ('Compare Nairobi County and Mombasa County using whole-database county data.', 'compare_groups'),
        ('Show every table that contains county or counties and matched row counts.', 'validation_check'),
    ]

    location_templates = [
        ("How many organizations are in {county}?", 'aggregation'),
        ("Count organizations located in {county}.", 'aggregation'),
        ("Number of NGOs operating in {county}.", 'aggregation'),
        ("Total PBO entities in {county}.", 'aggregation'),
        ("How many organization names match {county} county records?", 'aggregation'),
        ("Give organization count for {county} across all tables.", 'aggregation'),
        ("How many submissions mention {county} in county columns?", 'aggregation'),
        ("Find entities linked to {county} using whole database search.", 'aggregation'),
        ("Show county distribution and highlight {county}.", 'distribution'),
        ("Rank counties and show where {county} appears.", 'ranking'),
        ("Compare {county} with Nairobi City County on organization count.", 'compare_groups'),
        ("Validate county-related rows for {county} in all sources.", 'validation_check'),
    ]

    generated = []
    for county in COUNTY_NAME_HINTS:
        for template, intent in location_templates:
            generated.append((template.format(county=county), intent))
            if len(generated) >= 300:
                break
        if len(generated) >= 300:
            break
    prompt_specs.extend(generated)
    rows = []
    next_id = start_id
    for question_text, intent in prompt_specs:
        rows.append({
            'id': next_id,
            'question': question_text,
            'intent': intent,
            'target_domain': 'all_county_sources',
            'fields_hint': ['county', 'counties', 'project_county', 'counties_of_operation'],
            'answer_style': 'brief_analytic',
        })
        next_id += 1
    return rows, next_id


def merge_sources(existing_sources: List[dict], generated_sources: List[dict]) -> List[dict]:
    merged: Dict[str, dict] = {}
    order: List[str] = []

    def upsert(row: dict, prefer_generated: bool) -> None:
        key = normalize_name(row.get('normalized_name') or row.get('filename') or '')
        if not key:
            return
        if key not in merged:
            merged[key] = row.copy()
            order.append(key)
            return

        current = merged[key]
        if prefer_generated:
            for fld in ('filename', 'normalized_name', 'sheet_name'):
                if row.get(fld):
                    current[fld] = row.get(fld)

        for list_fld in ('aliases', 'columns', 'normalized_columns'):
            base_values = current.get(list_fld) or []
            extra_values = row.get(list_fld) or []
            seen = set()
            combined = []
            for value in list(base_values) + list(extra_values):
                val = str(value).strip()
                if not val:
                    continue
                marker = val.lower()
                if marker in seen:
                    continue
                seen.add(marker)
                combined.append(val)
            current[list_fld] = combined

    for src in existing_sources:
        if isinstance(src, dict):
            upsert(src, prefer_generated=False)

    for src in generated_sources:
        if isinstance(src, dict):
            upsert(src, prefer_generated=True)

    return [merged[key] for key in order]


def question_key(row: dict) -> Tuple[str, str, str]:
    return (
        (row.get('question') or '').strip().lower(),
        (row.get('intent') or '').strip().lower(),
        normalize_name(row.get('target_domain') or ''),
    )


def merge_questions(existing_questions: List[dict], generated_questions: List[dict]) -> List[dict]:
    merged: List[dict] = []
    seen = set()

    for row in list(existing_questions) + list(generated_questions):
        if not isinstance(row, dict):
            continue
        question_text = (row.get('question') or '').strip()
        intent = (row.get('intent') or '').strip().lower()
        if not question_text or not intent:
            continue
        key = question_key(row)
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            'id': row.get('id'),
            'question': question_text,
            'intent': intent,
            'target_domain': normalize_name(row.get('target_domain') or ''),
            'fields_hint': row.get('fields_hint') or [],
            'answer_style': (row.get('answer_style') or 'brief_analytic').strip() or 'brief_analytic',
        })

    for idx, row in enumerate(merged, start=1):
        row['id'] = idx

    return merged


def build_generated_payloads(source_path: Path) -> Tuple[List[dict], List[dict], List[str]]:
    table_columns = get_sqlite_tables_with_columns(source_path)

    generated_sources = []
    generated_questions = []
    global_keywords = set()
    next_id = 1

    for table_name in sorted(table_columns.keys()):
        columns = table_columns[table_name]
        normalized_name = normalize_name(table_name)
        normalized_columns = [normalize_name(col) for col in columns if normalize_name(col)]

        generated_sources.append({
            'filename': table_name,
            'normalized_name': normalized_name,
            'sheet_name': 'sqlite_backup',
            'aliases': build_aliases(normalized_name, columns),
            'columns': columns,
            'normalized_columns': normalized_columns,
        })

        rows, next_id = build_questions_for_source(normalized_name, normalized_columns, next_id)
        generated_questions.extend(rows)

        for keyword in normalized_columns:
            if keyword:
                global_keywords.add(keyword)

    # Ensure county/counties prompts and routing metadata always include a whole-database scan mode.
    county_source = build_global_county_source()
    generated_sources.append(county_source)
    county_questions, next_id = build_global_county_questions(next_id)
    generated_questions.extend(county_questions)
    for keyword in county_source.get('normalized_columns', []):
        if keyword:
            global_keywords.add(keyword)
    global_keywords.update({'county', 'counties', 'all_county_sources'})

    return generated_sources, generated_questions, sorted(global_keywords)


def merge_and_write_jsons(project_root: Path, source_path: Path) -> dict:
    generated_sources, generated_questions, generated_keywords = build_generated_payloads(source_path)

    path_keyword_schema = project_root / TARGET_JSON_FILES['keyword_schema']
    path_live_keyword_schema = project_root / TARGET_JSON_FILES['live_keyword_schema']
    path_filename_questions = project_root / TARGET_JSON_FILES['filename_questions']
    path_live_questions = project_root / TARGET_JSON_FILES['live_questions']
    path_training = project_root / TARGET_JSON_FILES['training_dataset']

    keyword_schema = read_json(path_keyword_schema, {'version': 1, 'description': '', 'sources': [], 'global_keywords': []})
    live_keyword_schema = read_json(path_live_keyword_schema, {'version': 1, 'description': '', 'sources': [], 'global_keywords': []})
    filename_questions = read_json(path_filename_questions, {'dataset_name': 'form14_filename_questions', 'purpose': '', 'questions': []})
    live_questions = read_json(path_live_questions, {'dataset_name': 'form14_live_questions', 'purpose': '', 'questions': []})
    training_dataset = read_json(path_training, {'dataset_name': 'form14_data_analysis_training_dataset', 'questions': []})

    merged_keyword_sources = merge_sources(keyword_schema.get('sources') or [], generated_sources)
    merged_live_keyword_sources = merge_sources(live_keyword_schema.get('sources') or [], generated_sources)

    keyword_words = sorted(set([normalize_name(k) for k in (keyword_schema.get('global_keywords') or []) if normalize_name(k)] + generated_keywords))
    live_keyword_words = sorted(set([normalize_name(k) for k in (live_keyword_schema.get('global_keywords') or []) if normalize_name(k)] + generated_keywords))

    keyword_schema['version'] = keyword_schema.get('version') or 1
    keyword_schema['description'] = keyword_schema.get('description') or 'Keyword schema for data analysis routing.'
    keyword_schema['sources'] = merged_keyword_sources
    keyword_schema['global_keywords'] = keyword_words

    live_keyword_schema['version'] = live_keyword_schema.get('version') or 1
    live_keyword_schema['description'] = f"Live keyword schema merged from backup {source_path.name}."
    live_keyword_schema['sources'] = merged_live_keyword_sources
    live_keyword_schema['global_keywords'] = live_keyword_words

    merged_filename_questions = merge_questions(filename_questions.get('questions') or [], generated_questions)
    merged_live_questions = merge_questions(live_questions.get('questions') or [], generated_questions)
    merged_training_questions = merge_questions(training_dataset.get('questions') or [], generated_questions)

    filename_questions['dataset_name'] = filename_questions.get('dataset_name') or 'form14_data_analysis_filename_questions'
    filename_questions['purpose'] = filename_questions.get('purpose') or 'Prompt routing questions from filename and schema keywords.'
    filename_questions['questions'] = merged_filename_questions

    live_questions['dataset_name'] = live_questions.get('dataset_name') or 'form14_live_database_keyword_questions'
    live_questions['purpose'] = f"Supplemental prompts merged from backup {source_path.name}."
    live_questions['questions'] = merged_live_questions

    training_dataset['questions'] = merged_training_questions

    write_json(path_keyword_schema, keyword_schema)
    write_json(path_live_keyword_schema, live_keyword_schema)
    write_json(path_filename_questions, filename_questions)
    write_json(path_live_questions, live_questions)
    write_json(path_training, training_dataset)

    return {
        'source_tables': len(generated_sources),
        'generated_questions': len(generated_questions),
        'keyword_sources': len(merged_keyword_sources),
        'live_keyword_sources': len(merged_live_keyword_sources),
        'filename_questions': len(merged_filename_questions),
        'live_questions': len(merged_live_questions),
        'training_questions': len(merged_training_questions),
    }


def ensure_target_table(source_conn: sqlite3.Connection, target_conn: sqlite3.Connection, table_name: str) -> None:
    cur = target_conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    )
    if cur.fetchone():
        return

    source_cur = source_conn.cursor()
    source_cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table_name,),
    )
    row = source_cur.fetchone()
    if not row or not row[0]:
        raise RuntimeError(f'Cannot create target table {table_name}: source SQL not found.')
    cur.execute(row[0])


def report_exists_by_id(conn: sqlite3.Connection, report_id: int) -> bool:
    cur = conn.cursor()
    try:
        cur.execute('SELECT 1 FROM pbo_reports WHERE id = ? LIMIT 1', (report_id,))
        return cur.fetchone() is not None
    except sqlite3.OperationalError:
        return False


def upsert_reports(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table_name: str,
    source_columns: List[str],
    target_columns: List[str],
    dry_run: bool,
) -> Tuple[Dict[int, int], Dict[str, int]]:
    common = [col for col in source_columns if col in target_columns]
    if not common:
        return {}, {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}

    rows = fetch_rows(source_conn, table_name, common)
    report_map: Dict[int, int] = {}
    stats = {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}

    target_cur = target_conn.cursor()

    for row in rows:
        data = {col: clean_string(row[idx]) for idx, col in enumerate(common)}
        src_id = data.get('id')
        if src_id is None:
            stats['skipped'] += 1
            continue

        lookup = report_lookup_key(data, common)
        existing_id = None
        if lookup:
            where_sql, where_params = build_where_clause(lookup)
            target_cur.execute(f'SELECT COALESCE(id, rowid) FROM pbo_reports WHERE {where_sql} LIMIT 1', where_params)
            found = target_cur.fetchone()
            if found and found[0] is not None:
                existing_id = int(found[0])

        update_columns = [col for col in common if col != 'id']
        if existing_id is not None:
            report_map[int(src_id)] = existing_id
            if not update_columns:
                stats['deduped'] += 1
                continue

            if not dry_run:
                set_sql = ', '.join(f'{quote_ident(col)} = ?' for col in update_columns)
                values = [data.get(col) for col in update_columns] + [existing_id]
                try:
                    target_cur.execute(f'UPDATE pbo_reports SET {set_sql} WHERE id = ?', values)
                except sqlite3.IntegrityError:
                    stats['deduped'] += 1
                    continue
            stats['updated'] += 1
            continue

        insert_columns = [col for col in common if col != 'id']
        if not insert_columns:
            stats['skipped'] += 1
            continue

        if dry_run:
            fake_id = int(src_id)
            report_map[int(src_id)] = fake_id
            stats['inserted'] += 1
            continue

        placeholders = ', '.join('?' for _ in insert_columns)
        insert_sql = (
            f'INSERT INTO pbo_reports ({", ".join(quote_ident(col) for col in insert_columns)}) '
            f'VALUES ({placeholders})'
        )
        try:
            target_cur.execute(insert_sql, [data.get(col) for col in insert_columns])
        except sqlite3.IntegrityError:
            stats['deduped'] += 1
            continue
        new_id = int(target_cur.lastrowid)
        report_map[int(src_id)] = new_id
        stats['inserted'] += 1

    return report_map, stats


def upsert_child_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    table_name: str,
    source_columns: List[str],
    target_columns: List[str],
    report_id_map: Dict[int, int],
    dry_run: bool,
) -> Dict[str, int]:
    common = [col for col in source_columns if col in target_columns]
    if not common:
        return {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}

    rows = fetch_rows(source_conn, table_name, common)
    natural_key = candidate_natural_key(table_name, common)
    signature_columns = dedupe_signature_columns(common, natural_key)
    stats = {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}
    target_cur = target_conn.cursor()
    has_id_column = 'id' in target_columns

    for row in rows:
        data = {col: clean_string(row[idx]) for idx, col in enumerate(common)}

        if 'report_id' in data and data['report_id'] is not None:
            src_report_id = int(data['report_id'])
            mapped = report_id_map.get(src_report_id)
            if mapped is None and report_exists_by_id(target_conn, src_report_id):
                mapped = src_report_id
            if mapped is None:
                stats['skipped'] += 1
                continue
            data['report_id'] = mapped

        key_values = {
            col: data.get(col)
            for col in natural_key
            if col in data and data.get(col) is not None
        }

        existing_id = None
        existing_where_sql = ''
        existing_where_params: List[Any] = []
        if natural_key and len(key_values) == len(natural_key):
            where_sql, where_params = build_where_clause({col: data.get(col) for col in natural_key})
            select_col = 'COALESCE(id, rowid)' if has_id_column else '1'
            target_cur.execute(
                f'SELECT {select_col} FROM {quote_ident(table_name)} WHERE {where_sql} LIMIT 1',
                where_params,
            )
            found = target_cur.fetchone()
            if found and found[0] is not None:
                existing_where_sql = where_sql
                existing_where_params = where_params
                if has_id_column:
                    existing_id = int(found[0])

        if existing_id is None and signature_columns:
            signature_payload = {col: data.get(col) for col in signature_columns}
            where_sql, where_params = build_where_clause(signature_payload)
            select_col = 'COALESCE(id, rowid)' if has_id_column else '1'
            target_cur.execute(
                f'SELECT {select_col} FROM {quote_ident(table_name)} WHERE {where_sql} LIMIT 1',
                where_params,
            )
            found = target_cur.fetchone()
            if found and found[0] is not None:
                existing_where_sql = where_sql
                existing_where_params = where_params
                if has_id_column:
                    existing_id = int(found[0])

        updatable_columns = [col for col in common if col != 'id' and col not in natural_key]
        insert_columns = [col for col in common if col != 'id']

        row_exists = existing_id is not None or bool(existing_where_sql)
        if row_exists:
            if not updatable_columns:
                stats['deduped'] += 1
                continue

            if not dry_run:
                set_sql = ', '.join(f'{quote_ident(col)} = ?' for col in updatable_columns)
                values = [data.get(col) for col in updatable_columns]
                try:
                    if existing_id is not None:
                        target_cur.execute(
                            f'UPDATE {quote_ident(table_name)} SET {set_sql} WHERE id = ?',
                            values + [existing_id],
                        )
                    elif existing_where_sql:
                        target_cur.execute(
                            f'UPDATE {quote_ident(table_name)} SET {set_sql} WHERE {existing_where_sql}',
                            values + existing_where_params,
                        )
                except sqlite3.IntegrityError:
                    stats['deduped'] += 1
                    continue
            stats['updated'] += 1
            continue

        if not insert_columns:
            stats['skipped'] += 1
            continue

        if not dry_run:
            placeholders = ', '.join('?' for _ in insert_columns)
            insert_sql = (
                f'INSERT INTO {quote_ident(table_name)} ({", ".join(quote_ident(col) for col in insert_columns)}) '
                f'VALUES ({placeholders})'
            )
            try:
                target_cur.execute(insert_sql, [data.get(col) for col in insert_columns])
            except sqlite3.IntegrityError:
                stats['deduped'] += 1
                continue
        stats['inserted'] += 1

    return stats


def import_selected_tables_to_db(
    source_path: Path,
    target_db_path: Path,
    selected_tables: List[str],
    dry_run: bool,
) -> Dict[str, Any]:
    source_conn = sqlite3.connect(str(source_path))
    source_conn.row_factory = sqlite3.Row
    target_conn = sqlite3.connect(str(target_db_path))
    target_conn.row_factory = sqlite3.Row
    source_conn.execute('PRAGMA foreign_keys = OFF')
    target_conn.execute('PRAGMA foreign_keys = OFF')

    source_tables = set(get_table_names(source_conn))
    target_tables = set(get_table_names(target_conn))
    report_map: Dict[int, int] = {}
    per_table: Dict[str, Dict[str, int]] = {}

    normalized_tables = [normalize_name(t) for t in selected_tables if normalize_name(t)]
    tables = []
    seen_tables = set()
    for table in normalized_tables:
        if table in seen_tables:
            continue
        seen_tables.add(table)
        tables.append(table)

    if 'pbo_reports' in tables:
        tables = ['pbo_reports'] + [t for t in tables if t != 'pbo_reports']

    try:
        target_conn.execute('BEGIN')
        for table_name in tables:
            if table_name not in source_tables:
                per_table[table_name] = {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}
                continue

            if table_name not in target_tables:
                ensure_target_table(source_conn, target_conn, table_name)
                target_tables.add(table_name)

            source_columns = get_table_columns(source_conn, table_name)
            target_columns = get_table_columns(target_conn, table_name)

            if table_name == 'pbo_reports':
                report_map, stats = upsert_reports(
                    source_conn=source_conn,
                    target_conn=target_conn,
                    table_name=table_name,
                    source_columns=source_columns,
                    target_columns=target_columns,
                    dry_run=dry_run,
                )
                per_table[table_name] = stats
                continue

            stats = upsert_child_rows(
                source_conn=source_conn,
                target_conn=target_conn,
                table_name=table_name,
                source_columns=source_columns,
                target_columns=target_columns,
                report_id_map=report_map,
                dry_run=dry_run,
            )
            per_table[table_name] = stats

        if dry_run:
            target_conn.rollback()
        else:
            target_conn.commit()
    except Exception:
        target_conn.rollback()
        raise
    finally:
        source_conn.close()
        target_conn.close()

    totals = {'inserted': 0, 'updated': 0, 'deduped': 0, 'skipped': 0}
    for stats in per_table.values():
        for key in totals:
            totals[key] += int(stats.get(key, 0))

    return {
        'source': str(source_path),
        'target_db': str(target_db_path),
        'tables': tables,
        'per_table': per_table,
        'totals': totals,
        'dry_run': dry_run,
    }



"""python3 import_gdrivebackup.py --mode metadata --page-key report_edit
"""

def run_db_updates(project_root: Path, page_key: str) -> None:
    loader_cmd = [sys.executable, str(project_root / 'form14_data_analysis_training_loader.py'), '--replace', '--page-key', page_key]
    print('Running:', ' '.join(loader_cmd))
    subprocess.run(loader_cmd, check=True)

    retrain_script = (
        'from app import app, train_data_analysis_intent_model\n'
        f'with app.app_context():\n    state=train_data_analysis_intent_model(page_key="{page_key}", force=True, model_family="rf")\n'
        'print("model_available", bool(state.get("available")), "accuracy", state.get("accuracy"))\n'
    )
    retrain_cmd = [sys.executable, '-c', retrain_script]
    print('Running:', ' '.join(retrain_cmd[:3]), '...')
    subprocess.run(retrain_cmd, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Backup import helper with two modes: '
            'metadata merge to JSON catalogs, and table import into form14.db.'
        )
    )
    parser.add_argument('--source', default=None, help='Path to backup sqlite file. Defaults to known backup locations.')
    parser.add_argument('--project-root', default='.', help='Project root containing target JSON files.')
    parser.add_argument(
        '--mode',
        choices=['metadata', 'tables', 'both'],
        default='metadata',
        help='metadata=JSON merge, tables=selected table import, both=run both modes.',
    )
    parser.add_argument(
        '--target-db',
        default=None,
        help='Target SQLite database path for --mode tables/both. Defaults to form14.db when available.',
    )
    parser.add_argument(
        '--tables',
        default=None,
        help='Comma-separated tables for table import mode. Use "all" to import all source tables (default behavior).',
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview table import counts without writing changes.',
    )
    parser.add_argument('--page-key', default='report_edit', help='Page key for training loader updates.')
    parser.add_argument('--skip-db-updates', action='store_true', help='Skip loader/retrain DB update calls.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    source_path = find_source_path(args.source)

    print(f'Using backup source: {source_path}')

    run_metadata = args.mode in {'metadata', 'both'}
    run_tables = args.mode in {'tables', 'both'}

    if run_metadata:
        for filename in TARGET_JSON_FILES.values():
            path = project_root / filename
            if not path.exists():
                raise FileNotFoundError(f'Missing target JSON file: {path}')

        stats = merge_and_write_jsons(project_root, source_path)
        print('Metadata merge complete:')
        for key, value in stats.items():
            print(f'  - {key}: {value}')

        if not args.skip_db_updates:
            run_db_updates(project_root, args.page_key)

    if run_tables:
        target_db_path = resolve_target_db_path(project_root, args.target_db)
        if not target_db_path.exists():
            raise FileNotFoundError(f'Target DB not found: {target_db_path}')
        source_conn = sqlite3.connect(str(source_path))
        try:
            source_tables = get_table_names(source_conn)
        finally:
            source_conn.close()
        selected_tables = resolve_selected_tables(args.tables, source_tables)
        if not selected_tables:
            raise ValueError('No tables selected for import.')
        import_stats = import_selected_tables_to_db(
            source_path=source_path,
            target_db_path=target_db_path,
            selected_tables=selected_tables,
            dry_run=args.dry_run,
        )
        print('Table import complete:')
        print(f'  - target_db: {import_stats["target_db"]}')
        print(f'  - dry_run: {import_stats["dry_run"]}')
        for table_name in import_stats['tables']:
            stats = import_stats['per_table'].get(table_name) or {}
            print(
                f'  - {table_name}: '
                f'inserted={stats.get("inserted", 0)} '
                f'updated={stats.get("updated", 0)} '
                f'deduped={stats.get("deduped", 0)} '
                f'skipped={stats.get("skipped", 0)}'
            )
        totals = import_stats['totals']
        print(
            '  - totals: '
            f'inserted={totals.get("inserted", 0)} '
            f'updated={totals.get("updated", 0)} '
            f'deduped={totals.get("deduped", 0)} '
            f'skipped={totals.get("skipped", 0)}'
        )

    print('Done.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
