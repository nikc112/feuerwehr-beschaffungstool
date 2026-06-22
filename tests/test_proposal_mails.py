def _create_proposal(client, monkeypatch, email='melder@example.com'):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    data = {'bezeichnung': 'Wärmebildkamera', 'einreicher_name': 'Max Melder'}
    if email:
        data['einreicher_email'] = email
    r = client.post('/api/proposals', data=data, content_type='multipart/form-data')
    return r.get_json()['nr']


def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr('app.notifications.notify_submitter',
                        lambda app_, email, name, subject, body: sent.append(
                            {'email': email, 'subject': subject, 'body': body}))
    return sent


def test_approve_sends_mail_to_submitter(app, auth_client, monkeypatch):
    sent = _capture_mail(monkeypatch)
    nr = _create_proposal(auth_client, monkeypatch, 'melder@example.com')
    r = auth_client.post('/api/proposals/' + nr + '/approve')
    assert r.status_code == 200
    assert r.get_json()['notified'] is True
    assert len(sent) == 1
    assert sent[0]['email'] == 'melder@example.com'
    assert nr in sent[0]['subject']
    assert 'genehmigt' in sent[0]['body'].lower()


def test_approve_without_email_skips_mail(app, auth_client, monkeypatch):
    sent = _capture_mail(monkeypatch)
    nr = _create_proposal(auth_client, monkeypatch, email=None)
    r = auth_client.post('/api/proposals/' + nr + '/approve')
    assert r.status_code == 200
    assert r.get_json()['notified'] is False
    assert r.get_json()['status'] == 'approved'
    assert sent == []


def test_reject_includes_reason(app, auth_client, monkeypatch):
    sent = _capture_mail(monkeypatch)
    nr = _create_proposal(auth_client, monkeypatch, 'melder@example.com')
    r = auth_client.post('/api/proposals/' + nr + '/reject', json={'grund': 'Kein Budget vorhanden'})
    assert r.status_code == 200
    assert r.get_json()['status'] == 'rejected'
    assert 'Kein Budget vorhanden' in sent[0]['body']
    assert 'Begründung' in sent[0]['body']


def test_reject_without_reason_has_no_begruendung(app, auth_client, monkeypatch):
    sent = _capture_mail(monkeypatch)
    nr = _create_proposal(auth_client, monkeypatch, 'melder@example.com')
    r = auth_client.post('/api/proposals/' + nr + '/reject', json={'grund': ''})
    assert r.status_code == 200
    assert 'Begründung' not in sent[0]['body']


def test_decision_mail_settings_present_and_persist(app, auth_client):
    d = auth_client.get('/api/settings').get_json()
    for k in ('approve_subject', 'approve_body', 'reject_subject', 'reject_body'):
        assert ('_default_' + k) in d and d['_default_' + k]
    auth_client.put('/api/settings', json={'approve_subject': 'Genehmigt: {nr}'})
    assert auth_client.get('/api/settings').get_json()['approve_subject'] == 'Genehmigt: {nr}'


def test_custom_template_is_used(app, auth_client, monkeypatch):
    sent = _capture_mail(monkeypatch)
    auth_client.put('/api/settings', json={'approve_subject': 'OK {nr} – {bezeichnung}'})
    nr = _create_proposal(auth_client, monkeypatch, 'melder@example.com')
    auth_client.post('/api/proposals/' + nr + '/approve')
    assert sent[0]['subject'] == 'OK ' + nr + ' – Wärmebildkamera'


def test_reject_stores_reason_and_reopen_clears(app, auth_client, monkeypatch):
    monkeypatch.setattr('app.notifications.notify_submitter', lambda *a, **k: None)
    nr = _create_proposal(auth_client, monkeypatch, 'melder@example.com')
    auth_client.post('/api/proposals/' + nr + '/reject', json={'grund': 'Kein Budget'})
    rej = auth_client.get('/api/proposals?status=rejected').get_json()
    assert rej[0]['rejection_reason'] == 'Kein Budget'
    # Wieder öffnen → zurück auf pending, Grund entfernt
    r = auth_client.post('/api/proposals/' + nr + '/reopen')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'pending'
    assert r.get_json()['rejection_reason'] == ''
    assert auth_client.get('/api/proposals?status=rejected').get_json() == []
    assert any(x['nr'] == nr for x in auth_client.get('/api/proposals?status=pending').get_json())


def test_betrachter_cannot_reject(app, client, monkeypatch):
    nr = _create_proposal(client, monkeypatch, 'melder@example.com')
    from app import db
    from app.models import User
    with app.app_context():
        u = User(username='bet', role='betrachter')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
    client.post('/api/auth/login', json={'username': 'bet', 'password': 'secret123'})
    assert client.post('/api/proposals/' + nr + '/reject', json={'grund': 'x'}).status_code == 403
