# PyInstaller spec: builds a double-click ClinCurate app (Windows .exe, macOS .app).
# Build from the repository root:  pyinstaller packaging/clincurate.spec
import sys
from pathlib import Path

root = Path(SPECPATH).parent
pkg = root / "clincurate"

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(root)],
    datas=[
        (str(pkg / "schemas"), "clincurate/schemas"),
        (str(pkg / "web" / "templates"), "clincurate/web/templates"),
        (str(pkg / "web" / "static"), "clincurate/web/static"),
    ],
    hiddenimports=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="ClinCurate",
    console=False,  # no terminal window; the app opens in the browser
    upx=False,
)
if sys.platform == "darwin":
    app = BUNDLE(exe, name="ClinCurate.app", bundle_identifier="edu.ucsf.clincurate")
