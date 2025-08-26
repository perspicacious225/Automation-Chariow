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
