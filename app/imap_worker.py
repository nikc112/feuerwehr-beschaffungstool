import base64
import imaplib
import email
import email.policy
import email.utils
import os
import re
import threading
import time
import logging
import uuid

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r'\[FF-([^\]]+)\]', re.IGNORECASE)
_worker_thread = None


def _decode_part_payload(part):
    """Return decoded bytes for a MIME part, with explicit base64 fallback.

    get_payload(decode=True) with the legacy compat32 policy can return
    raw base64 text as bytes when the Content-Transfer-Encoding header
    isn't matched exactly. This function detects that case and retries.
    """
    payload = part.get_payload(decode=True)
    if not payload:
        return payload

    # If the first bytes look like base64 text (not binary PDF/ZIP/etc.)
    # the decode step silently failed — try explicit base64 decode.
    try:
        header = payload[:8]
        header.decode('ascii')            # raises UnicodeDecodeError for true binary
        candidate = payload.decode('ascii').strip()
        if re.match(r'^[A-Za-z0-9+/=\r\n]+$', candidate[:256]):
            decoded = base64.b64decode(candidate)
            logger.warning('IMAP: get_payload(decode=True) returned base64 text — fallback applied')
            return decoded
    except (UnicodeDecodeError, Exception):
        pass  # payload is genuine binary, nothing to do

    return payload


def _save_raw_email(upload_dir, raw_email, ts):
    """Roh-Mail (RFC822-Bytes) als email_<ts>_<uid>.eml ablegen, Dateinamen zurückgeben.

    Zufalls-Suffix verhindert, dass sich zwei Mails aus derselben Sekunde
    gegenseitig überschreiben."""
    safe = f'email_{ts}_{uuid.uuid4().hex[:8]}.eml'
    fpath = os.path.join(upload_dir, safe)
    with open(fpath, 'wb') as fh:
        fh.write(raw_email)
    logger.info('IMAP: Roh-Mail gespeichert %s (%d bytes)', safe, len(raw_email))
    return safe


def poll_mail_once(app):
    """Abruf je nach Verbindungsart: Microsoft 365 (Graph) oder IMAP."""
    with app.app_context():
        from .models import Settings
        provider = Settings.get('mail_provider') or 'smtp'
    if provider == 'm365':
        from .graph_mail import poll_graph_once
        return poll_graph_once(app)
    return poll_imap_once(app)


def poll_imap_once(app):
    with app.app_context():
        from . import db
        from .models import Settings, Proposal, Quote, Supplier

        if Settings.get('imap_enabled') != 'true':
            return {'skipped': True, 'reason': 'IMAP deaktiviert'}

        host = Settings.get('imap_host') or ''
        port_s = Settings.get('imap_port') or '993'
        user = Settings.get('imap_user') or ''
        password = Settings.get('imap_password') or ''
        folder = Settings.get('imap_folder') or 'INBOX'
        use_ssl = (Settings.get('imap_ssl') or 'true').lower() == 'true'

        if not host or not user or not password:
            return {'error': 'IMAP nicht vollständig konfiguriert (Host, Benutzer, Passwort)'}

        try:
            port = int(port_s)
        except ValueError:
            port = 993

        try:
            M = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
            M.login(user, password)
            M.select(folder)

            _, data = M.search(None, 'UNSEEN')
            msg_ids = data[0].split() if data[0] else []
            checked = len(msg_ids)
            imported = 0
            skipped = 0

            upload_dir = app.config.get('UPLOAD_FOLDER', '/app/data/uploads')
            os.makedirs(upload_dir, exist_ok=True)

            for msg_id in msg_ids:
                try:
                    _, raw = M.fetch(msg_id, '(RFC822)')
                    raw_email = raw[0][1]
                    msg = email.message_from_bytes(raw_email, policy=email.policy.default)

                    subject = msg.get('Subject', '')
                    sender_full = msg.get('From', '')
                    sender_email = email.utils.parseaddr(sender_full)[1].lower()

                    match = _TAG_RE.search(subject)
                    if not match:
                        M.store(msg_id, '+FLAGS', '\\Seen')
                        skipped += 1
                        continue

                    proposal_nr = match.group(1)
                    proposal = Proposal.query.filter_by(nr=proposal_nr).first()
                    if not proposal:
                        M.store(msg_id, '+FLAGS', '\\Seen')
                        skipped += 1
                        continue

                    # Extract PDF attachments and plain-text body
                    pdfs = []
                    body_parts = []
                    for part in msg.walk():
                        ct = part.get_content_type()
                        cd = str(part.get('Content-Disposition') or '')
                        if ct in ('application/pdf',) or (
                            ct == 'application/octet-stream' and 'attachment' in cd
                            and (part.get_filename() or '').lower().endswith('.pdf')
                        ):
                            payload = _decode_part_payload(part)
                            orig_name = part.get_filename() or 'angebot.pdf'
                            if payload:
                                ts = int(time.time())
                                safe_name = re.sub(r'[^\w.\-]', '_', orig_name)
                                # Endung auf .pdf erzwingen (Schutz vor aktiver
                                # Endung wie .html/.svg beim spaeteren Ausliefern)
                                if not safe_name.lower().endswith('.pdf'):
                                    safe_name += '.pdf'
                                # Zufalls-Suffix gegen Überschreiben bei gleichem Namen/Sekunde
                                safe = f'email_{ts}_{uuid.uuid4().hex[:8]}_{safe_name}'
                                fpath = os.path.join(upload_dir, safe)
                                with open(fpath, 'wb') as fh:
                                    fh.write(payload)
                                logger.info('IMAP: PDF gespeichert %s (%d bytes)', safe, len(payload))
                                pdfs.append((safe, orig_name))
                        elif ct == 'text/plain' and 'attachment' not in cd:
                            raw_payload = part.get_payload(decode=True)
                            if raw_payload:
                                body_parts.append(raw_payload.decode('utf-8', errors='replace'))

                    eml_name = _save_raw_email(upload_dir, raw_email, int(time.time()))
                    body_text = '\n'.join(body_parts).strip()[:600] or 'Automatisch aus E-Mail empfangen'

                    # Match supplier by sender email
                    supplier = Supplier.query.filter(
                        db.func.lower(Supplier.email) == sender_email
                    ).first()

                    def _make_quote(fname=None, orig=None):
                        return Quote(
                            proposal_nr=proposal_nr,
                            supplier_id=supplier.id if supplier else None,
                            preis_stueck=0.0,
                            notizen=body_text,
                            filepath=fname,
                            filename=orig,
                            source='email',
                            sender_email=sender_email,
                            eml_path=eml_name,
                        )

                    if pdfs:
                        for fname, orig in pdfs:
                            db.session.add(_make_quote(fname, orig))
                    else:
                        db.session.add(_make_quote())

                    db.session.commit()
                    M.store(msg_id, '+FLAGS', '\\Seen')
                    imported += 1
                    try:
                        from .notifications import notify_new_quote
                        notify_new_quote(app, proposal_nr, sender_email)
                    except Exception as e:
                        logger.error('IMAP: Benachrichtigung fehlgeschlagen: %s', e)

                except Exception as e:
                    logger.error('IMAP message processing error: %s', e)
                    db.session.rollback()

            M.close()
            M.logout()
            return {'checked': checked, 'imported': imported, 'skipped': skipped}

        except Exception as e:
            logger.error('IMAP connection error: %s', e)
            return {'error': str(e)}


def start_imap_worker(app):
    global _worker_thread

    def loop():
        while True:
            try:
                with app.app_context():
                    from .models import Settings
                    interval_min = int(Settings.get('imap_interval') or '15')
            except Exception:
                interval_min = 15
            interval_sec = max(1, interval_min) * 60

            try:
                poll_mail_once(app)
            except Exception as e:
                logger.error('Mail worker loop error: %s', e)

            time.sleep(interval_sec)

    _worker_thread = threading.Thread(target=loop, name='imap-worker', daemon=True)
    _worker_thread.start()
