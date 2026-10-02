from PyInstaller.utils.hooks import collect_all, copy_metadata

runtime_data, runtime_binaries, runtime_imports = collect_all('codex_cli_bin')
datas = runtime_data + copy_metadata('openai-codex') + copy_metadata('openai-codex-cli-bin')
a = Analysis(['src/main.py'], pathex=['src'], binaries=runtime_binaries, datas=datas,
             hiddenimports=runtime_imports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'tkinter', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore'],
             noarchive=False)
# Qt 6.11 wheels use Windows' system ICU (unversioned exports). PyInstaller's
# PATH scan can pick an unrelated runtime's ICU 78 with suffixed exports instead.
# Leave these OS libraries to Windows, matching the installed PySide6 wheel.
a.binaries = [entry for entry in a.binaries if entry[0].lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CodexDispatcher', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CodexDispatcher')
