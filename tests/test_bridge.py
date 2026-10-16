"""Security tests for the loopback bridge; no real SSH or DNS connection."""
import http.client
import json
from threading import Thread

import pytest

from serviter import bridge
from serviter.models import UserError


CONFIG = {
    'host': '212.116.232.118', 'port': '22', 'user': 'ubuntu',
    'password': 'SENSITIVE-SSH-ONLY-MEMORY', 'key_file': '',
    'sudo_password': '', 'repo_url': 'https://github.com/KIBHR/cipher-breach.git',
    'branch': 'feature/adt-studio-website', 'github_token': 'SENSITIVE-TOKEN-ONLY-MEMORY',
    'source_folder': 'website', 'domain': 'adt-studio.asia', 'tls_email': '',
    'api_url': 'https://api.adt-studio.asia/v1/health',
}


def test_valid_config_and_fingerprint():
    c = bridge.config_from_dict(CONFIG)
    assert c.user == 'ubuntu'
    assert len(bridge.config_fingerprint(c)) == 64
    with pytest.raises(UserError):
        bridge.config_from_dict({**CONFIG, 'domain': 'api.adt-studio.asia'})
    with pytest.raises(UserError):
        bridge.config_from_dict({**CONFIG, 'port': 'xyz'})


def test_server_only_localhost_and_bearer():
    server = bridge.LocalServer(('127.0.0.1', 0), 'A' * 64)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('GET', '/health')
        result = connection.getresponse()
        assert result.status == 401
        assert b'Unauthorized' in result.read()
        connection.request('GET', '/health', headers={'Authorization': 'Bearer ' + 'A' * 64})
        result = connection.getresponse()
        assert result.status == 200
        assert json.loads(result.read())['ok']
        connection.request('POST', '/command', body=json.dumps({'action': 'publish', 'config': CONFIG}), headers={
            'Authorization': 'Bearer ' + 'A' * 64, 'Content-Type': 'application/json',
            'Origin': 'https://untrusted.example.com',
        })
        result = connection.getresponse()
        assert result.status == 403
        assert b'SENSITIVE' not in result.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_mutation_requires_fresh_inspection(monkeypatch):
    engine = bridge.Engine()
    with pytest.raises(UserError, match='проверить'):
        engine.execute('publish', CONFIG)
    with pytest.raises(UserError, match='проверить'):
        engine.execute('rollback', CONFIG)
    monkeypatch.setattr(bridge.deploy, 'inspect', lambda config, log: {'ok': True, 'api': True})
    monkeypatch.setattr(bridge.deploy, 'publish', lambda config, log: {'ok': True, 'url': 'https://adt-studio.asia/'})
    out = engine.execute('inspect', CONFIG)
    assert out['ok']
    assert engine.execute('publish', CONFIG)['ok']
    with pytest.raises(UserError, match='проверить'):
        engine.execute('publish', CONFIG)
    engine.execute('inspect', CONFIG)
    with pytest.raises(UserError, match='проверить'):
        engine.execute('publish', {**CONFIG, 'branch': 'main'})


def test_pin_requires_probe(monkeypatch):
    engine = bridge.Engine()
    with pytest.raises(UserError, match='SSH'):
        engine.execute('pin', CONFIG, 'SHA256:bogus')
    monkeypatch.setattr(bridge, 'probe_host', lambda config: ({'fingerprint': 'SHA256:TEST', 'type': 'ssh-ed25519', 'trusted': 'unknown'}, 'opaque-key'))
    called = []
    monkeypatch.setattr(bridge, 'pin_host', lambda config, key, expected: called.append((key, expected)))
    engine.execute('probe', CONFIG)
    with pytest.raises(UserError, match='совпадает'):
        engine.execute('pin', CONFIG, 'SHA256:bad')
    engine.execute('pin', CONFIG, 'SHA256:TEST')
    assert called == [('opaque-key', 'SHA256:TEST')]