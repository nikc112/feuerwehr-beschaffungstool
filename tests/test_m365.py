import json


def _set(app, **kv):
    from app import db
    from app.models import Settings
    with app.app_context():
        for k, v in kv.items():
            Settings.set(k, v)
        db.session.commit()


def _configure_m365(app):
    _set(app, mail_provider='m365', m365_tenant='contoso.onmicrosoft.com',
         m365_client_id='cid', m365_client_secret='secret', m365_mailbox='beschaffung@x.de')


# ── Token-Caching ──────────────────────────────────────────────────────────────
def test_get_token_caches(monkeypatch):
    from app import graph_mail
    calls = []
    monkeypatch.setattr(graph_mail, '_token_cache', {})
    monkeypatch.setattr(graph_mail, '_token_request',
                        lambda tenant, payload: calls.append(1) or {'access_token': 'TOK', 'expires_in': 3600})
    s = {'tenant': 't-unique', 'client_id': 'c', 'client_secret': 'x', 'mailbox': 'm@x.de'}
    assert graph_mail._get_token(s) == 'TOK'
    assert graph_mail._get_token(s) == 'TOK'
    assert len(calls) == 1   # zweiter Aufruf aus dem Cache


# ── Settings: Keys vorhanden, Secret maskiert, persistent ──────────────────────
def test_m365_settings_persist_and_mask(app, auth_client):
    auth_client.put('/api/settings', json={
        'mail_provider': 'm365', 'm365_tenant': 'contoso', 'm365_client_id': 'cid',
        'm365_client_secret': 'topsecret', 'm365_mailbox': 'b@x.de'})
    d = auth_client.get('/api/settings').get_json()
    assert d['mail_provider'] == 'm365'
    assert d['m365_tenant'] == 'contoso'
    assert d['m365_mailbox'] == 'b@x.de'
    assert d['m365_client_secret'] == '●●●●●●'        # maskiert, nicht im Klartext
    # leeres Secret beim erneuten Speichern lässt den Wert unverändert
    auth_client.put('/api/settings', json={'m365_client_secret': ''})
    from app.models import Settings
    with app.app_context():
        assert Settings.get('m365_client_secret') == 'topsecret'


# ── Versand-Weiche: provider=m365 -> Graph statt SMTP ──────────────────────────
def test_send_email_dispatches_to_graph(app, monkeypatch):
    _set(app, mail_provider='m365')
    captured = {}
    from app import graph_mail
    monkeypatch.setattr(graph_mail, 'send_mail',
                        lambda to_email, to_name, subject, body: captured.update(
                            to=to_email, subj=subject))
    from app.email_service import send_email
    with app.app_context():
        send_email('a@b.de', 'Name', 'Betreff', 'Text')
    assert captured == {'to': 'a@b.de', 'subj': 'Betreff'}


# ── Abruf-Weiche: provider=m365 -> Graph-Poll ──────────────────────────────────
def test_poll_dispatches_to_graph(app, monkeypatch):
    _set(app, mail_provider='m365')
    from app import graph_mail
    monkeypatch.setattr(graph_mail, 'poll_graph_once', lambda a: {'imported': 0, 'via': 'graph'})
    from app.imap_worker import poll_mail_once
    assert poll_mail_once(app).get('via') == 'graph'


# ── send_mail baut korrekten Graph-Payload ─────────────────────────────────────
def test_graph_send_mail_payload(app, monkeypatch):
    _configure_m365(app)
    from app import graph_mail
    seen = {}
    monkeypatch.setattr(graph_mail, '_get_token', lambda s: 'TOK')
    monkeypatch.setattr(graph_mail, '_api',
                        lambda method, path, token, body=None, raw=False: seen.update(
                            method=method, path=path, body=body))
    with app.app_context():
        graph_mail.send_mail('kunde@x.de', 'Kunde', 'Angebotsanfrage', 'Bitte um Angebot')
    assert seen['method'] == 'POST' and seen['path'].endswith('/sendMail')
    assert seen['body']['message']['subject'] == 'Angebotsanfrage'
    assert seen['body']['message']['toRecipients'][0]['emailAddress']['address'] == 'kunde@x.de'


# ── poll_graph_once verarbeitet eine getaggte Mail -> Angebot ──────────────────
def test_graph_poll_imports_quote(app, monkeypatch):
    _configure_m365(app)
    from app import db, graph_mail
    from app.models import Proposal, Quote
    with app.app_context():
        db.session.add(Proposal(nr='01/2026', bezeichnung='Kamera', status='approved'))
        db.session.commit()

    monkeypatch.setattr(graph_mail, '_get_token', lambda s: 'TOK')

    def fake_api(method, path, token, body=None, raw=False):
        if method == 'GET' and 'mailFolders/inbox/messages' in path:
            return {'value': [{
                'id': 'M1', 'subject': 'Re: Angebot [FF-01/2026]',
                'from': {'emailAddress': {'address': 'lieferant@x.de'}},
                'hasAttachments': False,
                'body': {'contentType': 'text', 'content': 'Unser Angebot anbei'},
            }]}
        if raw:                      # /$value -> Roh-MIME
            return b'From: lieferant@x.de\r\nSubject: x\r\n\r\nbody'
        return {}                    # PATCH isRead etc.

    monkeypatch.setattr(graph_mail, '_api', fake_api)
    res = graph_mail.poll_graph_once(app)
    assert res['imported'] == 1
    with app.app_context():
        q = Quote.query.filter_by(proposal_nr='01/2026').first()
        assert q is not None and q.source == 'email' and q.sender_email == 'lieferant@x.de'
