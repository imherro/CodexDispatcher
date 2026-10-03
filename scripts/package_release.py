"""Package the built folder and public documentation; never copies local app data."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import zipfile
from codex_dispatcher import __version__

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--folder', default=str(root / 'dist' / 'CodexDispatcher'))
args = parser.parse_args()
folder = Path(args.folder).resolve()
executable = folder / 'CodexDispatcher.exe'
if not executable.is_file():
    raise SystemExit('Run PyInstaller first')
shutil.copy2(root / 'README.md', folder / 'README.md')
docs = folder / 'docs'
docs.mkdir(exist_ok=True)
for name in (f'release-{__version__}.md', 'notification-acceptance-result.json', 'mention-acceptance-result.json', 'desktop-bridge-acceptance-result.json', 'packaged-smoke-result.json', 'gui-acceptance-result.json'):
    source = root / 'docs' / name
    if source.is_file():
        shutil.copy2(source, docs / name)
images = docs / 'screenshots'
images.mkdir(exist_ok=True)
for name in ('main-window.png', 'worker-config.png', 'worker-refresh-error.png', 'notification-detail.png'):
    shutil.copy2(root / 'docs' / 'screenshots' / name, images / name)
archive = folder.parent / f'CodexDispatcher-{__version__}-windows-x64.zip'
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
    for path in sorted(folder.rglob('*')):
        if path.is_file():
            output.write(path, path.relative_to(folder.parent))
def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()
report = {'version':__version__, 'executable_sha256':digest(executable), 'archive_sha256':digest(archive),
          'archive_bytes':archive.stat().st_size, 'folder_bytes':sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())}
(root / 'docs' / 'build-result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
