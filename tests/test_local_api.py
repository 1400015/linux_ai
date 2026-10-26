"""Tests for src.local_api — loopback-only REST API."""

import json
import unittest
import urllib.request
import urllib.error
from types import SimpleNamespace

from src.local_api import (
    APIContext,
    LocalAPIError,
    LocalAPIServer,
    ROUTES,
    find_route,
    generate_token,
    is_loopback_host,
    path_exists,
)


class TestIsLoopbackHost(unittest.TestCase):
    def test_localhost(self):
        self.assertTrue(is_loopback_host("localhost"))

    def test_127_0_0_1(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))

    def test_127_range(self):
        self.assertTrue(is_loopback_host("127.0.0.2"))
        self.assertTrue(is_loopback_host("127.1.2.3"))

    def test_ipv6_loopback(self):
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("[::1]"))

    def test_rejects_non_loopback(self):
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.1"))
        self.assertFalse(is_loopback_host("10.0.0.1"))
        self.assertFalse(is_loopback_host("example.com"))
        self.assertFalse(is_loopback_host(""))

    def test_rejects_malformed_127(self):
        self.assertFalse(is_loopback_host("127.0.0"))
        self.assertFalse(is_loopback_host("127.0.0.256"))
        self.assertFalse(is_loopback_host("127.abc.def.ghi"))


class TestGenerateToken(unittest.TestCase):
    def test_token_is_random(self):
        a = generate_token()
        b = generate_token()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 32)

    def test_token_is_urlsafe(self):
        token = generate_token()
        # urlsafe base64: only these characters
        import string
        allowed = set(string.ascii_letters + string.digits + "-_")
        self.assertTrue(all(c in allowed for c in token))


class TestRouteTable(unittest.TestCase):
    def test_routes_are_unique(self):
        keys = [(r.method, r.path) for r in ROUTES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_health_is_public(self):
        route = find_route("GET", "/api/health")
        self.assertIsNotNone(route)
        self.assertFalse(route.auth_required)

    def test_all_other_routes_require_auth(self):
        for route in ROUTES:
            if route.path == "/api/health":
                continue
            self.assertTrue(route.auth_required, route.path)

    def test_find_route_unknown(self):
        self.assertIsNone(find_route("GET", "/api/nope"))
        self.assertIsNone(find_route("DELETE", "/api/stats"))

    def test_path_exists(self):
        self.assertTrue(path_exists("/api/stats"))
        self.assertFalse(path_exists("/api/nope"))

    def test_expected_routes_present(self):
        paths = {r.path for r in ROUTES}
        for expected in ("/api/health", "/api/stats", "/api/sessions", "/api/config",
                         "/api/a11y/contrast", "/api/pomodoro", "/api/sync/preview",
                         "/api/sync/import", "/api/benchmarks"):
            self.assertIn(expected, paths)


class TestLocalAPIError(unittest.TestCase):
    def test_carries_status(self):
        err = LocalAPIError(404, "not found")
        self.assertEqual(err.status, 404)
        self.assertEqual(err.message, "not found")
        self.assertEqual(str(err), "not found")


class LiveServerMixin:
    """Start a real LocalAPIServer on an ephemeral loopback port."""

    def start_server(self, ctx=None, **ctx_kwargs):
        if ctx is None:
            ctx = APIContext(token=generate_token(), **ctx_kwargs)
        server = LocalAPIServer(ctx, host="127.0.0.1", port=0)
        server.start_background()
        self.addCleanup(server.shutdown)
        return server, ctx

    def request(self, server, method, path, token=None, body=None, headers=None):
        url = server.url + path
        data = None
        req_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        if token:
            req_headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))


class TestLiveServer(LiveServerMixin, unittest.TestCase):
    def test_health_no_auth(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["data"]["status"], "ok")
        self.assertIn("uptime_seconds", body["data"])

    def test_auth_required(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/stats")
        self.assertEqual(status, 401)
        self.assertFalse(body["ok"])

    def test_auth_with_bearer_token(self):
        server, ctx = self.start_server(ai_client=SimpleNamespace(
            get_token_usage=lambda provider=None: {}))
        status, body = self.request(server, "GET", "/api/stats", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_auth_with_custom_header(self):
        server, ctx = self.start_server(ai_client=SimpleNamespace(
            get_token_usage=lambda provider=None: {}))
        status, body = self.request(server, "GET", "/api/stats",
                                    headers={"X-Linux-AI-Token": ctx.token})
        self.assertEqual(status, 200)

    def test_wrong_token_rejected(self):
        server, ctx = self.start_server(ai_client=SimpleNamespace(
            get_token_usage=lambda provider=None: {}))
        status, body = self.request(server, "GET", "/api/stats", token="wrong-token")
        self.assertEqual(status, 401)

    def test_404_unknown_route(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/nope", token=ctx.token)
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])

    def test_405_wrong_method(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "DELETE", "/api/stats", token=ctx.token)
        self.assertEqual(status, 405)

    def test_trailing_slash_normalized(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/health/")
        self.assertEqual(status, 200)

    def test_server_binds_ephemeral_port(self):
        server, ctx = self.start_server()
        self.assertGreater(server.port, 0)
        self.assertIn("127.0.0.1", server.url)

    def test_request_count_increments(self):
        server, ctx = self.start_server()
        self.request(server, "GET", "/api/health")
        self.request(server, "GET", "/api/health")
        status, body = self.request(server, "GET", "/api/health")
        self.assertEqual(body["data"]["requests_served"], 3)


class TestLiveServerRefusesNonLoopback(unittest.TestCase):
    def test_refuses_0_0_0_0(self):
        ctx = APIContext(token="x")
        with self.assertRaises(ValueError):
            LocalAPIServer(ctx, host="0.0.0.0", port=0)

    def test_refuses_external_ip(self):
        ctx = APIContext(token="x")
        with self.assertRaises(ValueError):
            LocalAPIServer(ctx, host="192.168.1.1", port=0)


class TestStatsEndpoint(LiveServerMixin, unittest.TestCase):
    def test_stats_with_usage(self):
        client = SimpleNamespace(get_token_usage=lambda provider=None: {
            "openrouter": {"input": 1000, "output": 500, "total": 1500},
            "local_llm": {"input": 200, "output": 100, "total": 300},
        })
        server, ctx = self.start_server(ai_client=client)
        status, body = self.request(server, "GET", "/api/stats", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["total_tokens"], 1800)
        self.assertEqual(body["data"]["provider_count"], 2)
        self.assertIn("text", body["data"])

    def test_stats_no_client(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/stats", token=ctx.token)
        self.assertEqual(status, 503)


class TestSessionsEndpoint(LiveServerMixin, unittest.TestCase):
    def test_sessions_list(self):
        store = SimpleNamespace(list_sessions=lambda include_archived=False: [
            {"id": "a", "title": "First", "message_count": 5, "archived": False,
             "created_at": 1.0, "updated_at": 2.0},
            {"id": "b", "title": "Second", "message_count": 3, "archived": True,
             "created_at": 3.0, "updated_at": 4.0},
        ])
        server, ctx = self.start_server(history_store=store)
        status, body = self.request(server, "GET", "/api/sessions", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["count"], 2)

    def test_sessions_archived_filter(self):
        calls = []

        def list_sessions(include_archived=False):
            calls.append(include_archived)
            return []

        store = SimpleNamespace(list_sessions=list_sessions)
        server, ctx = self.start_server(history_store=store)
        self.request(server, "GET", "/api/sessions?archived=true", token=ctx.token)
        self.assertEqual(calls, [True])

    def test_sessions_no_store(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/sessions", token=ctx.token)
        self.assertEqual(status, 503)


class TestConfigEndpoint(LiveServerMixin, unittest.TestCase):
    def test_config_returns_safe_subset(self):
        config = SimpleNamespace(
            get=lambda key, default=None: {
                "app.theme": "dark",
                "app.language": "pt",
                "app.expert_mode": False,
                "app.always_on_top": True,
                "app.dock_mode": "float",
                "app.dock_edge": "right",
                "ui.font_family": "Monospace",
                "ui.font_size": 12,
                "context.max_messages": 20,
                "context.max_chars": 12000,
                "pomodoro.enabled": True,
                "pomodoro.work_seconds": 1500,
                "pomodoro.short_break_seconds": 300,
                "pomodoro.long_break_seconds": 900,
                "pomodoro.cycles_before_long_break": 4,
            }.get(key, default),
            get_assistance_mode=lambda: "auto",
            get_local_model_settings=lambda: {
                "base_url": "http://localhost:11434/v1",
                "model": "llama3.2",
                "timeout": 120,
                "backend": "ollama",
                "strict_local": True,
            },
        )
        server, ctx = self.start_server(config=config)
        status, body = self.request(server, "GET", "/api/config", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["app"]["theme"], "dark")
        self.assertEqual(body["data"]["assistance"]["mode"], "auto")
        # No credentials in the response.
        flat = json.dumps(body)
        self.assertNotIn("api_key", flat)

    def test_config_no_manager(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/config", token=ctx.token)
        self.assertEqual(status, 503)


class TestContrastEndpoint(LiveServerMixin, unittest.TestCase):
    def test_contrast_get(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "GET", "/api/a11y/contrast?fg=%23000000&bg=%23ffffff",
            token=ctx.token)
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body["data"]["ratio"], 21.0, places=1)
        self.assertEqual(body["data"]["level"], "AAA")

    def test_contrast_get_missing_params(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/a11y/contrast", token=ctx.token)
        self.assertEqual(status, 400)

    def test_contrast_get_invalid_color(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "GET", "/api/a11y/contrast?fg=red&bg=%23ffffff",
            token=ctx.token)
        self.assertEqual(status, 400)

    def test_contrast_post_batch(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/a11y/contrast", token=ctx.token,
            body={"pairs": [
                {"fg": "#000000", "bg": "#ffffff"},
                {"fg": "#cccccc", "bg": "#ffffff"},
            ]})
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["count"], 2)
        self.assertEqual(body["data"]["results"][0]["level"], "AAA")
        self.assertEqual(body["data"]["results"][1]["level"], "Fail")

    def test_contrast_post_empty_pairs(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/a11y/contrast", token=ctx.token,
            body={"pairs": []})
        self.assertEqual(status, 400)

    def test_contrast_post_too_many(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/a11y/contrast", token=ctx.token,
            body={"pairs": [{"fg": "#000", "bg": "#fff"}] * 101})
        self.assertEqual(status, 400)

    def test_contrast_post_invalid_pair(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/a11y/contrast", token=ctx.token,
            body={"pairs": [{"fg": "bad", "bg": "#ffffff"}]})
        self.assertEqual(status, 200)
        self.assertIn("error", body["data"]["results"][0])


class TestPomodoroEndpoint(LiveServerMixin, unittest.TestCase):
    def make_timer(self):
        from src.pomodoro import PomodoroTimer, PomodoroConfig
        return PomodoroTimer(config=PomodoroConfig(work_seconds=1500))

    def test_pomodoro_get(self):
        server, ctx = self.start_server(pomodoro=self.make_timer())
        status, body = self.request(server, "GET", "/api/pomodoro", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["phase"], "work")
        self.assertEqual(body["data"]["remaining_seconds"], 1500.0)

    def test_pomodoro_start_and_pause(self):
        timer = self.make_timer()
        server, ctx = self.start_server(pomodoro=timer)
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token,
            body={"action": "start"})
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["running"])
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token,
            body={"action": "pause"})
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["running"])

    def test_pomodoro_skip(self):
        timer = self.make_timer()
        server, ctx = self.start_server(pomodoro=timer)
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token,
            body={"action": "skip"})
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["phase"], "short_break")

    def test_pomodoro_reset(self):
        timer = self.make_timer()
        server, ctx = self.start_server(pomodoro=timer)
        self.request(server, "POST", "/api/pomodoro", token=ctx.token,
                     body={"action": "start"})
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token,
            body={"action": "reset"})
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["phase"], "work")
        self.assertFalse(body["data"]["running"])

    def test_pomodoro_invalid_action(self):
        server, ctx = self.start_server(pomodoro=self.make_timer())
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token,
            body={"action": "bogus"})
        self.assertEqual(status, 400)

    def test_pomodoro_missing_action(self):
        server, ctx = self.start_server(pomodoro=self.make_timer())
        status, body = self.request(
            server, "POST", "/api/pomodoro", token=ctx.token, body={})
        self.assertEqual(status, 400)

    def test_pomodoro_no_timer(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/pomodoro", token=ctx.token)
        self.assertEqual(status, 503)


class TestSyncEndpoints(LiveServerMixin, unittest.TestCase):
    def make_bundle_text(self):
        from src.sync_bundle import build_bundle, serialize_bundle
        return serialize_bundle(build_bundle(
            {"app": {"theme": "dark", "language": "pt"},
             "assistance": {"mode": "auto"}},
            [{"id": "s1", "title": "Test", "created_at": 1.0, "updated_at": 2.0,
              "archived": False, "messages": []}],
        ))

    def test_sync_preview(self):
        config = SimpleNamespace(config={"app": {"theme": "light"}})
        store = SimpleNamespace(list_sessions=lambda include_archived=False: [])
        server, ctx = self.start_server(config=config, history_store=store)
        status, body = self.request(
            server, "POST", "/api/sync/preview", token=ctx.token,
            body={"bundle": self.make_bundle_text()})
        self.assertEqual(status, 200)
        self.assertIn("app.theme", body["data"]["settings_to_apply"])
        self.assertEqual(body["data"]["sessions_to_add"], 1)

    def test_sync_preview_invalid_bundle(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/sync/preview", token=ctx.token,
            body={"bundle": "not json"})
        self.assertEqual(status, 400)

    def test_sync_preview_missing_bundle(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/sync/preview", token=ctx.token, body={})
        self.assertEqual(status, 400)

    def test_sync_import_applies(self):
        config = SimpleNamespace(
            config={"app": {"theme": "light"}},
            flush=lambda: True,
        )
        store = SimpleNamespace(
            list_sessions=lambda include_archived=False: [],
            _snapshot=lambda: {"sessions": [], "active_session_id": None, "version": 1},
            replace_sessions=lambda sessions: None,
        )
        server, ctx = self.start_server(config=config, history_store=store)
        status, body = self.request(
            server, "POST", "/api/sync/import", token=ctx.token,
            body={"bundle": self.make_bundle_text()})
        self.assertEqual(status, 200)
        self.assertGreater(body["data"]["applied_settings"], 0)
        self.assertEqual(config.config["app"]["theme"], "dark")

    def test_sync_import_no_config(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/sync/import", token=ctx.token,
            body={"bundle": self.make_bundle_text()})
        self.assertEqual(status, 503)


class TestBenchmarksEndpoint(LiveServerMixin, unittest.TestCase):
    def test_benchmarks_get_empty(self):
        server, ctx = self.start_server()
        status, body = self.request(server, "GET", "/api/benchmarks", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["cached"])

    def test_benchmarks_post_runs(self):
        server, ctx = self.start_server()
        status, body = self.request(
            server, "POST", "/api/benchmarks", token=ctx.token,
            body={"scale": 0.05})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("results", body["data"])

    def test_benchmarks_get_after_post(self):
        server, ctx = self.start_server()
        self.request(server, "POST", "/api/benchmarks", token=ctx.token,
                     body={"scale": 0.05})
        status, body = self.request(server, "GET", "/api/benchmarks", token=ctx.token)
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["cached"])

    def test_benchmarks_post_invalid_scale(self):
        server, ctx = self.start_server()
        for bad in (0, -1, "fast", True, 100):
            with self.subTest(scale=bad):
                status, body = self.request(
                    server, "POST", "/api/benchmarks", token=ctx.token,
                    body={"scale": bad})
                self.assertEqual(status, 400)


class TestRequestBodyLimits(LiveServerMixin, unittest.TestCase):
    def test_oversized_body_rejected(self):
        server, ctx = self.start_server()
        # Build a body just over 1 MiB.
        big = {"bundle": "x" * (1024 * 1024)}
        url = server.url + "/api/sync/preview"
        data = json.dumps(big).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(data)),
                "Authorization": "Bearer " + ctx.token,
            })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            status = error.code
            json.loads(error.read().decode())
        self.assertEqual(status, 413)

    def test_invalid_json_body(self):
        server, ctx = self.start_server()
        url = server.url + "/api/pomodoro"
        data = b"not json{"
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + ctx.token,
            })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 400)

    def test_non_object_json_body(self):
        server, ctx = self.start_server(pomodoro=SimpleNamespace(
            to_dict=lambda: {}, remaining=lambda: 0, progress=lambda: 0))
        url = server.url + "/api/pomodoro"
        data = b"[1, 2, 3]"
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + ctx.token,
            })
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 400)


class TestSecurityHeaders(LiveServerMixin, unittest.TestCase):
    def test_no_cors_headers(self):
        server, ctx = self.start_server()
        url = server.url + "/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertNotIn("Access-Control-Allow-Origin", response.headers)

    def test_nosniff_header(self):
        server, ctx = self.start_server()
        url = server.url + "/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_content_type_json(self):
        server, ctx = self.start_server()
        url = server.url + "/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertIn("application/json", response.headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()
