import pytest
from serviter.models import Config, UserError, validate_api, validate_domain, validate_host, validate_ref, validate_repo, validate_source


def example(**overrides):
    base = dict(host='212.116.232.118', port=22, user='ubuntu', password='secret',
                key_file='', sudo_password='', repo_url='https://github.com/KIBHR/cipher-breach.git',
                branch='feature/adt-studio-website', github_token='token', source_folder='website',
                domain='adt-studio.asia', tls_email='owner@example.com',
                api_url='https://api.adt-studio.asia/v1/health')
    base.update(overrides)
    return Config(**base)


def test_valid_config_and_manifest_do_not_include_credentials():
    cfg = example().validated()
    manifest = str(cfg.manifest())
    assert 'secret' not in manifest
    assert 'token' not in manifest
    assert 'owner@example.com' in manifest


@pytest.mark.parametrize('name', ['api.example.com', 'localhost', '../../etc/passwd', 'X!', 'admin.example.com'])
def test_unsafe_domains_rejected(name):
    with pytest.raises(UserError):
        validate_domain(name)


@pytest.mark.parametrize('ref', ['../main', 'main;rm', '/main', 'main//child', '.lock', 'x.lock', ''])
def test_unsafe_ref_rejected(ref):
    with pytest.raises(UserError):
        validate_ref(ref)


@pytest.mark.parametrize('path', ['../src', '/../../etc', '.ssh', 'website/.env', 'website/../foo', 'a\\b'])
def test_unsafe_source_rejected(path):
    with pytest.raises(UserError):
        validate_source(path)


def test_github_repo_validation():
    assert validate_repo('https://github.com/KIBHR/serviter.git') == ('KIBHR', 'serviter')
    assert validate_repo('https://github.com/KIBHR/serviter') == ('KIBHR', 'serviter')
    with pytest.raises(UserError):
        validate_repo('https://github.com:443/KIBHR/serviter')


def test_api_health_whitelist():
    assert validate_api('https://api.adt-studio.asia/v1/health', 'adt-studio.asia')
    for url in ['http://api.adt-studio.asia/v1/health', 'https://example.com/',
                'https://api.adt-studio.asia/v1/me', 'https://api.adt-studio.asia/v1/health?x=1']:
        with pytest.raises(UserError):
            validate_api(url, 'adt-studio.asia')


def test_ipv6_host_supported():
    assert validate_host('2a00:5da0:1:201::911') == '2a00:5da0:1:201::911'


def test_preflight_requires_credentials():
    with pytest.raises(UserError):
        example(password='').validated()