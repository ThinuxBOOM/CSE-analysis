"""
Secret redaction for everything the P1 tools print, log or store in the backup ledger. Secret VALUES (off-site
repository string, password, backend credentials) are read from their files only to be masked here; the files
themselves are handed to tools by path, never copied into arguments or logs.
"""
import re

MASK = "***"
_URL_USERINFO = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/\s@]+)@", re.I)
_ASSIGNMENT = re.compile(r"(?P<key>\b[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|ACCESS_KEY|PRIVATE_KEY|API_KEY)[A-Z0-9_]*)"
                         r"(?P<sep>\s*[=:]\s*)(?P<val>[^\s'\"]+)", re.I)


class Redactor:
    def __init__(self, secrets=()):
        # longest first, so a secret containing another secret is masked whole
        self.secrets = sorted({s for s in secrets if isinstance(s, str) and len(s.strip()) >= 4}, key=len, reverse=True)

    def add(self, *values):
        return Redactor(tuple(self.secrets) + tuple(values))

    def __call__(self, text):
        if text is None:
            return None
        out = str(text)
        for s in self.secrets:
            out = out.replace(s, MASK)
        out = _URL_USERINFO.sub(lambda m: m.group("scheme") + MASK + "@", out)
        out = _ASSIGNMENT.sub(lambda m: m.group("key") + m.group("sep") + MASK, out)
        return out

    def obj(self, value):
        """Redacts every string inside a JSON-like structure."""
        if isinstance(value, str):
            return self(value)
        if isinstance(value, dict):
            return {k: self.obj(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.obj(v) for v in value]
        return value


def secrets_from_env_file(path):
    """(env dict, secret values) from a KEY=VALUE file; comments and blank lines ignored; quotes stripped."""
    env, values = {}, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k:
                env[k] = v
                values.append(v)
    return env, values
