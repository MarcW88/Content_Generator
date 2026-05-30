"""
webhook.py
──────────
Serveur HTTP léger qui expose l'agent via un webhook POST.
Compatible Make (Integromat) et n8n.

Endpoint : POST /generate
Body JSON : { "keyword": "...", "refresh_style": false }

Réponse   : JSON bundle (même structure que outputs/<slug>.json)

Sécurité optionnelle : header X-Secret doit matcher WEBHOOK_SECRET
si la variable est définie dans .env

Démarrage :
    python webhook.py
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
from agent import run

logger = logging.getLogger("webhook")
logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt= "%H:%M:%S",
)


class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # noqa: A002
        logger.info("%s — %s", self.address_string(), format % args)

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "agent": "content-agent"})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/generate":
            self._send_json(404, {"error": "Unknown endpoint. Use POST /generate"})
            return

        # ── Auth check ────────────────────────────────────────────────────────
        if config.WEBHOOK_SECRET:
            provided = self.headers.get("X-Secret", "")
            if provided != config.WEBHOOK_SECRET:
                self._send_json(401, {"error": "Invalid or missing X-Secret header"})
                return

        # ── Parse body ────────────────────────────────────────────────────────
        length  = int(self.headers.get("Content-Length", 0))
        raw     = self.rfile.read(length)
        try:
            body    = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        keyword = body.get("keyword", "").strip()
        if not keyword:
            self._send_json(400, {"error": "Missing 'keyword' field"})
            return

        refresh = bool(body.get("refresh_style", False))

        # ── Run agent (blocking — Make / n8n handle timeout) ─────────────────
        logger.info("Received generation request: keyword=%r refresh=%s", keyword, refresh)
        try:
            result = run(keyword, refresh_style=refresh)
            self._send_json(200, result)
        except Exception as exc:
            logger.exception("Agent run failed")
            self._send_json(500, {"error": str(exc)})


def start_server():
    server  = HTTPServer((config.WEBHOOK_HOST, config.WEBHOOK_PORT), WebhookHandler)
    logger.info(
        "Webhook server listening on http://%s:%d",
        config.WEBHOOK_HOST,
        config.WEBHOOK_PORT,
    )
    logger.info("Endpoint  : POST http://localhost:%d/generate", config.WEBHOOK_PORT)
    logger.info("Health    : GET  http://localhost:%d/health",   config.WEBHOOK_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")
        server.server_close()


if __name__ == "__main__":
    start_server()
