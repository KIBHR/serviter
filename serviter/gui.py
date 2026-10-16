"""Mac-friendly local desktop GUI. Credentials are kept only in RAM."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk, filedialog

from . import __version__
from . import deploy
from .models import Config, UserError
from .ssh_client import probe_host, pin_host

BG = '#09131b'
PANEL = '#101e28'
FIELD = '#152b36'
CYAN = '#6ee8d5'
WHITE = '#edf7f6'
DIM = '#9db7bc'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('SERVITER — безопасная публикация сайтов')
        self.geometry('1010x840')
        self.minsize(830, 700)
        self.configure(bg=BG)
        self.events = queue.Queue()
        self.busy = False
        self.checked = None
        self.entries: dict[str, tk.Entry] = {}
        self._init_theme()
        self._draw()
        self.after(100, self._poll)

    def _init_theme(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=WHITE, font=('Helvetica Neue', 11))
        style.configure('Hint.TLabel', background=BG, foreground=DIM, font=('Helvetica Neue', 10))
        style.configure('Title.TLabel', background=BG, foreground=WHITE, font=('Helvetica Neue', 24, 'bold'))
        style.configure('TEntry', fieldbackground=FIELD, foreground=WHITE, insertcolor=CYAN,
                        borderwidth=1, relief='flat', padding=8)
        style.configure('TButton', background='#1a4146', foreground=WHITE, padding=(16, 10),
                        borderwidth=0, font=('Helvetica Neue', 11, 'bold'))
        style.map('TButton', background=[('active', '#2b6267'), ('disabled', '#18313a')],
                  foreground=[('disabled', DIM)])
        style.configure('Go.TButton', background='#5ddac6', foreground='#06191c')
        style.map('Go.TButton', background=[('active', '#9affea'), ('disabled', '#174741')])
        style.configure('TCheckbutton', background=BG, foreground=WHITE)

    def _draw(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=34, pady=(29, 10))
        ttk.Label(top, text='SERVITER /', foreground=CYAN, font=('Menlo', 12, 'bold')).pack(anchor='w')
        ttk.Label(top, text='Развёртывание без риска для соседних сервисов.',
                  style='Title.TLabel').pack(anchor='w', pady=(6, 4))
        ttk.Label(top, text='Локальное приложение • секреты только в оперативной памяти • выделенный Apache vhost • HTTPS • откат',
                  style='Hint.TLabel').pack(anchor='w')
        ttk.Separator(self).pack(fill='x', padx=34, pady=(8, 13))

        form = ttk.Frame(self)
        form.pack(fill='x', padx=34)
        form.columnconfigure(0, weight=1, uniform='cols')
        form.columnconfigure(1, weight=1, uniform='cols')
        a = ttk.Frame(form); a.grid(row=0, column=0, sticky='nsew', padx=(0, 17))
        b = ttk.Frame(form); b.grid(row=0, column=1, sticky='nsew', padx=(17, 0))
        self._section(a, '01 / СЕРВЕР')
        self._field(a, 'host', 'IP или SSH-хост', '212.116.232.118')
        row = ttk.Frame(a); row.pack(fill='x')
        self._field(row, 'user', 'SSH-пользователь', 'ubuntu', side='left')
        self._field(row, 'port', 'Порт', '22', side='left', narrow=True)
        self._field(a, 'password', 'SSH-пароль', '', secret=True)
        self._field(a, 'key_file', 'Путь к приватному SSH-ключу (вместо пароля)', '')
        self._field(a, 'sudo_password', 'Пароль sudo (если отличается от SSH)', '', secret=True)
        self._section(b, '02 / GITHUB И САЙТ')
        self._field(b, 'repo_url', 'Ссылка на GitHub', 'https://github.com/KIBHR/cipher-breach.git')
        row2 = ttk.Frame(b); row2.pack(fill='x')
        self._field(row2, 'branch', 'Ветка', 'feature/adt-studio-website', side='left')
        self._field(row2, 'source_folder', 'Папка сайта', 'website', side='left')
        self._field(b, 'github_token', 'GitHub Token (только для приватного репозитория)', '', secret=True)
        self._field(b, 'domain', 'Домен публичного сайта', 'adt-studio.asia')
        self._field(b, 'tls_email', 'E-mail для выпуска сертификата HTTPS', '')
        self._field(b, 'api_url', 'Проверять игровой API (рекомендуется)', 'https://api.adt-studio.asia/v1/health')

        controls = ttk.Frame(self)
        controls.pack(fill='x', padx=34, pady=(21, 10))
        self.check_btn = ttk.Button(controls, text='1. Проверить сервер', command=self._check)
        self.check_btn.pack(side='left', padx=(0, 10))
        self.deploy_btn = ttk.Button(controls, text='2. Опубликовать HTTPS', style='Go.TButton',
                                     command=self._publish, state='disabled')
        self.deploy_btn.pack(side='left', padx=(0, 10))
        self.rollback_btn = ttk.Button(controls, text='↶ Откатить сайт', command=self._rollback, state='disabled')
        self.rollback_btn.pack(side='left')
        self.progress = ttk.Progressbar(controls, mode='indeterminate', length=120)
        self.progress.pack(side='right', padx=10)
        self.status = tk.StringVar(value='Заполните поля и нажмите «Проверить сервер».')
        ttk.Label(self, textvariable=self.status, style='Hint.TLabel').pack(fill='x', padx=34, pady=(2, 9))
        log_header = ttk.Frame(self); log_header.pack(fill='x', padx=34)
        ttk.Label(log_header, text='ЖУРНАЛ БЕЗ ПАРОЛЕЙ', foreground=CYAN, font=('Menlo', 10)).pack(side='left')
        self.log_box = tk.Text(self, height=9, font=('Menlo', 10), bg='#081018', fg='#b7d7d5',
                               insertbackground=CYAN, wrap='word', relief='flat', padx=15, pady=11, state='disabled')
        self.log_box.pack(fill='both', expand=True, padx=34, pady=(9, 13))
        self._log('Приложение не сохраняет SSH-пароль и GitHub Token на диск.')
        self._log('Первый SSH-ключ необходимо сверить с отпечатком сервера из независимого источника.')

    def _section(self, parent, title):
        ttk.Label(parent, text=title, foreground=CYAN, font=('Menlo', 11, 'bold')).pack(anchor='w', pady=(7, 15))

    def _field(self, parent, name, label, example, *, secret=False, side=None, narrow=False):
        container = ttk.Frame(parent)
        container.pack(side=side or 'top', fill='x', expand=True, padx=(0, 10) if side else 0,
                       pady=(0, 10))
        ttk.Label(container, text=label, style='Hint.TLabel').pack(anchor='w', pady=(0, 4))
        entry = ttk.Entry(container, show='•' if secret else '', width=8 if narrow else 36)
        entry.pack(fill='x')
        if example:
            entry.insert(0, example)
        self.entries[name] = entry
        if not secret:
            entry.bind('<KeyRelease>', lambda ev: self._invalidate())

    def _invalidate(self):
        self.checked = None
        self.deploy_btn.configure(state='disabled')
        self.rollback_btn.configure(state='disabled')

    def _config(self):
        e = {k: entry.get().strip() for k, entry in self.entries.items()}
        try:
            port = int(e['port'])
        except ValueError:
            raise UserError('SSH-порт должен быть числом.') from None
        return Config(host=e['host'], port=port, user=e['user'], password=e['password'],
                      key_file=e['key_file'], sudo_password=e['sudo_password'],
                      repo_url=e['repo_url'], branch=e['branch'], github_token=e['github_token'],
                      source_folder=e['source_folder'], domain=e['domain'],
                      tls_email=e['tls_email'], api_url=e['api_url']).validated()

    def _log(self, text):
        self.log_box.configure(state='normal')
        self.log_box.insert('end', '› ' + text + '\n')
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

    def _submit(self, label, fn):
        if self.busy:
            return
        self.busy = True
        self.check_btn.configure(state='disabled')
        self.deploy_btn.configure(state='disabled')
        self.rollback_btn.configure(state='disabled')
        self.progress.start(14)
        self.status.set(label)
        def go():
            try:
                result = fn()
                self.events.put(('ok', label, result))
            except Exception as exc:
                # Do not log exception tracebacks; some libraries may include URLs/headers.
                message = str(exc) if isinstance(exc, UserError) else type(exc).__name__
                self.events.put(('error', label, message))
        threading.Thread(target=go, daemon=True).start()

    def _check(self):
        try:
            config = self._config()
        except UserError as exc:
            messagebox.showerror('Проверка полей', str(exc), parent=self)
            return
        self.checked = None
        self._submit('Проверяем отпечаток SSH-ключа...', lambda: ('probe', config, probe_host(config)))

    def _trust_host(self, config, probe):
        details, key = probe
        fp = details['fingerprint']
        if details['trusted'] == 'known':
            self._log('SSH-ключ соответствует ранее доверенному ключу: ' + fp)
            self._preflight(config)
            return
        description = ('Впервые обнаружен SSH-сервер.\n\n'
                       + config.host + ' · ' + details['type'] + '\n' + fp + '\n\n'
                       'Сверьте SHA256-отпечаток через панель VPS или доверенный SSH-сеанс.\n'
                       'На VPS: sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256\n\n'
                       'Введите отпечаток целиком, чтобы явно доверять этому ключу:')
        entered = simpledialog.askstring('Проверка подлинности SSH-сервера', description, parent=self)
        if entered != fp:
            self._log('Отпечаток не подтверждён. Сервер не добавлен в доверенные.')
            return
        try:
            pin_host(config, key, entered)
        except UserError as exc:
            messagebox.showerror('Отпечаток SSH', str(exc), parent=self)
            return
        self._log('SSH-ключ закреплён локально. При неожиданной смене ключа подключение будет заблокировано.')
        self._preflight(config)

    def _preflight(self, config):
        self._submit('Проверяем сервер и защиту API (ничего не меняем)...',
                     lambda: ('inspect', config, deploy.inspect(config, self._thread_log)))

    def _thread_log(self, line):
        self.events.put(('log', line))

    def _publish(self):
        try:
            config = self._config()
        except UserError as exc:
            messagebox.showerror('Проверка полей', str(exc), parent=self)
            return
        if self.checked != config:
            self._invalidate()
            messagebox.showwarning('Повторная проверка', 'Поля изменились. Сначала снова проверьте сервер.', parent=self)
            return
        if not messagebox.askyesno('Подтверждение публикации',
                f'Опубликовать папку «{config.source_folder or "."}» из ветки «{config.branch}»\n'
                f'на https://{config.domain}/ ?\n\n'
                'Будут созданы отдельный виртуальный хост Apache и папка выпусков.\n'
                'Существующий API не изменяется, его доступность проверяется до и после.\n'
                'Подтвердить изменения на сервере?', parent=self):
            return
        self._submit('Публикуем сайт и проверяем HTTPS...', lambda: ('publish', config, deploy.publish(config, self._thread_log)))

    def _rollback(self):
        try:
            config = self._config()
        except UserError as exc:
            messagebox.showerror('Проверка полей', str(exc), parent=self)
            return
        if self.checked != config:
            self._invalidate()
            messagebox.showwarning('Проверка', 'Сначала снова проверьте сервер.', parent=self)
            return
        if not messagebox.askyesno('Откат', 'Восстановить предыдущий выпуск сайта? API не меняем.', parent=self):
            return
        self._submit('Выполняем откат предыдущего выпуска...', lambda: ('rollback', config, deploy.rollback(config, self._thread_log)))

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'log':
                    self._log(event[1]); continue
                kind, label, result = event
                self.busy = False
                self.progress.stop()
                self.check_btn.configure(state='normal')
                if kind == 'error':
                    self.status.set('Операция не выполнена')
                    self._log('ОШИБКА: ' + result)
                    messagebox.showerror('Serviter', result, parent=self)
                    continue
                action, config, payload = result
                if action == 'probe':
                    self.status.set('Проверка SSH-ключа')
                    self._trust_host(config, payload)
                elif action == 'inspect':
                    self.checked = config
                    self.deploy_btn.configure(state='normal')
                    self.rollback_btn.configure(state='normal' if payload.get('existing_site') else 'disabled')
                    self.status.set('Сервер проверен. Можно публиковать.')
                    self._log('Apache и DNS проверены. API: ' + ('здоров' if payload.get('api') else 'не указан'))
                    self._log('Сертификат: ' + ('уже найден' if payload['https_certificate'] else 'будет получен через Certbot'))
                elif action in ('publish', 'rollback'):
                    self.status.set('Успешно: ' + payload.get('url', ''))
                    self._log('ГОТОВО: ' + payload.get('url', ''))
                    self.checked = None  # always re-check before next server mutation
                    messagebox.showinfo('Готово', 'Сайт доступен: ' + payload.get('url', ''), parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    App().mainloop()