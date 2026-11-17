"""Canonical text artifacts (§7.1). A model is a JSON document defined by this project, with sorted keys and floats
written by repr (shortest round-trip), never a language-level pickle; its SHA-256 is its identity. A changed byte is
refused (MLK-16). The environment tuple records library versions, the CPU feature set, the thread count (1) and the
seed: the same bytes in that environment give the same predictions to 9 decimal places, and retraining there
reproduces the hash (MLK-11). Identical bytes across hosts are not claimed."""
import hashlib
import json
import platform
from dataclasses import dataclass


class Refused(ValueError):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _cpu_flags():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("flags"):
                    return hashlib.sha256(" ".join(sorted(line.split(":", 1)[1].split())).encode()).hexdigest()[:16]
    except OSError:
        pass
    return platform.machine()


def environment(seed):
    import lightgbm
    import numpy
    import scipy
    import sklearn
    from threadpoolctl import threadpool_info
    blas = sorted({f"{i.get('internal_api')} {i.get('version')}" for i in threadpool_info()})
    return {"python": platform.python_version(), "numpy": numpy.__version__, "scipy": scipy.__version__,
            "scikit-learn": sklearn.__version__, "lightgbm": lightgbm.__version__, "cpu": _cpu_flags(),
            "blas": blas, "threads": 1, "seed": seed}


def one_thread():
    """Enforce the one thread the environment tuple records (first M5-4 audit, minor): BLAS and OpenMP pools."""
    from threadpoolctl import threadpool_limits
    return threadpool_limits(limits=1)


def dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


@dataclass(frozen=True)
class Artifact:
    adapter: str
    text: str
    sha256: str

    @classmethod
    def make(cls, adapter, body):
        text = dumps({"adapter": adapter, "body": body})
        return cls(adapter, text, hashlib.sha256(text.encode("utf-8")).hexdigest())

    def load(self, expected_adapter):
        """The body, after checking the bytes against the identity (MLK-16) and the adapter."""
        if hashlib.sha256(self.text.encode("utf-8")).hexdigest() != self.sha256:
            raise Refused("artifact_integrity", "the artifact's bytes do not match its SHA-256")
        doc = json.loads(self.text)
        if doc.get("adapter") != expected_adapter:
            raise Refused("artifact_adapter", f"a {doc.get('adapter')} artifact given to {expected_adapter}")
        return doc["body"]


@dataclass(frozen=True)
class Predictions:
    """Per row: direction probabilities (rise, flat, fall) or None, the class, the return, the volatility, an
    interval (low, high) or None, and contributions {output: {feature: value}} or None. None means the adapter is
    silent on that row for that output (§7.3)."""
    keys: list
    probs: list
    direction: list
    ret: list
    volatility: list
    interval: list
    contributions: list


def q9(x):
    """The 9-decimal quantisation the reproducibility claim is stated in (§7.1)."""
    return None if x is None else round(float(x), 9)
