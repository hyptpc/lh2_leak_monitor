import os
import unittest
from unittest.mock import patch

from lh2_monitor.credentials import caen_credentials
from lh2_monitor.hardware import caen_operation


class CredentialTests(unittest.TestCase):
    def test_credentials_come_only_from_environment(self):
        with patch.dict(os.environ, {'CAEN_USERNAME': 'test-user',
                                     'CAEN_PASSWORD': 'test-password'}, clear=True):
            self.assertEqual(caen_credentials(), ('test-user', 'test-password'))

    def test_missing_credentials_have_no_builtin_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Set CAEN_USERNAME and CAEN_PASSWORD'):
                caen_credentials()

    def test_partial_configuration_error_does_not_reveal_values(self):
        for values in ({'CAEN_USERNAME': 'private-user'}, {'CAEN_PASSWORD': 'private-password'}):
            with self.subTest(values=list(values)), patch.dict(os.environ, values, clear=True):
                with self.assertRaises(RuntimeError) as error:
                    caen_credentials()
                for value in values.values():
                    self.assertNotIn(value, str(error.exception))

    def test_missing_credentials_fail_before_vendor_import_or_connection(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'Set CAEN_USERNAME and CAEN_PASSWORD'):
                caen_operation({'host': '192.0.2.1', 'slot': 4}, 10, inventory=True)
