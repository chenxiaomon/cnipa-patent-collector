#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import web_dashboard


class TestPublicBrowserCompanionJobs(unittest.TestCase):
    def test_public_browser_allows_its_auto_paginate_companion(self):
        job_manager = web_dashboard.JobManager()
        public_browser = web_dashboard.Job(
            id="public-browser",
            action="public_browser",
            title="public browser",
            command=["python", "launch_browser_with_proxy.py"],
        )
        job_manager._jobs[public_browser.id] = public_browser

        with job_manager._lock:
            conflict = job_manager._start_conflict_locked("public_auto_paginate")

        self.assertIsNone(conflict)

    def test_duplicate_public_browser_is_still_rejected(self):
        job_manager = web_dashboard.JobManager()
        public_browser = web_dashboard.Job(
            id="public-browser",
            action="public_browser",
            title="public browser",
            command=["python", "launch_browser_with_proxy.py"],
        )
        job_manager._jobs[public_browser.id] = public_browser

        with job_manager._lock:
            conflict = job_manager._start_conflict_locked("public_browser")

        self.assertIs(conflict, public_browser)

    def test_public_browser_still_blocks_fee_collection(self):
        job_manager = web_dashboard.JobManager()
        public_browser = web_dashboard.Job(
            id="public-browser",
            action="public_browser",
            title="public browser",
            command=["python", "launch_browser_with_proxy.py"],
        )
        job_manager._jobs[public_browser.id] = public_browser

        with job_manager._lock:
            conflict = job_manager._start_conflict_locked("collect_fees")

        self.assertIs(conflict, public_browser)

    def test_duplicate_auto_paginate_is_rejected(self):
        job_manager = web_dashboard.JobManager()
        auto_paginate = web_dashboard.Job(
            id="auto-paginate",
            action="public_auto_paginate",
            title="auto paginate",
            command=["python", "auto_paginate_public.py"],
        )
        job_manager._jobs[auto_paginate.id] = auto_paginate

        with job_manager._lock:
            conflict = job_manager._start_conflict_locked(
                "public_auto_paginate"
            )

        self.assertIs(conflict, auto_paginate)


if __name__ == "__main__":
    unittest.main()
