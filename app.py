import logging

from app import create_app

app = create_app()

if app.config.get("QUIET_HTTP_LOGS", True):
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
