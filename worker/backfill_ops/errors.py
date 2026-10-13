"""Why HB-6 refused to act."""


class OpsRefused(Exception):
    """A precondition of an HB-6 command or wake-up failed before anything was written."""

    def __init__(self, refusals):
        super().__init__("; ".join(f"{code}: {msg}" for code, msg in refusals))
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]
