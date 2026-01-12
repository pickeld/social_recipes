import os
import subprocess
from faster_whisper import WhisperModel

from config import config


class Transcriber:
    def __init__(
        self,
        video_path: str,
        model_size: str | None = None,  # tiny | base | small | medium | large-v3 (defaults to config.WHISPER_MODEL)
        device: str = "auto",           # "cpu" or "cuda"
        compute_type: str = "auto"      # "float16"/"int8"/"auto"
    ):
        self.video_path = video_path
        self.model_size = model_size or config.WHISPER_MODEL
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self.audio_path = self._get_audio_path()

    def _get_audio_path(self):
        base, _ = os.path.splitext(self.video_path)
        return f"{base}.wav"

    def _extract_audio(self, overwrite: bool = False):
        """Extract mono WAV audio at 16kHz using ffmpeg."""
        if os.path.exists(self.audio_path) and not overwrite:
            return self.audio_path

        cmd = [
            "ffmpeg",
            "-y", "-i", self.video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            self.audio_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return self.audio_path

    def _load_model(self):
        if self.model is None:
            self.model = WhisperModel(
                self.model_size, device=self.device, compute_type=self.compute_type)

    def transcribe(self, language: str | None = None) -> str:
        """Return the full transcription as plain text.

        Args:
            language: Language code for transcription (e.g., 'he', 'en').
                     Defaults to config.TARGET_LANGUAGE if not specified.
        """
        self._extract_audio()
        self._load_model()

        # Use target language from config if not specified
        lang = language or config.TARGET_LANGUAGE

        segments, info = self.model.transcribe(
            self.audio_path, language=lang)
        text_parts = [seg.text.strip() for seg in segments]
        return " ".join(text_parts).strip()

    def extract_visual_text(self) -> str:
        """
        Extract on-screen text from video using LLM vision capabilities.
        Supports both Gemini (direct video upload) and OpenAI (frame extraction).

        Returns:
            Extracted text from video as a single string.
        """
        if config.LLM_PROVIDER == "gemini":
            return self._extract_visual_text_gemini()
        elif config.LLM_PROVIDER == "openai":
            return self._extract_visual_text_openai()
        else:
            raise ValueError(
                f"Visual text extraction not supported for provider: {config.LLM_PROVIDER}")

    def _extract_visual_text_gemini(self) -> str:
        """Extract visual text using Gemini's direct video understanding."""
        from google import genai
        from google.genai import types
        import time

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Upload the video file to Gemini
        video_file = client.files.upload(file=self.video_path)

        # Wait for the video to be processed
        while video_file.state and video_file.state.name == "PROCESSING":
            time.sleep(2)
            video_file = client.files.get(name=video_file.name or "")

        if video_file.state and video_file.state.name == "FAILED":
            raise RuntimeError(f"Video processing failed: {video_file.state}")

        prompt = self._get_visual_text_prompt()

        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(
                            file_uri=video_file.uri or "",
                            mime_type=video_file.mime_type or "video/mp4"
                        ),
                        types.Part.from_text(text=prompt),
                    ],
                ),
            ],
        )

        return response.text or ""

    def _extract_visual_text_openai(self) -> str:
        """Extract visual text using OpenAI's vision API with extracted frames."""
        import base64
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY)

        # Extract frames from video
        frames = self._extract_frames(num_frames=8)
        if not frames:
            raise RuntimeError("No frames could be extracted from video")

        # Encode frames as base64
        image_contents = []
        for frame_path in frames:
            with open(frame_path, "rb") as f:
                b64_image = base64.standard_b64encode(f.read()).decode("utf-8")
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}",
                    "detail": "high"
                }
            })

        prompt = self._get_visual_text_prompt()

        response = client.responses.create(
            model=config.OPENAI_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        *image_contents
                    ]
                }
            ]
        )

        return response.output_text or ""

    def _extract_frames(self, num_frames: int = 8) -> list[str]:
        """Extract evenly-spaced frames from video using ffmpeg."""
        base, _ = os.path.splitext(self.video_path)
        frames_dir = f"{base}_frames"
        os.makedirs(frames_dir, exist_ok=True)

        # Get video duration
        duration_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            self.video_path
        ]

        try:
            result = subprocess.run(
                duration_cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            duration = 30.0  # Default assumption

        # Calculate timestamps
        margin = min(0.5, duration * 0.05)
        interval = (duration - 2 * margin) / (num_frames - 1)
        timestamps = [margin + (i * interval) for i in range(num_frames)]

        frame_paths = []
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(frames_dir, f"frame_{i:02d}.jpg")

            if not os.path.exists(frame_path):
                cmd = [
                    "ffmpeg", "-y", "-ss", str(ts),
                    "-i", self.video_path,
                    "-vframes", "1",
                    "-q:v", "2",
                    frame_path
                ]
                subprocess.run(cmd, capture_output=True, check=True)

            frame_paths.append(frame_path)

        return frame_paths

    def _get_visual_text_prompt(self) -> str:
        """Get the prompt for visual text extraction."""
        # Map language codes to full names for clearer prompts
        lang_names = {
            "he": "Hebrew",
            "en": "English",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "ar": "Arabic",
            "ru": "Russian",
        }
        target_lang = lang_names.get(
            config.TARGET_LANGUAGE, config.TARGET_LANGUAGE)

        return f"""Analyze this video/images and extract ALL text that appears on screen.
This includes:
- Recipe titles and names
- Ingredient lists with quantities
- Cooking instructions or steps
- Captions or subtitles
- Any overlay text, annotations, or labels
- Timer displays or temperatures

Return ONLY the extracted text, organized logically.
If text appears multiple times, include it once.
Format ingredient lists clearly with quantities and measurements.
Output the text in {target_lang} language. If the original text is in a different language, translate it to {target_lang}."""
