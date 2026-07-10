#!/usr/bin/env python3
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database_backup


def copy_if_present(source_path: Path | None, target_dir: Path) -> Path | None:
    if source_path is None or not source_path.exists():
        return None
    target_path = target_dir / source_path.name
    shutil.copy2(source_path, target_path)
    return target_path


def main():
    parser = argparse.ArgumentParser(
        description="Create a deployment bootstrap dump from the current configured database."
    )
    parser.add_argument(
        "--output-dir",
        default="db_bootstrap",
        help="Directory that will receive the deployment bootstrap artifacts.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="form14_bootstrap_") as temp_dir:
        result = database_backup.perform_backup_cycle(backup_root=temp_dir)
        copied_paths = []

        mysql_dump_path = copy_if_present(result.get("mysql_dump_path"), output_dir)
        if mysql_dump_path is not None:
            copied_paths.append(mysql_dump_path)

        postgres_dump_path = copy_if_present(result.get("dump_path"), output_dir)
        if postgres_dump_path is not None:
            copied_paths.append(postgres_dump_path)

        sqlite_candidates = [
            file_path
            for file_path in result.get("files", [])
            if file_path.suffix.lower() in {".sqlite", ".db"}
        ]
        if sqlite_candidates:
            sqlite_path = copy_if_present(sqlite_candidates[0], output_dir)
            if sqlite_path is not None:
                copied_paths.append(sqlite_path)

        archive_path = result.get("archive_path")
        if archive_path is not None and archive_path.exists():
            bundle_path = output_dir / "returnsform14_org_backup_bundle.zip"
            shutil.copy2(archive_path, bundle_path)
            copied_paths.append(bundle_path)

    print(f"Created {len(copied_paths)} deployment bootstrap artifact(s) in {output_dir}")
    for path in copied_paths:
        print(path)


if __name__ == "__main__":
    main()
