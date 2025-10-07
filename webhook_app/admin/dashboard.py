# webhook_app/admin/dashboard.py
from flask import Blueprint, jsonify, render_template, request, Response, redirect, make_response, url_for, abort, flash
from flask_login import login_required, current_user
from functools import wraps
import os, csv, io, html as _html
from webhook_app.config import Config
from webhook_app.database_pg import upsert_template

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

from webhook_app.database_pg import get_connection, execute_with_retry

def _scalar(sql, params=()):
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn, sql, params, fetch="one")
        if not row:
            return 0
        return list(row.values())[0] if isinstance(row, dict) else (row[0] if row else 0)

def _rows(sql, params=()):
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, params, fetch="all") or []
        return [dict(r) for r in rows]



# ------------------------------
#   ROUTES API / JSON 
# ------------------------------ 
@dashboard_bp.route("/metrics.json")
@login_required
def metrics_json():
    data = {}

    # pending dues / futures / customers pending
    data["pending_due"] = _scalar("""
        SELECT COUNT(*) FROM scheduled_notifications
        WHERE sent_at IS NULL AND due_at <= NOW()
    """)

    data["pending_future"] = _scalar("""
        SELECT COUNT(*) FROM scheduled_notifications
        WHERE sent_at IS NULL AND due_at > NOW()
    """)

    data["relance_customer_pending_count"] = _scalar("""
        SELECT COUNT(DISTINCT sale_id) FROM scheduled_notifications
        WHERE sent_at IS NULL AND due_at > NOW()
    """)

    # Envois 24h
    sent24 = _rows("""
        SELECT channel, COUNT(*) AS cnt
        FROM notification_log
        WHERE sent_at >= NOW() - INTERVAL '24 hours'
        GROUP BY channel
    """)
    sent24_map = {r["channel"]: r["cnt"] for r in sent24}
    data["sent_24h_email"]    = int(sent24_map.get("email", 0))
    data["sent_24h_whatsapp"] = int(sent24_map.get("whatsapp", 0))

    data["errors_24h"] = _scalar("""
        SELECT COUNT(*)
        FROM scheduled_notifications
        WHERE error IS NOT NULL AND sent_at >= NOW() - INTERVAL '24 hours'
    """)

    data["cadences_active"] = _scalar("""
        SELECT COUNT(DISTINCT COALESCE(contact_key,'')||'|'||COALESCE(product_id,''))
        FROM scheduled_notifications
        WHERE sent_at IS NULL
    """)

    # Sales KPIs
    data["gmv_1d"] = _scalar("""
        SELECT COALESCE(SUM(amount_value*0.90),0)
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= date_trunc('day', NOW())
    """)

    data["gmv_yday"] = _scalar("""
        SELECT COALESCE(SUM(amount_value*0.90),0)
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= (date_trunc('day', NOW()) - INTERVAL '1 day')
          AND COALESCE(completed_at,created_at) <  date_trunc('day', NOW())
    """)

    data["gmv_7d"] = _scalar("""
        SELECT COALESCE(SUM(amount_value*0.90),0)
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '7 days'
    """)

    data["orders_7d"] = _scalar("""
        SELECT COUNT(*) FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '7 days'
    """)

    data["orders_1d"] = _scalar("""
        SELECT COUNT(*) FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= date_trunc('day', NOW())
    """)

    data["aov_7d"] = (data["gmv_7d"] / data["orders_7d"]) if data["orders_7d"] else 0.0

    # KPI 24h
    data["abandoned_24h"] = _scalar("""
        SELECT COUNT(*) FROM fact_sales
        WHERE status='abandoned'
          AND abandoned_at >= date_trunc('day', NOW())
    """)

    data["failed_24h"] = _scalar("""
        SELECT COUNT(*) FROM fact_sales
        WHERE status='failed'
          AND failed_at >= date_trunc('day', NOW())
    """)

    # Recovered 7d (72h window + proof of relance before completion)
    data["recovered_orders_7d"] = _scalar("""
        WITH ab AS (
          SELECT product_id, contact_key,
                 MAX(COALESCE(abandoned_at, failed_at)) AS last_ab
          FROM fact_sales
          WHERE status IN ('abandoned','failed')
            AND COALESCE(abandoned_at, failed_at) >= NOW() - INTERVAL '7 days'
            AND product_id IS NOT NULL AND contact_key IS NOT NULL
          GROUP BY product_id, contact_key
        ),
        co AS (
          SELECT sale_id, amount_value,
                 completed_at AS t,
                 product_id, contact_key
          FROM fact_sales
          WHERE status='completed'
            AND completed_at >= NOW() - INTERVAL '7 days'
            AND completed_at IS NOT NULL
            AND product_id IS NOT NULL AND contact_key IS NOT NULL
        )
        SELECT COUNT(*)
        FROM co
        JOIN ab USING(product_id, contact_key)
        WHERE co.t BETWEEN ab.last_ab AND (ab.last_ab + INTERVAL '72 hours')
          AND EXISTS (
            SELECT 1
            FROM notification_log nl
            WHERE nl.product_id  = co.product_id
              AND nl.contact_key = co.contact_key
              AND nl.template_type LIKE 'relance_%%'
              AND nl.sent_at BETWEEN ab.last_ab AND co.t
          )
    """)

    data["recovered_gmv_7d"] = _scalar("""
        WITH ab AS (
          SELECT product_id, contact_key,
                 MAX(COALESCE(abandoned_at, failed_at)) AS last_ab
          FROM fact_sales
          WHERE status IN ('abandoned','failed')
            AND COALESCE(abandoned_at, failed_at) >= NOW() - INTERVAL '7 days'
            AND product_id IS NOT NULL AND contact_key IS NOT NULL
          GROUP BY product_id, contact_key
        ),
        co AS (
          SELECT sale_id, amount_value,
                 completed_at AS t,
                 product_id, contact_key
          FROM fact_sales
          WHERE status='completed'
            AND completed_at >= NOW() - INTERVAL '7 days'
            AND completed_at IS NOT NULL
            AND product_id IS NOT NULL AND contact_key IS NOT NULL
        )
        SELECT COALESCE(SUM(co.amount_value*0.90),0)
        FROM co
        JOIN ab USING(product_id, contact_key)
        WHERE co.t BETWEEN ab.last_ab AND (ab.last_ab + INTERVAL '72 hours')
          AND EXISTS (
            SELECT 1
            FROM notification_log nl
            WHERE nl.product_id  = co.product_id
              AND nl.contact_key = co.contact_key
              AND nl.template_type LIKE 'relance_%%'
              AND nl.sent_at BETWEEN ab.last_ab AND co.t
          )
    """)

    data["ab_failed_7d"] = _scalar("""
        SELECT COUNT(*) FROM fact_sales
        WHERE status IN ('abandoned','failed')
          AND COALESCE(abandoned_at, failed_at) >= NOW() - INTERVAL '7 days'
    """)
    data["recovered_rate_7d"] = (data["recovered_orders_7d"] / data["ab_failed_7d"]) if data["ab_failed_7d"] else 0.0

    # Conversions par step
    data["conversions_by_step_7d"] = _rows("""
        WITH co AS (
          SELECT sale_id, product_id, contact_key,
                 COALESCE(completed_at,created_at) AS completed_at,
                 amount_value 
          FROM fact_sales
          WHERE status='completed'
            AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '7 days'
        ),
        lastrel AS (
          SELECT contact_key, product_id, template_type,
                 sent_at AS sent_epoch
          FROM notification_log
          WHERE template_type LIKE 'relance_%%'
        ),
        x AS (
          SELECT c.sale_id,
                 (
                   SELECT lr.template_type
                   FROM lastrel lr
                   WHERE lr.contact_key = c.contact_key
                     AND lr.product_id  = c.product_id
                     AND lr.sent_epoch <= c.completed_at
                   ORDER BY lr.sent_epoch DESC
                   LIMIT 1
                 ) AS template_type
          FROM co c
        )
        SELECT x.template_type AS step,
               COUNT(*) AS conv,
               COALESCE(SUM(c.amount_value *0.90 ),0) AS gmv
        FROM x
        JOIN co c ON c.sale_id = x.sale_id
        WHERE x.template_type IS NOT NULL
        GROUP BY x.template_type
        ORDER BY x.template_type
    """)

    # Tops
    data["top_products_7d"] = _rows("""
        SELECT COALESCE(product_id,'(n/a)') AS product_id,
               COUNT(*) AS orders,
               COALESCE(SUM(amount_value*0.90),0) AS gmv
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '7 days'
        GROUP BY COALESCE(product_id,'(n/a)')
        ORDER BY gmv DESC
        LIMIT 5
    """)

    data["countries_7d"] = _rows("""
        SELECT COALESCE(country,'(n/a)') AS country,
               COUNT(*) AS orders,
               COALESCE(SUM(amount_value*0.90),0) AS gmv
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '7 days'
        GROUP BY COALESCE(country,'(n/a)')
        ORDER BY gmv DESC
        LIMIT 5
    """)

    return jsonify(data)

@dashboard_bp.route("/ts.json")
@login_required
def ts_json():

    rows = _rows("""
      SELECT
        TO_CHAR(COALESCE(completed_at,created_at), 'YYYY-MM-DD') AS d,
        SUM(CASE WHEN status='completed' THEN amount_value*0.90 ELSE 0 END) AS gmv,
        SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)               AS orders,
        SUM(CASE WHEN status IN ('abandoned','failed') THEN 1 ELSE 0 END)  AS abandoned
      FROM fact_sales
      WHERE COALESCE(created_at, completed_at) >= NOW() - INTERVAL '30 days'
      GROUP BY 1
      ORDER BY 1
    """)
    return jsonify(rows)

@dashboard_bp.route("/heatmap.json")
@login_required
def heatmap_json():
  
    rows = _rows("""
      SELECT dow, hour_of_day, COUNT(*) AS cnt
      FROM fact_sales
      WHERE status='completed'
        AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '30 days'
      GROUP BY dow, hour_of_day
    """)
    return jsonify(rows)

@dashboard_bp.route("/export.csv")
@admin_required
def export_csv():
    q = """
    SELECT sale_id,status,amount_value,currency,product_id,product_name,store_name,
           contact_key,email,phone,country,created_at,completed_at,abandoned_at,
           time_to_complete_min,hour_of_day,dow,month,utm_source,utm_medium,utm_campaign,price_tier
    FROM fact_sales
    WHERE COALESCE(created_at,completed_at) >= NOW() - INTERVAL '90 days'
    ORDER BY COALESCE(completed_at,created_at) DESC
    """
    rows = _rows(q)
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

    from webhook_app.database_pg import get_connection, execute_with_retry
    with get_connection(readonly=True) as conn:
        products_rows = execute_with_retry(conn, """
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
                ORDER BY p.product_name ASC
            """, fetch="all") or []
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

    from webhook_app.database_pg import get_connection, execute_with_retry
    with get_connection(readonly=True) as conn:
        prod = execute_with_retry(conn, """
            SELECT COALESCE(product_name,'(sans nom)') AS product_name
            FROM dim_product
            WHERE TRIM(product_id)=%s
        """, (pid,), fetch="one")

        rows = execute_with_retry(conn, """
            SELECT id, TRIM(product_id) AS product_id, template_type, channel,
                   is_full_html, is_active, updated_at
            FROM message_templates
            WHERE TRIM(product_id) = %s
            ORDER BY template_type, channel
        """, (pid,), fetch="all") or []

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
        from webhook_app.database_pg import get_connection, execute_with_retry
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(conn, "SELECT * FROM message_templates WHERE id=%s", (rid,), fetch="one")

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



from webhook_app.database_pg import (
    get_pending_relances_by_contact, 
    get_pending_relances,
    cancel_relance_by_id,
    update_relance_due_at
)

@dashboard_bp.route("/relances")
@admin_required
def manage_relances_summary():
    contacts_with_relances = get_pending_relances_by_contact()
    return render_template("dashboard/relances_summary.html", contacts=contacts_with_relances)


@dashboard_bp.route("/relances/<contact_key>", methods=["GET", "POST"])
@admin_required
def manage_relances_for_contact(contact_key):
    if request.method == "POST":
        action = request.form.get("action")
        job_id = request.form.get("job_id")

        if action == "delete" and job_id:
            cancel_relance_by_id(int(job_id))
        
        elif action == "update" and job_id:
            new_due_at_str = request.form.get(f"due_at_{job_id}")
            if new_due_at_str:
                try:
                    update_relance_due_at(int(job_id), new_due_at_str)
                except ValueError:
                
                    flash("Format de date invalide.", "error")

        return redirect(url_for("dashboard.manage_relances_for_contact", contact_key=contact_key))

    pending_relances = get_pending_relances(contact_key=contact_key)
    return render_template("dashboard/relances_detail.html", 
                           relances=pending_relances, 
                           contact_key=contact_key)



@dashboard_bp.route("/templates/delete", methods=["POST"])
@admin_required
def delete_template_post():
    rid = request.form.get("id")
    if not rid:
        return "id requis", 400

    from webhook_app.database_pg import get_connection, execute_with_retry
    with get_connection() as conn:
        row = execute_with_retry(conn, "SELECT product_id FROM message_templates WHERE id=%s", (rid,), fetch="one")
        execute_with_retry(conn, "DELETE FROM message_templates WHERE id=%s", (rid,))

    if row and row["product_id"]:
        return redirect(url_for("dashboard.templates_for_product", id=row["product_id"]))



@dashboard_bp.route("/templates/delete_product", methods=["POST"])
@admin_required
def delete_product_post():

    pid = request.form.get("product_id")
    if not pid:
        return "product_id requis", 400
    try:
        from webhook_app.database_pg import get_connection, execute_with_retry
        with get_connection() as conn:
            execute_with_retry(conn, "DELETE FROM message_templates WHERE product_id=%s", (pid,))
            execute_with_retry(conn, "DELETE FROM dim_product WHERE product_id=%s", (pid,))
    except Exception as e:
        return f"Erreur suppression: {e}", 500
    return redirect(url_for("dashboard.templates_products"))



from webhook_app.database_pg import read_mappings_from_db, add_mapping_to_db, delete_mapping_from_db
@dashboard_bp.route("/mappings", methods=["GET", "POST"])
@admin_required 
def manage_mappings():
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "add":
            product_id = request.form.get("product_id")
            folder_id = request.form.get("folder_id")
            if product_id and folder_id:
                add_mapping_to_db(product_id, folder_id)
        
        elif action == "delete":
            product_id = request.form.get("product_id")
            folder_id = request.form.get("folder_id")
            if product_id and folder_id:
                delete_mapping_from_db(product_id, folder_id)
        
        return redirect(url_for("dashboard.manage_mappings"))

    # Pour une requête GET, on affiche simplement la page
    current_mappings = read_mappings_from_db()
    return render_template("dashboard/mappings.html", mappings=current_mappings)


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
        "product_name": "Microsoft 365 à vie 2025  ",
        "checkout_url": "https://digitechhub.store/checkout/test",
        "price_current_fmt": "3 900 FCFA",
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
            # VERSION CORRIGÉE : Utilise le gestionnaire de connexion et la bonne syntaxe
            with get_connection(readonly=True) as conn:
                row = execute_with_retry(conn, 
                                        "SELECT body, is_full_html FROM message_templates WHERE id=%s", 
                                        (rid,), 
                                        fetch="one")
            
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
