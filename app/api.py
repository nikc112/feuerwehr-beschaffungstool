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
                     DEFAULT_FORM_HEADING, DEFAULT_FORM_INTRO, DEFAULT_BRAND)
from . import db
from .email_service import send_email
from .notifications import notify_new_proposal
from .email_view import parse_email, render_email_page
from .ratelimit import rate_limit
from .audit import log as audit_log, diff as audit_diff

api_bp = Blueprint('api', __name__)

_MASK = '●●●●●●'

# {organisation} wird beim Erzeugen der Defaults durch den eingestellten Namen
# ersetzt (per .replace, damit die übrigen {…}-Platzhalter erhalten bleiben).
DEFAULT_EMAIL_SUBJECT = 'Angebotsanfrage: {bezeichnung} – {organisation}'
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
    '{organisation}\n'
    '{adresse}'
)


def _default_email_subject():
    from .models import get_branding
    return DEFAULT_EMAIL_SUBJECT.replace('{organisation}', get_branding()['name'])


def _default_email_body():
    from .models import get_branding
    b = get_branding()
    return (DEFAULT_EMAIL_BODY
            .replace('{organisation}', b['name'])
            .replace('{adresse}', b['address']))


# ── Entscheidungs-Mails an den Einreicher (Genehmigung / Ablehnung) ─────────────
DEFAULT_APPROVE_SUBJECT = 'Ihr Beschaffungsvorschlag {nr} wurde genehmigt'
DEFAULT_APPROVE_BODY = (
    'Guten Tag {einreicher},\n\n'
    'Ihr Beschaffungsvorschlag wurde genehmigt und in die Investitionsliste übernommen.\n\n'
    'Nr.: {nr}\n'
    'Bezeichnung: {bezeichnung}\n\n'
    'Mit freundlichen Grüßen\n'
    '{organisation}'
)
DEFAULT_REJECT_SUBJECT = 'Ihr Beschaffungsvorschlag {nr} wurde abgelehnt'
DEFAULT_REJECT_BODY = (
    'Guten Tag {einreicher},\n\n'
    'Ihr Beschaffungsvorschlag wurde leider abgelehnt.\n\n'
    'Nr.: {nr}\n'
    'Bezeichnung: {bezeichnung}\n'
    '{grund}'
    '\nMit freundlichen Grüßen\n'
    '{organisation}'
)


def _default_proposal_mail(key):
    """Default-Text mit eingesetztem Organisationsnamen (für Settings-Anzeige)."""
    from .models import get_branding
    raw = {
        'approve_subject': DEFAULT_APPROVE_SUBJECT, 'approve_body': DEFAULT_APPROVE_BODY,
        'reject_subject': DEFAULT_REJECT_SUBJECT, 'reject_body': DEFAULT_REJECT_BODY,
    }[key]
    return raw.replace('{organisation}', get_branding()['name'])


def _send_proposal_decision_mail(proposal, kind, grund=''):
    """Genehmigungs-/Ablehnungs-Mail an den Einreicher (best effort, nur wenn E-Mail vorhanden)."""
    from .models import get_branding
    from .notifications import notify_submitter
    if not (proposal.einreicher_email or '').strip():
        return False
    subj_key, body_key = (kind + '_subject'), (kind + '_body')
    subject_tpl = Settings.get(subj_key) or _default_proposal_mail(subj_key)
    body_tpl = Settings.get(body_key) or _default_proposal_mail(body_key)
    grund_str = f'Begründung: {grund.strip()}\n' if grund and grund.strip() else ''
    fields = dict(
        einreicher=proposal.einreicher_name or '',
        nr=proposal.nr, bezeichnung=proposal.bezeichnung or '',
        organisation=get_branding()['name'], grund=grund_str,
    )
    try:
        subject = subject_tpl.format(**fields)
        body = body_tpl.format(**fields)
    except (KeyError, IndexError, ValueError):
        subject = _default_proposal_mail(subj_key).format(**fields)
        body = _default_proposal_mail(body_key).format(**fields)
    notify_submitter(current_app._get_current_object(), proposal.einreicher_email,
                     proposal.einreicher_name or '', subject, body)
    return True


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


def _is_pdf_upload(f):
    """Nur echte PDFs akzeptieren: Content-Type UND .pdf-Endung (gegen XSS via
    gefälschtem Content-Type + aktiver Dateiendung wie .html/.svg)."""
    return bool(f and f.filename
                and f.content_type == 'application/pdf'
                and f.filename.lower().endswith('.pdf'))


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
@rate_limit(20, 600, 'submit')   # max. 20 Einreichungen / 10 min / IP
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
    einreicher_email = (form.get('einreicher_email') or '').strip()
    if '@' not in einreicher_email or '.' not in einreicher_email.split('@')[-1]:
        return jsonify({'error': 'Gültige E-Mail-Adresse des Einreichers erforderlich'}), 400

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
        einreicher_email=einreicher_email,
        einreicher_tel=(form.get('einreicher_tel') or '').strip(),
    )
    db.session.add(proposal)
    db.session.flush()

    upload_folder = current_app.config['UPLOAD_FOLDER']
    for f in files:
        if _is_pdf_upload(f):
            filename = secure_filename(f.filename)
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'
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
    audit_log('proposal.create', f'Vorschlag {nr}',
              f'„{proposal.bezeichnung}" eingereicht von {proposal.einreicher_name or "—"}')
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
@beschaffer_required
def update_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    data = request.get_json() or {}
    _before = {
        'hersteller': proposal.hersteller, 'modell': proposal.modell,
        'kosten': proposal.kosten, 'beschaffungsart': proposal.beschaffungsart,
        'prioritaet': proposal.prioritaet, 'menge': proposal.menge,
        'stueckpreis_geschaetzt': proposal.stueckpreis_geschaetzt,
        'geplanter_zeitpunkt': proposal.geplanter_zeitpunkt,
        'ablauf': proposal.ablauf, 'notizen': proposal.notizen,
    }
    if 'kosten' in data:
        try:
            proposal.kosten = float(data['kosten'])
        except (ValueError, TypeError):
            pass
    if 'hersteller' in data:
        proposal.hersteller = (data['hersteller'] or '').strip()
    if 'modell' in data:
        proposal.modell = (data['modell'] or '').strip()
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
    # Geplanter Beschaffungszeitpunkt – nur Beschaffer/Admin dürfen ihn setzen
    if 'geplanter_zeitpunkt' in data and current_user.role in ('beschaffer', 'admin'):
        proposal.geplanter_zeitpunkt = (data['geplanter_zeitpunkt'] or '').strip()
    db.session.commit()
    _after = {
        'hersteller': proposal.hersteller, 'modell': proposal.modell,
        'kosten': proposal.kosten, 'beschaffungsart': proposal.beschaffungsart,
        'prioritaet': proposal.prioritaet, 'menge': proposal.menge,
        'stueckpreis_geschaetzt': proposal.stueckpreis_geschaetzt,
        'geplanter_zeitpunkt': proposal.geplanter_zeitpunkt,
        'ablauf': proposal.ablauf, 'notizen': proposal.notizen,
    }
    changes = audit_diff(_before, _after, {
        'hersteller': 'Hersteller', 'modell': 'Modell', 'kosten': 'Kosten',
        'beschaffungsart': 'Beschaffungsart', 'prioritaet': 'Priorität',
        'menge': 'Menge', 'stueckpreis_geschaetzt': 'Stückpreis',
        'geplanter_zeitpunkt': 'Geplanter Zeitpunkt', 'ablauf': 'Ablauf', 'notizen': 'Notizen',
    })
    if changes:
        audit_log('proposal.update', f'Vorschlag {nr}', changes)
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
    bez = proposal.bezeichnung
    db.session.delete(proposal)
    db.session.commit()
    audit_log('proposal.delete', f'Vorschlag {nr}', f'„{bez}" gelöscht')
    return jsonify({'ok': True})


@api_bp.route('/proposals/<path:nr>/approve', methods=['POST'])
@beschaffer_required
def approve_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    proposal.status = 'approved'
    proposal.rejection_reason = ''
    proposal.approved_by_id = current_user.id
    proposal.approved_at = datetime.utcnow()
    db.session.commit()
    audit_log('proposal.approve', f'Vorschlag {nr}', f'„{proposal.bezeichnung}" genehmigt')
    notified = _send_proposal_decision_mail(proposal, 'approve')
    result = proposal.to_dict()
    result['notified'] = notified
    return jsonify(result)


@api_bp.route('/proposals/<path:nr>/reject', methods=['POST'])
@beschaffer_required
def reject_proposal(nr):
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    grund = (request.get_json(silent=True) or {}).get('grund', '') or ''
    proposal.status = 'rejected'
    proposal.rejection_reason = grund.strip()
    db.session.commit()
    audit_log('proposal.reject', f'Vorschlag {nr}',
              f'„{proposal.bezeichnung}" abgelehnt' + (f' – Grund: {grund.strip()}' if grund.strip() else ''))
    notified = _send_proposal_decision_mail(proposal, 'reject', grund)
    result = proposal.to_dict()
    result['notified'] = notified
    return jsonify(result)


@api_bp.route('/proposals/<path:nr>/reopen', methods=['POST'])
@beschaffer_required
def reopen_proposal(nr):
    """Abgelehnten Vorschlag zurück auf 'pending' setzen (Grund entfernen)."""
    proposal = Proposal.query.filter_by(nr=nr).first_or_404()
    proposal.status = 'pending'
    proposal.rejection_reason = ''
    db.session.commit()
    audit_log('proposal.reopen', f'Vorschlag {nr}', f'„{proposal.bezeichnung}" wieder geöffnet')
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
    audit_log('alternative.create', f'Vorschlag {nr}', f'Alternative hinzugefügt: {hersteller} {modell}')
    return jsonify(alt.to_dict()), 201


@api_bp.route('/proposals/<path:nr>/alternatives/<int:alt_id>', methods=['DELETE'])
@beschaffer_required
def delete_alternative(nr, alt_id):
    alt = Alternative.query.filter_by(id=alt_id, proposal_nr=nr).first_or_404()
    label = f'{alt.hersteller} {alt.modell}'
    db.session.delete(alt)
    db.session.commit()
    audit_log('alternative.delete', f'Vorschlag {nr}', f'Alternative gelöscht: {label}')
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
        if _is_pdf_upload(pdf):
            filename = secure_filename(pdf.filename)
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'
            safe_name = f'quote_{nr.replace("/", "-")}_{filename}'
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], safe_name)
            pdf.save(filepath)
            quote.filename = filename
            quote.filepath = safe_name

    db.session.add(quote)
    db.session.commit()
    _sup = db.session.get(Supplier, supplier_id_int) if supplier_id_int else None
    audit_log('quote.create', f'Vorschlag {nr}',
              f'Angebot erfasst: {_sup.name if _sup else "—"} – {preis_stueck:.2f} €/St.')
    return jsonify(quote.to_dict()), 201


@api_bp.route('/proposals/<path:nr>/quotes/<int:quote_id>', methods=['PUT'])
@beschaffer_required
def update_quote(nr, quote_id):
    quote = Quote.query.filter_by(id=quote_id, proposal_nr=nr).first_or_404()
    data = request.get_json() or {}
    _old_preis = quote.preis_stueck
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
    if 'preis_stueck' in data and _old_preis != quote.preis_stueck:
        audit_log('quote.update', f'Vorschlag {nr}',
                  f'Angebotspreis: {_old_preis:.2f} → {quote.preis_stueck:.2f} €/St.')
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
    _sup = quote.supplier.name if quote.supplier else (quote.sender_email or '—')
    db.session.delete(quote)
    db.session.commit()
    audit_log('quote.delete', f'Vorschlag {nr}', f'Angebot gelöscht: {_sup}')
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
    # Betrachter dürfen nur Dateien sehen, die zu GENEHMIGTEN Vorschlägen gehören.
    if current_user.role not in ('beschaffer', 'admin'):
        att = Attachment.query.filter_by(filepath=filename).first()
        quote = Quote.query.filter(
            (Quote.filepath == filename) | (Quote.eml_path == filename)
        ).first()
        nr = att.proposal_nr if att else (quote.proposal_nr if quote else None)
        if not nr:
            abort(404)
        p = Proposal.query.filter_by(nr=nr).first()
        if not p or p.status != 'approved':
            abort(403)
    resp = make_response(send_from_directory(current_app.config['UPLOAD_FOLDER'], filename))
    # Kein MIME-Sniffing; verhindert, dass eine Datei als aktiver Inhalt
    # (z. B. HTML) interpretiert wird, falls doch eine unerwartete Endung vorliegt.
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


# ── SUPPLIERS ──────────────────────────────────────────────────────────────────

@api_bp.route('/suppliers', methods=['GET'])
@login_required
def list_suppliers():
    suppliers = Supplier.query.order_by(Supplier.created_at).all()
    return jsonify([s.to_dict() for s in suppliers])


@api_bp.route('/suppliers', methods=['POST'])
@beschaffer_required
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
    audit_log('supplier.create', f'Lieferant {name}', f'E-Mail: {email}')
    return jsonify(supplier.to_dict()), 201


@api_bp.route('/suppliers/<int:sid>', methods=['PUT'])
@beschaffer_required
def update_supplier(sid):
    supplier = Supplier.query.get_or_404(sid)
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    email = (data.get('email') or '').strip()
    if not name or not email:
        return jsonify({'error': 'Name und E-Mail erforderlich'}), 400
    _sb = {'name': supplier.name, 'ansprechpartner': supplier.ansprechpartner,
           'tel': supplier.tel, 'email': supplier.email}
    supplier.name = name
    supplier.ansprechpartner = (data.get('ansprechpartner') or '').strip()
    supplier.tel = (data.get('tel') or '').strip()
    supplier.email = email
    db.session.commit()
    changes = audit_diff(_sb, {'name': name, 'ansprechpartner': supplier.ansprechpartner,
                               'tel': supplier.tel, 'email': email},
                         {'name': 'Name', 'ansprechpartner': 'Ansprechpartner',
                          'tel': 'Telefon', 'email': 'E-Mail'})
    if changes:
        audit_log('supplier.update', f'Lieferant {name}', changes)
    return jsonify(supplier.to_dict())


@api_bp.route('/suppliers/<int:sid>', methods=['DELETE'])
@beschaffer_required
def delete_supplier(sid):
    supplier = Supplier.query.get_or_404(sid)
    sname = supplier.name
    db.session.delete(supplier)
    db.session.commit()
    audit_log('supplier.delete', f'Lieferant {sname}', 'Lieferant gelöscht')
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
    audit_log('user.create', f'Benutzer {username}', f'Rolle: {role}, E-Mail: {email}')
    return jsonify(user.to_dict()), 201


@api_bp.route('/users/<int:uid>', methods=['DELETE'])
@admin_required
def delete_user(uid):
    if uid == current_user.id:
        return jsonify({'error': 'Eigenen Account kann man nicht löschen'}), 400
    user = User.query.get_or_404(uid)
    uname = user.username
    db.session.delete(user)
    db.session.commit()
    audit_log('user.delete', f'Benutzer {uname}', 'Benutzer gelöscht')
    return jsonify({'ok': True})


@api_bp.route('/users/<int:uid>', methods=['PUT'])
@admin_required
def update_user(uid):
    user = User.query.get_or_404(uid)
    data = request.get_json() or {}
    _ub = {'username': user.username, 'role': user.role, 'email': user.email,
           'notify': bool(user.notify)}
    _pw_changed = False
    if 'username' in data:
        new_username = (data.get('username') or '').strip()
        if not new_username:
            return jsonify({'error': 'Benutzername darf nicht leer sein'}), 400
        clash = User.query.filter(db.func.lower(User.username) == new_username.lower(),
                                  User.id != user.id).first()
        if clash:
            return jsonify({'error': 'Benutzername bereits vergeben'}), 400
        user.username = new_username
    if data.get('password'):
        if len(data['password']) < 6:
            return jsonify({'error': 'Passwort muss mindestens 6 Zeichen haben'}), 400
        user.set_password(data['password'])
        _pw_changed = True
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
    _ua = {'username': user.username, 'role': user.role, 'email': user.email,
           'notify': bool(user.notify)}
    changes = audit_diff(_ub, _ua, {'username': 'Benutzername', 'role': 'Rolle',
                                    'email': 'E-Mail', 'notify': 'Benachrichtigung'})
    if _pw_changed:
        changes = (changes + '; ' if changes else '') + 'Passwort zurückgesetzt'
    if changes:
        audit_log('user.update', f'Benutzer {user.username}', changes)
    return jsonify(user.to_dict())


# ── AUDIT-LOG ────────────────────────────────────────────────────────────────

@api_bp.route('/audit', methods=['GET'])
@admin_required
def list_audit():
    from .models import AuditLog
    q = (request.args.get('q') or '').strip().lower()
    action = (request.args.get('action') or '').strip()
    try:
        limit = min(max(int(request.args.get('limit') or 300), 1), 1000)
    except (ValueError, TypeError):
        limit = 300
    query = AuditLog.query
    if action:
        query = query.filter(AuditLog.action == action)
    if q:
        like = f'%{q}%'
        query = query.filter(db.or_(
            db.func.lower(AuditLog.username).like(like),
            db.func.lower(AuditLog.entity).like(like),
            db.func.lower(AuditLog.details).like(like),
            db.func.lower(AuditLog.action).like(like),
        ))
    rows = query.order_by(AuditLog.id.desc()).limit(limit).all()
    return jsonify([r.to_dict() for r in rows])


# ── SETTINGS ──────────────────────────────────────────────────────────────────

_SMTP_KEYS = ('smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_tls')
_TEMPLATE_KEYS = ('email_subject', 'email_body')
_IMAP_KEYS = ('imap_host', 'imap_port', 'imap_user', 'imap_password', 'imap_folder', 'imap_ssl', 'imap_enabled', 'imap_interval')
_M365_KEYS = ('mail_provider', 'm365_tenant', 'm365_client_id', 'm365_client_secret', 'm365_mailbox')
_FORM_KEYS = ('form_heading', 'form_intro')
_BRAND_KEYS = ('brand_name', 'brand_subtitle', 'brand_address', 'brand_color_primary',
               'brand_color_accent', 'brand_color_bg')
_PROPOSAL_MAIL_KEYS = ('approve_subject', 'approve_body', 'reject_subject', 'reject_body')


@api_bp.route('/settings', methods=['GET'])
@admin_required
def get_settings():
    result = {}
    for key in (_SMTP_KEYS + _TEMPLATE_KEYS + _IMAP_KEYS + _FORM_KEYS + _BRAND_KEYS
                + _PROPOSAL_MAIL_KEYS + _M365_KEYS):
        val = Settings.get(key)
        if key in ('smtp_password', 'imap_password', 'm365_client_secret'):
            result[key] = _MASK if val else ''
        else:
            result[key] = val
    result['_default_subject'] = _default_email_subject()
    result['_default_body'] = _default_email_body()
    result['_default_form_heading'] = DEFAULT_FORM_HEADING
    result['_default_form_intro'] = DEFAULT_FORM_INTRO
    for k, v in DEFAULT_BRAND.items():
        result['_default_brand_' + k] = v
    for k in _PROPOSAL_MAIL_KEYS:
        result['_default_' + k] = _default_proposal_mail(k)
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

    allowed = set(_SMTP_KEYS + _TEMPLATE_KEYS + _IMAP_KEYS + _FORM_KEYS + _BRAND_KEYS
                  + _PROPOSAL_MAIL_KEYS + _M365_KEYS)
    _changed = []
    for key, value in data.items():
        if key not in allowed:
            continue
        if key in ('smtp_password', 'imap_password', 'm365_client_secret') and (not value or value == _MASK):
            continue
        new_val = value or ''
        if Settings.get(key) != new_val:          # nur echte Änderungen protokollieren
            _changed.append(key)
        Settings.set(key, new_val)
    db.session.commit()
    if 'vergabe_tiers' in data:
        _changed.append('vergabe_tiers')
    if _changed:
        # Nur Schlüsselnamen protokollieren – keine Werte (Geheimnisse!)
        audit_log('settings.update', 'Einstellungen', 'Geändert: ' + ', '.join(sorted(_changed)))
    return jsonify({'ok': True})


# ── BRANDING / LOGO ──────────────────────────────────────────────────────────────

_LOGO_EXTS = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
              'gif': 'image/gif', 'svg': 'image/svg+xml', 'webp': 'image/webp'}


def _branding_dir():
    return os.path.join(os.path.dirname(current_app.config['UPLOAD_FOLDER']), 'branding')


@api_bp.route('/branding/logo', methods=['GET'])
def get_branding_logo():
    """Eigenes Logo aus dem data-Volume; sonst das neutrale Standard-Logo."""
    fname = Settings.get('brand_logo')
    resp = None
    if fname:
        path = os.path.join(_branding_dir(), fname)
        if os.path.isfile(path):
            resp = make_response(send_from_directory(_branding_dir(), fname))
    if resp is None:
        resp = make_response(current_app.send_static_file('default-logo.svg'))
    # Stored-XSS-Schutz: ein hochgeladenes SVG darf beim Direktaufruf kein
    # aktives Skripting im App-Origin ausführen (sandbox + nosniff).
    resp.headers['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@api_bp.route('/branding/logo', methods=['POST'])
@admin_required
def upload_branding_logo():
    f = request.files.get('logo')
    if not f or not f.filename:
        return jsonify({'error': 'Keine Datei hochgeladen'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in _LOGO_EXTS:
        return jsonify({'error': 'Nur PNG, JPG, GIF, SVG oder WEBP erlaubt'}), 400
    bdir = _branding_dir()
    os.makedirs(bdir, exist_ok=True)
    # alte Logo-Datei(en) entfernen
    old = Settings.get('brand_logo')
    if old:
        old_path = os.path.join(bdir, old)
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
    fname = 'logo.' + ext
    f.save(os.path.join(bdir, fname))
    Settings.set('brand_logo', fname)
    db.session.commit()
    return jsonify({'ok': True, 'logo_url': '/api/branding/logo'})


@api_bp.route('/branding/logo', methods=['DELETE'])
@admin_required
def delete_branding_logo():
    fname = Settings.get('brand_logo')
    if fname:
        path = os.path.join(_branding_dir(), fname)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        Settings.set('brand_logo', '')
        db.session.commit()
    return jsonify({'ok': True})


@api_bp.route('/imap/poll', methods=['POST'])
@beschaffer_required
def manual_imap_poll():
    from .imap_worker import poll_mail_once
    from flask import current_app
    result = poll_mail_once(current_app._get_current_object())
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

    subject_tpl = Settings.get('email_subject') or _default_email_subject()
    body_tpl = Settings.get('email_body') or _default_email_body()

    suppliers = Supplier.query.filter(Supplier.id.in_(recipient_ids)).all()
    errors = []
    sent = 0
    _sent_names = []

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
            _sent_names.append(supplier.name)
        except Exception as e:
            errors.append(f'{supplier.name}: {e}')

    if _sent_names:
        audit_log('email.request', f'Vorgang {nr}',
                  f'Angebotsanfrage „{bezeichnung}" gesendet an: ' + ', '.join(_sent_names))

    if errors and sent == 0:
        return jsonify({'error': 'E-Mail-Versand fehlgeschlagen', 'details': errors}), 500
    if errors:
        return jsonify({'sent': sent, 'errors': errors}), 207
    return jsonify({'sent': sent})
