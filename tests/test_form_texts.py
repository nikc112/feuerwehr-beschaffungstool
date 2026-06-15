def test_index_shows_default_form_texts(app, client):
    body = client.get('/').get_data(as_text=True)
    assert 'Beschaffungsvorschlag einreichen' in body
    assert 'Die Bearbeitung und Angebotseinholung erfolgt durch die Wehrführung.' in body
    # Wehrführer-Block wurde entfernt
    assert 'Heiko Bolt' not in body
    assert 'Olaf Semmelmann' not in body


def test_get_settings_returns_form_defaults(app, auth_client):
    d = auth_client.get('/api/settings').get_json()
    assert d['_default_form_heading'] == 'Beschaffungsvorschlag einreichen'
    assert 'Wehrführung' in d['_default_form_intro']
    # noch nichts gespeichert -> leer
    assert d['form_heading'] in ('', None)
    assert d['form_intro'] in ('', None)


def test_put_form_texts_persists_and_renders(app, client, auth_client):
    r = auth_client.put('/api/settings', json={
        'form_heading': 'Neue Überschrift',
        'form_intro': 'Zeile 1\nZeile 2',
    })
    assert r.status_code == 200
    d = auth_client.get('/api/settings').get_json()
    assert d['form_heading'] == 'Neue Überschrift'
    assert d['form_intro'] == 'Zeile 1\nZeile 2'
    body = client.get('/').get_data(as_text=True)
    assert 'Neue Überschrift' in body
    # Default-Einleitungstext wird durch den eigenen ersetzt
    assert 'Die Bearbeitung und Angebotseinholung erfolgt durch die Wehrführung.' not in body


def test_reset_form_texts_falls_back_to_default(app, client, auth_client):
    auth_client.put('/api/settings', json={'form_heading': 'X', 'form_intro': 'Y'})
    assert 'X' in client.get('/').get_data(as_text=True)
    r = auth_client.put('/api/settings', json={'form_heading': '', 'form_intro': ''})
    assert r.status_code == 200
    body = client.get('/').get_data(as_text=True)
    assert 'Beschaffungsvorschlag einreichen' in body
