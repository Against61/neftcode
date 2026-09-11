"""Preflight diagnostics for a configured local operator console."""
import importlib.metadata
from pathlib import Path
import platform
import sys
import tempfile

from . import __version__
from .agent_tool import strict_loads
from .history_adapter import sha
from .operator_console import POLICY
from .operator_runtime import IsolatedOperatorConsoleRuntime


def _inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError('SOURCE_OUTSIDE_DATA_ROOT_OR_MISSING: ' + str(relative))
    return path


def run_doctor(data_root, sources_path, *, as_of=None):
    """Validate environment, pinned sources and one isolated strict replay.

    The returned report contains metadata and counts only.  Historical values
    stay inside the disposable worker audit and are not printed by the doctor.
    """
    report = {
        'schema': 'neft-doctor-v1',
        'status': 'failed',
        'project_version': __version__,
        'python': platform.python_version(),
        'python_supported': sys.version_info >= (3, 12),
        'checks': [],
        'errors': [],
    }
    try:
        if not report['python_supported']:
            raise ValueError('PYTHON_3_12_OR_NEWER_REQUIRED')
        root = Path(data_root).resolve()
        config_path = Path(sources_path).resolve()
        if not root.is_dir():
            raise ValueError('DATA_ROOT_NOT_DIRECTORY')
        config = strict_loads(config_path.read_text())
        if (not isinstance(config, dict) or config.get('schema') != 'history-sources-v1' or
                set(config) != {'schema', 'telemetry', 'lims'}):
            raise ValueError('HISTORY_SOURCE_CONFIG_SCHEMA')

        report['data_root'] = str(root)
        report['sources_path'] = str(config_path)
        report['sources'] = {}
        for name in ('telemetry', 'lims'):
            spec = config.get(name)
            if (not isinstance(spec, dict) or set(spec) != {'path', 'sha256'} or
                    not isinstance(spec.get('path'), str) or
                    not isinstance(spec.get('sha256'), str)):
                raise ValueError(name.upper() + '_SOURCE_SCHEMA')
            path = _inside(root, spec['path'])
            actual = sha(path)
            if actual != spec['sha256']:
                raise ValueError(name.upper() + '_HASH_MISMATCH')
            report['sources'][name] = {
                'relative_path': spec['path'],
                'bytes': path.stat().st_size,
                'sha256': actual,
                'hash_matches': True,
            }
        report['checks'].append('PINNED_SOURCES_PRESENT_AND_HASHED')

        origin = as_of or POLICY['demo_origins'][0]
        with tempfile.TemporaryDirectory(prefix='neft-doctor-') as audit:
            runtime = IsolatedOperatorConsoleRuntime(audit, root, config_path)
            probe = runtime.audited_call('history', {
                'as_of': origin, 'use_lims_upper_bound': False,
            })
            if probe['is_error']:
                raise ValueError('RUNTIME_PROBE_FAILED: ' + '; '.join(
                    probe['output'].get('reasons', ['UNKNOWN'])))
            snapshot = probe['output']
            if snapshot.get('selected_lims'):
                raise ValueError('STRICT_PROBE_EXPOSED_LIMS')
            report['runtime'] = runtime.health()
            report['probe'] = {
                'as_of': snapshot['as_of'],
                'status': snapshot['decision']['status'],
                'scope': snapshot['scope'],
                'telemetry_channels': len(snapshot['telemetry']['channels']),
                'selected_lims_count': 0,
                'worker_isolated': snapshot['runtime']['mode'] == 'isolated_subprocess',
            }
        report['checks'].extend([
            'SOURCE_FORMAT_AND_TIME_WINDOW_READABLE',
            'STRICT_LIMS_NONDISCLOSURE',
            'ISOLATED_WORKER_COMPLETED',
        ])
        dependencies = {}
        for name in ('numpy', 'mcp'):
            try:
                dependencies[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                dependencies[name] = None
        report['dependencies'] = dependencies
        report['status'] = 'passed'
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as exc:
        report['errors'].append(str(exc))
    return report
