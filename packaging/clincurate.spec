# PyInstaller spec: builds a double-click ClinCurate app (Windows .exe, macOS .app).
# Build from the repository root:  pyinstaller packaging/clincurate.spec
#
# macOS uses a folder-style .app (not one-file): Apple notarization requires
# every binary inside the bundle to be signed in place, and PyInstaller 6
# deprecates one-file .app bundles. Set CODESIGN_IDENTITY to sign.
import os
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

if sys.platform == "darwin":
    identity = os.environ.get("CODESIGN_IDENTITY") or None
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="ClinCurate",
        console=False,
        upx=False,
        codesign_identity=identity,
        entitlements_file=str(root / "packaging" / "entitlements.plist") if identity else None,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="ClinCurate", upx=False)
    app = BUNDLE(
        coll,
        name="ClinCurate.app",
        bundle_identifier="edu.ucsf.clincurate",
        info_plist={"CFBundleShortVersionString": "0.1.0", "NSHighResolutionCapable": True},
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="ClinCurate",
        console=False,  # no terminal window; the app opens in the browser
        upx=False,
    )
