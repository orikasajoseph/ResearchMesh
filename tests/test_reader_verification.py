"""离线验收夹具检查：容忍格式空格，不放宽证据内容要求。"""
import unittest

from verify_reader_single import fixture_supports_match


class SingleVerificationTests(unittest.TestCase):
    def test_same_fixture_claims_with_punctuation_space(self):
        for value in (
            "Fixture method. Fixture conclusion. Fixture limitation.",
            "Fixture method . Fixture conclusion . Fixture limitation .",
        ):
            with self.subTest(value=value):
                self.assertTrue(fixture_supports_match(value))

    def test_no_list_or_empty_string_compatibility(self):
        for value in (None, ["method", "findings"], "", "  "):
            with self.subTest(value=value):
                self.assertFalse(fixture_supports_match(value))

    def test_missing_or_different_claim_still_fails(self):
        for value in (
            "method findings limitations",
            "Fixture method. Fixture conclusion.",
            "Fixture method. Different conclusion. Fixture limitation.",
            "Fixture method (pqac-1234abcd). Fixture conclusion. Fixture limitation.",
        ):
            with self.subTest(value=value):
                self.assertFalse(fixture_supports_match(value))
