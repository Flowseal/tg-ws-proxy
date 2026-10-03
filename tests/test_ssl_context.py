import os
import shutil
import ssl
import tempfile
import unittest
from unittest import mock

import certifi

from proxy.utils import create_ssl_context


class CreateSslContextTest(unittest.TestCase):
    def test_uses_certifi_bundle_when_it_is_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = os.path.join(directory, 'cacert.pem')
            shutil.copyfile(certifi.where(), bundle)
            with mock.patch('proxy.utils.certifi.where', return_value=bundle), \
                    mock.patch(
                        'proxy.utils.ssl.create_default_context',
                        wraps=ssl.create_default_context) as create:
                context = create_ssl_context()
                insecure = create_ssl_context(check_hostname=False)

            self.assertIsInstance(context, ssl.SSLContext)
            self.assertTrue(context.check_hostname)
            self.assertFalse(insecure.check_hostname)
            self.assertEqual(create.call_count, 2)
            for call in create.call_args_list:
                self.assertEqual(call, mock.call(cafile=bundle))

    def test_falls_back_to_distro_bundle_when_certifi_is_not_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = os.path.join(directory, 'ca-bundle.pem')
            shutil.copyfile(certifi.where(), bundle)
            dangling = os.path.join(directory, 'dangling.pem')
            os.symlink(os.path.join(directory, 'absent.pem'), dangling)
            with mock.patch('proxy.utils.certifi.where', return_value=dangling), \
                    mock.patch('proxy.utils._DISTRO_CA_CANDIDATES', (bundle,)), \
                    mock.patch(
                        'proxy.utils.ssl.create_default_context',
                        wraps=ssl.create_default_context) as create:
                context = create_ssl_context()

            self.assertIsInstance(context, ssl.SSLContext)
            create.assert_called_once_with(cafile=bundle)

    def test_omits_cafile_when_no_bundle_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, 'missing.pem')
            with mock.patch('proxy.utils.certifi.where', return_value=missing), \
                    mock.patch('proxy.utils._DISTRO_CA_CANDIDATES', ()), \
                    mock.patch(
                        'proxy.utils.ssl.create_default_context',
                        wraps=ssl.create_default_context) as create:
                context = create_ssl_context()

            self.assertIsInstance(context, ssl.SSLContext)
            create.assert_called_once_with()
