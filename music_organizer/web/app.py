"""Flask 应用工厂：页面、登录鉴权、静态资源与错误页。"""

from __future__ import annotations

import json
import secrets
from typing import Optional

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from .. import __version__, db, jobs
from ..automation import Automation, start_automation as start_automation_service
from ..config import Config, verify_password
from ..logging_setup import get_logger, setup_logging
from ..paths import AppPaths, get_paths
from .api import bp as api_bp
from .api import media as media_bp

log = get_logger("app")

PUBLIC_ENDPOINTS = {"login", "static", "health", "manifest", "favicon"}


def create_app(
    config: Optional[Config] = None,
    paths: Optional[AppPaths] = None,
    enable_automation: bool = True,
    debug: bool = False,
) -> Flask:
    paths = paths or get_paths()
    config = config or Config.load(paths)
    setup_logging(paths, debug=debug)
    paths.ensure()
    db.init_db(paths)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    app.config.update(
        SECRET_KEY=config.get("web.secret") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="morg_session",
        PERMANENT_SESSION_LIFETIME=7 * 24 * 3600,
        TEMPLATES_AUTO_RELOAD=debug,
        JSON_AS_ASCII=False,
    )
    app.json.ensure_ascii = False

    manager = jobs.get_manager(config, paths)
    if enable_automation:
        automation = start_automation_service(config, paths)
    else:
        automation = Automation(config, paths, manager)
    app.config["MORG_CONFIG"] = config
    app.config["MORG_PATHS"] = paths
    app.config["MORG_MANAGER"] = manager
    app.config["MORG_AUTOMATION"] = automation

    app.register_blueprint(api_bp)
    app.register_blueprint(media_bp)

    # ------------------------------------------------------------------
    @app.before_request
    def require_auth():
        cfg: Config = app.config["MORG_CONFIG"]
        if not cfg.has_web_password:
            return None
        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if session.get("auth") is True:
            return None
        if request.path.startswith(("/api/", "/media/")):
            return jsonify({"ok": False, "error": "未登录", "login_required": True}), 401
        return redirect(url_for("login", next=request.full_path if request.path != "/" else ""))

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith(("/api/", "/")) and not request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/")
    def index():
        cfg: Config = app.config["MORG_CONFIG"]
        return render_template(
            "index.html",
            version=__version__,
            app_title="本地音乐整理小插件",
            has_password=cfg.has_web_password,
            initialized=cfg.is_initialized(),
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        cfg: Config = app.config["MORG_CONFIG"]
        if not cfg.has_web_password:
            return redirect(url_for("index"))
        error = ""
        if request.method == "POST":
            password = request.form.get("password", "")
            if verify_password(password, cfg.get("web.password_hash")):
                session.permanent = True
                session["auth"] = True
                target = request.args.get("next") or url_for("index")
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("index")
                return redirect(target)
            error = "密码不正确"
            log.warning("登录失败：remote=%s", request.remote_addr)
        return render_template("login.html", version=__version__, error=error, next=request.args.get("next", ""))

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/health")
    def health():
        cfg: Config = app.config["MORG_CONFIG"]
        try:
            total = db.stats(app.config["MORG_PATHS"])["total"]
        except Exception:
            total = 0
        return jsonify({"ok": True, "version": __version__, "initialized": cfg.is_initialized(), "tracks": total})

    @app.get("/manifest.json")
    def manifest():
        return jsonify(
            {
                "name": "本地音乐整理小插件",
                "short_name": "音乐整理",
                "description": "为本地音乐批量嵌入歌词与封面",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#0a0d18",
                "theme_color": "#0a0d18",
                "icons": [
                    {"src": "/static/img/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}
                ],
            }
        )

    @app.get("/favicon.ico")
    def favicon():
        return redirect("/static/img/icon.svg", code=302)

    # ------------------------------------------------------------------
    @app.errorhandler(HTTPException)
    def handle_http_error(exc: HTTPException):
        if request.path.startswith(("/api/", "/media/")):
            return jsonify({"ok": False, "error": exc.description or exc.name}), exc.code or 500
        if exc.code == 404:
            return render_template("404.html", version=__version__), 404
        return exc

    @app.errorhandler(413)
    def too_large(exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "上传内容过大（上限 20MB）"}), 413
        return exc

    @app.errorhandler(Exception)
    def handle_error(exc: Exception):
        log.exception("未处理异常：%s", exc)
        if request.path.startswith(("/api/", "/media/")):
            return jsonify({"ok": False, "error": f"服务器错误：{exc}"}), 500
        return render_template("500.html", version=__version__, error=str(exc)), 500

    log.info("Web 应用已创建：music_dir=%s", config.music_dir_str or "(未设置)")
    return app
