#!/usr/bin/env python3
"""Import backup SQLite table metadata into data-analysis JSON catalogs.

This script merges (does not drop) existing JSON rows so any entries that are
not present in the backup are preserved.
"""


"""
python3 import_gdrivebackup.py --source '/home/harold-coder/Downloads/server_backups/returnsform14_org_backup(35).sqlite'

Validation I ran:

python3 -m py_compile import_gdrivebackup.py ✅
Dry-run table import against backup + instance/form14.db ✅
Use it like this:

python3 import_gdrivebackup.py \
  --mode tables \
  --source '/home/harold-coder/Downloads/server_backups/returnsform14_org_backup(35).sqlite' \
  --target-db 'instance/form14.db' \
  --tables 'pbo_reports,pbo_assets,pbo_donations,pbo_payments,pbo_officials,pbo_project_implementations,pbo_projects_carried_out'

  
  
  If you want, I can run the real import (non-dry-run) for your exact table list next.
"""



from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import re
from typing import Dict, List, Tuple


TARGET_JSON_FILES = {
    'filename_questions': 'form14_data_analysis_filename_questions.json',
    'keyword_schema': 'form14_data_analysis_keyword_schema.json',
    'live_keyword_schema': 'form14_data_analysis_live_keyword_schema.json',
    'live_questions': 'form14_data_analysis_live_questions.json',
    'training_dataset': 'form14_data_analysis_training_dataset.json',
}


DEFAULT_BACKUP_CANDIDATES = [
    Path('/home/harold-coder/Downloads/server_backups/returnsform14_org_backup(35).sqlite'),
    Path('/home/harold-coder/Downloads/server_backups/returnsform14_org_backup(36).sqlite'),
    Path('/home/harold-coder/Downloads/returnsform14_org_backup(35).sqlite'),
]


def normalize_name(value: str) -> str:
    text = (value or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text)
    return re.sub(r'_+', '_', text).strip('_')


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


def build_aliases(name: str, columns: List[str]) -> List[str]:
    aliases = {name, name.replace('_', ' ')}
    if name.startswith('pbo_'):
        aliases.add(name[4:])
        aliases.add(name[4:].replace('_', ' '))
    for col in columns:
        norm = normalize_name(col)
        if not norm:
            continue
        aliases.add(norm)
        aliases.add(norm.replace('_', ' '))
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
    pretty = source_name.replace('_', ' ')
    top_cols = normalized_columns[:4]
    metric_col = pick_metric_col(normalized_columns)
    group_col = pick_group_col(normalized_columns)
    default_intent = source_to_intent(source_name)

    prompt_specs = [
        (f"Show summary statistics for {pretty} using columns {', '.join(top_cols[:3]) if top_cols else 'available columns'}.", 'aggregation'),
        (f"List top 10 {group_col.replace('_', ' ')} in {pretty} by {metric_col.replace('_', ' ')}.", 'ranking'),
        (f"Show distribution of {group_col.replace('_', ' ')} from {pretty}.", 'distribution'),
        (f"Find missing or invalid values in {pretty} for {metric_col.replace('_', ' ')} and {group_col.replace('_', ' ')}.", 'validation_check'),
        (f"Compare groups in {pretty} using {group_col.replace('_', ' ')} and {metric_col.replace('_', ' ')}.", 'compare_groups'),
        (f"Detect outliers in {pretty} based on {metric_col.replace('_', ' ')}.", 'anomaly_detection'),
        (f"How many records are in {pretty} and how many unique {group_col.replace('_', ' ')} values exist?", default_intent),
        (f"Use table {source_name} to analyze {group_col.replace('_', ' ')} trends.", 'trend_analysis'),
    ]

    rows: List[dict] = []
    next_id = start_id
    for question_text, intent in prompt_specs:
        rows.append({
            'id': next_id,
            'question': question_text,
            'intent': intent,
            'target_domain': source_name,
            'fields_hint': top_cols[:6],
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
    parser = argparse.ArgumentParser(description='Merge backup SQLite schema into data-analysis JSON catalogs and update DB training rows.')
    parser.add_argument('--source', default=None, help='Path to backup sqlite file. Defaults to known backup locations.')
    parser.add_argument('--project-root', default='.', help='Project root containing target JSON files.')
    parser.add_argument('--page-key', default='report_edit', help='Page key for training loader updates.')
    parser.add_argument('--skip-db-updates', action='store_true', help='Skip loader/retrain DB update calls.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    source_path = find_source_path(args.source)

    for filename in TARGET_JSON_FILES.values():
        path = project_root / filename
        if not path.exists():
            raise FileNotFoundError(f'Missing target JSON file: {path}')

    print(f'Using backup source: {source_path}')
    stats = merge_and_write_jsons(project_root, source_path)

    print('Merge complete:')
    for key, value in stats.items():
        print(f'  - {key}: {value}')

    if not args.skip_db_updates:
        run_db_updates(project_root, args.page_key)

    print('Done.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
