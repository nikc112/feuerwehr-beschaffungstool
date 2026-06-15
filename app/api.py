import json
import os
from datetime import datetime
from functools import wraps

from flask import (Blueprint, request, jsonify, current_app,
                   send_from_directory, make_response, url_for, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from .models import (Proposal, Attachment, Supplier, User, Settings, Alternative, Quote,
                     get_vergabe_tiers, _VERGABE_KEYS,
                     DEFAULT_FORM_HEADING, DEFAULT_FORM_INTRO)
from . import db
from .email_service import send_email
from .notifications import notify_new_proposal
from .email_view import parse_email, render_email_page

api_bp = Blueprint('api', __name__)

_MASK = '●●●●●●'

DEFAULT_EMAIL_SUBJECT = 'Angebotsanfrage: {bezeichnung} – FF Moorrege'
DEFAULT_EMAIL_BODY = (
    'Guten Tag {ansprechpartner},\n\n'
    'hiermit bitten wir Sie um ein Angebot für folgendes Produkt:\n\n'
    'Bezeichnung:  {bezeichnung}\n'
    'Hersteller:   {hersteller}\n'
    'Modell / Typ: {modell}\n'
    'Kategorie:    {kategorie}\n'
    '{kosten}'
    '\nVorgangsnummer: {nr}\n\n'
    'Bitte senden Sie uns Ihr Angebot inkl. Lieferbedingungen und Lieferzeit zu.\n\n'
    'Mit freundlichen Grüßen\n'
    'Freiwillige Feuerwehr Moorrege\n'
    'Wedeler Ch. 67, 25436 Moorrege\n'
    'Wehrfuehrung@feuerwehr-moorrege.de'
)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Nicht angemeldet'}), 401
        if not current_user.is_admin:
            return jsonify({'error': 'Admin-Rechte erforderlich'}), 403
        return f(*args, **kwargs)
    return decorated


def beschaffer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'error': 'Nicht angemeldet'}), 401
        if current_user.role not in ('beschaffer', 'admin'):
            return jsonify({'error': 'Beschaffer-Rechte erforderlich'}), 403
        return f(*args, **kwargs)
    return decorated


def _next_nr():
    year = datetime.now().year
    proposals = Proposal.query.filter(Proposal.nr.like(f'%/{year}')).all()
    nums = []
    for p in proposals:
        try:
            nums.append(int(p.nr.split('/')[0]))
        except ValueError:
            pass
    next_num = (max(nums) + 1) if nums else 1
    return f'{next_num:02d}/{year}'


# ── PROPOSALS ──────────────────────────────────────────────────────────────────

@api_bp.route('/proposals', methods=['POST'])
def create_proposal():
    # Public endpoint by design — no login required, proposals go into pending status for approval
    ct = request.content_type or ''
    if 'multipart/form-data' in ct:
        form = request.form
        try:
            ablauf = json.loads(form.get('ablauf', '[]'))
        except (ValueError, TypeError):
            ablauf = []
        files = request.files.getlist('files')
    else:
        form = request.get_json() or {}
        ablauf = form.get('ablauf', [])
        if isinstance(ablauf, str):
            try:
                ablauf = json.loads(ablauf)
            except (ValueError, TypeError):
                ablauf = []
        files = []

    if not (form.get('bezeichnung') or '').strip():
        return jsonify({'error': 'Bezeichnung erforderlich'}), 400

    nr = _next_nr()
    proposal = Proposal(
        nr=nr,
        bezeichnung=(form.get('bezeichnung') or '').strip(),
        hersteller=(form.get('hersteller') or '').strip(),
        modell=(form.get('modell') or '').strip(),
        kategorie=form.get('kategorie') or '—',
        anlass=form.get('anlass') or '—',
        sachverhalt=form.get('sachverhalt') or '',
        risiken=form.get('risiken') or '',
        kosten=float(form.get('kosten') or 0),
        beschaffungsart=form.get('beschaffungsart') or '',
        foerderung=form.get('foerderung') or 'keine',
        prioritaet=form.get('prioritaet') or '—',
        ablauf=json.dumps(ablauf),
        einreicher_name=(form.get('einreicher_name') or '').strip(),
        einreicher_email=(form.get('einreicher_email') or '').strip(),
        einreicher_tel=(form.get('einreicher_tel') or '').strip(),
    )
    db.session.add(proposal)
    db.session.flush()

    upload_folder = current_app.config['UPLOAD_FOLDER']
    for f in files:
        if f.filename and f.content_type == 'application/pdf':
            filename = secure_filename(f.filename)
            safe_name = f'{nr.replace("/", "-")}_{filename}'
            filepath = os.path.join(upload_folder, safe_name)
            f.save(filepath)
            db.session.add(Attachment(
                proposal_nr=nr,
                filename=filename,
                filesize=os.path.getsize(filepath),
                filepath=safe_name,
            ))

    db.session.commit()
    notify_new_proposal(current_app._get_current_object(), nr,
                        proposal.bezeichnung, proposal.einreicher_name)
    return jsonify({'nr': nr}), 201


@api_bp.route('/proposals/pending', methods=['GET'])
@beschaffer_required
def list_pending_proposals():
    """Returns pending AND rejected proposals for the Eingangskorb."""
    proposals = Proposal.query.filter(
        Proposal.status.in_(['pending', 'rejected'])
    ).order_by(Proposal.created_at.desc()).all()
    return jsonify([p.to_dict() for p in proposals])


@api_bp.route('/proposals', methods=['GET'])
@login_required
def list_proposals():
    allowed_statuses = {'approved', 'pending', 'rejected'}
    status_filter = request.args.get('status', 'approved')
    if status_filter not in allowed_statuses:
        return jsonify({'error': 'Ungültiger Status'}), 400
    if status_filter != 'approved' and current_user.role not in ('beschaffer', 'admin'):
        return jsonify({'error': 'Beschaffer-Rechte erforderlich'}), 403
    proposals = Proposal.query.filter_by(status=status_filter).order_by(Proposal.created_at.desc()).all()
    return jsonify([p.to_dict() for p in proposals])


@api_bp.route('/proposals/<path:nr>', methods=['PUT'])
@login_required
def update_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    data = request.get_json() or {}
    if 'kosten' in data:
        try:
            proposal.kosten = float(data['kosten'])
        except (ValueError, TypeError):
            pass
    if 'beschaffungsart' in data:
        proposal.beschaffungsart = (data['beschaffungsart'] or '').strip()
    if 'ablauf' in data:
        ablauf = data['ablauf']
        if isinstance(ablauf, list):
            proposal.ablauf = json.dumps(ablauf)
    if 'notizen' in data:
        proposal.notizen = data['notizen'] or ''
    if 'prioritaet' in data:
        proposal.prioritaet = (data['prioritaet'] or '').strip()
    if 'menge' in data:
        try:
            proposal.menge = int(data['menge'])
        except (ValueError, TypeError):
            pass
    if 'stueckpreis_geschaetzt' in data:
        try:
            proposal.stueckpreis_geschaetzt = float(data['stueckpreis_geschaetzt'])
        except (ValueError, TypeError):
            pass
    db.session.commit()
    return jsonify(proposal.to_dict())


@api_bp.route('/proposals/<path:nr>', methods=['DELETE'])
@beschaffer_required
def delete_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    upload_folder = current_app.config['UPLOAD_FOLDER']
    for att in proposal.attachments:
        filepath = os.path.join(upload_folder, att.filepath or '')
        if att.filepath and os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(proposal)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/proposals/<path:nr>/approve', methods=['POST'])
@beschaffer_required
def approve_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    proposal.status = 'approved'
    proposal.approved_by_id = current_user.id
    proposal.approved_at = datetime.utcnow()
    db.session.commit()
    return jsonify(proposal.to_dict())


@api_bp.route('/proposals/<path:nr>/reject', methods=['POST'])
@beschaffer_required
def reject_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    proposal.status = 'rejected'
    db.session.commit()
    return jsonify(proposal.to_dict())


# ── ALTERNATIVES ───────────────────────────────────────────────────────────────

@api_bp.route('/proposals/<path:nr>/alternatives', methods=['GET'])
@login_required
def list_alternatives(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    return jsonify([a.to_dict() for a in proposal.alternatives])


@api_bp.route('/proposals/<path:nr>/alternatives', methods=['POST'])
@beschaffer_required
def create_alternative(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    data = request.get_json() or {}
    hersteller = (data.get('hersteller') or '').strip()
    modell = (data.get('modell') or '').strip()
    if not hersteller or not modell:
        return jsonify({'error': 'Hersteller und Modell erforderlich'}), 400
    alt = Alternative(
        proposal_nr=nr,
        hersteller=hersteller,
        modell=modell,
        notiz=data.get('notiz') or '',
    )
    db.session.add(alt)
    db.session.commit()
    return jsonify(alt.to_dict()), 201


@api_bp.route('/proposals/<path:nr>/alternatives/<int:alt_id>', methods=['DELETE'])
@beschaffer_required
def delete_alternative(nr, alt_id):
    alt = Alternative.query.filter_by(id=alt_id, proposal_nr=nr).first_or_404()
    db.session.delete(alt)
    db.session.commit()
    return jsonify({'ok': True})


# ── QUOTES ─────────────────────────────────────────────────────────────────────

@api_bp.route('/proposals/<path:nr>/quotes', methods=['GET'])
@login_required
def list_quotes(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    return jsonify([q.to_dict() for q in proposal.quotes])


@api_bp.route('/proposals/<path:nr>/quotes', methods=['POST'])
@beschaffer_required
def create_quote(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    # Accepts multipart/form-data (for optional PDF) or JSON
    if request.content_type and 'multipart/form-data' in request.content_type:
        form = request.form
    else:
        form = request.get_json() or {}

    try:
        preis_stueck = float(form.get('preis_stueck') or 0)
    except (ValueError, TypeError):
        return jsonify({'error': 'Ungültiger Preis'}), 400
    if preis_stueck <= 0:
        return jsonify({'error': 'Preis muss größer als 0 sein'}), 400

    supplier_id = form.get('supplier_id')
    alternative_id = form.get('alternative_id') or None

    try:
        supplier_id_int = int(supplier_id) if supplier_id else None
    except (ValueError, TypeError):
        return jsonify({'error': 'Ungültige Lieferanten-ID'}), 400

    try:
        alternative_id_int = int(alternative_id) if alternative_id else None
    except (ValueError, TypeError):
        return jsonify({'error': 'Ungültige Alternativ-ID'}), 400

    if supplier_id_int:
        if not db.session.get(Supplier, supplier_id_int):
            return jsonify({'error': 'Lieferant nicht gefunden'}), 404

    if alternative_id_int:
        if not Alternative.query.filter_by(id=alternative_id_int, proposal_nr=nr).first():
            return jsonify({'error': 'Alternative nicht gefunden oder gehört nicht zu diesem Vorschlag'}), 404

    quote = Quote(
        proposal_nr=nr,
        supplier_id=supplier_id_int,
        alternative_id=alternative_id_int,
        preis_stueck=preis_stueck,
        lieferzeit=(form.get('lieferzeit') or '').strip(),
        notizen=form.get('notizen') or '',
        erstellt_von_id=current_user.id,
    )

    # Handle optional PDF upload
    if request.content_type and 'multipart/form-data' in request.content_type:
        pdf = request.files.get('pdf')
        if pdf and pdf.filename and pdf.content_type == 'application/pdf':
            filename = secure_filename(pdf.filename)
            safe_name = f'quote_{nr.replace("/", "-")}_{filename}'
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], safe_name)
            pdf.save(filepath)
            quote.filename = filename
            quote.filepath = safe_name

    db.session.add(quote)
    db.session.commit()
    return jsonify(quote.to_dict()), 201


@api_bp.route('/proposals/<path:nr>/quotes/<int:quote_id>', methods=['PUT'])
@beschaffer_required
def update_quote(nr, quote_id):
    quote = Quote.query.filter_by(id=quote_id, proposal_nr=nr).first_or_404()
    data = request.get_json() or {}
    if 'preis_stueck' in data:
        try:
            quote.preis_stueck = float(data['preis_stueck'])
        except (ValueError, TypeError):
            return jsonify({'error': 'Ungültiger Preis'}), 400
    if 'lieferzeit' in data:
        quote.lieferzeit = (data['lieferzeit'] or '').strip()
    if 'notizen' in data:
        quote.notizen = data['notizen'] or ''
    db.session.commit()
    return jsonify(quote.to_dict())


@api_bp.route('/proposals/<path:nr>/quotes/<int:quote_id>', methods=['DELETE'])
@beschaffer_required
def delete_quote(nr, quote_id):
    quote = Quote.query.filter_by(id=quote_id, proposal_nr=nr).first_or_404()
    # Delete associated PDF file if any
    if quote.filepath:
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], quote.filepath)
        if os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(quote)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/quotes/<int:quote_id>/email', methods=['GET'])
@login_required
def view_quote_email(quote_id):
    quote = db.session.get(Quote, quote_id)
    if not quote or not quote.eml_path:
        abort(404)
    path = os.path.join(current_app.config['UPLOAD_FOLDER'], quote.eml_path)
    if not os.path.exists(path):
        abort(404)
    with open(path, 'rb') as fh:
        raw = fh.read()
    msg = parse_email(raw)
    download_url = url_for('api.download_quote_email', quote_id=quote_id)
    resp = make_response(render_email_page(msg, download_url))
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    # Skriptausführung auf der Seite unterbinden; der Mail-Body läuft zusätzlich
    # im sandbox-iframe ohne allow-scripts.
    resp.headers['Content-Security-Policy'] = "script-src 'none'"
    return resp


@api_bp.route('/quotes/<int:quote_id>/email.eml', methods=['GET'])
@login_required
def download_quote_email(quote_id):
    quote = db.session.get(Quote, quote_id)
    if not quote or not quote.eml_path:
        abort(404)
    return send_from_directory(
        current_app.config['UPLOAD_FOLDER'], quote.eml_path,
        as_attachment=True, download_name='angebot.eml',
    )


# ── UPLOADS ───────────────────────────────────────────────────────────────────

@api_bp.route('/uploads/<path:filename>', methods=['GET'])
@login_required
def get_upload(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


# ── SUPPLIERS ──────────────────────────────────────────────────────────────────

@api_bp.route('/suppliers', methods=['GET'])
@login_required
def list_suppliers():
    suppliers = Supplier.query.order_by(Supplier.created_at).all()
    return jsonify([s.to_dict() for s in suppliers])


@api_bp.route('/suppliers', methods=['POST'])
@login_required
def create_supplier():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not name or not email:
        return jsonify({'error': 'Name und E-Mail erforderlich'}), 400
    supplier = Supplier(
        name=name,
        ansprechpartner=(data.get('ansprechpartner') or '').strip(),
        tel=(data.get('tel') or '').strip(),
        email=email,
        is_test=bool(data.get('test', False)),
    )
    db.session.add(supplier)
    db.session.commit()
    return jsonify(supplier.to_dict()), 201


@api_bp.route('/suppliers/<int:sid>', methods=['PUT'])
@login_required
def update_supplier(sid):
    supplier = Supplier.query.get_or_404(sid)
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not name or not email:
        return jsonify({'error': 'Name und E-Mail erforderlich'}), 400
    supplier.name = name
    supplier.ansprechpartner = (data.get('ansprechpartner') or '').strip()
    supplier.tel = (data.get('tel') or '').strip()
    supplier.email = email
    db.session.commit()
    return jsonify(supplier.to_dict())


@api_bp.route('/suppliers/<int:sid>', methods=['DELETE'])
@login_required
def delete_supplier(sid):
    supplier = Supplier.query.get_or_404(sid)
    db.session.delete(supplier)
    db.session.commit()
    return jsonify({'ok': True})


# ── USERS ──────────────────────────────────────────────────────────────────────

@api_bp.route('/users', methods=['GET'])
@admin_required
def list_users():
    users = User.query.order_by(User.created_at).all()
    return jsonify([u.to_dict() for u in users])


def _email_taken(email, exclude_id=None):
    email = (email or '').strip().lower()
    if not email:
        return False
    q = User.query.filter(db.func.lower(User.email) == email)
    if exclude_id is not None:
        q = q.filter(User.id != exclude_id)
    return q.first() is not None


@api_bp.route('/users', methods=['POST'])
@admin_required
def create_user():
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or len(password) < 6:
        return jsonify({'error': 'Benutzername und Passwort (min. 6 Zeichen) erforderlich'}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Benutzername bereits vergeben'}), 400
    role = (data.get('role') or 'betrachter').strip()
    if role not in ('betrachter', 'beschaffer', 'admin'):
        return jsonify({'error': 'Ungültige Rolle'}), 400
    email = (data.get('email') or '').strip()
    if not email or '@' not in email:
        return jsonify({'error': 'Gültige E-Mail-Adresse erforderlich'}), 400
    if _email_taken(email):
        return jsonify({'error': 'E-Mail bereits vergeben'}), 400
    if 'notify' in data:
        notify = bool(data.get('notify'))
    else:
        notify = (role == 'beschaffer')
    user = User(username=username, role=role, email=email, notify=notify)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return jsonify(user.to_dict()), 201


@api_bp.route('/users/<int:uid>', methods=['DELETE'])
@admin_required
def delete_user(uid):
    if uid == current_user.id:
        return jsonify({'error': 'Eigenen Account kann man nicht löschen'}), 400
    user = User.query.get_or_404(uid)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/users/<int:uid>', methods=['PUT'])
@admin_required
def update_user(uid):
    user = User.query.get_or_404(uid)
    data = request.get_json() or {}
    if 'role' in data:
        role = (data.get('role') or '').strip()
        if role not in ('betrachter', 'beschaffer', 'admin'):
            return jsonify({'error': 'Ungültige Rolle'}), 400
        if uid == current_user.id and role != 'admin':
            return jsonify({'error': 'Eigene Admin-Rechte können nicht entzogen werden'}), 400
        user.role = role
    if 'email' in data:
        new_email = (data.get('email') or '').strip()
        if new_email and _email_taken(new_email, exclude_id=user.id):
            return jsonify({'error': 'E-Mail bereits vergeben'}), 400
        user.email = new_email
    if 'notify' in data:
        user.notify = bool(data.get('notify'))
    db.session.commit()
    return jsonify(user.to_dict())


# ── SETTINGS ──────────────────────────────────────────────────────────────────

_SMTP_KEYS = ('smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_tls')
_TEMPLATE_KEYS = ('email_subject', 'email_body')
_IMAP_KEYS = ('imap_host', 'imap_port', 'imap_user', 'imap_password', 'imap_folder', 'imap_ssl', 'imap_enabled', 'imap_interval')
_FORM_KEYS = ('form_heading', 'form_intro')


@api_bp.route('/settings', methods=['GET'])
@admin_required
def get_settings():
    result = {}
    for key in _SMTP_KEYS + _TEMPLATE_KEYS + _IMAP_KEYS + _FORM_KEYS:
        val = Settings.get(key)
        if key in ('smtp_password', 'imap_password'):
            result[key] = _MASK if val else ''
        else:
            result[key] = val
    result['_default_subject'] = DEFAULT_EMAIL_SUBJECT
    result['_default_body'] = DEFAULT_EMAIL_BODY
    result['_default_form_heading'] = DEFAULT_FORM_HEADING
    result['_default_form_intro'] = DEFAULT_FORM_INTRO
    result['vergabe_tiers'] = get_vergabe_tiers()
    return jsonify(result)


def _validate_vergabe_tiers(tiers):
    """None zurückgeben wenn gültig, sonst eine Fehlermeldung (String)."""
    if not isinstance(tiers, list) or len(tiers) != len(_VERGABE_KEYS):
        return 'Vergabe-Konfiguration muss vier Stufen enthalten.'
    if [t.get('key') for t in tiers] != _VERGABE_KEYS:
        return 'Unerwartete Stufen-Reihenfolge.'
    prev = 0
    for t in tiers[:-1]:
        if not (t.get('label') or '').strip():
            return 'Bezeichnung darf nicht leer sein.'
        try:
            m = int(t['max'])
        except (KeyError, ValueError, TypeError):
            return 'Grenzwert muss eine Zahl sein.'
        if m <= prev:
            return 'Grenzwerte müssen größer als 0 und streng aufsteigend sein.'
        prev = m
    if not (tiers[-1].get('label') or '').strip():
        return 'Bezeichnung darf nicht leer sein.'
    return None


@api_bp.route('/settings', methods=['PUT'])
@admin_required
def update_settings():
    data = request.get_json() or {}

    # Vergabe-Schwellen separat behandeln (strukturierte JSON-Konfiguration)
    if 'vergabe_tiers' in data:
        tv = data['vergabe_tiers']
        if tv is None or tv == '' or tv == []:
            Settings.set('vergabe_tiers', '')  # Reset → Defaults
        else:
            err = _validate_vergabe_tiers(tv)
            if err:
                return jsonify({'error': err}), 400
            cleaned = [{
                'key': t['key'],
                'label': t['label'].strip(),
                'max': (None if t['key'] == 'europa' else int(t['max'])),
                'info': (t.get('info') or '').strip(),
            } for t in tv]
            Settings.set('vergabe_tiers', json.dumps(cleaned, ensure_ascii=False))

    allowed = set(_SMTP_KEYS + _TEMPLATE_KEYS + _IMAP_KEYS + _FORM_KEYS)
    for key, value in data.items():
        if key not in allowed:
            continue
        if key in ('smtp_password', 'imap_password') and (not value or value == _MASK):
            continue
        Settings.set(key, value or '')
    db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/imap/poll', methods=['POST'])
@beschaffer_required
def manual_imap_poll():
    from .imap_worker import poll_imap_once
    from flask import current_app
    result = poll_imap_once(current_app._get_current_object())
    if 'error' in result:
        return jsonify(result), 502
    return jsonify(result)


# ── EMAIL ──────────────────────────────────────────────────────────────────────

@api_bp.route('/email/send', methods=['POST'])
@beschaffer_required
def send_emails():
    data = request.get_json() or {}
    recipient_ids = data.get('recipientIds', [])
    if not recipient_ids:
        return jsonify({'error': 'Keine Empfänger ausgewählt'}), 400

    bezeichnung = data.get('bezeichnung') or '—'
    hersteller = data.get('hersteller') or '—'
    modell = data.get('modell') or '—'
    kategorie = data.get('kategorie') or '—'
    kosten = data.get('kosten')
    nr = data.get('nr') or '(noch nicht vergeben)'

    kosten_str = ''
    if kosten:
        try:
            kosten_str = f'Geschätzte Kosten: {float(kosten):,.0f} € netto\n'.replace(',', '.')
        except (ValueError, TypeError):
            pass

    subject_tpl = Settings.get('email_subject') or DEFAULT_EMAIL_SUBJECT
    body_tpl = Settings.get('email_body') or DEFAULT_EMAIL_BODY

    suppliers = Supplier.query.filter(Supplier.id.in_(recipient_ids)).all()
    errors = []
    sent = 0

    for supplier in suppliers:
        try:
            subject = subject_tpl.format(
                bezeichnung=bezeichnung, hersteller=hersteller,
                modell=modell, kategorie=kategorie, nr=nr,
                lieferant=supplier.name,
                ansprechpartner=supplier.ansprechpartner or supplier.name,
            ) + f' [FF-{nr}]'
            body = body_tpl.format(
                ansprechpartner=supplier.ansprechpartner or supplier.name,
                lieferant=supplier.name,
                bezeichnung=bezeichnung, hersteller=hersteller,
                modell=modell, kategorie=kategorie,
                kosten=kosten_str, nr=nr,
            )
        except KeyError as e:
            errors.append(f'Vorlagen-Fehler: unbekannter Platzhalter {e}')
            continue

        try:
            send_email(
                to_email=supplier.email,
                to_name=supplier.name,
                subject=subject,
                body=body,
            )
            sent += 1
        except Exception as e:
            errors.append(f'{supplier.name}: {e}')

    if errors and sent == 0:
        return jsonify({'error': 'E-Mail-Versand fehlgeschlagen', 'details': errors}), 500
    if errors:
        return jsonify({'sent': sent, 'errors': errors}), 207
    return jsonify({'sent': sent})
