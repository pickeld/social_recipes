"""
Image extraction module for extracting the best dish image from cooking videos.
Uses LLM vision to analyze frames and select the most appealing shot of the finished dish.
"""

import os
import subprocess
import shutil
from llm_providers import get_image_selector
from helpers import setup_logger

logger = setup_logger(__name__)


class ImageExtractor:
    """
    Extract a high-quality image of the finished dish from a cooking video.
    Uses LLM vision to identify and select the best frame showing the final result.
    """

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.dish_dir = os.path.dirname(video_path) or "tmp"
        self.frames_dir = os.path.join(self.dish_dir, "dish_frames")

    def extract_best_image(self, num_candidates: int = 12) -> str | None:
        """
        Extract the best image of the finished dish from the video.
        
        Args:
            num_candidates: Number of frames to extract and analyze.
        
        Returns:
            Path to the best image file, or None if extraction fails.
        """
        logger.info(f"[Extract Image] Starting image extraction from video: {self.video_path}")
        
        # Focus on the last portion of the video where finished dish is more likely
        logger.info(f"[Extract Image] Extracting {num_candidates} candidate frames...")
        frames = self._extract_frames_weighted_end(num_candidates)
        if not frames:
            logger.warning("[Extract Image] No frames could be extracted from video")
            return None
        logger.info(f"[Extract Image] Extracted {len(frames)} candidate frames")

        # Use LLM to select the best frame
        try:
            logger.info(f"[Extract Image] Using LLM to select best frame from {len(frames)} candidates...")
            selector = get_image_selector()
            best_frame_idx = selector.select_best_frame(frames)
            logger.info(f"[Extract Image] LLM selected frame index: {best_frame_idx}")
        except Exception as e:
            logger.error(f"[Extract Image] LLM selection failed: {e}")
            best_frame_idx = None

        if best_frame_idx is None:
            # Fallback: use the last frame (most likely to show finished dish)
            best_frame_idx = len(frames) - 1
            logger.info(f"[Extract Image] Using fallback frame index: {best_frame_idx}")

        best_frame = frames[best_frame_idx]
        logger.debug(f"[Extract Image] Selected frame {best_frame_idx}: {best_frame}")
        
        # Copy to final output location with descriptive name
        output_path = os.path.join(self.dish_dir, "dish.jpg")
        
        # Create high-quality version of the selected frame
        logger.info("[Extract Image] Enhancing selected frame...")
        self._enhance_frame(best_frame, output_path)
        
        logger.info(f"[Extract Image] Best dish image saved to: {output_path}")
        return output_path

    def _extract_frames_weighted_end(self, num_frames: int = 12) -> list[str]:
        """
        Extract frames with more emphasis on the end of the video.
        Cooking videos typically show the finished dish near the end.
        """
        os.makedirs(self.frames_dir, exist_ok=True)

        # Get video duration
        duration = self._get_video_duration()

        # Extract more frames from the last third of the video
        # First 4 frames evenly from first 2/3, last 8 frames from final 1/3
        early_count = num_frames // 3
        late_count = num_frames - early_count
        
        timestamps = []
        
        # Early portion (first 2/3 of video)
        if early_count > 0:
            early_end = duration * 0.66
            early_interval = early_end / (early_count + 1)
            for i in range(early_count):
                timestamps.append(early_interval * (i + 1))
        
        # Late portion (last 1/3 of video) - more densely sampled
        late_start = duration * 0.66
        late_duration = duration - late_start - 0.5  # Leave small margin at end
        if late_count > 0 and late_duration > 0:
            late_interval = late_duration / (late_count + 1)
            for i in range(late_count):
                timestamps.append(late_start + late_interval * (i + 1))

        frame_paths = []
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(self.frames_dir, f"dish_candidate_{i:02d}.jpg")
            
            if not os.path.exists(frame_path):
                cmd = [
                    "ffmpeg", "-y", "-ss", str(ts),
                    "-i", self.video_path,
                    "-vframes", "1",
                    "-q:v", "2",  # High quality JPEG
                    frame_path
                ]
                try:
                    subprocess.run(cmd, capture_output=True, check=True)
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to extract frame at {ts}s: {e}")
                    continue

            if os.path.exists(frame_path):
                frame_paths.append(frame_path)

        return frame_paths

    def _get_video_duration(self) -> float:
        """Get video duration in seconds using ffprobe."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            self.video_path
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 30.0  # Default assumption

    def _enhance_frame(self, source_path: str, output_path: str):
        """
        Create an enhanced version of the selected frame.
        Applies mild sharpening and ensures good quality output.
        """
        cmd = [
            "ffmpeg", "-y",
            "-i", source_path,
            "-vf", "unsharp=5:5:0.5:5:5:0.5",  # Mild sharpening
            "-q:v", "1",  # Highest JPEG quality
            output_path
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError:
            # Fallback: just copy the file
            shutil.copy2(source_path, output_path)


def extract_dish_image(video_path: str) -> str | None:
    """
    Convenience function to extract the best dish image from a video.
    
    Args:
        video_path: Path to the video file.
    
    Returns:
        Path to the extracted image, or None if extraction fails.
    """
    extractor = ImageExtractor(video_path)
    return extractor.extract_best_image()


def extract_dish_image_candidates(video_path: str, num_candidates: int = 12) -> dict:
    """
    Extract dish image candidates from a video for user selection.
    
    Args:
        video_path: Path to the video file.
        num_candidates: Number of candidate frames to extract.
    
    Returns:
        Dictionary containing:
        - 'best_image': Path to the AI-selected best image
        - 'best_index': Index of the best image in candidates
        - 'candidates': List of paths to all candidate images
    """
    extractor = ImageExtractor(video_path)
    
    # Extract frames
    frames = extractor._extract_frames_weighted_end(num_candidates)
    if not frames:
        return {'best_image': None, 'best_index': 0, 'candidates': []}
    
    # Use LLM to select the best frame
    try:
        from llm_providers import get_image_selector
        selector = get_image_selector()
        best_frame_idx = selector.select_best_frame(frames)
    except Exception as e:
        logger.error(f"LLM selection failed: {e}")
        best_frame_idx = len(frames) - 1  # Fallback to last frame
    
    if best_frame_idx is None:
        best_frame_idx = len(frames) - 1
    
    # Create enhanced version of the best frame
    best_frame = frames[best_frame_idx]
    output_path = os.path.join(extractor.dish_dir, "dish.jpg")
    extractor._enhance_frame(best_frame, output_path)
    
    return {
        'best_image': output_path,
        'best_index': best_frame_idx,
        'candidates': frames
    }
