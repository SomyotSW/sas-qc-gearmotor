#!/usr/bin/env python3
"""
salvage_raw_selection.py
========================
ถ้า selection_tables มี _parse_error แต่มี _raw เต็ม
→ parse raw ด้วย brace-balanced scanner + inherit model code
→ ได้ rows เต็มพร้อม service_factor

Usage:
  python salvage_raw_selection.py --input DB.json --output DB_fixed.json
  python salvage_raw_selection.py --from-r2 --upload   # download/upload R2
"""

from __future__ import annotations
import argparse, json, os, re, sys
from pathlib import Path
from typing import Optional

_GEAR_RE = re.compile(r"^[RSKFW][A-Z]*\d{2,3}$")


def _extract_balanced_objects(text: str) -> list[str]:
    """Scan text for balanced {...} objects (handles nested)."""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth, j = 1, i + 1
            in_str, esc = False, False
            while j < len(text) and depth > 0:
                ch = text[j]
                if esc: esc = False
                elif ch == '\\': esc = True
                elif ch == '"': in_str = not in_str
                elif not in_str:
                    if ch == '{': depth += 1
                    elif ch == '}': depth -= 1
                j += 1
            if depth == 0:
                results.append(text[i:j])
                i = j
                continue
        i += 1
    return results


def salvage_raw(raw: str) -> Optional[dict]:
    """
    Parse _raw response into {tables: [{power_kw, rows: [...]}]}.
    
    Strategy:
      1. Find all top-level power_kw markers with their positions
      2. For each kW region, extract all row objects between this kW and the next
      3. Parse each row, inherit model/variants when missing
    """
    if not raw: return None
    
    # strip markdown fences
    cleaned = raw.replace('```json', '').replace('```', '').strip()
    
    # Find all power_kw positions
    kw_matches = list(re.finditer(r'"power_kw"\s*:\s*([\d.]+)', cleaned))
    if not kw_matches:
        return None
    
    tables = []
    for idx, km in enumerate(kw_matches):
        try:
            kw = float(km.group(1))
        except ValueError:
            continue
        
        # Find region: from this kW's position to next kW (or end)
        region_start = km.start()
        region_end = kw_matches[idx + 1].start() if idx + 1 < len(kw_matches) else len(cleaned)
        region = cleaned[region_start:region_end]
        
        # Find "rows": [...] within region
        # Look for all balanced objects in this region — skip the first (which is the parent)
        objs = _extract_balanced_objects(region)
        
        rows = []
        for obj_str in objs:
            # Filter: must have "ratio" and not "power_kw" (that's the wrapper)
            if '"ratio"' not in obj_str or '"power_kw"' in obj_str:
                continue
            try:
                row = json.loads(obj_str)
            except json.JSONDecodeError:
                continue
            if 'ratio' not in row:
                continue
            rows.append(row)
        
        if rows:
            # Inherit model/variants: backward pass then forward pass
            last_model, last_code, last_variants = None, None, None
            for r in reversed(rows):
                if r.get('full_model'):
                    last_model = r['full_model']
                    last_code = r.get('model_code')
                    last_variants = r.get('variants')
                elif last_model:
                    r['full_model'] = last_model
                    if last_code and 'model_code' not in r:
                        r['model_code'] = last_code
                    if last_variants and not r.get('variants'):
                        r['variants'] = last_variants
                    r['_inherited'] = True
            
            last_model, last_code, last_variants = None, None, None
            for r in rows:
                if r.get('full_model'):
                    last_model = r['full_model']
                    last_code = r.get('model_code')
                    last_variants = r.get('variants')
                elif last_model:
                    r['full_model'] = last_model
                    if last_code and 'model_code' not in r:
                        r['model_code'] = last_code
                    if last_variants and not r.get('variants'):
                        r['variants'] = last_variants
                    r['_inherited'] = True
            
            tables.append({'power_kw': kw, 'rows': rows})
    
    return {'tables': tables} if tables else None


def fix_database(db: dict) -> tuple[int, int, int]:
    """Fix in-place. Returns (salvaged, rows_added, still_broken)."""
    salvaged, rows_added, still_broken = 0, 0, 0
    
    new_sel = []
    for st in db.get('selection_tables', []):
        if st.get('_parse_error') and st.get('_raw'):
            result = salvage_raw(st['_raw'])
            if result and result.get('tables'):
                new_entry = {'_page': st.get('_page'), 'tables': result['tables'], '_salvaged': True}
                new_sel.append(new_entry)
                salvaged += 1
                rows_added += sum(len(t.get('rows', [])) for t in result['tables'])
                continue
            else:
                still_broken += 1
        new_sel.append(st)
    
    db['selection_tables'] = new_sel
    
    # Rebuild model_index
    idx = {}
    for st in db.get('selection_tables', []):
        for t in st.get('tables', []) or []:
            kw = t.get('power_kw')
            for r in t.get('rows', []):
                m = r.get('full_model')
                if not m: continue
                e = idx.setdefault(m, {'model': m, 'applications': [], 'dimensions': {}})
                e['applications'].append({
                    'power_kw': kw,
                    'ratio': r.get('ratio'),
                    'output_speed_rpm': r.get('output_speed_rpm'),
                    'output_torque_nm': r.get('output_torque_nm'),
                    'service_factor': r.get('service_factor'),
                    'variants': r.get('variants', []),
                })
    db['model_index'] = idx
    
    return salvaged, rows_added, still_broken


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input', '-i')
    p.add_argument('--output', '-o')
    p.add_argument('--from-r2', action='store_true')
    p.add_argument('--upload', action='store_true')
    p.add_argument('--brand', default='sas')
    p.add_argument('--catalog', default='r_series')
    args = p.parse_args()
    
    r2 = None
    r2_key = f"databases/{args.brand}__{args.catalog}.json"
    
    if args.from_r2:
        try:
            import boto3
            from botocore.client import Config
        except ImportError:
            print("pip install boto3"); sys.exit(1)
        
        for k in ['R2_ACCOUNT_ID','R2_ACCESS_KEY_ID','R2_SECRET_KEY']:
            if not os.environ.get(k):
                print(f"ENV {k} not set"); sys.exit(1)
        bucket = os.environ.get('R2_BUCKET_NAME', 'qc-gear-motor')
        r2 = boto3.client('s3',
            endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
            aws_secret_access_key=os.environ['R2_SECRET_KEY'],
            config=Config(signature_version='s3v4'),
            region_name='auto')
        print(f"Downloading R2: {r2_key}")
        obj = r2.get_object(Bucket=bucket, Key=r2_key)
        db = json.loads(obj['Body'].read())
    else:
        if not args.input:
            print("--input required unless --from-r2"); sys.exit(1)
        with open(args.input) as f:
            db = json.load(f)
    
    # Count before
    before_rows = sum(len(t.get('rows', [])) for st in db.get('selection_tables', []) for t in (st.get('tables') or []))
    before_parse_err = sum(1 for st in db.get('selection_tables', []) if st.get('_parse_error'))
    
    print(f"Before: selection rows={before_rows}, parse errors={before_parse_err}")
    
    salvaged, rows_added, still_broken = fix_database(db)
    
    after_rows = sum(len(t.get('rows', [])) for st in db.get('selection_tables', []) for t in (st.get('tables') or []))
    print(f"Salvaged: {salvaged} tables (+{rows_added} rows), {still_broken} still broken")
    print(f"After:  selection rows={after_rows}")
    print(f"Model index: {len(db.get('model_index', {}))} unique models")
    
    # kW coverage
    kws = set()
    for st in db.get('selection_tables', []):
        for t in (st.get('tables') or []):
            if t.get('power_kw') is not None:
                kws.add(t.get('power_kw'))
    print(f"kW coverage: {sorted(kws)}")
    
    # Write output
    if args.upload and r2:
        body = json.dumps(db, ensure_ascii=False, indent=2).encode('utf-8')
        print(f"Uploading to R2: {r2_key} ({len(body)/1024:.1f} KB)")
        r2.put_object(Bucket=os.environ.get('R2_BUCKET_NAME','qc-gear-motor'),
                      Key=r2_key, Body=body, ContentType='application/json')
        print("[OK] Uploaded")
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        print(f"[OK] Wrote: {args.output}")

if __name__ == '__main__':
    main()
