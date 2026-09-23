import unittest
from tools.verify_cached_extraction import verification_failed


class CachedVerificationExitTests(unittest.TestCase):
    def test_source_mismatch_fails_even_when_parsing_succeeds(self):
        self.assertTrue(verification_failed(dict(errors=0, source_checks_matched=33, source_checks_total=35)))

    def test_parser_failure_fails_even_when_supplied_checks_match(self):
        self.assertTrue(verification_failed(dict(errors=1, source_checks_matched=35, source_checks_total=35)))

    def test_matching_source_checks_pass(self):
        self.assertFalse(verification_failed(dict(errors=0, source_checks_matched=35, source_checks_total=35)))
