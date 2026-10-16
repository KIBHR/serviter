extends SceneTree
## Godot 4.7 runtime regression: header must not wrap into vertical letters.
## Runs without launching the SSH helper or contacting a server.


func _initialize() -> void:
    call_deferred("_run")


func _run() -> void:
    var scene_script := load("res://ui/main.gd")
    var ui: Control = scene_script.new()
    ui.size = Vector2(1600, 900)
    ui.call("_draw_interface")
    var logo: Label = null
    var tagline: Label = null
    var hint: Label = null
    for candidate in ui.find_children("*", "Label", true, false):
        var label := candidate as Label
        if label.text == "SERVITER":
            logo = label
        elif label.text == "LOCAL DEPLOY / GODOT 4":
            tagline = label
        elif label.text.begins_with("Шифрованный SSH/SFTP"):
            hint = label
    if logo == null or tagline == null or hint == null:
        push_error("Expected Serviter header and hint labels were not created")
        quit(1)
        return
    if logo.autowrap_mode != TextServer.AUTOWRAP_OFF or tagline.autowrap_mode != TextServer.AUTOWRAP_OFF:
        push_error("Top bar labels must never wrap into single-letter columns")
        quit(1)
        return
    if hint.autowrap_mode == TextServer.AUTOWRAP_OFF:
        push_error("Long help text should wrap naturally")
        quit(1)
        return
    var field_grid: GridContainer = ui.get("field_grid")
    var action_grid: GridContainer = ui.get("action_grid")
    if field_grid == null or action_grid == null:
        push_error("Missing responsive layout containers")
        quit(1)
        return
    ui.call("_update_responsive_layout")
    if field_grid.columns != 2 or action_grid.columns != 3:
        push_error("Desktop columns are configured incorrectly")
        quit(1)
        return
    ui.size = Vector2(800, 700)
    ui.call("_update_responsive_layout")
    if field_grid.columns != 1 or action_grid.columns != 1:
        push_error("Small-screen layout must stack labels, fields and buttons")
        quit(1)
        return
    ui.free()
    print("Serviter UI layout smoke test passed")
    quit(0)