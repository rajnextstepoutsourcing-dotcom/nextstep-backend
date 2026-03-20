import bcrypt
import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from app import db, mail
from app.models import User, Tenant, PasswordResetToken
from flask_mail import Message

auth_bp = Blueprint('auth', __name__)


# ── Login ────────────────────────────────────────────────────────────────────
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'

        user = User.query.filter_by(email=email).first()

        # Check credentials
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            flash('Invalid email or password.', 'error')
            return render_template('auth/login.html')

        # Check account is active
        if not user.active:
            flash('Your account is pending approval. We will notify you by email once activated.', 'warning')
            return render_template('auth/login.html')

        # Log in
        login_user(user, remember=remember)
        user.last_login = datetime.utcnow()
        db.session.commit()

        next_page = request.args.get('next')
        if next_page:
            return redirect(next_page)
        return _redirect_by_role(user)

    return render_template('auth/login.html')


# ── Logout ───────────────────────────────────────────────────────────────────
@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))


# ── Register ─────────────────────────────────────────────────────────────────
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return _redirect_by_role(current_user)

    if request.method == 'POST':
        full_name = request.form.get('full_name', '').strip()
        company   = request.form.get('company', '').strip()
        email     = request.form.get('email', '').strip().lower()
        phone     = request.form.get('phone', '').strip()
        password  = request.form.get('password', '')
        confirm   = request.form.get('confirm_password', '')
        tools     = request.form.getlist('tools')

        # Validation
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

        # Create tenant
        tenant = Tenant(
            company_name=company,
            status='pending',
            plan_name='pending',
            tokens_total=0
        )
        db.session.add(tenant)
        db.session.flush()

        # Create user (inactive until owner approves)
        pw_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user = User(
            tenant_id=tenant.id,
            name=full_name,
            email=email,
            password_hash=pw_hash,
            role='admin',
            active=False
        )
        db.session.add(user)
        db.session.commit()

        # Notify owner by email
        _notify_owner_new_registration(full_name, company, email, phone, tools)

        flash('Registration submitted! We will review and activate your account within one business day.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


# ── Forgot Password ───────────────────────────────────────────────────────────
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()

        # Always show success (don't reveal if email exists)
        if user and user.active:
            token_str  = secrets.token_urlsafe(48)
            expires_at = datetime.utcnow() + timedelta(hours=2)

            # Invalidate old tokens
            PasswordResetToken.query.filter_by(user_id=user.id, used=False).delete()

            reset_token = PasswordResetToken(
                user_id=user.id,
                token=token_str,
                expires_at=expires_at
            )
            db.session.add(reset_token)
            db.session.commit()

            reset_url = url_for('auth.reset_password', token=token_str, _external=True)
            _send_reset_email(user.email, user.name, reset_url)

        flash('If that email is registered, you will receive a password reset link shortly.', 'info')
        return redirect(url_for('auth.login'))

    return render_template('auth/forgot_password.html')


# ── Reset Password ────────────────────────────────────────────────────────────
@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    reset_token = PasswordResetToken.query.filter_by(token=token, used=False).first()

    if not reset_token or reset_token.expires_at < datetime.utcnow():
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('auth.forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')

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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _redirect_by_role(user):
    if user.role == 'owner':
        return redirect(url_for('owner.dashboard'))
    return redirect(url_for('dashboard.index'))


def _send_reset_email(to_email, name, reset_url):
    try:
        msg = Message(
            subject='NextStep — Password Reset Request',
            recipients=[to_email]
        )
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
        msg = Message(
            subject=f'NextStep — New Registration: {company}',
            recipients=[owner_email]
        )
        msg.body = f"""New registration request on NextStep:

Name:    {name}
Company: {company}
Email:   {email}
Phone:   {phone}
Tools:   {', '.join(tools) if tools else 'Not specified'}

Log in to your owner dashboard to review and activate this account:
https://nextstep.co.uk/owner/dashboard

— NextStep System
"""
        mail.send(msg)
    except Exception as e:
        print(f'[Mail Error] Could not notify owner: {e}')
