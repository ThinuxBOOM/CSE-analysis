"""Why HB-5 refused to act."""


class F6Refused(Exception):
    """A precondition of HB-5 failed before anything was written."""

    def __init__(self, refusals):
        super().__init__("; ".join(f"{code}: {msg}" for code, msg in refusals))
        self.refusals = list(refusals)

    @property
    def codes(self):
        return [c for c, _ in self.refusals]
