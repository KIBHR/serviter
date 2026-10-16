"""SSH trust-on-explicit-confirmation, encrypted upload and root worker execution."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import time
import sys
import uuid

from .models import Config, UserError


def _paramiko():
    import paramiko
    return paramiko


def identity(config: Config) -> str:
    return config.host if config.port == 22 else f'[{config.host}]:{config.port}'


def known_hosts_path() -> Path:
    folder = Path.home() / '.serviter'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        folder.chmod(0o700)
    except OSError:
        pass
    return folder / 'known_hosts'


def fingerprint(key) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return 'SHA256:' + base64.b64encode(digest).decode('ascii').rstrip('=')


def _known_key(host: str, key) -> str:
    p = _paramiko()
    matched = False
    # Fail closed if either our pin or OpenSSH's known_hosts disagrees.
    for file in (str(known_hosts_path()), str(Path.home() / '.ssh' / 'known_hosts')):
        if not Path(file).is_file():
            continue
        hosts = p.HostKeys()
        try:
            hosts.load(file)
            existing = hosts.lookup(host)
        except (OSError, ValueError):
            continue
        if existing and key.get_name() in existing:
            if existing[key.get_name()].asbytes() != key.asbytes():
                raise UserError('SSH-ключ сервера изменился! Публикация заблокирована. Проверьте сервер вне приложения.')
            matched = True
    return 'known' if matched else 'unknown'


def probe_host(config: Config):
    p = _paramiko()
    transport = None
    try:
        with socket.create_connection((config.host, config.port), timeout=10) as sock:
            transport = p.Transport(sock)
            transport.banner_timeout = 12
            transport.start_client(timeout=12)
            key = transport.get_remote_server_key()
            result = {'fingerprint': fingerprint(key), 'type': key.get_name(), 'trusted': _known_key(identity(config), key)}
            return result, key
    except UserError:
        raise
    except (OSError, EOFError, p.SSHException) as exc:
        raise UserError('SSH-сервер недоступен: ' + type(exc).__name__) from exc
    finally:
        if transport:
            transport.close()


def pin_host(config: Config, key, expected_fingerprint: str) -> None:
    if fingerprint(key) != expected_fingerprint:
        raise UserError('Отпечаток SSH-ключа не совпадает.')
    host = identity(config)
    if _known_key(host, key) == 'known':
        return
    p = _paramiko()
    path = known_hosts_path()
    hosts = p.HostKeys()
    if path.exists():
        hosts.load(str(path))
    hosts.add(host, key.get_name(), key)
    hosts.save(str(path))
    os.chmod(path, 0o600)


def connect(config: Config):
    p = _paramiko()
    client = p.SSHClient()
    client.load_system_host_keys()
    kh = known_hosts_path()
    if kh.exists():
        client.load_host_keys(str(kh))
    client.set_missing_host_key_policy(p.RejectPolicy())
    try:
        client.connect(config.host, port=config.port, username=config.user,
                       password=config.password or None, key_filename=config.key_file or None,
                       look_for_keys=False, allow_agent=False,
                       timeout=12, auth_timeout=15, banner_timeout=12)
    except Exception as exc:
        client.close()
        raise UserError('SSH-авторизация или проверка ключа не удалась: ' + type(exc).__name__) from exc
    return client


def run(client, command: str, *, timeout: int = 30, sudo_password: str = '') -> tuple[int, str, str]:
    if command.startswith('sudo '):
        command = command.replace('sudo ', "sudo -S -p '' " if sudo_password else 'sudo -n ', 1)
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=False)
    if sudo_password and command.startswith('sudo -S'):
        stdin.write(sudo_password + '\n')
        stdin.flush()
    stdin.channel.shutdown_write()
    out = stdout.read(1024 * 1024 + 1).decode('utf-8', 'replace')
    err = stderr.read(65536 + 1).decode('utf-8', 'replace')
    status = stdout.channel.recv_exit_status()
    if len(out) > 1024 * 1024 or len(err) > 65536:
        raise UserError('Удалённый процесс вывел слишком много данных.')
    return status, out, err


class SSHSession:
    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.stage = None

    def __enter__(self):
        self.client = connect(self.config)
        status, output, _ = run(self.client, 'mktemp -d /tmp/serviter.XXXXXXXX', timeout=15, sudo_password=None)
        stage = output.strip()
        if status or not stage.startswith('/tmp/serviter.') or '/' in stage.removeprefix('/tmp/'):
            self.client.close()
            raise UserError('Не удалось создать временную защищённую папку на сервере.')
        self.stage = stage
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.client:
            if self.stage:
                try:
                    run(self.client, 'rm -rf -- ' + shlex.quote(self.stage), timeout=12, sudo_password=None)
                except Exception:
                    pass
            self.client.close()

    def upload(self, filename: str, data: bytes):
        if not filename.replace('_', '').replace('.', '').isalnum():
            raise UserError('Некорректное имя временного файла.')
        with self.client.open_sftp() as sftp:
            with sftp.file(self.stage + '/' + filename, 'wb') as stream:
                stream.write(data)
            sftp.chmod(self.stage + '/' + filename, 0o600)

    def invoke(self, action: str, manifest: dict, archive: bytes | None = None) -> dict:
        from . import remote_worker
        worker_file = (Path(sys._MEIPASS) / 'remote_worker.py'
                       if getattr(sys, 'frozen', False) else Path(remote_worker.__file__))
        self.upload('remote_worker.py', worker_file.read_bytes())
        self.upload('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))
        if archive is not None:
            self.upload('site.tar.gz', archive)
        stage = shlex.quote(self.stage)
        command = (f'sudo /usr/bin/python3 {stage}/remote_worker.py '
                   f'--action {shlex.quote(action)} --manifest {stage}/manifest.json'
                   + (f' --archive {stage}/site.tar.gz' if archive is not None else ''))
        status, out, err = run(self.client, command, timeout=210,
                               sudo_password=self.config.sudo_password or self.config.password)
        try:
            obj = json.loads(out.strip())
        except (json.JSONDecodeError, ValueError):
            obj = {}
        if status or obj.get('ok') is not True:
            # Never show raw stderr: sudo/other tools may include sensitive information.
            reason = obj.get('error', '') if isinstance(obj, dict) else ''
            raise UserError('Сервер отклонил операцию: ' + (reason[:400] if reason else 'см. системные журналы на VPS'))
        return obj