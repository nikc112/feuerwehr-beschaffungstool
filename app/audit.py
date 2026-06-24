"""Zentrales Änderungs-Protokoll (Audit-Log).

`log(action, entity, details)` schreibt einen Protokoll-Eintrag – best effort,
d. h. ein Fehler beim Protokollieren bricht die eigentliche Aktion nie ab.
"""
import logging

logger = logging.getLogger(__name__)


def log(action, entity='', details=''):
    """Einen Audit-Eintrag schreiben. Nutzt current_user/IP, wenn vorhanden."""
    try:
        from flask import request, has_request_context
        from flask_login import current_user
        from . import db
        from .models import AuditLog

        user_id, username, ip = None, '(System)', ''
        if has_request_context():
            ip = (request.remote_addr or '')
            try:
                if current_user and current_user.is_authenticated:
                    user_id = current_user.id
                    username = current_user.username
                else:
                    username = '(öffentlich)'
            except Exception:
                username = '(unbekannt)'

        entry = AuditLog(user_id=user_id, username=username, action=action,
                         entity=(entity or '')[:160], details=(details or '')[:4000], ip=ip[:64])
        db.session.add(entry)
        db.session.commit()
    except Exception as e:  # Protokollierung darf die Aktion nie stören
        logger.error('Audit-Log fehlgeschlagen (%s): %s', action, e)
        try:
            from . import db
            db.session.rollback()
        except Exception:
            pass


def diff(old, new, labels):
    """Lesbare Änderungsbeschreibung aus zwei Wert-Dicts.

    labels: {feldname: 'Anzeigename'}. Nur tatsächlich geänderte Felder.
    Gibt z. B. zurück: "Kosten: 3.400 → 5.170; Beschaffungsart: — → Direktauftrag".
    """
    parts = []
    for key, label in labels.items():
        ov, nv = old.get(key), new.get(key)
        if str(ov or '') != str(nv or ''):
            parts.append(f"{label}: {ov if (ov not in (None, '')) else '—'} → "
                         f"{nv if (nv not in (None, '')) else '—'}")
    return '; '.join(parts)
