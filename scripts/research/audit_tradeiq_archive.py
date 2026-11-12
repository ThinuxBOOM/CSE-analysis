#!/usr/bin/env python3
"""Read-only structural and source-vs-converted row audit for TradeIQ."""
from __future__ import annotations
import argparse, csv, hashlib, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def csv_profile(path: Path) -> dict:
    total=nonempty=blank=width=0
    with path.open("r",encoding="utf-8-sig",newline="") as f:
        for row in csv.reader(f):
            total += 1
            if any(str(v).strip() for v in row):
                nonempty += 1
                width=max(width,len(row))
            else:
                blank += 1
    return {"physical_records":total,"nonempty_rows":nonempty,"blank_rows":blank,"max_columns":width}

def workbook_info(path: Path) -> list[dict]:
    out=[]
    if path.suffix.lower()==".xlsx":
        from openpyxl import load_workbook
        wb=load_workbook(path,read_only=True,data_only=True)
        for ws in wb.worksheets:
            rows=0; cols=0
            for row in ws.iter_rows(values_only=True):
                if any(v is not None and str(v).strip() for v in row):
                    rows+=1; cols=max(cols,len(row))
            out.append({"sheet":ws.title,"nonempty_rows":rows,"max_columns":cols,
                        "worksheet_rows":ws.max_row or 0,"worksheet_columns":ws.max_column or 0})
        wb.close()
    elif path.suffix.lower()==".xls":
        import xlrd
        wb=xlrd.open_workbook(str(path),on_demand=True)
        for ws in wb.sheets():
            rows=sum(1 for r in range(ws.nrows) if any(str(v).strip() for v in ws.row_values(r)))
            out.append({"sheet":ws.name,"nonempty_rows":rows,"max_columns":ws.ncols,
                        "worksheet_rows":ws.nrows,"worksheet_columns":ws.ncols})
        wb.release_resources()
    return out

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--archive",required=True,type=Path)
    ap.add_argument("--out",required=True,type=Path)
    a=ap.parse_args(); root=a.archive.resolve()
    if not root.is_dir(): raise SystemExit(f"archive directory missing: {root}")
    files=sorted(p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
    inventory=[]; parse_errors=[]; workbook_cache={}
    for p in files:
        rel=p.relative_to(root).as_posix()
        item={"path":rel,"bytes":p.stat().st_size,"sha256":sha256(p)}
        ext=p.suffix.lower()
        try:
            if ext==".csv":
                item.update(format="csv",**csv_profile(p))
            elif ext in (".xls",".xlsx"):
                item.update(format=ext[1:],sheets=workbook_info(p))
                workbook_cache[str(p)]=item["sheets"]
        except Exception as e:
            item["parse_error"]=f"{type(e).__name__}: {e}"
            parse_errors.append({"path":rel,"error":item["parse_error"]})
        inventory.append(item)

    manifests=[p for p in files if p.name.lower()=="conversion_manifest.csv"]
    manifest_rows=0; manifest_unresolved=[]; row_mismatches=[]; row_diagnostics=[]
    for mf in manifests:
        try:
            with mf.open("r",encoding="utf-8-sig",newline="") as f:
                for row in csv.DictReader(f):
                    manifest_rows+=1
                    resolved={}
                    for key in ("source_path","output_csv"):
                        value=(row.get(key) or "").strip()
                        if not value: continue
                        candidates=[root/value,mf.parent/value,mf.parent.parent/value]
                        target=next((x for x in candidates if x.is_file()),None)
                        if target is None:
                            manifest_unresolved.append({"manifest":mf.relative_to(root).as_posix(),"field":key,"path":value})
                        else: resolved[key]=target
                    target=resolved.get("output_csv"); source=resolved.get("source_path")
                    if target:
                        try:
                            cp=csv_profile(target); expected=int(row.get("rows") or 0)
                            sp=None
                            if source:
                                sheets=workbook_cache.get(str(source))
                                if sheets is None:
                                    sheets=workbook_info(source); workbook_cache[str(source)]=sheets
                                sp=next((x for x in sheets if str(x["sheet"])==str(row.get("sheet_name") or "").strip()),None)
                            d={"output_csv":target.relative_to(root).as_posix(),
                               "source_path":source.relative_to(root).as_posix() if source else None,
                               "sheet_name":row.get("sheet_name"),"manifest_rows":expected,
                               "source_worksheet_rows":sp.get("worksheet_rows") if sp else None,
                               "source_nonempty_rows":sp.get("nonempty_rows") if sp else None,
                               "csv_physical_records":cp["physical_records"],
                               "csv_nonempty_rows":cp["nonempty_rows"],"csv_blank_rows":cp["blank_rows"]}
                            if sp and sp["worksheet_rows"]==expected and sp["nonempty_rows"]==cp["nonempty_rows"]:
                                d["classification"]="RESOLVED: worksheet extent includes empty rows omitted from converted CSV"
                            elif sp and sp["nonempty_rows"]!=cp["nonempty_rows"]:
                                d["classification"]="UNRESOLVED: source and converted nonempty row counts differ"
                                row_mismatches.append(d.copy())
                            else:
                                d["classification"]="UNRESOLVED: source worksheet profile unavailable or extent differs"
                                row_mismatches.append(d.copy())
                            if expected!=cp["nonempty_rows"]: row_diagnostics.append(d)
                        except Exception as e:
                            parse_errors.append({"path":str(target),"error":f"manifest comparison: {e}"})
        except Exception as e:
            parse_errors.append({"path":mf.relative_to(root).as_posix(),"error":f"manifest: {e}"})

    totals={"files":len(inventory),"bytes":sum(x["bytes"] for x in inventory),
      "csv_files":sum(x.get("format")=="csv" for x in inventory),
      "workbooks":sum(x.get("format") in ("xls","xlsx") for x in inventory),
      "manifest_files":len(manifests),"manifest_rows":manifest_rows,
      "parse_errors":len(parse_errors),"manifest_unresolved_references":len(manifest_unresolved),
      "manifest_row_count_mismatches":len(row_mismatches),
      "manifest_count_differences_explained":len(row_diagnostics)-len(row_mismatches)}
    report={"generated_at":datetime.now(timezone.utc).isoformat(),
      "archive_git_commit":os.environ.get("TRADEIQ_COMMIT","unknown"),
      "scope":"read-only structural inventory and source-vs-converted row-count reconciliation; not canonical admission",
      "totals":totals,"manifest_unresolved":manifest_unresolved,
      "row_count_diagnostics":row_diagnostics,"row_mismatches":row_mismatches,
      "parse_errors":parse_errors,"files":inventory,
      "limitations":["No authoritative CSE trading calendar reconciliation.",
      "No security-master/delisted-universe completeness proof.","No independent semantic mapping of legacy workbook columns.",
      "No corporate-action completeness or point-in-time adjustment validation.",
      "No rights/licensing determination for storing or redistributing underlying CSE data.",
      "No project database import; no live CSE requests."]}
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"tradeiq_inventory.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    md=["# TradeIQ archive structural audit","",
      f"- TradeIQ commit: {report['archive_git_commit']}",
      f"- Generated: {report['generated_at']}","","## Counts",""]
    md += [f"- {k}: **{v}**" for k,v in totals.items()]
    md += ["","## Row-count reconciliation","",
      "A manifest row count is treated as the source worksheet extent, not automatically as the number of non-empty converted CSV records.",
      "A discrepancy is resolved only when the source workbook worksheet extent equals the manifest count AND source non-empty rows equal converted CSV non-empty rows.",""]
    md += [f"- {x['output_csv']}: manifest={x['manifest_rows']}; source worksheet extent={x['source_worksheet_rows']}; source non-empty={x['source_nonempty_rows']}; CSV physical records={x['csv_physical_records']}; CSV non-empty={x['csv_nonempty_rows']}; CSV blank={x['csv_blank_rows']}; {x['classification']}" for x in row_diagnostics] or ["- No row-count differences"]
    md += ["","## Unresolved row-count mismatches",""]
    md += [f"- {json.dumps(x,ensure_ascii=False)}" for x in row_mismatches] or ["- None"]
    md += ["","## Manifest unresolved references",""]
    md += [f"- {x['field']}: {x['path']}" for x in manifest_unresolved[:200]] or ["- None"]
    md += ["","## Parse errors",""]
    md += [f"- {x['path']}: {x['error']}" for x in parse_errors[:200]] or ["- None"]
    md += ["","## Limitations",""]+[f"- {x}" for x in report["limitations"]]
    (a.out/"tradeiq_inventory.md").write_text("\n".join(md)+"\n",encoding="utf-8")
    print(json.dumps(totals,indent=2))
    return 0
if __name__=="__main__": sys.exit(main())
