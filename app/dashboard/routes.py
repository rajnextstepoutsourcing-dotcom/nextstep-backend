from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required, current_user
from datetime import datetime
from app import db
from app.models import Job, UsageRecord, User
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard')
@login_required
def index():
    # Owner goes to owner dashboard
    if current_user.role == 'owner':
        return redirect(url_for('owner.dashboard'))

    tenant = current_user.tenant
    today  = datetime.utcnow().date()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    jobs_today = Job.query.filter_by(tenant_id=tenant.id)\
        .filter(func.date(Job.created_at) == today).count()

    jobs_this_month = Job.query.filter_by(tenant_id=tenant.id)\
        .filter(Job.created_at >= month_start).count()

    tokens_used_month = db.session.query(
        func.sum(UsageRecord.billable_output_count)
    ).filter_by(tenant_id=tenant.id)\
     .filter(UsageRecord.created_at >= month_start).scalar() or 0

    recent_jobs = Job.query.filter_by(tenant_id=tenant.id)\
        .order_by(Job.created_at.desc()).limit(10).all()

    team_count = User.query.filter_by(tenant_id=tenant.id, active=True).count()

    return render_template('dashboard/tenant_dashboard.html',
        tenant=tenant,
        jobs_today=jobs_today,
        jobs_this_month=jobs_this_month,
        tokens_used_month=tokens_used_month,
        recent_jobs=recent_jobs,
        team_count=team_count,
        now=datetime.utcnow()
    )
