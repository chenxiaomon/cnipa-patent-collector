#!/usr/bin/env python3

import json
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

    def test_dashboard_saves_and_resets_all_coordinate_configs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / 'config.json'
            detail_config_path = Path(tmpdir) / 'config_fwxx.json'
            web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()

            with (
                patch.object(web_dashboard, 'CONFIG_FILE', config_path),
                patch.object(web_dashboard, 'CONFIG_FWXX_FILE', detail_config_path),
                patch.object(web_dashboard, 'api_token_matches', return_value=True),
            ):
                server = web_dashboard.ThreadingHTTPServer(
                    ('127.0.0.1', 0), web_dashboard.DashboardHandler
                )
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                base_url = f'http://127.0.0.1:{server.server_address[1]}'

                def post(path: str, payload: dict) -> dict:
                    request = urllib.request.Request(
                        base_url + path,
                        data=json.dumps(payload).encode('utf-8'),
                        headers={'Content-Type': 'application/json'},
                        method='POST',
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.loads(response.read().decode('utf-8'))

                try:
                    search_config = {'input_x': 10, 'input_y': 20, 'button_x': 30, 'button_y': 40}
                    detail_config = {
                        'link_x': 50,
                        'link_y': 60,
                        'fwxx_menu_x': 70,
                        'fwxx_menu_y': 80,
                        'fee_menu_x': 90,
                        'fee_menu_y': 100,
                    }
                    post('/api/config', {
                        'search_text': json.dumps(search_config),
                        'detail_text': json.dumps(detail_config),
                    })

                    self.assertEqual(json.loads(config_path.read_text(encoding='utf-8')), search_config)
                    self.assertEqual(json.loads(detail_config_path.read_text(encoding='utf-8')), detail_config)

                    with self.assertRaises(urllib.error.HTTPError) as invalid_save:
                        post('/api/config', {
                            'search_text': json.dumps({
                                'input_x': 11, 'input_y': 21,
                                'button_x': 31, 'button_y': 41,
                            }),
                            'detail_text': json.dumps({
                                **detail_config,
                                'fee_menu_x': True,
                            }),
                        })
                    self.assertEqual(invalid_save.exception.code, 400)
                    self.assertEqual(json.loads(config_path.read_text(encoding='utf-8')), search_config)
                    self.assertEqual(json.loads(detail_config_path.read_text(encoding='utf-8')), detail_config)

                    reset_response = post('/api/config/reset', {})
                    self.assertFalse(config_path.exists())
                    self.assertFalse(detail_config_path.exists())
                    self.assertEqual(set(reset_response['backups']), {'search', 'detail'})
                    for backup_path in reset_response['backups'].values():
                        self.assertTrue(Path(backup_path).exists())
                finally:
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=5)
