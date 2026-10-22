"""Distinguish task identifiers from credential prefixes without using real keys."""
import unittest
from scripts.prepare_team_sync import classify


class SecretPatternTests(unittest.TestCase):
    def test_task_and_other_embedded_identifiers_are_not_key_prefixes(self):
        for name in ("task-", "mask-", "flask-"):
            with self.subTest(name=name):
                self.assertEqual(classify("doc.md", "ID: `" + name + "a" * 32 + "`", []),
                                 "CANDIDATE_REQUIRES_DIFF_REVIEW")

    def test_delimited_key_and_token_prefixes_still_block_upload(self):
        for prefix in ("sk-", "sk-proj-", "ghp_", "gho_", "github_pat_"):
            fake = prefix + "x" * 30
            for text in (fake, 'KEY="' + fake + '"', "Bearer " + fake, "`" + fake + "`"):
                with self.subTest(prefix=prefix, text=text):
                    self.assertEqual(classify("doc.md", text, []), "POSSIBLE_SECRET_DO_NOT_UPLOAD")

    def test_exact_local_secret_match_is_not_weakened_by_prefix_boundary(self):
        fake = "example_private_value" * 2
        self.assertEqual(classify("doc.md", "embedded" + fake, [fake]), "POSSIBLE_SECRET_DO_NOT_UPLOAD")

    def test_private_key_marker_and_env_file_still_block_upload(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertEqual(classify("doc.md", marker, []), "POSSIBLE_SECRET_DO_NOT_UPLOAD")
        self.assertEqual(classify(".env", "", []), "DO_NOT_UPLOAD")
