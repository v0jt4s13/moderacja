import socket
import os
import sys
from datetime import datetime
from markupsafe import Markup
from flask import Flask, render_template, request, session, redirect, url_for, abort, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from config import LOGS_DIR_PATH, get_config
get_config()

from auth import login_required, log_entry_access 
os.makedirs(LOGS_DIR_PATH, exist_ok=True)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
# max content size to upload
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024

from news_to_video.routes import news_to_video_bp
from materialy_reklamowe import materialy_reklamowe_bp
from webutils.routes import webutils_bp
app.register_blueprint(news_to_video_bp)
app.register_blueprint(materialy_reklamowe_bp)
app.register_blueprint(webutils_bp)

try:
    from price_compare.routes import price_compare_bp
except:
    pass
try:
    app.register_blueprint(price_compare_bp)
except:
    pass

@app.template_filter('datetimeformat')
def datetimeformat(value, format='%Y-%m-%d %H:%M:%S'):
    return datetime.fromtimestamp(value).strftime(format)

app.secret_key = os.getenv("FLASK_SECRET_KEY")
# Lista użytkowników: login → hasło
USERS = {
    "admin": {"password": os.getenv("ADMIN_PASSWORD"), "role": "admin"}
}

@app.route('/')
@login_required()
def index():
    log_entry_access('/index')
    now = datetime.utcnow()
    current_year = now.strftime("%Y")
    return render_template(
        'index.html'
    )

@app.route("/logout")
@login_required()
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    log_entry_access('/login')
    if session.get("user"):
        return redirect(url_for("index"))
    
    if request.method == "POST":
        user = request.form.get("username")
        pwd = request.form.get("password")
        user_data = USERS.get(user)
        # print(f'user={user}; pwd={pwd}; USERS={USERS}')
        if user_data and user_data["password"] == pwd:
            session["user"] = user
            session["role"] = user_data["role"]
            
            return redirect(url_for("index"))

        # return "❌ Nieprawidłowe dane logowania", 403

        return render_template("login.html", error="Nieprawidłowe dane logowania")

    return render_template("login.html")

@app.route("/help")
def help():
    # Publiczna pomoc: przekieruj do /webutils/man
    return redirect(url_for("webutils.show_manuals"))

@app.errorhandler(500)
def forbidden(e):
    return render_template("500.html"), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

def find_free_port(start_port=5000, max_tries=20):
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(('127.0.0.1', port)) != 0:
                return port
    raise RuntimeError("Nie znaleziono wolnego portu.")

if __name__ == "__main__":
    port = find_free_port()
    print(f"Uruchamianie na porcie {port-1}...")
    app.run(host='0.0.0.0', debug=True, port=port)
