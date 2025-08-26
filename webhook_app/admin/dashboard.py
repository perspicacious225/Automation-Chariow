# # webhook_app/admin/dashboard.py
# from flask import Blueprint, jsonify, render_template_string, request, Response, redirect, make_response
# import sqlite3, csv, io
# import sqlite3, os, html as _html
# from webhook_app.config import Config
# from webhook_app.utils.database import upsert_template
# dashboard_bp = Blueprint("dashboard", __name__)

# def _guard():
#     tok = getattr(Config, "DASHBOARD_TOKEN", None)
#     if not tok:
#         return True  # pas de token => dashboard ouvert
#     # 1) header accepté (compatibilité outils externes)
#     if request.headers.get("X-Dashboard-Token") == tok:
#         return True
#     # 2) cookie HttpOnly
#     if request.cookies.get("dash_t") == tok:
#         return True
#     return False

# # def _guard():
# #     tok = getattr(Config, "DASHBOARD_TOKEN", None)
# #     if not tok:
# #         return True
# #     # Autorise soit l'entête, soit ?token=...
# #     return (
# #         request.headers.get("X-Dashboard-Token") == tok
# #         or request.args.get("token") == tok
# #     )


# def _conn():
#     return sqlite3.connect(Config.DB_PATH)

# def _scalar(conn, sql, params=()):
#     cur = conn.execute(sql, params)
#     row = cur.fetchone()
#     return (row[0] if row else 0) or 0

# def _rows(conn, sql, params=()):
#     cur = conn.execute(sql, params)
#     cols = [c[0] for c in cur.description]
#     return [dict(zip(cols, r)) for r in cur.fetchall()]

# @dashboard_bp.route("/metrics.json")
# def metrics_json():
#     if not _guard(): return jsonify({"error":"forbidden"}), 403
#     conn = _conn()
#     try:
#         data = {}
#         # Relances en file
#         data["pending_due"]    = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE sent_at IS NOT NULL AND due_at <= datetime('now')")
#         data["pending_future"] = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE sent_at IS NULL AND due_at >  datetime('now')")
#         data["relance_customer_pending_count"] = _scalar(conn, "SELECT COUNT(DISTINCT sale_id) as client_unique_relance FROM scheduled_notifications WHERE sent_at IS NULL AND due_at > datetime('now')")

#         # Envois 24h par canal
#         sent24 = _rows(conn, "SELECT channel, COUNT(*) cnt FROM notification_log WHERE sent_at >= datetime('now','-1 day') GROUP BY channel")
#         sent24 = {r["channel"]: r["cnt"] for r in sent24}
#         data["sent_24h_email"]    = sent24.get("email",0)
#         data["sent_24h_whatsapp"] = sent24.get("whatsapp",0)

#         # Erreurs 24h & cadences actives
#         data["errors_24h"] = _scalar(conn, "SELECT COUNT(*) FROM scheduled_notifications WHERE error IS NOT NULL AND sent_at >= datetime('now','-1 day')")
#         data["cadences_active"] = _scalar(conn, "SELECT COUNT(DISTINCT COALESCE(contact_key,'')||'|'||COALESCE(product_id,'')) FROM scheduled_notifications WHERE sent_at IS NULL")

#         # Ventes 1j
#         data["gmv_1d"] = _scalar(conn,
#           "SELECT COALESCE(SUM(amount_value-(amount_value*0.15)),0) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','start of day')"
#         )
#         # Ventes 7j
#         data["gmv_7d"] = _scalar(conn,
#           "SELECT COALESCE(SUM(amount_value-(amount_value*0.15)),0) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')"
#         )
#         data["orders_7d"] = _scalar(conn,
#           "SELECT COUNT(*) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')"
#         )
#         data["orders_1d"] = _scalar(conn,
#           "SELECT COUNT(*) FROM fact_sales WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','start of day')"
#         )
#         data["aov_7d"] = (data["gmv_7d"] / data["orders_7d"]) if data["orders_7d"] else 0.0

#         # Abandons/failed 24h
#         data["abandoned_24h"] = _scalar(conn,
#           "SELECT COUNT(*) FROM fact_sales WHERE status='abandoned' AND COALESCE(abandoned_at,created_at) >= datetime('now','start of day')"
#         )
#         data["failed_24h"] = _scalar(conn,
#           "SELECT COUNT(*) FROM fact_sales WHERE status='failed' AND COALESCE(created_at,completed_at) >= datetime('now','start of day')"
#         )

#         # Recovered 7j (orders + GMV) et taux de panier récupéré
#         data["recovered_orders_7d"] = _scalar(conn, """
#           WITH ab AS (
#             SELECT product_id, contact_key, MAX(COALESCE(abandoned_at,created_at)) AS last_ab
#             FROM fact_sales
#             WHERE status IN ('abandoned','failed')
#               AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
#             GROUP BY product_id, contact_key
#           ),
#           co AS (
#             SELECT sale_id, amount_value, COALESCE(completed_at,created_at) AS t, product_id, contact_key
#             FROM fact_sales
#             WHERE status='completed'
#               AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
#           )
#           SELECT COUNT(*) FROM co JOIN ab USING(product_id,contact_key)
#           WHERE co.t BETWEEN ab.last_ab AND datetime(ab.last_ab,'+72 hour')
#         """)
#         data["recovered_gmv_7d"] = _scalar(conn, """
#           WITH ab AS (
#             SELECT product_id, contact_key, MAX(COALESCE(abandoned_at,created_at)) AS last_ab
#             FROM fact_sales
#             WHERE status IN ('abandoned','failed')
#               AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
#             GROUP BY product_id, contact_key
#           ),
#           co AS (
#             SELECT sale_id, amount_value, COALESCE(completed_at,created_at) AS t, product_id, contact_key
#             FROM fact_sales
#             WHERE status='completed'
#               AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
#           )
#           SELECT COALESCE(SUM(co.amount_value-(co.amount_value*0.15)),0) FROM co JOIN ab USING(product_id,contact_key)
#           WHERE co.t BETWEEN ab.last_ab AND datetime(ab.last_ab,'+72 hour')
#         """)
#         data["ab_failed_7d"] = _scalar(conn, """
#           SELECT COUNT(*) FROM fact_sales
#           WHERE status IN ('abandoned','failed')
#             AND COALESCE(abandoned_at,created_at) >= datetime('now','-7 day')
#         """)
#         data["recovered_rate_7d"] = (data["recovered_orders_7d"] / data["ab_failed_7d"]) if data["ab_failed_7d"] else 0.0

#         # Conversions par step (via dernière relance avant la vente)
#         conv_by_step = _rows(conn, """
#         WITH co AS (
#           SELECT sale_id, product_id, contact_key,
#                  COALESCE(completed_at,created_at) AS completed_at, amount_value
#           FROM fact_sales
#           WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
#         ),
#         lastrel AS (
#           SELECT nl.contact_key, nl.product_id, nl.template_type, nl.sent_at
#           FROM notification_log nl
#           WHERE nl.template_type LIKE 'relance_%'
#         )
#         SELECT x.template_type AS step, COUNT(*) AS conv, COALESCE(SUM(co.amount_value),0) AS gmv
#         FROM (
#           SELECT c.sale_id,
#                  (SELECT lr.template_type FROM lastrel lr
#                   WHERE lr.contact_key=c.contact_key AND lr.product_id=c.product_id AND lr.sent_at<=c.completed_at
#                   ORDER BY lr.sent_at DESC LIMIT 1) AS template_type
#           FROM co c
#         ) x JOIN fact_sales co ON co.sale_id = x.sale_id
#         WHERE x.template_type IS NOT NULL
#         GROUP BY x.template_type
#         ORDER BY x.template_type
#         """)
#         data["conversions_by_step_7d"] = conv_by_step

#         # A/B split (envois par arm)
#         data["ab_sent_7d"] = _rows(conn, """
#           SELECT ab_arm, COUNT(*) AS cnt
#           FROM notification_log
#           WHERE sent_at >= datetime('now','-7 day')
#           GROUP BY ab_arm
#         """)

#         # Segmentation RFM (vue globale)
#         data["rfm_segments"] = _rows(conn, """
#           SELECT rfm_segment AS segment, COUNT(*) AS customers, COALESCE(SUM(gmv_total),0) AS gmv
#           FROM dim_customer
#           GROUP BY rfm_segment
#           ORDER BY gmv DESC
#         """)

#         # Top produits & pays (7j)
#         data["top_products_7d"] = _rows(conn, """
#           SELECT COALESCE(product_id,'(n/a)') AS product_id, COUNT(*) AS orders, COALESCE(SUM(amount_value),0) AS gmv
#           FROM fact_sales WHERE status='completed'
#             AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
#           GROUP BY COALESCE(product_id,'(n/a)')
#           ORDER BY gmv DESC LIMIT 5
#         """)
#         data["countries_7d"] = _rows(conn, """
#           SELECT COALESCE(country,'(n/a)') AS country, COUNT(*) AS orders, COALESCE(SUM(amount_value),0) AS gmv
#           FROM fact_sales WHERE status='completed'
#             AND COALESCE(completed_at,created_at) >= datetime('now','-7 day')
#           GROUP BY COALESCE(country,'(n/a)')
#           ORDER BY gmv DESC LIMIT 5
#         """)

#         return jsonify(data)
#     finally:
#         conn.close()

# @dashboard_bp.route("/ts.json")
# def ts_json():
#     if not _guard(): return jsonify({"error":"forbidden"}), 403
#     conn = _conn()
#     try:
#         rows = _rows(conn, """
#           SELECT strftime('%Y-%m-%d', COALESCE(completed_at,created_at)) AS d,
#                  SUM(CASE WHEN status='completed' THEN amount_value ELSE 0 END) AS gmv,
#                  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS orders,
#                  SUM(CASE WHEN status='abandoned' THEN 1 ELSE 0 END) AS abandoned
#           FROM fact_sales
#           WHERE COALESCE(created_at, completed_at) >= datetime('now','-30 day')
#           GROUP BY strftime('%Y-%m-%d', COALESCE(completed_at,created_at))
#           ORDER BY d
#         """)
#         return jsonify(rows)
#     finally:
#         conn.close()

# @dashboard_bp.route("/heatmap.json")
# def heatmap_json():
#     if not _guard(): return jsonify({"error":"forbidden"}), 403
#     conn = _conn()
#     try:
#         rows = _rows(conn, """
#           SELECT dow, hour_of_day, COUNT(*) AS cnt
#           FROM fact_sales
#           WHERE status='completed' AND COALESCE(completed_at,created_at) >= datetime('now','-30 day')
#           GROUP BY dow, hour_of_day
#         """)
#         return jsonify(rows)
#     finally:
#         conn.close()

# @dashboard_bp.route("/export.csv")
# def export_csv():
#     if not _guard(): return jsonify({"error":"forbidden"}), 403
#     conn = _conn()
#     try:
#         q = """
#         SELECT sale_id,status,amount_value,currency,product_id,product_name,store_name,
#                contact_key,email,phone,country,created_at,completed_at,abandoned_at,
#                time_to_complete_min,hour_of_day,dow,month,utm_source,utm_medium,utm_campaign,price_tier
#         FROM fact_sales
#         WHERE COALESCE(created_at,completed_at) >= datetime('now','-90 day')
#         ORDER BY COALESCE(completed_at,created_at) DESC
#         """
#         rows = _rows(conn, q)
#     finally:
#         conn.close()
#     si = io.StringIO()
#     if rows:
#         w = csv.DictWriter(si, fieldnames=list(rows[0].keys()))
#         w.writeheader()
#         for r in rows: w.writerow(r)
#     else:
#         si.write("")
#     return Response(si.getvalue(), mimetype="text/csv",
#                     headers={"Content-Disposition": "attachment; filename=export_90d.csv"})


# @dashboard_bp.route("/login")
# def dashboard_login():
#     tok = getattr(Config, "DASHBOARD_TOKEN", None)
#     given = request.args.get("token")
#     nxt = request.args.get("next") or "/dashboard/"
#     if not tok:
#         # pas de token configuré => rien à faire
#         return redirect(nxt)
#     if given != tok:
#         return "forbidden", 403
#     resp = make_response(redirect(nxt))
#     # 7 jours, HttpOnly ; tu peux mettre secure=True si toujours en HTTPS (ex: Render prod)
#     resp.set_cookie("dash_t", tok, max_age=7*24*3600, httponly=True, samesite="Lax")
#     return resp

# @dashboard_bp.route("/logout")
# def dashboard_logout():
#     nxt = request.args.get("next") or "/dashboard/"
#     resp = make_response(redirect(nxt))
#     resp.delete_cookie("dash_t")
#     return resp


# @dashboard_bp.route("/", methods=["GET"])
# def dashboard_view():
#     if not _guard(): return "forbidden", 403
#     html = """
# <!doctype html><meta charset="utf-8"><title>Dashboard KPI</title>
# <style>
# body{font-family:system-ui,Segoe UI,Roboto,Ubuntu,sans-serif;background:#0b1220;color:#e6edf7;margin:0;padding:24px}
# h1{margin:0 0 16px;font-size:22px}
# .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;margin-bottom:20px}
# .card{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px}
# .kpi{font-size:28px;font-weight:700;margin:6px 0}.muted{color:#8892b0;font-size:12px}
# table{width:100%;border-collapse:collapse;margin-top:8px} th,td{padding:8px;border-bottom:1px solid rgba(255,255,255,.06);font-size:13px}
# .tag{display:inline-block;background:#0D47A122;border:1px solid #0D47A144;color:#cfe3ff;padding:2px 8px;border-radius:999px;font-size:12px}
# .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.06);margin:2px 6px 2px 0;font-size:12px}
# .ok{color:#7ee787}.ko{color:#ff7b72}
# </style>
# <h1>Dashboard KPI — Relances & Ventes</h1>
# <div class="grid" id="kpis"></div>
# <div class="card"><div class="muted">Conversions par step (7j)</div><div id="steps"></div></div>
# <div class="grid">
#   <div class="card"><div class="muted">Top produits (7j)</div><table id="topprod"></table></div>
#   <div class="card"><div class="muted">Pays (7j)</div><table id="countries"></table></div>
#   <div class="card"><div class="muted">Segments RFM</div><table id="rfm"></table></div>
# </div>
# <script>
# async function load(){
#   // --- NEW: pick up token from URL and/or send as header ---
#   const p = new URLSearchParams(location.search);
#   const token   = p.get('token') || '';
#   const headers = token ? { 'X-Dashboard-Token': token } : {};
#   const qs      = token ? ('?token=' + encodeURIComponent(token)) : '';

#   // Use token for the API call (both query string and header, works with either guard)
#   const resp = await fetch('/dashboard/metrics.json' + qs, { headers });
#   const m = await resp.json();

#   const k = document.getElementById('kpis');
#   const pct = (x)=> (x*100).toFixed(1)+'%';
#   k.innerHTML = `
#     <div class="card"><div class="muted">GMV 1j</div><div class="kpi">${(m.gmv_1d||0).toFixed(0)}</div></div>
#     <div class="card"><div class="muted">Commandes 1j</div><div class="kpi">${m.orders_1d||0}</div></div>
#     <div class="card"><div class="muted">GMV 7j</div><div class="kpi">${(m.gmv_7d||0).toFixed(0)}</div></div>
#     <div class="card"><div class="muted">Commandes 7j</div><div class="kpi">${m.orders_7d||0}</div></div>
#     <div class="card"><div class="muted">AOV 7j</div><div class="kpi">${(m.aov_7d||0).toFixed(0)}</div></div>
#     <div class="card"><div class="muted">Recovered GMV 7j</div><div class="kpi">${(m.recovered_gmv_7d||0).toFixed(0)}</div></div>
#     <div class="card"><div class="muted">Recovered Orders 7j</div><div class="kpi">${m.recovered_orders_7d||0}</div></div>
#     <div class="card"><div class="muted">Recovered Rate 7j</div><div class="kpi">${m.ab_failed_7d? pct(m.recovered_rate_7d||0): 'n/a'}</div></div>
#     <div class="card"><div class="muted">Abandons today</div><div class="kpi">${m.abandoned_24h||0}</div></div>
#     <div class="card"><div class="muted">Failed today</div><div class="kpi">${m.failed_24h||0}</div></div>
#     <div class="card"><div class="muted">Relances échues</div><div class="kpi">${m.pending_due||0}</div></div>
#     <div class="card"><div class="muted">Relances planifiées</div><div class="kpi">${m.pending_future||0}</div></div>
#     <div class="card"><div class="muted">Clients programmés pour relance</div><div class="kpi">${m.relance_customer_pending_count||0}</div></div>
#     <div class="card"><div class="muted">24h Email</div><div class="kpi">${m.sent_24h_email||0}</div></div>
#     <div class="card"><div class="muted">24h WhatsApp</div><div class="kpi">${m.sent_24h_whatsapp||0}</div></div>
#     <div class="card"><div class="muted">Erreurs 24h</div><div class="kpi ko">${m.errors_24h||0}</div></div>
#   `;

#   const steps = document.getElementById('steps');
#   steps.innerHTML =
#     (m.conversions_by_step_7d||[])
#       .map(r=>`<span class="pill">${r.step}: ${r.conv} (${(r.gmv||0).toFixed(0)})</span>`)
#       .join('') || '<div class="muted">Aucune donnée</div>';

#   function table(id, rows, cols){
#     const t = document.getElementById(id);
#     if(!rows || !rows.length){t.innerHTML='<tr><td class="muted">Aucune donnée</td></tr>'; return;}
#     t.innerHTML = '<thead><tr>'+cols.map(c=>`<th>${c[1]}</th>`).join('')+'</tr></thead>'+
#                   '<tbody>'+rows.map(r=>'<tr>'+cols.map(c=>`<td>${r[c[0]]??''}</td>`).join('')+'</tr>').join('')+'</tbody>';
#   }
#   table('topprod', m.top_products_7d, [['product_id','Produit'],['orders','Cmds'],['gmv','GMV']]);
#   table('countries', m.countries_7d, [['country','Pays'],['orders','Cmds'],['gmv','GMV']]);
#   table('rfm', m.rfm_segments, [['segment','Segment'],['customers','Clients'],['gmv','GMV']]);
# }
# load();
# </script>

# """
#     return html


# @dashboard_bp.route("/templates", methods=["GET"])
# def templates_products():
#     if not _guard(): return "forbidden", 403
#     import html

#     conn = _conn(); conn.row_factory = sqlite3.Row
#     try:
#         rows = conn.execute("""
#             SELECT
#               p.product_id,
#               p.product_name,
#               COALESCE(COUNT(t.id), 0)                 AS nb_tpl,
#               COALESCE(SUM(CASE WHEN t.channel='email' THEN 1 ELSE 0 END), 0)    AS nb_email,
#               COALESCE(SUM(CASE WHEN t.channel='whatsapp' THEN 1 ELSE 0 END), 0) AS nb_whatsapp
#             FROM dim_product p
#             LEFT JOIN message_templates t ON t.product_id = p.product_id
#             GROUP BY p.product_id, p.product_name
#             ORDER BY p.product_name ASC
#         """).fetchall()

#         # compteur globaux
#         globals_cnt = conn.execute("""
#             SELECT
#               COALESCE(COUNT(id),0)                                     AS nb_tpl,
#               COALESCE(SUM(CASE WHEN channel='email' THEN 1 END),0)     AS nb_email,
#               COALESCE(SUM(CASE WHEN channel='whatsapp' THEN 1 END),0)  AS nb_whatsapp
#             FROM message_templates
#             WHERE product_id IS NULL
#         """).fetchone()
#     finally:
#         conn.close()

#     def row_html(r):
#         pid = r["product_id"]
#         pname = r["product_name"] or "(sans nom)"
#         return f"""
#         <tr>
#           <td>{html.escape(pname)}</td>
#           <td>
#             <span class="pill">total: {r['nb_tpl']}</span>
#             <span class="pill">email: {r['nb_email']}</span>
#             <span class="pill">wa: {r['nb_whatsapp']}</span>
#           </td>
#           <td style="white-space:nowrap">
#             <a class="btn" href="/dashboard/templates/view?id={pid}">Voir</a>
#             <a class="btn secondary" href="/dashboard/templates/view/edit?pid={pid}">+ Ajouter un template</a>
#             <form method="post" action="/dashboard/templates/delete_product" style="display:inline"
#                   onsubmit="return confirm('Supprimer ce produit ET ses templates ?');">
#               <input type="hidden" name="product_id" value="{pid}">
#               <button class="btn danger" type="submit">Supprimer</button>
#             </form>
#           </td>
#         </tr>
#         """

#     html_page = f"""
# <!doctype html><meta charset="utf-8"><title>Templates par produit</title>
# <style>
# body{{font-family:system-ui,Segoe UI,Roboto,Ubuntu,sans-serif;background:#0b1220;color:#e6edf7;margin:0;padding:24px}}
# h1{{margin:0 0 16px;font-size:22px}}
# .card{{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px}}
# .actions{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
# .btn{{display:inline-block;background:#1f6feb;border:1px solid #275ea6;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none;font-size:14px}}
# .btn.secondary{{background:transparent;border-color:rgba(255,255,255,.2);color:#e6edf7}}
# .btn.danger{{background:#7a3131;border-color:#a43d3d;cursor:pointer}}
# table{{width:100%;border-collapse:collapse;margin-top:8px}}
# th,td{{padding:10px;border-bottom:1px solid rgba(255,255,255,.06);font-size:14px;vertical-align:top}}
# th{{text-align:left;color:#9fb4d5}}
# .pill{{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.06);font-size:12px;margin-right:6px}}
# .muted{{color:#8892b0}}
# </style>

# <h1>Templates — par produit</h1>
# <div class="actions">
#   <div class="muted">Crée et gère des messages par produit et par étape. </div>
#   <div>
#     <a class="btn secondary" href="/dashboard/templates/globals">Templates globaux ({globals_cnt['nb_tpl']} total)</a>
#     <a class="btn" href="/dashboard/templates/view/edit">+ Nouveau template (global)</a>
#   </div>
# </div>

# <div class="card">
#   <table>
#     <thead>
#       <tr><th>Produit</th><th>Templates</th><th>Actions</th></tr>
#     </thead>
#     <tbody>
#       {''.join(row_html(r) for r in rows) if rows else "<tr><td colspan='3' class='muted'>Aucun produit</td></tr>"}
#     </tbody>
#   </table>
# </div>
# """
#     return html_page

# @dashboard_bp.route("/templates/view", methods=["GET"])
# def templates_for_product():
#     if not _guard(): return "forbidden", 403
#     import html

#     pid = request.args.get("id")
#     if not pid:
#         return redirect("/dashboard/templates")

#     conn = _conn(); conn.row_factory = sqlite3.Row
#     try:
#         prod = conn.execute("SELECT product_name FROM dim_product WHERE product_id=?", (pid,)).fetchone()
#         rows = conn.execute("""
#             SELECT id, product_id, template_type, channel, is_full_html, is_active, updated_at
#             FROM message_templates
#             WHERE product_id = ?
#             ORDER BY template_type, channel
#         """, (pid,)).fetchall()
#     finally:
#         conn.close()

#     pname = (prod["product_name"] if prod else "(inconnu)")

#     def row_html(r):
#         return f"""
#         <tr>
#           <td><code>{html.escape(r['template_type'])}</code></td>
#           <td>{html.escape(r['channel'])}</td>
#           <td>{'Oui' if r['is_full_html'] else 'Non'}</td>
#           <td>{'actif' if r['is_active'] else 'inactif'}</td>
#           <td class="muted">{html.escape(r['updated_at'] or '')}</td>
#           <td style="white-space:nowrap">
#             <a class="btn" href="/dashboard/templates/preview?id={r['id']}" target="_blank">Prévisualiser</a>
#             <a class="btn" href="/dashboard/templates/view/edit?id={r['id']}">Éditer</a>
#             <form method="post" action="/dashboard/templates/delete" style="display:inline"
#                   onsubmit="return confirm('Supprimer ce template ?');">
#               <input type="hidden" name="id" value="{r['id']}">
#               <button class="btn danger" type="submit">Supprimer</button>
#             </form>
#           </td>
#         </tr>
#         """

#     html_page = f"""
# <!doctype html><meta charset="utf-8"><title>Templates — {pname}</title>
# <style>
# body{{font-family:system-ui,Segoe UI,Roboto,Ubuntu,sans-serif;background:#0b1220;color:#e6edf7;margin:0;padding:24px}}
# h1{{margin:0 0 16px;font-size:22px}}
# .card{{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px}}
# .actions{{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}}
# .btn{{display:inline-block;background:#1f6feb;border:1px solid #275ea6;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none;font-size:14px}}
# .btn.secondary{{background:transparent;border-color:rgba(255,255,255,.2);color:#e6edf7}}
# .btn.danger{{background:#7a3131;border-color:#a43d3d;cursor:pointer}}
# table{{width:100%;border-collapse:collapse;margin-top:8px}}
# th,td{{padding:10px;border-bottom:1px solid rgba(255,255,255,.06);font-size:14px;vertical-align:top}}
# th{{text-align:left;color:#9fb4d5}}
# .muted{{color:#8892b0}}
# code{{background:rgba(255,255,255,.05);padding:2px 6px;border-radius:4px}}
# </style>

# <h1>Templates — {html.escape(pname)}</h1>
# <div class="actions">
#   <a class="btn secondary" href="/dashboard/templates">← Retour produits</a>
#   <a class="btn" href="/dashboard/templates/view/edit?pid={pid}">+ Nouveau template</a>
# </div>

# <div class="card">
#   <table>
#     <thead>
#       <tr><th>Type</th><th>Canal</th><th>HTML complet</th><th>Statut</th><th>Modifié</th><th>Actions</th></tr>
#     </thead>
#     <tbody>
#       {''.join(row_html(r) for r in rows) if rows else "<tr><td colspan='6' class='muted'>Aucun template pour ce produit</td></tr>"}
#     </tbody>
#   </table>
# </div>
# """
#     return html_page



# @dashboard_bp.route("/templates/view/edit", methods=["GET","POST"])
# def edit_template():
#     if not _guard(): return "forbidden", 403
#     import html as _html

#     # types proposés
#     suggested_types = [
#         "relance_t30","relance_t6h","relance_t23h","relance_t47h",
#         "confirm_3_1","confirm_3_2","confirm_3_3","confirm_3_4","confirm_3_5"
#     ]

#     if request.method == "POST":
#         product_id    = request.form.get("product_id") or None
#         template_type = (request.form.get("template_type") or "").strip()
#         channel       = (request.form.get("channel") or "email").strip()
#         subject       = request.form.get("subject") or ""
#         body          = request.form.get("body") or ""
#         is_full_html  = bool(request.form.get("is_full_html"))
#         is_active     = bool(request.form.get("is_active"))
#         if not template_type:
#             return "template_type requis", 400
#         upsert_template(product_id, template_type, channel, subject, body, is_full_html, is_active)
#         # redirection contextuelle
#         if product_id:
#             return redirect(f"/dashboard/templates/view?id={product_id}")
#         else:
#             return redirect("/dashboard/templates/globals")

#     # --- GET ---
#     rid = request.args.get("id")
#     pid_prefill = request.args.get("pid")  # pour préremplir en “nouveau”
#     row = None
#     if rid:
#         conn = _conn(); conn.row_factory = sqlite3.Row
#         row = conn.execute("SELECT * FROM message_templates WHERE id=?", (rid,)).fetchone()
#         conn.close()

#     def val(k, default=""):
#         if row is None:
#             if k == 'product_id' and pid_prefill:
#                 return pid_prefill
#             return default
#         try:
#             return row[k] if row[k] is not None else default
#         except Exception:
#             return default

#     v_pid  = _html.escape(str(val('product_id','') or ''), quote=True)
#     v_type = _html.escape(str(val('template_type','') or ''), quote=True)
#     v_chan = str(val('channel','email') or 'email')
#     v_subj = _html.escape(str(val('subject','') or ''), quote=True)
#     v_body = _html.escape(str(val('body','') or ''))
#     ck_full = 'checked' if val('is_full_html',0) else ''
#     ck_act  = 'checked' if (val('is_active',1) or 1) else ''
#     options_type = "".join(f"<option value='{_html.escape(t, True)}'></option>" for t in suggested_types)

#     html_page = f"""
# <!doctype html><meta charset="utf-8"><title>{'Modifier' if rid else 'Nouveau'} template</title>
# <style>
# body{{font-family:system-ui,Segoe UI,Roboto,Ubuntu,sans-serif;background:#0b1220;color:#e6edf7;margin:0;padding:24px}}
# h1{{margin:0 0 16px;font-size:22px}}
# .card{{background:#121a2b;border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:16px;max-width:980px;margin:0 auto}}
# .grid{{display:grid;grid-template-columns:1fr;gap:14px}}
# label{{display:block;font-size:13px;color:#9fb4d5;margin-bottom:6px}}
# input[type=text], select, textarea{{width:100%;box-sizing:border-box;background:#0b1220;border:1px solid rgba(255,255,255,.15);color:#e6edf7;border-radius:8px;padding:10px}}
# textarea{{min-height:260px;resize:vertical;line-height:1.45}}
# .row2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
# .ctrls{{display:flex;gap:10px;margin-top:10px;justify-content:center}}
# .btn{{display:inline-block;background:#1f6feb;border:1px solid #275ea6;color:#fff;padding:10px 14px;border-radius:8px;text-decoration:none;font-size:14px;cursor:pointer}}
# .btn.secondary{{background:transparent;border-color:rgba(255,255,255,.25);color:#e6edf7}}
# .muted{{color:#8892b0;font-size:12px}}
# .help{{background:#0f172a;border:1px dashed rgba(255,255,255,.15);padding:10px;border-radius:8px;margin-top:8px}}
# .badge{{display:inline-block;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);padding:2px 8px;border-radius:999px;font-size:12px;margin-right:6px}}
# .center{{max-width:980px;margin:0 auto}}
# </style>

# <h1 class="center">{'Modifier' if rid else 'Nouveau'} template</h1>
# <div class="card">
#   <form method="post" id="tplForm">
#     <div class="grid">
#       <div>
#         <label>Produit (laisser vide =&nbsp;global)</label>
#         <input type="text" name="product_id" value="{v_pid}" placeholder="ex: prd_k3eyyy">
#       </div>

#       <div class="row2">
#         <div>
#           <label>Template type</label>
#           <input list="tplTypes" type="text" name="template_type" value="{v_type}" required
#                  placeholder="ex: relance_t30, relance_t6h, confirm_3_2">
#           <datalist id="tplTypes">{options_type}</datalist>
#         </div>
#         <div>
#           <label>Canal</label>
#           <select name="channel">
#             <option value="email" {"selected" if v_chan=="email" else ""}>email</option>
#             <option value="whatsapp" {"selected" if v_chan=="whatsapp" else ""}>whatsapp</option>
#           </select>
#         </div>
#       </div>

#       <div>
#         <label>Sujet (email uniquement)</label>
#         <input type="text" name="subject" value="{v_subj}" placeholder="ex: Un souci avec ta commande ?">
#       </div>

#       <div>
#         <label>Corps</label>
#         <textarea name="body" id="bodyArea" spellcheck="false"
#                   placeholder="HTML complet (si coché) ou fragment HTML (email), texte brut (WhatsApp)">{v_body}</textarea>
#         <div class="help muted">
#           <div style="margin-bottom:6px">Placeholders disponibles :</div>
#           <span class="badge">{{{{customer_first_name}}}}</span>
#           <span class="badge">{{{{customer_email}}}}</span>
#           <span class="badge">{{{{product_name}}}}</span>
#           <span class="badge">{{{{checkout_url}}}}</span>
#           <span class="badge">{{{{price_current_fmt}}}}</span>
#           <span class="badge">{{{{price_after_fmt}}}}</span>
#           <span class="badge">{{{{store_name}}}}</span>
#           <span class="badge">{{{{store_url}}}}</span>
#           <span class="badge">{{{{sale_id}}}}</span>
#           <span class="badge">{{{{current_year}}}}</span>
#         </div>
#       </div>

#       <div class="row2">
#         <label><input type="checkbox" name="is_full_html" {ck_full}> HTML complet (email)</label>
#         <label><input type="checkbox" name="is_active" {ck_act}> Actif</label>
#       </div>

#       <div class="ctrls">
#         <button class="btn" type="submit">Enregistrer</button>
#         <a class="btn secondary" href="{f'/dashboard/templates/view?id={pid_prefill}' if pid_prefill else '/dashboard/templates'}">Annuler</a>
#         <button class="btn secondary" type="button" onclick="doPreview()">Prévisualiser</button>
#       </div>
#     </div>
#   </form>
# </div>

# <script>
# async function doPreview(){{
#   const f = document.getElementById('tplForm');
#   const fd = new FormData(f);
#   const payload = {{
#     body: fd.get('body') || "",
#     is_full_html: !!fd.get('is_full_html')
#   }};
#   try {{
#     const res = await fetch('/dashboard/templates/preview', {{
#       method: 'POST',
#       headers: {{ 'Content-Type': 'application/json' }},
#       body: JSON.stringify(payload)
#     }});
#     const html = await res.text();
#     const w = window.open('', '_blank');
#     w.document.open(); w.document.write(html); w.document.close();
#   }} catch(err) {{
#     alert('Prévisualisation indisponible: ' + err);
#   }}
# }}
# </script>
# """
#     return html_page

# @dashboard_bp.route("/templates/delete", methods=["POST"])
# def delete_template():
#     if not _guard(): return "forbidden", 403
#     rid = request.form.get("id")
#     if not rid:
#         return "id requis", 400
#     try:
#         conn = sqlite3.connect(Config.DB_PATH)
#         with conn:
#             conn.execute("DELETE FROM message_templates WHERE id=?", (rid,))
#     except Exception as e:
#         return f"Erreur suppression: {e}", 500
#     return redirect("/dashboard/templates")
# @dashboard_bp.route("/templates/delete_product", methods=["POST"])
# def delete_product():
#     if not _guard(): return "forbidden", 403
#     pid = request.form.get("product_id")
#     if not pid:
#         return "product_id requis", 400
#     try:
#         conn = _conn()
#         with conn:
#             conn.execute("DELETE FROM message_templates WHERE product_id=?", (pid,))
#             conn.execute("DELETE FROM dim_product WHERE product_id=?", (pid,))
#     except Exception as e:
#         return f"Erreur suppression: {e}", 500
#     return redirect("/dashboard/templates")



# @dashboard_bp.route("/templates/preview", methods=["GET", "POST"])
# def preview_template():
#     if not _guard():
#         return "forbidden", 403

#     # Rendu d’email de marque si dispo, sinon fallback simple
#     try:
#         from webhook_app.services.mailer import render_email_with_brand as _brand_wrap
#     except Exception:
#         def _brand_wrap(fragment_html, tvars):
#             return f"""<!doctype html><meta charset="utf-8">
#             <title>Preview</title>
#             <div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:24px auto;padding:16px;border:1px solid #eee;border-radius:8px">
#               {fragment_html}
#             </div>"""

#     # Variables de test (tu peux les enrichir)
#     tvars = {
#         "customer_first_name": "Jean",
#         "customer_email": "jean@example.com",
#         "product_name": "Microsoft 365 à vie",
#         "checkout_url": "https://digitechhub.store/checkout/test",
#         "price_current_fmt": "5 000 FCFA",
#         "price_after_fmt": "15 000 FCFA",
#         "store_name": "Digitech Hub",
#         "store_url": "https://digitechhub.store",
#         "sale_id": "S123456",
#         "current_year": "2025",
#     }

#     def _render_body(body: str, is_full_html: bool):
#         try:
#             html = body.format_map({k: v for k, v in tvars.items()})
#         except Exception as e:
#             # Informe sur le placeholder manquant
#             html = f"<pre style='color:#c33'>Erreur de formatage: {e}</pre>\n" + body
#         if not is_full_html:
#             html = _brand_wrap(html, tvars)
#         return html

#     if request.method == "POST":
#         data = request.get_json(force=True)
#         body = data.get("body", "")
#         is_full_html = bool(data.get("is_full_html"))
#         html = _render_body(body, is_full_html)
#         return html, 200, {"Content-Type": "text/html; charset=utf-8"}

#     # --- GET ---
#     # 1) ?id=<ID>  -> prévisualise un template sauvegardé
#     rid = request.args.get("id")
#     if rid:
#         conn = sqlite3.connect(Config.DB_PATH); conn.row_factory = sqlite3.Row
#         row = conn.execute("SELECT body, is_full_html FROM message_templates WHERE id=?", (rid,)).fetchone()
#         conn.close()
#         if not row:
#             return "Template introuvable", 404
#         body = row["body"] or ""
#         is_full_html = bool(row["is_full_html"])
#         html = _render_body(body, is_full_html)
#         return html, 200, {"Content-Type": "text/html; charset=utf-8"}

#     # 2) ?body=...&is_full_html=0|1  -> aperçu ad-hoc
#     if "body" in request.args:
#         body = request.args.get("body", "")
#         is_full_html = request.args.get("is_full_html", "0") in ("1", "true", "True", "yes")
#         html = _render_body(body, is_full_html)
#         return html, 200, {"Content-Type": "text/html; charset=utf-8"}

#     # 3) sinon, page d’aide rapide
#     help_html = """
#     <!doctype html><meta charset="utf-8"><title>Preview</title>
#     <div style="font-family:Segoe UI,Arial,sans-serif;max-width:720px;margin:24px auto">
#       <h2>Prévisualisation — usage</h2>
#       <p>Cette route attend un POST depuis la page d’édition ou un GET avec paramètres :</p>
#       <ul>
#         <li><code>/dashboard/templates/preview?id=&lt;ID&gt;</code> — prévisualiser un template enregistré</li>
#         <li><code>/dashboard/templates/preview?body=...&amp;is_full_html=0|1</code> — aperçu immédiat</li>
#       </ul>
#     </div>
#     """
#     return help_html, 200, {"Content-Type": "text/html; charset=utf-8"}




# webhook_app/admin/dashboard.py
from flask import Blueprint, jsonify, render_template, request, Response, redirect, make_response, url_for, abort
from flask_login import login_required, current_user
from functools import wraps
import os, sqlite3, csv, io, html as _html
from webhook_app.config import Config
from webhook_app.utils.database import upsert_template

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",  
    static_folder="static",        
    static_url_path="/static"     
)

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return fn(*args, **kwargs)
    return wrapper




def _conn():
    # PRUDENT: active row_factory au besoin dans chaque route
    return sqlite3.connect(Config.DB_PATH)

def _scalar(conn, sql, params=()):
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return (row[0] if row else 0) or 0

def _rows(conn, sql, params=()):
    cur = conn.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ------------------------------
#   ROUTES API / JSON 
# ------------------------------ 
@dashboard_bp.route("/metrics.json")
@login_required  # ou @admin_required si tu veux restreindre aux admins
def metrics_json():
    conn = _conn()
    try:
        data = {}

        # ----------------------
        # Scheduled / Notifications
        # ----------------------
        data["sent_due"] = _scalar(conn, """
            SELECT COUNT(*) FROM scheduled_notifications
            WHERE sent_at IS NOT NULL
              AND due_at <= CAST(strftime('%s','now') AS INTEGER)
        """)

        data["pending_future"] = _scalar(conn, """
            SELECT COUNT(*) FROM scheduled_notifications
            WHERE sent_at IS NULL
              AND due_at > CAST(strftime('%s','now') AS INTEGER)
        """)

        data["relance_customer_pending_count"] = _scalar(conn, """
            SELECT COUNT(DISTINCT sale_id) FROM scheduled_notifications
            WHERE sent_at IS NULL
              AND due_at > CAST(strftime('%s','now') AS INTEGER)
        """)

        # Envois 24h par canal
        sent24 = _rows(conn, """
            SELECT channel, COUNT(*) AS cnt
            FROM notification_log
            WHERE sent_at >= CAST(strftime('%s','now','-1 day') AS INTEGER)
            GROUP BY channel
        """)
        sent24 = {r["channel"]: r["cnt"] for r in sent24}
        data["sent_24h_email"]    = sent24.get("email", 0)
        data["sent_24h_whatsapp"] = sent24.get("whatsapp", 0)

        # Erreurs 24h & cadences actives
        data["errors_24h"] = _scalar(conn, """
            SELECT COUNT(*) FROM scheduled_notifications
            WHERE error IS NOT NULL
              AND sent_at >= CAST(strftime('%s','now','-1 day') AS INTEGER)
        """)
        data["cadences_active"] = _scalar(conn, """
            SELECT COUNT(DISTINCT COALESCE(contact_key,'')||'|'||COALESCE(product_id,''))
            FROM scheduled_notifications
            WHERE sent_at IS NULL
        """)

        # ----------------------
        # Sales (epoch seconds)
        # ----------------------

        # Aujourd’hui (00:00 → maintenant)
        data["gmv_1d"] = _scalar(conn, """
            SELECT COALESCE(SUM(amount_value*0.85),0)
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at, created_at) >= CAST(strftime('%s','now','start of day') AS INTEGER)
        """)

        # Hier (00:00 → 24:00)
        data["gmv__1d"] = _scalar(conn, """
            SELECT COALESCE(SUM(amount_value*0.85),0)
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at, created_at) >= CAST(strftime('%s','now','start of day','-1 day') AS INTEGER)
              AND COALESCE(completed_at, created_at) <  CAST(strftime('%s','now','start of day') AS INTEGER)
        """)

        # 7 derniers jours (roulants, maintenant-7j → maintenant)
        data["gmv_7d"] = _scalar(conn, """
            SELECT COALESCE(SUM(amount_value*0.85),0)
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at, created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
        """)

        data["orders_7d"] = _scalar(conn, """
            SELECT COUNT(*)
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at, created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
        """)

        data["orders_1d"] = _scalar(conn, """
            SELECT COUNT(*)
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at, created_at) >= CAST(strftime('%s','now','start of day') AS INTEGER)
        """)

        data["aov_7d"] = (data["gmv_7d"] / data["orders_7d"]) if data["orders_7d"] else 0.0

        # Abandons / Failed "aujourd’hui" (minuit → maintenant)
        data["abandoned_24h"] = _scalar(conn, """
            SELECT COUNT(*) FROM fact_sales
            WHERE status='abandoned'
              AND COALESCE(abandoned_at, created_at) >= CAST(strftime('%s','now','start of day') AS INTEGER)
        """)
        data["failed_24h"] = _scalar(conn, """
            SELECT COUNT(*) FROM fact_sales
            WHERE status='failed'
              AND COALESCE(created_at, completed_at) >= CAST(strftime('%s','now','start of day') AS INTEGER)
        """)

        # Recovered 7j (orders + GMV) — fenêtre de 72h après abandon
        data["recovered_orders_7d"] = _scalar(conn, """
            WITH ab AS (
              SELECT product_id, contact_key,
                     MAX(COALESCE(abandoned_at,created_at)) AS last_ab
              FROM fact_sales
              WHERE status IN ('abandoned','failed')
                AND COALESCE(abandoned_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
              GROUP BY product_id, contact_key
            ),
            co AS (
              SELECT sale_id, amount_value,
                     COALESCE(completed_at,created_at) AS t,
                     product_id, contact_key
              FROM fact_sales
              WHERE status='completed'
                AND COALESCE(completed_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            )
            SELECT COUNT(*)
            FROM co JOIN ab USING(product_id,contact_key)
            WHERE co.t BETWEEN ab.last_ab AND (ab.last_ab + 72*3600)
        """)

        data["recovered_gmv_7d"] = _scalar(conn, """
            WITH ab AS (
              SELECT product_id, contact_key,
                     MAX(COALESCE(abandoned_at,created_at)) AS last_ab
              FROM fact_sales
              WHERE status IN ('abandoned','failed')
                AND COALESCE(abandoned_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
              GROUP BY product_id, contact_key
            ),
            co AS (
              SELECT sale_id, amount_value,
                     COALESCE(completed_at,created_at) AS t,
                     product_id, contact_key
              FROM fact_sales
              WHERE status='completed'
                AND COALESCE(completed_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            )
            SELECT COALESCE(SUM(co.amount_value * 0.85),0)
            FROM co JOIN ab USING(product_id,contact_key)
            WHERE co.t BETWEEN ab.last_ab AND (ab.last_ab + 72*3600)
        """)

        data["ab_failed_7d"] = _scalar(conn, """
            SELECT COUNT(*)
            FROM fact_sales
            WHERE status IN ('abandoned','failed')
              AND COALESCE(abandoned_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
        """)
        data["recovered_rate_7d"] = (data["recovered_orders_7d"] / data["ab_failed_7d"]) if data["ab_failed_7d"] else 0.0

        # Conversions par step (dernière relance avant la vente)
        conv_by_step = _rows(conn, """
            WITH co AS (
              SELECT sale_id, product_id, contact_key,
                     COALESCE(completed_at,created_at) AS completed_at,
                     amount_value
              FROM fact_sales
              WHERE status='completed'
                AND COALESCE(completed_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            ),
            lastrel AS (
              SELECT nl.contact_key, nl.product_id, nl.template_type, nl.sent_at
              FROM notification_log nl
              WHERE nl.template_type LIKE 'relance_%'
            )
            SELECT x.template_type AS step,
                   COUNT(*) AS conv,
                   COALESCE(SUM(co.amount_value),0) AS gmv
            FROM (
              SELECT c.sale_id,
                     (SELECT lr.template_type
                      FROM lastrel lr
                      WHERE lr.contact_key=c.contact_key
                        AND lr.product_id=c.product_id
                        AND lr.sent_at <= c.completed_at
                      ORDER BY lr.sent_at DESC
                      LIMIT 1) AS template_type
              FROM co c
            ) x
            JOIN fact_sales co ON co.sale_id = x.sale_id
            WHERE x.template_type IS NOT NULL
            GROUP BY x.template_type
            ORDER BY x.template_type
        """)
        data["conversions_by_step_7d"] = conv_by_step

        # A/B split (envois par arm)
        data["ab_sent_7d"] = _rows(conn, """
            SELECT ab_arm, COUNT(*) AS cnt
            FROM notification_log
            WHERE sent_at >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            GROUP BY ab_arm
        """)

        # Segmentation RFM (vue globale)
        data["rfm_segments"] = _rows(conn, """
            SELECT rfm_segment AS segment,
                   COUNT(*) AS customers,
                   COALESCE(SUM(gmv_total * 0.85),0) AS gmv
            FROM dim_customer
            GROUP BY rfm_segment
            ORDER BY gmv DESC
        """)

        # Top produits & pays (7j)
        data["top_products_7d"] = _rows(conn, """
            SELECT COALESCE(product_id,'(n/a)') AS product_id,
                   COUNT(*) AS orders,
                   COALESCE(SUM(amount_value * 0.85),0) AS gmv
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            GROUP BY COALESCE(product_id,'(n/a)')
            ORDER BY gmv DESC
            LIMIT 5
        """)
        data["countries_7d"] = _rows(conn, """
            SELECT COALESCE(country,'(n/a)') AS country,
                   COUNT(*) AS orders,
                   COALESCE(SUM(amount_value * 0.85),0) AS gmv
            FROM fact_sales
            WHERE status='completed'
              AND COALESCE(completed_at,created_at) >= CAST(strftime('%s','now','-7 day') AS INTEGER)
            GROUP BY COALESCE(country,'(n/a)')
            ORDER BY gmv DESC
            LIMIT 5
        """)

        return jsonify(data)
    finally:
        conn.close()

@dashboard_bp.route("/ts.json")
@login_required
def ts_json():
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
@login_required
def heatmap_json():
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
@admin_required
def export_csv():
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


# ------------------------------
#   AUTH
# ------------------------------

@dashboard_bp.route("/login")
def dashboard_login():
    tok = getattr(Config, "DASHBOARD_TOKEN", None)
    given = request.args.get("token")
    nxt = request.args.get("next") or url_for("dashboard.dashboard_view")
    if not tok:
        return redirect(nxt)
    if given != tok:
        return "forbidden", 403
    resp = make_response(redirect(nxt))
    resp.set_cookie("dash_t", tok, max_age=7*24*3600, httponly=True, samesite="Lax")
    return resp

# ------------------------------
#   VUES HTML -> Templates Jinja
# ------------------------------

@dashboard_bp.route("/", methods=["GET"])
@login_required
def dashboard_view():
    return render_template("dashboard/index.html")


@dashboard_bp.route("/templates", methods=["GET"])
@admin_required
def templates_products():

    conn = _conn(); conn.row_factory = sqlite3.Row
    try:
        # Liste des produits (ID non vide) + compte des templates par canal
        products_rows = conn.execute("""
            SELECT
              TRIM(p.product_id) AS product_id,
              COALESCE(p.product_name,'(sans nom)') AS product_name,
              COUNT(t.id) AS nb_tpl,
              SUM(CASE WHEN t.channel='email' THEN 1 ELSE 0 END)    AS nb_email,
              SUM(CASE WHEN t.channel='whatsapp' THEN 1 ELSE 0 END) AS nb_whatsapp
            FROM dim_product p
            LEFT JOIN message_templates t
              ON TRIM(t.product_id) = TRIM(p.product_id)
            WHERE p.product_id IS NOT NULL
              AND TRIM(p.product_id) <> ''
            GROUP BY TRIM(p.product_id), p.product_name
            ORDER BY p.product_name COLLATE NOCASE ASC
        """).fetchall()
    finally:
        conn.close()

    # Normalise None -> 0 pour éviter les affichages vides
    products = [{
        "product_id":   r["product_id"],
        "product_name": r["product_name"],
        "nb_tpl":       r["nb_tpl"] or 0,
        "nb_email":     r["nb_email"] or 0,
        "nb_whatsapp":  r["nb_whatsapp"] or 0,
    } for r in products_rows]

    return render_template("dashboard/templates_products.html", products=products)


@dashboard_bp.route("/templates/view", methods=["GET"])
@admin_required
def templates_for_product():

    pid = (request.args.get("id") or "").strip()
    if not pid:

        return redirect(url_for("dashboard.templates_products"))

    conn = _conn(); conn.row_factory = sqlite3.Row
    try:
        prod = conn.execute("""
            SELECT COALESCE(product_name,'(sans nom)') AS product_name
            FROM dim_product
            WHERE TRIM(product_id)=?
        """, (pid,)).fetchone()

        rows = conn.execute("""
            SELECT id, TRIM(product_id) AS product_id, template_type, channel,
                   is_full_html, is_active, updated_at
            FROM message_templates
            WHERE TRIM(product_id) = ?
            ORDER BY template_type, channel
        """, (pid,)).fetchall()
    finally:
        conn.close()

    return render_template(
        "dashboard/templates_for_product.html",
        pid=pid,
        product_name=(prod["product_name"] if prod else "(inconnu)"),
        templates=rows
    )

@dashboard_bp.route("/templates/view/edit", methods=["GET","POST"])
@admin_required
def edit_template():

    suggested_types = [
        "relance_t30","relance_t6h","relance_t23h","relance_t47h",
        "confirm_3_1","confirm_3_2","confirm_3_3","confirm_3_4","confirm_3_5"
    ]

    if request.method == "POST":
        product_id    = request.form.get("product_id") or None
        template_type = (request.form.get("template_type") or "").strip()
        channel       = (request.form.get("channel") or "email").strip()
        subject       = request.form.get("subject") or ""
        body          = request.form.get("body") or ""
        is_full_html  = bool(request.form.get("is_full_html"))
        is_active     = bool(request.form.get("is_active"))
        if not template_type:
            return "template_type requis", 400

        upsert_template(product_id, template_type, channel, subject, body, is_full_html, is_active)

        if product_id:
            return redirect(url_for("dashboard.templates_for_product", id=product_id))


    # GET
    rid = request.args.get("id")
    pid_prefill = request.args.get("pid")
    conn = None; row = None
    if rid:
        conn = _conn(); conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM message_templates WHERE id=?", (rid,)).fetchone()
        conn.close()

    def val(field, default=""):
        if row is None:
            if field == "product_id" and pid_prefill:
                return pid_prefill
            return default
        v = row[field] if field in row.keys() else default
        return v if v is not None else default

    ctx = {
        "rid": rid,
        "suggested_types": suggested_types,
        "product_id": val("product_id",""),
        "template_type": val("template_type",""),
        "channel": val("channel","email") or "email",
        "subject": val("subject",""),
        "body": val("body",""),
        "is_full_html": bool(val("is_full_html",0)),
        "is_active": bool(val("is_active",1)),
        "pid_prefill": pid_prefill
    }
    return render_template("dashboard/edit_template.html", **ctx)


@dashboard_bp.route("/templates/delete", methods=["POST"])
@admin_required
def delete_template_post():
    rid = request.form.get("id")
    if not rid:
        return "id requis", 400

    conn = _conn(); conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT product_id FROM message_templates WHERE id=?", (rid,)).fetchone()
        with conn:
            conn.execute("DELETE FROM message_templates WHERE id=?", (rid,))
    finally:
        conn.close()

    if row and row["product_id"]:
        return redirect(url_for("dashboard.templates_for_product", id=row["product_id"]))



@dashboard_bp.route("/templates/delete_product", methods=["POST"])
@admin_required
def delete_product_post():

    pid = request.form.get("product_id")
    if not pid:
        return "product_id requis", 400
    try:
        conn = _conn()
        with conn:
            conn.execute("DELETE FROM message_templates WHERE product_id=?", (pid,))
            conn.execute("DELETE FROM dim_product WHERE product_id=?", (pid,))
    except Exception as e:
        return f"Erreur suppression: {e}", 500
    return redirect(url_for("dashboard.templates_products"))


@dashboard_bp.route("/templates/preview", methods=["GET", "POST"])
@admin_required
def preview_template():

    try:
        from webhook_app.services.mailer import render_email_with_brand as _brand_wrap
    except Exception:
        def _brand_wrap(fragment_html, tvars):
            return f"""<!doctype html><meta charset="utf-8">
            <title>Preview</title>
            <div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:24px auto;padding:16px;border:1px solid #eee;border-radius:8px">
              {fragment_html}
            </div>"""

    tvars = {
        "customer_first_name": "Jean",
        "customer_email": "jean@example.com",
        "product_name": "Microsoft 365 à vie",
        "checkout_url": "https://digitechhub.store/checkout/test",
        "price_current_fmt": "5 000 FCFA",
        "price_after_fmt": "15 000 FCFA",
        "store_name": "Digitech Hub",
        "store_url": "https://digitechhub.store",
        "sale_id": "S123456",
        "current_year": "2025",
    }

    def _render_body(body: str, is_full_html: bool):
        try:
            html = body.format_map({k: v for k, v in tvars.items()})
        except Exception as e:
            html = f"<pre style='color:#c33'>Erreur de formatage: {e}</pre>\n" + body
        if not is_full_html:
            html = _brand_wrap(html, tvars)
        return html

    if request.method == "POST":
        data = request.get_json(force=True)
        body = data.get("body", "")
        is_full_html = bool(data.get("is_full_html"))
        html = _render_body(body, is_full_html)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    rid = request.args.get("id")
    if rid:
        conn = _conn(); conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT body, is_full_html FROM message_templates WHERE id=?", (rid,)).fetchone()
        conn.close()
        if not row:
            return "Template introuvable", 404
        body = row["body"] or ""
        is_full_html = bool(row["is_full_html"])
        html = _render_body(body, is_full_html)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    if "body" in request.args:
        body = request.args.get("body", "")
        is_full_html = request.args.get("is_full_html", "0").lower() in ("1","true","yes")
        html = _render_body(body, is_full_html)
        return html, 200, {"Content-Type": "text/html; charset=utf-8"}

    help_html = """
    <!doctype html><meta charset="utf-8"><title>Preview</title>
    <div style="font-family:Segoe UI,Arial,sans-serif;max-width:720px;margin:24px auto">
      <h2>Prévisualisation — usage</h2>
      <ul>
        <li><code>/dashboard/templates/preview?id=&lt;ID&gt;</code></li>
        <li><code>/dashboard/templates/preview?body=...&amp;is_full_html=0|1</code></li>
      </ul>
    </div>
    """
    return help_html, 200, {"Content-Type": "text/html; charset=utf-8"}
