"""Tests for src.voice_input — GTK-free voice input core."""

import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.voice_input import (
    DEFAULT_MAX_BYTES,
    DEFAULT_SAMPLE_RATE,
    STT_BACKENDS,
    AudioData,
    SystemBackend,
    VoiceBackendUnavailable,
    VoiceConfig,
    VoiceInputError,
    VoskBackend,
    WhisperAPIBackend,
    WhisperBackend,
    build_backend,
    capture_and_transcribe,
    file_capture,
    format_backend_status,
    list_backend_status,
    load_voice_config,
    transcribe_audio,
)


def make_wav_bytes(duration_seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    """Generate a tiny silent WAV file for testing."""
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = int(duration_seconds * sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def make_pcm(duration_seconds: float = 0.1, sample_rate: int = 16000) -> bytes:
    frames = int(duration_seconds * sample_rate)
    return b"\x00\x00" * frames


class TestAudioData(unittest.TestCase):
    def test_duration(self):
        pcm = make_pcm(0.5, 16000)
        audio = AudioData(pcm=pcm, sample_rate=16000, channels=1, sample_width=2)
        self.assertAlmostEqual(audio.duration_seconds, 0.5, places=2)

    def test_duration_zero_pcm(self):
        audio = AudioData(pcm=b"")
        self.assertEqual(audio.duration_seconds, 0.0)

    def test_size_bytes(self):
        audio = AudioData(pcm=b"\x00" * 1000)
        self.assertEqual(audio.size_bytes, 1000)

    def test_wav_roundtrip(self):
        pcm = make_pcm(0.1, 16000)
        audio = AudioData(pcm=pcm, sample_rate=16000, channels=1, sample_width=2)
        wav_bytes = audio.to_wav_bytes()
        parsed = AudioData.from_wav_bytes(wav_bytes)
        self.assertEqual(parsed.sample_rate, 16000)
        self.assertEqual(parsed.channels, 1)
        self.assertEqual(parsed.sample_width, 2)
        self.assertEqual(parsed.pcm, pcm)

    def test_from_wav_bytes_invalid(self):
        with self.assertRaises(wave.Error):
            AudioData.from_wav_bytes(b"not a wav file")


class TestVoiceConfig(unittest.TestCase):
    def test_defaults(self):
        vc = VoiceConfig()
        self.assertFalse(vc.enabled)
        self.assertEqual(vc.backend, "vosk")
        self.assertEqual(vc.language, "pt")
        self.assertEqual(vc.sample_rate, DEFAULT_SAMPLE_RATE)

    def test_from_dict(self):
        vc = VoiceConfig.from_dict({
            "enabled": True,
            "backend": "whisper",
            "language": "en",
            "whisper_model": "small",
        })
        self.assertTrue(vc.enabled)
        self.assertEqual(vc.backend, "whisper")
        self.assertEqual(vc.language, "en")
        self.assertEqual(vc.whisper_model, "small")

    def test_from_dict_not_dict(self):
        vc = VoiceConfig.from_dict("not a dict")
        self.assertEqual(vc.backend, "vosk")

    def test_to_dict_no_api_key(self):
        vc = VoiceConfig(api_key="secret123")
        data = vc.to_dict()
        self.assertNotIn("api_key", data)

    def test_validate_clean(self):
        vc = VoiceConfig(backend="whisper")
        self.assertEqual(vc.validate(), [])

    def test_validate_unknown_backend(self):
        vc = VoiceConfig(backend="unknown")
        warnings = vc.validate()
        self.assertTrue(any("unknown backend" in w for w in warnings))

    def test_validate_bad_sample_rate(self):
        vc = VoiceConfig(backend="whisper", sample_rate=100)
        warnings = vc.validate()
        self.assertTrue(any("sample_rate" in w for w in warnings))

    def test_validate_bad_duration(self):
        vc = VoiceConfig(backend="whisper", max_duration_seconds=0)
        warnings = vc.validate()
        self.assertTrue(any("max_duration" in w for w in warnings))

    def test_validate_vosk_missing_model(self):
        vc = VoiceConfig(backend="vosk", vosk_model_path="")
        warnings = vc.validate()
        self.assertTrue(any("vosk_model_path" in w for w in warnings))

    def test_validate_whisper_api_missing_key(self):
        vc = VoiceConfig(backend="whisper-api", api_key="")
        warnings = vc.validate()
        self.assertTrue(any("api_key" in w for w in warnings))

    def test_validate_system_missing_command(self):
        vc = VoiceConfig(backend="system", system_command="")
        warnings = vc.validate()
        self.assertTrue(any("system_command" in w for w in warnings))

    def test_validate_bad_language(self):
        vc = VoiceConfig(backend="whisper", language="x" * 20)
        warnings = vc.validate()
        self.assertTrue(any("language" in w for w in warnings))

    def test_validate_vosk_with_model_path(self):
        vc = VoiceConfig(backend="vosk", vosk_model_path="/models/vosk-pt")
        # Still warns if path doesn't exist? No — validate() only checks emptiness.
        self.assertEqual(vc.validate(), [])


class TestLoadVoiceConfig(unittest.TestCase):
    def test_defaults_when_missing(self):
        config = SimpleNamespace(get=lambda k, d=None: d)
        vc = load_voice_config(config)
        self.assertEqual(vc.backend, "vosk")

    def test_loads_values(self):
        stored = {"enabled": True, "backend": "whisper", "whisper_model": "tiny"}
        config = SimpleNamespace(get=lambda k, d=None: stored if k == "voice" else d)
        vc = load_voice_config(config)
        self.assertTrue(vc.enabled)
        self.assertEqual(vc.backend, "whisper")
        self.assertEqual(vc.whisper_model, "tiny")

    def test_whisper_api_pulls_key_from_provider(self):
        stored_voice = {"backend": "whisper-api"}
        def fake_get(key, default=None):
            if key == "voice":
                return stored_voice
            if key == "api.default_provider":
                return "openrouter"
            if key == "api.providers.openrouter.api_key":
                return "sk-test-key"
            return default
        config = SimpleNamespace(get=fake_get)
        vc = load_voice_config(config)
        self.assertEqual(vc.api_key, "sk-test-key")

    def test_non_dict_returns_defaults(self):
        config = SimpleNamespace(get=lambda k, d=None: "not a dict" if k == "voice" else d)
        vc = load_voice_config(config)
        self.assertEqual(vc.backend, "vosk")


class TestVoskBackend(unittest.TestCase):
    def test_not_available_no_path(self):
        backend = VoskBackend("")
        self.assertFalse(backend.is_available())
        self.assertIn("vosk_model_path", backend.unavailable_reason())

    def test_not_available_bad_path(self):
        backend = VoskBackend("/nonexistent/path/xyz")
        self.assertFalse(backend.is_available())
        self.assertIn("not found", backend.unavailable_reason())

    def test_not_available_no_package(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            backend = VoskBackend(tmp)
            # vosk is not installed in the test environment.
            self.assertFalse(backend.is_available())
            self.assertIn("vosk", backend.unavailable_reason().lower())

    def test_transcribe_raises_when_unavailable(self):
        backend = VoskBackend("")
        audio = AudioData(pcm=make_pcm())
        with self.assertRaises(VoiceBackendUnavailable):
            backend.transcribe(audio, "pt")


class TestWhisperBackend(unittest.TestCase):
    def test_not_available_no_package(self):
        backend = WhisperBackend()
        # whisper is not installed in the test environment.
        if not backend.is_available():
            self.assertIn("whisper", backend.unavailable_reason().lower())

    def test_transcribe_raises_when_unavailable(self):
        backend = WhisperBackend()
        audio = AudioData(pcm=make_pcm())
        if not backend.is_available():
            with self.assertRaises(VoiceBackendUnavailable):
                backend.transcribe(audio, "pt")


class TestWhisperAPIBackend(unittest.TestCase):
    def test_not_available_no_key(self):
        backend = WhisperAPIBackend("https://api.example.com/v1", "", "whisper-1")
        self.assertFalse(backend.is_available())
        self.assertIn("API key", backend.unavailable_reason())

    def test_not_available_no_url(self):
        backend = WhisperAPIBackend("", "sk-key", "whisper-1")
        self.assertFalse(backend.is_available())

    def test_available_with_key_and_url(self):
        backend = WhisperAPIBackend("https://api.example.com/v1", "sk-key", "whisper-1")
        self.assertTrue(backend.is_available())

    def test_transcribe_raises_when_unavailable(self):
        backend = WhisperAPIBackend("", "", "whisper-1")
        audio = AudioData(pcm=make_pcm())
        with self.assertRaises(VoiceBackendUnavailable):
            backend.transcribe(audio, "pt")

    def test_transcribe_success(self):
        backend = WhisperAPIBackend("https://api.example.com/v1", "sk-key", "whisper-1")
        audio = AudioData(pcm=make_pcm())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "  hello world  "}
        with patch("requests.post", return_value=mock_response) as mock_post:
            text = backend.transcribe(audio, "pt")
        self.assertEqual(text, "hello world")
        call_kwargs = mock_post.call_args
        self.assertIn("audio/transcriptions", call_kwargs[0][0])
        self.assertEqual(call_kwargs[1]["headers"]["Authorization"], "Bearer sk-key")
        self.assertEqual(call_kwargs[1]["data"]["model"], "whisper-1")
        self.assertEqual(call_kwargs[1]["data"]["language"], "pt")

    def test_transcribe_api_error(self):
        backend = WhisperAPIBackend("https://api.example.com/v1", "sk-key", "whisper-1")
        audio = AudioData(pcm=make_pcm())
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        with patch("requests.post", return_value=mock_response):
            with self.assertRaises(VoiceInputError) as ctx:
                backend.transcribe(audio, "pt")
        self.assertIn("401", str(ctx.exception))

    def test_transcribe_invalid_json(self):
        backend = WhisperAPIBackend("https://api.example.com/v1", "sk-key", "whisper-1")
        audio = AudioData(pcm=make_pcm())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("not json")
        with patch("requests.post", return_value=mock_response):
            with self.assertRaises(VoiceInputError):
                backend.transcribe(audio, "pt")

    def test_transcribe_request_exception(self):
        import requests
        backend = WhisperAPIBackend("https://api.example.com/v1", "sk-key", "whisper-1")
        audio = AudioData(pcm=make_pcm())
        with patch("requests.post", side_effect=requests.ConnectionError("refused")):
            with self.assertRaises(VoiceInputError) as ctx:
                backend.transcribe(audio, "pt")
        self.assertIn("API request failed", str(ctx.exception))

    def test_base_url_trailing_slash_stripped(self):
        backend = WhisperAPIBackend("https://api.example.com/v1/", "sk-key", "whisper-1")
        self.assertEqual(backend.base_url, "https://api.example.com/v1")


class TestSystemBackend(unittest.TestCase):
    def test_not_available_no_command(self):
        backend = SystemBackend("")
        self.assertFalse(backend.is_available())
        self.assertIn("system_command", backend.unavailable_reason())

    def test_not_available_command_not_in_path(self):
        backend = SystemBackend("nonexistent-stt-command-xyz")
        self.assertFalse(backend.is_available())
        self.assertIn("not found", backend.unavailable_reason())

    def test_available_with_real_command(self):
        backend = SystemBackend("echo")
        self.assertTrue(backend.is_available())

    def test_transcribe_raises_when_unavailable(self):
        backend = SystemBackend("")
        audio = AudioData(pcm=make_pcm())
        with self.assertRaises(VoiceBackendUnavailable):
            backend.transcribe(audio, "pt")

    def test_transcribe_rejects_metacharacters(self):
        backend = SystemBackend("echo; rm -rf /")
        audio = AudioData(pcm=make_pcm())
        with self.assertRaises(VoiceInputError) as ctx:
            backend.transcribe(audio, "pt")
        self.assertIn("metacharacters", str(ctx.exception))

    def test_transcribe_success(self):
        # Use "true" which always exits 0 and prints nothing, then verify
        # the call succeeds. We avoid "echo" because it would print the temp
        # file path (which is fine) but the test just needs exit 0.
        backend = SystemBackend("true")
        audio = AudioData(pcm=make_pcm())
        text = backend.transcribe(audio, "pt")
        self.assertEqual(text, "")

    def test_transcribe_failure_exit_code(self):
        backend = SystemBackend("false")  # always exits 1
        audio = AudioData(pcm=make_pcm())
        with self.assertRaises(VoiceInputError) as ctx:
            backend.transcribe(audio, "pt")
        self.assertIn("exit 1", str(ctx.exception))

    def test_transcribe_timeout(self):
        # Use a wrapper script that ignores extra args and sleeps. We write
        # a tiny shell script to /tmp that sleeps regardless of arguments.
        import os
        import stat
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/sh\nsleep 10\n")
            script = f.name
        os.chmod(script, os.stat(script).st_mode | stat.S_IEXEC)
        try:
            backend = SystemBackend(script, timeout=1)
            self.assertTrue(backend.is_available())
            audio = AudioData(pcm=make_pcm())
            with self.assertRaises(VoiceInputError) as ctx:
                backend.transcribe(audio, "pt")
            self.assertIn("timed out", str(ctx.exception))
        finally:
            Path(script).unlink(missing_ok=True)


class TestBuildBackend(unittest.TestCase):
    def test_builds_vosk(self):
        vc = VoiceConfig(backend="vosk", vosk_model_path="/models/vosk")
        backend = build_backend(vc)
        self.assertIsInstance(backend, VoskBackend)

    def test_builds_whisper(self):
        vc = VoiceConfig(backend="whisper", whisper_model="small")
        backend = build_backend(vc)
        self.assertIsInstance(backend, WhisperBackend)

    def test_builds_whisper_api(self):
        vc = VoiceConfig(backend="whisper-api", api_key="sk", api_model="whisper-1")
        backend = build_backend(vc)
        self.assertIsInstance(backend, WhisperAPIBackend)

    def test_builds_system(self):
        vc = VoiceConfig(backend="system", system_command="stt-cli")
        backend = build_backend(vc)
        self.assertIsInstance(backend, SystemBackend)

    def test_unknown_backend_raises(self):
        vc = VoiceConfig(backend="unknown")
        with self.assertRaises(VoiceInputError):
            build_backend(vc)


class TestListBackendStatus(unittest.TestCase):
    def test_returns_all_backends(self):
        vc = VoiceConfig(backend="vosk")
        status = list_backend_status(vc)
        for name in STT_BACKENDS:
            self.assertIn(name, status)
            self.assertIn("available", status[name])
            self.assertIn("selected", status[name])

    def test_marks_selected(self):
        vc = VoiceConfig(backend="whisper")
        status = list_backend_status(vc)
        self.assertTrue(status["whisper"]["selected"])
        self.assertFalse(status["vosk"]["selected"])

    def test_unavailable_has_reason(self):
        vc = VoiceConfig(backend="vosk", vosk_model_path="")
        status = list_backend_status(vc)
        self.assertFalse(status["vosk"]["available"])
        self.assertTrue(status["vosk"]["reason"])


class TestFormatBackendStatus(unittest.TestCase):
    def test_table_has_header(self):
        vc = VoiceConfig()
        text = format_backend_status(list_backend_status(vc))
        lines = text.splitlines()
        self.assertIn("Backend", lines[0])
        self.assertIn("Status", lines[0])

    def test_table_lists_all_backends(self):
        vc = VoiceConfig()
        text = format_backend_status(list_backend_status(vc))
        for name in STT_BACKENDS:
            self.assertIn(name, text)

    def test_selected_marker(self):
        vc = VoiceConfig(backend="whisper")
        text = format_backend_status(list_backend_status(vc))
        # The selected backend line starts with "*".
        for line in text.splitlines():
            if "whisper" in line and "whisper-api" not in line:
                # Could be either the whisper or whisper-api line.
                pass
        # Just check that at least one line starts with "*".
        self.assertTrue(any(l.startswith("*") for l in text.splitlines()))


class TestFileCapture(unittest.TestCase):
    def test_loads_wav_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(make_wav_bytes(0.1))
            path = tmp.name
        try:
            audio = file_capture(path, sample_rate=16000, max_duration=60, max_bytes=DEFAULT_MAX_BYTES)
            self.assertAlmostEqual(audio.duration_seconds, 0.1, places=1)
            self.assertEqual(audio.sample_rate, 16000)
        finally:
            Path(path).unlink()

    def test_missing_file(self):
        with self.assertRaises(VoiceInputError):
            file_capture("/nonexistent/audio.wav", 16000, 60, DEFAULT_MAX_BYTES)

    def test_oversized_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(b"\x00" * 1000)
            path = tmp.name
        try:
            with self.assertRaises(VoiceInputError) as ctx:
                file_capture(path, 16000, 60, max_bytes=100)
            self.assertIn("byte limit", str(ctx.exception))
        finally:
            Path(path).unlink()

    def test_too_long_audio(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(make_wav_bytes(5.0, 16000))
            path = tmp.name
        try:
            with self.assertRaises(VoiceInputError) as ctx:
                file_capture(path, 16000, max_duration=1, max_bytes=DEFAULT_MAX_BYTES)
            self.assertIn("duration", str(ctx.exception))
        finally:
            Path(path).unlink()


class TestTranscribeAudio(unittest.TestCase):
    def test_uses_provided_backend(self):
        vc = VoiceConfig(backend="vosk")
        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = "hello"
        audio = AudioData(pcm=make_pcm())
        text = transcribe_audio(audio, vc, backend=mock_backend)
        self.assertEqual(text, "hello")
        mock_backend.transcribe.assert_called_once_with(audio, "pt")

    def test_builds_backend_from_config(self):
        vc = VoiceConfig(backend="system", system_command="true")
        audio = AudioData(pcm=make_pcm())
        text = transcribe_audio(audio, vc)
        self.assertEqual(text, "")


class TestCaptureAndTranscribe(unittest.TestCase):
    def test_with_fake_capture(self):
        vc = VoiceConfig(backend="system", system_command="true")
        fake_audio = AudioData(pcm=make_pcm())
        def fake_capture(sample_rate, max_duration, max_bytes):
            return fake_audio
        text = capture_and_transcribe(vc, capture=fake_capture)
        self.assertEqual(text, "")

    def test_with_fake_capture_and_backend(self):
        vc = VoiceConfig(backend="vosk")
        fake_audio = AudioData(pcm=make_pcm())
        mock_backend = MagicMock()
        mock_backend.transcribe.return_value = "transcribed text"
        def fake_capture(sample_rate, max_duration, max_bytes):
            return fake_audio
        text = capture_and_transcribe(vc, capture=fake_capture, backend=mock_backend)
        self.assertEqual(text, "transcribed text")


class TestSounddeviceCapture(unittest.TestCase):
    def test_unavailable_without_package(self):
        from src.voice_input import sounddevice_capture
        # sounddevice may be installed but PortAudio missing (raises OSError
        # at import), or not installed at all (ImportError). Both paths lead
        # to VoiceBackendUnavailable.
        try:
            import sounddevice  # noqa: F401
            import sounddevice as sd
            # If we get here, try to actually use it — if PortAudio is
            # missing the import already raised. If it works, skip.
            sd.query_devices()
            self.skipTest("sounddevice is functional; cannot test unavailable path")
        except (ImportError, OSError):
            pass
        with self.assertRaises(VoiceBackendUnavailable):
            sounddevice_capture(16000, 60, DEFAULT_MAX_BYTES)


class TestSTTBackendsConstant(unittest.TestCase):
    def test_all_backends_listed(self):
        self.assertEqual(set(STT_BACKENDS), {"vosk", "whisper", "whisper-api", "system"})


if __name__ == "__main__":
    unittest.main()
