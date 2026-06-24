from flask import Blueprint, request, jsonify
from flask_login import login_user, logout_user, current_user, login_required
from .models import User
from . import db
from .ratelimit import rate_limit

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
@rate_limit(30, 300, 'login')   # max. 30 Versuche / 5 min / IP
def login():
    data = request.get_json() or {}
    ident = (data.get('username') or '').strip()
    password = data.get('password') or ''
    user = User.query.filter_by(username=ident).first()
    if not user and ident:
        user = User.query.filter(db.func.lower(User.email) == ident.lower()).first()
    if user and user.check_password(password):
        login_user(user, remember=True)
        return jsonify({'user': user.to_dict()})
    return jsonify({'error': 'Ungültige Anmeldedaten'}), 401


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'ok': True})


@auth_bp.route('/me')
def me():
    if current_user.is_authenticated:
        return jsonify({'user': current_user.to_dict()})
    return jsonify({'user': None}), 401


@auth_bp.route('/needs-setup')
def needs_setup():
    return jsonify({'needsSetup': User.query.count() == 0})


@auth_bp.route('/setup', methods=['POST'])
def setup():
    if User.query.count() > 0:
        return jsonify({'error': 'Setup bereits abgeschlossen'}), 403
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    email = (data.get('email') or '').strip()
    if not username or len(password) < 6:
        return jsonify({'error': 'Benutzername oder Passwort ungültig (min. 6 Zeichen)'}), 400
    if not email or '@' not in email:
        return jsonify({'error': 'Gültige E-Mail-Adresse erforderlich'}), 400
    user = User(username=username, role='admin', email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    return jsonify({'user': user.to_dict()})
