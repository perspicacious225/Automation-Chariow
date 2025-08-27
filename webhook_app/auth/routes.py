# webhook_app/auth/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from webhook_app.utils.auth import (
    ensure_users_schema, users_count,
    get_user_by_email, create_user, verify_password
)

auth_bp = Blueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    template_folder="templates" 
)

@auth_bp.before_app_first_request
def _ensure_schema_once():
    ensure_users_schema()

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    # Politique: inscription ouverte seulement si aucun utilisateur n'existe,
    # sinon, réservée aux admins déjà connectés.
    if users_count() > 0 and (not current_user.is_authenticated or not current_user.is_admin):
        flash("Inscription fermée (réservée à l’admin).", "warning")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        is_admin = bool(request.form.get("is_admin"))

        if not email or not password:
            flash("Email et mot de passe requis.", "danger")
            return render_template("auth/register.html")

        if get_user_by_email(email):
            flash("Email déjà utilisé.", "danger")
            return render_template("auth/register.html")

        user = create_user(email, password, is_admin=is_admin)
        login_user(user, remember=True)
        return redirect(url_for("dashboard.dashboard_view"))

    return render_template("auth/register.html")

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "")
        password = request.form.get("password", "")
        user = get_user_by_email(email)
        if not user or not verify_password(user, password):
            flash("Identifiants invalides.", "danger")
            return render_template("auth/login.html")
        login_user(user, remember=True)
        nxt = request.args.get("next") or url_for("dashboard.dashboard_view")
        return redirect(nxt)
    return render_template("auth/login.html")

@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
