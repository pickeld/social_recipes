"""
Shared extraction pipeline used by the web UI and CLI.

Supports two source types:
  - video  : download via yt-dlp → Whisper transcription → Chef
  - webpage: HTTP fetch → Schema.org / HTML extraction → Chef
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from config import config
from image_extractor import extract_dish_image_candidates
from transcriber import Transcriber
from uploaders import (
    format_targets,
    get_enabled_targets,
    upload_recipe_to_targets,
)
from url_safety import UnsafeURLError, assert_public_http_url
from video_downloader import VideoDownloader
from web_recipe_fetcher import is_video_url, fetch_web_recipe, download_image


class ProgressReporter(Protocol):
    def is_cancelled(self) -> bool: ...
    def update(self, stage: str, message: str, percent: int, video_title: str | None = None) -> None: ...


@dataclass
class PipelineResult:
    recipe_data: dict | None = None
    image_path: str | None = None
    output_target: str = ""
    llm_tokens_estimate: int = 0
    error: str | None = None
    awaiting_approval: bool = False


@dataclass
class PipelineStats:
    llm_tokens_estimate: int = 0

    def add_text(self, text: str) -> None:
        # Rough token estimate (~4 chars per token) for cost tracking
        self.llm_tokens_estimate += max(1, len(text) // 4)


@dataclass
class PreviewWaiter:
    """Slot-free approval handoff: persists the artifact via open_approval_fn
    and emits the preview; the worker thread returns immediately afterwards."""

    job_id: str
    target_label: str
    emit_preview: Callable[[dict], None]
    open_approval_fn: Callable[..., Optional[str]]
    is_cancelled: Callable[[], bool]


def _no_target_result(recipe_data, image_path, stats) -> PipelineResult:
    """Result for the 'nothing enabled in Settings' failure case."""
    return PipelineResult(
        error='No recipe manager is enabled — enable Mealie and/or Tandoor '
              'in Settings',
        recipe_data=recipe_data,
        image_path=image_path,
        llm_tokens_estimate=stats.llm_tokens_estimate,
    )


def run_extraction_pipeline(
    url: str,
    reporter: ProgressReporter,
    *,
    work_dir: str = "/tmp",
    stats: PipelineStats | None = None,
    preview: PreviewWaiter | None = None,
    skip_upload: bool = False,
) -> PipelineResult:
    """Run the full download → transcribe → recipe pipeline."""
    stats = stats or PipelineStats()

    try:
        assert_public_http_url(url)
        config.reload()

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("info", "Fetching video information...", 10)
        downloader = VideoDownloader(url)
        item = downloader._get_info()
        description = item.get("description", "No description available.")
        title = item.get("title", "Untitled")
        reporter.update("info", f"Video: {title}", 15, video_title=title)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("download", "Downloading video...", 20)
        vid_id, video_path = downloader._download_video()
        if vid_id is None:
            return PipelineResult(error="Failed to download video")

        dish_dir = os.path.join(work_dir, vid_id)
        reporter.update("download", "Video downloaded successfully", 30)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("transcribe", "Transcribing audio...", 35)
        transcriber = Transcriber(video_path)
        lang = config.TARGET_LANGUAGE
        audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")

        if os.path.exists(audio_cache):
            reporter.update("transcribe", "Using cached transcription", 40)
            with open(audio_cache, "r", encoding="utf-8") as f:
                transcription = f.read()
        else:
            transcription = transcriber.transcribe()
            with open(audio_cache, "w", encoding="utf-8") as f:
                f.write(transcription)
        stats.add_text(transcription)
        reporter.update("transcribe", "Audio transcribed", 50)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("visual", "Extracting on-screen text...", 55)
        visual_text = ""
        visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
        if os.path.exists(visual_cache):
            reporter.update("visual", "Using cached visual text", 60)
            with open(visual_cache, "r", encoding="utf-8") as f:
                visual_text = f.read()
        else:
            try:
                visual_text = transcriber.extract_visual_text()
                with open(visual_cache, "w", encoding="utf-8") as f:
                    f.write(visual_text)
                stats.add_text(visual_text)
            except Exception as exc:
                reporter.update("visual", f"Warning: Could not extract visual text: {exc}", 60)
        reporter.update("visual", "Visual text extracted", 65)

        combined_transcription = transcription
        if visual_text:
            combined_transcription = (
                f"=== AUDIO TRANSCRIPTION ===\n{transcription}\n\n"
                f"=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===\n{visual_text}"
            )

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("image", "Extracting dish image candidates...", 70)
        image_path = None
        image_candidates: list[str] = []
        best_image_index = 0
        image_cache = os.path.join(dish_dir, "dish.jpg")
        frames_dir = os.path.join(dish_dir, "dish_frames")

        if os.path.exists(frames_dir) and os.path.exists(image_cache):
            reporter.update("image", "Using cached dish images", 75)
            image_path = image_cache
            image_candidates = sorted(
                os.path.join(frames_dir, f)
                for f in os.listdir(frames_dir)
                if f.startswith("dish_candidate_") and f.endswith(".jpg")
            )
        else:
            try:
                result = extract_dish_image_candidates(video_path)
                image_path = result.get("best_image")
                image_candidates = result.get("candidates", [])
                best_image_index = result.get("best_index", 0)
            except Exception as exc:
                reporter.update("image", f"Warning: Could not extract image: {exc}", 75)
        reporter.update("image", "Image candidates extracted", 80)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("evaluate", "Creating recipe with AI...", 85)
        from chef import Chef

        chef = Chef(source_url=url, description=description, transcription=combined_transcription)
        stats.add_text(combined_transcription)
        recipe_data = chef.create_recipe()
        if not recipe_data:
            return PipelineResult(error="Failed to create recipe", llm_tokens_estimate=stats.llm_tokens_estimate)

        reporter.update("evaluate", "Recipe created successfully", 90)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        if config.CONFIRM_BEFORE_UPLOAD and preview is not None:
            _open_preview_approval(preview, recipe_data, image_path,
                                   image_candidates, best_image_index, reporter)
            return PipelineResult(
                awaiting_approval=True,
                recipe_data=recipe_data,
                image_path=image_path,
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        upload_targets = get_enabled_targets()
        if not upload_targets:
            return _no_target_result(recipe_data, image_path, stats)

        reporter.update('upload',
                        f'Uploading to {format_targets(upload_targets)}...', 95)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled", recipe_data=recipe_data, image_path=image_path,
                                  llm_tokens_estimate=stats.llm_tokens_estimate)

        if skip_upload:
            reporter.update("complete", "Recipe created (upload skipped)", 100)
            return PipelineResult(
                recipe_data=recipe_data,
                image_path=image_path,
                output_target="none",
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        final_target, failures = upload_recipe_to_targets(recipe_data, image_path)
        if failures and len(failures) == len(upload_targets):
            msgs = "; ".join(f"{t}: {msg}" for t, msg in failures)
            return PipelineResult(error=f"All uploads failed: {msgs}", recipe_data=recipe_data,
                                  image_path=image_path, llm_tokens_estimate=stats.llm_tokens_estimate)

        if failures:
            failed_msgs = "; ".join(f"{t}: {msg}" for t, msg in failures)
            reporter.update(
                "complete",
                f"Uploaded to {final_target}. Failed: {failed_msgs}",
                100,
            )
        else:
            reporter.update("complete", f"Recipe uploaded successfully to {final_target}!", 100)
        return PipelineResult(
            recipe_data=recipe_data,
            image_path=image_path,
            output_target=final_target,
            llm_tokens_estimate=stats.llm_tokens_estimate,
        )

    except Exception as exc:
        return PipelineResult(error=f"Error: {exc}", llm_tokens_estimate=stats.llm_tokens_estimate)


def run_web_recipe_pipeline(
    url: str,
    reporter: ProgressReporter,
    *,
    work_dir: str = "/tmp",
    stats: PipelineStats | None = None,
    preview: PreviewWaiter | None = None,
    skip_upload: bool = False,
) -> PipelineResult:
    """Run the fetch → parse → normalise → upload pipeline for a recipe web page."""
    stats = stats or PipelineStats()

    try:
        assert_public_http_url(url)
        config.reload()

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("info", "Fetching recipe page...", 10)
        web_data = fetch_web_recipe(url)
        title = web_data["title"]
        reporter.update("info", f"Page: {title}", 20, video_title=title)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("visual", "Extracting recipe content...", 35)
        page_text = web_data.get("page_text", "")
        structured = web_data.get("structured")
        stats.add_text(page_text)
        reporter.update("visual", "Recipe content extracted", 50)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("image", "Downloading recipe image...", 60)
        image_path: str | None = None
        image_url = web_data.get("image_url")
        if image_url:
            import hashlib
            url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
            img_dir = os.path.join(work_dir, f"web_{url_hash}")
            image_path = download_image(image_url, img_dir)
        reporter.update("image", "Image ready" if image_path else "No image found", 65)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("evaluate", "Normalising recipe with AI...", 70)
        from chef import Chef

        chef = Chef(
            source_url=url,
            description=web_data.get("description", ""),
            transcription="",
        )
        recipe_data = chef.create_recipe_from_web_content(
            page_text=page_text,
            structured_data=structured,
            source_url=url,
        )
        if not recipe_data:
            return PipelineResult(
                error="Failed to normalise recipe",
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        reporter.update("evaluate", "Recipe normalised successfully", 85)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        # Reuse the same preview / upload logic from the video pipeline
        if config.CONFIRM_BEFORE_UPLOAD and preview is not None:
            _open_preview_approval(
                preview, recipe_data, image_path, [], 0, reporter
            )
            return PipelineResult(
                awaiting_approval=True,
                recipe_data=recipe_data,
                image_path=image_path,
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        upload_targets = get_enabled_targets()
        if not upload_targets:
            return _no_target_result(recipe_data, image_path, stats)

        reporter.update("upload",
                        f"Uploading to {format_targets(upload_targets)}...", 92)

        if reporter.is_cancelled():
            return PipelineResult(
                error="cancelled",
                recipe_data=recipe_data,
                image_path=image_path,
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        if skip_upload:
            reporter.update("complete", "Recipe created (upload skipped)", 100)
            return PipelineResult(
                recipe_data=recipe_data,
                image_path=image_path,
                output_target="none",
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        final_target, failures = upload_recipe_to_targets(recipe_data, image_path)
        if failures and len(failures) == len(upload_targets):
            msgs = "; ".join(f"{t}: {msg}" for t, msg in failures)
            return PipelineResult(
                error=f"All uploads failed: {msgs}",
                recipe_data=recipe_data,
                image_path=image_path,
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        if failures:
            failed_msgs = "; ".join(f"{t}: {msg}" for t, msg in failures)
            reporter.update(
                "complete",
                f"Uploaded to {final_target}. Failed: {failed_msgs}",
                100,
            )
        else:
            reporter.update(
                "complete", f"Recipe uploaded successfully to {final_target}!", 100
            )

        return PipelineResult(
            recipe_data=recipe_data,
            image_path=image_path,
            output_target=final_target,
            llm_tokens_estimate=stats.llm_tokens_estimate,
        )

    except Exception as exc:
        return PipelineResult(
            error=f"Error: {exc}", llm_tokens_estimate=stats.llm_tokens_estimate
        )


def run_url_pipeline(
    url: str,
    reporter: ProgressReporter,
    *,
    work_dir: str = "/tmp",
    stats: PipelineStats | None = None,
    preview: PreviewWaiter | None = None,
    skip_upload: bool = False,
) -> PipelineResult:
    """Auto-detect URL type and route to the appropriate pipeline."""
    try:
        assert_public_http_url(url)
    except UnsafeURLError as exc:
        return PipelineResult(error=f"Error: {exc}")
    if is_video_url(url):
        return run_extraction_pipeline(
            url,
            reporter,
            work_dir=work_dir,
            stats=stats,
            preview=preview,
            skip_upload=skip_upload,
        )
    return run_web_recipe_pipeline(
        url,
        reporter,
        work_dir=work_dir,
        stats=stats,
        preview=preview,
        skip_upload=skip_upload,
    )


def _open_preview_approval(
    preview: PreviewWaiter,
    recipe_data: dict,
    image_path: str | None,
    image_candidates: list,
    best_image_index: int,
    reporter: ProgressReporter,
) -> str:
    """Park the job for human approval and emit the preview payload.

    Non-blocking by design: the artifact is persisted (JobManager.open_approval)
    and the preview emitted; the worker thread returns right after. The
    upload itself happens later in a fresh 'upload'-phase worker once the
    user approves.
    """
    reporter.update("preview", "Waiting for your confirmation...", 90)

    image_data = None
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

    candidate_images_data = []
    for idx, candidate_path in enumerate(image_candidates):
        if os.path.exists(candidate_path):
            with open(candidate_path, "rb") as f:
                candidate_images_data.append({
                    "index": idx,
                    "data": base64.b64encode(f.read()).decode("utf-8"),
                    "path": candidate_path,
                    "is_best": idx == best_image_index,
                })

    display_target = preview.target_label

    upload_id = preview.open_approval_fn(
        job_id=preview.job_id,
        recipe_data=recipe_data,
        image_path=image_path,
        image_candidates=image_candidates,
        output_target=display_target,
        best_image_index=best_image_index,
    )

    preview.emit_preview({
        "job_id": preview.job_id,
        "upload_id": upload_id,
        "recipe": recipe_data,
        "image_data": image_data,
        "candidate_images": candidate_images_data,
        "best_image_index": best_image_index,
        "output_target": display_target,
    })
    return upload_id
