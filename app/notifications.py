"""Benachrichtigungs-Mails an Beschaffer/Admins (best effort, nicht-blockierend)."""
import logging
import threading

logger = logging.getLogger(__name__)


def recipients():
    """(email, username)-Liste der zu benachrichtigenden Beschaffer/Admins."""
    from .models import User
    users = User.query.filter(
        User.role.in_(('beschaffer', 'admin')),
        User.notify.is_(True),
    ).all()
    return [(u.email, u.username) for u in users if (u.email or '').strip()]


def send_notifications(app, subject, body):
    """Synchron an alle Empfänger senden; Fehler je Empfänger nur loggen."""
    from .email_service import send_email
    with app.app_context():
        for email, name in recipients():
            try:
                send_email(email, name, subject, body)
            except Exception as e:
                logger.error('Benachrichtigung an %s fehlgeschlagen: %s', email, e)


def notify_async(app, subject, body):
    """Versand in einem Daemon-Thread anstoßen (blockiert den Aufrufer nicht)."""
    threading.Thread(
        target=send_notifications, args=(app, subject, body),
        name='notify', daemon=True,
    ).start()


def notify_new_proposal(app, nr, bezeichnung, einreicher):
    subject = f'Neuer Vorschlag im Eingangskorb: {nr}'
    body = ('Im Eingangskorb ist ein neuer Beschaffungsvorschlag eingegangen.\n\n'
            f'Nr.: {nr}\n'
            f'Bezeichnung: {bezeichnung}\n'
            f'Eingereicht von: {einreicher or "—"}\n\n'
            'Bitte im Tool prüfen.')
    notify_async(app, subject, body)


def notify_new_quote(app, nr, sender_email):
    subject = f'Neues Angebot eingegangen: {nr}'
    body = (f'Zu Vorschlag {nr} ist ein neues Angebot per E-Mail eingegangen.\n\n'
            f'Absender: {sender_email or "—"}\n\n'
            'Bitte im Angebotsvergleich prüfen.')
    notify_async(app, subject, body)
