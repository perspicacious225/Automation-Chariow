from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from webhook_app.utils.auth_pg import (
    ensure_users_schema, users_count,
    get_user_by_email, create_user, verify_password
)

auth_bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    template_folder="templates"
)

@auth_bp.record_once
def _init_users_schema(state):
    app = state.app
    with app.app_context():
        try:
            ensure_users_schema()
            app.logger.info("users schema ensured (auth)")
        except Exception:
            app.logger.exception("ensure_users_schema failed")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if users_count() > 0 and (not current_user.is_authenticated or not current_user.is_admin):
        return redirect(url_for("auth.login"))

    error = None
    prefill_email = ""

    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))
        prefill_email = email

        if not email or not password:
            error = "Email et mot de passe requis."
        elif get_user_by_email(email):
            error = "Cet email est déjà utilisé."
        else:
            user = create_user(email, password, is_admin=is_admin)
            login_user(user, remember=True)
            return redirect(url_for("dashboard.dashboard_view"))

    return render_template("auth/register.html", error=error, prefill_email=prefill_email)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    prefill_email = ""

    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        prefill_email = email
        user = get_user_by_email(email)

        if not email or not password:
            error = "Email et mot de passe requis."
        elif not user or not verify_password(user, password):
            error = "Identifiants invalides. Vérifiez votre email et mot de passe."
        else:
            login_user(user, remember=True)
            nxt = request.args.get("next") or url_for("dashboard.dashboard_view")
            return redirect(nxt)

    return render_template("auth/login.html", error=error, prefill_email=prefill_email)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))