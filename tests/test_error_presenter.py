"""Tests for src.error_presenter — user-friendly error presentation."""

import unittest

from src.error_presenter import (
    ErrorCategory,
    PresentedError,
    error_summary,
    present_credential_shadow,
    present_error,
    _classify,
)


class TestClassify(unittest.TestCase):
    def test_ai_request_cancelled(self):
        from src.ai_client import AIRequestCancelled
        self.assertEqual(_classify(AIRequestCancelled("test")), ErrorCategory.CANCELLED)

    def test_provider_not_configured(self):
        from src.ai_client import ProviderNotConfigured
        self.assertEqual(_classify(ProviderNotConfigured("test")), ErrorCategory.NOT_CONFIGURED)

    def test_ai_response_limit(self):
        from src.ai_client import AIResponseLimitError
        self.assertEqual(_classify(AIResponseLimitError("test")), ErrorCategory.RESPONSE_LIMIT)

    def test_image_errors(self):
        from src.ai_client import AIImageRequestError, AIUnsupportedImageProvider, AIUnsupportedImageModel
        for exc_cls in (AIImageRequestError, AIUnsupportedImageProvider, AIUnsupportedImageModel):
            self.assertEqual(_classify(exc_cls("test")), ErrorCategory.IMAGE_UNSUPPORTED)

    def test_provider_error_network(self):
        from src.ai_client import AIProviderError
        self.assertEqual(_classify(AIProviderError("connection refused")), ErrorCategory.NETWORK)

    def test_provider_error_rate_limit(self):
        from src.ai_client import AIProviderError
        self.assertEqual(_classify(AIProviderError("rate limit exceeded 429")), ErrorCategory.RATE_LIMIT)

    def test_provider_error_auth(self):
        from src.ai_client import AIProviderError
        self.assertEqual(_classify(AIProviderError("401 unauthorized")), ErrorCategory.AUTH)

    def test_provider_error_timeout(self):
        from src.ai_client import AIProviderError
        self.assertEqual(_classify(AIProviderError("request timed out")), ErrorCategory.TIMEOUT)

    def test_provider_error_model_not_found(self):
        from src.ai_client import AIProviderError
        self.assertEqual(_classify(AIProviderError("model not found")), ErrorCategory.MODEL_UNAVAILABLE)

    def test_requests_timeout(self):
        from requests.exceptions import Timeout
        self.assertEqual(_classify(Timeout("timed out")), ErrorCategory.TIMEOUT)

    def test_requests_connection_error(self):
        from requests.exceptions import ConnectionError
        self.assertEqual(_classify(ConnectionError("refused")), ErrorCategory.NETWORK)

    def test_requests_http_error_401(self):
        from requests.exceptions import HTTPError
        from requests import Response
        response = Response()
        response.status_code = 401
        error = HTTPError("unauthorized", response=response)
        self.assertEqual(_classify(error), ErrorCategory.AUTH)

    def test_requests_http_error_429(self):
        from requests.exceptions import HTTPError
        from requests import Response
        response = Response()
        response.status_code = 429
        error = HTTPError("rate limited", response=response)
        self.assertEqual(_classify(error), ErrorCategory.RATE_LIMIT)

    def test_requests_http_error_404(self):
        from requests.exceptions import HTTPError
        from requests import Response
        response = Response()
        response.status_code = 404
        error = HTTPError("not found", response=response)
        self.assertEqual(_classify(error), ErrorCategory.MODEL_UNAVAILABLE)

    def test_requests_http_error_500(self):
        from requests.exceptions import HTTPError
        from requests import Response
        response = Response()
        response.status_code = 500
        error = HTTPError("server error", response=response)
        self.assertEqual(_classify(error), ErrorCategory.NETWORK)

    def test_http_read_cancelled(self):
        from src.bounded_http import HTTPReadCancelled
        self.assertEqual(_classify(HTTPReadCancelled("test")), ErrorCategory.CANCELLED)

    def test_http_read_limit(self):
        from src.bounded_http import HTTPReadLimitError
        self.assertEqual(_classify(HTTPReadLimitError("test")), ErrorCategory.RESPONSE_LIMIT)

    def test_http_read_error(self):
        from src.bounded_http import HTTPReadError
        self.assertEqual(_classify(HTTPReadError("test")), ErrorCategory.NETWORK)

    def test_permission_error(self):
        self.assertEqual(_classify(PermissionError("denied")), ErrorCategory.FILE_PERMISSION)

    def test_file_not_found(self):
        self.assertEqual(_classify(FileNotFoundError("missing")), ErrorCategory.FILE_NOT_FOUND)

    def test_value_error(self):
        self.assertEqual(_classify(ValueError("bad")), ErrorCategory.VALIDATION)

    def test_unknown_error(self):
        self.assertEqual(_classify(RuntimeError("boom")), ErrorCategory.UNKNOWN)


class TestPresentError(unittest.TestCase):
    def test_returns_presented_error(self):
        result = present_error(RuntimeError("boom"))
        self.assertIsInstance(result, PresentedError)
        self.assertEqual(result.category, ErrorCategory.UNKNOWN)

    def test_message_is_not_raw_exception(self):
        result = present_error(RuntimeError("sensitive internal detail"))
        self.assertNotIn("sensitive", result.message)
        self.assertNotIn("internal", result.message)

    def test_technical_field_has_type_name(self):
        result = present_error(ValueError("bad"))
        self.assertIn("ValueError", result.technical)

    def test_cancelled_has_no_action(self):
        from src.ai_client import AIRequestCancelled
        result = present_error(AIRequestCancelled("test"))
        self.assertIsNone(result.action)

    def test_network_has_action(self):
        from requests.exceptions import ConnectionError
        result = present_error(ConnectionError("refused"))
        self.assertIsNotNone(result.action)

    def test_str_includes_action(self):
        from requests.exceptions import ConnectionError
        result = present_error(ConnectionError("refused"))
        text = str(result)
        self.assertIn(result.message, text)
        if result.action:
            self.assertIn(result.action, text)

    def test_no_url_in_message(self):
        from src.ai_client import AIProviderError
        result = present_error(AIProviderError("failed to connect to https://api.example.com/v1"))
        self.assertNotIn("api.example.com", result.message)
        self.assertNotIn("https://", result.message)

    def test_no_credentials_in_message(self):
        from src.ai_client import AIProviderError
        result = present_error(AIProviderError("auth failed for key sk-12345abcdef"))
        self.assertNotIn("sk-12345", result.message)
        self.assertNotIn("abcdef", result.message)


class TestPresentCredentialShadow(unittest.TestCase):
    def test_basic(self):
        result = present_credential_shadow("openrouter", "OPENROUTER_API_KEY", True)
        self.assertEqual(result.category, ErrorCategory.CREDENTIAL_SHADOWED)
        self.assertIn("openrouter", result.message)
        self.assertIn("OPENROUTER_API_KEY", result.message)

    def test_can_remove_action(self):
        result = present_credential_shadow("openrouter", "OPENROUTER_API_KEY", True)
        self.assertIsNotNone(result.action)
        self.assertIn("Settings", result.action)

    def test_cannot_remove_action(self):
        result = present_credential_shadow("openrouter", "OPENROUTER_API_KEY", False)
        self.assertIsNotNone(result.action)
        self.assertIn("source", result.action.lower())

    def test_technical_field(self):
        result = present_credential_shadow("openrouter", "OPENROUTER_API_KEY", True)
        self.assertEqual(result.technical, "credential_shadow")


class TestErrorSummary(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(error_summary([]), "")

    def test_single_error(self):
        result = error_summary([ValueError("bad")])
        self.assertIn("input", result.lower())

    def test_multiple_same_category(self):
        result = error_summary([ValueError("a"), ValueError("b")])
        self.assertIn("+1", result)

    def test_multiple_different_categories(self):
        result = error_summary([ValueError("a"), RuntimeError("b")])
        self.assertIn("2", result)
        self.assertIn("First", result)


class TestPresentedErrorDataclass(unittest.TestCase):
    def test_frozen(self):
        import dataclasses
        error = PresentedError(category=ErrorCategory.UNKNOWN, message="test")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            error.message = "changed"

    def test_defaults(self):
        error = PresentedError(category=ErrorCategory.UNKNOWN, message="test")
        self.assertIsNone(error.action)
        self.assertIsNone(error.technical)

    def test_str_without_action(self):
        error = PresentedError(category=ErrorCategory.CANCELLED, message="Cancelled.")
        self.assertEqual(str(error), "Cancelled.")


if __name__ == "__main__":
    unittest.main()
