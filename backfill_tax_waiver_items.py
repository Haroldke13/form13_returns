from sqlalchemy import inspect, text

from app import app
from models import db


TABLE_NAME = "pbo_tax_waiver_items"
OLD_COLUMNS = {"item_specify", "is_checked", "amount"}
NEW_COLUMNS = {
    "item_description",
    "quantity",
    "exemption_type",
    "estimated_tax_waived",
    "certificate_approval_no",
}


def backfill_tax_waiver_items():
    with app.app_context():
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if TABLE_NAME not in tables:
            print(f"Table '{TABLE_NAME}' not found. Nothing to backfill.")
            return

        columns = {col["name"] for col in inspector.get_columns(TABLE_NAME)}
        if not OLD_COLUMNS.issubset(columns):
            print("Old tax waiver columns not found. Backfill skipped.")
            return

        if not NEW_COLUMNS.issubset(columns):
            print("New tax waiver columns not found. Run migrations first.")
            return

        sql = text(
            """
            UPDATE pbo_tax_waiver_items
            SET
                item_description = COALESCE(item_description, item_specify),
                estimated_tax_waived = COALESCE(estimated_tax_waived, amount),
                exemption_type = COALESCE(
                    exemption_type,
                    CASE WHEN is_checked THEN 'Tax Waiver' ELSE NULL END
                )
            """
        )
        result = db.session.execute(sql)
        db.session.commit()
        print(f"Backfilled {result.rowcount} tax waiver item rows.")


if __name__ == "__main__":
    backfill_tax_waiver_items()
