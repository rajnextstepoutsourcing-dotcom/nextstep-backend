from flask import Blueprint, render_template, redirect, url_for, current_app, request, flash
import threading

try:
    import requests
except Exception:
    requests = None
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import Job, UsageRecord, User, UserSession
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


def _warm_tool_service(target: str, token: str | None = None, timeout: float = 2.5):
    if not target or requests is None:
        return
    try:
        warm_target = f"{target.rstrip('/')}/health"
        requests.get(warm_target, timeout=timeout, allow_redirects=True)
    except Exception:
        pass


def _tool_url_map():
    return {
        'nmc': current_app.config['TOOL_URL_NMC'],
        'dbs': current_app.config['TOOL_URL_DBS'],
        'rtw': current_app.config['TOOL_URL_RTW'],
        'checklist': current_app.config['TOOL_URL_CHECKLIST'],
    }


def _get_or_create_user_session_token(user):
    now = datetime.utcnow()
    existing = UserSession.query.filter(
        UserSession.user_id == user.id,
        UserSession.expires_at > now
    ).order_by(UserSession.created_at.desc()).first()
    if existing:
        return existing.token
    # fallback: login route normally creates it, so missing token means force re-login
    return None


@dashboard_bp.route('/dashboard')
@login_required
def index():
    if current_user.role == 'owner':
        return redirect(url_for('owner.dashboard'))

    tenant = current_user.tenant
    if not current_user.active or not tenant or tenant.status != 'active':
        flash('Your company account is not active yet. Please wait for approval.', 'warning')
        return redirect(url_for('auth.logout'))

    today = datetime.utcnow().date()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    jobs_today = Job.query.filter_by(tenant_id=tenant.id).filter(func.date(Job.created_at) == today).count()
    jobs_this_month = Job.query.filter_by(tenant_id=tenant.id).filter(Job.created_at >= month_start).count()
    tokens_used_month = db.session.query(func.sum(UsageRecord.billable_output_count)).filter_by(tenant_id=tenant.id).filter(UsageRecord.created_at >= month_start).scalar() or 0
    recent_jobs = Job.query.filter_by(tenant_id=tenant.id).order_by(Job.created_at.desc()).limit(10).all()
    team_count = User.query.filter_by(tenant_id=tenant.id, active=True).count()

    tool_links = {slug: url_for('dashboard.launch_tool', tool_slug=slug) for slug in _tool_url_map().keys()}

    return render_template('dashboard/tenant_dashboard.html', tenant=tenant, jobs_today=jobs_today, jobs_this_month=jobs_this_month, tokens_used_month=tokens_used_month, recent_jobs=recent_jobs, team_count=team_count, tool_links=tool_links, now=datetime.utcnow())


@dashboard_bp.route('/launch/<tool_slug>')
@login_required
def launch_tool(tool_slug):
    if current_user.role == 'owner':
        flash('Owner accounts cannot launch tenant tools directly.', 'warning')
        return redirect(url_for('owner.dashboard'))

    tenant = current_user.tenant
    if not current_user.active or not tenant or tenant.status != 'active':
        flash('Your company account is not active yet.', 'warning')
        return redirect(url_for('dashboard.index'))

    tool_urls = _tool_url_map()
    target = tool_urls.get(tool_slug)
    if not target:
        flash('Tool launch URL is not configured.', 'error')
        return redirect(url_for('dashboard.index'))

    token = _get_or_create_user_session_token(current_user)
    if not token:
        flash('Your login session expired. Please log in again.', 'warning')
        return redirect(url_for('auth.logout'))

    target = (target or '').strip()
    if not target:
        flash('Tool launch URL is not configured.', 'error')
        return redirect(url_for('dashboard.index'))
    separator = '&' if '?' in target else '?'
    launch_url = f"{target.rstrip('/')}{separator}ns_token={token}" if '?' not in target else f"{target}{separator}ns_token={token}"
    if tool_slug == 'checklist':
        try:
            timeout = float(current_app.config.get('TOOL_WARMUP_TIMEOUT_SECONDS', 2.5))
            threading.Thread(target=_warm_tool_service, args=(target, token, timeout), daemon=True, name='warm-checklist-tool').start()
        except Exception:
            pass
    return redirect(launch_url)
