#!/usr/bin/env python3
"""Utility entrypoint for the admin data-analysis bot.

This avoids filename conflicts with the existing `form14_training_loader.py`
used for field-help training data.
"""

import argparse
from pathlib import Path

from app import (
    app,
    DATA_ANALYSIS_PAGE_KEY,
    upsert_data_analysis_training_dataset,
    train_data_analysis_intent_model,
    answer_data_analysis_question,
)


def main():
    parser = argparse.ArgumentParser(description='Run data-analysis bot utility actions.')
    parser.add_argument('--replace-dataset', action='store_true', help='Replace DB training rows before training.')
    parser.add_argument('--train', action='store_true', help='Train the intent model.')
    parser.add_argument('--ask', default='', help='Run a single local test question.')
    parser.add_argument('--model-family', default='rf', choices=['rf', 'dt'], help='Classifier family.')
    args = parser.parse_args()
    conflict_paths = [
        Path('form14_training_loader.py'),
        Path('/home/harold-coder/Downloads/form14_training_loader.py'),
    ]
    existing_conflicts = [str(path) for path in conflict_paths if path.exists()]
    if existing_conflicts:
        print(f"Filename conflict check: related loader files found -> {', '.join(existing_conflicts)}")

    with app.app_context():
        if args.replace_dataset:
            result = upsert_data_analysis_training_dataset(page_key=DATA_ANALYSIS_PAGE_KEY, replace=True)
            print(
                f"Dataset reloaded: inserted={result.get('inserted', 0)} "
                f"train={result.get('train', 0)} test={result.get('test', 0)}"
            )

        if args.train or args.ask:
            state = train_data_analysis_intent_model(
                page_key=DATA_ANALYSIS_PAGE_KEY,
                force=True,
                model_family=args.model_family,
            )
            if state.get('available'):
                print(
                    f"Model ready ({args.model_family}). "
                    f"accuracy={state.get('accuracy', 0.0):.2%}, labels={state.get('labels', [])}"
                )
            else:
                print(f"Model unavailable: {state.get('reason', 'unknown reason')}")

        if args.ask:
            response = answer_data_analysis_question(args.ask, page_key=DATA_ANALYSIS_PAGE_KEY)
            print(f"Intent: {response.get('intent')}")
            print(f"Answer: {response.get('answer')}")
            if response.get('result_path'):
                print(f"HTML result: {response.get('result_path')}")


if __name__ == '__main__':
    main()
