import csv, re, argparse, sys, datetime, json
from pathlib import Path

def guess_currency(amount_formatted: str|None) -> str|None:
    if not amount_formatted: return None
    s = str(amount_formatted)
    if "CFA" in s or "FCFA" in s: return "XOF"
    if "US" in s and "$" in s:    return "USD"
    if "€" in s:                  return "EUR"
    if "$" in s:                  return "USD"
    return None

def to_float(v):
    if v is None: return 0.0
    s = str(v).replace("\u202f","").replace("\xa0","").replace(" ", "")
    try:
        return float(s.replace(",", "")) 
    except Exception:
        import re as _re
        m = _re.findall(r"[\d]+(?:[.,]\d+)?", str(v))
        return float(m[0].replace(",","")) if m else 0.0

def to_epoch(dt_str: str|None) -> int|None:
    if not dt_str: return None
    s = str(dt_str).strip()
    try:
        if s.endswith("Z"): s = s[:-1] + "+00:00"
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                dt = datetime.datetime.strptime(s, fmt)
                return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
            except Exception:
                pass
    return None

def norm_status(s: str|None) -> str:
    x = (s or "").strip().lower()
    if x in {"completed","paid","successful","success","succesfull","successfull"}: return "completed"
    
    return x 

def slugify_pid(product_name: str|None) -> str|None:
    if not product_name: return None
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "_", product_name.lower()).strip("_")
    return f"prd_{s[:16]}" if s else None


sys.path.append(str(Path(__file__).resolve().parents[1]))
from webhook_app.config import Config 
from webhook_app.utils.database import ensure_fact_dims_schema, upsert_fact_from_webhook 
from webhook_app.utils.migrations import ensure_fact_sales_failed_at  
from webhook_app.utils.database import _contact_key as contact_key

def load_product_map(path: str|None) -> dict:
    """Optionnel: mappe nom produit CSV -> product_id officiel (ex: prd_k3eyyy)."""
    if not path: return {}
    p = Path(path)
    if not p.exists(): print(f"[AVERTISSEMENT] product_map introuvable: {p}"); return {}
    if p.suffix.lower() == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    mp = {}
    with p.open("r", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for row in rd:
            pn = (row.get("product_name") or "").strip()
            pid = (row.get("product_id") or "").strip()
            if pn and pid: mp[pn] = pid
    return mp
def build_payload(row, product_map: dict, default_store_id: str|None, default_store_name: str|None):
    # Colonnes CSV
    date_str   = row.get("Date")
    sale_id    = row.get("Sale ID")
    cust_name  = row.get("Customer Name")
    email      = row.get("Email")
    phone      = row.get("Phone")
    country    = row.get("Country")
    status_in  = row.get("Status")
    amount_v   = row.get("Amount")
    amount_fmt = row.get("Amount Formatted")
    product_nm = row.get("Product")

    st  = norm_status(status_in)
    amt = to_float(amount_v)
    ccy = guess_currency(amount_fmt) or "XOF"

    # timestamps (on ancre sur 'Date' du CSV)
    created   = to_epoch(date_str)
    completed = abandoned = failed = None
    if st == "completed":
        completed = created
    elif st == "abandoned":
        abandoned = created
    elif st == "failed":
        failed = created

    # product_id: 
    pid = product_map.get(product_nm) or slugify_pid(product_nm)

    payload = {
        "event": f"{st}.sale" if st in {"failed","abandoned"} else "successful.sale",
        "sale": {
            "id": sale_id,
            "status": st,
            "amount": {"value": amt, "currency": ccy},
            "created_at": date_str,
            "completed_at": None if completed is None else datetime.datetime.utcfromtimestamp(completed).isoformat()+"Z",
            "abandoned_at": None if abandoned is None else datetime.datetime.utcfromtimestamp(abandoned).isoformat()+"Z",
            "failed_at":    None if failed    is None else datetime.datetime.utcfromtimestamp(failed).isoformat()+"Z",
        },
        "product": {
            "id": pid,
            "name": product_nm,
            "url": None,
            "price": {"value": amt, "currency": ccy},
        },
        "customer": {
            "id": None,
            "name": cust_name,
            "email": email,
            "phone": phone,
            "country": country,
        },
        "store": {
            "id": default_store_id,
            "name": default_store_name,
            "url": None,
        },
    }

    ck = contact_key(email, phone)
    ab_time = abandoned or failed 

  
    return payload, st, pid, ck, ab_time, sale_id




def import_csv(csv_path: str, product_map_path: str|None=None,
                store_id="store_4jy5h5ggl4fu", store_name="Digitech Hub",
                dry_run=False):
    ensure_fact_dims_schema()
    ensure_fact_sales_failed_at()

    pmap = load_product_map(product_map_path)

    with open(csv_path, "r", encoding="utf-8") as f:
        sample = f.read(4096); f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            dialect = csv.excel
        rd = csv.DictReader(f, dialect=dialect)

        n_total = n_upsert = 0
        for row in rd:
            n_total += 1
            payload, st, pid, ck, ab_time, sale_id = build_payload(
                row, pmap, store_id, store_name
            )

    
            if st != "completed":
                continue

            if dry_run:
                print(f"[DRY] would upsert COMPLETED {sale_id} pid={pid} ck={ck}")
            else:
                upsert_fact_from_webhook(payload)
                n_upsert += 1

        print(f"Import terminé. Lignes lues={n_total}, completed importés={n_upsert}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path", help="Chemin du CSV export ventes")
    ap.add_argument("--product-map", help="JSON/CSV mapping product_name->product_id", default=None)
    ap.add_argument("--store-id", default="store_4jy5h5ggl4fu")
    ap.add_argument("--store-name", default="Digitech Hub")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    import_csv(args.csv_path, product_map_path=args.product_map,
               store_id=args.store_id, store_name=args.store_name,
               dry_run=args.dry_run)
