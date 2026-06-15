from app import db
from app.models import User
import app.notifications as notif


def _mk(app, username, role, email='', notify=False):
    with app.app_context():
        u = User(username=username, role=role, email=email, notify=notify)
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()


def test_recipients_filters_by_role_notify_email(app):
    _mk(app, 'besch1', 'beschaffer', 'b1@ff.de', True)     # rein
    _mk(app, 'admin1', 'admin', 'a1@ff.de', True)          # rein
    _mk(app, 'admin2', 'admin', 'a2@ff.de', False)         # raus: notify aus
    _mk(app, 'besch2', 'beschaffer', '', True)             # raus: keine Mail
    _mk(app, 'betr1', 'betrachter', 'v1@ff.de', True)      # raus: Rolle
    with app.app_context():
        emails = sorted(e for e, _ in notif.recipients())
    assert emails == ['a1@ff.de', 'b1@ff.de']


def test_send_notifications_calls_send_for_each(app, monkeypatch):
    _mk(app, 'b', 'beschaffer', 'b@ff.de', True)
    _mk(app, 'a', 'admin', 'a@ff.de', True)
    sent = []
    monkeypatch.setattr('app.email_service.send_email',
                        lambda to, name, subj, body: sent.append(to))
    notif.send_notifications(app, 'Betr', 'Text')
    assert sorted(sent) == ['a@ff.de', 'b@ff.de']


def test_send_notifications_swallows_errors(app, monkeypatch):
    _mk(app, 'b', 'beschaffer', 'b@ff.de', True)
    def boom(*a, **k):
        raise RuntimeError('SMTP down')
    monkeypatch.setattr('app.email_service.send_email', boom)
    notif.send_notifications(app, 'Betr', 'Text')  # darf nicht werfen


def test_notify_new_proposal_dispatches(app, monkeypatch):
    calls = []
    monkeypatch.setattr(notif, 'notify_async', lambda a, s, b: calls.append((s, b)))
    notif.notify_new_proposal(app, '01/2026', 'Pumpe', 'Max')
    assert len(calls) == 1
    assert '01/2026' in calls[0][0]
    assert 'Pumpe' in calls[0][1]


def test_notify_new_quote_dispatches(app, monkeypatch):
    calls = []
    monkeypatch.setattr(notif, 'notify_async', lambda a, s, b: calls.append((s, b)))
    notif.notify_new_quote(app, '02/2026', 'lieferant@firma.de')
    assert len(calls) == 1
    assert '02/2026' in calls[0][0]
    assert 'lieferant@firma.de' in calls[0][1]
