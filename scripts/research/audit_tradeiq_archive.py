#!/usr/bin/env python3
"""Read-only inventory and structural audit of the public TradeIQ CSE archive.

Does not contact CSE, modify the archive, or import data into the project database.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def nonempty_csv_rows(path: Path) -> tuple[int,int]:
    count=0; width=0
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        for row in csv.reader(f):
            if any(str(v).strip() for v in row):
                count+=1; width=max(width,len(row))
    return count,width

def workbook_info(path: Path) -> list[dict]:
    out=[]
    if path.suffix.lower()==".xlsx":
        from openpyxl import load_workbook
        wb=load_workbook(path,read_only=True,data_only=True)
        for ws in wb.worksheets:
            rows=0; cols=0
            for row in ws.iter_rows(values_only=True):
                if any(v is not None and str(v).strip() for v in row):
                    rows+=1
                    cols=max(cols,len(row))
            out.append({"sheet":ws.title,"nonempty_rows":rows,"max_columns":cols})
        wb.close()
    elif path.suffix.lower()==".xls":
        import xlrd
        wb=xlrd.open_workbook(str(path),on_demand=True)
        for ws in wb.sheets():
            rows=sum(1 for r in range(ws.nrows) if any(str(v).strip() for v in ws.row_values(r)))
            out.append({"sheet":ws.name,"nonempty_rows":rows,"max_columns":ws.ncols})
        wb.release_resources()
    return out

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--archive",required=True,type=Path)
    ap.add_argument("--out",required=True,type=Path)
    a=ap.parse_args()
    root=a.archive.resolve()
    if not root.is_dir(): raise SystemExit(f"archive directory missing: {root}")
    files=sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    inventory=[]; parse_errors=[]; manifest_info={}
    for p in files:
        rel=p.relative_to(root).as_posix()
        item={"path":rel,"bytes":p.stat().st_size,"sha256":sha256(p)}
        ext=p.suffix.lower()
        try:
            if ext==".csv":
                n,w=nonempty_csv_rows(p); item.update(format="csv",nonempty_rows=n,max_columns=w)
            elif ext in (".xls",".xlsx"):
                item.update(format=ext[1:],sheets=workbook_info(p))
        except Exception as e:
            item["parse_error"]=f"{type(e).__name__}: {e}"
            parse_errors.append({"path":rel,"error":item["parse_error"]})
        inventory.append(item)
    manifests=[p for p in files if p.name.lower()=="conversion_manifest.csv"]
    manifest_rows=0; manifest_unresolved=[]; row_mismatches=[]
    for mf in manifests:
        try:
            with mf.open("r",encoding="utf-8-sig",newline="") as f:
                for row in csv.DictReader(f):
                    manifest_rows+=1
                    for key in ("source_path","output_csv"):
                        value=(row.get(key) or "").strip()
                        if not value: continue
                        candidates=[root/value, mf.parent/value, mf.parent.parent/value]
                        target=next((x for x in candidates if x.is_file()),None)
                        if target is None:
                            manifest_unresolved.append({"manifest":mf.relative_to(root).as_posix(),"field":key,"path":value})
                        elif key=="output_csv":
                            try:
                                actual,_=nonempty_csv_rows(target)
                                expected=int(row.get("rows") or 0)
                                # Manifest row counts may use a convention (header included/excluded);
                                # retain exact comparisons as evidence, never silently normalize.
                                if actual not in (expected,expected+1):
                                    row_mismatches.append({"path":target.relative_to(root).as_posix(),"manifest_rows":expected,"actual_nonempty_rows":actual})
                            except Exception as e:
                                parse_errors.append({"path":str(target),"error":str(e)})
        except Exception as e:
            parse_errors.append({"path":mf.relative_to(root).as_posix(),"error":f"manifest: {e}"})
    totals={
      "files":len(inventory),"bytes":sum(x["bytes"] for x in inventory),
      "csv_files":sum(x.get("format")=="csv" for x in inventory),
      "workbooks":sum(x.get("format") in ("xls","xlsx") for x in inventory),
      "manifest_files":len(manifests),"manifest_rows":manifest_rows,
      "parse_errors":len(parse_errors),"manifest_unresolved_references":len(manifest_unresolved),
      "manifest_row_count_mismatches":len(row_mismatches)
    }
    report={"generated_at":datetime.now(timezone.utc).isoformat(),"archive_path":str(root),
      "archive_git_commit":os.environ.get("TRADEIQ_COMMIT","unknown"),"scope":"structural inventory only; not canonical admission",
      "totals":totals,"manifest_unresolved":manifest_unresolved,"row_mismatches":row_mismatches,
      "parse_errors":parse_errors,"files":inventory,
      "limitations":["No authoritative CSE trading calendar reconciliation.",
      "No security-master/delisted-universe completeness proof.","No independent semantic mapping of legacy workbook columns.",
      "No corporate-action completeness or point-in-time adjustment validation.",
      "No rights/licensing determination for storing or redistributing underlying CSE data.",
      "No project database import; no live CSE requests."]}
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"tradeiq_inventory.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    md=["# TradeIQ archive structural audit","",
      f"- TradeIQ commit: `{report['archive_git_commit']}`",
      f"- Generated: {report['generated_at']}","",
      "## Counts",""]
    md += [f"- {k}: **{v}**" for k,v in totals.items()]
    md += ["","## Interpretation","",
      "This is a reproducible structural inventory, not a Phase 3 acceptance decision. Any parse error, unresolved manifest reference, or row-count mismatch requires investigation. Even a clean structural run cannot establish source authenticity, trading-calendar completeness, security identity, delisting coverage, corporate-action completeness, point-in-time safety, or data rights.","",
      "## Manifest unresolved references",""]
    md += [f"- {x['field']}: `{x['path']}`" for x in manifest_unresolved[:200]] or ["- None"]
    md += ["","## Row-count mismatches",""]
    md += [f"- `{x['path']}`: manifest={x['manifest_rows']}, parsed={x['actual_nonempty_rows']}" for x in row_mismatches[:200]] or ["- None"]
    md += ["","## Parse errors",""]
    md += [f"- `{x['path']}`: {x['error']}" for x in parse_errors[:200]] or ["- None"]
    (a.out/"tradeiq_inventory.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps(totals,indent=2))
    # Structural anomalies are findings, not a reason to discard the evidence artifact.
    return 0
if __name__=="__main__": sys.exit(main())
