import os
from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()


def _resolve_secret_key(data_dir):
    """Sicheren SECRET_KEY liefern.

    Bevorzugt die ENV-Variable. Ist keine (oder nur der unsichere Platzhalter)
    gesetzt, wird ein persistenter Zufallsschlüssel im data-Verzeichnis erzeugt
    und wiederverwendet (Sessions überleben Neustarts). So gibt es keinen
    schwachen, bekannten Default-Schlüssel mehr.
    """
    import secrets
    key = os.environ.get('SECRET_KEY')
    if key and key not in ('bitte-aendern', 'bitte-aendern-mit-langem-zufallswert'):
        return key
    path = os.path.join(data_dir, 'secret_key')
    try:
        if os.path.exists(path):
            with open(path) as fh:
                existing = fh.read().strip()
            if existing:
                return existing
        generated = secrets.token_hex(32)
        with open(path, 'w') as fh:
            fh.write(generated)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return generated
    except OSError:
        return secrets.token_hex(32)


def create_app():
    app = Flask(__name__)

    data_dir = os.environ.get('DATA_DIR', '/app/data')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'uploads'), exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'branding'), exist_ok=True)

    app.config['SECRET_KEY'] = _resolve_secret_key(data_dir)
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(data_dir, 'database.db')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    app.config['UPLOAD_FOLDER'] = os.path.join(data_dir, 'uploads')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    # "Remember me"-Cookie ebenfalls absichern (sonst CSRF-Vektor, da ohne SameSite
    # cross-site mitgesendet). Secure-Flags per ENV aktivieren, wenn hinter HTTPS.
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'
    _secure = os.environ.get('COOKIE_SECURE', '').lower() == 'true'
    app.config['SESSION_COOKIE_SECURE'] = _secure
    app.config['REMEMBER_COOKIE_SECURE'] = _secure

    # Hinter einem Reverse-Proxy die echte Client-IP aus X-Forwarded-* übernehmen
    # (wichtig für korrektes Rate-Limiting). Default 0 = kein Proxy vertrauen.
    try:
        _proxies = int(os.environ.get('TRUSTED_PROXIES', '0') or 0)
    except ValueError:
        _proxies = 0
    if _proxies > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_proxies, x_proto=_proxies, x_host=_proxies)

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify({'error': 'Nicht angemeldet'}), 401

    # Automatische Abmeldung nach 15 Minuten Inaktivität. Jeder Request eines
    # angemeldeten Nutzers verlängert die Sitzung – außer /api/auth/me, damit
    # das Status-Polling des Frontends die Sitzung nicht künstlich am Leben hält.
    SESSION_TIMEOUT_SECONDS = 15 * 60

    @app.before_request
    def _session_timeout():
        import time
        from flask import session, request
        from flask_login import current_user, logout_user
        if not current_user.is_authenticated:
            return
        last = session.get('last_seen')
        now = time.time()
        if last is not None and now - last > SESSION_TIMEOUT_SECONDS:
            logout_user()   # löscht auch ein evtl. vorhandenes Remember-Cookie
            session.pop('last_seen', None)
            return          # Request läuft anonym weiter -> 401 bei geschützten Routen
        if last is None or request.path != '/api/auth/me':
            session['last_seen'] = now

    # Security-Header für alle Antworten. setdefault, damit Routen mit eigener,
    # strengerer Policy (E-Mail-Ansicht, Logo) nicht überschrieben werden.
    # SAMEORIGIN statt DENY: die Original-Mail-Ansicht läuft in einem eigenen iframe.
    _CSP = ("default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'self'")

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        if (resp.content_type or '').startswith('text/html'):
            resp.headers.setdefault('Content-Security-Policy', _CSP)
        return resp

    with app.app_context():
        from .models import User, Supplier, Proposal, Settings, Alternative, Quote  # noqa: F401
        db.create_all()
        _migrate()

    from .imap_worker import start_imap_worker
    start_imap_worker(app)

    from .auth import auth_bp
    from .api import api_bp
    from .main import main_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(main_bp)

    return app


def _migrate():
    from sqlalchemy import text
    migrations = [
        # Original migrations
        'ALTER TABLE proposals ADD COLUMN notizen TEXT',
        'ALTER TABLE proposals ADD COLUMN einreicher_name VARCHAR(200)',
        'ALTER TABLE proposals ADD COLUMN einreicher_email VARCHAR(200)',
        'ALTER TABLE proposals ADD COLUMN einreicher_tel VARCHAR(50)',
        # User role column
        'ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT "betrachter"',
        # Proposal new columns (new rows default to 'pending'; existing rows handled by UPDATE below)
        'ALTER TABLE proposals ADD COLUMN status VARCHAR(20) DEFAULT "pending"',
        'ALTER TABLE proposals ADD COLUMN approved_by_id INTEGER',
        'ALTER TABLE proposals ADD COLUMN approved_at DATETIME',
        'ALTER TABLE proposals ADD COLUMN menge INTEGER DEFAULT 1',
        'ALTER TABLE proposals ADD COLUMN stueckpreis_geschaetzt FLOAT',
        'ALTER TABLE proposals ADD COLUMN geplanter_zeitpunkt VARCHAR(20)',
        'ALTER TABLE proposals ADD COLUMN rejection_reason TEXT',
        'ALTER TABLE proposals ADD COLUMN abteilung VARCHAR(200)',
        # Beschaffungsabschluss / Historie
        'ALTER TABLE proposals ADD COLUMN beschafft_am DATETIME',
        'ALTER TABLE proposals ADD COLUMN beschafft_supplier_id INTEGER REFERENCES suppliers(id)',
        'ALTER TABLE proposals ADD COLUMN beschafft_lieferant VARCHAR(200)',
        'ALTER TABLE proposals ADD COLUMN rechnungsbetrag FLOAT',
        'ALTER TABLE proposals ADD COLUMN rechnung_filepath VARCHAR(512)',
        'ALTER TABLE proposals ADD COLUMN rechnung_filename VARCHAR(256)',
        '''CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME,
            user_id INTEGER,
            username VARCHAR(80),
            action VARCHAR(60),
            entity VARCHAR(160),
            details TEXT,
            ip VARCHAR(64)
        )''',
        # New tables
        '''CREATE TABLE IF NOT EXISTS alternatives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_nr VARCHAR(20) NOT NULL REFERENCES proposals(nr) ON DELETE CASCADE,
            hersteller VARCHAR(200) NOT NULL,
            modell VARCHAR(200) NOT NULL,
            notiz TEXT
        )''',
        '''CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_nr VARCHAR(20) NOT NULL REFERENCES proposals(nr) ON DELETE CASCADE,
            supplier_id INTEGER REFERENCES suppliers(id),
            alternative_id INTEGER REFERENCES alternatives(id) ON DELETE SET NULL,
            preis_stueck FLOAT NOT NULL,
            lieferzeit VARCHAR(100),
            notizen TEXT,
            filepath VARCHAR(512),
            filename VARCHAR(256),
            erstellt_am DATETIME DEFAULT CURRENT_TIMESTAMP,
            erstellt_von_id INTEGER REFERENCES users(id)
        )''',
        # Set status for existing rows that have NULL status
        'UPDATE proposals SET status="approved" WHERE status IS NULL',
        # Quote: IMAP source tracking
        'ALTER TABLE quotes ADD COLUMN source VARCHAR(20) DEFAULT "manual"',
        'ALTER TABLE quotes ADD COLUMN sender_email VARCHAR(200)',
        'ALTER TABLE quotes ADD COLUMN eml_path VARCHAR(512)',
        'ALTER TABLE users ADD COLUMN email VARCHAR(200)',
        'ALTER TABLE users ADD COLUMN notify BOOLEAN DEFAULT 0',
        'UPDATE users SET notify=1 WHERE role=\'beschaffer\'',
    ]
    for sql in migrations:
        try:
            db.session.execute(text(sql))
            db.session.commit()
        except Exception:
            db.session.rollback()

    # Set role based on legacy is_admin column for existing users where role is still NULL
    try:
        db.session.execute(text("UPDATE users SET role='admin' WHERE is_admin=1 AND (role IS NULL OR role='betrachter')"))
        db.session.execute(text("UPDATE users SET role='betrachter' WHERE (is_admin=0 OR is_admin IS NULL) AND role IS NULL"))
        db.session.commit()
    except Exception:
        db.session.rollback()


