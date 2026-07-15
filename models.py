
from flask_sqlalchemy import SQLAlchemy
from datetime import date, datetime, timezone
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import String, Text
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()
db.Text = Text().with_variant(LONGTEXT(), 'mysql')

REPORT_WORKFLOW_STATUS_VALUES = (
    'draft',
    'validated',
    'submitted',
    'in_review',
    'returned',
    'approved',
)
REPORT_REVIEW_STATUS_VALUES = (
    'pending',
    'reviewed',
    'returned',
    'approved',
)
IMPORT_BATCH_STATUS_VALUES = (
    'pending',
    'processing',
    'completed',
    'completed_with_errors',
    'failed',
)
UPLOADED_FILE_STATUS_VALUES = (
    'uploaded',
    'processing',
    'processed',
    'ocr_processed',
    'ocr_failed',
)


def build_status_check_constraint(column_name, allowed_values):
    allowed_sql = ", ".join(f"'{value}'" for value in allowed_values)
    return f"lower(trim({column_name})) in ({allowed_sql})"


def utc_now():
    return datetime.now(timezone.utc)


RETURN_DATE_PLACEHOLDER = date(9999, 9, 9)


# User model for authentication and roles
from flask_login import UserMixin

class User(UserMixin, db.Model):


        def get_id(self):
            return str(self.id)
        
        __tablename__ = 'users_for_form14'
        id = db.Column(db.Integer, primary_key=True)
        email = db.Column(db.String(320), unique=True, nullable=True)
        full_name = db.Column(db.String(255), nullable=True)
        phone = db.Column(db.String(320), nullable=True)
        department = db.Column(db.String(120), nullable=True)
        must_change_password = db.Column(db.Boolean, default=False, nullable=False)
        password_hash = db.Column(db.String(255), nullable=True)
        password_changed_at = db.Column(db.DateTime, nullable=True)
        role = db.Column(db.String(50), default='user')  # 'admin', 'user', 'designate'
        is_superadmin = db.Column(db.Boolean, default=False, nullable=False)
        can_manage_all_records = db.Column(db.Boolean, default=False, nullable=False)
        is_authorized = db.Column(db.Boolean, default=False)  # Must be authorized by admin
        authorized_at = db.Column(db.DateTime, nullable=True)
        authorized_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True)
        last_login_at = db.Column(db.DateTime, nullable=True)
        last_login_ip = db.Column(db.String(64), nullable=True)
        failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
        last_failed_login_at = db.Column(db.DateTime, nullable=True)
        # Link to user's report (nullable for admin)
        
        report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id'), nullable=True)
        report = db.relationship('PBOReport', foreign_keys=[report_id], backref='single_user_report', uselist=False)

         # ✅ CORRECT RELATIONSHIP (ONE USER → MANY REPORTS)
        reports = db.relationship(
            'PBOReport',
            backref='user',
            lazy=True,
            cascade='all, delete-orphan',
            foreign_keys='PBOReport.user_id'
        )
        activity_logs = db.relationship(
            'UserActivityLog',
            backref='user',
            lazy=True,
            cascade='all, delete-orphan',
            foreign_keys='UserActivityLog.user_id'
        )
        authorized_by = db.relationship('User', remote_side=[id], foreign_keys=[authorized_by_id], post_update=True)
        uploaded_files = db.relationship('UploadedFile', backref='uploaded_by', lazy=True, foreign_keys='UploadedFile.uploaded_by_id')
        import_batches = db.relationship('ImportBatch', backref='created_by', lazy=True, foreign_keys='ImportBatch.created_by_id')
        def set_password(self, password, mark_changed=True):
            self.password_hash = generate_password_hash(password)
            self.password_changed_at = utc_now() if mark_changed else None
        def check_password(self, password):
            return check_password_hash(self.password_hash, password)

class PBOReport(db.Model):

       
    __tablename__ = 'pbo_reports'
    __table_args__ = (
        db.CheckConstraint(
            build_status_check_constraint('workflow_status', REPORT_WORKFLOW_STATUS_VALUES),
            name='ck_pbo_reports_workflow_status_allowed',
        ),
        db.CheckConstraint(
            build_status_check_constraint('review_status', REPORT_REVIEW_STATUS_VALUES),
            name='ck_pbo_reports_review_status_allowed',
        ),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True)
    
    # Admin Table Fields
    filing_period = db.Column(db.Text, nullable=True)
    filling_fee = db.Column(db.Text, nullable=True)
    late_returns = db.Column(db.Text, nullable=True)
    penalty_paid = db.Column(db.Text, nullable=True)
    outstanding_penalty = db.Column(db.Text, nullable=True)
    financial_year_ending_month = db.Column(db.Text, nullable=True)
    form_14 = db.Column(db.Text, nullable=True)
    audit_report = db.Column(db.Text, nullable=True)
    requests = db.Column(db.Text, nullable=True)
    date_received = db.Column(db.Text, nullable=True)
    received_by = db.Column(db.Text, nullable=True)
    filed_for_action_by_registry = db.Column(db.Text, nullable=True)
    date_filed_by_registry_for_action = db.Column(db.Text, nullable=True)
    designate_by_pco = db.Column(db.Text, nullable=True)  # Editable by admin via dropdown
    date_assigned = db.Column(db.Text, nullable=True)
    review_acknowledgement = db.Column(db.Text, nullable=True)
    date_acknowledged_notice_sent = db.Column(db.Text, nullable=True)
    end_of_notice_period = db.Column(db.Text, nullable=True)
    notice_countdown = db.Column(db.Text, nullable=True)


    imputed_fields = db.Column(db.Text, nullable=True)
    # TF Risk Score (computed and stored)
    risk_score = db.Column(db.Integer, nullable=True, index=True)





    created_at = db.Column(db.DateTime, default=utc_now)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)
    submitted_at = db.Column(db.DateTime, nullable=True)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    last_viewed_at = db.Column(db.DateTime, nullable=True)
    last_modified_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True)
    last_modified_by = db.relationship('User', foreign_keys=[last_modified_by_id], backref='modified_reports')
    workflow_status = db.Column(db.String(64), nullable=False, default='draft', index=True)
    review_status = db.Column(db.String(64), nullable=False, default='pending', index=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True)
    reviewed_by = db.relationship('User', foreign_keys=[reviewed_by_id], backref='reviewed_reports')
    reviewed_at = db.Column(db.DateTime, nullable=True)
    review_notes = db.Column(db.Text, nullable=True)
    return_reason = db.Column(db.Text, nullable=True)
    duplicate_flag = db.Column(db.Boolean, nullable=False, default=False, index=True)
    data_source = db.Column(db.String(128), nullable=True, default='form')
    import_batch_id = db.Column(db.Integer, db.ForeignKey('import_batches.id'), nullable=True, index=True)
    submission_token = db.Column(db.String(64), nullable=True, unique=True, index=True)

    # Reporting Period
    reporting_period_start = db.Column(db.Date, nullable=True)
    reporting_period_end = db.Column(db.Date, nullable=True)
    reporting_period_start_raw = db.Column(db.String(50), nullable=True)
    reporting_period_end_raw = db.Column(db.String(50), nullable=True)
    return_date = db.Column(
        db.Date,
        nullable=False,
        default=RETURN_DATE_PLACEHOLDER,
        server_default=RETURN_DATE_PLACEHOLDER.isoformat(),
    )

    # Section A1 - PBO Particulars
    pbo_name = db.Column(db.Text, nullable=True)
    pbo_name_normalized = db.Column(db.String(255), nullable=True, index=True)
    pbo_registration_number = db.Column(db.String(255), nullable=True)
    pbo_registration_date = db.Column(db.Date, nullable=True)

    kra_pin = db.Column(db.String(50), nullable=True)
    postal_address = db.Column(db.Text, nullable=True)
    physical_address = db.Column(db.Text, nullable=True)
    telephone = db.Column(db.String(50), nullable=True)
    cell_phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(320), nullable=True)
    website = db.Column(db.String(512), nullable=True)
    social_media = db.Column(db.Text, nullable=True)

    # Section A2 - Contact Person
    contact_name = db.Column(db.String(255), nullable=True)
    contact_position = db.Column(db.String(255), nullable=True)
    contact_telephone = db.Column(db.String(100), nullable=True)
    contact_email = db.Column(db.Text, nullable=True)
    contact_nationality = db.Column(db.String(120), nullable=True)
    contact_gender = db.Column(db.String(50), nullable=True)

    # Section A3-A6 - Additional Registration Info
    registration_number = db.Column(db.String(255), nullable=True)
    pin_number = db.Column(db.String(64), nullable=True)
    date_of_registration = db.Column(db.Date, nullable=True)
    scope = db.Column(db.String(50), nullable=True)  # NATIONAL or INTERNATIONAL
    countries_of_operation = db.Column(db.Text, nullable=True)
    counties = db.Column(db.Text, nullable=True)  # Store as comma-separated or JSON

    # Section B - Assets & Finance
    assets_stolen = db.Column(db.String(10), nullable=True)  # Yes/No
    cash_balance_previous_year = db.Column(db.Float, nullable=True)
    income_b2_total = db.Column(db.Float, nullable=True)
    receipts_total = db.Column(db.Float, nullable=True)
    cash_bank_balance = db.Column(db.Float, nullable=True)

    # Section B - Audited Report
    audited = db.Column(db.String(10), nullable=True)  # Yes/No

    # Section C - Staff Counts (Kenya)
    staff_kenyan_prev = db.Column(db.Integer, nullable=True)
    staff_foreign_prev = db.Column(db.Integer, nullable=True)
    staff_kenyan_current = db.Column(db.Integer, nullable=True)
    staff_foreign_current = db.Column(db.Integer, nullable=True)
    staff_kenyan_came_in = db.Column(db.Integer, nullable=True)
    staff_foreign_came_in = db.Column(db.Integer, nullable=True)
    staff_kenyan_left = db.Column(db.Integer, nullable=True)
    staff_foreign_left = db.Column(db.Integer, nullable=True)

    # Section C - Staff Counts (Other Countries)
    staff_other_kenyan_prev = db.Column(db.Integer, nullable=True)
    staff_other_foreign_prev = db.Column(db.Integer, nullable=True)
    staff_other_kenyan_current = db.Column(db.Integer, nullable=True)
    staff_other_foreign_current = db.Column(db.Integer, nullable=True)

    # Section C - Volunteers/Interns Counts
    volunteers_kenyan_prev = db.Column(db.Integer, nullable=True)
    volunteers_foreign_prev = db.Column(db.Integer, nullable=True)
    volunteers_kenyan_current = db.Column(db.Integer, nullable=True)
    volunteers_foreign_current = db.Column(db.Integer, nullable=True)

    # Section D - Project Implementation (basic flags)
    project_implementation_method = db.Column(db.Text, nullable=True)  # comma-separated

    # Contributions in Kind (Section D)
    local_material = db.Column(db.Boolean, default=False)
    local_material_amount = db.Column(db.Float, nullable=True)
    local_labour = db.Column(db.Boolean, default=False)
    local_labour_amount = db.Column(db.Float, nullable=True)
    local_financial = db.Column(db.Boolean, default=False)
    local_financial_amount = db.Column(db.Float, nullable=True)
    local_other = db.Column(db.Boolean, default=False)
    local_other_specify = db.Column(db.Text, nullable=True)
    local_other_amount = db.Column(db.Float, nullable=True)
    gov_tax_waiver = db.Column(db.Boolean, default=False)
    gov_tax_waiver_amount = db.Column(db.Float, nullable=True)
    gov_other = db.Column(db.Boolean, default=False)
    gov_other_specify = db.Column(db.Text, nullable=True)
    gov_other_amount = db.Column(db.Float, nullable=True)

    # Section D - Collaboration & Networking (stored as text for now)
    collaboration_networking = db.Column(db.Text, nullable=True)

    # Section E - Governance
    number_of_directors = db.Column(db.Integer, nullable=True)
    number_of_registered_members = db.Column(db.Integer, nullable=True)
    number_of_board_meetings = db.Column(db.Integer, nullable=True)
    election_frequency = db.Column(db.String(50), nullable=True)
    election_frequency_other = db.Column(db.String(255), nullable=True)
    date_last_agm = db.Column(db.Date, nullable=True)
    date_last_election = db.Column(db.Date, nullable=True)
    date_last_board_meeting = db.Column(db.Date, nullable=True)

    # Section E - Membership (distinct fields)
    membership_number_of_directors = db.Column(db.Integer, nullable=True)
    membership_number_of_registered_members = db.Column(db.Integer, nullable=True)
    membership_number_of_board_meetings = db.Column(db.Integer, nullable=True)
    membership_date_last_agm = db.Column(db.Date, nullable=True)
    membership_date_last_election = db.Column(db.Date, nullable=True)

    # Section E - Non-Membership (distinct fields)
    non_membership_number_of_directors = db.Column(db.Integer, nullable=True)
    non_membership_number_of_board_meetings = db.Column(db.Integer, nullable=True)
    non_membership_date_last_board_meeting = db.Column(db.Date, nullable=True)
    non_membership_date_last_election = db.Column(db.Date, nullable=True)

    # Form submission (Section E)
    submitter_fullname = db.Column(db.String(255), nullable=True)
    signature = db.Column(db.Text, nullable=True)  # Base64 encoded signature image
    submission_date = db.Column(db.Date, nullable=True)

    # Annual Return Submission Deadline (admin set)
    edit_deadline = db.Column(db.DateTime, nullable=True)  # Deadline for editing reports
    form_submission_deadline = db.Column(db.DateTime, nullable=True)  # Deadline for submitting new reports

    # Relationships for repeated data
    assets = db.relationship('Asset', backref='report', cascade='all, delete-orphan')
    igas = db.relationship('IncomeGeneratingActivity', backref='report', cascade='all, delete-orphan')
    donations = db.relationship('Donation', backref='report', cascade='all, delete-orphan')
    grants = db.relationship('Grant', backref='report', cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='report', cascade='all, delete-orphan')
    bank_accounts = db.relationship('BankAccount', backref='report', cascade='all, delete-orphan')
    auditors = db.relationship('AuditorEntry', backref='report', cascade='all, delete-orphan')
    staff_biodata = db.relationship('StaffBiodata', backref='report', cascade='all, delete-orphan')
    volunteer_biodata = db.relationship('VolunteerBiodata', backref='report', cascade='all, delete-orphan')
    volunteer_privileges = db.relationship('VolunteerPrivilege', backref='report', cascade='all, delete-orphan')
    training_records = db.relationship('TrainingRecord', backref='report', cascade='all, delete-orphan')
    tax_waiver_items = db.relationship('TaxWaiverItem', backref='report', cascade='all, delete-orphan')
    officials = db.relationship('Official', backref='report', cascade='all, delete-orphan')
    project_implementations = db.relationship('ProjectImplementation', backref='report', cascade='all, delete-orphan')
    projects_carried_out = db.relationship('ProjectCarriedOut', backref='report', cascade='all, delete-orphan')
    collaborations = db.relationship('CollaborationNetworking', backref='report', cascade='all, delete-orphan')
    activity_logs = db.relationship('UserActivityLog', backref='report', cascade='all, delete-orphan', order_by='desc(UserActivityLog.created_at)')
    field_changes = db.relationship('FieldChangeLog', backref='report', cascade='all, delete-orphan', order_by='desc(FieldChangeLog.created_at)')
    uploaded_files = db.relationship('UploadedFile', backref='report', cascade='all, delete-orphan', order_by='desc(UploadedFile.created_at)')






    def __repr__(self):
        return f'<PBOReport {self.id} - {self.pbo_name}>'
    
    def update_risk_score(self, compute_func):
            """Update and store the TF risk score using the provided compute function."""
            score, _ = compute_func(self)
            self.risk_score = score
            return score

    @staticmethod
    def eager_query():
            from sqlalchemy.orm import joinedload
            return PBOReport.query.options(
                joinedload(PBOReport.donations),
                joinedload(PBOReport.grants),
                joinedload(PBOReport.collaborations)
            )

class PostalCodeCache(db.Model):
    __tablename__ = 'postal_code_cache'

    id = db.Column(db.Integer, primary_key=True)
    postal_code = db.Column(db.String(10), unique=True, nullable=True)
    town = db.Column(db.String(1000), nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, onupdate=utc_now)


class UserActivityLog(db.Model):
    __tablename__ = 'user_activity_logs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True, index=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id'), nullable=True, index=True)
    action = db.Column(db.String(255), nullable=False, index=True)
    route = db.Column(db.String(1000), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    def __repr__(self):
        return f'<UserActivityLog {self.action} user={self.user_id} report={self.report_id}>'


class AdminSetting(db.Model):
    __tablename__ = 'admin_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(255), nullable=False, unique=True, index=True)
    value = db.Column(db.Text, nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, nullable=False, onupdate=utc_now)

    updated_by = db.relationship('User', foreign_keys=[updated_by_id])


class FieldHelpCache(db.Model):
    __tablename__ = 'field_help_cache'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    context_json = db.Column(db.Text, nullable=False)
    search_index_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=utc_now, nullable=False, onupdate=utc_now)


class FieldHelpDecisionModel(db.Model):
    __tablename__ = 'field_help_decision_models'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, unique=True, index=True)
    model_json = db.Column(db.Text, nullable=False)
    train_size = db.Column(db.Integer, nullable=False, default=0)
    test_size = db.Column(db.Integer, nullable=False, default=0)
    accuracy = db.Column(db.Float, nullable=False, default=0.0)
    updated_at = db.Column(db.DateTime, default=utc_now, nullable=False, onupdate=utc_now)


class FieldHelpMemory(db.Model):
    __tablename__ = 'field_help_memory'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(50), nullable=False, default='local')
    score = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)


class FieldHelpRule(db.Model):
    __tablename__ = 'field_help_rules'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True)
    field_name = db.Column(db.String(160), nullable=False, index=True)
    field_label = db.Column(db.String(1000), nullable=False)
    heading = db.Column(db.String(1000), nullable=True)
    is_allowed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_disabled = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_required = db.Column(db.Boolean, nullable=False, default=False, index=True)
    used_in_sector_report = db.Column(db.Boolean, nullable=False, default=False, index=True)
    notes = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=utc_now, nullable=False, onupdate=utc_now)


class FieldHelpIntentSample(db.Model):
    __tablename__ = 'field_help_intent_samples'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True)
    input_text = db.Column(db.Text, nullable=False)
    label = db.Column(db.String(80), nullable=False, index=True)
    dataset_split = db.Column(db.String(12), nullable=False, default='train', index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)


class FieldHelpInteraction(db.Model):
    __tablename__ = 'field_help_interactions'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    normalized_question = db.Column(db.Text, nullable=True)
    intent = db.Column(db.String(80), nullable=True, index=True)
    route = db.Column(db.String(80), nullable=True, index=True)
    response_source = db.Column(db.String(80), nullable=True, index=True)
    answer = db.Column(db.Text, nullable=False)
    confidence = db.Column(db.Float, nullable=True)
    blocked = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)


class DataAnalysisTrainingQuestion(db.Model):
    __tablename__ = 'data_analysis_training_questions'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True, default='report_edit')
    question = db.Column(db.Text, nullable=False)
    intent = db.Column(db.String(80), nullable=False, index=True)
    target_domain = db.Column(db.String(120), nullable=True, index=True)
    fields_hint_json = db.Column(db.Text, nullable=True)
    answer_style = db.Column(db.String(80), nullable=True)
    dataset_split = db.Column(db.String(12), nullable=False, default='train', index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)


class DataAnalysisBotInteraction(db.Model):
    __tablename__ = 'data_analysis_bot_interactions'

    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(120), nullable=False, index=True, default='report_edit')
    question = db.Column(db.Text, nullable=False)
    intent = db.Column(db.String(80), nullable=True, index=True)
    confidence = db.Column(db.Float, nullable=True)
    answer = db.Column(db.Text, nullable=False)
    result_token = db.Column(db.String(80), nullable=True, index=True)
    result_path = db.Column(db.String(512), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])


class ImportBatch(db.Model):
    __tablename__ = 'import_batches'
    __table_args__ = (
        db.CheckConstraint(
            build_status_check_constraint('status', IMPORT_BATCH_STATUS_VALUES),
            name='ck_import_batches_status_allowed',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    source_type = db.Column(db.String(50), nullable=False, default='manual')
    original_filename = db.Column(db.String(1000), nullable=True)
    stored_filename = db.Column(db.String(1000), nullable=True)
    status = db.Column(db.String(50), nullable=False, default='pending', index=True)
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    success_rows = db.Column(db.Integer, nullable=False, default=0)
    error_rows = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    reports = db.relationship('PBOReport', backref='import_batch', lazy=True)
    row_errors = db.relationship('ImportRowError', backref='batch', cascade='all, delete-orphan', order_by='ImportRowError.row_number.asc()')
    uploaded_files = db.relationship('UploadedFile', backref='batch', cascade='all, delete-orphan', order_by='desc(UploadedFile.created_at)')


class ImportRowError(db.Model):
    __tablename__ = 'import_row_errors'

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('import_batches.id', ondelete='CASCADE'), nullable=False, index=True)
    row_number = db.Column(db.Integer, nullable=False)
    field_name = db.Column(db.String(1000), nullable=True)
    error_message = db.Column(db.Text, nullable=False)
    row_payload = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)


class UploadedFile(db.Model):
    __tablename__ = 'uploaded_files'
    __table_args__ = (
        db.CheckConstraint(
            build_status_check_constraint('status', UPLOADED_FILE_STATUS_VALUES),
            name='ck_uploaded_files_status_allowed',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id'), nullable=True, index=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('import_batches.id'), nullable=True, index=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True, index=True)
    category = db.Column(db.String(50), nullable=True, index=True)
    original_filename = db.Column(db.String(1000), nullable=False)
    stored_filename = db.Column(db.String(1000), nullable=False)
    storage_path = db.Column(db.String(512), nullable=False)
    mime_type = db.Column(db.String(1000), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    sha256_hash = db.Column(db.String(64), nullable=True, index=True)
    status = db.Column(db.String(50), nullable=False, default='uploaded', index=True)
    extracted_text = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)


class FieldChangeLog(db.Model):
    __tablename__ = 'field_change_logs'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users_for_form14.id'), nullable=True, index=True)
    action = db.Column(db.String(255), nullable=False, index=True)
    field_name = db.Column(db.String(255), nullable=False, index=True)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    user = db.relationship('User', foreign_keys=[user_id])


class Asset(db.Model):
    __tablename__ = 'pbo_assets'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    item = db.Column(db.String(1000), nullable=True)
    number = db.Column(db.Integer, nullable=True)
    value = db.Column(db.Float, nullable=True)


class IncomeGeneratingActivity(db.Model):
    __tablename__ = 'pbo_iga'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    activity = db.Column(db.String(1000), nullable=True)
    amount = db.Column(db.Float, nullable=True)


class Donation(db.Model):
    __tablename__ = 'pbo_donations'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(1000), nullable=True)
    category = db.Column(db.String(1000), nullable=True)
    country = db.Column(db.String(1000), nullable=True)
    amount = db.Column(db.Float, nullable=True)


class Grant(db.Model):
    __tablename__ = 'pbo_grants'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(1000), nullable=True)
    registration_no = db.Column(db.String(1000), nullable=True)
    country = db.Column(db.String(1000), nullable=True)
    amount = db.Column(db.Float, nullable=True)


class Payment(db.Model):
    __tablename__ = 'pbo_payments'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    description = db.Column(db.String(1000), nullable=True)
    kenya_amount = db.Column(db.Float, nullable=True)
    other_amount = db.Column(db.Float, nullable=True)


class BankAccount(db.Model):
    __tablename__ = 'pbo_bank_accounts'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    bank_name = db.Column(db.String(1000), nullable=True)
    branch = db.Column(db.String(1000), nullable=True)
    account_number = db.Column(db.String(1000), nullable=True)
    currency = db.Column(db.String(50), nullable=True)


class AuditorEntry(db.Model):
    __tablename__ = 'pbo_auditors'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    firm = db.Column(db.String(1000), nullable=True)
    auditor_name = db.Column(db.String(1000), nullable=True)
    practicing_no = db.Column(db.String(1000), nullable=True)


class StaffBiodata(db.Model):
    __tablename__ = 'pbo_staff_biodata'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    category = db.Column(db.String(1000), nullable=True)
    prev_year = db.Column(db.Integer, nullable=True)
    curr_year = db.Column(db.Integer, nullable=True)


class VolunteerBiodata(db.Model):
    __tablename__ = 'pbo_volunteer_biodata'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    category = db.Column(db.String(1000), nullable=True)
    prev_year = db.Column(db.Integer, nullable=True)
    curr_year = db.Column(db.Integer, nullable=True)


class VolunteerPrivilege(db.Model):
    __tablename__ = 'pbo_volunteer_privileges'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    category = db.Column(db.String(1000), nullable=True)
    kenyan_volunteer = db.Column(db.Boolean, default=False)
    kenyan_intern = db.Column(db.Boolean, default=False)
    international_volunteer = db.Column(db.Boolean, default=False)
    international_intern = db.Column(db.Boolean, default=False)


class TrainingRecord(db.Model):
    __tablename__ = 'pbo_training_records'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    training_type = db.Column(db.String(1000), nullable=True)
    kenyan_count = db.Column(db.Integer, nullable=True)
    international_count = db.Column(db.Integer, nullable=True)


class TaxWaiverItem(db.Model):
    __tablename__ = 'pbo_tax_waiver_items'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    item_description = db.Column(db.String(1000), nullable=True)
    quantity = db.Column(db.Integer, nullable=True)
    exemption_type = db.Column(db.String(50), nullable=True)
    estimated_tax_waived = db.Column(db.Float, nullable=True)
    certificate_approval_no = db.Column(db.String(1000), nullable=True)


class Official(db.Model):
    __tablename__ = 'pbo_officials'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    role = db.Column(db.String(1000), nullable=True)
    name = db.Column(db.String(1000), nullable=True)
    nationality = db.Column(db.String(1000), nullable=True)
    gender = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(1000), nullable=True)
    residence = db.Column(db.String(1000), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    kra_pin = db.Column(db.String(50), nullable=True)
    professional_qualification = db.Column(db.String(1000), nullable=True)
    signature = db.Column(db.Text, nullable=True)  # Base64 encoded signature image


class ProjectImplementation(db.Model):
    __tablename__ = 'pbo_project_implementations'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    sector = db.Column(db.String(1000), nullable=True)
    county = db.Column(db.String(1000), nullable=True)
    vulnerable_group = db.Column(db.String(1000), nullable=True)
    beneficiaries_no = db.Column(db.Integer, nullable=True)
    spending_per_county = db.Column(db.Float, nullable=True)
    duration_years = db.Column(db.Float, nullable=True)
    completion_status = db.Column(db.String(1000), nullable=True)
    amount_spent_kenya = db.Column(db.Float, nullable=True)
    amount_spent_other = db.Column(db.Float, nullable=True)


class ProjectCarriedOut(db.Model):
    __tablename__ = 'pbo_projects_carried_out'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    sector = db.Column(db.String(1000), nullable=True)
    carried_forward_kenya = db.Column(db.String(1000), nullable=True)
    carried_forward_other = db.Column(db.String(1000), nullable=True)
    started_kenya = db.Column(db.String(1000), nullable=True)
    started_other = db.Column(db.String(1000), nullable=True)
    completed_kenya = db.Column(db.String(1000), nullable=True)
    completed_other = db.Column(db.String(1000), nullable=True)


class CollaborationNetworking(db.Model):
    __tablename__ = 'pbo_collaboration_networking'

    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('pbo_reports.id', ondelete='CASCADE'), nullable=False)
    partner_type = db.Column(db.String(1000), nullable=True)
    info_exchange = db.Column(db.String(1000), nullable=True)
    tech_support_to_partner = db.Column(db.String(1000), nullable=True)
    tech_support_from_partner = db.Column(db.String(1000), nullable=True)
    funding_to_partner = db.Column(db.String(1000), nullable=True)
    funding_from_partner = db.Column(db.String(1000), nullable=True)
    equipment_to_partner = db.Column(db.String(1000), nullable=True)
    equipment_from_partner = db.Column(db.String(1000), nullable=True)


REPORT_MODELS = (
    PBOReport,
    Asset,
    IncomeGeneratingActivity,
    Donation,
    Grant,
    Payment,
    BankAccount,
    AuditorEntry,
    StaffBiodata,
    VolunteerBiodata,
    VolunteerPrivilege,
    TrainingRecord,
    TaxWaiverItem,
    Official,
    ProjectImplementation,
    ProjectCarriedOut,
    CollaborationNetworking,
)

REPORT_CHILD_MODELS = tuple(
    model for model in REPORT_MODELS
    if model is not PBOReport and hasattr(model, 'report_id')
)

ALL_MODELS = (
    User,
    PostalCodeCache,
    UserActivityLog,
    AdminSetting,
    FieldHelpCache,
    FieldHelpDecisionModel,
    FieldHelpMemory,
    FieldHelpRule,
    FieldHelpIntentSample,
    FieldHelpInteraction,
    DataAnalysisTrainingQuestion,
    DataAnalysisBotInteraction,
    ImportBatch,
    ImportRowError,
    UploadedFile,
    FieldChangeLog,
) + REPORT_MODELS

LEGACY_SQLITE_ID_MODELS = ALL_MODELS

LEGACY_SQLITE_ID_PK_CACHE = {}

TEXT_UPPERCASE_EXCLUSIONS = {
    "signature",
    "workflow_status",
    "review_status",
    "data_source",
}


def uppercase_model_text(mapper, connection, target):
    for column in target.__table__.columns:
        if column.name in TEXT_UPPERCASE_EXCLUSIONS:
            continue
        if not isinstance(column.type, (String, Text)):
            continue
        value = getattr(target, column.name)
        if isinstance(value, str):
            normalized = value.strip()
            setattr(target, column.name, normalized.upper() if normalized else None)


def sqlite_table_has_real_id_primary_key(connection, table_name):
    if connection.dialect.name != "sqlite":
        return True

    cached_value = LEGACY_SQLITE_ID_PK_CACHE.get(table_name)
    if cached_value is not None:
        return cached_value

    escaped_table_name = table_name.replace('"', '""')
    table_info_rows = connection.exec_driver_sql(
        f'PRAGMA table_info("{escaped_table_name}")'
    ).mappings().all()
    id_column = next((row for row in table_info_rows if row.get("name") == "id"), None)
    has_real_primary_key = bool(id_column) and int(id_column.get("pk") or 0) > 0
    LEGACY_SQLITE_ID_PK_CACHE[table_name] = has_real_primary_key
    return has_real_primary_key


def assign_legacy_sqlite_id(mapper, connection, target):
    table = getattr(target, "__table__", None)
    if table is None or connection.dialect.name != "sqlite" or "id" not in table.c:
        return

    current_id = getattr(target, "id", None)
    if current_id not in (None, ""):
        return

    if sqlite_table_has_real_id_primary_key(connection, table.name):
        return

    escaped_table_name = table.name.replace('"', '""')
    next_id = connection.execute(
        text(f'SELECT COALESCE(MAX(COALESCE(id, rowid)), 0) + 1 FROM "{escaped_table_name}"')
    ).scalar()
    if next_id is not None:
        target.id = int(next_id)


for model in LEGACY_SQLITE_ID_MODELS:
    event.listen(model, "before_insert", assign_legacy_sqlite_id)


@event.listens_for(Session, "before_flush")
def enforce_report_child_parent_integrity(session, flush_context, instances):
    for obj in session.new.union(session.dirty):
        if not isinstance(obj, REPORT_CHILD_MODELS):
            continue
        if obj in session.deleted:
            continue
        if getattr(obj, 'report_id', None) is not None:
            continue
        if getattr(obj, 'report', None) is not None:
            continue
        raise ValueError(
            f"{obj.__class__.__name__} cannot be saved without a parent PBOReport."
        )

for model in REPORT_MODELS:
    event.listen(model, "before_insert", uppercase_model_text)
    event.listen(model, "before_update", uppercase_model_text)
