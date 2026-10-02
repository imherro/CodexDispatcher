"""Explicit, isolated live SDK spike. Never sends to pre-existing threads."""
import argparse
import json
import tempfile
import threading
from pathlib import Path
from importlib.metadata import version

from openai_codex import Codex, ApprovalMode, Sandbox
from openai_codex.client import CodexClient
from openai_codex.generated.v2_all import ThreadSourceKind


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-model', action='store_true')
    parser.add_argument('--output', default='docs/spike-result.json')
    args = parser.parse_args()
    report = {'sdk': version('openai-codex'), 'model_test': 'not_requested'}
    with Codex() as codex:
        report['server'] = codex.metadata.serverInfo.version
        account = codex.account().model_dump(mode='json', by_alias=True)
        report['authenticated'] = bool(account.get('account'))
        report['models'] = [m.model for m in codex.models().data]
        listing = codex.thread_list(cwd=str(Path.cwd()), source_kinds=list(ThreadSourceKind), limit=100)
        report['project_thread_count'] = len(listing.data)
        report['thread_fields'] = list(listing.data[0].model_dump(by_alias=True)) if listing.data else []
    if args.run_model:
        with tempfile.TemporaryDirectory(prefix='codex-dispatcher-spike-') as project:
            report['test_project'] = project
            marker = 'dispatcher-context-72941'
            with Codex() as first:
                thread = first.thread_start(cwd=project, sandbox=Sandbox.read_only,
                                            approval_mode=ApprovalMode.deny_all)
                thread.set_name('Codex Dispatcher / isolated SDK spike')
                report['test_thread_id'] = thread.id
                result = thread.run('This is an isolated SDK test. Do not use any tools. '
                                    f'Remember the marker {marker}. Reply only RECEIVED.')
                report['first_status'] = result.status.value
            with CodexClient() as reader:
                reader.initialize()
                report['persisted_cwd'] = str(reader.thread_read(thread.id).thread.cwd.root)
            with Codex() as second:
                resumed = second.thread_resume(thread.id)
                handle = resumed.turn('Do not use any tools. Return the exact marker from my previous message.')
                # Read the same test thread from a distinct runtime while the turn is active.
                with CodexClient() as other:
                    other.initialize()
                    state = other.thread_read(thread.id, include_turns=True).model_dump(mode='json', by_alias=True)
                    report['other_client_status_during_turn'] = state['thread']['status']
                    report['other_client_turn_statuses'] = [t['status'] for t in state['thread'].get('turns', [])]
                result = handle.run()
                report['second_status'] = result.status.value
                report['context_preserved'] = marker in (result.final_response or '')
                report['same_thread_id'] = resumed.id == thread.id
                report['model_test'] = 'passed' if report['context_preserved'] else 'failed'
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
