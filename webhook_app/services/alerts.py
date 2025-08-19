import sqlite3
from webhook_app.config import Config
from webhook_app.services.mailer import EmailService

def evaluate_and_alert():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM scheduled_notifications WHERE error IS NOT NULL AND sent_at >= datetime('now','-1 day')")
        errors_24h = cur.fetchone()[0] or 0

        cur = conn.execute("""
          WITH today AS (
            SELECT COUNT(*) AS ab FROM fact_sales WHERE status='abandoned' AND COALESCE(abandoned_at,created_at) >= date('now')
          ), yesterday AS (
            SELECT COUNT(*) AS ab FROM fact_sales WHERE status='abandoned' AND date(COALESCE(abandoned_at,created_at)) = date('now','-1 day')
          )
          SELECT (SELECT ab FROM today), (SELECT ab FROM yesterday)
        """)
        t_ab, y_ab = cur.fetchone()
        t_ab = t_ab or 0; y_ab = y_ab or 0
        spike = (y_ab > 0 and t_ab >= y_ab * Config.ALERT_ABANDON_SPIKE_FACTOR)

        # conversion rate today vs yesterday
        conv = conn.execute("""
          WITH s AS (
            SELECT date(COALESCE(completed_at,created_at)) AS d,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) c,
                   SUM(CASE WHEN status IN ('abandoned','failed','completed') THEN 1 ELSE 0 END) t
            FROM fact_sales
            WHERE date(COALESCE(created_at,completed_at)) >= date('now','-1 day')
            GROUP BY date(COALESCE(created_at,completed_at))
          )
          SELECT
            COALESCE((SELECT c*1.0/t FROM s WHERE d=date('now')),0.0),
            COALESCE((SELECT c*1.0/t FROM s WHERE d=date('now','-1 day')),0.0)
        """).fetchone()
        today_conv, yday_conv = conv or (0.0,0.0)
        drop = (yday_conv>0 and today_conv <= yday_conv * Config.ALERT_CONV_DROP_FACTOR)

        alerts = []
        if errors_24h >= Config.ALERT_ERROR_THRESHOLD_24H:
            alerts.append(f"ERREURS: {errors_24h} envois en erreur sur 24h (seuil {Config.ALERT_ERROR_THRESHOLD_24H})")
        if spike:
            alerts.append(f"ABANDONS: pic aujourd'hui {t_ab} vs hier {y_ab} (x{Config.ALERT_ABANDON_SPIKE_FACTOR})")
        if drop:
            alerts.append(f"CONVERSION: chute {today_conv:.2%} vs {yday_conv:.2%} (seuil {Config.ALERT_CONV_DROP_FACTOR:.2f}x)")

        if alerts and Config.ALERT_EMAILS:
            body = "<br>".join(alerts)
            EmailService().send_email(
                recipient=Config.ALERT_EMAILS[0],
                subject="⚠️ Alertes KPI relances/ventes",
                html_body=f"<h3>Alertes</h3><p>{body}</p>",
                plain_fallback="\n".join(alerts)
            )
        return alerts
    finally:
        conn.close()
