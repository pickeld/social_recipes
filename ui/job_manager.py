"""
Job Manager for Pick-a-Recipe
DB-backed job queue with explicit state machine, configurable concurrency,
slot-free approvals, and progress tracking.

State machine (see .omo/plans/task-queue-redesign.md):

    queued ──► running ──► awaiting_approval ──► uploading ──► completed
                  │               │
                  │               ├──► cancelled / expired
                  │               └──► (approve resumes via upload phase)
                  ├──► failed / cancelled
                  └──► cancelled

Workers never block waiting for human confirmation: reaching the preview
stage parks the job in `awaiting_approval` and frees the worker slot.
"""

import os
import sys
import shutil
import time
import base64
import queue
import secrets
import threading
from typing import Dict, List, Optional, Callable, TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from database import (
    create_job as db_create_job,
    get_job,
    get_active_jobs,
    get_queued_jobs,
    get_db,
    update_job_progress,
    fail_job as db_fail_job,
    cancel_job as db_cancel_job,
    complete_job as db_complete_job,
    create_history_entry,
    get_queue_position,
    update_job_tokens,
    create_pending_upload,
    get_pending_upload,
    get_pending_uploads,
    cancel_pending_upload,
    DATA_DIR,
    extend_leases,
    sweep_stale_leases,
    expire_due_approvals,
    find_stranded_approvals,
)

if TYPE_CHECKING:
    from flask_socketio import SocketIO


QUEUED = 'queued'
RUNNING = 'running'
AWAITING_APPROVAL = 'awaiting_approval'
UPLOADING = 'uploading'
COMPLETED = 'completed'
FAILED = 'failed'
CANCELLED = 'cancelled'
EXPIRED = 'expired'

TERMINAL_STATES = {COMPLETED, FAILED, CANCELLED, EXPIRED}

VALID_TRANSITIONS = {
    QUEUED: {RUNNING, CANCELLED},
    RUNNING: {AWAITING_APPROVAL, COMPLETED, FAILED, CANCELLED},
    AWAITING_APPROVAL: {UPLOADING, CANCELLED, EXPIRED},
    UPLOADING: {COMPLETED, FAILED, CANCELLED},
}


def resolve_max_concurrent() -> int:
    env_val = os.environ.get('MAX_CONCURRENT_JOBS')
    if env_val:
        try:
            return max(1, min(16, int(env_val)))
        except ValueError:
            pass
    config.reload()
    return config.MAX_CONCURRENT_JOBS


class InvalidTransition(Exception):
    pass


class JobManager:
    """FIFO (priority-aware) job queue with a fixed worker pool and an
    explicit, DB-authoritative state machine."""

    LEASE_MINUTES = 10
    HEARTBEAT_INTERVAL_SECONDS = 30
    SWEEPER_INTERVAL_SECONDS = 30

    def __init__(self, socketio):
        self.socketio = socketio
        self.max_concurrent = resolve_max_concurrent()
        self.active_jobs: Dict[str, dict] = {}
        self.cancellation_flags: Dict[str, threading.Event] = {}
        self._work_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._process_func: Optional[Callable] = None
        self._resume_func: Optional[Callable] = None
        self._restore_pending = True

        for _ in range(self.max_concurrent):
            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self._workers.append(worker)

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._sweeper_loop, daemon=True).start()

    # ===== wiring =====

    def set_process_func(self, func: Callable) -> None:
        self._process_func = func
        if self._restore_pending:
            self._restore_pending = False
            self._restore_active_jobs()

    def set_resume_func(self, func: Callable) -> None:
        """Function called as func(job_id, jm) for the upload-resume phase."""
        self._resume_func = func

    def refresh_concurrency(self) -> None:
        """Update max concurrent from config/env (applies to new worker spawns only)."""
        self.max_concurrent = resolve_max_concurrent()

    def _rooms(self, job_id: str) -> list[str]:
        """Target rooms for job events: the job room plus its owner's room."""
        rooms = [f'job_{job_id}']
        job = get_job(job_id)
        if job and job.get('user_id'):
            rooms.append(f"user_{job['user_id']}")
        return rooms

    def _emit_to_rooms(self, event: str, payload: dict, job_id: str) -> None:
        for room in self._rooms(job_id):
            self.socketio.emit(event, payload, room=room)

    # ===== state machine =====

    def transition(
        self,
        job_id: str,
        new_state: str,
        *,
        expected_old: Optional[str] = None,
        reason: Optional[str] = None,
        percent: Optional[int] = None,
        stage_message: Optional[str] = None,
        force: bool = False,
        claim_only_if_ready: bool = False,
        with_lease_minutes: Optional[int] = None,
    ) -> bool:
        """Move a job to new_state; the single sanctioned status writer.

        expected_old turns the update into a compare-and-set claim (returns
        False when another actor won the race). force=True bypasses the
        transition table for crash-recovery/admin paths only.
        """
        job = get_job(job_id)
        if not job:
            return False
        old_state = job['status']

        if expected_old is not None and old_state != expected_old:
            # Lost a CAS race or state moved on: not an error.
            if not force:
                return False

        if not force:
            if old_state not in VALID_TRANSITIONS or \
                    new_state not in VALID_TRANSITIONS[old_state]:
                raise InvalidTransition(
                    f'{old_state!r} -> {new_state!r} for job {job_id}'
                )

        assignments = [
            'status = ?',
            "state_changed_at = CURRENT_TIMESTAMP",
            "updated_at = CURRENT_TIMESTAMP",
        ]
        params: list = [new_state]
        if percent is not None:
            assignments.append('progress = ?')
            params.append(percent)
        if stage_message is not None:
            assignments.append('stage_message = ?')
            params.append(stage_message)
        if reason is not None:
            assignments.append('error_message = ?')
            params.append(reason)
        if with_lease_minutes is not None:
            assignments.append("lease_expires_at = datetime('now', ?)")
            params.append(f'+{int(with_lease_minutes)} minutes')
        params.append(job_id)

        guard = ''
        if expected_old is not None and not force:
            guard = ' AND status = ?'
            params.append(expected_old)
            if claim_only_if_ready:
                guard += (" AND (next_run_at IS NULL "
                          "OR next_run_at <= datetime('now'))")

        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f'UPDATE recipe_jobs SET {", ".join(assignments)} '
                f'WHERE id = ?{guard}',
                params,
            )
            conn.commit()
            claimed = cursor.rowcount > 0

        if claimed:
            with self._lock:
                entry = self.active_jobs.get(job_id)
                if entry:
                    entry['status'] = new_state
                    if percent is not None:
                        entry['progress'] = percent
            payload = {
                'job_id': job_id,
                'status': new_state,
                'previous_status': old_state,
                'reason': reason,
            }
            self._emit_to_rooms(f'job_{new_state}', payload, job_id)
        return claimed

    # ===== worker pool =====

    def _worker_loop(self) -> None:
        while True:
            job_id, phase = self._work_queue.get()
            try:
                if self.is_cancelled(job_id):
                    continue
                if phase == 'extract':
                    if not self.transition(
                        job_id, RUNNING, expected_old=QUEUED,
                        percent=1, stage_message='Starting...',
                        claim_only_if_ready=True,
                        with_lease_minutes=self.LEASE_MINUTES,
                    ):
                        continue
                    self.update_progress(
                        job_id, 'pending', 'Starting...', 1
                    )
                    if self._process_func:
                        self._process_func(job_id, self)
                elif phase == 'upload':
                    if not self.transition(
                        job_id, UPLOADING, expected_old=AWAITING_APPROVAL,
                        stage_message='Uploading approved recipe...',
                        with_lease_minutes=self.LEASE_MINUTES,
                    ):
                        continue
                    if self._resume_func:
                        self._resume_func(job_id, self)
            except Exception as exc:
                self.fail_job(job_id, f'Worker error: {exc}')
            finally:
                self._work_queue.task_done()
                self._cleanup_job(job_id)
                self._broadcast_queue_positions()

    def _restore_active_jobs(self) -> None:
        """Crash recovery on boot. Approval-parked jobs survive restarts."""
        try:
            active = get_active_jobs()
            for job in active:
                job_id = job['id']
                status = job['status']
                if status == AWAITING_APPROVAL:
                    # Artifact lives in pending_uploads; owner re-emits
                    # previews at startup. Nothing to redo here.
                    continue
                if status == QUEUED:
                    with self._lock:
                        self.active_jobs[job_id] = {
                            'url': job['url'],
                            'status': QUEUED,
                            'progress': job.get('progress', 0),
                        }
                        self.cancellation_flags[job_id] = threading.Event()
                    self._work_queue.put((job_id, 'extract'))
                else:
                    # Legacy mid-flight statuses cannot be resumed safely yet.
                    db_fail_job(job_id, 'Server was restarted during processing. Please retry.')
        except Exception as exc:
            print(f"Error restoring active jobs: {exc}")

    # ===== submission =====

    def create_new_job(
        self,
        url: str,
        *,
        retry_from_history_id: int | None = None,
        priority: int = 0,
        user_id: str | None = None,
    ) -> str:
        job_id = db_create_job(
            url, retry_from_history_id=retry_from_history_id,
            priority=priority, user_id=user_id,
        )
        with self._lock:
            self.active_jobs[job_id] = {
                'url': url,
                'status': QUEUED,
                'progress': 0,
                'user_id': user_id,
            }
            self.cancellation_flags[job_id] = threading.Event()
        return job_id

    def start_job(self, job_id: str, process_func: Callable,
                  phase: str = 'extract') -> None:
        self.set_process_func(process_func)
        position = get_queue_position(job_id)
        msg = f'Queued — position {position}' if position > 1 else 'Queued — starting soon...'
        self.update_progress(job_id, 'queued', msg, 0)
        self._work_queue.put((job_id, phase))
        self._broadcast_queue_positions()

    def enqueue_upload_resume(self, job_id: str) -> bool:
        """Re-enqueue an approved job for its upload phase."""
        self._work_queue.put((job_id, 'upload'))
        return True

    # ===== cancellation =====

    def is_cancelled(self, job_id: str) -> bool:
        flag = self.cancellation_flags.get(job_id)
        return flag.is_set() if flag else False

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self.cancellation_flags:
                self.cancellation_flags[job_id].set()
        job = get_job(job_id)
        if not job:
            return False
        if job['status'] not in TERMINAL_STATES:
            try:
                self.transition(job_id, CANCELLED, force=True,
                                reason='Cancelled by user')
            except Exception:
                db_cancel_job(job_id)
        # An approval parked on this job dies with it.
        cancel_pending_upload_for_job(job_id)
        self._emit_to_rooms('job_cancelled', {'job_id': job_id}, job_id)
        self._broadcast_queue_positions()
        return True

    # ===== progress =====

    def update_progress(
        self,
        job_id: str,
        stage: str,
        message: str,
        percent: int,
        video_title: Optional[str] = None,
    ) -> None:
        if self.is_cancelled(job_id):
            return

        status_map = {
            'queued': QUEUED,
            'pending': RUNNING,
            'info': 'downloading',
            'download': 'downloading',
            'transcribe': 'transcribing',
            'visual': 'extracting',
            'image': 'extracting',
            'evaluate': 'creating',
            'preview': AWAITING_APPROVAL,
            'upload': UPLOADING,
            'complete': COMPLETED,
            'error': FAILED,
            'cancelled': CANCELLED,
        }
        status = status_map.get(stage, 'processing')

        current = get_job(job_id)
        if current and status != current['status']:
            if current['status'] in TERMINAL_STATES:
                return
            if current['status'] in VALID_TRANSITIONS and \
                    status in VALID_TRANSITIONS[current['status']]:
                self.transition(job_id, status, percent=percent,
                                stage_message=message)
            else:
                # Progress metadata for sub-states (downloading, transcribing,
                # ...) rides inside the RUNNING row without changing state.
                update_job_progress(job_id, current['status'], percent,
                                    stage, message, video_title)
        else:
            update_job_progress(job_id, status, percent, stage, message,
                                video_title)

        with self._lock:
            if job_id in self.active_jobs:
                self.active_jobs[job_id]['status'] = status
                self.active_jobs[job_id]['progress'] = percent
                if video_title:
                    self.active_jobs[job_id]['video_title'] = video_title

        payload = {
            'job_id': job_id,
            'stage': stage,
            'message': message,
            'percent': percent,
            'video_title': video_title,
            'queue_position': get_queue_position(job_id) if status == QUEUED else 0,
        }
        self._emit_to_rooms('job_progress', payload, job_id)

    # ===== completion =====

    def complete_job(
        self,
        job_id: str,
        recipe_data: dict,
        image_path: Optional[str],
        output_target: str,
        llm_tokens: int = 0,
    ) -> None:
        job = get_job(job_id)
        if not job:
            return

        if llm_tokens:
            update_job_tokens(job_id, llm_tokens)

        thumbnail_data = None
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as f:
                    thumbnail_data = base64.b64encode(f.read()).decode('utf-8')
            except Exception:
                pass

        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=recipe_data.get('name'),
            recipe_data=recipe_data,
            thumbnail_path=image_path,
            thumbnail_data=thumbnail_data,
            status='success',
            output_target=output_target,
        )
        db_complete_job(job_id)
        with get_db() as conn:
            conn.execute(
                "UPDATE recipe_jobs SET state_changed_at = CURRENT_TIMESTAMP "
                'WHERE id = ?', (job_id,)
            )
            conn.commit()

        payload = {
            'job_id': job_id,
            'recipe': recipe_data,
            'llm_tokens_used': llm_tokens,
        }
        self._emit_to_rooms('job_complete', payload, job_id)

    def fail_job(self, job_id: str, error_message: str, llm_tokens: int = 0) -> None:
        job = get_job(job_id)
        if not job:
            return

        if llm_tokens:
            update_job_tokens(job_id, llm_tokens)

        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=None,
            recipe_data=None,
            thumbnail_path=None,
            thumbnail_data=None,
            status='failed',
            error_message=error_message,
        )
        if job['status'] not in TERMINAL_STATES:
            db_fail_job(job_id, error_message)
            with get_db() as conn:
                conn.execute(
                    "UPDATE recipe_jobs SET state_changed_at = CURRENT_TIMESTAMP "
                    'WHERE id = ?', (job_id,)
                )
                conn.commit()

        payload = {'job_id': job_id, 'error': error_message}
        self._emit_to_rooms('job_failed', payload, job_id)

    # ===== slot-free approvals =====

    def open_approval(
        self,
        job_id: str,
        recipe_data: dict,
        image_path: Optional[str],
        image_candidates: list,
        output_target: str,
        best_image_index: int = 0,
        timeout_minutes: int = 5,
    ) -> Optional[str]:
        """Park a finished-extraction job in awaiting_approval.

        Persists the artifact (images relocated under DATA_DIR/artifacts),
        records the approval request, flips state, and returns immediately —
        the caller (worker thread) is free to pick up other work.
        """
        upload_id = secrets.token_hex(16)
        job = get_job(job_id)
        user_id = job.get('user_id') if job else None

        final_image, final_candidates = _relocate_artifacts(
            job_id, image_path, image_candidates
        )

        create_pending_upload(
            upload_id=upload_id,
            job_id=job_id,
            recipe_data=recipe_data,
            image_path=final_image,
            image_candidates=final_candidates,
            output_target=output_target,
            best_image_index=best_image_index,
            timeout_minutes=timeout_minutes,
            user_id=user_id,
        )

        try:
            self.transition(
                job_id, AWAITING_APPROVAL, percent=90,
                stage_message='Waiting for your confirmation...',
            )
        except InvalidTransition:
            # Job moved on concurrently (e.g. cancelled mid-flight): drop
            # the orphaned request rather than fighting the state machine.
            from database import delete_pending_upload
            delete_pending_upload(upload_id)
            return None
        return upload_id

    def confirm_approval(self, upload_id: str,
                         selected_image_index: Optional[int] = None,
                         recipe: Optional[dict] = None) -> dict:
        """Approve a pending upload and schedule its upload phase."""
        upload = get_pending_upload(upload_id)
        if not upload or upload['status'] != 'pending':
            return {'ok': False, 'error': 'not found or already processed'}

        if recipe is not None:
            from chef import RecipeEditError, apply_confirmed_recipe
            from database import update_pending_upload_recipe
            try:
                merged = apply_confirmed_recipe(upload['recipe_data'], recipe)
            except RecipeEditError as exc:
                return {'ok': False, 'error': str(exc), 'code': 'invalid_recipe'}
            if not update_pending_upload_recipe(upload_id, merged):
                return {'ok': False, 'error': 'not found or already processed'}

        from database import confirm_pending_upload as db_confirm
        if not db_confirm(upload_id, selected_image_index):
            return {'ok': False, 'error': 'already processed'}

        job_id = upload['job_id']
        self.enqueue_upload_resume(job_id)
        self.socketio.emit(
            'approval_confirmed',
            {'upload_id': upload_id, 'job_id': job_id},
            room=f'job_{job_id}',
        )
        self._emit_to_rooms('approvals_updated', {'job_id': job_id}, job_id)
        return {'ok': True, 'job_id': job_id}

    def reject_approval(self, upload_id: str) -> dict:
        """Reject a pending upload; its job becomes cancelled."""
        upload = get_pending_upload(upload_id)
        if not upload or upload['status'] != 'pending':
            return {'ok': False, 'error': 'not found or already processed'}
        if not cancel_pending_upload(upload_id):
            return {'ok': False, 'error': 'already processed'}

        job_id = upload['job_id']
        with self._lock:
            if job_id in self.cancellation_flags:
                self.cancellation_flags[job_id].set()
        job = get_job(job_id)
        if job and job['status'] not in TERMINAL_STATES:
            self.transition(job_id, CANCELLED, force=True,
                            reason='Recipe rejected by user')
        self.socketio.emit(
            'approval_rejected',
            {'upload_id': upload_id, 'job_id': job_id},
            room=f'job_{job_id}',
        )
        self._emit_to_rooms('approvals_updated', {'job_id': job_id}, job_id)
        return {'ok': True, 'job_id': job_id}

    def refresh_queue_positions(self) -> None:
        """Re-broadcast queue positions (e.g. after a priority change)."""
        self._broadcast_queue_positions()

    def get_approvals(self, *, user_id: Optional[str] = None,
                      is_admin: bool = False) -> list:
        return get_pending_uploads(user_id=user_id, is_admin=is_admin)

    # ===== introspection =====

    def get_all_active_jobs(self, *, user_id: Optional[str] = None,
                            is_admin: bool = False) -> list:
        return get_active_jobs(user_id=user_id, is_admin=is_admin)

    def get_job_status(self, job_id: str, *, user_id: Optional[str] = None,
                       is_admin: bool = False) -> Optional[dict]:
        job = get_job(job_id, user_id=user_id, is_admin=is_admin)
        if job and job.get('status') == QUEUED:
            job = dict(job)
            job['queue_position'] = get_queue_position(job_id)
        return job

    def get_queue_stats(self) -> dict:
        queued = get_queued_jobs()
        running = sum(
            1 for j in self.active_jobs.values()
            if j.get('status') not in (QUEUED, *TERMINAL_STATES)
        )
        return {
            'max_concurrent': self.max_concurrent,
            'queued_count': len(queued),
            'active_count': len(get_active_jobs()),
            'running_count': running,
        }

    def _broadcast_queue_positions(self) -> None:
        for job in get_queued_jobs():
            pos = get_queue_position(job['id'])
            self.update_progress(
                job['id'], 'queued', f'Queued — position {pos}', 0,
                video_title=job.get('video_title'),
            )

    def _cleanup_job(self, job_id: str) -> None:
        with self._lock:
            self.cancellation_flags.pop(job_id, None)

    # ===== resilience (heartbeat + sweeper) =====

    def _heartbeat_loop(self) -> None:
        """Keep leases fresh for every claimed-but-unfinished job."""
        while True:
            time.sleep(self.HEARTBEAT_INTERVAL_SECONDS)
            try:
                with self._lock:
                    live = [
                        jid for jid, info in self.active_jobs.items()
                        if info.get('status') not in (QUEUED, *TERMINAL_STATES)
                    ]
                extend_leases(live, minutes=self.LEASE_MINUTES)
            except Exception as exc:
                print(f'[Heartbeat] error: {exc}')

    def _sweeper_loop(self) -> None:
        while True:
            time.sleep(self.SWEEPER_INTERVAL_SECONDS)
            try:
                self.sweep_once()
            except Exception as exc:
                print(f'[Sweeper] error: {exc}')

    def sweep_once(self) -> dict:
        """One recovery pass: stale worker leases + due approvals."""
        result = sweep_stale_leases()
        expired_job_ids = set(expire_due_approvals())
        # Reconcile jobs whose approval row resolved outside this pass
        # (e.g. a crash between the sweeper's two writes).
        expired_job_ids.update(find_stranded_approvals())
        for job_id in sorted(expired_job_ids):
            if get_job(job_id):
                try:
                    self.transition(job_id, EXPIRED,
                                    reason='Approval window elapsed')
                except InvalidTransition:
                    pass
        if result.get('requeued'):
            self._broadcast_queue_positions()
        return {**result, 'expired': len(expired_job_ids)}


def _relocate_artifacts(job_id: str, image_path: Optional[str],
                        image_candidates: list) -> tuple[Optional[str], list]:
    """Copy approval-time images into DATA_DIR/artifacts/<job_id>/ so /tmp
    cleanup cannot orphan a parked approval."""
    artifacts_dir = os.path.join(DATA_DIR, 'artifacts', job_id)
    final_candidates: list = []
    try:
        os.makedirs(artifacts_dir, exist_ok=True)

        def _copy(src: str) -> str:
            dest = os.path.join(artifacts_dir, os.path.basename(src))
            if os.path.exists(src) and not os.path.exists(dest):
                shutil.copy2(src, dest)
            return dest if os.path.exists(dest) else src

        if image_path:
            image_path = _copy(image_path)
        final_candidates = [_copy(p) for p in (image_candidates or [])]
    except OSError as exc:
        print(f'Artifact relocation failed for {job_id}: {exc}')
    return image_path, final_candidates


def prune_artifact_dirs() -> int:
    """Delete approval-artifact dirs whose owning job row is gone."""
    artifacts_root = os.path.join(DATA_DIR, 'artifacts')
    if not os.path.isdir(artifacts_root):
        return 0
    pruned = 0
    for entry in os.listdir(artifacts_root):
        path = os.path.join(artifacts_root, entry)
        if not os.path.isdir(path):
            continue
        if get_job(entry) is None:
            shutil.rmtree(path, ignore_errors=True)
            pruned += 1
    return pruned


def cancel_pending_upload_for_job(job_id: str) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE pending_uploads SET status = 'cancelled' "
            "WHERE job_id = ? AND status = 'pending'",
            (job_id,),
        )
        conn.commit()
        return cursor.rowcount


job_manager: Optional[JobManager] = None


def init_job_manager(socketio) -> JobManager:
    global job_manager
    job_manager = JobManager(socketio)
    return job_manager


def get_job_manager() -> Optional[JobManager]:
    return job_manager
