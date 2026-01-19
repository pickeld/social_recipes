"""
Job Manager for Social Recipes
Manages concurrent recipe analysis jobs with progress tracking and persistence.
"""

import os
import sys
import base64
import threading
from typing import Dict, Optional, Callable, Any, TYPE_CHECKING
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import (
    create_job as db_create_job, get_job, get_active_jobs, update_job_progress,
    fail_job as db_fail_job, cancel_job as db_cancel_job, complete_job as db_complete_job,
    create_history_entry
)

if TYPE_CHECKING:
    from flask_socketio import SocketIO


class JobManager:
    """
    Manages concurrent recipe analysis jobs.
    
    Features:
    - Tracks multiple active jobs
    - Limits concurrent processing via semaphore
    - Persists job state to database
    - Emits progress via WebSocket
    """
    
    MAX_CONCURRENT_JOBS = 3
    
    def __init__(self, socketio):
        """
        Initialize the JobManager.
        
        Args:
            socketio: Flask-SocketIO instance for emitting progress
        """
        self.socketio = socketio
        self.active_jobs: Dict[str, dict] = {}  # job_id -> job metadata
        self.job_threads: Dict[str, threading.Thread] = {}
        self.cancellation_flags: Dict[str, threading.Event] = {}
        self.semaphore = threading.Semaphore(self.MAX_CONCURRENT_JOBS)
        self._lock = threading.Lock()
        
        # Restore any active jobs from database on startup
        self._restore_active_jobs()
    
    def _restore_active_jobs(self):
        """Restore active jobs from database on startup."""
        try:
            active = get_active_jobs()
            for job in active:
                job_id = job['id']
                # Mark stale running jobs as failed (server was restarted)
                if job['status'] in ('downloading', 'transcribing', 'extracting', 'creating', 'uploading'):
                    db_fail_job(job_id, 'Server was restarted during processing. Please retry.')
                else:
                    self.active_jobs[job_id] = {
                        'url': job['url'],
                        'status': job['status'],
                        'progress': job['progress']
                    }
        except Exception as e:
            print(f"Error restoring active jobs: {e}")
    
    def create_new_job(self, url: str) -> str:
        """
        Create a new analysis job.
        
        Args:
            url: Video URL to analyze
            
        Returns:
            job_id: Unique identifier for the job
        """
        job_id = db_create_job(url)
        
        with self._lock:
            self.active_jobs[job_id] = {
                'url': url,
                'status': 'pending',
                'progress': 0
            }
            self.cancellation_flags[job_id] = threading.Event()
        
        return job_id
    
    def start_job(self, job_id: str, process_func: Callable):
        """
        Start processing a job.
        
        Args:
            job_id: Job identifier
            process_func: Function to call for processing (receives job_id and JobManager)
        """
        def wrapped_process():
            # Acquire semaphore to limit concurrent jobs
            self.semaphore.acquire()
            try:
                if self.is_cancelled(job_id):
                    return
                process_func(job_id, self)
            finally:
                self.semaphore.release()
                self._cleanup_job(job_id)
        
        thread = threading.Thread(target=wrapped_process, daemon=True)
        with self._lock:
            self.job_threads[job_id] = thread
        thread.start()
    
    def is_cancelled(self, job_id: str) -> bool:
        """Check if a job has been cancelled."""
        flag = self.cancellation_flags.get(job_id)
        return flag.is_set() if flag else False
    
    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a running job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            True if job was cancelled, False if not found
        """
        with self._lock:
            if job_id in self.cancellation_flags:
                self.cancellation_flags[job_id].set()
        
        # Update database
        result = db_cancel_job(job_id)
        
        if result:
            # Emit cancellation event
            self.socketio.emit('job_cancelled', {'job_id': job_id}, room=f'job_{job_id}')
            self.socketio.emit('job_cancelled', {'job_id': job_id})  # Broadcast too
        
        return result
    
    def update_progress(self, job_id: str, stage: str, message: str, 
                        percent: int, video_title: Optional[str] = None):
        """
        Update job progress and emit via WebSocket.
        
        Args:
            job_id: Job identifier
            stage: Current processing stage
            message: Status message
            percent: Progress percentage (0-100)
            video_title: Optional video title to store
        """
        # Check for cancellation
        if self.is_cancelled(job_id):
            return
        
        # Determine status from stage
        status_map = {
            'pending': 'pending',
            'info': 'downloading',
            'download': 'downloading',
            'transcribe': 'transcribing',
            'visual': 'extracting',
            'image': 'extracting',
            'evaluate': 'creating',
            'preview': 'awaiting_confirmation',
            'upload': 'uploading',
            'complete': 'completed',
            'error': 'failed',
            'cancelled': 'cancelled'
        }
        status = status_map.get(stage, 'processing')
        
        # Update database
        update_job_progress(job_id, status, percent, stage, message, video_title)
        
        # Update local cache
        with self._lock:
            if job_id in self.active_jobs:
                self.active_jobs[job_id]['status'] = status
                self.active_jobs[job_id]['progress'] = percent
                if video_title:
                    self.active_jobs[job_id]['video_title'] = video_title
        
        # Emit to job-specific room
        self.socketio.emit('job_progress', {
            'job_id': job_id,
            'stage': stage,
            'message': message,
            'percent': percent,
            'video_title': video_title
        }, room=f'job_{job_id}')
        
        # Also broadcast to all connected clients (for the job list)
        self.socketio.emit('job_progress', {
            'job_id': job_id,
            'stage': stage,
            'message': message,
            'percent': percent,
            'video_title': video_title
        })
    
    def complete_job(self, job_id: str, recipe_data: dict, image_path: Optional[str],
                     output_target: str):
        """
        Mark a job as completed and save to history.
        
        Args:
            job_id: Job identifier
            recipe_data: The extracted recipe data
            image_path: Path to the dish image
            output_target: Where it was uploaded (tandoor/mealie)
        """
        job = get_job(job_id)
        if not job:
            return
        
        # Encode thumbnail for storage
        thumbnail_data = None
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as f:
                    thumbnail_data = base64.b64encode(f.read()).decode('utf-8')
            except Exception:
                pass
        
        # Create history entry
        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=recipe_data.get('name'),
            recipe_data=recipe_data,
            thumbnail_path=image_path,
            thumbnail_data=thumbnail_data,
            status='success',
            output_target=output_target
        )
        
        # Mark job as completed
        db_complete_job(job_id)
        
        # Emit completion event
        self.socketio.emit('job_complete', {
            'job_id': job_id,
            'recipe': recipe_data
        }, room=f'job_{job_id}')
        self.socketio.emit('job_complete', {
            'job_id': job_id,
            'recipe': recipe_data
        })
    
    def fail_job(self, job_id: str, error_message: str):
        """
        Mark a job as failed and save to history.
        
        Args:
            job_id: Job identifier
            error_message: Description of the error
        """
        job = get_job(job_id)
        if not job:
            return
        
        # Create history entry for failed job
        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=None,
            recipe_data=None,
            thumbnail_path=None,
            thumbnail_data=None,
            status='failed',
            error_message=error_message
        )
        
        # Mark job as failed
        db_fail_job(job_id, error_message)
        
        # Emit failure event
        self.socketio.emit('job_failed', {
            'job_id': job_id,
            'error': error_message
        }, room=f'job_{job_id}')
        self.socketio.emit('job_failed', {
            'job_id': job_id,
            'error': error_message
        })
    
    def get_all_active_jobs(self) -> list:
        """Get all active jobs from database."""
        return get_active_jobs()
    
    def get_job_status(self, job_id: str) -> Optional[dict]:
        """Get current status of a job."""
        return get_job(job_id)
    
    def _cleanup_job(self, job_id: str):
        """Clean up job resources after completion."""
        with self._lock:
            self.job_threads.pop(job_id, None)
            self.cancellation_flags.pop(job_id, None)
            # Keep in active_jobs for a bit so clients can see final state
            # It will be cleaned up by periodic cleanup or on next restore


# Singleton instance - will be initialized by app.py
job_manager: Optional[JobManager] = None


def init_job_manager(socketio) -> JobManager:
    """Initialize the global job manager instance."""
    global job_manager
    job_manager = JobManager(socketio)
    return job_manager


def get_job_manager() -> Optional[JobManager]:
    """Get the global job manager instance."""
    return job_manager
