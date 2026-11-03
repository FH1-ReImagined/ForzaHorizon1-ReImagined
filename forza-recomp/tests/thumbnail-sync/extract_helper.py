"""Compile the production helper with controlled timer/clock services, without a game/SDK."""
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text()
body = source[source.index("constexpr auto kWindow"):]
services = {
    "std::thread([generation]": "FakeTimer([generation]",
    "std::this_thread::sleep_for(kWindow);": "/* timer dispatched by fixture */",
    "std::chrono::steady_clock::now()": "FakeNow()",
}
for original, fake in services.items():
    if body.count(original) != 1:
        raise ValueError(f"production timer/clock service changed: {original}")
    body = body.replace(original, fake)
Path(sys.argv[2]).write_text(body)
