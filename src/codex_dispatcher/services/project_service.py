import os
from pathlib import Path
import shutil
import subprocess

from codex_dispatcher.domain.models import DispatchError
from .security import redact


def inspect_project(path):
    directory = Path(path).resolve()
    if not directory.is_dir():
        raise DispatchError('目标目录不存在')
    result = {'path': str(directory), 'git': False, 'remote': '', 'branch': ''}
    git = shutil.which('git')
    if not git:
        result['note'] = '未找到 git（目标目录不强制要求为 Git repository）'
        return result
    def command(*args):
        try:
            process = subprocess.run([git, '-C', str(directory), *args], capture_output=True, text=True,
                                     encoding='utf-8', errors='replace', timeout=10,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            return redact(process.stdout.strip()) if process.returncode == 0 else ''
        except (OSError, subprocess.TimeoutExpired):
            return ''
    result['git'] = command('rev-parse', '--is-inside-work-tree') == 'true'
    if result['git']:
        result['remote'] = command('remote', 'get-url', 'origin') or '无 origin remote'
        result['branch'] = command('branch', '--show-current') or 'detached / unborn'
    else:
        result['note'] = '不是 Git repository（允许继续配置）'
    return result
