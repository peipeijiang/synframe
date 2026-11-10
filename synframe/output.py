"""Atomic, non-overwriting output publication."""
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def new_output(destination, suffix):
    destination = Path(destination).expanduser().absolute()
    if destination.exists():
        raise ValueError("output already exists: " + str(destination))
    if not destination.parent.is_dir():
        raise ValueError("output directory does not exist")
    fd, name = tempfile.mkstemp(prefix=".synframe-", suffix=suffix, dir=str(destination.parent))
    os.close(fd)
    temp = Path(name)
    try:
        yield temp
        # Hard-link publishes atomically and refuses an existing destination.
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
