import argparse
import unittest

from proxy.tg_ws_proxy import _log_max_mb_type, _port_type


class PortTypeTest(unittest.TestCase):
    def test_accepts_valid_port(self):
        self.assertEqual(_port_type('1443'), 1443)

    def test_accepts_boundary_ports(self):
        self.assertEqual(_port_type('1'), 1)
        self.assertEqual(_port_type('65535'), 65535)

    def test_rejects_zero(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _port_type('0')

    def test_rejects_out_of_range(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _port_type('65536')

    def test_rejects_non_integer(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _port_type('abc')


class LogMaxMbTypeTest(unittest.TestCase):
    def test_accepts_positive_float(self):
        self.assertEqual(_log_max_mb_type('2.5'), 2.5)

    def test_rejects_non_positive(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _log_max_mb_type('0')
        with self.assertRaises(argparse.ArgumentTypeError):
            _log_max_mb_type('-1')

    def test_rejects_nan_and_infinity(self):
        for value in ('nan', 'inf', '-inf'):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    _log_max_mb_type(value)

    def test_rejects_non_numeric(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            _log_max_mb_type('abc')
