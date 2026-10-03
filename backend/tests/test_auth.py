"""Identity comes from the verified session, never from the request body."""

import unittest
from dataclasses import replace

from backend.config import settings
from backend.core import auth
from backend.core.auth import AuthError


def supabase_settings(**overrides):
    values = dict(auth_mode="supabase", supabase_url="https://project.supabase.test", supabase_key="anon-key")
    values.update(overrides)
    return replace(settings, **values)


class BearerTests(unittest.TestCase):
    def test_missing_header(self):
        with self.assertRaises(AuthError) as caught:
            auth.extract_bearer(None)
        self.assertEqual(caught.exception.status_code, 401)

    def test_wrong_scheme_or_empty_token(self):
        for header in ("Basic abc", "Bearer", "Bearer   "):
            with self.assertRaises(AuthError):
                auth.extract_bearer(header)

    def test_token_is_extracted(self):
        self.assertEqual(auth.extract_bearer("Bearer abc.def"), "abc.def")
        self.assertEqual(auth.extract_bearer("bearer  abc.def "), "abc.def")


class VerifyTokenTests(unittest.TestCase):
    def setUp(self):
        auth.clear_cache()
        self.addCleanup(auth.clear_cache)
        self.calls = []

    def fetch(self, url, key, token):
        self.calls.append((url, key, token))
        return {
            "id": "7d1c-user",
            "email": "Asha.Surveyor@Example.TEST",
            "user_metadata": {"full_name": "Asha Rao"},
        }

    def test_valid_session_yields_the_user(self):
        user = auth.verify_token("token-1", supabase_settings(), fetch=self.fetch)
        self.assertEqual(user.user_id, "7d1c-user")
        self.assertEqual(user.email, "asha.surveyor@example.test")
        self.assertEqual(user.name, "Asha Rao")
        self.assertFalse(user.is_dev)
        self.assertEqual(self.calls, [("https://project.supabase.test", "anon-key", "token-1")])

    def test_result_is_cached_briefly(self):
        clock = [100.0]
        config = supabase_settings()
        auth.verify_token("token-1", config, fetch=self.fetch, now=lambda: clock[0])
        auth.verify_token("token-1", config, fetch=self.fetch, now=lambda: clock[0])
        self.assertEqual(len(self.calls), 1)
        clock[0] += 3600
        auth.verify_token("token-1", config, fetch=self.fetch, now=lambda: clock[0])
        self.assertEqual(len(self.calls), 2)

    def test_rejected_session_is_not_cached(self):
        def reject(url, key, token):
            self.calls.append(token)
            raise AuthError("Session is invalid or has expired. Sign in again.")

        for _ in range(2):
            with self.assertRaises(AuthError):
                auth.verify_token("stale", supabase_settings(), fetch=reject)
        self.assertEqual(self.calls, ["stale", "stale"])

    def test_payload_without_identity_is_refused(self):
        with self.assertRaises(AuthError):
            auth.verify_token("t", supabase_settings(), fetch=lambda *_: {"id": "x"})

    def test_unconfigured_server_says_so(self):
        with self.assertRaises(AuthError) as caught:
            auth.verify_token("t", supabase_settings(supabase_url="", supabase_key=""), fetch=self.fetch)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.calls, [])


class AuthenticateTests(unittest.TestCase):
    def test_supabase_mode_requires_a_token(self):
        with self.assertRaises(AuthError):
            auth.authenticate(None, supabase_settings())

    def test_off_mode_is_an_explicit_development_identity(self):
        user = auth.authenticate(None, replace(settings, auth_mode="off"))
        self.assertTrue(user.is_dev)
        self.assertEqual(user.user_id, "local-dev")


if __name__ == "__main__":
    unittest.main()
