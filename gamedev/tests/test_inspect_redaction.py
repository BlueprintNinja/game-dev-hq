import unittest

from gamedev.inspect_redaction import redact


class InspectRedactionTests(unittest.TestCase):
    def test_common_credentials_are_removed_without_erasing_normal_content(self):
        text = 'hello\nAuthorization: Bearer abc123\napi_key="sensitive"\nordinary work'
        result = redact(text)
        self.assertIn("ordinary work", result)
        self.assertNotIn("abc123", result)
        self.assertNotIn("sensitive", result)

    def test_multiline_private_keys_are_removed(self):
        result = redact("before\n-----BEGIN PRIVATE KEY-----\nsecret-value\n-----END PRIVATE KEY-----\nafter")
        self.assertNotIn("secret-value", result)
        self.assertIn("after", result)

    def test_json_quoted_values_and_unterminated_key_are_removed(self):
        text = '{"password": "value with spaces", "client_secret": "another-secret"}'
        result = redact(text)
        self.assertNotIn("value with spaces", result)
        self.assertNotIn("another-secret", result)
        self.assertNotIn("key material", redact("-----BEGIN PRIVATE KEY-----\nkey material"))

    def test_json_authorization_and_access_keys_are_removed(self):
        result = redact('{"Authorization": "Bearer sampletoken", "key": "AKIAABCDEFGHIJKLMNOP"}')
        self.assertNotIn("sampletoken", result)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", result)
