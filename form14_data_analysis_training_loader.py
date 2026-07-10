#!/usr/bin/env python3
"""Load data-analysis bot training questions into DB (SQLite dev / Postgres prod).

This loader uses Flask-SQLAlchemy, so it is database-engine agnostic.
"""

import argparse
import json
from pathlib import Path

from app import app, normalize_field_help_page_key, load_data_analysis_dataset
from models import db, DataAnalysisTrainingQuestion


def warn_filename_conflicts():
    local_conflicts = []
    for name in ('form14_training_loader.py', 'form14_data_analysis_bot.py'):
        if Path(name).exists():
            local_conflicts.append(name)
    external_conflicts = []
    for path in (
        Path('/home/harold-coder/Downloads/form14_training_loader.py'),
        Path('/home/harold-coder/Downloads/form14_data_analysis_bot.py'),
    ):
        if path.exists():
            external_conflicts.append(str(path))
    if local_conflicts or external_conflicts:
        print('Filename conflict check:')
        if local_conflicts:
            print(f"  Local files present: {', '.join(local_conflicts)}")
        if external_conflicts:
            print(f"  External files present: {', '.join(external_conflicts)}")


def load_dataset(path):
    payload = load_data_analysis_dataset(path)
    if not isinstance(payload, dict):
        raise ValueError('Dataset must be a JSON object.')
    questions = payload.get('questions')
    if not isinstance(questions, list):
        raise ValueError('Dataset JSON must include a "questions" array.')
    return payload, questions


def main():
    parser = argparse.ArgumentParser(description='Load data-analysis training dataset into DB.')
    parser.add_argument(
        '--dataset',
        default='form14_data_analysis_training_dataset.json',
        help='Path to JSON dataset file.',
    )
    parser.add_argument('--page-key', default='report_edit', help='Logical page key.')
    parser.add_argument('--replace', action='store_true', help='Replace existing rows for the page key.')
    parser.add_argument('--train-size', type=int, default=240, help='Number of rows marked train first.')
    parser.add_argument('--test-size', type=int, default=60, help='Number of rows marked test after train rows.')
    args = parser.parse_args()
    warn_filename_conflicts()

    dataset, questions = load_dataset(args.dataset)
    page_key = normalize_field_help_page_key(args.page_key)
    train_size = max(1, min(args.train_size, len(questions)))
    test_size = max(0, min(args.test_size, max(len(questions) - train_size, 0)))

    with app.app_context():
        if args.replace:
            DataAnalysisTrainingQuestion.query.filter_by(page_key=page_key).delete()
            db.session.commit()

        inserted = 0
        for idx, item in enumerate(questions):
            question = (item.get('question') or '').strip()
            intent = (item.get('intent') or '').strip().lower()
            if not question or not intent:
                continue
            if idx < train_size:
                split = 'train'
            elif idx < train_size + test_size:
                split = 'test'
            else:
                split = 'train'
            db.session.add(DataAnalysisTrainingQuestion(
                page_key=page_key,
                question=question,
                intent=intent,
                target_domain=(item.get('target_domain') or dataset.get('dataset_name') or '')[:120],
                fields_hint_json=json.dumps(item.get('fields_hint') or [], ensure_ascii=True),
                answer_style=(item.get('answer_style') or '').strip()[:80] or None,
                dataset_split=split,
            ))
            inserted += 1
        db.session.commit()

    effective_train = inserted - test_size if inserted > test_size else inserted
    print(
        f'Loaded {inserted} rows for page_key={page_key} '
        f'(train={effective_train}, test={min(test_size, inserted)}).'
    )


if __name__ == '__main__':
    main()
