from PyInstaller.utils.hooks import collect_all, copy_metadata

runtime_data, runtime_binaries, runtime_imports = collect_all('codex_cli_bin')
datas = runtime_data + copy_metadata('openai-codex') + copy_metadata('openai-codex-cli-bin')
a = Analysis(['src/main.py'], pathex=['src'], binaries=runtime_binaries, datas=datas,
             hiddenimports=runtime_imports, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['pytest', 'tkinter', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore'],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CodexDispatcher', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CodexDispatcher')
