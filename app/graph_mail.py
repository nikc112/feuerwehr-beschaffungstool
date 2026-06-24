"""Microsoft-365-Anbindung über Microsoft Graph (App-only / Client-Credentials).

Sendet (Mail.Send) und ruft ab (Mail.Read) über die Graph-REST-API – ohne
SMTP/IMAP, ohne interaktiven Login. Wird nur genutzt, wenn die Verbindungsart
auf "m365" steht; ansonsten bleibt alles bei IMAP/SMTP.

Einrichtung in Azure/Entra ID (einmalig):
  1. App-Registrierung -> Tenant-ID, Client-ID, Client-Secret.
  2. Application-Berechtigungen (Graph): Mail.Send + Mail.Read, Admin-Zustimmung.
  3. Empfohlen: Application Access Policy, die den Zugriff auf das eine Postfach
     beschränkt.
"""
import os
import re
import json
import time
import base64
import logging
import urllib.parse
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'
_TAG_RE = re.compile(r'\[FF-([^\]]+)\]', re.IGNORECASE)
_token_cache = {}  # (tenant|client_id) -> (token, expiry_epoch)


def _settings():
    from .models import Settings
    return {
        'tenant': (Settings.get('m365_tenant') or '').strip(),
        'client_id': (Settings.get('m365_client_id') or '').strip(),
        'client_secret': Settings.get('m365_client_secret') or '',
        'mailbox': (Settings.get('m365_mailbox') or '').strip(),
    }


def is_configured(s=None):
    s = s or _settings()
    return all([s['tenant'], s['client_id'], s['client_secret'], s['mailbox']])


# ── HTTP-Hilfen (in Tests einzeln mockbar) ──────────────────────────────────────
def _token_request(tenant, payload):
    url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method='POST')
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def _api(method, path, token, body=None, raw=False):
    url = GRAPH + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        raise RuntimeError(f'Graph {method} {path} -> {e.code}: {detail}')
    if raw:
        return content
    return json.loads(content.decode('utf-8')) if content else {}


def _get_token(s):
    now = time.time()
    ck = s['tenant'] + '|' + s['client_id']
    tok, exp = _token_cache.get(ck, (None, 0))
    if tok and now < exp - 60:
        return tok
    body = _token_request(s['tenant'], {
        'client_id': s['client_id'],
        'client_secret': s['client_secret'],
        'scope': 'https://graph.microsoft.com/.default',
        'grant_type': 'client_credentials',
    })
    if 'access_token' not in body:
        raise RuntimeError('Kein access_token erhalten: ' + json.dumps(body)[:200])
    tok = body['access_token']
    _token_cache[ck] = (tok, now + int(body.get('expires_in', 3600)))
    return tok


# ── Senden ──────────────────────────────────────────────────────────────────────
def send_mail(to_email, to_name, subject, body):
    s = _settings()
    if not is_configured(s):
        raise RuntimeError('Microsoft 365 nicht vollständig konfiguriert')
    token = _get_token(s)
    mbox = urllib.parse.quote(s['mailbox'])
    payload = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'Text', 'content': body},
            'toRecipients': [{'emailAddress': {'address': to_email, 'name': to_name or to_email}}],
        },
        'saveToSentItems': True,
    }
    _api('POST', f'/users/{mbox}/sendMail', token, payload)


# ── Abrufen ───────────────────────────────────────────────────────────────────
def _html_to_text(html):
    return re.sub(r'<[^>]+>', ' ', html or '')


def poll_graph_once(app):
    with app.app_context():
        from . import db
        from .models import Settings, Proposal, Quote, Supplier

        if (Settings.get('mail_provider') or 'smtp') != 'm365':
            return {'skipped': True, 'reason': 'Microsoft 365 nicht aktiv'}
        s = _settings()
        if not is_configured(s):
            return {'error': 'Microsoft 365 nicht vollständig konfiguriert'}

        try:
            token = _get_token(s)
        except Exception as e:
            logger.error('Graph token error: %s', e)
            return {'error': str(e)}

        mbox = urllib.parse.quote(s['mailbox'])
        upload_dir = app.config.get('UPLOAD_FOLDER', '/app/data/uploads')
        os.makedirs(upload_dir, exist_ok=True)

        try:
            resp = _api('GET', f'/users/{mbox}/mailFolders/inbox/messages'
                               '?$filter=isRead eq false&$top=25'
                               '&$select=id,subject,from,hasAttachments,body', token)
        except Exception as e:
            logger.error('Graph fetch error: %s', e)
            return {'error': str(e)}

        msgs = resp.get('value', [])
        checked, imported, skipped = len(msgs), 0, 0

        for m in msgs:
            mid = m.get('id')
            try:
                subject = m.get('subject') or ''
                sender = ((m.get('from') or {}).get('emailAddress') or {}).get('address', '').lower()
                match = _TAG_RE.search(subject)
                if not match:
                    _api('PATCH', f'/users/{mbox}/messages/{mid}', token, {'isRead': True})
                    skipped += 1
                    continue
                nr = match.group(1)
                proposal = Proposal.query.filter_by(nr=nr).first()
                if not proposal:
                    _api('PATCH', f'/users/{mbox}/messages/{mid}', token, {'isRead': True})
                    skipped += 1
                    continue

                body = m.get('body') or {}
                text = body.get('content', '') or ''
                if (body.get('contentType') or '').lower() == 'html':
                    text = _html_to_text(text)
                body_text = text.strip()[:600] or 'Automatisch aus E-Mail empfangen'

                # Roh-MIME als .eml für die Original-Mail-Ansicht
                eml_name = None
                try:
                    raw = _api('GET', f'/users/{mbox}/messages/{mid}/$value', token, raw=True)
                    eml_name = f'email_{int(time.time())}.eml'
                    with open(os.path.join(upload_dir, eml_name), 'wb') as fh:
                        fh.write(raw)
                except Exception as e:
                    logger.warning('Graph .eml-Download fehlgeschlagen: %s', e)

                # PDF-Anhänge
                pdfs = []
                if m.get('hasAttachments'):
                    atts = _api('GET', f'/users/{mbox}/messages/{mid}/attachments', token).get('value', [])
                    for a in atts:
                        is_file = str(a.get('@odata.type', '')).endswith('fileAttachment')
                        name = a.get('name') or 'angebot.pdf'
                        if is_file and (a.get('contentType') == 'application/pdf'
                                        or name.lower().endswith('.pdf')):
                            try:
                                content = base64.b64decode(a.get('contentBytes', ''))
                            except Exception:
                                continue
                            safe = re.sub(r'[^\w.\-]', '_', name)
                            if not safe.lower().endswith('.pdf'):
                                safe += '.pdf'
                            fname = f'email_{int(time.time())}_{safe}'
                            with open(os.path.join(upload_dir, fname), 'wb') as fh:
                                fh.write(content)
                            pdfs.append((fname, name))

                supplier = Supplier.query.filter(db.func.lower(Supplier.email) == sender).first()

                def _mk(fn=None, orig=None):
                    return Quote(proposal_nr=nr, supplier_id=supplier.id if supplier else None,
                                 preis_stueck=0.0, notizen=body_text, filepath=fn, filename=orig,
                                 source='email', sender_email=sender, eml_path=eml_name)

                if pdfs:
                    for fn, orig in pdfs:
                        db.session.add(_mk(fn, orig))
                else:
                    db.session.add(_mk())
                db.session.commit()

                _api('PATCH', f'/users/{mbox}/messages/{mid}', token, {'isRead': True})
                imported += 1
                try:
                    from .notifications import notify_new_quote
                    notify_new_quote(app, nr, sender)
                except Exception as e:
                    logger.error('Graph Benachrichtigung fehlgeschlagen: %s', e)
            except Exception as e:
                logger.error('Graph message processing error (%s): %s', mid, e)
                db.session.rollback()

        return {'checked': checked, 'imported': imported, 'skipped': skipped}
