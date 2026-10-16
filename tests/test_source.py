from io import BytesIO
import stat
import tarfile
import zipfile
import pytest

from serviter.models import UserError
from serviter.source import create_site_archive


def fake_zip(files: dict[str, bytes], *, symlink: str = '') -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for path, body in files.items():
            item = zipfile.ZipInfo('KIBHR-repo-SHA/' + path)
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777 if path == symlink else stat.S_IFREG | 0o644) << 16
            z.writestr(item, body)
    return buf.getvalue()


def test_static_only_and_no_private_files():
    source = fake_zip({'website/index.html': b'<!doctype html><html></html>',
                       'website/assets/site.js': b'alert(1)',
                       'website/README.md': b'secrets here',
                       'website/deploy/preflight.sh': b'do not upload',
                       'server/config.php': b'SUPERSECRET',
                       'website/.env': b'PRIVATE'})
    archive, meta = create_site_archive(source, 'website')
    with tarfile.open(fileobj=BytesIO(archive), mode='r:gz') as z:
        names = z.getnames()
        assert sorted(names) == ['assets/site.js', 'index.html']
        assert b'SUPERSECRET' not in archive
        assert meta['files'] == 2


def test_root_repo_supported():
    a, _ = create_site_archive(fake_zip({'index.html': b'<html>hi</html>'}), '')
    with tarfile.open(fileobj=BytesIO(a), mode='r:gz') as z:
        assert z.getnames() == ['index.html']


def test_requires_index():
    with pytest.raises(UserError, match='index.html'):
        create_site_archive(fake_zip({'website/site.css': b'p{}'}), 'website')


def test_reject_symlink():
    with pytest.raises(UserError, match='Символические'):
        create_site_archive(fake_zip({'website/index.html': b'../../etc/passwd'},
                                         symlink='website/index.html'), 'website')


def test_reject_traversal():
    with pytest.raises(UserError):
        create_site_archive(fake_zip({'website/../index.html': b'<html></html>'}), 'website')


def test_reject_duplicate_paths():
    source = BytesIO()
    with zipfile.ZipFile(source, 'w') as z:
        z.writestr('repo-x/website/index.html', 'one')
        z.writestr('repo-x/website/index.html', 'two')
    with pytest.raises(UserError, match='Повторяющиеся'):
        create_site_archive(source.getvalue(), 'website')