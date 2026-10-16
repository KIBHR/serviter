from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import PurePosixPath
import re
from urllib.parse import urlparse

_DOMAIN = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
_REF = re.compile(r'^[a-zA-Z0-9._/-]{1,180}$')
_REPO = re.compile(r'^https://github\.com/([a-zA-Z0-9-]{1,39})/([a-zA-Z0-9._-]{1,100})(?:\.git)?/?$')


class UserError(ValueError):
    """Safe-to-display validation or deployment failure."""


def validate_domain(domain: str) -> str:
    val = domain.strip().lower().rstrip('.')
    if not _DOMAIN.fullmatch(val) or val.startswith(('api.', 'admin.', 'mail.')):
        raise UserError('Укажите обычный домен сайта (не api.*), например adt-studio.asia.')
    return val


def validate_host(host: str) -> str:
    value = host.strip().strip('[]')
    if not value or any(c in value for c in '/\\\n\r\t :@') and ':' not in value:
        raise UserError('Укажите IP-адрес или DNS-имя SSH-сервера.')
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        if ':' in value or not _DOMAIN.fullmatch(value.lower()):
            raise UserError('Некорректный адрес SSH-сервера.')
        return value.lower()


def validate_repo(url: str) -> tuple[str, str]:
    match = _REPO.fullmatch(url.strip())
    if not match:
        raise UserError('Поддерживается ссылка вида https://github.com/OWNER/REPO.git')
    return match.group(1), match.group(2).removesuffix('.git')


def validate_ref(ref: str) -> str:
    v = ref.strip()
    if not _REF.fullmatch(v) or v.startswith(('-', '/', '.')) or '..' in v or '//' in v or v.endswith(('.', '/', '.lock')):
        raise UserError('Некорректная ветка GitHub.')
    return v


def validate_source(path: str) -> str:
    value = path.strip().strip('/')
    if value in ('', '.'):  # repository root
        return ''
    p = PurePosixPath(value)
    if '\\' in value or any(x in ('.', '..') or x.startswith('.') for x in p.parts):
        raise UserError('Укажите относительную папку сайта без ../ и скрытых сегментов.')
    if any(not re.fullmatch(r'[a-zA-Z0-9_.-]+', x) for x in p.parts):
        raise UserError('Некорректный путь к папке сайта.')
    return str(p)


def validate_api(url: str, site_domain: str) -> str:
    text = url.strip()
    if not text:
        return ''
    u = urlparse(text)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or u.port not in (None, 443):
        raise UserError('Адрес защищаемого API должен быть HTTPS URL без логина, пароля и нестандартного порта.')
    # Limit to related subdomains; arbitrary URLs could be an SSRF primitive on the VPS.
    if u.hostname != 'api.' + site_domain or u.path != '/v1/health' or u.query:
        raise UserError('Для защиты разрешён только https://api.' + site_domain + '/v1/health')
    return text


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    user: str
    password: str
    key_file: str
    sudo_password: str
    repo_url: str
    branch: str
    github_token: str
    source_folder: str
    domain: str
    tls_email: str
    api_url: str

    def validated(self) -> 'Config':
        validate_host(self.host)
        validate_domain(self.domain)
        validate_repo(self.repo_url)
        validate_ref(self.branch)
        validate_source(self.source_folder)
        validate_api(self.api_url, self.domain)
        if not 1 <= self.port <= 65535 or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', self.user):
            raise UserError('Некорректный SSH-порт или пользователь.')
        if not self.password and not self.key_file:
            raise UserError('Укажите SSH-пароль или путь к SSH-ключу.')
        if self.tls_email and (not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', self.tls_email) or len(self.tls_email) > 254):
            raise UserError('Некорректный адрес для сертификата Let’s Encrypt.')
        return self

    def manifest(self) -> dict:
        return {
            'domain': validate_domain(self.domain),
            'target_host': validate_host(self.host),
            'api_url': validate_api(self.api_url, self.domain),
            'tls_email': self.tls_email.strip(),
        }