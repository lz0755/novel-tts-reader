import json
import shutil
import ssl
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event

import httpx
import pytest

from novel_tts.application.costs import calibrate, estimate, pricing_key
from novel_tts.domain.models import ProviderError
from novel_tts.infrastructure.profiles import DEFAULTS
from novel_tts.infrastructure.store import Store
from novel_tts.providers.llm import RemoteLLM


def test_lost_response_and_manual_resend_survive_restart(book, monkeypatch):
    store, _, _, view = book
    p = DEFAULTS["custom"]
    monkeypatch.setattr("novel_tts.infrastructure.store.time.time", lambda: 1800000000.0)

    def lost(request):
        raise httpx.ReadTimeout("response lost")

    def send(db):
        client = RemoteLLM(
            p,
            "",
            transport=httpx.MockTransport(lost),
            record=lambda r: db.record_attempt(view.book, r),
        )
        # A fresh request has a new batch id, as it would from the desktop.
        with pytest.raises(ProviderError, match="可能已执行并计费"):
            client.analyze_chunk(db.request(view.chapter, 0), Event())

    send(store)
    first = store.attempts()[0]
    reopened = Store(store.paths)
    try:
        send(reopened)
        second = reopened.attempts()[0]
        assert first["job"] != second["job"] and first["hash"] != second["hash"]
        assert first["logical_id"] == second["logical_id"]
        assert second["previous_attempt"] == first["attempt"]
        assert (
            second["previous_job"] == first["job"]
            and second["duplicate_charge_possible"]
        )
        assert second["state"] == "unknown" and second["cost"] is None
    finally:
        reopened.close()


def test_ten_chapter_cost_preview_matches_active_pricing():
    p = replace(
        DEFAULTS["custom"],
        price_date="2026-11-21",
        currency="CNY",
        input_per_million=3,
        output_per_million=10,
    )
    corpus = json.loads(
        (Path(__file__).parents[1] / "fixtures/regression.json").read_text(
            encoding="utf-8"
        )
    )
    rows = []
    for i, ch in enumerate(corpus["chapters"]):
        rows.extend({"id": s["id"], "parts": s["gold"]} for s in ch["parents"])
        rows.append(
            dict(
                attempt=str(i),
                kind="usage",
                cost=0.03 + i / 100,
                chapter=ch["title"],
                currency=p.currency,
                price_date=p.price_date,
                pricing_key=pricing_key(p),
            )
        )
    report = calibrate(corpus, rows)
    low, high = estimate(report, pricing_key(p), 10000)
    assert 0 < low < high and report["attempts"] == 10
    with pytest.raises(ValueError, match="定价配置|价格配置"):
        estimate(report, pricing_key(replace(p, model="another")), 10000)
    with pytest.raises(ValueError, match="未知费用"):
        calibrate(corpus, rows[:-1] + [rows[-1] | {"cost": None}])
    with pytest.raises(ValueError, match="十章完整"):
        calibrate(corpus, rows[1:])


class NoCredentialsProxy(BaseHTTPRequestHandler):
    def do_CONNECT(self):
        self.send_error(407, "Proxy authentication required")

    def log_message(self, *args):
        pass


@contextmanager
def local_server(ctx=None):
    with ThreadingHTTPServer(("127.0.0.1", 0), NoCredentialsProxy) as server:
        if ctx:
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            worker.join()


@pytest.mark.parametrize("fault", ["proxy", "certificate"])
def test_real_transport_diagnostics(book, tmp_path, monkeypatch, fault):
    store, _, _, view = book
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    ctx = None
    if fault == "certificate":
        openssl = shutil.which("openssl")
        if not openssl:
            pytest.skip("TLS 测试需要本地 openssl")
        cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(key),
                "-out",
                str(cert),
            ],
            check=True,
            capture_output=True,
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
    with local_server(ctx) as port:
        if fault == "proxy":
            monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
            endpoint = "https://book-sample.invalid/v1"
        else:
            endpoint = f"https://127.0.0.1:{port}/v1"
        p = replace(DEFAULTS["custom"], endpoint=endpoint)
        client = RemoteLLM(p, "", record=lambda r: store.record_attempt(view.book, r))
        with pytest.raises(ProviderError, match="代理|TLS"):
            client.analyze_chunk(store.request(view.chapter, 0), Event())
    assert store.attempts()[0]["state"] == fault + "_failed"
