"""Per-launch Data Out settings. Independent of optional controller haptics."""
import ipaddress
import json
from pathlib import Path


def environment(settings, directory=None):
    env = {'FORZA_DATA_OUT_ENABLE': '0', 'FORZA_DATA_OUT_IP': '127.0.0.1',
           'FORZA_DATA_OUT_PORT': '5300', 'FORZA_DATA_OUT_RATE': '60', 'FORZA_DATA_OUT_FORMAT': 'horizon'}
    if settings.get('data_out', False) is not True:
        return env  # Explicitly suppress destinations inherited from another session.
    try:
        address = ipaddress.IPv4Address(str(settings.get('data_out_ip', '127.0.0.1')).strip())
        port = int(settings.get('data_out_port', 5300))
        rate = int(settings.get('data_out_rate', 60))
        form = settings.get('data_out_format', 'horizon')
        if not 1 <= port <= 65535 or not 1 <= rate <= 60 or form not in ('horizon', 'fm7'):
            raise ValueError()
        if address.is_unspecified or address.is_multicast or int(address) == 0xffffffff:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Data Out needs a numeric IPv4 destination, port 1–65535, and rate 1–60 Hz.') from None
    if directory is not None:
        try:
            metadata = json.loads((Path(directory) / 'forza-telemetry.json').read_text(encoding='utf-8'))
            supported = isinstance(metadata, dict) and metadata.get('data_out') == 1
        except (ValueError, OSError):
            supported = False
        if not supported:
            raise RuntimeError('Rebuild the game in Setup > Build to add Data Out, or turn Data Out off.')
    env.update(FORZA_DATA_OUT_ENABLE='1', FORZA_DATA_OUT_IP=str(address), FORZA_DATA_OUT_PORT=str(port),
               FORZA_DATA_OUT_RATE=str(rate), FORZA_DATA_OUT_FORMAT=form)
    return env
