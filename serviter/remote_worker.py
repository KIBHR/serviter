#!/usr/bin/env python3
"""Restricted remote Ubuntu/Apache publisher run as root, shipped over authenticated SSH.

No shell evaluation of user data; a dedicated vhost and release directory only.
No use of Docker, database clients, Git, game server folders or API configuration.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen


class PublishError(Exception):
    pass


STATIC_EXTS = {'.html', '.htm', '.css', '.js', '.mjs', '.svg', '.png', '.jpg', '.jpeg',
               '.gif', '.webp', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.txt',
               '.xml', '.json', '.webmanifest', '.avif', '.mp4', '.webm', '.wasm'}
DOMAIN_RE = re.compile(r'^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
SITE_ROOT = Path('/var/www/serviter-sites')
APACHE_AVAILABLE = Path('/etc/apache2/sites-available')
APACHE_ENABLED = Path('/etc/apache2/sites-enabled')


def call(args: list[str], *, timeout: int = 25) -> subprocess.CompletedProcess:
    p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=timeout, text=True)
    return p


def must(args: list[str], *, timeout: int = 25, error: str = 'Команда не выполнена'):
    result = call(args, timeout=timeout)
    if result.returncode:
        raise PublishError(error)
    return result


def safe_domain(domain: str) -> str:
    if not isinstance(domain, str) or not DOMAIN_RE.fullmatch(domain) or domain.startswith(('api.', 'admin.', 'mail.')):
        raise PublishError('Недопустимый домен. Поддомены api.* запрещены.')
    return domain


def manifest_checks(manifest: dict):
    domain = safe_domain(manifest['domain'])
    if not isinstance(manifest.get('target_host'), str) or not manifest['target_host']:
        raise PublishError('Не указан хост назначения.')
    health = manifest.get('api_url', '')
    if health and (health != f'https://api.{domain}/v1/health'):
        raise PublishError('Адрес API должен быть https://api.' + domain + '/v1/health')
    email = manifest.get('tls_email', '')
    if not isinstance(email, str) or len(email) > 254 or (email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
        raise PublishError('Некорректный e-mail для TLS.')
    # DNS must really point at the SSH host before certbot makes public changes.
    try:
        ips = {x[4][0] for x in socket.getaddrinfo(domain, 80, type=socket.SOCK_STREAM)}
        target = {x[4][0] for x in socket.getaddrinfo(manifest['target_host'], 22, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise PublishError('Не удалось проверить DNS домена.') from exc
    if not ips.intersection(target):
        raise PublishError('DNS домена не указывает на SSH-сервер. Сначала проверьте A/AAAA записи.')


def os_checks(domain: str):
    try:
        data = Path('/etc/os-release').read_text()
    except OSError as exc:
        raise PublishError('Не удалось определить ОС.') from exc
    if not re.search(r'^ID=ubuntu$', data, re.M):
        raise PublishError('Автоматическая публикация поддерживает только Ubuntu.')
    if os.geteuid() != 0:
        raise PublishError('Нужны права sudo.')
    if not APACHE_AVAILABLE.is_dir() or not APACHE_ENABLED.is_dir():
        raise PublishError('Apache не найден. Настройки других веб-серверов не изменяются.')
    must(['apache2ctl', 'configtest'], error='Проверка конфигурации Apache не прошла.')
    must(['systemctl', 'is-active', '--quiet', 'apache2'], error='Apache не запущен.')
    mods = must(['apache2ctl', '-M'], error='Не удалось определить модули Apache.').stdout
    for required in ('ssl_module', 'alias_module', 'dir_module'):
        if required not in mods:
            raise PublishError('В Apache отсутствует модуль ' + required + '. Никакие модули автоматически не меняем.')
    owned = filename(domain)
    for entry in APACHE_ENABLED.iterdir():
        if not entry.is_file() or entry.name == owned:
            continue
        try:
            body = entry.read_text(errors='replace')
        except OSError:
            raise PublishError('Не удалось прочитать текущий виртуальный хост Apache.')
        # Remove comment-only lines before looking for matches.
        lines = [line.split('#', 1)[0].strip() for line in body.splitlines()]
        if any(re.match(r'^Server(?:Name|Alias)\s+', l, re.I) and domain in l.split()[1:] for l in lines):
            raise PublishError('Для этого домена уже существует чужой виртуальный хост: ' + entry.name)
    cfg = APACHE_AVAILABLE / owned
    if cfg.exists():
        body = cfg.read_text(errors='replace')
        if '# Managed by Serviter; domain=' + domain not in body:
            raise PublishError('Конфигурация домена создана не Serviter. Автозамена запрещена.')
    enabled = APACHE_ENABLED / owned
    if cfg.is_file() and not enabled.is_symlink():
        raise PublishError('Конфигурация Serviter существует, но сайт отключён: нужен ручной осмотр.')
    if enabled.is_symlink() and enabled.resolve() != cfg:
        raise PublishError('Символическая ссылка vhost указывает на чужой файл.')
    if enabled.exists() and not enabled.is_symlink():
        raise PublishError('Имя vhost занято посторонним файлом.')


def filename(domain: str) -> str:
    return 'serviter-' + safe_domain(domain) + '.conf'


def api_status(url: str) -> dict | None:
    if not url:
        return None
    try:
        with urlopen(url, timeout=10) as response:
            if response.status != 200:
                raise PublishError('API вернул неуспешный HTTP статус.')
            data = json.loads(response.read(4096).decode('utf-8'))
    except Exception as exc:
        raise PublishError('Игровой API недоступен. Чтобы исключить риск, операция прервана.') from exc
    if not isinstance(data, dict) or data.get('ok') is not True:
        raise PublishError('API не ответил штатным JSON-здоровьем.')
    return {key: data.get(key) for key in ('service', 'api', 'ok')}


def home(domain: str) -> Path:
    return SITE_ROOT / safe_domain(domain)


def http_vhost(domain: str, tls: bool = False) -> str:
    root = str(home(domain) / 'current')
    common = (f'    DocumentRoot {root}\n'
              f'    <Directory {root}>\n'
              '        Options -Indexes -Includes -ExecCGI -MultiViews\n'
              '        AllowOverride None\n'
              '        Require all granted\n'
              '        DirectoryIndex index.html\n'
              '    </Directory>\n')
    top = f'# Managed by Serviter; domain={domain}\n'
    port80 = (f'<VirtualHost *:80>\n    ServerName {domain}\n' + common +
              f'    RedirectMatch 301 ^/(?!\\.well-known/acme-challenge/)(.*)$ https://{domain}/$1\n'
              '</VirtualHost>\n')
    if not tls:
        return top + port80
    cert = Path('/etc/letsencrypt/live') / domain
    port443 = (f'<VirtualHost *:443>\n    ServerName {domain}\n' + common +
               '    SSLEngine On\n'
               f'    SSLCertificateFile {cert}/fullchain.pem\n'
               f'    SSLCertificateKeyFile {cert}/privkey.pem\n'
               '    ErrorDocument 404 /404.html\n'
               '</VirtualHost>\n')
    return top + port80 + '\n' + port443


def write_config(domain: str, body: str):
    path = APACHE_AVAILABLE / filename(domain)
    temp = path.with_suffix('.conf.new')
    temp.write_text(body, encoding='utf-8')
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def change_link(link: Path, destination: Path):
    if link.exists() and not link.is_symlink():
        raise PublishError('Целевой путь занят обычной папкой или файлом.')
    tmp = link.parent / ('current.next.' + os.urandom(4).hex())
    tmp.symlink_to(destination)
    os.replace(tmp, link)


def validated_release(path: str | None, domain: str) -> Path | None:
    if path is None:
        return None
    if not isinstance(path, str):
        raise PublishError('Некорректная запись выпуска.')
    root = (home(domain) / 'releases').resolve()
    candidate = Path(path)
    if candidate.parent != root or not candidate.is_dir() or candidate.is_symlink():
        raise PublishError('Путь выпуска выходит за выделенную директорию.')
    return candidate


def extract(archive_path: Path, destination: Path):
    count = 0
    total = 0
    has_index = False
    with tarfile.open(archive_path, mode='r:gz') as tar:
        for member in tar:
            p = PurePosixPath(member.name)
            if (not member.isfile() or p.is_absolute() or '..' in p.parts or
                any(x.startswith('.') or x == 'deploy' for x in p.parts) or
                p.suffix.lower() not in STATIC_EXTS):
                raise PublishError('Недопустимое содержимое архива для статического сайта.')
            if member.size < 0 or member.size > 40 * 1024 * 1024 or total + member.size > 180 * 1024 * 1024:
                raise PublishError('Архив превышает безопасный размер.')
            if count >= 5000:
                raise PublishError('Слишком много файлов.')
            target = destination.joinpath(*p.parts)
            if target.exists():
                raise PublishError('Повторяющийся файл в архиве.')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o644)
            has_index |= member.name == 'index.html'
            count += 1
            total += member.size
    if not has_index:
        raise PublishError('В архиве сайта нет index.html.')
    for folder in destination.rglob('*'):
        if folder.is_dir():
            folder.chmod(0o755)
    return count


def verify_public_site(domain: str):
    try:
        with urlopen(f'https://{domain}/', timeout=14) as response:
            if response.status != 200 or b'<html' not in response.read(8192).lower():
                raise PublishError('Сайт не отдаёт корректную HTML-страницу по HTTPS.')
    except Exception as exc:
        raise PublishError('После публикации сайт недоступен по HTTPS. Выполняется откат.') from exc


def reload_apache():
    must(['apache2ctl', 'configtest'], error='Проверка новой конфигурации Apache не пройдена.')
    must(['systemctl', 'reload', 'apache2'], error='Не удалось безопасно перечитать Apache.')


def inspect(manifest: dict) -> dict:
    domain = safe_domain(manifest['domain'])
    manifest_checks(manifest)
    os_checks(domain)
    cfg = APACHE_AVAILABLE / filename(domain)
    cert = Path('/etc/letsencrypt/live') / domain
    tls = (cert / 'fullchain.pem').is_file() and (cert / 'privkey.pem').is_file()
    if not tls and not shutil.which('certbot'):
        raise PublishError('Нет TLS-сертификата и certbot. Сначала установите certbot без изменения API.')
    if not tls and not manifest.get('tls_email'):
        raise PublishError('Для нового TLS-сертификата необходим контактный e-mail.')
    baseline = api_status(manifest.get('api_url', ''))
    path = home(domain)
    current = path / 'current'
    if current.exists() and not current.is_symlink():
        raise PublishError('Каталог current занят неуправляемым файлом.')
    return {'ok': True, 'domain': domain, 'https_certificate': tls,
            'existing_site': cfg.is_file(), 'api': baseline,
            'target': str(current)}


def do_publish(manifest: dict, tar_file: Path) -> dict:
    before = inspect(manifest)
    domain = safe_domain(manifest['domain'])
    base = home(domain)
    releases = base / 'releases'
    releases.mkdir(mode=0o755, parents=True, exist_ok=True)
    current = base / 'current'
    previous = current.resolve() if current.is_symlink() else None
    if previous is not None:
        validated_release(str(previous), domain)
    release_dir = releases / (time.strftime('%Y%m%d-%H%M%S') + '-' + os.urandom(4).hex())
    release_dir.mkdir(mode=0o755)
    config_file = APACHE_AVAILABLE / filename(domain)
    enabled_file = APACHE_ENABLED / filename(domain)
    was_managed = config_file.is_file()
    former_config = config_file.read_bytes() if was_managed else None
    had_link = enabled_file.is_symlink()
    has_cert = bool(before['https_certificate'])
    switched = False
    try:
        num_files = extract(tar_file, release_dir)
        change_link(current, release_dir)
        switched = True
        if not was_managed:
            write_config(domain, http_vhost(domain, tls=False))
            if not had_link:
                enabled_file.symlink_to(config_file)
            reload_apache()
        if not has_cert:
            # HTTP-01 webroot doesn't stop or modify the game's HTTPS endpoint.
            certbot = ['certbot', 'certonly', '--non-interactive', '--agree-tos', '--webroot',
                       '-w', str(current), '-d', domain, '--email', manifest['tls_email'], '--keep-until-expiring']
            must(certbot, timeout=150, error='Не удалось получить TLS-сертификат без остановки Apache.')
        if not was_managed or not has_cert:
            write_config(domain, http_vhost(domain, tls=True))
            reload_apache()
        verify_public_site(domain)
        after_api = api_status(manifest.get('api_url', ''))
        if after_api != before['api']:
            raise PublishError('Ответ игрового API изменился. Выполняется откат.')
        state_path = base / 'state.json'
        state = {'current': str(release_dir), 'previous': str(previous) if previous else None,
                 'domain': domain, 'updated': int(time.time())}
        tmp = base / ('state.' + os.urandom(4).hex() + '.json')
        tmp.write_text(json.dumps(state), encoding='utf-8')
        os.chmod(tmp, 0o600)
        os.replace(tmp, state_path)
        return {'ok': True, 'url': f'https://{domain}/', 'release': release_dir.name,
                'files': num_files, 'api_unchanged': after_api == before['api']}
    except Exception:
        if switched:
            try:
                if previous:
                    change_link(current, previous)
                else:
                    current.unlink(missing_ok=True)
            except OSError:
                pass
        if former_config is None:
            try:
                if not had_link:
                    enabled_file.unlink(missing_ok=True)
                config_file.unlink(missing_ok=True)
                reload_apache()
            except Exception:
                pass
        else:
            try:
                config_file.write_bytes(former_config)
                reload_apache()
            except Exception:
                pass
        raise


def do_rollback(manifest: dict) -> dict:
    before = inspect(manifest)
    domain = safe_domain(manifest['domain'])
    base = home(domain)
    state_file = base / 'state.json'
    if not state_file.is_file():
        raise PublishError('Ранее опубликованного выпуска Serviter нет.')
    state = json.loads(state_file.read_text())
    if state.get('domain') != domain:
        raise PublishError('Неверный домен в журнале выпусков.')
    past = validated_release(state.get('previous'), domain)
    active = validated_release(state.get('current'), domain)
    if not past:
        raise PublishError('Нет предыдущей версии для отката.')
    current = base / 'current'
    if not current.is_symlink() or current.resolve() != active:
        raise PublishError('Текущий сайт изменён вне Serviter. Автооткат запрещён.')
    change_link(current, past)
    try:
        verify_public_site(domain)
        after_api = api_status(manifest.get('api_url', ''))
        if before['api'] != after_api:
            raise PublishError('Состояние API изменилось после отката.')
        state['current'], state['previous'] = str(past), str(active)
        temp = base / ('state.' + os.urandom(4).hex() + '.json')
        temp.write_text(json.dumps(state))
        os.chmod(temp, 0o600)
        os.replace(temp, state_file)
        return {'ok': True, 'url': f'https://{domain}/', 'release': past.name,
                'api_unchanged': True}
    except Exception:
        change_link(current, active)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', required=True, choices=('inspect', 'publish', 'rollback'))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--archive')
    args = parser.parse_args()
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        domain = safe_domain(manifest['domain'])
        if os.geteuid() != 0:
            raise PublishError('Требуются права root.')
        lock_path = Path('/var/lock') / ('serviter-' + domain + '.lock')
        with lock_path.open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.action == 'inspect':
                result = inspect(manifest)
            elif args.action == 'publish':
                if not args.archive:
                    raise PublishError('Не передан архив сайта.')
                result = do_publish(manifest, Path(args.archive))
            else:
                result = do_rollback(manifest)
        print(json.dumps(result, ensure_ascii=False))
    except (PublishError, OSError, json.JSONDecodeError, ValueError, subprocess.TimeoutExpired, BlockingIOError) as exc:
        # Deliberately exclude tracebacks, command output and secrets.
        message = str(exc) if isinstance(exc, PublishError) else type(exc).__name__
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())