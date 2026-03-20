import bcrypt
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask import make_response
from flask_mail import Message

from app import db, mail
from app.models import User, Tenant, PasswordResetToken, UserSession

auth_bp = Blueprint('auth', __name__)


def _build_ns_token_response(user, destination, remember=False):
    token_str = secrets.token_urlsafe(48)
    expires_at = datetime.utcnow() + timedelta(days=7)

    UserSession.query.filter_by(user_id=user.id).delete()
    session_record = UserSession(user_id=user.id, token=token_str, expires_at=expires_at)
    db.session.add(session_record)
    db.session.commit()

    response = make_response(destination)
    cookie_kwargs = dict(
        httponly=True,
        samesite='None' if current_app.config.get('SESSION_COOKIE_SECURE') else 'Lax',
        secure=current_app.config.get('SESSION_COOKIE_SECURE', False),
        max_age=60 * 60 * 24 * 7,
        path='/'
    )
    cookie_domain = current_app.config.get('COOKIE_DOMAIN')
    if cookie_domain:
        cookie_kwargs['domain'] = cookie_domain

    response.set_cookie('ns_token', token_str, **cookie_kwargs)
    return response


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(email=email).first()

        if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            flash('Invalid email or password.', 'error')
            return render_template('auth/login.html')

        if not user.active:
            flash('Your account is pending approval. We will notify you by email once activated.', 'warning')
            return render_template('auth/login.html')

        if user.tenant and user.tenant.status != 'active':
            flash('Your company account is not active yet. Please contact NextStep support.', 'warning')
            return render_template('auth/login.html')

        login_user(user, remember=remember)
        user.last_login = datetime.utcnow()
        db.session.commit()

        next_page = request.args.get('next')
        dest = redirect(next_page) if next_page else _redirect_by_role(user)
        return _build_ns_token_response(user, dest, remember=remember)

    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    try:
        UserSession.query.filter_by(user_id=current_user.id).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
    logout_user()
    response = make_response(redirect(url_for('auth.login')))
    response.delete_cookie('ns_token', path='/')
    flash('You have been logged out.', 'info')
    return response


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        company = request.form.get('company', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        tools = request.form.getlist('tools')
        requested_tools = ', '.join(tools)

        if not all([full_name, company, email, phone, password, confirm]):
            flash('Please fill in all required fields.', 'error')
            return render_template('auth/register.html')

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('auth/register.html')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('auth/register.html')

        if User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'error')
            return render_template('auth/register.html')

        tenant = Tenant(company_name=company, status='pending', plan_name='pending', tokens_total=0)
        db.session.add(tenant)
        db.session.flush()

        pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user = User(
            tenant_id=tenant.id,
            name=full_name,
            email=email,
            phone=phone,
            requested_tools=requested_tools,
            password_hash=pw_hash,
            role='admin',
            active=False
        )
        db.session.add(user)
        db.session.commit()

        _notify_owner_new_registration(full_name, company, email, phone, tools)

        flash('Registration submitted! We will review and activate your account within one business day.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        if user and user.active:
            token_str = secrets.token_urlsafe(48)
            expires_at = datetime.utcnow() + timedelta(hours=2)
            PasswordResetToken.query.filter_by(user_id=user.id, used=False).delete()
            reset_token = PasswordResetToken(user_id=user.id, token=token_str, expires_at=expires_at)
            db.session.add(reset_token)
            db.session.commit()
            reset_url = url_for('auth.reset_password', token=token_str, _external=True)
            _send_reset_email(user.email, user.name, reset_url)

        flash('If that email is registered, you will receive a password reset link shortly.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()

    if not reset_token or reset_token.expires_at < datetime.utcnow():
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')

        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('auth/reset_password.html', token=token)

        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('auth/reset_password.html', token=token)

        user = reset_token.user
        user.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        reset_token.used = True
        db.session.commit()

        flash('Password reset successfully. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)


def _redirect_by_role(user):
    if user.role == 'owner':
        return redirect(url_for('owner.dashboard'))
    return redirect(url_for('dashboard.index'))


def _send_reset_email(to_email, name, reset_url):
    try:
        msg = Message(subject='NextStep — Password Reset Request', recipients=[to_email])
        msg.body = f"""Hi {name},

You requested a password reset for your NextStep account.

Click the link below to reset your password (valid for 2 hours):
{reset_url}

If you did not request this, please ignore this email.

— NextStep Team
"""
        mail.send(msg)
    except Exception as e:
        print(f'[Mail Error] Could not send reset email: {e}')


def _notify_owner_new_registration(name, company, email, phone, tools):
    import os
    owner_email = os.environ.get('OWNER_EMAIL', 'raj.nextstepoutsourcing@gmail.com')
    try:
        msg = Message(subject=f'NextStep — New Registration: {company}', recipients=[owner_email])
        msg.body = f"""New registration request on NextStep:

Name:    {name}
Company: {company}
Email:   {email}
Phone:   {phone}
Tools:   {', '.join(tools) if tools else 'Not specified'}

Log in to your owner dashboard to review and activate this account:
{url_for('owner.approvals', _external=True)}

— NextStep System
"""
        mail.send(msg)
    except Exception as e:
        print(f'[Mail Error] Could not notify owner: {e}')
