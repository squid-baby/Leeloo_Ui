#!/usr/bin/env python3
"""
LEELOO file helpers — power-safe JSON writes

The gadget can lose power at any moment. Writing a JSON file in place
('w' mode) truncates it first, so a power cut mid-write leaves an empty or
half-written file. load_json() then returns {} and the device forgets its
setup, its Spotify tokens, or its messages.

atomic_write_json() writes to a temp file in the same directory, fsyncs it,
then os.replace()s it over the target. Readers see either the old file or
the new one, never a partial one.
"""

import json
import os
import tempfile


def atomic_write_json(path, data, indent=2, mode=None):
    """Write data as JSON to path atomically.

    mode: file permission bits for a new file (e.g. 0o600 for secrets).
          An existing file keeps its current permissions unless mode is given.
    """
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = 0o644

    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    # Persist the rename itself (directory entry) — best effort
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
