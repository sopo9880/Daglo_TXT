from pathlib import Path
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas, binaries, hiddenimports = [], [], []
for package in ("playwright", "customtkinter"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
if not (root / "browsers").is_dir():
    raise RuntimeError("Install bundled browsers first: PLAYWRIGHT_BROWSERS_PATH=browsers python -m playwright install chromium")
datas += [(str(root / "browsers"), "browsers"), (str(root / "assets"), "assets")]
a = Analysis([str(root / "daglo_collector.py")], pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DagloTXT", debug=False,
          strip=False, upx=False, console=False, icon=str(root / "assets" / "app.ico"), disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DagloTXT")
