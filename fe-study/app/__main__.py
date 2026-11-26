import argparse
import os

from . import create_app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true", help="Flask 開発サーバで起動")
    args = ap.parse_args()
    host = os.environ.get("FE_HOST", "127.0.0.1")
    port = int(os.environ.get("FE_PORT", "8000"))
    app = create_app()
    if args.dev:
        app.run(host=host, port=port, debug=True)
        return
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port)
        return
    serve(app, host=host, port=port, threads=4)


if __name__ == "__main__":
    main()
