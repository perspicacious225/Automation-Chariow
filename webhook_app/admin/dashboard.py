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
    data["gmv_30d"] = _scalar("""
        SELECT COALESCE(SUM(amount_value*0.90),0)
        FROM fact_sales
        WHERE status='completed'
          AND COALESCE(completed_at,created_at) >= NOW() - INTERVAL '30 days'
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
    data["recovered_orders_30d"] = _scalar("""
        WITH ab AS (
          SELECT product_id, contact_key,
                 MAX(COALESCE(abandoned_at, failed_at)) AS last_ab
          FROM fact_sales
          WHERE status IN ('abandoned','failed')
            AND COALESCE(abandoned_at, failed_at) >= NOW() - INTERVAL '30 days'
            AND product_id IS NOT NULL AND contact_key IS NOT NULL
          GROUP BY product_id, contact_key
        ),
        co AS (
          SELECT sale_id, amount_value,
                 completed_at AS t,
                 product_id, contact_key
          FROM fact_sales
          WHERE status='completed'
            AND completed_at >= NOW() - INTERVAL '30 days'
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
        "is_full_html": bool(val("is_full_html","0")),
        "is_active": bool(val("is_active","1")),
        "pid_prefill": pid_prefill
    }
    return render_template("dashboard/edit_template.html", **ctx)


from webhook_app.database_pg import (
    get_pending_relances_by_contact, 
    cancel_relances_for_contacts,
    get_pending_relances, 
    cancel_relance_by_id, 
    update_relance_due_at
)

@dashboard_bp.route("/relances", methods=["GET", "POST"])
@admin_required
def manage_relances_summary():
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "bulk_delete":
            contacts_to_cancel = request.form.getlist("selected_contacts")
            if contacts_to_cancel:
                cancelled_count = cancel_relances_for_contacts(contacts_to_cancel)
                flash(f"{cancelled_count or 0} relance(s) annulée(s) pour {len(contacts_to_cancel)} client(s).", "success")
        
        elif action == "delete":
            job_id = request.form.get("job_id")
            if job_id:
                cancel_relance_by_id(int(job_id))
                flash(f"La relance #{job_id} a été annulée.", "success")
        
        elif action == "update":
            job_id = request.form.get("job_id")
            new_due_at_str = request.form.get(f"due_at_{job_id}")
            if job_id and new_due_at_str:
                try:
                    update_relance_due_at(int(job_id), new_due_at_str)
                    flash(f"La date de la relance #{job_id} a été mise à jour.", "success")
                except ValueError:
                    flash("Format de date invalide.", "error")
        
        return redirect(url_for("dashboard.manage_relances_summary", 
                                search=request.args.get('search', ''), 
                                page=request.args.get('page', 1)))

    # Logique GET pour afficher la liste
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '', type=str)
    per_page = 20

    contacts, total_count = get_pending_relances_by_contact(
        search_term=search_term,
        page=page,
        per_page=per_page
    )

    total_pages = math.ceil(total_count / per_page)
    
    return render_template(
        "dashboard/relances_summary.html", 
        contacts=contacts,
        page=page,
        total_pages=total_pages,
        search_term=search_term
    )

# ROUTE API POUR LE MODAL (celle qui cause l'erreur)
@dashboard_bp.route("/api/relances/<contact_key>")
@admin_required
def api_relances_for_contact(contact_key):
    relances = get_pending_relances(contact_key=contact_key)
    relances_list = []
    for relance in relances:
        relance_dict = dict(relance)
        if relance_dict.get('due_at'):
            relance_dict['due_at_iso'] = relance_dict['due_at'].strftime('%Y-%m-%dT%H:%M')
        relances_list.append(relance_dict)
    return jsonify(relances_list)


@dashboard_bp.route("/templates/delete", methods=["POST"])
@admin_required
def delete_template_post():
    rid = request.form.get("id")
    if not rid:
        response = jsonify({"error": "id requis"})
        response.status_code = 400
        return response

    product_id_to_redirect = None 

    with get_connection() as conn:
        row = execute_with_retry(conn, "SELECT product_id FROM message_templates WHERE id=%s", (rid,), fetch="one")
        if row:
            product_id_to_redirect = row.get("product_id")
        # Now perform the deletion
        execute_with_retry(conn, "DELETE FROM message_templates WHERE id=%s", (rid,))

    # Decide where to redirect
    if product_id_to_redirect:
        # If there was a product ID, redirect to that product's template list
        return redirect(url_for("dashboard.templates_for_product", id=product_id_to_redirect))
    else:
        # If no product ID (or template didn't exist), redirect to the main list
        # Assuming 'templates_products' is your main template listing route
        return redirect(url_for("dashboard.templates_products"))

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

from datetime import datetime, timezone

from webhook_app.database_pg import (
    create_direct_campaign, 
    get_all_template_types,
    get_confirmed_customer_emails, 
    get_unconverted_customer_emails,
    get_campaign_by_id
)

# Campaign manager routes

# @dashboard_bp.route("/direct-send", methods=["GET", "POST"])
# @admin_required
# def direct_send():
#     if request.method == "POST":
#         subject = request.form.get("subject")
#         html_body = request.form.get("html_body")
#         recipients_str = request.form.get("recipients", "")
#         segment = request.form.get("segment")
#         scheduled_at_str = request.form.get("scheduled_at")

#         # NOUVEAU : Récupération des filtres
#         product_id_filter = request.form.get("product_id_filter") or None
#         start_date = request.form.get("start_date") or None
#         end_date = request.form.get("end_date") or None

#         recipients = []
#         if segment:
#             # On passe les filtres aux fonctions de la base de données
#             if segment == "confirmed":
#                 recipients = get_confirmed_customer_emails(
#                     product_id=product_id_filter, 
#                     start_date=start_date, 
#                     end_date=end_date
#                 )
#             elif segment == "unconverted":
#                 recipients = get_unconverted_customer_emails(
#                     product_id=product_id_filter, 
#                     start_date=start_date, 
#                     end_date=end_date
#                 )
#         elif recipients_str:
#             recipients = [email.strip() for email in recipients_str.replace(',', '\n').split('\n') if email.strip()]

#         if not all([subject, html_body, recipients]):
#             flash("Sujet, Message et au moins un Destinataire (manuel ou via segment) sont requis.", "error")
#         else:
#             scheduled_at = scheduled_at_str or datetime.now(timezone.utc).isoformat()
#             campaign_id = create_direct_campaign(subject, html_body, recipients, scheduled_at)
#             flash(f"Campagne #{campaign_id} programmée avec succès pour {len(recipients)} destinataire(s).", "success")
#             return redirect(url_for("dashboard.direct_send"))

#     template_types = get_all_template_types()
#     return render_template("dashboard/direct_send.html", template_types=template_types)


#--------------------------------------

@dashboard_bp.route("/direct-send", methods=["GET", "POST"])    
@admin_required
def direct_send():
    prefill_data = {} # Dictionary to hold data for pre-filling the form

    # --- GET Request Logic ---
    if request.method == "GET":
        clone_id = request.args.get('clone_id', type=int)
        if clone_id:
            
            original_campaign = get_campaign_by_id(clone_id)
            if original_campaign:
                prefill_data['subject'] = f"{original_campaign['subject']}"
                prefill_data['html_body'] = original_campaign['html_body']
                flash(f"Clonage de la campagne #{clone_id}. Vérifiez et modifiez avant de programmer.", "info")

    # --- POST Request Logic ---
    elif request.method == "POST":
        subject = request.form.get("subject")
        html_body = request.form.get("html_body")
        recipients_str = request.form.get("recipients", "")
        segment = request.form.get("segment") or None
        scheduled_at_str = request.form.get("scheduled_at")
        product_id_filter = request.form.get("product_id_filter") or None
        start_date = request.form.get("start_date") or None
        end_date = request.form.get("end_date") or None

        recipients = []
        # ... (logic for getting recipients based on segment or manual input - unchanged) ...   
        if segment:
            if segment == "confirmed": recipients = get_confirmed_customer_emails(
                product_id=product_id_filter,
                    start_date=start_date,
                    end_date=end_date
            )
            elif segment == "unconverted": recipients = get_unconverted_customer_emails(
                product_id=product_id_filter,
                    start_date=start_date,
                    end_date=end_date
            )
        elif recipients_str: 
            recipients = [email.strip() for email in recipients_str.replace(',', '\n').split('\n') if email.strip()]


        if not all([subject, html_body, recipients]):
            flash("Sujet, Message et au moins un Destinataire (manuel ou via segment) sont requis.", "error")
        else:
            scheduled_at = scheduled_at_str or datetime.now(timezone.utc).isoformat()
            campaign_id = create_direct_campaign(subject, html_body, recipients, scheduled_at, 
                segment_name=segment,
                filter_product_id=product_id_filter,
                filter_start_date=start_date,
                filter_end_date=end_date)
            
            flash(f"Campagne #{campaign_id} programmée avec succès pour {len(recipients)} destinataire(s).", "success")
            # Redirect to the history page after successful creation might be better UX
            return redirect(url_for("dashboard.campaign_history"))

    # --- Render Template (for both GET and POST if POST fails) ---
    template_types = get_all_template_types()
    # Pass the prefill_data to the template
    return render_template("dashboard/direct_send.html",
                           template_types=template_types,
                           prefill=prefill_data)







from webhook_app.database_pg import get_all_campaigns

import math


@dashboard_bp.route("/campaigns")
@admin_required
def campaign_history():
    # Récupérer les paramètres depuis l'URL (ex: /campaigns?page=2&search=promo)
    page = request.args.get('page', 1, type=int)
    search_term = request.args.get('search', '', type=str)
    per_page = 5
    # Appeler la fonction de base de données mise à jour
    campaigns, total_count = get_all_campaigns(
        search_term=search_term, 
        page=page, 
        per_page=per_page
    )

    # Calculer le nombre total de pages
    total_pages = math.ceil(total_count / per_page)

    return render_template(
        "dashboard/campaign_history.html", 
        campaigns=campaigns,
        page=page,
        total_pages=total_pages,
        search_term=search_term
    )

from webhook_app.database_pg import get_all_campaigns, get_campaign_by_id


@dashboard_bp.route("/api/campaigns/<int:campaign_id>")
@admin_required
def api_campaign_detail(campaign_id):
    campaign = get_campaign_by_id(campaign_id)
    if not campaign:
        return jsonify({"error": "Not Found"}), 404
    
    # Convertir l'objet de la base de données en un dictionnaire JSON compatible
    campaign_dict = dict(campaign)
    for key, value in campaign_dict.items():
        
        # Convertit les dates en chaînes de texte
        if hasattr(value, 'isoformat'): 
            campaign_dict[key] = value.isoformat()

    return jsonify(campaign_dict)

from webhook_app.database_pg import delete_campaign_by_id
@dashboard_bp.route("/campaigns/<int:campaign_id>/delete", methods=["POST"])
@admin_required
def delete_campaign(campaign_id):

    deleted_count = delete_campaign_by_id(campaign_id)
    if deleted_count > 0: # type: ignore
        flash(f"Campagne #{campaign_id} supprimée avec succès.", "success")
    else:
        flash(f"Impossible de supprimer la campagne #{campaign_id} (peut-être déjà supprimée).", "warning")
    # Rediriger vers la page d'historique après la suppression
    return redirect(url_for("dashboard.campaign_history"))



@dashboard_bp.route("/templates/preview", methods=["GET", "POST"])
@admin_required
def preview_template(): 
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
