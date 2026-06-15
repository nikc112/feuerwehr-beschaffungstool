def test_proposal_without_hersteller_modell_accepted(app, client, monkeypatch):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    # Nur Bezeichnung gesetzt – Hersteller/Modell sind kein Pflichtfeld
    r = client.post('/api/proposals', data={'bezeichnung': 'Wärmebildkamera'},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    assert r.get_json()['nr']


def test_proposal_without_bezeichnung_rejected(app, client, monkeypatch):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    r = client.post('/api/proposals', data={'hersteller': 'FLIR', 'modell': 'K33'},
                    content_type='multipart/form-data')
    assert r.status_code == 400
