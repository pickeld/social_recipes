import os
import subprocess
from faster_whisper import WhisperModel


class Transcriber:
    def __init__(
        self,
        video_path: str,
        model_size: str = "base",     # tiny | base | small | medium | large-v3
        device: str = "auto",         # "cpu" or "cuda"
        compute_type: str = "auto"    # "float16"/"int8"/"auto"
    ):
        self.video_path = video_path
        self.model_size = model_size
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
        """Return the full transcription as plain text."""
        self._extract_audio()
        self._load_model()

        segments, info = self.model.transcribe(
            self.audio_path, language=language)
        text_parts = [seg.text.strip() for seg in segments]
        return " ".join(text_parts).strip()
