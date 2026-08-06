"""Production entrypoint — serves the panel with waitress (a real WSGI server).

Reads HOST/PORT from the environment so it drops straight into Render, Railway,
Fly, Docker, or a local network. Run: `py -3.12 serve.py`.
"""
import os
from waitress import serve
from webapp.app import app

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5000"))
    print(f"leadgen serving on http://{host}:{port}  (Ctrl+C to stop)")
    serve(app, host=host, port=port, threads=8)
