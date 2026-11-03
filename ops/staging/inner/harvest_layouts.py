"""Runner side of the staging seed (docs/ops/STAGING.md, ST-23): the synthetic F4 word layers the frozen F4 tests build
(tests/rf4_support.harvest, which imports the F4 test module and therefore pytest), pickled for
ops/staging/seed_synthetic.py in the host container, which carries no test tooling. Synthetic only: no CSE document or
response is involved.

    python3 harvest_layouts.py <app> <out.pkl>
"""
import os
import pickle
import sys

app, out = sys.argv[1], sys.argv[2]
sys.path[:0] = [app, os.path.join(app, "tests")]

import rf4_support as R  # noqa: E402

layers = [(name, words) for name, words, _cls, _kw in R.harvest()]
with_cells = [name for name, words in layers if R.cells(R.chain(words, 1)[1])]
with open(out, "wb") as f:
    pickle.dump({"layers": layers, "with_cells": with_cells}, f)
print(f"harvested {len(layers)} synthetic layouts, {len(with_cells)} with cells -> {out}")
