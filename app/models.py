import json
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db, login_manager


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='betrachter')
    email = db.Column(db.String(200))
    notify = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_admin(self):
        return self.role == 'admin'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'role': self.role,
            'email': self.email or '',
            'notify': bool(self.notify),
            'is_admin': self.is_admin,
            'created_at': self.created_at.strftime('%d.%m.%Y') if self.created_at else None,
        }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class Proposal(db.Model):
    __tablename__ = 'proposals'
    id = db.Column(db.Integer, primary_key=True)
    nr = db.Column(db.String(20), unique=True, nullable=False)
    bezeichnung = db.Column(db.String(200), nullable=False)
    hersteller = db.Column(db.String(200))
    modell = db.Column(db.String(200))
    kategorie = db.Column(db.String(500))
    anlass = db.Column(db.String(500))
    sachverhalt = db.Column(db.Text)
    risiken = db.Column(db.Text)
    kosten = db.Column(db.Float, default=0)
    beschaffungsart = db.Column(db.String(100))
    foerderung = db.Column(db.String(500))
    prioritaet = db.Column(db.String(20))
    ablauf = db.Column(db.Text, default='[]')
    notizen = db.Column(db.Text)
    einreicher_name = db.Column(db.String(200))
    einreicher_email = db.Column(db.String(200))
    einreicher_tel = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # New columns
    status = db.Column(db.String(20), nullable=False, default='pending')
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    menge = db.Column(db.Integer, default=1)
    stueckpreis_geschaetzt = db.Column(db.Float, nullable=True)
    geplanter_zeitpunkt = db.Column(db.String(20))  # geplantes Beschaffungsjahr, z. B. "2028"
    rejection_reason = db.Column(db.Text)  # Ablehnungsgrund (nur bei status='rejected')

    attachments = db.relationship('Attachment', backref='proposal', lazy=True,
                                  cascade='all, delete-orphan')
    alternatives = db.relationship('Alternative', backref='proposal', lazy=True,
                                   cascade='all, delete-orphan')
    quotes = db.relationship('Quote', backref='proposal', lazy=True,
                             cascade='all, delete-orphan')
    approved_by = db.relationship('User', foreign_keys=[approved_by_id])

    def to_dict(self):
        return {
            'nr': self.nr,
            'bezeichnung': self.bezeichnung,
            'hersteller': self.hersteller or '',
            'modell': self.modell or '',
            'kategorie': self.kategorie or '—',
            'anlass': self.anlass or '',
            'sachverhalt': self.sachverhalt or '',
            'risiken': self.risiken or '',
            'kosten': self.kosten or 0,
            'beschaffungsart': self.beschaffungsart or '',
            'foerderung': self.foerderung or 'keine',
            'prioritaet': self.prioritaet or '—',
            'ablauf': json.loads(self.ablauf or '[]'),
            'notizen': self.notizen or '',
            'einreicher_name': self.einreicher_name or '',
            'einreicher_email': self.einreicher_email or '',
            'einreicher_tel': self.einreicher_tel or '',
            'created_at': self.created_at.strftime('%d.%m.%Y') if self.created_at else '',
            'files': [a.to_dict() for a in self.attachments],
            'status': self.status or 'pending',
            'approved_by_id': self.approved_by_id,
            'approved_by': self.approved_by.username if self.approved_by else None,
            'approved_at': self.approved_at.strftime('%d.%m.%Y %H:%M') if self.approved_at else None,
            'menge': self.menge or 1,
            'stueckpreis_geschaetzt': self.stueckpreis_geschaetzt,
            'geplanter_zeitpunkt': self.geplanter_zeitpunkt or '',
            'rejection_reason': self.rejection_reason or '',
        }


class Attachment(db.Model):
    __tablename__ = 'attachments'
    id = db.Column(db.Integer, primary_key=True)
    proposal_nr = db.Column(db.String(20), db.ForeignKey('proposals.nr'), nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    filesize = db.Column(db.Integer)
    filepath = db.Column(db.String(512))

    def to_dict(self):
        return {'name': self.filename, 'size': self.filesize, 'path': self.filepath}


class Settings(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=''):
        s = db.session.get(Settings, key)
        return s.value if s else default

    @staticmethod
    def set(key, value):
        s = db.session.get(Settings, key)
        if s:
            s.value = value
        else:
            db.session.add(Settings(key=key, value=value))


class AuditLog(db.Model):
    """Änderungs-/Aktivitätsprotokoll (wer hat wann was geändert)."""
    __tablename__ = 'audit_log'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(80))      # Snapshot (bleibt auch nach User-Löschung)
    action = db.Column(db.String(60))        # z. B. 'proposal.approve'
    entity = db.Column(db.String(160))       # z. B. 'Vorschlag 01/2026'
    details = db.Column(db.Text)             # menschenlesbar, inkl. Feld-Änderungen
    ip = db.Column(db.String(64))

    def to_dict(self):
        return {
            'id': self.id,
            'created_at': self.created_at.strftime('%d.%m.%Y %H:%M:%S') if self.created_at else '',
            'username': self.username or '—',
            'action': self.action or '',
            'entity': self.entity or '',
            'details': self.details or '',
            'ip': self.ip or '',
        }


class Supplier(db.Model):
    __tablename__ = 'suppliers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    ansprechpartner = db.Column(db.String(200))
    tel = db.Column(db.String(50))
    email = db.Column(db.String(200), nullable=False)
    is_test = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'ansprechpartner': self.ansprechpartner or '',
            'tel': self.tel or '',
            'email': self.email,
            'test': self.is_test,
        }


class Alternative(db.Model):
    __tablename__ = 'alternatives'
    id = db.Column(db.Integer, primary_key=True)
    proposal_nr = db.Column(db.String(20), db.ForeignKey('proposals.nr', ondelete='CASCADE'),
                            nullable=False)
    hersteller = db.Column(db.String(200), nullable=False)
    modell = db.Column(db.String(200), nullable=False)
    notiz = db.Column(db.Text)

    def to_dict(self):
        return {
            'id': self.id,
            'proposal_nr': self.proposal_nr,
            'hersteller': self.hersteller,
            'modell': self.modell,
            'notiz': self.notiz or '',
        }


class Quote(db.Model):
    __tablename__ = 'quotes'
    id = db.Column(db.Integer, primary_key=True)
    proposal_nr = db.Column(db.String(20), db.ForeignKey('proposals.nr', ondelete='CASCADE'),
                            nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'), nullable=True)
    alternative_id = db.Column(db.Integer, db.ForeignKey('alternatives.id', ondelete='SET NULL'), nullable=True)
    preis_stueck = db.Column(db.Float, nullable=False)
    lieferzeit = db.Column(db.String(100))
    notizen = db.Column(db.Text)
    filepath = db.Column(db.String(512))
    filename = db.Column(db.String(256))
    eml_path = db.Column(db.String(512))
    erstellt_am = db.Column(db.DateTime, default=datetime.utcnow)
    erstellt_von_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    source = db.Column(db.String(20), default='manual')
    sender_email = db.Column(db.String(200), nullable=True)

    supplier = db.relationship('Supplier', foreign_keys=[supplier_id])
    erstellt_von = db.relationship('User', foreign_keys=[erstellt_von_id])

    def to_dict(self):
        return {
            'id': self.id,
            'proposal_nr': self.proposal_nr,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'alternative_id': self.alternative_id,
            'preis_stueck': self.preis_stueck,
            'lieferzeit': self.lieferzeit or '',
            'notizen': self.notizen or '',
            'filepath': self.filepath or '',
            'filename': self.filename or '',
            'eml_path': self.eml_path or '',
            'erstellt_am': self.erstellt_am.strftime('%d.%m.%Y %H:%M') if self.erstellt_am else None,
            'erstellt_von_id': self.erstellt_von_id,
            'source': self.source or 'manual',
            'sender_email': self.sender_email or '',
        }


# ── Vergabe-Schwellen (konfigurierbar) ──────────────────────────────────────────

DEFAULT_VERGABE_TIERS = [
    {'key': 'direkt', 'label': 'Direktauftrag', 'max': 50000,
     'info': 'Freihändige Vergabe zulässig (§14 UVgO). Empfehlung: mind. 1 Vergleichsangebot.'},
    {'key': 'beschraenkt', 'label': 'Beschränkte Ausschreibung', 'max': 150000,
     'info': 'Beschränkte Ausschreibung ohne Teilnahmewettbewerb (§3 SHVgVO). Mind. 3 Angebote einholen.'},
    {'key': 'oeffentlich', 'label': 'Öffentliche Ausschreibung', 'max': 221000,
     'info': 'Öffentliche Ausschreibung gem. UVgO. Bekanntmachung über e-Vergabe-SH.'},
    {'key': 'europa', 'label': 'Europaweite Ausschreibung', 'max': None,
     'info': 'EU-Schwellenwert überschritten. Europaweite Ausschreibung nach VgV/GWB. Gemeinde einbeziehen.'},
]

_VERGABE_KEYS = [t['key'] for t in DEFAULT_VERGABE_TIERS]


def get_vergabe_tiers():
    """Konfigurierte Vergabe-Stufen liefern; bei leer/ungültig die Defaults."""
    import copy
    raw = Settings.get('vergabe_tiers')
    if raw:
        try:
            tiers = json.loads(raw)
            if (isinstance(tiers, list) and len(tiers) == len(DEFAULT_VERGABE_TIERS)
                    and [t.get('key') for t in tiers] == _VERGABE_KEYS):
                return tiers
        except (ValueError, TypeError):
            pass
    return copy.deepcopy(DEFAULT_VERGABE_TIERS)


# ── Texte der Seite "Neuer Vorschlag" (konfigurierbar) ───────────────────────────

DEFAULT_FORM_HEADING = 'Beschaffungsvorschlag einreichen'
DEFAULT_FORM_INTRO = (
    'Jeder kann hier einen Vorschlag einreichen. '
    'Die Bearbeitung und Angebotseinholung erfolgt durch die Wehrführung.'
)


def get_form_texts():
    """Überschrift und Einleitungstext der Vorschlags-Seite; bei leer die Defaults."""
    return {
        'heading': Settings.get('form_heading') or DEFAULT_FORM_HEADING,
        'intro': Settings.get('form_intro') or DEFAULT_FORM_INTRO,
    }


# ── Branding / Erscheinungsbild (konfigurierbar, update-sicher im data-Volume) ──

DEFAULT_BRAND = {
    'name': 'Freiwillige Feuerwehr Musterstadt',
    'subtitle': 'Beschaffungsmanagement',
    'address': 'Musterstraße 1 · 12345 Musterstadt',
    'color_primary': '#0785B7',
    'color_accent': '#E95146',
    'color_bg': '#f5f7fa',
}


def _lighten(hex_color, factor=0.18):
    """Hex-Farbe Richtung Weiß aufhellen (für die Hover-Variante der Primärfarbe)."""
    try:
        h = (hex_color or '').lstrip('#')
        if len(h) != 6:
            return hex_color
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r = round(r + (255 - r) * factor)
        g = round(g + (255 - g) * factor)
        b = round(b + (255 - b) * factor)
        return f'#{r:02x}{g:02x}{b:02x}'
    except (ValueError, TypeError):
        return hex_color


def get_branding():
    """Aktuelle Branding-Werte; je Schlüssel der gespeicherte Wert oder der Default."""
    primary = Settings.get('brand_color_primary') or DEFAULT_BRAND['color_primary']
    return {
        'name': Settings.get('brand_name') or DEFAULT_BRAND['name'],
        'subtitle': Settings.get('brand_subtitle') or DEFAULT_BRAND['subtitle'],
        'address': Settings.get('brand_address') or DEFAULT_BRAND['address'],
        'color_primary': primary,
        'primary_light': _lighten(primary),
        'color_accent': Settings.get('brand_color_accent') or DEFAULT_BRAND['color_accent'],
        'color_bg': Settings.get('brand_color_bg') or DEFAULT_BRAND['color_bg'],
        'logo_url': '/api/branding/logo',
    }
