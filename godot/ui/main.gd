extends Control
## Serviter desktop interface. Actual SSH and GitHub transfers use a local,
## authenticated, ephemeral Python sidecar. No credentials are written to disk.

const BG := Color("071019")
const PANEL := Color("101f2c")
const FIELD := Color("172c38")
const BORDER := Color("294957")
const FG := Color("e4f8f5")
const MUTED := Color("9aafb6")
const AQUA := Color("66e7d8")
const GOLD := Color("e6b677")

const FIELD_INFO := [
    ["host", "SSH-адрес сервера", "212.116.232.118", false],
    ["user", "SSH-пользователь", "ubuntu", false],
    ["port", "SSH-порт", "22", false],
    ["password", "SSH-пароль", "", true],
    ["key_file", "Путь к SSH-ключу (вместо пароля)", "", false],
    ["sudo_password", "Пароль sudo (если отличается)", "", true],
    ["repo_url", "GitHub-репозиторий", "https://github.com/KIBHR/cipher-breach.git", false],
    ["branch", "Ветка", "feature/adt-studio-website", false],
    ["source_folder", "Папка статического сайта", "website", false],
    ["github_token", "GitHub Token (если репозиторий приватный)", "", true],
    ["domain", "Публичный домен сайта", "adt-studio.asia", false],
    ["tls_email", "E-mail сертификата HTTPS", "", false],
    ["api_url", "Проверка игрового API", "https://api.adt-studio.asia/v1/health", false],
]

var fields: Dictionary = {}
var check_button: Button
var publish_button: Button
var rollback_button: Button
var status_label: Label
var log_box: RichTextLabel
var indicator: ColorRect
var request: HTTPRequest
var startup_timer: Timer
var confirm_dialog: ConfirmationDialog
var pin_dialog: ConfirmationDialog
var pin_input: LineEdit
var pending_confirmation := ""
var pending_request := ""
var bridge_pid := -1
var bridge_port := 0
var bridge_secret := ""
var ping_attempts := 0
var ready := false
var busy := false
var checked := false
var checked_fingerprint := ""
var last_config_fingerprint := ""
var last_config: Dictionary = {}


func _ready() -> void:
    get_window().min_size = Vector2i(870, 670)
    _draw_interface()
    request = HTTPRequest.new()
    request.timeout = 250.0
    request.use_threads = true
    add_child(request)
    request.request_completed.connect(_on_request_completed)
    startup_timer = Timer.new()
    startup_timer.wait_time = 0.4
    startup_timer.timeout.connect(_on_startup_timer)
    add_child(startup_timer)
    _start_bridge()


func _exit_tree() -> void:
    # Kill our own child only. Avoid calling any shell or killing unrelated PIDs.
    if bridge_pid > 0:
        OS.kill(bridge_pid)
        bridge_pid = -1


func _draw_interface() -> void:
    var root := MarginContainer.new()
    root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
    root.add_theme_constant_override("margin_left", 30)
    root.add_theme_constant_override("margin_right", 30)
    root.add_theme_constant_override("margin_top", 23)
    root.add_theme_constant_override("margin_bottom", 20)
    add_child(root)
    var stack := VBoxContainer.new()
    stack.add_theme_constant_override("separation", 16)
    root.add_child(stack)
    var bar := HBoxContainer.new()
    stack.add_child(bar)
    var logo := _label("SERVITER", 28, AQUA)
    bar.add_child(logo)
    var gap := Control.new()
    gap.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    bar.add_child(gap)
    indicator = ColorRect.new()
    indicator.custom_minimum_size = Vector2(9, 9)
    indicator.color = GOLD
    bar.add_child(indicator)
    var tagline := _label("LOCAL DEPLOY / GODOT 4", 12, MUTED)
    bar.add_child(tagline)

    var title := _label("Публикация сайта без риска для игрового API", 21, FG)
    stack.add_child(title)
    var hint := _label("Шифрованный SSH/SFTP · проверка ключа · отдельный Apache vhost · HTTPS · откат", 13, MUTED)
    stack.add_child(hint)
    _rule(stack)

    var scroller := ScrollContainer.new()
    scroller.size_flags_vertical = Control.SIZE_EXPAND_FILL
    scroller.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
    stack.add_child(scroller)
    var columns := HBoxContainer.new()
    columns.add_theme_constant_override("separation", 22)
    columns.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    scroller.add_child(columns)
    var left := _column("01  /  СЕРВЕР")
    var right := _column("02  /  GITHUB И САЙТ")
    columns.add_child(left)
    columns.add_child(right)
    for i in range(FIELD_INFO.size()):
        var spec: Array = FIELD_INFO[i]
        _field(left if i < 6 else right, str(spec[0]), str(spec[1]), str(spec[2]), bool(spec[3]))

    _rule(stack)
    var controls := HBoxContainer.new()
    controls.add_theme_constant_override("separation", 12)
    stack.add_child(controls)
    check_button = _button("1  Проверить сервер", false)
    check_button.pressed.connect(_check_server)
    controls.add_child(check_button)
    publish_button = _button("2  Опубликовать HTTPS", true)
    publish_button.pressed.connect(_confirm_publish)
    controls.add_child(publish_button)
    rollback_button = _button("↶  Откатить", false)
    rollback_button.pressed.connect(_confirm_rollback)
    controls.add_child(rollback_button)

    status_label = _label("Запуск защищённого локального модуля…", 13, MUTED)
    stack.add_child(status_label)
    stack.add_child(_label("ЖУРНАЛ ОПЕРАЦИЙ / БЕЗ СЕКРЕТОВ", 12, AQUA))
    log_box = RichTextLabel.new()
    log_box.bbcode_enabled = false
    log_box.scroll_following = true
    log_box.custom_minimum_size = Vector2(0, 125)
    log_box.size_flags_vertical = Control.SIZE_EXPAND_FILL
    log_box.add_theme_color_override("default_color", MUTED)
    log_box.add_theme_font_size_override("normal_font_size", 12)
    stack.add_child(log_box)
    _log("Пароли и GitHub Token остаются в оперативной памяти приложения.")
    _log("При первом подключении сверьте SHA256-отпечаток SSH с панелью провайдера VPS.")

    confirm_dialog = ConfirmationDialog.new()
    confirm_dialog.title = "Подтверждение Serviter"
    confirm_dialog.confirmed.connect(_confirmed)
    add_child(confirm_dialog)
    pin_dialog = ConfirmationDialog.new()
    pin_dialog.title = "Проверка подлинности SSH-сервера"
    pin_dialog.dialog_text = "Сверьте SSH SHA256-отпечаток через доверенный канал. Вставьте его ниже точно как показано."
    pin_dialog.confirmed.connect(_confirmed_pin)
    add_child(pin_dialog)
    pin_input = LineEdit.new()
    pin_input.placeholder_text = "SHA256:..."
    pin_input.custom_minimum_size = Vector2(500, 38)
    pin_dialog.get_vbox().add_child(pin_input)
    _update_buttons()


func _column(title: String) -> VBoxContainer:
    var box := VBoxContainer.new()
    box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
    box.add_theme_constant_override("separation", 8)
    box.add_child(_label(title, 14, AQUA))
    return box


func _field(box: VBoxContainer, id: String, description: String, example: String, secret: bool) -> void:
    box.add_child(_label(description, 13, MUTED))
    var entry := LineEdit.new()
    entry.custom_minimum_size = Vector2(0, 37)
    entry.text = example
    entry.placeholder_text = "Не указан" if example.is_empty() else example
    entry.secret = secret
    entry.add_theme_stylebox_override("normal", _style(FIELD, BORDER))
    entry.add_theme_stylebox_override("focus", _style(FIELD, AQUA))
    entry.add_theme_color_override("font_color", FG)
    entry.add_theme_color_override("font_placeholder_color", MUTED.darkened(0.25))
    entry.text_changed.connect(func(_new_text: String): _invalidate())
    box.add_child(entry)
    fields[id] = entry


func _button(caption: String, highlighted: bool) -> Button:
    var b := Button.new()
    b.text = caption
    b.custom_minimum_size = Vector2(0, 44)
    b.add_theme_stylebox_override("normal", _style(AQUA if highlighted else PANEL, AQUA if highlighted else BORDER))
    b.add_theme_stylebox_override("hover", _style(Color("a4ffe9") if highlighted else FIELD, AQUA))
    b.add_theme_stylebox_override("disabled", _style(PANEL, BORDER))
    b.add_theme_color_override("font_color", BG if highlighted else FG)
    b.add_theme_color_override("font_disabled_color", MUTED.darkened(0.35))
    b.add_theme_font_size_override("font_size", 13)
    return b


func _style(color: Color, outline: Color) -> StyleBoxFlat:
    var style := StyleBoxFlat.new()
    style.bg_color = color
    style.border_color = outline
    style.set_border_width_all(1)
    style.set_corner_radius_all(5)
    style.content_margin_left = 12
    style.content_margin_right = 12
    style.content_margin_top = 6
    style.content_margin_bottom = 6
    return style


func _label(content: String, size: int, tone: Color) -> Label:
    var l := Label.new()
    l.text = content
    l.add_theme_font_size_override("font_size", size)
    l.add_theme_color_override("font_color", tone)
    return l


func _rule(box: VBoxContainer) -> void:
    var h := HSeparator.new()
    box.add_child(h)


func _log(line: String) -> void:
    if log_box == null:
        return
    # Never log the config, HTTP body, passwords, tokens or traceback.
    log_box.append_text("› " + line + "\n")


func _invalidate() -> void:
    checked = false
    last_config_fingerprint = ""
    _update_buttons()


func _update_buttons() -> void:
    if check_button == null:
        return
    check_button.disabled = not ready or busy
    publish_button.disabled = not ready or busy or not checked
    rollback_button.disabled = not ready or busy or not checked


func _start_bridge() -> void:
    bridge_port = randi_range(32768, 61000)
    var crypto := Crypto.new()
    bridge_secret = crypto.generate_random_bytes(32).hex_encode()
    var bridge_path := OS.get_executable_path().get_base_dir().path_join("serviter-bridge")
    var executable := bridge_path
    var arguments := PackedStringArray(["--port", str(bridge_port), "--secret", bridge_secret])
    if not FileAccess.file_exists(bridge_path):
        # Godot editor development only. Exported applications ship a bundled helper.
        if not OS.has_feature("editor"):
            _fail("В сборке отсутствует модуль serviter-bridge.")
            return
        executable = "python3"
        arguments = PackedStringArray([
            ProjectSettings.globalize_path("res://../run_serviter_bridge.py"),
            "--port", str(bridge_port), "--secret", bridge_secret,
        ])
    bridge_pid = OS.create_process(executable, arguments, false)
    if bridge_pid <= 0:
        _fail("Не удалось запустить локальный модуль SSH. Проверьте установку Python.")
        return
    startup_timer.start()
    _log("Защищённый модуль запущен; ожидание локального соединения.")


func _on_startup_timer() -> void:
    if ready or pending_request != "":
        return
    if ping_attempts >= 35:
        startup_timer.stop()
        _fail("Локальный модуль не ответил. Проверьте версию приложения или Python-зависимости.")
        return
    ping_attempts += 1
    _request_local("health", {})


func _request_local(action: String, payload: Dictionary) -> void:
    pending_request = action
    var endpoint := "/health" if action == "health" else "/command"
    var headers := PackedStringArray(["Authorization: Bearer " + bridge_secret, "Accept: application/json"])
    var method := HTTPClient.METHOD_GET
    var body := ""
    if action != "health":
        headers.append("Content-Type: application/json")
        method = HTTPClient.METHOD_POST
        payload["action"] = action
        body = JSON.stringify(payload)
    var err := request.request("http://127.0.0.1:%s%s" % [str(bridge_port), endpoint], headers, method, body)
    if err != OK:
        pending_request = ""
        if action != "health":
            _fail("Не удалось отправить локальный запрос: " + str(err))


func _on_request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
    var action := pending_request
    pending_request = ""
    if action == "health":
        if result == HTTPRequest.RESULT_SUCCESS and response_code == 200:
            ready = true
            startup_timer.stop()
            status_label.text = "Готов к подключению. Введите параметры сервера и репозитория."
            indicator.color = AQUA
            _log("Локальный модуль готов. Можно проверить SSH-сервер.")
            _update_buttons()
        return
    busy = false
    var reply = JSON.parse_string(body.get_string_from_utf8())
    if result != HTTPRequest.RESULT_SUCCESS or not (reply is Dictionary):
        _fail("Нет ответа от локального модуля. Данные сервера не изменены либо операция требует проверки.")
        return
    if not bool(reply.get("ok", false)):
        _fail(str(reply.get("error", "Сервер отклонил операцию.")))
        return
    var data: Dictionary = reply.get("data", {})
    match action:
        "probe":
            var details: Dictionary = data.get("details", {})
            var key: String = str(details.get("fingerprint", ""))
            if str(details.get("trusted", "")) == "known":
                _log("SSH-ключ совпадает с ранее доверенным: " + key)
                _send_command("inspect")
            else:
                checked_fingerprint = key
                pin_dialog.dialog_text = ("SSH: " + str(fields["host"].text) +
                    "\nТип ключа: " + str(details.get("type", "")) +
                    "\nПРОВЕРЬТЕ ЧЕРЕЗ ПАНЕЛЬ VPS:\n" + key +
                    "\n\nВведите отпечаток точно. Не подтверждайте наугад.")
                pin_input.text = ""
                pin_dialog.popup_centered()
                status_label.text = "Требуется подтверждение SSH-отпечатка."
        "pin":
            _log("SSH-ключ закреплён. Неожиданная смена ключа заблокирует доступ.")
            _send_command("inspect")
        "inspect":
            checked = true
            last_config_fingerprint = JSON.stringify(_config())
            status_label.text = "Проверка завершена. Можно публиковать сайт."
            _log("Ubuntu / Apache / DNS проверены без изменений.")
            _log("API проверен" if bool(data.get("api", false)) else "API не указан в параметрах")
            _log("HTTPS сертификат найден" if bool(data.get("https_certificate", false)) else "Сертификат будет запрошен при публикации")
            rollback_button.disabled = not bool(data.get("existing_site", false))
        "publish", "rollback":
            checked = false
            status_label.text = "Готово: " + str(data.get("url", ""))
            _log("УСПЕХ: " + str(data.get("url", "")))
            _log("Перед следующим изменением выполните проверку повторно.")
    _update_buttons()
    # Rollback is only possible if a site existed in the inspect response.
    if action == "inspect":
        rollback_button.disabled = not bool(data.get("existing_site", false))


func _config() -> Dictionary:
    var c: Dictionary = {}
    for spec in FIELD_INFO:
        var name: String = spec[0]
        c[name] = str(fields[name].text).strip_edges()
    return c


func _send_command(action: String, extra: Dictionary = {}) -> void:
    if not ready or busy:
        return
    var config := _config()
    if action in ["publish", "rollback"] and (not checked or JSON.stringify(config) != last_config_fingerprint):
        _invalidate()
        _fail("Параметры изменились. Сначала повторно проверьте сервер.")
        return
    busy = true
    _update_buttons()
    var payload := {"config": config}
    payload.merge(extra)
    status_label.text = "Выполняется: " + action + "…"
    if action in ["publish", "rollback"]:
        _log("Публикация и HTTPS" if action == "publish" else "Откат предыдущей версии")
    elif action == "inspect":
        _log("Проверка Ubuntu, Apache, DNS, HTTPS и игрового API…")
    elif action == "probe":
        _log("Проверяем ключ SSH-сервера…")
    _request_local(action, payload)


func _check_server() -> void:
    checked = false
    _send_command("probe")


func _confirmed_pin() -> void:
    if pin_input.text.strip_edges() != checked_fingerprint:
        _fail("Отпечаток не подтверждён. Сервер не добавлен в доверенные.")
        return
    _send_command("pin", {"fingerprint": checked_fingerprint})


func _confirm_publish() -> void:
    if not checked:
        return
    pending_confirmation = "publish"
    confirm_dialog.dialog_text = ("Опубликовать статические файлы из «" + str(fields["source_folder"].text) +
        "» на https://" + str(fields["domain"].text) + "/?\n\n" +
        "Создаётся только отдельный vhost; игровой API не изменяется.\n" +
        "HTTPS и API проверяются до и после операции.")
    confirm_dialog.popup_centered()


func _confirm_rollback() -> void:
    if not checked or rollback_button.disabled:
        return
    pending_confirmation = "rollback"
    confirm_dialog.dialog_text = "Восстановить предыдущую версию сайта?\nИгровой API не изменяется."
    confirm_dialog.popup_centered()


func _confirmed() -> void:
    if pending_confirmation not in ["publish", "rollback"]:
        return
    var selected := pending_confirmation
    pending_confirmation = ""
    _send_command(selected)


func _fail(text: String) -> void:
    busy = false
    checked = false
    indicator.color = GOLD
    status_label.text = text
    _log("ОШИБКА: " + text)
    _update_buttons()