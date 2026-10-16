# Serviter for Godot 4.7.2

Godot desktop edition of Serviter. The **editor/UI is written in GDScript**. SSH, strict host-key pinning, GitHub archive filtering, HTTPS provisioning, atomic releases and rollback reuse the existing tested Python deploy core via a **localhost-only authenticated process**.

## Why a separate Python helper?

Godot's standard GDScript runtime does not provide a supported SSH/SFTP implementation. Shelling out with SSH passwords in arguments, or rewriting Apache deployment safeguards in GDScript, would be unsafe. A separate native helper lets the Godot UI own presentation without running arbitrary shell scripts or storing credentials in project files.

The helper listens only on `127.0.0.1` at a random port. Godot generates a new cryptographically strong capability token each run. Every request must supply it in an Authorization header. The helper rejects browser Origin requests and unexpected Host headers. The capability appears in the local helper's process arguments, **not** in log files; users with local process-inspection access to the same computer should be treated as trusted. The connection is local unencrypted HTTP (loopback only). SSH credentials exist in RAM while a request runs; no settings or passwords are persisted by Serviter. The only persistent security state is pinned SSH host keys in `~/.serviter/known_hosts`.

## Open in Godot editor

Install Godot 4.7.2 standard edition and Python 3.11+ with packages from repo root `requirements.txt`. Open the **`godot/project.godot`** project in Godot and run it (`F5`). Godot starts `run_serviter_bridge.py` from the repository root in editor mode. If Python is not on PATH, start Godot from a terminal with your Python virtual environment active.

## Fullscreen display

Serviter starts in **fullscreen mode** (Godot `Window.MODE_FULLSCREEN`). Press **F11** or click **«Оконный режим · F11»** in the top bar to switch to windowed mode. Press **Esc** while fullscreen to leave fullscreen. The two configuration columns automatically stack vertically on narrower screens; scroll to reach all fields.

## Larger, readable type (v0.2.2)

All labels, fields, action buttons, dialog messages and operation logs use larger type and more legible contrast by default. The default Godot font size is set to **20 pt** in `godot/project.godot` under `[gui]`; programmatically created controls have explicit sizes in `godot/ui/main.gd`, including the `FONT_BODY`, `FONT_BUTTON` and `FONT_LOG` constants. The form remains scrollable, and on narrower screens both settings sections appear in one column.

## macOS app

GitHub Actions workflow **Check and build Serviter Godot for macOS** automatically checks Python, parses the Godot project with Godot 4.7.2, builds separate Apple Silicon and Intel PyInstaller SSH helpers, exports a Universal macOS `.app` using official templates and packages both architecture-specific helpers together. Download **Actions → latest successful run → Artifacts → Serviter-Godot-macOS**. Extract the archive and open `Serviter.app`.

The build is **not Developer ID notarized**. macOS Gatekeeper may block it until manually permitted in the system security settings. Do not blindly disable Gatekeeper. For software distribution to other people, properly sign and notarize the app.

## Safe usage

1. Enter SSH host, username and either password or key-file path; GitHub repository, branch, static site subfolder and domain.
2. Click **Проверить сервер**. For a new host, verify the displayed SSH SHA256 fingerprint using your provider's VPS console, not by copying it blindly, and type it in exactly.
3. Serviter performs a **read-only preflight** of Ubuntu, Apache, DNS and optionally the game's API.
4. After success, explicitly confirm **Опубликовать HTTPS**. Site files are filtered and transferred via SFTP, never to the API vhost. Rollback requires a fresh inspection.

**Scope:** static sites on Ubuntu/Apache. Not designed for deploying PHP backends, PostgreSQL, containers, Node servers or Godot game executable builds. For `cipher-breach`, choose `website` subfolder. This **does not connect to production automatically**, and it does **not** handle DNS updates at registrar.

### Development tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Run the Godot headless parse in CI (or locally with Godot 4.7.2):

```bash
godot --headless --path godot --editor --quit
```