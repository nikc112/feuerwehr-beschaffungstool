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


def test_beschaffer_can_add_hersteller_modell_later(app, auth_client, monkeypatch):
    monkeypatch.setattr('app.api.notify_new_proposal', lambda *a, **k: None)
    # Vorschlag ohne Marke/Modell
    nr = auth_client.post('/api/proposals', data={'bezeichnung': 'Wärmebildkamera'},
                          content_type='multipart/form-data').get_json()['nr']
    # Beschaffer/Admin pflegt Hersteller & Modell nach
    r = auth_client.put('/api/proposals/' + nr, json={'hersteller': 'FLIR', 'modell': 'K33'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['hersteller'] == 'FLIR'
    assert body['modell'] == 'K33'
