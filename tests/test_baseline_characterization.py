"""Baseline characterization tests pinning CURRENT job-queue DB behavior.

These tests pass against the unmodified codebase and exist to detect
accidental behavior drift while ui/database.py and ui/job_manager.py are
redesigned (see .omo/plans/task-queue-redesign.md).
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir


def _purge(job_ids):
    from database import get_db
    with get_db() as conn:
        conn.executemany(
            'DELETE FROM recipe_jobs WHERE id = ?', [(j,) for j in job_ids]
        )
        conn.commit()


class TestJobLifecycle(unittest.TestCase):
    def test_create_job_defaults(self):
        from database import create_job, get_job
        job_id = create_job('https://example.com/a')
        try:
            self.assertIsInstance(job_id, str)
            self.assertTrue(job_id)
            job = get_job(job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job['status'], 'queued')
            self.assertEqual(job['progress'], 0)
        finally:
            _purge([job_id])

    def test_queue_position_creation_order(self):
        from database import create_job, get_queue_position
        ids = [create_job(f'https://example.com/q{i}') for i in range(3)]
        try:
            self.assertEqual([get_queue_position(j) for j in ids], [1, 2, 3])
            self.assertEqual(get_queue_position('no-such-job'), 0)
        finally:
            _purge(ids)

    def test_update_job_progress_persists_fields(self):
        from database import create_job, get_job, update_job_progress
        job_id = create_job('https://example.com/p')
        try:
            ok = update_job_progress(
                job_id, 'transcribing', 42, 'transcribe',
                'Transcribing audio...', 'Some Video Title',
            )
            self.assertTrue(ok)
            job = get_job(job_id)
            self.assertEqual(job['status'], 'transcribing')
            self.assertEqual(job['progress'], 42)
            self.assertEqual(job['current_stage'], 'transcribe')
            self.assertEqual(job['stage_message'], 'Transcribing audio...')
            self.assertEqual(job['video_title'], 'Some Video Title')

            # No video_title variant must not wipe or fail.
            ok = update_job_progress(
                job_id, 'evaluate', 85, 'evaluate', 'Creating recipe...'
            )
            self.assertTrue(ok)
            self.assertEqual(get_job(job_id)['video_title'], 'Some Video Title')
        finally:
            _purge([job_id])

    def test_terminal_transitions(self):
        from database import (
            cancel_job, complete_job, create_job, fail_job, get_job,
        )
        f = create_job('https://example.com/f')
        c = create_job('https://example.com/c')
        x = create_job('https://example.com/x')
        try:
            self.assertTrue(fail_job(f, 'boom'))
            job = get_job(f)
            self.assertEqual(job['status'], 'failed')
            self.assertEqual(job['error_message'], 'boom')

            self.assertTrue(cancel_job(c))
            self.assertEqual(get_job(c)['status'], 'cancelled')

            self.assertTrue(complete_job(x))
            job = get_job(x)
            self.assertEqual(job['status'], 'completed')
            self.assertEqual(job['progress'], 100)
        finally:
            _purge([f, c, x])


class TestPendingUploads(unittest.TestCase):
    def _mk(self, timeout=5):
        from database import create_pending_upload, get_db
        upload_id = 'up_' + os.urandom(8).hex()
        create_pending_upload(
            upload_id=upload_id,
            job_id='job_' + os.urandom(8).hex(),
            recipe_data={'name': 'Test Dish', 'ingredients': ['a', 'b']},
            image_path=None,
            image_candidates=[],
            output_target='mealie',
            best_image_index=0,
            timeout_minutes=timeout,
        )
        if timeout < 0:
            # Current quirk: negative timeouts yield an invalid SQLite
            # modifier and therefore NULL expires_at (never expires).
            # Backdate directly to simulate a genuinely expired row.
            with get_db() as conn:
                conn.execute(
                    "UPDATE pending_uploads SET expires_at = datetime('now', '-1 hour') "
                    'WHERE id = ?',
                    (upload_id,),
                )
                conn.commit()
        return upload_id

    def test_roundtrip_preserves_recipe(self):
        from database import get_pending_upload
        upload_id = self._mk()
        item = get_pending_upload(upload_id)
        self.assertIsNotNone(item)
        self.assertEqual(item['recipe_data']['name'], 'Test Dish')
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(item['output_target'], 'mealie')

    def test_update_recipe_only_while_pending(self):
        from database import (
            confirm_pending_upload,
            get_pending_upload,
            update_pending_upload_recipe,
        )
        upload_id = self._mk()
        self.assertTrue(
            update_pending_upload_recipe(upload_id, {'name': 'Edited Dish'})
        )
        self.assertEqual(get_pending_upload(upload_id)['recipe_data']['name'], 'Edited Dish')
        self.assertTrue(confirm_pending_upload(upload_id))
        self.assertFalse(
            update_pending_upload_recipe(upload_id, {'name': 'Too late'})
        )
        self.assertEqual(get_pending_upload(upload_id)['recipe_data']['name'], 'Edited Dish')

    def test_confirm_is_one_shot(self):
        from database import confirm_pending_upload, get_pending_upload
        upload_id = self._mk()
        self.assertTrue(confirm_pending_upload(upload_id))
        self.assertFalse(confirm_pending_upload(upload_id))
        self.assertEqual(get_pending_upload(upload_id)['status'], 'confirmed')

    def test_cancel_is_one_shot_and_blocks_confirm(self):
        from database import cancel_pending_upload, confirm_pending_upload
        upload_id = self._mk()
        self.assertTrue(cancel_pending_upload(upload_id))
        self.assertFalse(cancel_pending_upload(upload_id))
        self.assertFalse(confirm_pending_upload(upload_id))

    def test_expiry_sweep_marks_expired(self):
        from database import (
            cleanup_expired_pending_uploads, get_pending_upload,
        )
        upload_id = self._mk(timeout=-1)
        swept = cleanup_expired_pending_uploads()
        self.assertGreaterEqual(swept, 1)
        self.assertEqual(get_pending_upload(upload_id)['status'], 'expired')


class TestActiveJobsFiltering(unittest.TestCase):
    def test_active_excludes_terminal_states(self):
        from database import (
            cancel_job, complete_job, create_job, fail_job, get_active_jobs,
        )
        f = create_job('https://example.com/af')
        c = create_job('https://example.com/ac')
        x = create_job('https://example.com/ax')
        live = create_job('https://example.com/live')
        try:
            fail_job(f, 'boom')
            cancel_job(c)
            complete_job(x)
            statuses = {j['id']: j['status'] for j in get_active_jobs()}
            self.assertNotIn(f, statuses)
            self.assertNotIn(c, statuses)
            self.assertNotIn(x, statuses)
            self.assertIn(live, statuses)
        finally:
            _purge([f, c, x, live])


if __name__ == '__main__':
    unittest.main()
