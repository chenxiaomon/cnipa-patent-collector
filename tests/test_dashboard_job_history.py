"""Dashboard task history keeps active work visible and reports export failures."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import web_dashboard


class TestVisibleJobs(unittest.TestCase):
    def test_old_active_job_survives_completed_history_limit(self):
        job_manager = web_dashboard.JobManager()
        active_job = web_dashboard.Job(
            'active', 'mitm_proxy', '主代理', ['python', 'start_mitm_proxy.py'],
            started_at='2026-01-01T00:00:00Z',
        )
        job_manager._jobs[active_job.id] = active_job
        for index in range(web_dashboard.MAX_COMPLETED_JOBS + 5):
            job = web_dashboard.Job(
                f'done-{index}', 'export_excel', '导出', ['python', 'export.py'],
                started_at=f'2026-09-06T00:00:{index:02d}Z', status='finished',
            )
            job_manager._jobs[job.id] = job

        visible_jobs = job_manager.list_jobs()

        self.assertIn('active', {job['id'] for job in visible_jobs})
        self.assertEqual(len(visible_jobs), web_dashboard.MAX_COMPLETED_JOBS + 1)
        self.assertEqual(len(job_manager._jobs), web_dashboard.MAX_COMPLETED_JOBS + 1)

    def test_long_running_job_is_retained_when_it_finishes_recently(self):
        job_manager = web_dashboard.JobManager()
        long_job = web_dashboard.Job(
            'long', 'main_full', '长时间采集', ['python', 'collection_watchdog.py'],
            started_at='2026-01-01T00:00:00Z',
            finished_at='2026-09-06T12:00:00Z', status='finished',
        )
        job_manager._jobs[long_job.id] = long_job
        for index in range(web_dashboard.MAX_COMPLETED_JOBS):
            job = web_dashboard.Job(
                f'done-{index}', 'export_excel', '导出', ['python', 'export.py'],
                started_at=f'2026-09-06T10:00:{index:02d}Z',
                finished_at=f'2026-09-06T11:00:{index:02d}Z', status='finished',
            )
            job_manager._jobs[job.id] = job

        visible_ids = {job['id'] for job in job_manager.list_jobs()}

        self.assertIn(long_job.id, visible_ids)
        self.assertEqual(len(visible_ids), web_dashboard.MAX_COMPLETED_JOBS)

    def test_code_maintenance_and_collection_jobs_conflict_both_ways(self):
        job_manager = web_dashboard.JobManager()
        collection_job = web_dashboard.Job(
            'collecting', 'main_full', '主采集', ['python', 'collection_watchdog.py'],
        )
        job_manager._jobs[collection_job.id] = collection_job
        self.assertIs(
            job_manager._start_conflict_locked('fetch_update'),
            collection_job,
        )

        job_manager._jobs.clear()
        update_job = web_dashboard.Job(
            'updating', 'fetch_update', '代码更新', ['python', 'fetch_update.py'],
        )
        job_manager._jobs[update_job.id] = update_job
        self.assertIs(job_manager._start_conflict_locked('main_full'), update_job)
        self.assertIs(job_manager._start_conflict_locked('upgrade_code'), update_job)

    def test_rejected_manual_batch_creates_no_request_file(self):
        job_manager = web_dashboard.JobManager()
        collection_job = web_dashboard.Job(
            'collecting', 'main_full', '主采集', ['python', 'collection_watchdog.py'],
        )
        job_manager._jobs[collection_job.id] = collection_job

        with patch.object(web_dashboard, 'create_manual_fwxx_request') as create_request, patch.object(
            web_dashboard.subprocess, 'Popen'
        ) as popen:
            with self.assertRaisesRegex(ValueError, '桌面浏览器'):
                job_manager.start('collect_fwxx_batch', {'app_nos': '202310411762X'})

        create_request.assert_not_called()
        popen.assert_not_called()


class TestFilteredExcelBuild(unittest.TestCase):
    def test_failed_export_raises_and_removes_temporary_file(self):
        with patch('detection_logger.DetectionLogger') as logger_class:
            logger_class.return_value.export_to_excel.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'Excel 生成失败'):
                web_dashboard.build_filtered_excel([])
            exported_path = Path(logger_class.return_value.export_to_excel.call_args.args[0])
        self.assertFalse(exported_path.exists())

    def test_successful_export_returns_workbook_and_removes_temporary_file(self):
        workbook = b'workbook-bytes'

        def write_workbook(path):
            Path(path).write_bytes(workbook)
            return True

        with patch('detection_logger.DetectionLogger') as logger_class:
            logger_class.return_value.export_to_excel.side_effect = write_workbook
            self.assertEqual(
                web_dashboard.build_filtered_excel([{'application_no': 'A'}]),
                workbook,
            )
            exported_path = Path(logger_class.return_value.export_to_excel.call_args.args[0])
        self.assertFalse(exported_path.exists())

    def test_empty_export_is_rejected_and_removes_temporary_file(self):
        with patch('detection_logger.DetectionLogger') as logger_class:
            logger_class.return_value.export_to_excel.return_value = True
            with self.assertRaisesRegex(RuntimeError, '输出文件为空'):
                web_dashboard.build_filtered_excel([])
            exported_path = Path(logger_class.return_value.export_to_excel.call_args.args[0])
        self.assertFalse(exported_path.exists())

    def test_export_exception_removes_partial_file(self):
        def fail_after_partial_write(path):
            Path(path).write_bytes(b'partial workbook')
            raise OSError('writer failed')

        with patch('detection_logger.DetectionLogger') as logger_class:
            logger_class.return_value.export_to_excel.side_effect = fail_after_partial_write
            with self.assertRaisesRegex(OSError, 'writer failed'):
                web_dashboard.build_filtered_excel([])
            exported_path = Path(logger_class.return_value.export_to_excel.call_args.args[0])
        self.assertFalse(exported_path.exists())

    def test_cleanup_failure_keeps_original_export_error_visible(self):
        real_unlink = Path.unlink
        exported_path = None

        def locked_temporary_file(path, *args, **kwargs):
            del args, kwargs
            nonlocal exported_path
            exported_path = path
            raise PermissionError('file locked')

        with patch('detection_logger.DetectionLogger') as logger_class, patch.object(
            Path, 'unlink', locked_temporary_file,
        ):
            logger_class.return_value.export_to_excel.return_value = False
            with self.assertRaisesRegex(
                RuntimeError,
                'Excel 生成失败.*临时文件清理失败.*file locked',
            ):
                web_dashboard.build_filtered_excel([])

        self.assertIsNotNone(exported_path)
        real_unlink(exported_path, missing_ok=True)

    def test_cleanup_failure_does_not_replace_keyboard_interrupt(self):
        real_unlink = Path.unlink
        exported_path = None

        def locked_temporary_file(path, *args, **kwargs):
            del args, kwargs
            nonlocal exported_path
            exported_path = path
            raise PermissionError('file locked')

        with patch('detection_logger.DetectionLogger') as logger_class, patch.object(
            Path, 'unlink', locked_temporary_file,
        ):
            logger_class.return_value.export_to_excel.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                web_dashboard.build_filtered_excel([])

        self.assertIsNotNone(exported_path)
        real_unlink(exported_path, missing_ok=True)

    def test_http_export_failure_returns_json_500(self):
        web_dashboard.DashboardHandler.job_manager = web_dashboard.JobManager()
        with patch.object(web_dashboard, 'api_token_matches', return_value=True), patch.object(
            web_dashboard._patents_db, 'query_filtered', return_value=[]
        ), patch.object(
            web_dashboard, 'build_filtered_excel', side_effect=RuntimeError('Excel 生成失败')
        ):
            server = web_dashboard.ThreadingHTTPServer(
                ('127.0.0.1', 0), web_dashboard.DashboardHandler,
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            request = urllib.request.Request(
                f'http://127.0.0.1:{server.server_address[1]}/api/export/excel-filtered',
                data=json.dumps({}).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST',
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as response_error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(response_error.exception.code, 500)
                response_payload = json.loads(response_error.exception.read().decode('utf-8'))
                self.assertEqual(response_payload, {'error': 'Excel 生成失败'})
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
