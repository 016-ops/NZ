"""Fail-closed retail adapter boundary. No PH constants are imported."""
import json
from pathlib import Path


class CompatibilityBlocked(Exception):
    pass


class ClientProfile:
    def __init__(self, path: Path):
        self.data = json.loads(path.read_text(encoding='utf-8'))

    def require(self, capability: str):
        if self.data['capabilities'].get(capability) is not True:
            raise CompatibilityBlocked(f'{capability}: no current-client evidence')

    def decode(self, frame: bytes):
        if len(frame) > 65536:
            raise ValueError('Frame exceeds research input limit')
        raise CompatibilityBlocked('No verified framing, encoding, or message schemas')
