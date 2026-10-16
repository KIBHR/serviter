"""Fetch a GitHub ZIP archive into a strictly filtered static-site tarball."""
from __future__ import annotations

from io import BytesIO
import hashlib
from pathlib import PurePosixPath
import stat
import tarfile
import zipfile
from urllib.parse import quote, urlparse

import requests
from .models import Config, UserError, validate_repo, validate_ref, validate_source

MAX_DOWNLOAD = 75 * 1024 * 1024
MAX_SITE_BYTES = 180 * 1024 * 1024
MAX_FILE = 40 * 1024 * 1024
MAX_FILES = 5000
STATIC_SUFFIXES = {'.html', '.htm', '.css', '.js', '.mjs', '.svg', '.png', '.jpg', '.jpeg', '.gif',
                   '.webp', '.ico', '.woff', '.woff2', '.ttf', '.otf', '.txt', '.xml', '.json',
                   '.webmanifest', '.avif', '.mp4', '.webm', '.wasm'}
DENIED_PARTS = {'.git', '.github', '.env', '.ssh', 'node_modules', '__pycache__', 'deploy',
                '.well-known', 'secrets', 'backup', 'backups'}


def download_zip(config: Config) -> bytes:
    owner, repo = validate_repo(config.repo_url)
    branch = validate_ref(config.branch)
    endpoint = f'https://api.github.com/repos/{owner}/{repo}/zipball/{quote(branch, safe="")}'
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Serviter/0.1'}
    if config.github_token.strip():
        headers['Authorization'] = 'Bearer ' + config.github_token.strip()
    # Never follow cross-host redirects carrying Authorization credentials.
    with requests.Session() as session:
        session.trust_env = True
        try:
            response = session.get(endpoint, headers=headers, timeout=(10, 30), allow_redirects=False, stream=True)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get('Location', '')
                target = urlparse(location)
                if target.scheme != 'https' or target.hostname != 'codeload.github.com' or target.username or target.password:
                    raise UserError('GitHub вернул небезопасный адрес перенаправления. Загрузка остановлена.')
                response.close()
                response = session.get(location, headers={'User-Agent': 'Serviter/0.1'}, timeout=(10, 40),
                                       allow_redirects=False, stream=True)
            if response.status_code in (401, 403, 404):
                raise UserError('Репозиторий или ветка недоступны. Для приватного репозитория нужен GitHub Token с правом Contents: Read.')
            if response.status_code != 200:
                raise UserError(f'GitHub вернул HTTP {response.status_code}.')
            content = BytesIO()
            for chunk in response.iter_content(chunk_size=65536):
                content.write(chunk)
                if content.tell() > MAX_DOWNLOAD:
                    raise UserError('Архив GitHub слишком большой для безопасной публикации.')
            return content.getvalue()
        except requests.RequestException as exc:
            raise UserError('Не удалось загрузить архив GitHub: ' + type(exc).__name__) from exc
        finally:
            if 'response' in locals():
                response.close()


def create_site_archive(zip_data: bytes, source_folder: str) -> tuple[bytes, dict]:
    prefix = validate_source(source_folder)
    output = BytesIO()
    extracted_count = 0
    total_bytes = 0
    added = set()
    try:
        with zipfile.ZipFile(BytesIO(zip_data)) as archive, tarfile.open(fileobj=output, mode='w:gz') as tar:
            records = archive.infolist()
            if len(records) > MAX_FILES * 2:
                raise UserError('В репозитории слишком много файлов.')
            if not records:
                raise UserError('Архив репозитория пуст.')
            roots = {item.filename.split('/')[0] for item in records}
            if len(roots) != 1:
                raise UserError('Неожиданная структура архива GitHub.')
            root = next(iter(roots))
            for item in records:
                path = item.filename
                if ('\\' in path or path.startswith('/') or '\x00' in path or
                    any(part == '..' for part in path.split('/'))):
                    raise UserError('Обнаружен потенциально опасный путь в архиве.')
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind == stat.S_IFLNK:
                    raise UserError('Символические ссылки в источнике сайта запрещены.')
                if item.is_dir():
                    continue
                if kind not in (0, stat.S_IFREG):
                    raise UserError('Специальные файлы в архиве не поддерживаются.')
                relative_to_repo = '/'.join(path.split('/')[1:])
                if prefix:
                    if not relative_to_repo.startswith(prefix + '/'):
                        continue
                    relative = relative_to_repo[len(prefix) + 1:]
                else:
                    relative = relative_to_repo
                parts = PurePosixPath(relative).parts
                if (not parts or any(part.startswith('.') or part in DENIED_PARTS for part in parts)
                    or PurePosixPath(relative).suffix.lower() not in STATIC_SUFFIXES):
                    continue
                if relative in added:
                    raise UserError('Повторяющиеся пути в архиве.')
                if item.file_size > MAX_FILE or total_bytes + item.file_size > MAX_SITE_BYTES:
                    raise UserError('Размер файлов сайта превышает безопасный предел.')
                if extracted_count >= MAX_FILES:
                    raise UserError('Слишком много файлов сайта.')
                with archive.open(item) as src:
                    content = src.read(MAX_FILE + 1)
                if len(content) != item.file_size:
                    raise UserError('Повреждённый файл в ZIP-архиве.')
                info = tarfile.TarInfo(relative)
                info.size = len(content)
                info.mode = 0o644
                info.mtime = 0
                tar.addfile(info, BytesIO(content))
                extracted_count += 1
                total_bytes += len(content)
                added.add(relative)
    except (zipfile.BadZipFile, OSError, EOFError) as exc:
        raise UserError('Не удалось прочитать архив репозитория.') from exc
    if 'index.html' not in added:
        raise UserError('В выбранной папке отсутствует index.html. Для проекта игры укажите папку website.')
    result = output.getvalue()
    return result, {'files': extracted_count, 'bytes': total_bytes, 'sha256': hashlib.sha256(result).hexdigest()}