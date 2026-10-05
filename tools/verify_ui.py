import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ["DAGLO_DATA_DIR"] = str(root / "build" / "ui-test-data")
from daglo_collector import App, FolderInfo
from folder_view import folder_key
from app_paths import atomic_json, data_dir

atomic_json(data_dir() / "config.json", {"auto_check_updates": False})

app = App()
app.update()
app.sort_var.set("이름 오름차순")
app.folders = [FolderInfo("강의 10", "url10", "10"), FolderInfo("강의 2", "url2", "2"), FolderInfo("강의 1", "url1", "1")]
app._render_folders()
assert app.selection_label.cget("text") == "3 / 3개 선택"
assert list(app.folder_rows) == ["1", "2", "10"]
app.folder_checkboxes[0].toggle()
assert app.selection_label.cget("text") == "2 / 3개 선택"
app.sort_var.set("이름 내림차순")
app._on_sort_change()
assert list(app.folder_rows) == ["10", "2", "1"]
assert {folder_key(f) for var, f in app.folder_vars if var.get()} == {"10", "2"}
app.search_var.set("강의 1")
assert app.selection_label.cget("text") == "2 / 3개 선택"
assert app.folder_total_label.cget("text") == "전체 3개 · 표시 2개"
app.clear_selection()
assert app.collect_btn.cget("state") == "disabled"
app.select_all()
assert app.collect_btn.cget("state") == "normal"
app.search_var.set("")
app.sort_var.set("선택한 폴더 먼저")
app.folder_vars[0][0].set(False)
app._selection_changed()
assert list(app.folder_rows) == ["1", "2", "10"]
# Rescanning preserves selections by folder ID, independent of order.
app.folders.reverse()
app._render_folders()
assert {folder_key(f) for var, f in app.folder_vars if var.get()} == {"1", "2"}
for geometry in ("1220x850", "1080x740"):
    app.geometry(geometry)
    app.update()
    for name in ("selection_label", "search_entry", "sort_menu", "collect_btn", "update_btn", "log_box"):
        widget = getattr(app, name)
        x = widget.winfo_rootx() - app.winfo_rootx()
        y = widget.winfo_rooty() - app.winfo_rooty()
        assert x >= 0 and y >= 0 and x + widget.winfo_width() <= app.winfo_width() + 1 and y + widget.winfo_height() <= app.winfo_height() + 1, (geometry, name, x, y, widget.winfo_width(), widget.winfo_height())
app.after(400, app.quit)
app.mainloop()
app.destroy()
print("GUI checks passed: ordering, counts, filtering, preserved selection, rescan and layout at both window sizes")
