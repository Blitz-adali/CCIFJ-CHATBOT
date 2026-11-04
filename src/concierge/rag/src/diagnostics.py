"""Bounded retrieval diagnostics without queries, exception messages or secrets."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import threading
import traceback

PATH = Path(__file__).resolve().parents[4] / 'var' / 'retrieval-health.json'
_lock = threading.Lock()
_state = {}
_persist = False
logger = logging.getLogger(__name__)


def record(stage, error=None):
    if stage not in {'embedding', 'tokenizer', 'semantic', 'lexical', 'rerank'}:
        return
    with _lock:
        previous = _state.get(stage, {})
        entry = {**previous, 'status': 'error' if error else 'ok',
                 'checked_at': datetime.now(timezone.utc).isoformat(),
                 'successes': previous.get('successes', 0) + int(error is None),
                 'failures': previous.get('failures', 0) + int(error is not None)}
        if error:
            frames = traceback.extract_tb(error.__traceback__)[-8:]
            entry['last_error'] = {'type': type(error).__name__,
                                   'frames': [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno} for f in frames]}
        _state[stage] = entry
        if not _persist:
            return
        try:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = PATH.with_suffix('.tmp')
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(_state, stream, indent=2)
            temporary.replace(PATH)
        except OSError:
            # Diagnostics cannot turn a successful lookup into a failed turn.
            logger.warning('Could not persist retrieval diagnostics')


def enable_persistence():
    global _persist
    _persist = True


def snapshot():
    with _lock:
        public = json.loads(json.dumps(_state))
        for entry in public.values():
            if 'last_error' in entry:
                entry['last_error'] = {'type': entry['last_error']['type']}
        return public
