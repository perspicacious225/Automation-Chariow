# webhook_app/admin/dashboard.py
from flask import Blueprint, jsonify, render_template_string, request, Response
import sqlite3, csv, io, os, json
from webhook_app.config import Config

dashboard_bp = Blueprint("dashboard", __name__)

# def _guard():
#     tok = getattr(Config, "DASHBOARD_TOKEN", None)
#     if not tok: return True
#     return request.headers.get("X-Dashboard-Token") == tok
def _guard():
    tok = getattr(Config, "DASHBOARD_TOKEN", None)
    if not tok:
        return True
    # Autorise soit l'entête, soit ?token=...
    return (
        request.headers.get("X-Dashboard-Token") == tok
        or request.args.get("token") == tok
    )


def _conn():
    return sqlite3.connect(Config.DB_PATH)

def _scalar(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return (row[0] if row else 0) or 0

def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

@dashboard_bp.route("/metrics.json")
def metrics_json():
    if not _guard(): return jsonify({"error":"forbidden"}), 403
    conn = _conn()
    try:
        data = {}
        # Relances en file
        data["pending_due"]    = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE sent_at IS NULL AND due_at <= datetime('now')")
        data["pending_future"] = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE sent_at IS NULL AND due_at >  datetime('now')")

        # Envois 24h par canal
        sent24 = _rows(conn, "SELECT channel, COUNT(*) cnt FROM notification_log WHERE sent_at >= datetime('now','-1 day') GROUP BY channel")
        sent24 = {r["channel"]: r["cnt"] for r in sent24}
        data["sent_24h_email"]    = sent24.get("email",0)
        data["sent_24h_whatsapp"] = sent24.get("whatsapp",0)

        # Erreurs 24h & cadences actives
        data["errors_24h"] = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE error IS NOT NULL AND sent_at >= datetime('now','-1 day')")
        data["cadences_active"] = _scalar(conn, "SELECT COUNT(DISTINCT COALESCE(contact_key,'')||'|'||COALESCE(product_id,'')) FROM scheduled_notifications WHERE sent_at IS NULL")

        # Ventes 7j
        data["gmv_7d"] = _scalar(conn,
          "SELECT COALESCE(SUM(amount_value),0) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')"
        )
        data["orders_7d"] = _scalar(conn,
          "SELECT COUNT(*) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')"
        )
        data["aov_7d"] = (data["gmv_7d"] / data["orders_7d"]) if data["orders_7d"] else 0.0

        # Abandons/failed 24h
        data["abandoned_24h"] = _scalar(conn,
          "SELECT COUNT(*) FROM fact_sales WHERE status='abandoned' AND COALESCE(abandoned_at,created_at) >= datetime('now','-1 day')"
        )
        data["failed_24h"] = _scalar(conn,
          "SELECT COUNT(*) FROM fact_sales WHERE status='failed' AND COALESCE(created_at,completed_at) >= datetime('now','-1 day')"
        )

        # Recovered 7j (orders + GMV) et taux de panier récupéré
        data["recovered_orders_7d"] = _scalar(conn, """
          WITH ab AS (
            SELECT product_id, contact_key, MAX(COALESCE(abandoned_at,created_at)) AS last_ab
            FROM fact_sales
            WHERE status IN ('abandoned','failed')
              AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
            GROUP BY product_id, contact_key
          ),
          co AS (
            SELECT sale_id, amount_value, COALESCE(completed_at,created_at) AS t, product_id, contact_key
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
          )
          SELECT COUNT(*) FROM co JOIN ab USING(product_id,contact_key)
          WHERE co.t BETWEEN ab.last_ab AND datetime(ab.last_ab,'+72 hour')
        """)
        data["recovered_gmv_7d"] = _scalar(conn, """
          WITH ab AS (
            SELECT product_id, contact_key, MAX(COALESCE(abandoned_at,created_at)) AS last_ab
            FROM fact_sales
            WHERE status IN ('abandoned','failed')
              AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
            GROUP BY product_id, contact_key
          ),
          co AS (
            SELECT sale_id, amount_value, COALESCE(completed_at,created_at) AS t, product_id, contact_key
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
          )
          SELECT COALESCE(SUM(co.amount_value),0) FROM co JOIN ab USING(product_id,contact_key)
          WHERE co.t BETWEEN ab.last_ab AND datetime(ab.last_ab,'+72 hour')
        """)
        data["ab_failed_7d"] = _scalar(conn, """
          SELECT COUNT(*) FROM fact_sales
          WHERE status IN ('abandoned','failed')
            AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
        """)
        data["recovered_rate_7d"] = (data["recovered_orders_7d"] / data["ab_failed_7d"]) if data["ab_failed_7d"] else 0.0

        # Conversions par step (via dernière relance avant la vente)
        conv_by_step = _rows(conn, """
        WITH co AS (
          SELECT sale_id, product_id, contact_key,
                 COALESCE(completed_at,created_at) AS completed_at, amount_value
          FROM fact_sales
          WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
        ),
        lastrel AS (
          SELECT nl.contact_key, nl.product_id, nl.template_type, nl.sent_at
          FROM notification_log nl
          WHERE nl.template_type LIKE 'relance_%'
        )
        SELECT x.template_type AS step, COUNT(*) AS conv, COALESCE(SUM(co.amount_value),0) AS gmv
        FROM (
          SELECT c.sale_id,
                 (SELECT lr.template_type FROM lastrel lr
                  WHERE lr.contact_key=c.contact_key AND lr.product_id=c.product_id AND lr.sent_at<=c.completed_at
                  ORDER BY lr.sent_at DESC LIMIT 1) AS template_type
          FROM co c
        ) x JOIN fact_sales co ON co.sale_id = x.sale_id
        WHERE x.template_type IS NOT NULL
        GROUP BY x.template_type
        ORDER BY x.template_type
        """)
        data["conversions_by_step_7d"] = conv_by_step

        # A/B split (envois par arm)
        data["ab_sent_7d"] = _rows(conn, """
          SELECT ab_arm, COUNT(*) AS cnt
          FROM notification_log
          WHERE sent_at >= datetime('now','-7 day')
          GROUP BY ab_arm
        """)

        # Segmentation RFM (vue globale)
        data["rfm_segments"] = _rows(conn, """
          SELECT rfm_segment AS segment, COUNT(*) AS customers, COALESCE(SUM(gmv_total),0) AS gmv
          FROM dim_customer
          GROUP BY rfm_segment
          ORDER BY gmv DESC
        """)

        # Top produits & pays (7j)
        data["top_products_7d"] = _rows(conn, """
          SELECT COALESCE(product_id,'(n/a)') AS product_id, COUNT(*) AS orders, COALESCE(SUM(amount_value),0) AS gmv
          FROM fact_sales WHERE status='completed'
            AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
          GROUP BY COALESCE(product_id,'(n/a)')
          ORDER BY gmv DESC LIMIT 5
        """)
        data["countries_7d"] = _rows(conn, """
          SELECT COALESCE(country,'(n/a)') AS country, COUNT(*) AS orders, COALESCE(SUM(amount_value),0) AS gmv
          FROM fact_sales WHERE status='completed'
            AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
          GROUP BY COALESCE(country,'(n/a)')
          ORDER BY gmv DESC LIMIT 5
        """)

        return jsonify(data)
    finally:
        conn.close()

@dashboard_bp.route("/ts.json")
def ts_json():
    if not _guard(): return jsonify({"error":"forbidden"}), 403
    conn = _conn()
    try:
        rows = _rows(conn, """
          SELECT strftime('%Y-%m-%d', COALESCE(completed_at,created_at)) AS d,
                 SUM(CASE WHEN status='completed' THEN amount_value ELSE 0 END) AS gmv,
                 SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS orders,
                 SUM(CASE WHEN status='abandoned' THEN 1 ELSE 0 END) AS abandoned
          FROM fact_sales
          WHERE COALESCE(created_at, completed_at) >= datetime('now','-30 day')
          GROUP BY strftime('%Y-%m-%d', COALESCE(completed_at,created_at))
          ORDER BY d
        """)
        return jsonify(rows)
    finally:
        conn.close()

@dashboard_bp.route("/heatmap.json")
def heatmap_json():
    if not _guard(): return jsonify({"error":"forbidden"}), 403
    conn = _conn()
    try:
        rows = _rows(conn, """
          SELECT dow, hour_of_day, COUNT(*) AS cnt
          FROM fact_sales
          WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-30 day')
          GROUP BY dow, hour_of_day
        """)
        return jsonify(rows)
    finally:
        conn.close()

@dashboard_bp.route("/export.csv")
def export_csv():
    if not _guard(): return jsonify({"error":"forbidden"}), 403
    conn = _conn()
    try:
        q = """
        SELECT sale_id,status,amount_value,currency,product_id,product_name,store_name,
               contact_key,email,phone,country,created_at,completed_at,abandoned_at,
               time_to_complete_min,hour_of_day,dow,month,utm_source,utm_medium,utm_campaign,price_tier
        FROM fact_sales
        WHERE COALESCE(created_at,completed_at) >= datetime('now','-90 day')
        ORDER BY COALESCE(completed_at,created_at) DESC
        """
        rows = _rows(conn, q)
    finally:
        conn.close()
    si = io.StringIO()
    if rows:
        w = csv.DictWriter(si, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows: w.writerow(r)
    else:
        si.write("")
    return Response(si.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=export_90d.csv"})

@dashboard_bp.route("/", methods=["GET"])
def dashboard_view():
    if not _guard(): return "forbidden", 403
    html = """
<!doctype html><meta charset="utf-8"><title>Dashboard KPI</title>
<style>
body{font-family:system-ui,Segoe UI,Roboto,Ubuntu,sans-serif;background:#0b1220;color:#e6edf7;margin:0;padding:24px}
h1{margin:0 0 16px;font-size:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin-bottom:20px}
.card{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px}
.kpi{font-size:28px;font-weight:700;margin:6px 0}.muted{color:#8892b0;font-size:12px}
table{width:100%;border-collapse:collapse;margin-top:8px} th,td{padding:8px;border-bottom:1px solid rgba(255,255,255,.06);font-size:13px}
.tag{display:inline-block;background:#0D47A122;border:1px solid #0D47A144;color:#cfe3ff;padding:2px 8px;border-radius:999px;font-size:12px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.06);margin:2px 6px 2px 0;font-size:12px}
.ok{color:#7ee787}.ko{color:#ff7b72}
</style>
<h1>Dashboard KPI — Relances & Ventes</h1>
<div class="grid" id="kpis"></div>
<div class="card"><div class="muted">Conversions par step (7j)</div><div id="steps"></div></div>
<div class="grid">
  <div class="card"><div class="muted">Top produits (7j)</div><table id="topprod"></table></div>
  <div class="card"><div class="muted">Pays (7j)</div><table id="countries"></table></div>
  <div class="card"><div class="muted">Segments RFM</div><table id="rfm"></table></div>
</div>
<script>
async function load(){
  // --- NEW: pick up token from URL and/or send as header ---
  const p = new URLSearchParams(location.search);
  const token   = p.get('token') || '';
  const headers = token ? { 'X-Dashboard-Token': token } : {};
  const qs      = token ? ('?token=' + encodeURIComponent(token)) : '';

  // Use token for the API call (both query string and header, works with either guard)
  const resp = await fetch('/dashboard/metrics.json' + qs, { headers });
  const m = await resp.json();

  const k = document.getElementById('kpis');
  const pct = (x)=> (x*100).toFixed(1)+'%';
  k.innerHTML = `
    <div class="card"><div class="muted">GMV 7j</div><div class="kpi">${(m.gmv_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Commandes 7j</div><div class="kpi">${m.orders_7d||0}</div></div>
    <div class="card"><div class="muted">AOV 7j</div><div class="kpi">${(m.aov_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Recovered GMV 7j</div><div class="kpi">${(m.recovered_gmv_7d||0).toFixed(0)}</div></div>
    <div class="card"><div class="muted">Recovered Orders 7j</div><div class="kpi">${m.recovered_orders_7d||0}</div></div>
    <div class="card"><div class="muted">Recovered Rate 7j</div><div class="kpi">${m.ab_failed_7d? pct(m.recovered_rate_7d||0): 'n/a'}</div></div>
    <div class="card"><div class="muted">Abandons 24h</div><div class="kpi">${m.abandoned_24h||0}</div></div>
    <div class="card"><div class="muted">Failed 24h</div><div class="kpi">${m.failed_24h||0}</div></div>
    <div class="card"><div class="muted">Relances échues</div><div class="kpi">${m.pending_due||0}</div></div>
    <div class="card"><div class="muted">Relances planifiées</div><div class="kpi">${m.pending_future||0}</div></div>
    <div class="card"><div class="muted">24h Email</div><div class="kpi">${m.sent_24h_email||0}</div></div>
    <div class="card"><div class="muted">24h WhatsApp</div><div class="kpi">${m.sent_24h_whatsapp||0}</div></div>
    <div class="card"><div class="muted">Erreurs 24h</div><div class="kpi ko">${m.errors_24h||0}</div></div>
  `;

  const steps = document.getElementById('steps');
  steps.innerHTML =
    (m.conversions_by_step_7d||[])
      .map(r=>`<span class="pill">${r.step}: ${r.conv} (${(r.gmv||0).toFixed(0)})</span>`)
      .join('') || '<div class="muted">Aucune donnée</div>';

  function table(id, rows, cols){
    const t = document.getElementById(id);
    if(!rows || !rows.length){t.innerHTML='<tr><td class="muted">Aucune donnée</td></tr>'; return;}
    t.innerHTML = '<thead><tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead>'+
                  '<tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${r[c[0]]??''}</td>`).join('')+'</tr>').join('')+'</tbody>';
  }
  table('topprod', m.top_products_7d, [['product_id','Produit'],['orders','Cmds'],['gmv','GMV']]);
  table('countries', m.countries_7d, [['country','Pays'],['orders','Cmds'],['gmv','GMV']]);
  table('rfm', m.rfm_segments, [['segment','Segment'],['customers','Clients'],['gmv','GMV']]);
}
load();
</script>

"""
    return html
