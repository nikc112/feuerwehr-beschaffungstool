def test_create_proposal_triggers_notification(app, client, monkeypatch):
    calls = []
    monkeypatch.setattr('app.api.notify_new_proposal',
                        lambda a, nr, bez, einr: calls.append((nr, bez)))
    r = client.post('/api/proposals', data={'bezeichnung': 'Tragkraftspritze'},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    assert len(calls) == 1
    assert calls[0][1] == 'Tragkraftspritze'
