"""DE-4: one run end to end: load the MF snapshot, the links and the F8 results; assemble; write in one
transaction. The configurations are content-addressed and inserted if absent.

FL-10 (design §12; branch review, Phase 4 M2): in KNOWN a run uses the configurations in force at T: where the owner
has designated an F7, DE or MF configuration at or before T, the run is refused unless it is the one this code
implements (or, for MF, the snapshot's). In AVAILABLE the F7, DE and F8 configuration ids are passed explicitly and
must be the code's; a later designation changes nothing in either mode."""
from worker.financial_features import panel as pn

from . import assemble, codec, loader, writer


class NotInForce(ValueError):
    pass


def in_force(conn, table, at):
    """The configuration id designated (purpose canonical) by the latest row recorded at or before `at`, or None."""
    with conn.cursor() as cur:
        cur.execute(f"select configuration_id from {table} where purpose = 'canonical' and recorded_at <= %s "
                    "order by recorded_at desc, id desc limit 1", (at,))
        row = cur.fetchone()
    conn.rollback()
    return row[0] if row else None


def _check_configurations(conn, head, code, passed):
    """code: {"f7": id, "de": id}; passed: {"f7", "de", "f8"} ids given by the caller (or None)."""
    if head["mode"] == "AVAILABLE":
        missing = [k for k in ("f7", "de", "f8") if not passed.get(k)]
        if missing:
            raise NotInForce(f"an AVAILABLE run names its configurations explicitly: {missing} missing (FL-10)")
    else:
        for kind, table in (("f7", "f7_designations"), ("de", "de_designations")):
            d = in_force(conn, table, head["cutoff"])
            if d is not None and d != code[kind]:
                raise NotInForce(f"the {kind} configuration in force at T is {d}; this code implements {code[kind]}")
        d = in_force(conn, "mf_designations", head["cutoff"])
        if d is not None and d != head["configuration_id"]:
            raise NotInForce(f"the MF configuration in force at T is {d}; the snapshot is of {head['configuration_id']}")
    for kind in ("f7", "de"):
        if passed.get(kind) and passed[kind] != code[kind]:
            raise NotInForce(f"the {kind} configuration {passed[kind]} is not the one this code implements")


def run(conn, snapshot_id, *, f8_configuration_id=None, f7_configuration_id=None, de_configuration_id=None,
        status="provisional"):
    head, rows = loader.snapshot(conn, snapshot_id)
    code = {"f7": codec.f7_configuration(status)[0], "de": codec.de_configuration(status)[0]}
    _check_configurations(conn, head, code, {"f7": f7_configuration_id, "de": de_configuration_id,
                                             "f8": f8_configuration_id})
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
