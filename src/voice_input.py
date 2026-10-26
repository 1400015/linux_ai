"""GTK-free voice input core: speech-to-text backends and audio capture.

This module owns the data model and the backend abstraction for voice input.
It never imports GTK, never touches a microphone directly in tests, and
degrades gracefully when optional dependencies (``vosk``, ``sounddevice``,
``whisper``) are not installed — every backend reports its availability via
``is_available()`` and raises :class:`VoiceInputError` with a helpful message
when invoked without its dependencies.

Backends
--------
- ``vosk``: offline STT using a local Vosk model directory. Zero network
  traffic; the model is downloaded separately by the user.
- ``whisper``: offline STT using OpenAI Whisper (``openai-whisper`` or
  ``faster-whisper``). GPU optional.
- ``whisper-api``: cloud STT via the OpenAI-compatible ``/audio/transcriptions``
  endpoint (OpenRouter, OpenAI, Groq, etc.). Requires an API key.
- ``system``: delegates to an external ``stt`` command (e.g. ``whisper-cli``,
  ``vosk-transcriber``) via subprocess. Useful on minimal systems.

Audio capture
-------------
:class:`AudioCapture` is an abstraction over the microphone. The production
implementation (:func:`default_capture`) uses ``sounddevice``; tests inject a
fake. Recording is always bounded: a maximum duration and a maximum byte
count prevent runaway captures.

Configuration lives under ``voice`` in the config file (see
:func:`load_voice_config`). The GUI and CLI both read it through
:class:`VoiceConfig`.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2  # 16-bit PCM
DEFAULT_MAX_DURATION_SECONDS = 60
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 MiB cap on captured audio

#: Backend identifiers accepted in config.
STT_BACKENDS = ("vosk", "whisper", "whisper-api", "system")

#: Human-readable labels for each backend.
BACKEND_LABELS: Dict[str, str] = {
    "vosk": "Vosk (offline)",
    "whisper": "Whisper (offline)",
    "whisper-api": "Whisper API (cloud)",
    "system": "System command",
}

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VoiceInputError(Exception):
    """Raised when voice input cannot be performed."""


class VoiceBackendUnavailable(VoiceInputError):
    """Raised when the selected backend's dependencies are not installed."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class VoiceConfig:
    """Voice input configuration (mirrors the ``voice`` config section)."""

    enabled: bool = False
    backend: str = "vosk"
    language: str = "pt"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS
    # vosk
    vosk_model_path: str = ""
    # whisper (offline)
    whisper_model: str = "base"
    whisper_device: str = "cpu"
    # whisper-api (cloud)
    api_base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    api_model: str = "openai/whisper-1"
    api_timeout: int = 60
    # system command
    system_command: str = ""
    system_timeout: int = 60

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceConfig":
        if not isinstance(data, dict):
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            backend=str(data.get("backend", "vosk")),
            language=str(data.get("language", "pt")),
            sample_rate=int(data.get("sample_rate", DEFAULT_SAMPLE_RATE)),
            max_duration_seconds=int(data.get("max_duration_seconds", DEFAULT_MAX_DURATION_SECONDS)),
            vosk_model_path=str(data.get("vosk_model_path", "")),
            whisper_model=str(data.get("whisper_model", "base")),
            whisper_device=str(data.get("whisper_device", "cpu")),
            api_base_url=str(data.get("api_base_url", "https://openrouter.ai/api/v1")),
            api_key=str(data.get("api_key", "")),
            api_model=str(data.get("api_model", "openai/whisper-1")),
            api_timeout=int(data.get("api_timeout", 60)),
            system_command=str(data.get("system_command", "")),
            system_timeout=int(data.get("system_timeout", 60)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "max_duration_seconds": self.max_duration_seconds,
            "vosk_model_path": self.vosk_model_path,
            "whisper_model": self.whisper_model,
            "whisper_device": self.whisper_device,
            "api_base_url": self.api_base_url,
            # api_key is intentionally NOT serialized here; it lives in the
            # credential store or the api.providers section.
            "api_model": self.api_model,
            "api_timeout": self.api_timeout,
            "system_command": self.system_command,
            "system_timeout": self.system_timeout,
        }

    def validate(self) -> List[str]:
        """Return a list of validation warnings (empty = valid)."""
        warnings: List[str] = []
        if self.backend not in STT_BACKENDS:
            warnings.append("unknown backend: {!r} (expected one of {})".format(
                self.backend, ", ".join(STT_BACKENDS)))
        if not 8000 <= self.sample_rate <= 48000:
            warnings.append("sample_rate must be between 8000 and 48000")
        if not 1 <= self.max_duration_seconds <= 600:
            warnings.append("max_duration_seconds must be between 1 and 600")
        if self.backend == "vosk" and not self.vosk_model_path:
            warnings.append("vosk backend requires vosk_model_path")
        if self.backend == "whisper-api" and not self.api_key:
            warnings.append("whisper-api backend requires api_key (set via credential store)")
        if self.backend == "system" and not self.system_command:
            warnings.append("system backend requires system_command")
        if not self.language or len(self.language) > 10:
            warnings.append("language must be a non-empty string (max 10 chars)")
        return warnings


def load_voice_config(config: Any) -> VoiceConfig:
    """Build a :class:`VoiceConfig` from a ConfigManager."""
    raw = config.get("voice", {})
    if not isinstance(raw, dict):
        return VoiceConfig()
    vc = VoiceConfig.from_dict(raw)
    # The API key is not stored in the voice section; pull it from the
    # provider config if the backend is whisper-api.
    if vc.backend == "whisper-api" and not vc.api_key:
        provider = config.get("api.default_provider", "openrouter")
        vc.api_key = config.get("api.providers.{}.api_key".format(provider), "") or ""
    return vc


# ---------------------------------------------------------------------------
# Audio capture abstraction
# ---------------------------------------------------------------------------


@dataclass
class AudioData:
    """Captured audio: raw PCM bytes + format metadata."""

    pcm: bytes
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    sample_width: int = DEFAULT_SAMPLE_WIDTH

    @property
    def duration_seconds(self) -> float:
        bytes_per_sample = self.channels * self.sample_width
        if bytes_per_sample == 0 or self.sample_rate == 0:
            return 0.0
        return len(self.pcm) / (self.sample_rate * bytes_per_sample)

    @property
    def size_bytes(self) -> int:
        return len(self.pcm)

    def to_wav_bytes(self) -> bytes:
        """Wrap the PCM data in a WAV container (in-memory)."""
        import io
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(self.sample_width)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.pcm)
        return buffer.getvalue()

    @classmethod
    def from_wav_bytes(cls, data: bytes) -> "AudioData":
        """Parse a WAV file into AudioData."""
        import io
        with wave.open(io.BytesIO(data), "rb") as wav:
            return cls(
                pcm=wav.readframes(wav.getnframes()),
                sample_rate=wav.getframerate(),
                channels=wav.getnchannels(),
                sample_width=wav.getsampwidth(),
            )


#: A capture function: (sample_rate, max_duration, max_bytes) -> AudioData.
#: Tests inject a fake; the production default is :func:`sounddevice_capture`.
CaptureFunc = Callable[[int, int, int], AudioData]


def sounddevice_capture(sample_rate: int, max_duration: int, max_bytes: int) -> AudioData:
    """Capture audio from the default microphone using ``sounddevice``.

    Raises :class:`VoiceBackendUnavailable` when sounddevice is not installed,
    and :class:`VoiceInputError` when no microphone is available.
    """
    try:
        import sounddevice as sd
    except (ImportError, OSError) as error:
        # OSError covers "PortAudio library not found" which sounddevice
        # raises at import time when the C library is missing.
        raise VoiceBackendUnavailable(
            "sounddevice is not available ({}); install the 'voice' extra "
            "(pip install linux-ai-assistant[voice]) or use a different backend".format(error)
        ) from error

    max_frames = sample_rate * max_duration
    logger.info("Recording audio: %d Hz, max %d s", sample_rate, max_duration)
    try:
        recording = sd.rec(
            frames=max_frames,
            samplerate=sample_rate,
            channels=DEFAULT_CHANNELS,
            dtype="int16",
        )
        sd.wait()
    except Exception as error:
        raise VoiceInputError("microphone capture failed: {}".format(error)) from error

    pcm = recording.tobytes()
    if len(pcm) > max_bytes:
        # Truncate to the byte cap (should not happen with max_duration, but
        # be defensive).
        pcm = pcm[:max_bytes]
    return AudioData(pcm=pcm, sample_rate=sample_rate)


def file_capture(wav_path: str, sample_rate: int, max_duration: int, max_bytes: int) -> AudioData:
    """Load audio from a WAV file instead of capturing from a microphone.

    Useful for testing, batch transcription, and the CLI ``transcribe``
    subcommand.
    """
    path = Path(wav_path)
    if not path.is_file():
        raise VoiceInputError("audio file not found: {}".format(path))
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise VoiceInputError(
            "audio file exceeds the {} byte limit".format(max_bytes))
    audio = AudioData.from_wav_bytes(data)
    if audio.duration_seconds > max_duration:
        raise VoiceInputError(
            "audio duration {:.1f}s exceeds the {}s limit".format(
                audio.duration_seconds, max_duration))
    return audio


# ---------------------------------------------------------------------------
# STT backends
# ---------------------------------------------------------------------------


class STTBackend:
    """Abstract speech-to-text backend."""

    name: str = "abstract"

    def is_available(self) -> bool:
        raise NotImplementedError

    def unavailable_reason(self) -> str:
        return "backend not available"

    def transcribe(self, audio: AudioData, language: str) -> str:
        raise NotImplementedError


class VoskBackend(STTBackend):
    """Offline STT using a local Vosk model."""

    name = "vosk"

    def __init__(self, model_path: str):
        self.model_path = model_path
        self._model: Any = None
        self._recognizer_factory: Any = None

    def is_available(self) -> bool:
        if not self.model_path or not Path(self.model_path).is_dir():
            return False
        try:
            import vosk  # noqa: F401
            return True
        except ImportError:
            return False

    def unavailable_reason(self) -> str:
        if not self.model_path:
            return "vosk_model_path is not set"
        if not Path(self.model_path).is_dir():
            return "vosk model directory not found: {}".format(self.model_path)
        return "the 'vosk' package is not installed (pip install vosk)"

    def transcribe(self, audio: AudioData, language: str) -> str:
        if not self.is_available():
            raise VoiceBackendUnavailable(self.unavailable_reason())
        import vosk

        if self._model is None:
            logger.info("Loading Vosk model from %s", self.model_path)
            self._model = vosk.Model(self.model_path)

        recognizer = vosk.KaldiRecognizer(self._model, audio.sample_rate)
        recognizer.SetWords(True)
        recognizer.AcceptWaveform(audio.pcm)
        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()
        logger.info("Vosk transcription: %d chars", len(text))
        return text


class WhisperBackend(STTBackend):
    """Offline STT using OpenAI Whisper (``openai-whisper`` package)."""

    name = "whisper"

    def __init__(self, model_name: str = "base", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model: Any = None

    def is_available(self) -> bool:
        try:
            import whisper  # noqa: F401
            return True
        except ImportError:
            return False

    def unavailable_reason(self) -> str:
        return "the 'whisper' package is not installed (pip install openai-whisper)"

    def transcribe(self, audio: AudioData, language: str) -> str:
        if not self.is_available():
            raise VoiceBackendUnavailable(self.unavailable_reason())
        import whisper

        if self._model is None:
            logger.info("Loading Whisper model '%s' on %s", self.model_name, self.device)
            self._model = whisper.load_model(self.model_name, device=self.device)

        # Whisper expects a temp WAV file or a numpy array. We write a temp
        # WAV to keep the interface simple and avoid a numpy dependency here.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio.to_wav_bytes())
            tmp_path = tmp.name
        try:
            result = self._model.transcribe(tmp_path, language=language or None)
            text = result.get("text", "").strip()
            logger.info("Whisper transcription: %d chars", len(text))
            return text
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class WhisperAPIBackend(STTBackend):
    """Cloud STT via an OpenAI-compatible ``/audio/transcriptions`` endpoint."""

    name = "whisper-api"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def unavailable_reason(self) -> str:
        if not self.api_key:
            return "no API key configured for the whisper-api backend"
        if not self.base_url:
            return "no base URL configured for the whisper-api backend"
        return "whisper-api backend is not available"

    def transcribe(self, audio: AudioData, language: str) -> str:
        if not self.is_available():
            raise VoiceBackendUnavailable(self.unavailable_reason())
        try:
            import requests
        except ImportError as error:
            raise VoiceBackendUnavailable("the 'requests' package is required") from error

        url = "{}/audio/transcriptions".format(self.base_url)
        wav_bytes = audio.to_wav_bytes()
        if len(wav_bytes) > DEFAULT_MAX_BYTES:
            raise VoiceInputError("audio exceeds the {} byte limit for API upload".format(
                DEFAULT_MAX_BYTES))
        headers = {"Authorization": "Bearer {}".format(self.api_key)}
        data = {"model": self.model}
        if language:
            data["language"] = language
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        logger.info("Uploading %.1f KB to %s", len(wav_bytes) / 1024, url)
        try:
            response = requests.post(url, headers=headers, data=data, files=files,
                                     timeout=self.timeout)
        except requests.RequestException as error:
            raise VoiceInputError("API request failed: {}".format(error)) from error
        if response.status_code != 200:
            raise VoiceInputError(
                "API returned {}: {}".format(response.status_code, response.text[:200]))
        try:
            payload = response.json()
        except ValueError as error:
            raise VoiceInputError("API returned invalid JSON") from error
        text = payload.get("text", "").strip() if isinstance(payload, dict) else ""
        logger.info("Whisper API transcription: %d chars", len(text))
        return text


class SystemBackend(STTBackend):
    """Delegate transcription to an external command (e.g. ``whisper-cli``)."""

    name = "system"

    def __init__(self, command: str, timeout: int = 60):
        self.command = command.strip()
        self.timeout = timeout

    def is_available(self) -> bool:
        if not self.command:
            return False
        # The command must be a single executable name (no shell metacharacters).
        executable = self.command.split()[0] if self.command else ""
        return bool(executable) and shutil.which(executable) is not None

    def unavailable_reason(self) -> str:
        if not self.command:
            return "system_command is not set"
        executable = self.command.split()[0]
        return "command not found in PATH: {}".format(executable)

    def transcribe(self, audio: AudioData, language: str) -> str:
        # Reject shell metacharacters BEFORE the availability check: the
        # command is split on whitespace and passed to subprocess without a
        # shell, so metacharacters would be passed as literal arguments to
        # an untrusted executable. Failing early is safer than relying on
        # the executable to reject them.
        if any(c in self.command for c in (";", "|", "&", "$", "`", ">", "<")):
            raise VoiceInputError(
                "system_command must not contain shell metacharacters")
        if not self.is_available():
            raise VoiceBackendUnavailable(self.unavailable_reason())
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio.to_wav_bytes())
            tmp_path = tmp.name
        try:
            argv = self.command.split() + [tmp_path]
            if language:
                argv += ["--language", language]
            logger.info("Running system STT: %s", " ".join(argv))
            result = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout,
            )
            if result.returncode != 0:
                raise VoiceInputError(
                    "system command failed (exit {}): {}".format(
                        result.returncode, result.stderr.strip()[:200]))
            text = result.stdout.strip()
            logger.info("System STT transcription: %d chars", len(text))
            return text
        except subprocess.TimeoutExpired as error:
            raise VoiceInputError(
                "system command timed out after {}s".format(self.timeout)) from error
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def build_backend(config: VoiceConfig) -> STTBackend:
    """Instantiate the STT backend selected by `config`."""
    if config.backend == "vosk":
        return VoskBackend(config.vosk_model_path)
    if config.backend == "whisper":
        return WhisperBackend(config.whisper_model, config.whisper_device)
    if config.backend == "whisper-api":
        return WhisperAPIBackend(
            config.api_base_url, config.api_key, config.api_model, config.api_timeout,
        )
    if config.backend == "system":
        return SystemBackend(config.system_command, config.system_timeout)
    raise VoiceInputError("unknown backend: {!r}".format(config.backend))


def list_backend_status(config: VoiceConfig) -> Dict[str, Dict[str, Any]]:
    """Return availability info for every backend (not just the selected one)."""
    backends: List[STTBackend] = [
        VoskBackend(config.vosk_model_path),
        WhisperBackend(config.whisper_model, config.whisper_device),
        WhisperAPIBackend(config.api_base_url, config.api_key, config.api_model),
        SystemBackend(config.system_command),
    ]
    result: Dict[str, Dict[str, Any]] = {}
    for backend in backends:
        result[backend.name] = {
            "available": backend.is_available(),
            "reason": "" if backend.is_available() else backend.unavailable_reason(),
            "label": BACKEND_LABELS.get(backend.name, backend.name),
            "selected": backend.name == config.backend,
        }
    return result


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


def transcribe_audio(
    audio: AudioData,
    config: VoiceConfig,
    backend: Optional[STTBackend] = None,
) -> str:
    """Transcribe `audio` using the configured (or provided) backend."""
    if backend is None:
        backend = build_backend(config)
    warnings = config.validate()
    backend_warnings = [w for w in warnings if w.startswith(config.backend)]
    if backend_warnings:
        logger.warning("Voice config warnings: %s", "; ".join(backend_warnings))
    return backend.transcribe(audio, config.language)


def capture_and_transcribe(
    config: VoiceConfig,
    capture: Optional[CaptureFunc] = None,
    backend: Optional[STTBackend] = None,
) -> str:
    """Capture audio from the microphone and transcribe it.

    `capture` defaults to :func:`sounddevice_capture`. Tests inject a fake
    that returns a fixed :class:`AudioData`.
    """
    if capture is None:
        capture = sounddevice_capture
    audio = capture(config.sample_rate, config.max_duration_seconds, DEFAULT_MAX_BYTES)
    return transcribe_audio(audio, config, backend)


def format_backend_status(status: Dict[str, Dict[str, Any]]) -> str:
    """Human-readable table of backend availability."""
    lines = ["{:<14s} {:<10s} {}".format("Backend", "Status", "Detail")]
    lines.append("{:<14s} {:<10s} {}".format("-" * 14, "-" * 10, "-" * 30))
    for name in STT_BACKENDS:
        info = status.get(name, {})
        state = "OK" if info.get("available") else "unavailable"
        marker = "*" if info.get("selected") else " "
        detail = info.get("label", name)
        if not info.get("available") and info.get("reason"):
            detail += " ({})".format(info["reason"])
        lines.append("{} {:<13s} {:<10s} {}".format(marker, name, state, detail))
    return "\n".join(lines)
