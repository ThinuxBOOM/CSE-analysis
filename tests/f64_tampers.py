"""
The section 11.6 tamper catalogue of F6.4 (not a test module), shared by P18 (tests/test_f64_postgres.py: the database
alone must refuse every case) and U10 (tests/test_f64_unit.py: the writer's pre-insert Python mirror must refuse every
case too). Each case changes a genuine decomposition - rows of codec.validation_rows, or a partition plan of
jobs.plan_partition - in one precise way; many are CONSISTENT forgeries, changed identically at every level but one
(typed column, element and parent envelope, re-hashed) so that exactly one check can refuse them.
"""
import json
import os
import sys
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from worker.financial_truth import canonical  # noqa: E402
from worker.financial_truth_store import codec  # noqa: E402

D = Decimal
cj = canonical.canonical_json


def rescale(d):
    """The same number with one more decimal place (1234000 -> 1234000.0; 50.0 -> 50.00)."""
    s = format(d, "f")
    return D(s + ("0" if "." in s else ".0"))


def mut_json(row, col, mutate):
    before = row[col]
    e = json.loads(before)
    mutate(e)
    row[col] = cj(e)
    assert row[col] != before, f"no-op change of {col}"


def reenvelope(row, env_col, hash_col, mutate):
    """Change an envelope and re-hash it, so that the envelope hash guard cannot be what refuses."""
    mut_json(row, env_col, mutate)
    row[hash_col] = codec.sha256_hex(row[env_col])


def sub(text, old, new):
    assert old in text, (old, text[:300])
    return text.replace(old, new, 1)


def swap_ab(e):
    """A comparison element with its a and b sides exchanged (the pair reversed)."""
    for x, y in (("a", "b"), ("a_so", "b_so"), ("a_member", "b_member")):
        if x in e:
            e[x], e[y] = e[y], e[x]
    c = e.get("comparison")
    if c is not None:
        c["a_value"], c["b_value"] = c["b_value"], c["a_value"]
        c["a_half_unit"], c["b_half_unit"] = c["b_half_unit"], c["a_half_unit"]


def swap_ab_row(c):
    """The typed a / b columns of a T7 / T15 row exchanged (keys, ordinals and values)."""
    for x in ("so_key", "observation_ordinal", "candidate_validation_key", "member_ordinal", "value", "half_unit"):
        if f"a_{x}" in c:
            c[f"a_{x}"], c[f"b_{x}"] = c[f"b_{x}"], c[f"a_{x}"]


def exact(x):
    """A comparable form that keeps what Python's == hides: a Decimal's scale (1234000 == 1234000.0)."""
    if isinstance(x, Decimal):
        return ("Decimal", str(x))
    if isinstance(x, dict):
        return {k: exact(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [exact(v) for v in x]
    return x


def swap(rows, col):
    rows[0][col], rows[1][col] = rows[1][col], rows[0][col]


# decomposition accessors (rows of codec.validation_rows / codec.record_rows)

def so_with(r, n):
    return next(s for s in r["T5"] if s["member_count"] == n and s["observation_status"] == "consistent")


def numeric_so(r):
    return next(s for s in r["T5"] if s["value_kind"] == "numeric" and s["observation_status"] == "consistent")


def members_of(r, s):
    return sorted((m for m in r["T6"] if m["so_key"] == s["so_key"]), key=lambda m: m["member_ordinal"])


def pairs_of(r, s):
    return sorted((c for c in r["T7"] if c["so_key"] == s["so_key"]), key=lambda c: c["comparison_ordinal"])


def so_envelope(s, mutate):
    reenvelope(s, "so_json", "output_hash", mutate)


def item(p, state):
    return next(i for i in p.items if i["append"] and i["result"].state == state)


def reseal_record(p, it):
    """After a change to a record's envelope: its new output_hash, and E6 re-pointed to it (re-hashed)."""
    t13 = it["rows"][0]
    old, t13["output_hash"] = t13["output_hash"], codec.sha256_hex(t13["result_json"])

    def repoint(e):
        e["results"] = [[k, t13["output_hash"] if (k, h) == (t13["ef_key"], old) else h] for k, h in e["results"]]
    reenvelope(p.batch_row, "output_json", "output_hash", repoint)


# ------------------------------------------------------------------------------------------------ validation tampers (T1-T7)

def t2_extra(r):
    c = dict(r["T2"][0], candidate_ordinal=len(r["T2"]), value_ordinal=7)
    c["candidate_validation_key"] = codec.sha256_hex(codec.candidate_validation_key_text(
        r["T1"]["validation_run_key"], r["T1"]["f5_run_id"], c))
    r["T2"].append(c)


def t2_reordered_with_e1(r):
    a, b = r["T2"][0], r["T2"][1]
    a["candidate_ordinal"], b["candidate_ordinal"] = 1, 0
    reenvelope(r["T1"], "output_json", "output_hash",
               lambda e: e.update(candidates=[e["candidates"][1], e["candidates"][0]] + e["candidates"][2:]))


def t3_key(r):
    o = r["T3"][0]
    old, o["op1_key"] = o["op1_key"], "1" * 64
    o["op1_json"] = sub(o["op1_json"], old, o["op1_key"])


def t3_reverse_ids(r):
    o = r["T3"][0]
    assert len(o["validated_candidate_ids"]) >= 2
    o["validated_candidate_ids"] = list(reversed(o["validated_candidate_ids"]))


def t5_representative_copy_with_e3(r):
    s = so_with(r, 2)
    s["normalized_value"] += 1
    so_envelope(s, lambda e: e.update(normalized_value=format(s["normalized_value"], "f")))


def t5_reported_with_e3(r):
    s = so_with(r, 2)
    s["reported_raw_value"] = "9,999"
    so_envelope(s, lambda e: e["reported"].update(raw_value="9,999"))


def member_element(r, mutate):
    mut_json(members_of(r, so_with(r, 2))[0], "member_json", mutate)


def member_everywhere(r, typed, mutate):
    """One member changed identically in its typed columns, its element and the SO's E3 (re-hashed): only the
    cross-level check against its candidate validation / F5 row (EDI-3) can refuse it."""
    s = so_with(r, 2)
    m = members_of(r, s)[0]
    m.update(typed)
    mut_json(m, "member_json", mutate)
    so_envelope(s, lambda e: mutate(e["members"][m["member_ordinal"]]))


def t6_extra(r):
    s = so_with(r, 1)
    other = next(c for c in r["T2"] if c["admitted"] and c["ef_key"] != s["ef_key"])
    r["T6"].append(dict(members_of(r, s)[0], candidate_validation_key=other["candidate_validation_key"],
                        candidate_id=other["candidate_id"], member_ordinal=1))


def t6_swap(r):
    a, b = members_of(r, so_with(r, 2))
    a["member_ordinal"], b["member_ordinal"] = 1, 0


def members_reordered_everywhere(r):
    """The two members of an SO in reversed order, their pair reversed with them, in T6, T7 and E3: consistent at
    every level, so that only the canonical member order (EDI-5) is violated."""
    s = so_with(r, 2)
    a, b = members_of(r, s)
    a["member_ordinal"], b["member_ordinal"] = 1, 0
    (c,) = pairs_of(r, s)
    swap_ab_row(c)
    c["a_member_ordinal"], c["b_member_ordinal"] = 0, 1
    mut_json(c, "comparison_json", swap_ab)
    so_envelope(s, lambda e: e.update(members=[e["members"][1], e["members"][0]],
                                      comparisons=[json.loads(c["comparison_json"])]))


def t7(r):
    return pairs_of(r, so_with(r, 2))[0]


def t7_reversed_with_element(r):
    c = t7(r)
    swap_ab_row(c)
    mut_json(c, "comparison_json", swap_ab)


def t7_copy_everywhere(r):
    s = so_with(r, 2)
    c = t7(r)
    c["a_value"] += 1
    v = format(c["a_value"], "f")
    mut_json(c, "comparison_json", lambda e: e["comparison"].update(a_value=v))
    so_envelope(s, lambda e: e["comparisons"][0]["comparison"].update(a_value=v))


def m0(r, n=2):
    return members_of(r, so_with(r, n))[0]


def rekey_run(rows, *, vrk=None, admission_version=None):
    """The whole decomposition re-keyed under another validation-run key, every T2 / T5 key, envelope and copy
    recomputed from it (in place). With admission_version: a genuine run of another version set (its key IS the hash
    of its text). With vrk: a run whose key is NOT the hash of its text, so that only EDI-4 can see it."""
    r = rows
    t1 = r["T1"]
    if admission_version:
        t1["admission_version"] = admission_version
    new = vrk or codec.sha256_hex(codec.validation_run_key_text(t1))
    t1["validation_run_key"] = new
    remap = {}
    for c in r["T2"]:
        c["validation_run_key"] = new
        k = codec.sha256_hex(codec.candidate_validation_key_text(new, t1["f5_run_id"], c))
        remap[c["candidate_validation_key"]], c["candidate_validation_key"] = k, k
    reenvelope(t1, "output_json", "output_hash",
               lambda e: e.update(candidates=[[remap[a], b] for a, b in e["candidates"]]))
    for o in r["T3"]:
        o["validation_run_key"] = new
    for f in r["T4"]:
        f["first_validation_run_key"] = new
    so_remap = {}
    for s in r["T5"]:
        s["validation_run_key"] = new
        k = codec.sha256_hex(codec.so_key_text(s))
        so_remap[s["so_key"]], s["so_key"] = k, k
        if s["representative_candidate_validation_key"]:
            s["representative_candidate_validation_key"] = remap[s["representative_candidate_validation_key"]]

        def m(e, k=k):
            e.update(so_key=k, validation_run_key=new)
            e["versions"]["admission_version"] = t1["admission_version"]
            for mem in e["members"]:
                mem["candidate_validation_key"] = remap[mem["candidate_validation_key"]]
        so_envelope(s, m)
    for mem in r["T6"]:
        mem["so_key"] = so_remap[mem["so_key"]]
        mem["candidate_validation_key"] = remap[mem["candidate_validation_key"]]
        mut_json(mem, "member_json", lambda e, k=mem["candidate_validation_key"]: e.update(candidate_validation_key=k))
    for c in r["T7"]:
        c["so_key"] = so_remap[c["so_key"]]
        c["a_candidate_validation_key"] = remap[c["a_candidate_validation_key"]]
        c["b_candidate_validation_key"] = remap[c["b_candidate_validation_key"]]
    return r


def rekey_fact(rows, old, fact):
    """Fact `old` replaced by the T4 row `fact` in every reference (in place): T2 ef_key with its E2 and E1, the SO's
    ef_key, so_key and identity (E3), and its members' and pairs' so_key."""
    r, new = rows, fact["ef_key"]
    r["T4"] = [fact if f["ef_key"] == old else f for f in r["T4"]]
    hashes = {}
    for c in r["T2"]:
        if c["ef_key"] == old:
            c["ef_key"] = new
            reenvelope(c, "output_json", "output_hash", lambda e: e["admission"].update(ef_key=new))
            hashes[c["candidate_validation_key"]] = c["output_hash"]
    reenvelope(r["T1"], "output_json", "output_hash",
               lambda e: e.update(candidates=[[k, hashes.get(k, h)] for k, h in e["candidates"]]))
    so_remap = {}
    for s in r["T5"]:
        if s["ef_key"] == old:
            s["ef_key"] = new
            k = codec.sha256_hex(codec.so_key_text(s))
            so_remap[s["so_key"]], s["so_key"] = k, k
            so_envelope(s, lambda e, k=k: e.update(so_key=k, ef_key=new, identity=codec.identity_json(fact)))
    for x in r["T6"] + r["T7"]:
        x["so_key"] = so_remap.get(x["so_key"], x["so_key"])
    return r


def fake_ef_key_everywhere(r):
    old = so_with(r, 2)["ef_key"]
    rekey_fact(r, old, dict(next(f for f in r["T4"] if f["ef_key"] == old), ef_key="f" * 64))


def other_issuer_everywhere(r):
    """A consistent fact of ANOTHER issuer (its ef_key is the true hash of that identity) in place of the SO's own."""
    from f63_factories import OTHER_ISSUER
    old = so_with(r, 2)["ef_key"]
    fact = dict(next(f for f in r["T4"] if f["ef_key"] == old), issuer_id=OTHER_ISSUER)
    fact["ef_key"] = codec.sha256_hex(codec.ef_key_text(fact))
    rekey_fact(r, old, fact)


def admitted_outside_every_so(r):
    """An admitted candidate that is no SO's member: the non-representative member dropped from T6, T7 and E3
    alike (member_count 1), so that only the completeness check can see it."""
    s = so_with(r, 2)
    a, b = members_of(r, s)
    keep, drop = (a, b) if a["candidate_validation_key"] == s["representative_candidate_validation_key"] else (b, a)
    r["T6"] = [m for m in r["T6"] if m is not drop]
    r["T7"] = [c for c in r["T7"] if c["so_key"] != s["so_key"]]
    old = keep["member_ordinal"]
    keep["member_ordinal"] = 0
    s["member_count"] = 1
    so_envelope(s, lambda e: e.update(members=[e["members"][old]], comparisons=[]))


def t3_reordered_with_e1(r):
    a, b = r["T3"][0], r["T3"][1]
    a["op1_ordinal"], b["op1_ordinal"] = 1, 0
    reenvelope(r["T1"], "output_json", "output_hash", lambda e: e.update(op1=[e["op1"][1], e["op1"][0]]))


def t7_pairs_reordered_with_e3(r):
    s = so_with(r, 3)
    c0, c1 = pairs_of(r, s)[:2]
    c0["comparison_ordinal"], c1["comparison_ordinal"] = 1, 0
    so_envelope(s, lambda e: e.update(comparisons=[e["comparisons"][1], e["comparisons"][0]] + e["comparisons"][2:]))


def t5_other_twin(r):
    s = so_with(r, 2)
    a, b = members_of(r, s)
    s["representative_candidate_validation_key"] = (
        b if s["representative_candidate_validation_key"] == a["candidate_validation_key"] else a)[
        "candidate_validation_key"]


def t6_candidate_of_other_twin(r):
    """A member whose candidate_id is its twin's (an identical F5 value), in T6, its element and E3 alike."""
    s = so_with(r, 2)
    a, b = members_of(r, s)
    a["candidate_id"] = b["candidate_id"]
    mut_json(a, "member_json", lambda e: e.update(candidate_id=b["candidate_id"]))
    so_envelope(s, lambda e: e["members"][a["member_ordinal"]].update(candidate_id=b["candidate_id"]))


def t7_ab_swapped_with_e3(r):
    s = so_with(r, 2)
    c = t7(r)

    def sw(e):
        e["a"], e["b"] = e["b"], e["a"]
    mut_json(c, "comparison_json", sw)
    so_envelope(s, lambda e: sw(e["comparisons"][c["comparison_ordinal"]]))


def candidate_of_other_run(r, other_id):
    """T2's first admitted candidate pointed at `other_id`, a candidate with the SAME coordinates in another F5 run of
    the same document, in T2, E2 (and E1), T6, its element and E3 alike."""
    c = next(x for x in r["T2"] if x["admitted"])
    c["candidate_id"] = other_id
    reenvelope(c, "output_json", "output_hash", lambda e: e["validation"].update(candidate_id=other_id))
    reenvelope(r["T1"], "output_json", "output_hash", lambda e: e.update(
        candidates=[[k, c["output_hash"] if k == c["candidate_validation_key"] else h] for k, h in e["candidates"]]))
    for m in r["T6"]:
        if m["candidate_validation_key"] == c["candidate_validation_key"]:
            m["candidate_id"] = other_id
            mut_json(m, "member_json", lambda e: e.update(candidate_id=other_id))
            s = next(x for x in r["T5"] if x["so_key"] == m["so_key"])
            so_envelope(s, lambda e, i=m["member_ordinal"]: e["members"][i].update(candidate_id=other_id))
    return c


def representative_without_e3_member(r):
    """t2's ambiguous SO given a typed representative - member 0's F5 value and F6.1 value in the typed columns and
    in E3, the ambiguity annotation removed from both - while E3's representative_member stays null."""
    s = so_with(r, 3)
    m = members_of(r, s)[0]
    mj = json.loads(m["member_json"])
    rep = mj["reported"]
    s.update({"representative_candidate_validation_key": m["candidate_validation_key"],
              "reported_raw_value": rep["raw_value"],
              "reported_parsed_value": None if rep["parsed_value"] is None else D(rep["parsed_value"]),
              "reported_representation_class": rep["representation_class"],
              "reported_printed_decimals": rep["printed_decimals"], "reported_sign_as_printed": rep["sign_as_printed"],
              "reported_scale": rep["reported_scale"], "reported_scale_basis": rep["scale_basis"],
              "reported_currency": rep["currency"], "reported_value_type": rep["value_type"],
              "normalized_value": m["normalized_value"], "half_unit": m["half_unit"],
              "annotations": [a for a in s["annotations"] if a != "representative_ambiguous"]})
    so_envelope(s, lambda e: e.update(reported=rep, normalized_value=mj["value"]["normalized_value"],
                                      half_unit=mj["value"]["half_unit"], annotations=list(s["annotations"])))


VALIDATION_TAMPERS = [          # (name, document, change, the checks that may refuse it, phase)
    ("T1 key", "d1", lambda r: r["T1"].update(validation_run_key="3" * 64), ["EDI-4"], "insert"),
    ("T1 candidates_total", "d1", lambda r: r["T1"].update(candidates_total=r["T1"]["candidates_total"] + 1,
                                                           not_admitted=r["T1"]["not_admitted"] + 1), ["EDI-2"],
     "insert"),
    ("T1 op1_count", "d8", lambda r: r["T1"].update(op1_count=r["T1"]["op1_count"] + 1), ["EDI-2"], "insert"),
    ("T1 publication date", "d1", lambda r: r["T1"].update(
        publication_date=r["T1"]["publication_date"] + timedelta(days=1)), ["Asia/Colombo"], "insert"),
    ("T2 eligibility", "d1", lambda r: r["T2"][0].update(
        eligibility="ineligible" if r["T2"][0]["eligibility"] != "ineligible" else "eligible"), ["EDI-2"], "insert"),
    ("T2 reason", "d1", lambda r: r["T2"][0].update(ineligible_reasons=r["T2"][0]["ineligible_reasons"] + ["x"]),
     ["EDI-2"], "insert"),
    ("T2 nil form", "d1", lambda r: next(c for c in r["T2"] if c["admitted"]).update(
        nil_form=["reported_nil", "hyphen_minus"]), ["EDI-2"], "insert"),
    ("T2 key", "d1", lambda r: r["T2"][0].update(candidate_validation_key="0" * 64), ["EDI-4"], "insert"),
    ("T2 wrong ordinal", "d1", lambda r: r["T2"][0].update(candidate_ordinal=len(r["T2"]) + 3), ["EDI-1"], "commit"),
    ("T2 duplicate", "d1", lambda r: r["T2"].append(dict(r["T2"][0], candidate_ordinal=len(r["T2"]))), ["pkey"],
     "insert"),
    ("T2 extra", "d1", t2_extra, ["candidate validation"], "insert"),
    ("T2 missing", "d1", lambda r: r.update(T2=[c for c in r["T2"] if c["admitted"]]), ["EDI-1"], "commit"),
    ("T2 reordered", "d1", lambda r: swap(r["T2"], "candidate_ordinal"), ["EDI-1"], "commit"),
    ("T2 reordered with E1", "d1", t2_reordered_with_e1, ["EDI-5"], "commit"),
    ("T3 outcome in the element", "d8", lambda r: r["T3"][0].update(op1_json=sub(
        r["T3"][0]["op1_json"], '"outcome":"pass"', '"outcome":"fail"')), ["EDI-2"], "insert"),
    ("T3 tolerance re-scaled", "d8", lambda r: r["T3"][0].update(tolerance=rescale(r["T3"][0]["tolerance"])),
     ["EDI-2"], "insert"),
    ("T3 validated ids", "d8", t3_reverse_ids, ["EDI-3"], "commit"),
    ("T3 key", "d8", t3_key, ["EDI-4"], "insert"),
    ("T3 wrong ordinal", "d8", lambda r: r["T3"][0].update(op1_ordinal=5), ["EDI-1"], "commit"),
    ("T3 missing", "d8", lambda r: r.update(T3=[]), ["fk_fcv_op1"], "insert"),
    ("T5 annotation", "d1", lambda r: so_with(r, 2).update(annotations=["multiple_roles"]), ["EDI-2"], "insert"),
    ("T5 interval re-scaled", "d1", lambda r: so_with(r, 2).update(interval_low=rescale(so_with(r, 2)["interval_low"])),
     ["EDI-2"], "insert"),
    ("T5 value", "d1", lambda r: so_with(r, 2).update(normalized_value=so_with(r, 2)["normalized_value"] + 1),
     ["EDI-2"], "insert"),
    ("T5 nil kind", "d1", lambda r: so_with(r, 2).update(value_kind="nil"), ["EDI-2"], "insert"),
    ("T5 member_count", "d1", lambda r: so_with(r, 2).update(member_count=3), ["EDI-2"], "insert"),
    ("T5 reported", "d1", lambda r: so_with(r, 2).update(reported_raw_value="9,999"), ["EDI-3"], "insert"),
    ("T5 key", "d1", lambda r: so_with(r, 2).update(so_key="2" * 64), ["EDI-4"], "insert"),
    ("T5 representative copy with E3", "d1", t5_representative_copy_with_e3, ["EDI-3"], "commit"),
    ("T5 reported with E3", "d1", t5_reported_with_e3, ["EDI-3"], "insert"),
    ("T6 decimal re-scaled", "d1", lambda r: m0(r).update(normalized_value=rescale(m0(r)["normalized_value"])),
     ["EDI-2"], "insert"),
    ("T6 value", "d1", lambda r: m0(r).update(normalized_value=m0(r)["normalized_value"] + 1), ["EDI-2"], "insert"),
    ("T6 half-unit", "d1", lambda r: m0(r).update(half_unit=m0(r)["half_unit"] * 2), ["EDI-2"], "insert"),
    ("T6 role", "d1", lambda r: m0(r).update(role="comparative"), ["EDI-2"], "insert"),
    ("T6 restated in the element", "d1", lambda r: m0(r).update(member_json=sub(
        m0(r)["member_json"], '"restated":false', '"restated":true')), ["EDI-2"], "insert"),
    ("T6 nil kind", "d1", lambda r: m0(r, 1).update(value_kind="nil", normalized_value=None, half_unit=None),
     ["SO member"], "insert"),
    ("T6 key", "d1", lambda r: m0(r).update(candidate_validation_key=members_of(r, so_with(r, 2))[1][
        "candidate_validation_key"]), ["SO member", "EDI-2"], "insert"),
    ("T6 representation in the element", "d1", lambda r: member_element(r, lambda e: e["reported"].update(
        representation_class="x_" + e["reported"]["representation_class"])), ["EDI-3"], "insert"),
    ("T6 reported with E3", "d1", lambda r: member_everywhere(r, {}, lambda e: e["reported"].update(
        raw_value="1,235")), ["EDI-3"], "insert"),
    ("T6 value with E3", "d1", lambda r: member_everywhere(r, {"normalized_value": D("1234001")}, lambda e: e[
        "value"].update(normalized_value="1234001")), ["EDI-3"], "insert"),
    ("T6 nil form with E3", "d1", lambda r: member_everywhere(r, {}, lambda e: e.update(
        nil_form=["reported_nil", "hyphen_minus"])), ["EDI-3"], "insert"),
    ("T6 wrong ordinal", "d1", lambda r: m0(r, 1).update(member_ordinal=9), ["EDI-1"], "commit"),
    ("T6 missing", "d1", lambda r: r.update(T6=[m for m in r["T6"] if m["so_key"] != so_with(r, 1)["so_key"]]),
     ["completeness", "EDI-1"], "commit"),
    ("T6 duplicate", "d1", lambda r: r["T6"].append(dict(m0(r, 1), member_ordinal=1)), ["pkey"], "insert"),
    ("T6 extra", "d1", t6_extra, ["SO member"], "insert"),
    ("T6 reordered", "d1", t6_swap, ["fk_fsc_"], "insert"),
    ("T6 and T7 reordered with E3", "d1", members_reordered_everywhere, ["EDI-5"], "commit"),
    ("T7 tolerance", "d1", lambda r: t7(r).update(tolerance=t7(r)["tolerance"] + 1), ["EDI-2"], "insert"),
    ("T7 tolerance re-scaled", "d1", lambda r: t7(r).update(tolerance=rescale(t7(r)["tolerance"])), ["EDI-2"],
     "insert"),
    ("T7 difference", "d1", lambda r: t7(r).update(abs_difference=t7(r)["abs_difference"] + 1), ["EDI-2"], "insert"),
    ("T7 half-unit", "d1", lambda r: t7(r).update(b_half_unit=t7(r)["b_half_unit"] * 2), ["EDI-2"], "insert"),
    ("T7 sign-only", "d1", lambda r: t7(r).update(sign_only=not t7(r)["sign_only"]), ["EDI-2"], "insert"),
    ("T7 reason", "d1", lambda r: t7(r).update(reason="nil_vs_numeric"), ["EDI-2"], "insert"),
    ("T7 outcome", "d1", lambda r: t7(r).update(outcome="disagree"), ["EDI-2"], "insert"),
    ("T7 reversed", "d1", lambda r: swap_ab_row(t7(r)), ["EDI-3"], "insert"),
    ("T7 reversed with the element", "d1", t7_reversed_with_element, ["chk_fsc_element"], "insert"),
    ("T7 copied value with E3", "d1", t7_copy_everywhere, ["copy of a member value"], "insert"),
    ("T7 missing", "d1", lambda r: r.update(T7=[]), ["EDI-1", "EDI-5"], "commit"),
    ("T7 duplicate", "d1", lambda r: r["T7"].append(dict(t7(r), comparison_ordinal=1)), ["pkey"], "insert"),
    ("T7 wrong ordinal", "d1", lambda r: t7(r).update(comparison_ordinal=4), ["EDI-1"], "commit"),
    # consistent forgeries that isolate one rule each (added after the mutation audit)
    ("T1 key with every child re-keyed", "d1", lambda r: rekey_run(r, vrk="3" * 64), ["EDI-4"], "insert"),
    ("T1 admission counts", "d1", lambda r: r["T1"].update(admitted_numeric=r["T1"]["admitted_numeric"] - 1,
                                                           admitted_nil=r["T1"]["admitted_nil"] + 1),
     ["completeness"], "commit"),
    ("an admitted candidate in no SO, E3 consistent", "d1", admitted_outside_every_so, ["completeness"], "commit"),
    ("T4 ef_key with every reference re-keyed", "d1", fake_ef_key_everywhere, ["ef_key is not the f6.identity.1 hash"],
     "insert"),
    ("a fact of another issuer with every reference re-keyed", "d1", other_issuer_everywhere, ["issuer decision"],
     "insert"),
    ("T3 reordered with E1", "t1", t3_reordered_with_e1, ["EDI-5"], "commit"),
    ("T7 pairs reordered with E3", "t2", t7_pairs_reordered_with_e3, ["EDI-5"], "commit"),
    ("T5 representative is the other twin", "t3", t5_other_twin, ["EDI-2"], "commit"),
    ("T6 candidate of the other twin, E3 consistent", "t3", t6_candidate_of_other_twin, ["SO member"], "insert"),
    ("T7 a / b swapped in the element with E3", "d1", t7_ab_swapped_with_e3, ["EDI-3"], "insert"),
    ("T7 values on a nil pair", "d3", lambda r: pairs_of(r, so_with(r, 2))[0].update(tolerance=D(1),
                                                                                    abs_difference=D(0)),
     ["EDI-2"], "insert"),
    ("a typed representative without E3's representative member", "t2", representative_without_e3_member,
     ["the SO has a representative but E3 has none"], "commit"),
]


# ------------------------------------------------------------------------------------------------ record and batch tampers (T12-T16)

def t13(p):
    return item(p, "corroborated")["rows"][0]


def t14(p):
    return sorted(item(p, "corroborated")["rows"][1], key=lambda x: x["observation_ordinal"])


def t15(p, state="corroborated"):
    return sorted(item(p, state)["rows"][2], key=lambda x: x["comparison_ordinal"])


def record_everywhere(p, change, state="corroborated"):
    """change(t13, t14, t15, e4): on the typed rows and on E4 alike; then E4 and E6 re-hashed."""
    it = item(p, state)
    rt13, rt14, rt15 = it["rows"]
    mut_json(rt13, "result_json", lambda e: change(rt13, rt14, rt15, e))
    reseal_record(p, it)


def rep_copy(rt13, rt14, rt15, e):
    rt13["representative_normalized_value"] += 1
    e["representative_normalized_value"] = format(rt13["representative_normalized_value"], "f")


def roles_copy(rt13, rt14, rt15, e):
    o = sorted(rt14, key=lambda x: x["observation_ordinal"])[0]
    mut_json(o, "observation_json", lambda j: j.update(roles=["comparative"]))
    e["observations"][o["observation_ordinal"]]["roles"] = ["comparative"]


def observations_reordered(rt13, rt14, rt15, e):
    """The two observations in reversed order, every cross pair reversed with them and re-sequenced, in T14, T15
    and E4: consistent at every level, so that only the canonical (document_sha256, so_key) order (EDI-5) fails."""
    o0, o1 = sorted(rt14, key=lambda x: x["observation_ordinal"])
    o0["observation_ordinal"], o1["observation_ordinal"] = 1, 0
    e["observations"] = [e["observations"][1], e["observations"][0]]
    for c in rt15:
        swap_ab_row(c)
        c["a_observation_ordinal"], c["b_observation_ordinal"] = 0, 1
        mut_json(c, "comparison_json", swap_ab)
    rt15.sort(key=lambda c: (c["a_observation_ordinal"], c["b_observation_ordinal"], c["a_member_ordinal"],
                             c["b_member_ordinal"]))
    for k, c in enumerate(rt15):
        c["comparison_ordinal"] = k
    e["comparisons"] = [json.loads(c["comparison_json"]) for c in rt15]


def pairs_reordered(rt13, rt14, rt15, e):
    a, b = sorted(rt15, key=lambda x: x["comparison_ordinal"])
    a["comparison_ordinal"], b["comparison_ordinal"] = 1, 0
    e["comparisons"] = [e["comparisons"][1], e["comparisons"][0]]


def pair_copy(rt13, rt14, rt15, e):
    c = sorted(rt15, key=lambda x: x["comparison_ordinal"])[0]
    c["a_value"] += 1
    v = format(c["a_value"], "f")
    mut_json(c, "comparison_json", lambda j: j["comparison"].update(a_value=v))
    e["comparisons"][0]["comparison"]["a_value"] = v


def results_reordered_with_e6(p):
    p.items.reverse()
    reenvelope(p.batch_row, "output_json", "output_hash", lambda e: e["results"].reverse())


def t15_reversed_with_element(p):
    c = t15(p)[0]
    swap_ab_row(c)
    mut_json(c, "comparison_json", swap_ab)


def twin_item(p):
    """The record of the twins document (tests/f64_scenarios.py tamper_docs t3)."""
    return next(i for i in p.items if i["append"] and i["result"].identity.period_end.isoformat() == "2022-09-30")


def t13_other_twin(p):
    """The record's representative set to the OTHER twin of its SO (identical copies; the harness supplies p.twins,
    each twin's candidate validation key -> the other's)."""
    rt13 = twin_item(p)["rows"][0]
    rt13["representative_candidate_validation_key"] = p.twins[rt13["representative_candidate_validation_key"]]


def document_count_everywhere(rt13, rt14, rt15, e):
    rt13["document_count"] += 1
    e["document_count"] = rt13["document_count"]


def reconciliation_version_everywhere(rt13, rt14, rt15, e):
    rt13["reconciliation_version"] = e["reconciliation_version"] = "f6.reconciliation.9"


def conflicting_input_supporting(rt13, rt14, rt15, e):
    o = sorted(rt14, key=lambda x: x["observation_ordinal"])[0]
    o["role_in_outcome"] = "supporting"
    mut_json(o, "observation_json", lambda j: j.update(role_in_outcome="supporting"))
    e["observations"][o["observation_ordinal"]]["role_in_outcome"] = "supporting"


def roles_swapped(rt13, rt14, rt15, e):
    o0, o1 = sorted(rt14, key=lambda x: x["observation_ordinal"])
    o0["role_in_outcome"], o1["role_in_outcome"] = o1["role_in_outcome"], o0["role_in_outcome"]
    for o in (o0, o1):
        mut_json(o, "observation_json", lambda j, v=o["role_in_outcome"]: j.update(role_in_outcome=v))
        e["observations"][o["observation_ordinal"]]["role_in_outcome"] = o["role_in_outcome"]


def representative_member_without_so(rt13, rt14, rt15, e):
    """The record made to look ambiguous - no representative SO, member, copies or 'representative' role, typed and
    in E4 alike, the ambiguity annotation added to both - while E4 still names a representative member."""
    for k in ("representative_so_key", "representative_candidate_validation_key", "reported_raw_value",
              "reported_parsed_value", "reported_representation_class", "reported_printed_decimals",
              "reported_sign_as_printed", "reported_scale", "reported_scale_basis", "reported_currency",
              "reported_value_type", "representative_normalized_value", "representative_half_unit"):
        rt13[k] = None
    rt13["annotations"] = list(rt13["annotations"]) + ["representative_ambiguous"]
    e.update(representative_so=None, representative=None, representative_normalized_value=None,
             representative_half_unit=None, annotations=list(rt13["annotations"]))
    for o in rt14:
        if o["role_in_outcome"] != "supporting":
            o["role_in_outcome"] = "supporting"
            mut_json(o, "observation_json", lambda j: j.update(role_in_outcome="supporting"))
            e["observations"][o["observation_ordinal"]]["role_in_outcome"] = "supporting"


RECORD_TAMPERS = [              # (name, change of the plan, the checks that may refuse it, phase)
    ("T13 state", lambda p: t13(p).update(state="single_source"), ["EDI-2"], "insert"),
    ("T13 interval re-scaled", lambda p: t13(p).update(interval_low=rescale(t13(p)["interval_low"])), ["EDI-2"],
     "insert"),
    ("T13 interval value", lambda p: t13(p).update(interval_low=t13(p)["interval_low"] - 1), ["EDI-2"], "insert"),
    ("T13 representative half-unit", lambda p: t13(p).update(
        representative_half_unit=t13(p)["representative_half_unit"] * 2), ["EDI-2"], "insert"),
    ("T13 comparison_count", lambda p: t13(p).update(comparison_count=t13(p)["comparison_count"] + 1), ["EDI-2"],
     "insert"),
    ("T13 reported", lambda p: t13(p).update(reported_raw_value="1,299"), ["EDI-2"], "insert"),
    ("T13 annotation", lambda p: t13(p).update(annotations=t13(p)["annotations"] + ["audit_labels_differ"]),
     ["EDI-2"], "insert"),
    ("T13 reason", lambda p: item(p, "conflicting")["rows"][0].update(
        reasons=item(p, "conflicting")["rows"][0]["reasons"] + ["values_disagree:x:y"]), ["EDI-2"], "insert"),
    ("T13 representative copy with E4", lambda p: record_everywhere(p, rep_copy), ["EDI-3"], "insert"),
    ("T14 role", lambda p: t14(p)[0].update(role_in_outcome="conflicting"), ["EDI-2"], "insert"),
    ("T14 roles in the element", lambda p: mut_json(t14(p)[0], "observation_json",
                                                    lambda j: j.update(roles=["comparative"])), ["EDI-3"], "insert"),
    ("T14 roles with E4", lambda p: record_everywhere(p, roles_copy), ["EDI-3"], "insert"),
    ("T14 so_output_hash", lambda p: t14(p)[0].update(so_output_hash="4" * 64), ["EDI-2"], "insert"),
    ("T14 reordered", lambda p: swap(t14(p), "observation_ordinal"), ["fk_frc_"], "insert"),
    ("T14 and T15 reordered with E4", lambda p: record_everywhere(p, observations_reordered), ["EDI-5"], "commit"),
    ("T14 missing", lambda p: item(p, "single_source")["rows"][1].clear(), ["EDI-1"], "commit"),
    ("T14 duplicate", lambda p: item(p, "corroborated")["rows"][1].append(dict(t14(p)[0], observation_ordinal=2)),
     ["pkey"], "insert"),
    ("T15 value", lambda p: t15(p)[0].update(a_value=t15(p)[0]["a_value"] + 1), ["EDI-2"], "insert"),
    ("T15 value re-scaled", lambda p: t15(p)[0].update(a_value=rescale(t15(p)[0]["a_value"])), ["EDI-2"], "insert"),
    ("T15 half-unit", lambda p: t15(p)[0].update(a_half_unit=t15(p)[0]["a_half_unit"] * 2), ["EDI-2"], "insert"),
    ("T15 tolerance", lambda p: t15(p)[0].update(tolerance=t15(p)[0]["tolerance"] + 1), ["EDI-2"], "insert"),
    ("T15 difference", lambda p: t15(p)[0].update(abs_difference=t15(p)[0]["abs_difference"] + 1), ["EDI-2"],
     "insert"),
    ("T15 sign-only", lambda p: t15(p, "conflicting")[0].update(sign_only=not t15(p, "conflicting")[0]["sign_only"]),
     ["EDI-2"], "insert"),
    ("T15 reason", lambda p: t15(p)[0].update(reason="nil_vs_numeric"), ["EDI-2"], "insert"),
    ("T15 reversed", lambda p: swap_ab_row(t15(p)[0]), ["EDI-3"], "insert"),
    ("T15 reversed with the element", t15_reversed_with_element, ["chk_frc_element"], "insert"),
    ("T15 copied value with E4", lambda p: record_everywhere(p, pair_copy), ["copy of a member value"], "insert"),
    ("T15 reordered", lambda p: swap(t15(p), "comparison_ordinal"), ["EDI-1"], "commit"),
    ("T15 reordered with E4", lambda p: record_everywhere(p, pairs_reordered), ["EDI-5"], "commit"),
    ("T15 missing", lambda p: item(p, "corroborated")["rows"][2].pop(), ["EDI-1"], "commit"),
    ("T15 duplicate", lambda p: item(p, "corroborated")["rows"][2].append(dict(t15(p)[0], comparison_ordinal=2)),
     ["pkey"], "insert"),
    ("T15 wrong ordinal", lambda p: t15(p)[1].update(comparison_ordinal=7), ["EDI-1"], "commit"),
    ("T16 missing", lambda p: p.items.pop(), ["EDI-1", "EDI-6", "completeness"], "commit"),
    ("T16 reordered", lambda p: p.items.reverse(), ["EDI-1"], "commit"),
    ("T16 reordered with E6", results_reordered_with_e6, ["EDI-5"], "commit"),
    ("T16 duplicate", lambda p: p.items.append(dict(p.items[-1])), ["record chain", "pkey"], "insert"),
    ("T12 results_count", lambda p: p.batch_row.update(results_count=p.batch_row["results_count"] + 1), ["EDI-2"],
     "insert"),
    ("T12 result hash in E6", lambda p: reenvelope(p.batch_row, "output_json", "output_hash",
                                                   lambda e: e["results"][0].__setitem__(1, "5" * 64)), ["EDI-1"],
     "commit"),
    ("T12 records_appended", lambda p: p.batch_row.update(records_appended=p.batch_row["records_appended"] - 1),
     ["EDI-6", "completeness"], "commit"),
    ("T12 configuration in E6", lambda p: reenvelope(p.batch_row, "output_json", "output_hash",
                                                     lambda e: e.update(configuration_id="6" * 64)), ["EDI-2"],
     "insert"),
    # consistent forgeries that isolate one rule each (added after the mutation audit)
    ("T13 representative is the other twin", t13_other_twin, ["EDI-3"], "insert"),
    ("T13 document_count with E4", lambda p: record_everywhere(p, document_count_everywhere),
     ["chk_frr_state", "completeness"], "insert"),
    ("T14 roles swapped with E4", lambda p: record_everywhere(p, roles_swapped), ["completeness"], "commit"),
    ("E4 names a representative member but the record has none",
     lambda p: record_everywhere(p, representative_member_without_so), ["E4 names a representative member"], "insert"),
    ("T14 an unknown SO", lambda p: t14(p)[0].update(so_key="5" * 64), ["an SO of another fact"], "insert"),
    ("T15 member ordinal", lambda p: t15(p)[0].update(a_member_ordinal=t15(p)[0]["a_member_ordinal"] + 1),
     ["fk_frc_a_member"], "insert"),
    ("T13 reconciliation_version with E4", lambda p: record_everywhere(p, reconciliation_version_everywhere),
     ["reconciliation_version differs from the configuration"], "insert"),
    ("a conflicting record with a supporting input, E4 consistent",
     lambda p: record_everywhere(p, conflicting_input_supporting, state="conflicting"),
     ["a conflicting record's inputs must all be conflicting"], "commit"),
]

# Where the Python mirror names the rule differently from the database's refusal (the database refuses these cases by
# a guard message, a foreign key or a CHECK): the mirror's own report that U10 requires.
MIRROR = {
    "T6 nil kind": ["T6 member is not an admitted candidate validation"],
    "T6 extra": ["T6 member is not an admitted candidate validation"],
    "T6 reordered": ["T7 member ordinal / key mismatch"],
    "a typed representative without E3's representative member": ["EDI-2 T5 representative without E3 representative"],
    "E4 names a representative member but the record has none": ["representative member without a representative SO"],
    "T14 an unknown SO": ["T14 SO missing"],
    "T15 member ordinal": ["T15 member reference"],
    "T13 reconciliation_version with E4": ["reconciliation_version differs from the configuration"],
    "a conflicting record with a supporting input, E4 consistent": ["completeness T14 roles"],
}
