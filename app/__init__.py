import os
from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()


def create_app():
    app = Flask(__name__)

    data_dir = os.environ.get('DATA_DIR', '/app/data')
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(os.path.join(data_dir, 'uploads'), exist_ok=True)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bitte-aendern')
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(data_dir, 'database.db')}"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}
    app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
    app.config['UPLOAD_FOLDER'] = os.path.join(data_dir, 'uploads')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    db.init_app(app)
    login_manager.init_app(app)

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify({'error': 'Nicht angemeldet'}), 401

    with app.app_context():
        from .models import User, Supplier, Proposal, Settings, Alternative, Quote  # noqa: F401
        db.create_all()
        _migrate()
        _seed_suppliers()

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


def _seed_suppliers():
    from .models import Supplier
    if Supplier.query.count() == 0:
        initial = [
            dict(name='Matuczak Feuerschutz', ansprechpartner='John-Robert Ramm',
                 tel='0160 90723350', email='j-r.ramm@matuczak.de', is_test=False),
            dict(name='CB König', ansprechpartner='Matthias Norton',
                 tel='', email='mnorton@cbkoenig.de', is_test=False),
            dict(name='Kraft Feuerschutz', ansprechpartner='Björn Beeken',
                 tel='0162 9416990', email='beeken@kraft-feuerschutz.de', is_test=False),
            dict(name='Jakob Nawrot', ansprechpartner='Jakob Nawrot',
                 tel='0173 4887450', email='jakob.nawrot@feuerwehr-moorrege.de', is_test=True),
        ]
        for s in initial:
            db.session.add(Supplier(**s))
        db.session.commit()
