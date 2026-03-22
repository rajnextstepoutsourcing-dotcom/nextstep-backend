import os
from flask import Flask
from sqlalchemy import text
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()
login_manager = LoginManager()
mail = Mail()


def create_app():
    app = Flask(__name__)

    # ── Core config ──────────────────────────────────────────
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-this')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///nextstep.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Fix Render postgres:// → postgresql://
    db_url = app.config['SQLALCHEMY_DATABASE_URI']
    if db_url.startswith('postgres://'):
        app.config['SQLALCHEMY_DATABASE_URI'] = db_url.replace('postgres://', 'postgresql://', 1)

    # ── Session security ─────────────────────────────────────
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
    app.config['PERMANENT_SESSION_LIFETIME'] = 86400 * 7  # 7 days

    # ── Tool launch URLs (free Render testing) ─────────────────
    app.config['TOOL_URL_NMC'] = os.environ.get('TOOL_URL_NMC', 'https://nmc-multiple.onrender.com')
    app.config['TOOL_URL_DBS'] = os.environ.get('TOOL_URL_DBS', 'https://dbs-webapp-v2.onrender.com')
    app.config['TOOL_URL_RTW'] = os.environ.get('TOOL_URL_RTW', 'https://rtw-live.onrender.com')
    app.config['TOOL_URL_CHECKLIST'] = os.environ.get('TOOL_URL_CHECKLIST', 'https://checklist-webapp-z3nz.onrender.com')  # checklist service root
    app.config['TOOL_WARMUP_TIMEOUT_SECONDS'] = float(os.environ.get('TOOL_WARMUP_TIMEOUT_SECONDS', '2.5'))

    # ── Mail config ──────────────────────────────────────────
    app.config['MAIL_SERVER']          = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    app.config['MAIL_PORT']            = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS']         = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
    app.config['MAIL_USERNAME']        = os.environ.get('MAIL_USERNAME', '')
    app.config['MAIL_PASSWORD']        = os.environ.get('MAIL_PASSWORD', '')
    app.config['MAIL_DEFAULT_SENDER']  = os.environ.get('MAIL_DEFAULT_SENDER', '')

    # ── Init extensions ──────────────────────────────────────
    db.init_app(app)
    mail.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    # ── Register blueprints ──────────────────────────────────
    from app.auth.routes import auth_bp
    from app.dashboard.routes import dashboard_bp
    from app.owner.routes import owner_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(owner_bp)

    # ── Create tables + seed owner ───────────────────────────
    with app.app_context():
        db.create_all()
        _ensure_schema_updates()
        _seed_owner()
        _seed_tools()

    from app.api.routes import api_bp
    app.register_blueprint(api_bp)

    return app


def _seed_owner():
    """Create the owner account on first run if it doesn't exist."""
    from app.models import User, Tenant
    import bcrypt

    owner_email = os.environ.get('OWNER_EMAIL', 'raj.nextstepoutsourcing@gmail.com')
    owner_password = os.environ.get('OWNER_PASSWORD', 'Nextstep@2026')
    owner_name = os.environ.get('OWNER_NAME', 'Raj')

    existing = User.query.filter_by(email=owner_email).first()
    if existing:
        return

    # Create a system tenant for the owner
    tenant = Tenant.query.filter_by(company_name='NextStep Admin').first()
    if not tenant:
        tenant = Tenant(
            company_name='NextStep Admin',
            status='active',
            plan_name='owner'
        )
        db.session.add(tenant)
        db.session.flush()

    pw_hash = bcrypt.hashpw(owner_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    owner = User(
        tenant_id=tenant.id,
        name=owner_name,
        email=owner_email,
        password_hash=pw_hash,
        role='owner',
        active=True
    )
    db.session.add(owner)
    db.session.commit()
    print(f'[NextStep] Owner account created: {owner_email}')

    # Seed tools table
    from app.models import Tool
    tools_data = [
        {'slug': 'nmc',       'display_name': 'NMC Check',     'token_cost': 1},
        {'slug': 'dbs',       'display_name': 'DBS Check',     'token_cost': 1},
        {'slug': 'rtw',       'display_name': 'Right to Work', 'token_cost': 1},
        {'slug': 'checklist', 'display_name': 'Checklist',     'token_cost': 1},
    ]
    for t in tools_data:
        if not Tool.query.filter_by(slug=t['slug']).first():
            db.session.add(Tool(**t))
    db.session.commit()
    print('[NextStep] Tools table seeded.')


def _seed_tools():
    """Ensure all tool rows exist in the tools table. Safe to run every startup."""
    from app.models import Tool
    tools_data = [
        {'slug': 'nmc',       'display_name': 'NMC Check',     'token_cost': 1},
        {'slug': 'dbs',       'display_name': 'DBS Check',     'token_cost': 1},
        {'slug': 'rtw',       'display_name': 'Right to Work', 'token_cost': 1},
        {'slug': 'checklist', 'display_name': 'Checklist',     'token_cost': 1},
    ]
    changed = False
    for t in tools_data:
        if not Tool.query.filter_by(slug=t['slug']).first():
            db.session.add(Tool(**t))
            changed = True
    if changed:
        db.session.commit()
        print('[NextStep] Tools table seeded.')


def _ensure_schema_updates():
    """Lightweight production-safe schema updates for columns added after first deploy."""
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(50)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS requested_tools TEXT",
    ]
    try:
        for stmt in statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        print(f'[NextStep] Schema update skipped/failed: {exc}')

