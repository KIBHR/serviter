"""High-level workflow. Checks are separate from commits/deploys."""
from __future__ import annotations

from collections.abc import Callable
from .models import Config
from .source import create_site_archive, download_zip
from .ssh_client import SSHSession


def inspect(config: Config, log: Callable[[str], None]):
    config.validated()
    log('SSH: подключаемся по проверенному ключу сервера...')
    with SSHSession(config) as ssh:
        log('Ubuntu / Apache / DNS / существующие виртуальные хосты / API: проверка без изменений...')
        result = ssh.invoke('inspect', config.manifest())
    log('Проверка завершена. API не изменён.')
    return result


def publish(config: Config, log: Callable[[str], None]):
    config.validated()
    log('Загружаем архив GitHub по HTTPS...')
    source = download_zip(config)
    log('Формируем архив только из безопасных статических файлов...')
    archive, stats = create_site_archive(source, config.source_folder)
    log(f"Подготовлено {stats['files']} файлов. SHA-256: {stats['sha256'][:16]}…")
    log('Передаём архив по SFTP в закрытую временную папку...')
    with SSHSession(config) as ssh:
        result = ssh.invoke('publish', config.manifest(), archive)
    log('Публикация завершена. Состояние API перепроверено.')
    return result


def rollback(config: Config, log: Callable[[str], None]):
    config.validated()
    log('Проверяем сохранённый предыдущий выпуск сайта...')
    with SSHSession(config) as ssh:
        result = ssh.invoke('rollback', config.manifest())
    log('Предыдущая версия восстановлена. Игровой API проверен.')
    return result