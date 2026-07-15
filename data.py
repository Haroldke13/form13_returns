import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import random
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone

try:
    import cv2
except Exception:
    cv2 = None
from faker import Faker
from sqlalchemy import func

from models import (
    Asset,
    BankAccount,
    Donation,
    FieldChangeLog,
    Official,
    PBOReport,
    Payment,
    ProjectImplementation,
    StaffBiodata,
    TrainingRecord,
    User,
    VolunteerBiodata,
    VolunteerPrivilege,
    db,
)


fake = Faker()
Faker.seed(2026)
random.seed(2026)
PADDLE_OCR_READER = None
OCR_FEEDBACK_CACHE = {"loaded_at": None, "profile": None}

COUNTIES = [
    "NAIROBI",
    "MOMBASA",
    "KISUMU",
    "NAKURU",
    "KIAMBU",
    "UASIN GISHU",
    "MACHAKOS",
]
SECTORS = [
    "HEALTH",
    "EDUCATION",
    "AGRICULTURE",
    "WATER",
    "YOUTH",
    "GOVERNANCE",
]


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_app_components():
    from app import app, compute_tf_risk

    return app, compute_tf_risk


def random_date(start_year=2020, end_year=2025):
    return fake.date_between(
        start_date=date(start_year, 1, 1),
        end_date=date(end_year, 12, 31),
    )


def ensure_test_user():
    email = "functional-check@example.com"
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            email=email,
            role="user",
            is_authorized=True,
            authorized_at=utcnow(),
        )
        user.set_password("FunctionalCheck@123")
        db.session.add(user)
        db.session.commit()
    return user


def base_report(user, index, data_source):
    return PBOReport(
        user_id=user.id,
        workflow_status=random.choice(["draft", "submitted", "validated"]),
        review_status=random.choice(["pending", "reviewed"]),
        data_source=data_source,
        reporting_period_start=date(2024, 1, 1),
        reporting_period_end=date(2024, 12, 31),
        return_date=date.today(),
        pbo_name=f"{fake.company().upper()} FOUNDATION {index}",
        pbo_registration_number=f"PBO/{2020 + index}/{random.randint(1000, 9999)}",
        pbo_registration_date=random_date(2012, 2024),
        kra_pin=fake.bothify(text="P#########A").upper(),
        postal_address=f"{random.randint(1000, 9999)} {random.choice(['00100', '20100', '40100'])} {random.choice(COUNTIES)}",
        physical_address=fake.address().replace("\n", ", ").upper(),
        telephone=f"020 {random.randint(2000000, 4999999)}",
        cell_phone=f"07{random.randint(10000000, 99999999)}",
        email=fake.company_email().lower(),
        website=f"https://{fake.domain_name()}",
        social_media=f"@{fake.user_name()}",
        contact_name=fake.name().upper(),
        contact_position=random.choice(["EXECUTIVE DIRECTOR", "PROGRAM MANAGER", "FINANCE MANAGER"]),
        contact_telephone=f"07{random.randint(10000000, 99999999)}",
        contact_email=fake.email().lower(),
        contact_nationality=random.choice(["KENYA", "UGANDA", "TANZANIA"]),
        contact_gender=random.choice(["MALE", "FEMALE"]),
        registration_number=f"REG/{random.randint(100, 999)}/{random.randint(1000, 9999)}",
        pin_number=fake.bothify(text="P#########A").upper(),
        date_of_registration=random_date(2012, 2024),
        scope=random.choice(["NATIONAL", "INTERNATIONAL"]),
        counties=", ".join(random.sample(COUNTIES, k=3)),
        submitted_at=utcnow() - timedelta(days=random.randint(0, 20)),
        last_activity_at=utcnow(),
        last_modified_by_id=user.id,
        edit_deadline=utcnow() + timedelta(days=30),
        form_submission_deadline=utcnow() + timedelta(days=30),
        review_notes="Seeded dataset for functionality checks.",
    )


def nullify_finance(report):
    report.assets_stolen = None
    report.cash_balance_previous_year = None
    report.cash_bank_balance = None
    report.audited = None
    report.assets.clear()
    report.donations.clear()
    report.grants.clear()
    report.payments.clear()
    report.bank_accounts.clear()
    report.auditors.clear()
    report.igas.clear()


def nullify_personnel(report):
    report.staff_kenyan_prev = None
    report.staff_foreign_prev = None
    report.staff_kenyan_current = None
    report.staff_foreign_current = None
    report.staff_kenyan_came_in = None
    report.staff_foreign_came_in = None
    report.staff_kenyan_left = None
    report.staff_foreign_left = None
    report.staff_other_kenyan_prev = None
    report.staff_other_foreign_prev = None
    report.staff_other_kenyan_current = None
    report.staff_other_foreign_current = None
    report.volunteers_kenyan_prev = None
    report.volunteers_foreign_prev = None
    report.volunteers_kenyan_current = None
    report.volunteers_foreign_current = None
    report.staff_biodata.clear()
    report.volunteer_biodata.clear()
    report.volunteer_privileges.clear()
    report.training_records.clear()


def nullify_projects(report):
    report.project_implementation_method = None
    report.local_material = None
    report.local_material_amount = None
    report.local_labour = None
    report.local_labour_amount = None
    report.local_financial = None
    report.local_financial_amount = None
    report.local_other = None
    report.local_other_specify = None
    report.local_other_amount = None
    report.gov_tax_waiver = None
    report.gov_tax_waiver_amount = None
    report.gov_other = None
    report.gov_other_specify = None
    report.gov_other_amount = None
    report.project_implementations.clear()
    report.projects_carried_out.clear()
    report.tax_waiver_items.clear()


def nullify_governance(report):
    report.number_of_directors = None
    report.number_of_registered_members = None
    report.number_of_board_meetings = None
    report.election_frequency = None
    report.election_frequency_other = None
    report.date_last_agm = None
    report.date_last_election = None
    report.date_last_board_meeting = None
    report.membership_number_of_directors = None
    report.membership_number_of_registered_members = None
    report.membership_number_of_board_meetings = None
    report.membership_date_last_agm = None
    report.membership_date_last_election = None
    report.non_membership_number_of_directors = None
    report.non_membership_number_of_board_meetings = None
    report.non_membership_date_last_board_meeting = None
    report.non_membership_date_last_election = None
    report.submitter_fullname = None
    report.signature = None
    report.submission_date = None
    report.officials.clear()


def apply_finance(report):
    report.assets_stolen = random.choice(["YES", "NO"])
    report.cash_balance_previous_year = round(random.uniform(300000, 3000000), 2)
    report.cash_bank_balance = round(random.uniform(500000, 8000000), 2)
    report.audited = random.choice(["YES", "NO"])
    report.assets.extend(
        [
            Asset(item="FURNITURE AND FITTINGS", number=random.randint(5, 30), value=round(random.uniform(50000, 250000), 2)),
            Asset(item="COMPUTERS AND ACCESSORIES", number=random.randint(2, 15), value=round(random.uniform(75000, 400000), 2)),
        ]
    )
    report.donations.extend(
        [
            Donation(
                name=fake.company().upper(),
                category=random.choice(["FOUNDATION", "NON GOVERNMENTAL ORGANIZATION", "CORPORATE DONORS"]),
                country=random.choice(["KENYA", "DENMARK", "USA", "SWITZERLAND"]),
                amount=round(random.uniform(80000, 3500000), 2),
            ),
            Donation(
                name=fake.company().upper(),
                category=random.choice(["FOUNDATION", "NON GOVERNMENTAL ORGANIZATION", "CORPORATE DONORS"]),
                country=random.choice(["KENYA", "UGANDA", "CANADA"]),
                amount=round(random.uniform(50000, 1800000), 2),
            ),
        ]
    )
    report.payments.extend(
        [
            Payment(description="PROJECT COSTS", kenya_amount=round(random.uniform(120000, 5000000), 2), other_amount=0),
            Payment(description="ADMINISTRATION COSTS", kenya_amount=round(random.uniform(80000, 1500000), 2), other_amount=0),
        ]
    )
    report.bank_accounts.append(
        BankAccount(
            bank_name=random.choice(["KCB BANK", "EQUITY BANK", "ABSA BANK"]),
            branch=random.choice(COUNTIES),
            account_number=str(random.randint(1000000000, 9999999999)),
            currency="KES",
        )
    )


def apply_personnel(report):
    kenyan_prev = random.randint(8, 25)
    kenyan_current = max(kenyan_prev - 2, 1) + random.randint(0, 4)
    foreign_prev = random.randint(0, 3)
    foreign_current = max(foreign_prev - 1, 0) + random.randint(0, 2)

    report.staff_kenyan_prev = kenyan_prev
    report.staff_foreign_prev = foreign_prev
    report.staff_kenyan_current = kenyan_current
    report.staff_foreign_current = foreign_current
    report.staff_kenyan_came_in = random.randint(0, 4)
    report.staff_foreign_came_in = random.randint(0, 2)
    report.staff_kenyan_left = random.randint(0, 2)
    report.staff_foreign_left = random.randint(0, 1)
    report.staff_other_kenyan_prev = 0
    report.staff_other_foreign_prev = 0
    report.staff_other_kenyan_current = 0
    report.staff_other_foreign_current = 0
    report.volunteers_kenyan_prev = random.randint(0, 10)
    report.volunteers_foreign_prev = random.randint(0, 2)
    report.volunteers_kenyan_current = random.randint(0, 10)
    report.volunteers_foreign_current = random.randint(0, 2)

    report.staff_biodata.extend(
        [
            StaffBiodata(category="KENYAN", prev_year=kenyan_prev, curr_year=kenyan_current),
            StaffBiodata(category="FOREIGN", prev_year=foreign_prev, curr_year=foreign_current),
        ]
    )
    report.volunteer_biodata.extend(
        [
            VolunteerBiodata(
                category="KENYAN",
                prev_year=report.volunteers_kenyan_prev,
                curr_year=report.volunteers_kenyan_current,
            ),
            VolunteerBiodata(
                category="FOREIGN",
                prev_year=report.volunteers_foreign_prev,
                curr_year=report.volunteers_foreign_current,
            ),
        ]
    )
    report.volunteer_privileges.append(
        VolunteerPrivilege(
            category="ALLOWANCES/STIPENDS",
            kenyan_volunteer=True,
            kenyan_intern=True,
            international_volunteer=False,
            international_intern=False,
        )
    )
    report.training_records.extend(
        [
            TrainingRecord(training_type="IN-HOUSE TRAINING", kenyan_count=random.randint(1, 8), international_count=0),
            TrainingRecord(training_type="PROFESSIONAL TRAINING", kenyan_count=random.randint(0, 3), international_count=0),
        ]
    )


def apply_projects(report):
    sector = random.choice(SECTORS)
    spend = round(random.uniform(150000, 5000000), 2)
    report.project_implementation_method = random.choice(
        ["DIRECT IMPLEMENTATION", "PARTNER IMPLEMENTATION", "DIRECT IMPLEMENTATION, PARTNER IMPLEMENTATION"]
    )
    report.local_material = True
    report.local_material_amount = round(random.uniform(10000, 200000), 2)
    report.local_labour = True
    report.local_labour_amount = round(random.uniform(10000, 150000), 2)
    report.local_financial = random.choice([True, False])
    report.local_financial_amount = round(random.uniform(10000, 200000), 2) if report.local_financial else None
    report.local_other = False
    report.local_other_specify = None
    report.local_other_amount = None
    report.gov_tax_waiver = None
    report.gov_tax_waiver_amount = None
    report.gov_other = None
    report.gov_other_specify = None
    report.gov_other_amount = None
    report.project_implementations.append(
        ProjectImplementation(
            sector=sector,
            county=random.choice(COUNTIES),
            vulnerable_group=random.choice(["WOMEN", "YOUTH", "CHILDREN", "PWD"]),
            beneficiaries_no=random.randint(50, 1200),
            spending_per_county=spend,
            duration_years=round(random.uniform(1, 3), 1),
            completion_status=random.choice(["ONGOING", "COMPLETED"]),
            amount_spent_kenya=spend,
            amount_spent_other=0,
        )
    )


def apply_governance(report):
    report.number_of_directors = random.randint(3, 9)
    report.number_of_registered_members = random.randint(15, 300)
    report.number_of_board_meetings = random.randint(2, 6)
    report.election_frequency = "ANNUAL"
    report.date_last_agm = random_date(2024, 2025)
    report.date_last_election = random_date(2024, 2025)
    report.date_last_board_meeting = random_date(2024, 2025)
    report.membership_number_of_directors = report.number_of_directors
    report.membership_number_of_registered_members = report.number_of_registered_members
    report.membership_number_of_board_meetings = report.number_of_board_meetings
    report.membership_date_last_agm = report.date_last_agm
    report.membership_date_last_election = report.date_last_election
    report.submitter_fullname = fake.name().upper()
    report.signature = "SIGNED"
    report.submission_date = random_date(2025, 2025)
    for role in ["CHAIRPERSON", "SECRETARY", "TREASURER"]:
        report.officials.append(
            Official(
                role=role,
                name=fake.name().upper(),
                nationality="KENYAN",
                gender=random.choice(["MALE", "FEMALE"]),
                email=fake.email().lower(),
                residence=random.choice(COUNTIES),
                phone=f"07{random.randint(10000000, 99999999)}",
                kra_pin=fake.bothify(text="P#########A").upper(),
                professional_qualification=random.choice(["CPA", "MBA", "BSC"]),
                signature="SIGNED",
            )
        )


def apply_section_profile(report, profile):
    if profile["finance"]:
        apply_finance(report)
    else:
        nullify_finance(report)

    if profile["personnel"]:
        apply_personnel(report)
    else:
        nullify_personnel(report)

    if profile["projects"]:
        apply_projects(report)
    else:
        nullify_projects(report)

    if profile["governance"]:
        apply_governance(report)
    else:
        nullify_governance(report)

    report.collaboration_networking = None
    report.collaborations.clear()


def build_seed_report(user, index):
    _, compute_tf_risk = get_app_components()
    profiles = [
        {"finance": True, "personnel": True, "projects": True, "governance": True},
        {"finance": True, "personnel": True, "projects": False, "governance": True},
        {"finance": True, "personnel": False, "projects": True, "governance": False},
        {"finance": True, "personnel": True, "projects": True, "governance": False},
    ]
    report = base_report(user, index, "seed_check")
    apply_section_profile(report, profiles[(index - 1) % len(profiles)])
    report.update_risk_score(compute_tf_risk)
    return report


def build_ocr_report(user):
    _, compute_tf_risk = get_app_components()
    report = PBOReport(
        user_id=user.id,
        workflow_status="submitted",
        review_status="pending",
        data_source="seed_ocr",
        reporting_period_start=date(2024, 1, 1),
        reporting_period_end=date(2024, 12, 30),
        return_date=date.today(),
        pbo_name="NON COMMUNICABLE DISEASES ALLIANCE KENYA",
        pbo_registration_number="OP.218/051/12-0125/8213",
        pbo_registration_date=date(2012, 7, 25),
        kra_pin="P051440912M",
        postal_address="5337 00100 NAIROBI",
        physical_address="KMA APARTMENTS BLOCK C UNIT 5.2",
        telephone="020 2002481",
        cell_phone="0769 535856",
        email="info@ncdak.org",
        website="www.ncdak.org",
        social_media=None,
        contact_name="DR. CATHERINE KAREKEZI",
        contact_position="EXECUTIVE DIRECTOR",
        contact_telephone="020 2002481",
        contact_email="catherine.karekezi@ncdak.org",
        contact_nationality="KENYA",
        contact_gender=None,
        registration_number="OP.218/051/12-0125/8213",
        pin_number="P051440912M",
        date_of_registration=date(2012, 7, 25),
        scope="NATIONAL",
        counties="NAIROBI, MERU, KISII, VIHIGA, TAITA TAVETA, NYERI, MAKUENI, SIAYA, ISIOLO, UASIN GISHU, HOMA BAY, KISUMU, BUSIA, KAKAMEGA, BUNGOMA, KILIFI, KWALE, MOMBASA",
        assets_stolen=None,
        cash_balance_previous_year=13007595.00,
        cash_bank_balance=69195197.00,
        audited="YES",
        staff_kenyan_prev=21,
        staff_foreign_prev=0,
        staff_kenyan_current=21,
        staff_foreign_current=0,
        staff_kenyan_came_in=2,
        staff_foreign_came_in=0,
        staff_kenyan_left=0,
        staff_foreign_left=0,
        staff_other_kenyan_prev=0,
        staff_other_foreign_prev=0,
        staff_other_kenyan_current=0,
        staff_other_foreign_current=0,
        volunteers_kenyan_prev=2,
        volunteers_foreign_prev=0,
        volunteers_kenyan_current=1,
        volunteers_foreign_current=1,
        project_implementation_method="DIRECT IMPLEMENTATION",
        local_material=None,
        local_material_amount=None,
        local_labour=None,
        local_labour_amount=None,
        local_financial=None,
        local_financial_amount=None,
        local_other=None,
        local_other_specify=None,
        local_other_amount=None,
        gov_tax_waiver=None,
        gov_tax_waiver_amount=None,
        gov_other=None,
        gov_other_specify=None,
        gov_other_amount=None,
        number_of_directors=None,
        number_of_registered_members=None,
        number_of_board_meetings=None,
        election_frequency=None,
        election_frequency_other=None,
        date_last_agm=None,
        date_last_election=None,
        date_last_board_meeting=None,
        membership_number_of_directors=None,
        membership_number_of_registered_members=None,
        membership_number_of_board_meetings=None,
        membership_date_last_agm=None,
        membership_date_last_election=None,
        non_membership_number_of_directors=None,
        non_membership_number_of_board_meetings=None,
        non_membership_date_last_board_meeting=None,
        non_membership_date_last_election=None,
        submitter_fullname=None,
        signature=None,
        submission_date=date(2025, 3, 28),
        collaboration_networking=None,
        date_received="2025-03-28",
        received_by="HEADQUARTERS RECEIVED",
        submitted_at=utcnow(),
        last_activity_at=utcnow(),
        last_modified_by_id=user.id,
        edit_deadline=utcnow() + timedelta(days=30),
        form_submission_deadline=utcnow() + timedelta(days=30),
        imputed_fields=(
            "Seed row based on OCR and handwriting extraction from user-provided Form 14 images. "
            "Inactive or unreadable fields were intentionally left NULL."
        ),
        review_notes="OCR-derived dataset for functionality checks.",
    )

    report.assets.extend(
        [
            Asset(item="FURNITURE AND FITTINGS", number=40, value=233536.00),
            Asset(item="COMPUTERS AND ACCESSORIES", number=8, value=213573.00),
            Asset(item="OFFICE EQUIPMENT", number=3, value=280581.00),
        ]
    )

    report.donations.extend(
        [
            Donation(name="IOGT NTO MOVEMENT", category="CORPORATE DONORS", country="DENMARK", amount=8053983.00),
            Donation(name="NCD ALLIANCE KENYA", category="MEMBERSHIP SUBSCRIPTION", country="KENYA", amount=100750.00),
            Donation(name="NOVARTIS", category="CORPORATE DONORS", country="SWITZERLAND", amount=5050067.00),
            Donation(name="NOVO NORDISK FOUNDATION", category="CORPORATE DONORS", country="DENMARK", amount=20739926.00),
            Donation(name="EAST AFRICA NCD ALLIANCE", category="NON GOVERNMENTAL ORGANIZATION", country="UGANDA", amount=1377950.00),
            Donation(name="NCD ALLIANCE KENYA", category="NON GOVERNMENTAL ORGANIZATION", country="KENYA", amount=353947.00),
            Donation(name="NCD CHILD", category="NON GOVERNMENTAL ORGANIZATION", country="CANADA", amount=590877.00),
            Donation(name="MASS GEN. BRIGHAM HOSPITAL", category="CORPORATE DONORS", country="UNITED STATES OF AMERICA", amount=81741575.00),
            Donation(name="DANISH NCD ALLIANCE", category="NON GOVERNMENTAL ORGANIZATION", country="DENMARK", amount=5227636.00),
            Donation(name="NCD ALLIANCE", category="NON GOVERNMENTAL ORGANIZATION", country="SWITZERLAND", amount=3756130.00),
            Donation(name="GLOBAL HEALTH ADVOCACY INCUBATOR", category="NON GOVERNMENTAL ORGANIZATION", country="UNITED STATES OF AMERICA", amount=2742120.00),
            Donation(name="UNION FOR INTERNATIONAL CANCER CONTROL", category="NON GOVERNMENTAL ORGANIZATION", country="SWITZERLAND", amount=90900.00),
            Donation(name="WORLD HEALTH ORGANISATION", category="NON GOVERNMENTAL ORGANIZATION", country="SWITZERLAND", amount=525780.00),
            Donation(name="WORLD DIABETES FOUNDATION", category="FOUNDATION", country="DENMARK", amount=8093784.00),
            Donation(name="THE LUNG AMBITION ALLIANCE", category="NON GOVERNMENTAL ORGANIZATION", country="UNITED STATES OF AMERICA", amount=3051338.00),
            Donation(name="AFRICAN POPULATION & HEALTH RESEARCH", category="NON GOVERNMENTAL ORGANIZATION", country="KENYA", amount=1680309.00),
            Donation(name="ROCHE HOLDING AG", category="CORPORATE DONORS", country="SWITZERLAND", amount=181635.00),
        ]
    )

    report.payments.extend(
        [
            Payment(description="PURCHASE OF TANGIBLE ASSETS", kenya_amount=103760649.00, other_amount=0),
            Payment(description="ADMINISTRATION COSTS", kenya_amount=6443437.00, other_amount=0),
            Payment(description="PERSONNEL EMOLUMENTS & BENEFITS (LOCAL STAFF)", kenya_amount=33111999.00, other_amount=0),
            Payment(description="PAYMENTS TOTAL", kenya_amount=143316085.00, other_amount=0),
        ]
    )

    report.bank_accounts.append(
        BankAccount(
            bank_name="STANDARD CHARTERED BANK",
            branch="YAYA CENTER",
            account_number=None,
            currency="KES",
        )
    )

    report.project_implementations.append(
        ProjectImplementation(
            sector="HEALTH",
            county="NAIROBI",
            vulnerable_group=None,
            beneficiaries_no=None,
            spending_per_county=103760649.00,
            duration_years=None,
            completion_status=None,
            amount_spent_kenya=103760649.00,
            amount_spent_other=0,
        )
    )

    report.staff_biodata.extend(
        [
            StaffBiodata(category="KENYAN", prev_year=21, curr_year=21),
            StaffBiodata(category="FOREIGN", prev_year=0, curr_year=0),
        ]
    )
    report.volunteer_biodata.extend(
        [
            VolunteerBiodata(category="KENYAN", prev_year=2, curr_year=1),
            VolunteerBiodata(category="FOREIGN", prev_year=0, curr_year=1),
        ]
    )
    report.volunteer_privileges.append(
        VolunteerPrivilege(
            category="ALLOWANCES/STIPENDS",
            kenyan_volunteer=True,
            kenyan_intern=True,
            international_volunteer=False,
            international_intern=False,
        )
    )
    report.training_records.extend(
        [
            TrainingRecord(training_type="IN-HOUSE TRAINING", kenyan_count=5, international_count=0),
            TrainingRecord(training_type="PROFESSIONAL TRAINING", kenyan_count=1, international_count=0),
        ]
    )

    nullify_governance(report)
    report.update_risk_score(compute_tf_risk)
    return report


def _clean_lines(text):
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if re.sub(r"\s+", " ", line).strip()]


def _normalize_ocr_labels(text):
    normalized = text or ""
    normalized = re.sub(r"\bNGO['’]?S\b", "PBO", normalized, flags=re.I)
    normalized = re.sub(r"\bNGOS\b", "PBO", normalized, flags=re.I)
    normalized = re.sub(r"\bNGO\b", "PBO", normalized, flags=re.I)
    return normalized


def _normalize_feedback_key(value):
    return re.sub(r"\s+", " ", str(value or "").strip()).upper()


def get_ocr_feedback_profile():
    now = utcnow()
    loaded_at = OCR_FEEDBACK_CACHE.get("loaded_at")
    if loaded_at and OCR_FEEDBACK_CACHE.get("profile") and (now - loaded_at).total_seconds() < 300:
        return OCR_FEEDBACK_CACHE["profile"]

    tracked_fields = [
        "pbo_name",
        "postal_address",
        "physical_address",
        "telephone",
        "cell_phone",
        "contact_name",
        "contact_position",
        "contact_telephone",
        "registration_number",
    ]

    profile = {"corrections": {}, "field_penalties": {}}

    rows = (
        db.session.query(
            FieldChangeLog.field_name,
            FieldChangeLog.old_value,
            FieldChangeLog.new_value,
            func.count(FieldChangeLog.id),
        )
        .join(PBOReport, PBOReport.id == FieldChangeLog.report_id)
        .filter(
            func.upper(PBOReport.data_source) == "OCR_UPLOAD",
            FieldChangeLog.action == "report_updated",
            FieldChangeLog.field_name.in_(tracked_fields),
        )
        .group_by(FieldChangeLog.field_name, FieldChangeLog.old_value, FieldChangeLog.new_value)
        .all()
    )

    field_change_totals = {}
    for field_name, old_value, new_value, count in rows:
        field_change_totals[field_name] = field_change_totals.get(field_name, 0) + int(count or 0)
        normalized_old = _normalize_feedback_key(old_value)
        normalized_new = re.sub(r"\s+", " ", str(new_value or "").strip())
        if not normalized_old or not normalized_new or normalized_old == _normalize_feedback_key(normalized_new):
            continue
        if int(count or 0) < 2:
            continue
        field_map = profile["corrections"].setdefault(field_name, {})
        existing = field_map.get(normalized_old)
        if not existing or int(count) > existing["count"]:
            field_map[normalized_old] = {"value": normalized_new, "count": int(count)}

    for field_name, total in field_change_totals.items():
        profile["field_penalties"][field_name] = min(0.15, (total or 0) * 0.01)

    OCR_FEEDBACK_CACHE["loaded_at"] = now
    OCR_FEEDBACK_CACHE["profile"] = profile
    return profile


def _section_lines(lines, start_markers, end_markers=None):
    start_index = 0
    for index, line in enumerate(lines):
        upper = line.upper()
        if any(marker in upper for marker in start_markers):
            start_index = index
            break

    end_index = len(lines)
    if end_markers:
        for index in range(start_index + 1, len(lines)):
            upper = lines[index].upper()
            if any(marker in upper for marker in end_markers):
                end_index = index
                break

    return lines[start_index:end_index]


def _value_after_label(lines, label, stop_labels=None):
    label_upper = label.upper()
    stop_labels = [item.upper() for item in (stop_labels or [])]
    for index, line in enumerate(lines):
        upper = line.upper()
        if not (
            upper == label_upper
            or upper.startswith(f"{label_upper} ")
            or upper.startswith(f"{label_upper}:")
            or upper.startswith(f"{label_upper})")
        ):
            continue
        after = line[upper.index(label_upper) + len(label_upper):].strip(" :-\t")
        if after:
            return after
        for next_index in range(index + 1, len(lines)):
            candidate = lines[next_index].strip()
            candidate_upper = candidate.upper()
            if not candidate:
                continue
            if any(stop in candidate_upper for stop in stop_labels):
                break
            if ":" not in candidate and len(candidate) > 1:
                return candidate
    return None


def _extract_email(text):
    match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.I)
    return match.group(0).lower() if match else None


def _extract_website(text):
    match = re.search(r"(?:https?://)?(?:www\.)?[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.I)
    if not match:
        return None
    value = match.group(0)
    return value if value.lower().startswith("http") or value.lower().startswith("www.") else None


def _extract_between_labels(text, start_label, end_label):
    pattern = re.escape(start_label) + r"\s*(.*?)\s*" + re.escape(end_label)
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip(" :-\t")
    return value or None


def _extract_section_a_pbo_name(lines):
    stop_markers = [
        "POSTAL ADDRESS",
        "PHYSICAL ADDRESS",
        "TELEPHONE",
        "CELL PHONE",
        "EMAIL",
        "A2)",
        "A3)",
    ]

    for index, line in enumerate(lines):
        upper = line.upper()
        if upper in {"NAME", "A1) NAME", "A1 NAME"} or upper.startswith("NAME "):
            for next_index in range(index + 1, len(lines)):
                candidate = lines[next_index].strip()
                candidate_upper = candidate.upper()
                if not candidate:
                    continue
                if any(marker in candidate_upper for marker in stop_markers):
                    break
                if "SECTION A" in candidate_upper or "GENERAL INFORMATION" in candidate_upper:
                    continue
                if "NAME AND ADDRESS OF PBO" in candidate_upper:
                    continue
                if len(candidate) > 2:
                    return candidate

    for index, line in enumerate(lines):
        upper = line.upper()
        if "NAME AND ADDRESS OF PBO" not in upper:
            continue
        for next_index in range(index + 1, len(lines)):
            candidate = lines[next_index].strip()
            candidate_upper = candidate.upper()
            if not candidate:
                continue
            if candidate_upper in {"NAME", "A1) NAME", "A1 NAME"}:
                continue
            if any(marker in candidate_upper for marker in stop_markers):
                break
            if len(candidate) > 2:
                return candidate

    return None


def _extract_currency_amount(text, anchor):
    pattern = re.escape(anchor) + r".{0,160}?([0-9][0-9,]*(?:\.\d{2})?)"
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_phone_candidates(text):
    return re.findall(r"(?:\+?254[\d\s-]{8,}|0[\d\s-]{8,})", text)


def _field_specific_cleanup(field_name, value):
    if value in (None, ""):
        return None

    cleaned = re.sub(r"\s+", " ", str(value)).strip(" :-\t")
    cleaned = _normalize_ocr_labels(cleaned)

    if field_name in {"telephone", "cell_phone", "contact_telephone"}:
        digits = re.sub(r"[^\d+]", "", cleaned)
        return digits or None

    if field_name in {"postal_address", "physical_address", "pbo_name", "contact_name", "contact_position", "registration_number"}:
        cleaned = cleaned.upper()
        cleaned = cleaned.replace("P. 0.", "P.O.")
        cleaned = cleaned.replace("P 0 BOX", "P.O BOX")
        cleaned = cleaned.replace("POSTALAD DRESS", "POSTAL ADDRESS")
        cleaned = cleaned.replace("PHYSICALAD DRESS", "PHYSICAL ADDRESS")
        cleaned = cleaned.replace("A1O", "A10")
        cleaned = cleaned.replace("A1 ", "A1 ")
        return cleaned

    return cleaned


def _estimate_text_structure_confidence(text):
    upper = (text or "").upper()
    if not upper.strip():
        return 0.0

    markers = [
        "SECTION A",
        "GENERAL INFORMATION",
        "A1",
        "NAME",
        "POSTAL ADDRESS",
        "PHYSICAL ADDRESS",
        "TELEPHONE",
        "CELL PHONE",
        "A2",
        "CONTACT PERSON",
        "A3",
        "REGISTRATION NUMBER",
        "DATE OF REGISTRATION",
    ]
    marker_hits = sum(1 for marker in markers if marker in upper)
    phone_hits = len(_extract_phone_candidates(upper))
    line_count = len([line for line in upper.splitlines() if line.strip()])
    density_bonus = 1 if line_count >= 8 else 0
    return min(1.0, ((marker_hits + min(phone_hits, 2) + density_bonus) / 12))


def _should_use_adaptive_tesseract(text):
    return _estimate_text_structure_confidence(text) < 0.58


def _parse_date_text(value):
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d/%b/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _friendly_ocr_error(message):
    cleaned = re.sub(r"\s+", " ", str(message or "")).strip()
    return cleaned or "OCR could not read the supplied file."


def ensure_tesseract_available():
    if shutil.which("tesseract"):
        return
    raise RuntimeError(
        "Tesseract OCR is not installed or not available on PATH. Install tesseract, then retry the OCR import."
    )


def ensure_pdf_tools_available():
    missing = [tool for tool in ("pdfinfo", "pdftoppm") if not shutil.which(tool)]
    if not missing:
        return
    raise RuntimeError(
        f"PDF OCR requires {', '.join(missing)} to be installed and available on PATH."
    )


def ensure_ocrmypdf_available():
    if shutil.which("ocrmypdf"):
        return
    raise RuntimeError(
        "OCRmyPDF is not installed or not available on PATH. Install ocrmypdf to enable the PDF verification pass."
    )


def ensure_opencv_available():
    if cv2 is not None:
        return
    raise RuntimeError(
        "OpenCV is not installed or not available. Install opencv-python-headless to enable OCR image preprocessing."
    )


def get_paddle_ocr_reader():
    global PADDLE_OCR_READER
    if PADDLE_OCR_READER is not None:
        return PADDLE_OCR_READER
    try:
        from paddleocr import PaddleOCR
    except Exception as exc:
        raise RuntimeError(
            "PaddleOCR is not installed or not available. Install paddleocr and its runtime dependencies to enable the secondary OCR engine."
        ) from exc

    PADDLE_OCR_READER = PaddleOCR(
        use_angle_cls=True,
        lang='en',
        show_log=False,
    )
    return PADDLE_OCR_READER


def _parse_tesseract_osd_rotation(osd_text):
    match = re.search(r"Rotate:\s+(\d+)", osd_text or "")
    if not match:
        return 0
    try:
        value = int(match.group(1)) % 360
    except ValueError:
        return 0
    return value if value in {0, 90, 180, 270} else 0


def _rotate_image_upright(image):
    fd, temp_input_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(temp_input_path, image)
    try:
        osd_result = subprocess.run(
            ["tesseract", temp_input_path, "stdout", "--psm", "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        rotation = _parse_tesseract_osd_rotation(osd_result.stdout)
    finally:
        if os.path.exists(temp_input_path):
            os.remove(temp_input_path)

    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE), rotation
    if rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180), rotation
    if rotation == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE), rotation
    return image, 0


def preprocess_ocr_image(image_path):
    ensure_opencv_available()
    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(
            f"Could not read {os.path.basename(image_path)}. Use a clear JPG, PNG, or PDF page export."
        )

    image, applied_rotation = _rotate_image_upright(image)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scaled = cv2.resize(gray, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.GaussianBlur(scaled, (5, 5), 0)
    normalized = cv2.normalize(denoised, None, 0, 255, cv2.NORM_MINMAX)
    _, thresholded = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    fd, temp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(temp_path, thresholded)
    return temp_path, applied_rotation


def _write_temp_image(image):
    ensure_opencv_available()
    fd, temp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    cv2.imwrite(temp_path, image)
    return temp_path


def _build_preprocessed_views(processed_path):
    image = cv2.imread(processed_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return [processed_path], []

    generated_paths = []
    view_paths = [processed_path]

    enlarged = cv2.resize(image, None, fx=2.4, fy=2.4, interpolation=cv2.INTER_CUBIC)
    adaptive = cv2.adaptiveThreshold(
        enlarged,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )
    sharpen_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, sharpen_kernel)
    adaptive_path = _write_temp_image(adaptive)
    generated_paths.append(adaptive_path)
    view_paths.append(adaptive_path)

    high_contrast = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    high_contrast = cv2.resize(high_contrast, None, fx=2.1, fy=2.1, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(high_contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bw_path = _write_temp_image(bw)
    generated_paths.append(bw_path)
    view_paths.append(bw_path)

    return view_paths, generated_paths


def _build_neighbor_tiles(image_path):
    ensure_opencv_available()
    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []

    height, width = image.shape[:2]
    if height < 50 or width < 50:
        return []

    generated_paths = []
    row_slices = [
        (0.0, 0.42),
        (0.25, 0.68),
        (0.52, 1.0),
    ]
    col_slices = [
        (0.0, 0.62),
        (0.18, 0.82),
        (0.38, 1.0),
    ]

    for row_start, row_end in row_slices:
        for col_start, col_end in col_slices:
            y1 = max(0, int(height * row_start))
            y2 = min(height, int(height * row_end))
            x1 = max(0, int(width * col_start))
            x2 = min(width, int(width * col_end))
            tile = image[y1:y2, x1:x2]
            if tile.size == 0:
                continue
            zoomed_tile = cv2.resize(tile, None, fx=2.8, fy=2.8, interpolation=cv2.INTER_CUBIC)
            _, tile_bw = cv2.threshold(zoomed_tile, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            tile_path = _write_temp_image(tile_bw)
            generated_paths.append(tile_path)

    return generated_paths


def _run_tesseract_on_processed_image(processed_path):
    dense_text = _run_tesseract_pass(processed_path, 6)
    sparse_text = _run_tesseract_pass(processed_path, 11)
    merged_text = _merge_ocr_texts([dense_text, sparse_text])
    if not merged_text.strip():
        raise RuntimeError(
            "Tesseract OCR did not detect readable text. Try a clearer scan, a higher-resolution image, or straighten the page."
        )
    return merged_text


def _run_tesseract_multiview(processed_path, include_tiles=True):
    view_paths, generated_variant_paths = _build_preprocessed_views(processed_path)
    generated_tile_paths = []
    texts = []
    try:
        for view_path in view_paths:
            try:
                texts.append(_run_tesseract_on_processed_image(view_path))
            except RuntimeError:
                continue

        if include_tiles:
            # Read overlapping zoomed tiles to capture microframes and neighboring fields.
            tile_source = view_paths[-1] if view_paths else processed_path
            generated_tile_paths = _build_neighbor_tiles(tile_source)
            for tile_path in generated_tile_paths:
                try:
                    texts.append(_run_tesseract_on_processed_image(tile_path))
                except RuntimeError:
                    continue
    finally:
        for path in generated_variant_paths + generated_tile_paths:
            if path != processed_path and os.path.exists(path):
                os.remove(path)

    merged_text = _merge_ocr_texts(texts)
    if not merged_text.strip():
        raise RuntimeError(
            "Tesseract OCR did not detect readable text. Try a clearer scan, a higher-resolution image, or straighten the page."
        )
    return merged_text


def _extract_paddle_lines(ocr_result):
    extracted_lines = []
    for block in ocr_result or []:
        if not block:
            continue
        if isinstance(block, dict):
            text_value = block.get('rec_text') or ''
            if text_value:
                extracted_lines.append(text_value)
            continue
        if isinstance(block, (list, tuple)):
            for item in block:
                if not item:
                    continue
                if isinstance(item, dict):
                    text_value = item.get('rec_text') or ''
                    if text_value:
                        extracted_lines.append(text_value)
                    continue
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    text_info = item[1]
                    if isinstance(text_info, (list, tuple)) and text_info:
                        text_value = text_info[0]
                        if text_value:
                            extracted_lines.append(str(text_value))
    return extracted_lines


def run_paddle_ocr(image_path):
    reader = get_paddle_ocr_reader()
    try:
        ocr_result = reader.ocr(image_path, cls=True)
    except Exception as exc:
        raise RuntimeError(_friendly_ocr_error(exc)) from exc

    merged_text = _merge_ocr_texts(["\n".join(_extract_paddle_lines(ocr_result))])
    if not merged_text.strip():
        raise RuntimeError(
            "PaddleOCR did not detect readable text. Try a clearer scan, a higher-resolution image, or straighten the page."
        )
    return merged_text


def run_ocrmypdf_sidecar_text(pdf_path):
    ensure_ocrmypdf_available()
    with tempfile.TemporaryDirectory(prefix="ocrmypdf_verify_") as temp_dir:
        output_pdf = os.path.join(temp_dir, f"{uuid.uuid4().hex}.pdf")
        sidecar_txt = os.path.join(temp_dir, f"{uuid.uuid4().hex}.txt")
        result = subprocess.run(
            [
                "ocrmypdf",
                "--skip-text",
                "--force-ocr",
                "--sidecar",
                sidecar_txt,
                "--output-type",
                "pdf",
                pdf_path,
                output_pdf,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "").strip() or "OCRmyPDF verification failed."
            raise RuntimeError(_friendly_ocr_error(stderr))
        if not os.path.exists(sidecar_txt):
            return ""
        with open(sidecar_txt, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()


def _run_tesseract_pass(image_path, psm):
    result = subprocess.run(
        [
            "tesseract",
            image_path,
            "stdout",
            "--oem",
            "1",
            "--psm",
            str(psm),
            "-c",
            "preserve_interword_spaces=1",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "OCR process failed."
        raise RuntimeError(stderr)
    return result.stdout


def _merge_ocr_texts(texts):
    merged_lines = []
    seen = set()
    for text in texts:
        for raw_line in text.splitlines():
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line:
                continue
            key = line.upper()
            if key in seen:
                continue
            seen.add(key)
            merged_lines.append(line)
    return "\n".join(merged_lines)


def run_tesseract_ocr(image_path):
    ensure_tesseract_available()
    processed_path, applied_rotation = preprocess_ocr_image(image_path)
    try:
        return _run_tesseract_multiview(processed_path)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "Tesseract OCR is not installed or not available on PATH. Install tesseract, then retry the OCR import."
        ) from exc
    except RuntimeError as exc:
        message = _friendly_ocr_error(exc)
        if applied_rotation:
            message = f"{message} The page was auto-rotated by {applied_rotation} degrees before reading."
        raise RuntimeError(message) from exc
    finally:
        if os.path.exists(processed_path):
            os.remove(processed_path)


def run_form14_ocr_analysis(image_path):
    processed_path, applied_rotation = preprocess_ocr_image(image_path)
    engine_errors = []
    engine_outputs = []
    tesseract_text = ""
    paddle_text = ""
    adaptive_used = False
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {}
            try:
                ensure_tesseract_available()
                futures["tesseract"] = executor.submit(_run_tesseract_on_processed_image, processed_path)
            except Exception as exc:
                engine_errors.append(f"Tesseract: {_friendly_ocr_error(exc)}")

            futures["paddle"] = executor.submit(run_paddle_ocr, processed_path)

            for engine_name, future in futures.items():
                try:
                    result_text = future.result()
                    if engine_name == "tesseract":
                        tesseract_text = result_text
                    elif engine_name == "paddle":
                        paddle_text = result_text
                except Exception as exc:
                    engine_errors.append(f"{'Tesseract' if engine_name == 'tesseract' else 'PaddleOCR'}: {_friendly_ocr_error(exc)}")

        engine_outputs.extend([text for text in (tesseract_text, paddle_text) if text.strip()])

        if tesseract_text and _should_use_adaptive_tesseract(tesseract_text):
            adaptive_used = True
            try:
                adaptive_tesseract_text = _run_tesseract_multiview(processed_path, include_tiles=True)
                engine_outputs.append(adaptive_tesseract_text)
                tesseract_text = _merge_ocr_texts([tesseract_text, adaptive_tesseract_text])
            except Exception as exc:
                engine_errors.append(f"Tesseract adaptive pass: {_friendly_ocr_error(exc)}")

        merged_text = _merge_ocr_texts(engine_outputs)
        if merged_text.strip():
            confidence = _estimate_text_structure_confidence(merged_text)
            return {
                "text": merged_text,
                "confidence": confidence,
                "adaptive_used": adaptive_used,
                "engine_errors": engine_errors,
            }

        combined_error = " ".join(engine_errors).strip() or (
            "OCR did not detect readable text. Try a clearer scan, a higher-resolution image, or straighten the page."
        )
        if applied_rotation:
            combined_error = f"{combined_error} The page was auto-rotated by {applied_rotation} degrees before reading."
        raise RuntimeError(combined_error)
    finally:
        if os.path.exists(processed_path):
            os.remove(processed_path)


def run_form14_ocr(image_path):
    return run_form14_ocr_analysis(image_path)["text"]


def render_pdf_to_images(pdf_path):
    ensure_pdf_tools_available()
    with tempfile.TemporaryDirectory(prefix="ocr_pdf_") as temp_dir:
        prefix = os.path.join(temp_dir, "page")
        result = subprocess.run(
            ["pdftoppm", "-png", "-r", "240", pdf_path, prefix],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "PDF conversion failed."
            raise RuntimeError(_friendly_ocr_error(stderr))

        image_paths = sorted(
            os.path.join(temp_dir, name)
            for name in os.listdir(temp_dir)
            if name.lower().endswith(".png")
        )
        if not image_paths:
            raise RuntimeError("No readable pages were generated from the PDF.")

        rendered_paths = []
        for image_path in image_paths:
            fd, temp_path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            with open(image_path, "rb") as source, open(temp_path, "wb") as target:
                target.write(source.read())
            rendered_paths.append(temp_path)
        return rendered_paths


def parse_form14_texts(page_texts):
    payload = {
        "fields": {},
        "assets": [],
        "payments": [],
        "bank_accounts": [],
        "project_implementations": [],
        "staff_biodata": [],
        "volunteer_biodata": [],
        "volunteer_privileges": [],
        "training_records": [],
        "warnings": [],
    }

    combined_text = _normalize_ocr_labels("\n".join(page_texts))
    lines = _clean_lines(combined_text)
    upper_text = combined_text.upper()

    general_lines = _section_lines(lines, ["SECTION A", "A1)"], ["SECTION B", "B1)"])
    contact_lines = _section_lines(lines, ["A2)", "NAME AND ADDRESS OF CONTACT PERSON"], ["A3", "A4", "A5"])
    counties_lines = _section_lines(lines, ["A6)", "COUNTIES OF OPERATION"], ["SECTION B", "B1)"])
    personnel_lines = _section_lines(lines, ["SECTION C", "PERSONNEL"], [])
    general_section_text = "\n".join(general_lines)
    contact_section_text = "\n".join(contact_lines)

    pbo_name = (
        _extract_section_a_pbo_name(general_lines)
        or _extract_between_labels(general_section_text, "NAME", "POSTAL ADDRESS")
        or _value_after_label(general_lines, "NAME", ["POSTAL ADDRESS", "PHYSICAL ADDRESS"])
    )
    postal_address = (
        _extract_between_labels(general_section_text, "POSTAL ADDRESS", "PHYSICAL ADDRESS")
        or _value_after_label(general_lines, "POSTAL ADDRESS", ["PHYSICAL ADDRESS"])
    )
    physical_address = (
        _extract_between_labels(general_section_text, "PHYSICAL ADDRESS", "TELEPHONE")
        or _value_after_label(general_lines, "PHYSICAL ADDRESS", ["TELEPHONE"])
    )
    phones = _extract_phone_candidates("\n".join(general_lines))
    contact_phones = _extract_phone_candidates("\n".join(contact_lines))

    payload["fields"].update(
        {
            "pbo_name": pbo_name,
            "postal_address": postal_address,
            "physical_address": physical_address,
            "telephone": phones[0].strip() if phones else None,
            "cell_phone": phones[1].strip() if len(phones) > 1 else None,
            "email": _extract_email("\n".join(general_lines)),
            "website": _extract_website("\n".join(general_lines)),
            "contact_name": (
                _extract_between_labels(contact_section_text, "NAME", "POSITION")
                or _value_after_label(contact_lines, "NAME", ["POSITION", "TELEPHONE"])
            ),
            "contact_position": (
                _extract_between_labels(contact_section_text, "POSITION", "TELEPHONE")
                or _value_after_label(contact_lines, "POSITION", ["TELEPHONE", "CELL PHONE"])
            ),
            "contact_telephone": contact_phones[0].strip() if contact_phones else None,
            "contact_email": _extract_email("\n".join(contact_lines)),
            "contact_nationality": _value_after_label(contact_lines, "NATIONALITY", []),
            "registration_number": _value_after_label(general_lines, "A3 A) REGISTRATION NUMBER", ["A3 B", "PBO PIN NUMBER"])
            or _value_after_label(general_lines, "REGISTRATION NUMBER", ["PBO PIN NUMBER"]),
            "pin_number": _value_after_label(general_lines, "A3 B) PBO PIN NUMBER", ["A4", "DATE OF REGISTRATION"])
            or _value_after_label(general_lines, "PIN NUMBER", ["A4", "DATE OF REGISTRATION"]),
            "date_of_registration": _parse_date_text(
                _value_after_label(general_lines, "A4) DATE OF REGISTRATION", ["A5", "SCOPE OF PBO"])
                or _value_after_label(general_lines, "DATE OF REGISTRATION", ["A5", "SCOPE OF PBO"])
            ),
            "scope": None,
            "counties": " ".join(counties_lines[1:]).strip() if len(counties_lines) > 1 else None,
            "cash_balance_previous_year": _extract_currency_amount(upper_text, "CASH AND BANK BALANCES CARRIED FORWARD FROM PREVIOUS YEAR"),
            "cash_bank_balance": _extract_currency_amount(upper_text, "CASH & BANK BALANCE")
            or _extract_currency_amount(upper_text, "CASH AND BANK BALANCE"),
            "audited": "YES" if "ACCOUNTS AUDITED IN THE LAST FINANCIAL YEAR" in upper_text and "YES" in upper_text else None,
        }
    )

    bank_name = _value_after_label(lines, "BANK", ["BRANCH"])
    branch = _value_after_label(lines, "BRANCH", [])
    if bank_name or branch:
        payload["bank_accounts"].append(
            {"bank_name": bank_name, "branch": branch, "account_number": None, "currency": "KES"}
        )

    health_spend = _extract_currency_amount(upper_text, "HEALTH")
    if health_spend:
        payload["project_implementations"].append(
            {
                "sector": "HEALTH",
                "county": "NAIROBI",
                "vulnerable_group": None,
                "beneficiaries_no": None,
                "spending_per_county": health_spend,
                "duration_years": None,
                "completion_status": None,
                "amount_spent_kenya": health_spend,
                "amount_spent_other": 0,
            }
        )
        payload["fields"]["project_implementation_method"] = "DIRECT IMPLEMENTATION"

    asset_patterns = [
        "FURNITURE AND FITTINGS",
        "COMPUTERS AND ACCESSORIES",
        "OFFICE EQUIPMENT",
    ]
    for asset_name in asset_patterns:
        asset_match = re.search(
            re.escape(asset_name) + r".{0,100}?([0-9]{1,4}).{0,80}?([0-9][0-9,]*(?:\.\d{2})?)",
            upper_text,
            flags=re.S,
        )
        if asset_match:
            payload["assets"].append(
                {
                    "item": asset_name,
                    "number": int(asset_match.group(1)),
                    "value": float(asset_match.group(2).replace(",", "")),
                }
            )

    section_c_text = "\n".join(personnel_lines).upper()
    staff_patterns = {
        "staff_kenyan_prev": r"PREVIOUS YEAR.{0,40}?([0-9]{1,3})",
        "staff_kenyan_current": r"CURRENT YEAR.{0,40}?([0-9]{1,3})",
        "staff_kenyan_came_in": r"STAFF WHO CAME IN THIS YEAR.{0,40}?([0-9]{1,3})",
        "staff_kenyan_left": r"STAFF WHO LEFT THIS YEAR.{0,40}?([0-9]{1,3})",
    }
    for field_name, pattern in staff_patterns.items():
        match = re.search(pattern, section_c_text, flags=re.S)
        if match:
            payload["fields"][field_name] = int(match.group(1))

    training_patterns = [
        ("IN-HOUSE TRAINING", "IN-HOUSE TRAINING"),
        ("PROFESSIONAL TRAINING", "PROFESSIONAL TRAINING"),
    ]
    for training_type, anchor in training_patterns:
        match = re.search(re.escape(anchor) + r".{0,40}?([0-9]{1,3})", section_c_text, flags=re.S)
        if match:
            payload["training_records"].append(
                {
                    "training_type": training_type,
                    "kenyan_count": int(match.group(1)),
                    "international_count": 0,
                }
            )

    if payload["fields"].get("staff_kenyan_prev") is not None or payload["fields"].get("staff_kenyan_current") is not None:
        payload["staff_biodata"].append(
            {
                "category": "KENYAN",
                "prev_year": payload["fields"].get("staff_kenyan_prev"),
                "curr_year": payload["fields"].get("staff_kenyan_current"),
            }
        )

    if not payload["fields"].get("pbo_name"):
        payload["warnings"].append("Could not confidently extract the PBO name from the uploaded images.")
    if not payload["fields"].get("registration_number"):
        payload["warnings"].append("Could not confidently extract the registration number.")

    return payload


def apply_ocr_feedback_to_fields(fields, feedback_profile):
    corrections = (feedback_profile or {}).get("corrections", {})
    corrected_fields = dict(fields)
    applied_feedback = []

    for field_name, value in list(corrected_fields.items()):
        cleaned_value = _field_specific_cleanup(field_name, value)
        corrected_fields[field_name] = cleaned_value
        normalized_key = _normalize_feedback_key(cleaned_value)
        replacement = corrections.get(field_name, {}).get(normalized_key)
        if replacement:
            corrected_fields[field_name] = _field_specific_cleanup(field_name, replacement["value"])
            applied_feedback.append(
                f"{field_name}: matched prior OCR correction pattern ({replacement['count']} similar edits)"
            )

    return corrected_fields, applied_feedback


def estimate_parsed_form_confidence(parsed_payload, raw_text, feedback_profile):
    fields = parsed_payload.get("fields", {})
    base_score = _estimate_text_structure_confidence(raw_text)
    present_fields = sum(
        1 for key in ("pbo_name", "postal_address", "physical_address", "telephone", "registration_number")
        if fields.get(key)
    )
    field_score = min(0.35, present_fields * 0.07)
    penalty = 0.0
    for field_name, value in fields.items():
        if value and field_name in (feedback_profile or {}).get("field_penalties", {}):
            penalty += feedback_profile["field_penalties"][field_name]
    return max(0.0, min(1.0, base_score + field_score - penalty))


def build_report_from_ocr_upload(user, page_file_paths, original_filenames=None):
    _, compute_tf_risk = get_app_components()
    page_texts = []
    file_results = []
    errors = []
    generated_image_paths = []
    feedback_profile = get_ocr_feedback_profile()

    for index, file_path in enumerate(page_file_paths):
        filename = original_filenames[index] if original_filenames and index < len(original_filenames) else os.path.basename(file_path)
        try:
            source_paths = [file_path]
            pdf_verification_text = ""
            if filename.lower().endswith(".pdf"):
                source_paths = render_pdf_to_images(file_path)
                generated_image_paths.extend(source_paths)

            combined_texts = []
            page_confidences = []
            for source_path in source_paths:
                ocr_result = run_form14_ocr_analysis(source_path)
                combined_texts.append(ocr_result["text"])
                page_confidences.append(float(ocr_result.get("confidence", 0.0) or 0.0))

            mean_confidence = sum(page_confidences) / len(page_confidences) if page_confidences else 0.0
            needs_pdf_verification = filename.lower().endswith(".pdf") and mean_confidence < 0.7
            if needs_pdf_verification:
                try:
                    pdf_verification_text = run_ocrmypdf_sidecar_text(file_path)
                except Exception as exc:
                    errors.append(f"{filename} OCRmyPDF verification skipped: {_friendly_ocr_error(exc)}")

            if pdf_verification_text.strip():
                combined_texts.append(pdf_verification_text)
            text = "\n".join(part for part in combined_texts if part.strip())
            page_texts.append(text)
            file_results.append(
                {
                    "filename": filename,
                    "status": "ocr_processed",
                    "text": text,
                    "error": None,
                }
            )
        except Exception as exc:
            errors.append(f"{filename}: {_friendly_ocr_error(exc)}")
            file_results.append(
                {
                    "filename": filename,
                    "status": "ocr_failed",
                    "text": "",
                    "error": _friendly_ocr_error(exc),
                }
            )
    for temp_image_path in generated_image_paths:
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)

    if not page_texts:
        raise ValueError("OCR could not read any of the uploaded images.")

    parsed = parse_form14_texts(page_texts)
    corrected_fields, applied_feedback = apply_ocr_feedback_to_fields(parsed["fields"], feedback_profile)
    parsed["fields"].update(corrected_fields)
    parsed_confidence = estimate_parsed_form_confidence(parsed, "\n".join(page_texts), feedback_profile)
    if applied_feedback:
        parsed["warnings"].append("Feedback-assisted OCR corrections applied: " + "; ".join(applied_feedback[:4]))
    if parsed_confidence < 0.6:
        parsed["warnings"].append("OCR confidence is low. Please review the extracted values carefully before saving.")
    fields = parsed["fields"]
    report = PBOReport(
        user_id=user.id,
        workflow_status="draft",
        review_status="pending",
        data_source="ocr_upload",
        reporting_period_start=date(date.today().year - 1, 1, 1),
        reporting_period_end=date(date.today().year - 1, 12, 31),
        return_date=date.today(),
        pbo_name=fields.get("pbo_name"),
        pbo_registration_number=fields.get("registration_number"),
        pbo_registration_date=fields.get("date_of_registration"),
        kra_pin=fields.get("pin_number"),
        postal_address=fields.get("postal_address"),
        physical_address=fields.get("physical_address"),
        telephone=fields.get("telephone"),
        cell_phone=fields.get("cell_phone"),
        email=fields.get("email"),
        website=fields.get("website"),
        contact_name=fields.get("contact_name"),
        contact_position=fields.get("contact_position"),
        contact_telephone=fields.get("contact_telephone"),
        contact_email=fields.get("contact_email"),
        contact_nationality=fields.get("contact_nationality"),
        registration_number=fields.get("registration_number"),
        pin_number=fields.get("pin_number"),
        date_of_registration=fields.get("date_of_registration"),
        scope=fields.get("scope"),
        counties=fields.get("counties"),
        cash_balance_previous_year=fields.get("cash_balance_previous_year"),
        cash_bank_balance=fields.get("cash_bank_balance"),
        audited=fields.get("audited"),
        staff_kenyan_prev=fields.get("staff_kenyan_prev"),
        staff_kenyan_current=fields.get("staff_kenyan_current"),
        staff_kenyan_came_in=fields.get("staff_kenyan_came_in"),
        staff_kenyan_left=fields.get("staff_kenyan_left"),
        project_implementation_method=fields.get("project_implementation_method"),
        submitted_at=utcnow(),
        last_activity_at=utcnow(),
        last_modified_by_id=user.id,
        imputed_fields=(
            "OCR import created from uploaded page images. "
            f"Estimated OCR confidence: {parsed_confidence:.2f}."
        ),
        review_notes=(
            "Auto-created from OCR upload. "
            "Please review extracted values before submission."
        ),
    )

    report.staff_foreign_prev = None
    report.staff_foreign_current = None
    report.staff_foreign_came_in = None
    report.staff_foreign_left = None
    report.staff_other_kenyan_prev = None
    report.staff_other_foreign_prev = None
    report.staff_other_kenyan_current = None
    report.staff_other_foreign_current = None
    report.volunteers_kenyan_prev = None
    report.volunteers_foreign_prev = None
    report.volunteers_kenyan_current = None
    report.volunteers_foreign_current = None

    for item in parsed["assets"]:
        report.assets.append(Asset(**item))
    for item in parsed["payments"]:
        report.payments.append(Payment(**item))
    for item in parsed["bank_accounts"]:
        report.bank_accounts.append(BankAccount(**item))
    for item in parsed["project_implementations"]:
        report.project_implementations.append(ProjectImplementation(**item))
    for item in parsed["staff_biodata"]:
        report.staff_biodata.append(StaffBiodata(**item))
    for item in parsed["volunteer_biodata"]:
        report.volunteer_biodata.append(VolunteerBiodata(**item))
    for item in parsed["volunteer_privileges"]:
        report.volunteer_privileges.append(VolunteerPrivilege(**item))
    for item in parsed["training_records"]:
        report.training_records.append(TrainingRecord(**item))

    report.update_risk_score(compute_tf_risk)
    return report, file_results, parsed["warnings"] + errors


def seed_reports(count=10, reset=False):
    app, _ = get_app_components()
    with app.app_context():
        user = ensure_test_user()

        if reset:
            for existing in PBOReport.query.filter(func.upper(PBOReport.data_source).in_(["SEED_CHECK", "SEED_OCR"])).all():
                db.session.delete(existing)
            db.session.commit()

        standard_count = max(0, count - 1)
        created = 0

        for index in range(1, standard_count + 1):
            db.session.add(build_seed_report(user, index))
            created += 1

        db.session.add(build_ocr_report(user))
        created += 1

        db.session.commit()
        print(f"Inserted {created} functionality datasets including 1 OCR-derived row.")
        print("Test user: functional-check@example.com / password: FunctionalCheck@123")


def main():
    parser = argparse.ArgumentParser(
        description="Seed Form 14 functionality datasets using active section profiles."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Total number of datasets to create, including the OCR-derived row.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing seed_check and seed_ocr reports before seeding.",
    )
    args = parser.parse_args()
    seed_reports(count=max(1, args.count), reset=args.reset)


if __name__ == "__main__":
    main()
