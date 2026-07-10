from app import app, db
from models import IncomeGeneratingActivity, PBOReport


PLACEHOLDER_ACTIVITY = "UNSPECIFIED IGA ACTIVITY"


def backfill_iga_rows():
    repaired_rows = 0
    touched_reports = set()

    with app.app_context():
        iga_rows = IncomeGeneratingActivity.query.all()

        for iga in iga_rows:
            changed = False
            activity = (iga.activity or "").strip()
            amount = iga.amount

            if not activity and amount not in (None, 0):
                iga.activity = PLACEHOLDER_ACTIVITY
                changed = True

            if activity == "OTHER":
                iga.activity = PLACEHOLDER_ACTIVITY
                changed = True

            if changed:
                repaired_rows += 1
                if iga.report_id:
                    touched_reports.add(iga.report_id)

        db.session.commit()

        print(f"Repaired IGA rows: {repaired_rows}")
        print(f"Reports touched: {len(touched_reports)}")

        if touched_reports:
            reports = (
                PBOReport.query
                .filter(PBOReport.id.in_(sorted(touched_reports)))
                .order_by(PBOReport.id.asc())
                .all()
            )
            for report in reports:
                print(f"Report {report.id}: {report.pbo_name or 'UNNAMED PBO'}")


if __name__ == "__main__":
    backfill_iga_rows()
