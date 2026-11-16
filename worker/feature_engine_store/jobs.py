"""DE-4: one run end to end: load the MF snapshot, the links and the F8 results; assemble; write in one
transaction. The configurations are content-addressed and inserted if absent."""
from worker.financial_features import panel as pn

from . import assemble, codec, loader, writer


def run(conn, snapshot_id, *, f8_configuration_id=None, status="provisional"):
    head, rows = loader.snapshot(conn, snapshot_id)
    decisions = loader.decisions(conn, [r.security.company_id for r in rows.values()])
    from .assemble import F8_MODE, _aware
    t, h = _aware(head["cutoff"]), _aware(head["horizon"])
    linked = {pn.security_link(decisions, r.security.company_id, F8_MODE[head["mode"]], t, h)
              for r in rows.values()}
    issuers = {lk.issuer_id for lk in linked if lk.state == pn.LINKED}
    res = loader.results(conn, issuers, mode=head["mode"], cutoff=head["cutoff"], horizon=head["horizon"],
                         f8_configuration_id=f8_configuration_id)
    a = assemble.assemble(mode=head["mode"], cutoff=head["cutoff"], horizon=head["horizon"],
                          snapshot_id=snapshot_id, rows=rows, results=res, decisions=decisions,
                          imported=head["imported"])
    return writer.write(conn, f7_config=codec.f7_configuration(status), de_config=codec.de_configuration(status),
                        panels=a.panels, run=a.run)
