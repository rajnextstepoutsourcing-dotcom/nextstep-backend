from datetime import datetime, timedelta
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import func
from app import db
from app.models import Tenant, User, Job, UsageRecord, Subscription, Tool
import bcrypt

owner_bp = Blueprint('owner', __name__, url_prefix='/owner')


# ── Owner required decorator ─────────────────────────────────────────────────
def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'owner':
            flash('Owner access required.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ── Owner Dashboard ───────────────────────────────────────────────────────────
@owner_bp.route('/dashboard')
@login_required
@owner_required
def dashboard():
    today = datetime.utcnow().date()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Overview stats
    total_tenants     = Tenant.query.filter(Tenant.plan_name != 'owner').count()
    active_tenants    = Tenant.query.filter_by(status='active').filter(Tenant.plan_name != 'owner').count()
    total_users       = User.query.filter(User.role != 'owner').count()
    active_users      = User.query.filter_by(active=True).filter(User.role != 'owner').count()
    pending_users     = User.query.filter_by(active=False).filter(User.role != 'owner').count()

    jobs_today        = Job.query.filter(func.date(Job.created_at) == today).count()
    jobs_this_month   = Job.query.filter(Job.created_at >= month_start).count()

    tokens_used_today = db.session.query(
        func.sum(UsageRecord.billable_output_count)
    ).filter(func.date(UsageRecord.created_at) == today).scalar() or 0

    tokens_used_month = db.session.query(
        func.sum(UsageRecord.billable_output_count)
    ).filter(UsageRecord.created_at >= month_start).scalar() or 0

    # Recent jobs
    recent_jobs = db.session.query(Job, User, Tenant, Tool)\
        .join(User, Job.user_id == User.id)\
        .join(Tenant, Job.tenant_id == Tenant.id)\
        .outerjoin(Tool, Job.tool_id == Tool.id)\
        .order_by(Job.created_at.desc())\
        .limit(10).all()

    # Recent pending registrations
    recent_registrations = User.query.filter(
        User.role != 'owner',
        User.active.is_(False)
    ).order_by(User.created_at.desc()).limit(5).all()

    return render_template('dashboard/owner_dashboard.html',
        total_tenants=total_tenants,
        active_tenants=active_tenants,
        total_users=total_users,
        active_users=active_users,
        pending_users=pending_users,
        jobs_today=jobs_today,
        jobs_this_month=jobs_this_month,
        tokens_used_today=tokens_used_today,
        tokens_used_month=tokens_used_month,
        recent_jobs=recent_jobs,
        recent_registrations=recent_registrations,
        now=datetime.utcnow()
    )


# ── Tenants List ──────────────────────────────────────────────────────────────
@owner_bp.route('/tenants')
@login_required
@owner_required
def tenants():
    search = request.args.get('search', '').strip()
    status = request.args.get('status', '')
    page   = request.args.get('page', 1, type=int)

    query = Tenant.query.filter(Tenant.plan_name != 'owner')
    if search:
        query = query.filter(Tenant.company_name.ilike(f'%{search}%'))
    if status:
        query = query.filter_by(status=status)

    tenants_page = query.order_by(Tenant.created_at.desc()).paginate(page=page, per_page=20, error_out=False)

    return render_template('dashboard/owner_tenants.html',
        tenants=tenants_page,
        search=search,
        status_filter=status
    )


# ── Tenant Detail ─────────────────────────────────────────────────────────────
@owner_bp.route('/tenants/<int:tenant_id>')
@login_required
@owner_required
def tenant_detail(tenant_id):
    tenant  = Tenant.query.get_or_404(tenant_id)
    users   = User.query.filter_by(tenant_id=tenant_id).all()
    jobs    = Job.query.filter_by(tenant_id=tenant_id).order_by(Job.created_at.desc()).limit(20).all()
    usage   = UsageRecord.query.filter_by(tenant_id=tenant_id).order_by(UsageRecord.created_at.desc()).limit(30).all()
    sub     = Subscription.query.filter_by(tenant_id=tenant_id).first()

    total_tokens_used = db.session.query(
        func.sum(UsageRecord.billable_output_count)
    ).filter_by(tenant_id=tenant_id).scalar() or 0

    return render_template('dashboard/owner_tenant_detail.html',
        tenant=tenant,
        users=users,
        jobs=jobs,
        usage=usage,
        subscription=sub,
        total_tokens_used=total_tokens_used
    )


# ── Activate / Deactivate Tenant ──────────────────────────────────────────────
@owner_bp.route('/tenants/<int:tenant_id>/activate', methods=['POST'])
@login_required
@owner_required
def activate_tenant(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    tokens = request.form.get('tokens', 100, type=int)
    plan   = request.form.get('plan', 'starter')

    tenant.status      = 'active'
    tenant.plan_name   = plan
    tenant.tokens_total = tokens

    # Activate the admin user of this tenant
    admin = User.query.filter_by(tenant_id=tenant_id, role='admin').first()
    if admin:
        admin.active = True

    db.session.commit()
    flash(f'{tenant.company_name} activated with {tokens} tokens.', 'success')
    return redirect(url_for('owner.tenant_detail', tenant_id=tenant_id))


@owner_bp.route('/tenants/<int:tenant_id>/suspend', methods=['POST'])
@login_required
@owner_required
def suspend_tenant(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    tenant.status = 'suspended'
    db.session.commit()
    flash(f'{tenant.company_name} suspended.', 'warning')
    return redirect(url_for('owner.tenant_detail', tenant_id=tenant_id))


# ── Top up tokens ─────────────────────────────────────────────────────────────
@owner_bp.route('/tenants/<int:tenant_id>/topup', methods=['POST'])
@login_required
@owner_required
def topup_tokens(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    amount = request.form.get('amount', 0, type=int)
    tenant.tokens_total += amount
    db.session.commit()
    flash(f'Added {amount} tokens to {tenant.company_name}.', 'success')
    return redirect(url_for('owner.tenant_detail', tenant_id=tenant_id))


# ── All Users ─────────────────────────────────────────────────────────────────
@owner_bp.route('/users')
@login_required
@owner_required
def users():
    search = request.args.get('search', '').strip()
    role   = request.args.get('role', '')
    status = request.args.get('status', '')
    page   = request.args.get('page', 1, type=int)

    query = User.query.filter(User.role != 'owner')
    if search:
        query = query.filter(
            (User.name.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )
    if role:
        query = query.filter_by(role=role)
    if status == 'pending':
        query = query.filter(User.active.is_(False))
    elif status == 'active':
        query = query.filter(User.active.is_(True))

    users_page = query.order_by(User.created_at.desc()).paginate(page=page, per_page=25, error_out=False)

    return render_template('dashboard/owner_users.html',
        users=users_page,
        search=search,
        role_filter=role,
        status_filter=status
    )




# ── Pending approvals ──────────────────────────────────────────────────────
@owner_bp.route('/approvals')
@login_required
@owner_required
def approvals():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '').strip()

    query = User.query.join(Tenant, User.tenant_id == Tenant.id).filter(
        User.role != 'owner',
        User.active.is_(False),
        Tenant.plan_name != 'owner'
    )
    if search:
        query = query.filter((User.name.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%')) | (Tenant.company_name.ilike(f'%{search}%')))

    users_page = query.order_by(User.created_at.desc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('dashboard/owner_users.html', users=users_page, search=search, role_filter='', status_filter='pending', approvals_mode=True)


@owner_bp.route('/approvals/<int:tenant_id>/approve', methods=['POST'])
@login_required
@owner_required
def approve_registration(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    tokens = max(request.form.get('tokens', 100, type=int), 0)
    plan = request.form.get('plan', 'starter').strip() or 'starter'

    tenant.status = 'active'
    tenant.plan_name = plan
    tenant.tokens_total = tokens
    if tenant.tokens_used > tenant.tokens_total:
        tenant.tokens_used = tenant.tokens_total

    admin_users = User.query.filter_by(tenant_id=tenant.id).all()
    for user in admin_users:
        if user.role in ('admin', 'staff', 'viewer'):
            user.active = True

    existing = Subscription.query.filter_by(tenant_id=tenant.id).first()
    if not existing:
        existing = Subscription(tenant_id=tenant.id)
        db.session.add(existing)
    existing.plan_name = plan
    existing.status = 'active'
    existing.tokens_per_month = tokens
    existing.monthly_price_gbp = existing.monthly_price_gbp or 0.0
    existing.billing_cycle_start = existing.billing_cycle_start or datetime.utcnow()
    existing.renewal_date = datetime.utcnow() + timedelta(days=30)

    db.session.commit()
    flash(f'{tenant.company_name} approved and activated with {tokens} tokens.', 'success')
    return redirect(url_for('owner.approvals'))


@owner_bp.route('/approvals/<int:tenant_id>/reject', methods=['POST'])
@login_required
@owner_required
def reject_registration(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    users = User.query.filter_by(tenant_id=tenant.id).all()
    for user in users:
        db.session.delete(user)
    sub = Subscription.query.filter_by(tenant_id=tenant.id).first()
    if sub:
        db.session.delete(sub)
    db.session.delete(tenant)
    db.session.commit()
    flash('Registration request rejected and removed.', 'warning')
    return redirect(url_for('owner.approvals'))

# ── Activate single user ──────────────────────────────────────────────────────
@owner_bp.route('/users/<int:user_id>/activate', methods=['POST'])
@login_required
@owner_required
def activate_user(user_id):
    user = User.query.get_or_404(user_id)
    user.active = True
    if user.tenant and user.tenant.status == 'pending':
        user.tenant.status = 'active'
        user.tenant.plan_name = user.tenant.plan_name if user.tenant.plan_name != 'pending' else 'starter'
        if user.tenant.tokens_total == 0:
            user.tenant.tokens_total = 100
    db.session.commit()
    flash(f'{user.name} activated.', 'success')
    return redirect(url_for('owner.users'))


@owner_bp.route('/users/<int:user_id>/deactivate', methods=['POST'])
@login_required
@owner_required
def deactivate_user(user_id):
    user = User.query.get_or_404(user_id)
    user.active = False
    db.session.commit()
    flash(f'{user.name} deactivated.', 'warning')
    return redirect(url_for('owner.users'))


# ── Job History ───────────────────────────────────────────────────────────────
@owner_bp.route('/jobs')
@login_required
@owner_required
def jobs():
    page   = request.args.get('page', 1, type=int)
    status = request.args.get('status', '')
    tool   = request.args.get('tool', '')

    query = db.session.query(Job, User, Tenant, Tool)\
        .join(User, Job.user_id == User.id)\
        .join(Tenant, Job.tenant_id == Tenant.id)\
        .outerjoin(Tool, Job.tool_id == Tool.id)

    if status:
        query = query.filter(Job.status == status)
    if tool:
        query = query.filter(Tool.slug == tool)

    jobs_page = query.order_by(Job.created_at.desc()).paginate(page=page, per_page=25, error_out=False)
    tools     = Tool.query.all()

    return render_template('dashboard/owner_jobs.html',
        jobs=jobs_page,
        tools=tools,
        status_filter=status,
        tool_filter=tool
    )


# ── Usage / Token Analytics ───────────────────────────────────────────────────
@owner_bp.route('/usage')
@login_required
@owner_required
def usage():
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Per tenant usage this month
    tenant_usage = db.session.query(
        Tenant.company_name,
        Tenant.tokens_total,
        Tenant.tokens_used,
        func.sum(UsageRecord.billable_output_count).label('month_tokens')
    ).join(UsageRecord, UsageRecord.tenant_id == Tenant.id)\
     .filter(UsageRecord.created_at >= month_start)\
     .group_by(Tenant.id)\
     .order_by(func.sum(UsageRecord.billable_output_count).desc())\
     .all()

    # Per tool usage this month
    tool_usage = db.session.query(
        Tool.display_name,
        func.sum(UsageRecord.billable_output_count).label('total')
    ).join(UsageRecord, UsageRecord.tool_id == Tool.id)\
     .filter(UsageRecord.created_at >= month_start)\
     .group_by(Tool.id)\
     .all()

    # Daily usage last 30 days
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    daily_usage = db.session.query(
        func.date(UsageRecord.created_at).label('day'),
        func.sum(UsageRecord.billable_output_count).label('tokens')
    ).filter(UsageRecord.created_at >= thirty_days_ago)\
     .group_by(func.date(UsageRecord.created_at))\
     .order_by(func.date(UsageRecord.created_at))\
     .all()

    return render_template('dashboard/owner_usage.html',
        tenant_usage=tenant_usage,
        tool_usage=tool_usage,
        daily_usage=daily_usage
    )


# ── Revenue / Billing ─────────────────────────────────────────────────────────
@owner_bp.route('/billing')
@login_required
@owner_required
def billing():
    subscriptions = db.session.query(Subscription, Tenant)\
        .join(Tenant, Subscription.tenant_id == Tenant.id)\
        .order_by(Subscription.created_at.desc())\
        .all()

    total_mrr = db.session.query(
        func.sum(Subscription.monthly_price_gbp)
    ).filter_by(status='active').scalar() or 0.0

    active_subs   = Subscription.query.filter_by(status='active').count()
    cancelled_subs = Subscription.query.filter_by(status='cancelled').count()

    return render_template('dashboard/owner_billing.html',
        subscriptions=subscriptions,
        total_mrr=total_mrr,
        active_subs=active_subs,
        cancelled_subs=cancelled_subs
    )


# ── Add / Edit Subscription ───────────────────────────────────────────────────
@owner_bp.route('/billing/add/<int:tenant_id>', methods=['POST'])
@login_required
@owner_required
def add_subscription(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)
    plan   = request.form.get('plan', 'starter')
    price  = request.form.get('price', 0.0, type=float)
    tokens = request.form.get('tokens', 100, type=int)

    existing = Subscription.query.filter_by(tenant_id=tenant_id).first()
    if existing:
        existing.plan_name          = plan
        existing.monthly_price_gbp  = price
        existing.tokens_per_month   = tokens
        existing.status             = 'active'
        existing.renewal_date       = datetime.utcnow() + timedelta(days=30)
    else:
        sub = Subscription(
            tenant_id=tenant_id,
            plan_name=plan,
            monthly_price_gbp=price,
            tokens_per_month=tokens,
            status='active',
            billing_cycle_start=datetime.utcnow(),
            renewal_date=datetime.utcnow() + timedelta(days=30)
        )
        db.session.add(sub)

    tenant.tokens_total = tokens
    db.session.commit()
    flash(f'Subscription updated for {tenant.company_name}.', 'success')
    return redirect(url_for('owner.billing'))


# ── System Health ─────────────────────────────────────────────────────────────
@owner_bp.route('/system')
@login_required
@owner_required
def system():
    total_jobs      = Job.query.count()
    failed_jobs     = Job.query.filter_by(status='failed').count()
    queued_jobs     = Job.query.filter_by(status='queued').count()
    running_jobs    = Job.query.filter_by(status='running').count()
    total_usage     = db.session.query(func.sum(UsageRecord.billable_output_count)).scalar() or 0
    total_tenants   = Tenant.query.filter(Tenant.plan_name != 'owner').count()
    total_users     = User.query.filter(User.role != 'owner').count()

    recent_failed = Job.query.filter_by(status='failed')\
        .order_by(Job.created_at.desc()).limit(10).all()

    return render_template('dashboard/owner_system.html',
        total_jobs=total_jobs,
        failed_jobs=failed_jobs,
        queued_jobs=queued_jobs,
        running_jobs=running_jobs,
        total_usage=total_usage,
        total_tenants=total_tenants,
        total_users=total_users,
        recent_failed=recent_failed,
        now=datetime.utcnow()
    )


# ── Change Owner Password ─────────────────────────────────────────────────────
@owner_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@owner_required
def settings():
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw     = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        if not bcrypt.checkpw(current_pw.encode('utf-8'), current_user.password_hash.encode('utf-8')):
            flash('Current password is incorrect.', 'error')
            return render_template('dashboard/owner_settings.html')

        if len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'error')
            return render_template('dashboard/owner_settings.html')

        if new_pw != confirm_pw:
            flash('Passwords do not match.', 'error')
            return render_template('dashboard/owner_settings.html')

        current_user.password_hash = bcrypt.hashpw(new_pw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.session.commit()
        flash('Password updated successfully.', 'success')

    return render_template('dashboard/owner_settings.html')
