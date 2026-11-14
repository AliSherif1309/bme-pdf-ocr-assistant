"""Resolve OCR executables without requiring a particular installation folder."""
from pathlib import Path
import os
import shutil

def find_tool(environment, bundled, commands):
    override = os.environ.get(environment)
    if override:
        return str(Path(override).expanduser())
    for path in bundled:
        if Path(path).is_file():
            return str(path)
    for command in commands:
        found = shutil.which(command)
        if found:
            return found
    return str(bundled[0])

def poppler_directory(base):
    override = os.environ.get('POPPLER_PATH')
    if override:
        return override
    bundled = Path(base) / 'poppler' / 'bin'
    return str(bundled) if bundled.is_dir() else None
