"""
Image extraction module for extracting the best dish image from cooking videos.
Uses LLM vision to analyze frames and select the most appealing shot of the finished dish.
"""

import os
import subprocess
import base64
from config import config


class ImageExtractor:
    """
    Extract a high-quality image of the finished dish from a cooking video.
    Uses LLM vision to identify and select the best frame showing the final result.
    """

    def __init__(self, video_path: str):
        self.video_path = video_path
        base, _ = os.path.splitext(video_path)
        self.frames_dir = f"{base}_frames"
        self.output_dir = os.path.dirname(video_path) or "tmp"

    def extract_best_image(self, num_candidates: int = 12) -> str | None:
        """
        Extract the best image of the finished dish from the video.
        
        Args:
            num_candidates: Number of frames to extract and analyze.
        
        Returns:
            Path to the best image file, or None if extraction fails.
        """
        # Focus on the last portion of the video where finished dish is more likely
        frames = self._extract_frames_weighted_end(num_candidates)
        if not frames:
            print("[ImageExtractor] No frames could be extracted from video")
            return None

        # Use LLM to select the best frame
        best_frame_idx = self._select_best_frame(frames)
        if best_frame_idx is None:
            # Fallback: use the last frame (most likely to show finished dish)
            best_frame_idx = len(frames) - 1

        best_frame = frames[best_frame_idx]
        
        # Copy to final output location with descriptive name
        base, _ = os.path.splitext(self.video_path)
        output_path = f"{base}_dish.jpg"
        
        # Create high-quality version of the selected frame
        self._enhance_frame(best_frame, output_path)
        
        print(f"[ImageExtractor] Best dish image saved to: {output_path}")
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
                    print(f"[ImageExtractor] Failed to extract frame at {ts}s: {e}")
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

    def _select_best_frame(self, frame_paths: list[str]) -> int | None:
        """
        Use LLM vision to select the best frame showing the finished dish.
        
        Returns:
            Index of the best frame, or None if selection fails.
        """
        if config.LLM_PROVIDER == "gemini":
            return self._select_best_frame_gemini(frame_paths)
        elif config.LLM_PROVIDER == "openai":
            return self._select_best_frame_openai(frame_paths)
        else:
            print(f"[ImageExtractor] LLM provider {config.LLM_PROVIDER} not supported for image selection")
            return None

    def _select_best_frame_gemini(self, frame_paths: list[str]) -> int | None:
        """Select best frame using Gemini vision."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Prepare image parts
        parts = []
        for i, path in enumerate(frame_paths):
            with open(path, "rb") as f:
                image_data = f.read()
            parts.append(types.Part.from_bytes(
                data=image_data,
                mime_type="image/jpeg"
            ))
            parts.append(types.Part.from_text(text=f"[Image {i}]"))

        prompt = self._get_selection_prompt(len(frame_paths))
        parts.append(types.Part.from_text(text=prompt))

        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[types.Content(role="user", parts=parts)],
            )
            return self._parse_selection_response(response.text or "", len(frame_paths))
        except Exception as e:
            print(f"[ImageExtractor] Gemini selection error: {e}")
            return None

    def _select_best_frame_openai(self, frame_paths: list[str]) -> int | None:
        """Select best frame using OpenAI vision."""
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)

        # Encode frames as base64
        image_contents = []
        for i, frame_path in enumerate(frame_paths):
            with open(frame_path, "rb") as f:
                b64_image = base64.standard_b64encode(f.read()).decode("utf-8")
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}",
                    "detail": "low"  # Use low detail for faster selection
                }
            })
            image_contents.append({
                "type": "text",
                "text": f"[Image {i}]"
            })

        prompt = self._get_selection_prompt(len(frame_paths))
        image_contents.append({"type": "text", "text": prompt})

        try:
            response = client.responses.create(
                model=config.OPENAI_MODEL,
                input=[{"role": "user", "content": image_contents}]
            )
            return self._parse_selection_response(response.output_text or "", len(frame_paths))
        except Exception as e:
            print(f"[ImageExtractor] OpenAI selection error: {e}")
            return None

    def _get_selection_prompt(self, num_frames: int) -> str:
        """Get the prompt for frame selection."""
        return f"""You are analyzing {num_frames} frames from a cooking video to find the BEST image of the finished dish.

Look for a frame that shows:
1. The COMPLETED/FINISHED dish (not preparation steps)
2. Appetizing presentation with good lighting
3. Clear, well-focused image
4. The food as the main subject (not the cook's face or hands)
5. Attractive plating or serving presentation

Respond with ONLY the number (0-{num_frames - 1}) of the best frame.
If none show a finished dish, pick the most appetizing food image.
Just respond with the single number, nothing else."""

    def _parse_selection_response(self, response: str, max_idx: int) -> int | None:
        """Parse LLM response to get frame index."""
        import re
        # Find first number in response
        match = re.search(r"\d+", response.strip())
        if match:
            idx = int(match.group())
            if 0 <= idx < max_idx:
                return idx
        return None

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
            import shutil
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
