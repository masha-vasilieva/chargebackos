import os
import unittest
from unittest.mock import patch

from services import error_reporting as er


class ScrubEventTests(unittest.TestCase):
    def _event(self):
        return {
            "message": "user bob@example.com failed",
            "request": {"url": "https://x/?token=secret", "headers": {"authorization": "Bearer abc"}, "cookies": "s=1", "data": "body"},
            "user": {"ip_address": "1.2.3.4", "id": "u1"},
            "extra": {"run": "r1"},
            "breadcrumbs": {"values": [{"message": "q"}]},
            "server_name": "host",
            "exception": {"values": [{
                "type": "ValueError", "module": "builtins", "value": "bad token abc123",
                "stacktrace": {"frames": [{"filename": "main.py", "lineno": 3, "function": "f",
                                           "vars": {"secret": "x"}, "context_line": "x = secret", "abs_path": "/home/u/main.py"}]},
            }]},
        }

    def test_keeps_type_and_location_only(self):
        out = er.scrub_event(self._event())
        text = repr(out)
        for leak in ("bob@example.com", "secret", "Bearer", "1.2.3.4", "abc123", "body", "host", "/home/u"):
            self.assertNotIn(leak, text)
        exc = out["exception"]["values"][0]
        self.assertEqual(exc["type"], "ValueError")
        self.assertEqual(exc["value"], "")
        self.assertEqual(exc["stacktrace"]["frames"][0]["lineno"], 3)
        self.assertEqual(exc["stacktrace"]["frames"][0]["function"], "f")


class EnvironmentTests(unittest.TestCase):
    def test_workspace_is_staging_otherwise_production(self):
        with patch.dict(os.environ, {"REPLIT_DEV_DOMAIN": "x.replit.dev"}):
            self.assertEqual(er.environment_name(), "staging")
        with patch.dict(os.environ, {}):
            os.environ.pop("REPLIT_DEV_DOMAIN", None)
            self.assertEqual(er.environment_name(), "production")


class InitTests(unittest.TestCase):
    def test_off_without_dsn(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(er.sentry_sdk, "init") as init:
            os.environ.pop("SENTRY_DSN", None)
            self.assertFalse(er.init_error_reporting())
            init.assert_not_called()

    def test_on_with_private_settings(self):
        with patch.object(er.sentry_sdk, "init") as init:
            self.assertTrue(er.init_error_reporting("https://k@o0.ingest.de.sentry.io/1"))
        kw = init.call_args.kwargs
        self.assertIs(kw["send_default_pii"], False)
        self.assertIs(kw["include_local_variables"], False)
        self.assertEqual(kw["traces_sample_rate"], 0)
        self.assertEqual(kw["max_breadcrumbs"], 0)
        self.assertEqual(kw["max_request_body_size"], "never")
        self.assertIs(kw["before_send"], er.scrub_event)


class EndToEndTests(unittest.TestCase):
    def test_real_sdk_event_has_no_message_or_request_data(self):
        import sentry_sdk
        from sentry_sdk.transport import Transport

        sent = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                for item in envelope.items:
                    if item.payload.json:
                        sent.append(item.payload.json)

        real_init = sentry_sdk.init
        def init_with_capture(**kw):
            return real_init(transport=Capture(), **kw)

        with patch.object(er.sentry_sdk, "init", init_with_capture):
            self.assertTrue(er.init_error_reporting("https://k@o0.ingest.de.sentry.io/1"))
        try:
            try:
                raise ValueError("card 4242 for bob@example.com")
            except ValueError:
                sentry_sdk.capture_exception()
            sentry_sdk.flush()
        finally:
            sentry_sdk.init()  # turn reporting off again for other tests
        self.assertEqual(len(sent), 1)
        text = repr(sent[0])
        self.assertNotIn("4242", text)
        self.assertNotIn("bob@example.com", text)
        self.assertEqual(sent[0]["exception"]["values"][0]["type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
