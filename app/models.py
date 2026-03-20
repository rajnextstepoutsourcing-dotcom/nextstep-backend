from datetime import datetime
from flask_login import UserMixin
from app import db, login_manager


# ── Login manager user loader ────────────────────────────────────────────────
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Tenant (Company) ─────────────────────────────────────────────────────────
class Tenant(db.Model):
    __tablename__ = 'tenants'

    id           = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(200), nullable=False)
    status       = db.Column(db.String(50), default='pending')   # pending, active, suspended
    plan_name    = db.Column(db.String(100), default='starter')
    tokens_total = db.Column(db.Integer, default=0)
    tokens_used  = db.Column(db.Integer, default=0)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    users        = db.relationship('User', backref='tenant', lazy=True)
    jobs         = db.relationship('Job', backref='tenant', lazy=True)
    subscription = db.relationship('Subscription', backref='tenant', uselist=False, lazy=True)
    usage        = db.relationship('UsageRecord', backref='tenant', lazy=True)

    @property
    def tokens_remaining(self):
        return max(0, self.tokens_total - self.tokens_used)

    @property
    def active_users_count(self):
        return User.query.filter_by(tenant_id=self.id, active=True).count()

    def __repr__(self):
        return f'<Tenant {self.company_name}>'


# ── User ─────────────────────────────────────────────────────────────────────
class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True)
    tenant_id     = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    name          = db.Column(db.String(200), nullable=False)
    email         = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(50), default='staff')   # owner, admin, staff, viewer
    active        = db.Column(db.Boolean, default=False)        # False until approved
    last_login    = db.Column(db.DateTime, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    jobs          = db.relationship('Job', backref='user', lazy=True)
    usage         = db.relationship('UsageRecord', backref='user', lazy=True)

    def __repr__(self):
        return f'<User {self.email}>'


# ── Tool ─────────────────────────────────────────────────────────────────────
class Tool(db.Model):
    __tablename__ = 'tools'

    id           = db.Column(db.Integer, primary_key=True)
    slug         = db.Column(db.String(50), unique=True, nullable=False)  # nmc, dbs, rtw, checklist
    display_name = db.Column(db.String(100), nullable=False)
    token_cost   = db.Column(db.Integer, default=1)
    active       = db.Column(db.Boolean, default=True)

    jobs         = db.relationship('Job', backref='tool', lazy=True)

    def __repr__(self):
        return f'<Tool {self.slug}>'


# ── Job ──────────────────────────────────────────────────────────────────────
class Job(db.Model):
    __tablename__ = 'jobs'

    id               = db.Column(db.Integer, primary_key=True)
    tenant_id        = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    user_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tool_id          = db.Column(db.Integer, db.ForeignKey('tools.id'), nullable=True)
    status           = db.Column(db.String(50), default='queued')  # queued, running, completed, failed
    total_items      = db.Column(db.Integer, default=0)
    successful_items = db.Column(db.Integer, default=0)
    failed_items     = db.Column(db.Integer, default=0)
    input_file_path  = db.Column(db.String(500), nullable=True)
    output_file_path = db.Column(db.String(500), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at     = db.Column(db.DateTime, nullable=True)

    items            = db.relationship('JobItem', backref='job', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Job {self.id} [{self.status}]>'


# ── Job Item ─────────────────────────────────────────────────────────────────
class JobItem(db.Model):
    __tablename__ = 'job_items'

    id           = db.Column(db.Integer, primary_key=True)
    job_id       = db.Column(db.Integer, db.ForeignKey('jobs.id'), nullable=False)
    row_number   = db.Column(db.Integer, nullable=True)
    display_name = db.Column(db.String(200), nullable=True)
    status       = db.Column(db.String(50), default='pending')  # pending, success, failed
    result_path  = db.Column(db.String(500), nullable=True)
    error_message= db.Column(db.Text, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<JobItem {self.id} [{self.status}]>'


# ── Subscription ─────────────────────────────────────────────────────────────
class Subscription(db.Model):
    __tablename__ = 'subscriptions'

    id                     = db.Column(db.Integer, primary_key=True)
    tenant_id              = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    plan_name              = db.Column(db.String(100), nullable=False)
    status                 = db.Column(db.String(50), default='active')  # active, cancelled, expired
    tokens_per_month       = db.Column(db.Integer, default=0)
    monthly_price_gbp      = db.Column(db.Float, default=0.0)
    billing_cycle_start    = db.Column(db.DateTime, nullable=True)
    renewal_date           = db.Column(db.DateTime, nullable=True)
    created_at             = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Subscription {self.plan_name} [{self.status}]>'


# ── Usage Record ─────────────────────────────────────────────────────────────
class UsageRecord(db.Model):
    __tablename__ = 'usage_records'

    id                  = db.Column(db.Integer, primary_key=True)
    tenant_id           = db.Column(db.Integer, db.ForeignKey('tenants.id'), nullable=False)
    user_id             = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    tool_id             = db.Column(db.Integer, db.ForeignKey('tools.id'), nullable=True)
    job_id              = db.Column(db.Integer, db.ForeignKey('jobs.id'), nullable=True)
    billable_output_count = db.Column(db.Integer, default=1)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<UsageRecord tenant={self.tenant_id} tokens={self.billable_output_count}>'


# ── Password Reset Token ──────────────────────────────────────────────────────
class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token      = db.Column(db.String(200), unique=True, nullable=False)
    used       = db.Column(db.Boolean, default=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user       = db.relationship('User', backref='reset_tokens')

    def __repr__(self):
        return f'<PasswordResetToken user={self.user_id}>'


# ── User Session (tool auth tokens) ──────────────────────────────────────────
class UserSession(db.Model):
    __tablename__ = 'user_sessions'

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token      = db.Column(db.String(200), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user       = db.relationship('User', backref='sessions')

    def __repr__(self):
        return f'<UserSession user={self.user_id}>'
