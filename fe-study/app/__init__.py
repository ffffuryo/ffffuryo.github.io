import random
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from . import db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(db_path=None):
    app = Flask(__name__, static_folder=None)
    path = db.resolve_db_path(db_path)
    app.config["DB_PATH"] = path
    app.config["IMAGES_DIR"] = str(db.IMAGES_DIR)
    app.config["RNG"] = random.Random()
    db.init_db(path)

    def get_db():
        if "db" not in g:
            g.db = db.connect(app.config["DB_PATH"])
        return g.db

    app.get_db = get_db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    from .api import bp
    app.register_blueprint(bp)

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html", max_age=0)

    @app.route("/static/<path:filename>")
    def static_files(filename):
        return send_from_directory(STATIC_DIR, filename, max_age=300)

    @app.route("/media/<path:filename>")
    def media(filename):
        return send_from_directory(app.config["IMAGES_DIR"], filename, max_age=86400)

    @app.after_request
    def no_store(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(HTTPException)
    def http_err(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description or e.name}), e.code
        return e

    return app
