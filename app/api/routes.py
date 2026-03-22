from flask import Blueprint, request, jsonify
from datetime import datetime
from app.models import UserSession, User, Tenant

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/validate-session')
def validate_session():
    token = request.args.get('token')
    if not token:
        return jsonify({'valid': False, 'error': 'missing token'}), 400

    session = UserSession.query.filter_by(token=token).first()
    if not session:
        return jsonify({'valid': False, 'error': 'invalid token'}), 401

    if session.expires_at and session.expires_at < datetime.utcnow():
        return jsonify({'valid': False, 'error': 'token expired'}), 401

    user = User.query.get(session.user_id)
    if not user:
        return jsonify({'valid': False, 'error': 'user not found'}), 404

    tenant = Tenant.query.get(user.tenant_id) if user.tenant_id else None
    if not tenant:
        return jsonify({'valid': False, 'error': 'tenant not found'}), 404

    return jsonify({
        'valid': True,
        'user': {
            'id': user.id,
            'email': user.email,
            'name': user.name,
            'role': user.role
        },
        'tenant': {
            'id': tenant.id,
            'name': tenant.company_name,
            'plan_name': tenant.plan_name
        }
    }), 200
