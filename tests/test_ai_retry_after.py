"""Retry-After deadlines and 429 retries without network access or sleeps."""

from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, call, patch

from tenacity import wait_none

from src.ai_client import AIClient, _RetryAfterRateLimit


NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc).timestamp()
FUTURE = 'Sun, 04 Oct 2026 12:00:30 GMT'


class RetryAfterParserTests(unittest.TestCase):
    def setUp(self):
        # Avoid session/configuration initialization: parsing only needs the
        # class constants and an observed clock.
        self.client = AIClient.__new__(AIClient)

    def test_numeric_headers_keep_existing_minimum_maximum_and_whitespace_behavior(self):
        for value, expected in ((' 30 ', 30), (30, 30), ('0', 1), ('-1', 1),
                                ('60', 60), ('999999999999999999999999999999', 60)):
            with self.subTest(value=value):
                self.assertEqual(self.client._parse_retry_after(value), expected)

    def test_missing_and_invalid_headers_use_default_integer_delay(self):
        for value in (None, '', 'invalid', '1.5', 'Sun, 99 Oct 2026 12:00:30 GMT',
                      'Sun, 04 Oct 10000 12:00:30 GMT'):
            with self.subTest(value=value), patch('src.ai_client.logger.warning'):
                delay = self.client._parse_retry_after(value)
                self.assertEqual(delay, 15)
                self.assertIsInstance(delay, int)

    def test_standard_http_date_is_measured_against_current_time(self):
        with patch('src.ai_client.time.time', return_value=NOW):
            self.assertEqual(self.client._parse_retry_after(FUTURE), 30)

    def test_fractional_remaining_delay_is_rounded_up(self):
        with patch('src.ai_client.time.time', return_value=NOW + 0.25):
            self.assertEqual(self.client._parse_retry_after(FUTURE), 30)

    def test_obsolete_http_date_formats_and_naive_dates_are_interpreted_in_utc(self):
        for value in ('Sunday, 04-Oct-26 12:00:30 GMT', 'Sun Oct  4 12:00:30 2026'):
            with self.subTest(value=value), patch('src.ai_client.time.time', return_value=NOW):
                self.assertEqual(self.client._parse_retry_after(value), 30)

    def test_explicit_timezone_offset_is_respected(self):
        with patch('src.ai_client.time.time', return_value=NOW):
            self.assertEqual(self.client._parse_retry_after('Sun, 04 Oct 2026 14:00:30 +0200'), 30)

    def test_past_current_and_extreme_future_dates_stay_within_existing_bounds(self):
        cases = (('Sun, 04 Oct 2026 11:59:00 GMT', 1),
                 ('Sun, 04 Oct 2026 12:00:00 GMT', 1),
                 ('Sun, 04 Oct 2026 12:02:00 GMT', 60),
                 ('Fri, 31 Dec 9999 23:59:59 GMT', 60))
        for value, expected in cases:
            with self.subTest(value=value), patch('src.ai_client.time.time', return_value=NOW):
                self.assertEqual(self.client._parse_retry_after(value), expected)

    def test_timestamp_conversion_failure_uses_safe_fallback(self):
        date = Mock(tzinfo=timezone.utc)
        date.timestamp.side_effect = OverflowError('synthetic unsupported timestamp')
        with patch('src.ai_client.parsedate_to_datetime', return_value=date), \
                patch('src.ai_client.logger.warning'):
            self.assertEqual(self.client._parse_retry_after(FUTURE), 15)


class RateLimitRequestTests(unittest.TestCase):
    def setUp(self):
        self.client = AIClient.__new__(AIClient)
        self.client.session = Mock(headers={})
        self.client._local_settings = Mock(return_value={'base_url': ''})
        self.client._strict_local = Mock(return_value=True)
        # Preserve production retry classification and attempt budget while
        # bypassing only Tenacity's real waits.
        self.request = AIClient._make_request.retry_with(wait=wait_none(), sleep=Mock())

    @staticmethod
    def response(code, retry_after=None):
        return Mock(status_code=code, headers={} if retry_after is None else {'Retry-After': retry_after})

    def test_429_http_date_closes_response_sleeps_computed_delay_then_retries(self):
        limited = self.response(429, FUTURE)
        success = self.response(200)
        self.client.session.post.side_effect = [limited, success]
        with patch('src.ai_client.time.time', return_value=NOW + 0.25), \
                patch('src.ai_client.time.sleep') as sleep:
            response = self.request(self.client, 'https://provider.invalid/chat/completions', {'messages': []})
        self.assertIs(response, success)
        self.assertEqual(self.client.session.post.call_count, 2)
        limited.close.assert_called_once()
        sleep.assert_called_once_with(30)
        success.raise_for_status.assert_called_once()

    def test_repeated_429_still_exhausts_three_attempt_budget(self):
        limited = [self.response(429, FUTURE) for _ in range(3)]
        self.client.session.post.side_effect = limited
        with patch('src.ai_client.time.time', return_value=NOW), \
                patch('src.ai_client.time.sleep') as sleep:
            with self.assertRaises(_RetryAfterRateLimit):
                self.request(self.client, 'https://provider.invalid/chat/completions', {'messages': []})
        self.assertEqual(self.client.session.post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(30), call(30), call(30)])
        for response in limited:
            response.close.assert_called_once()
