#!/usr/bin/env python3

import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import operator_api_token
import web_dashboard


class TestOperatorApiToken(unittest.TestCase):
    def test_token_is_generated_once_and_compared(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / 'api_token.txt'
            with patch.object(operator_api_token, 'API_TOKEN_FILE', token_path):
                first = operator_api_token.ensure_api_token()
                second = operator_api_token.ensure_api_token()
                self.assertEqual(first, second)
                self.assertTrue(operator_api_token.api_token_matches(first))
                self.assertFalse(operator_api_token.api_token_matches('wrong'))
                self.assertGreaterEqual(len(first), 32)

    def test_dashboard_rejects_unauthenticated_writes_except_requests(self):
        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
        server = web_dashboard.ThreadingHTTPServer(
            ('127.0.0.1', 0), web_dashboard.DashboardHandler
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        base_url = f'http://127.0.0.1:{server.server_address[1]}'

        def post(path: str) -> int:
            request = urllib.request.Request(
                base_url + path,
                data=b'{}',
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as exc:
                return exc.code

        try:
            self.assertEqual(post('/api/jobs'), 401)
            self.assertEqual(post('/api/config'), 401)
            self.assertEqual(post('/api/requests'), 400)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
