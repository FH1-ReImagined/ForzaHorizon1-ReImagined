"""Persistent preparation diagnostics shared by Build and Play."""
import json
import os
from pathlib import Path
import tempfile

FILE = "native-tuning-status.json"
ERROR_PREFIX = "NATIVE_TUNING_ERROR "
# Four 4,000-character fields can expand to 192,000 JSON characters when
# non-BMP Unicode is written as escaped UTF-16 surrogate pairs.
MAX_STATUS_CHARS = 200_000


def failure(code, reason, action, detail=""):
    return dict(schema=1, code=code, reason=reason, action=action, detail=detail[:4000])


def read(runtime):
    try:
        with (Path(runtime) / FILE).open(encoding="utf-8") as stream:
            value = json.loads(stream.read(MAX_STATUS_CHARS + 1))
        if (isinstance(value, dict) and value.get("schema") == 1 and
                all(isinstance(value.get(k), str) and len(value[k]) <= 4000
                    for k in ("code", "reason", "action", "detail"))):
            return value
    except (OSError, ValueError):
        pass
    return None


def write(runtime, value):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=runtime,
                                         prefix=".tuning-status-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=True)
            stream.write("\n")
        os.replace(temporary, runtime / FILE)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def summary(value):
    return f"Native car tuning unavailable: {value['reason']} {value['action']} Normal play is available."


def tool_failure(line):
    if not line.startswith(ERROR_PREFIX):
        return None
    try:
        value = json.loads(line[len(ERROR_PREFIX):])
        if (isinstance(value, dict) and
                all(isinstance(value.get(k), str) and len(value[k]) <= 4000
                    for k in ("code", "reason", "action", "detail"))):
            return failure(**{k: value[k] for k in ("code", "reason", "action", "detail")})
    except ValueError:
        pass
    return None
