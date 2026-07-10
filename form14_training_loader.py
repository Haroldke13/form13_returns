#!/usr/bin/env python3
"""Load field-help intent training samples into the local database.

Works with both SQLite (dev) and PostgreSQL (prod) because it uses Flask-SQLAlchemy.
"""

import argparse
import json
import random
from pathlib import Path

from app import app
from models import db, FieldHelpIntentSample


def normalize_page_key(value):
    key = (value or "report_edit").strip().lower().replace(" ", "_")
    return key or "report_edit"


def load_dataset(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Dataset must be a JSON array.")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        text_value = (item.get("input") or "").strip()
        label_value = (item.get("label") or "").strip().upper()
        if not text_value or not label_value:
            continue
        rows.append({"input": text_value, "label": label_value})
    if not rows:
        raise ValueError("No valid rows found in dataset.")
    return rows


def main():
    parser = argparse.ArgumentParser(description="Load field-help training samples into DB.")
    parser.add_argument(
        "--dataset",
        default="field_help_intent_dataset.json",
        help="Path to JSON dataset file.",
    )
    parser.add_argument(
        "--page-key",
        default="report_edit",
        help="Logical page key (default: report_edit).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing samples for this page key before loading.",
    )
    parser.add_argument(
        "--train-size",
        type=int,
        default=30,
        help="How many rows to mark as train split (default: 30).",
    )
    parser.add_argument(
        "--test-size",
        type=int,
        default=20,
        help="How many rows to mark as test split after train rows (default: 20).",
    )
    args = parser.parse_args()

    rows = load_dataset(args.dataset)
    random.Random(14014).shuffle(rows)
    page_key = normalize_page_key(args.page_key)
    train_size = max(1, min(args.train_size, len(rows)))
    test_size = max(0, min(args.test_size, max(len(rows) - train_size, 0)))

    with app.app_context():
        if args.replace:
            FieldHelpIntentSample.query.filter_by(page_key=page_key).delete()
            db.session.commit()

        inserted = 0
        for index, row in enumerate(rows):
            if index < train_size:
                split = "train"
            elif index < train_size + test_size:
                split = "test"
            else:
                split = "train"
            db.session.add(
                FieldHelpIntentSample(
                    page_key=page_key,
                    input_text=row["input"],
                    label=row["label"],
                    dataset_split=split,
                )
            )
            inserted += 1
        db.session.commit()

    print(
        f"Loaded {inserted} samples for page_key={page_key} "
        f"(train={train_size + max(len(rows) - train_size - test_size, 0)}, test={test_size})."
    )


if __name__ == "__main__":
    main()
