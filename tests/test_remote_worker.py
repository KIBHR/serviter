import io
import tarfile
from pathlib import Path
import pytest
from serviter import remote_worker as rw


def test_virtual_host_separation():
    site = rw.http_vhost('adt-studio.asia', tls=True)
    assert 'ServerName adt-studio.asia' in site
    assert 'ServerName api.adt-studio.asia' not in site
    assert '/var/www/serviter-sites/adt-studio.asia/current' in site
    assert '/etc/letsencrypt/live/adt-studio.asia/fullchain.pem' in site
    assert 'RedirectMatch 301' in site
    assert 'AllowOverride None' in site


def test_remote_reject_api_vhost():
    with pytest.raises(rw.PublishError):
        rw.safe_domain('api.adt-studio.asia')


def tarball(name, data):
    mem = io.BytesIO()
    with tarfile.open(fileobj=mem, mode='w:gz') as t:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    mem.seek(0)
    return mem


def test_extract_rejects_traversal(tmp_path):
    bad = tmp_path / 'site.tar.gz'
    bad.write_bytes(tarball('../index.html', b'<html>').getvalue())
    with pytest.raises(rw.PublishError):
        rw.extract(bad, tmp_path / 'output')
    assert not (tmp_path / 'index.html').exists()


def test_extract_valid_static(tmp_path):
    data = tarball('index.html', b'<html>ok</html>')
    path = tmp_path / 'site.tar.gz'
    path.write_bytes(data.getvalue())
    out = tmp_path / 'output'
    out.mkdir()
    assert rw.extract(path, out) == 1
    assert (out / 'index.html').read_text() == '<html>ok</html>'