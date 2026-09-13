import json
import threading
from pathlib import Path

from ..models import AuditRecord

_lock = threading.Lock()


class AuditLogger:
    """Append-only JSON-Lines audit log. Every chat decision is logged with
    the rule version applied, the citations used to ground the answer, and
    the latency, so any eligibility decision can be reconstructed and
    reviewed later.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, record: AuditRecord) -> None:
        line = record.model_dump_json()
        with _lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        return [json.loads(line) for line in lines]
