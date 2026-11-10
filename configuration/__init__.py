"""Shared TOML configuration for the proxy and its deployment installer."""
import argparse
import os
import ipaddress
from pathlib import Path


# section.key: (type, optional inclusive bounds / allowed values)
FIELDS = {
    'server.bind_address': (str, None), 'server.port': (int, (1, 65535)),
    'server.public_host': (str, None), 'server.public_port': (int, (1, 65535)),
    'access.secret': (str, None),
    'fake_tls.domain': (str, None), 'fake_tls.drs': (bool, None),
    'fake_tls.server_hello_split': (bool, None),
    'fake_tls.split_delay_ms': (int, (0, 1000)),
    'fake_tls.split_jitter_ms': (int, (0, 1000)),
    'masking.mode': (str, ('local', 'external')),
    'masking.backend_host': (str, None), 'masking.port': (int, (1, 65535)),
    'masking.certificate': (str, None), 'masking.private_key': (str, None),
    'deployment.mode': (str, ('host', 'docker')),
    'deployment.state_dir': (str, None), 'deployment.tcpmss': (int, (0, 1460)),
    'deployment.nfqws': (bool, None), 'deployment.nfqws_ttl': (int, (1, 255)),
    'upstream.dc_ips': (list, None), 'upstream.pool_size': (int, (0, 65535)),
    'upstream.cfproxy': (bool, None), 'upstream.h2': (bool, None),
    'upstream.secure': (bool, None), 'upstream.domains': (list, None),
    'upstream.workers': (list, None),
}
PATHS = {'masking.certificate', 'masking.private_key', 'deployment.state_dir'}
COMMON = {
    'server.port': 'port', 'server.public_host': 'public_host',
    'server.public_port': 'public_port', 'access.secret': 'secret',
    'fake_tls.drs': 'no_fake_tls_drs',
    'fake_tls.server_hello_split': 'no_fake_tls_split',
    'fake_tls.split_delay_ms': 'fake_tls_split_delay_ms',
    'fake_tls.split_jitter_ms': 'fake_tls_split_jitter_ms',
    'upstream.dc_ips': 'dc_ip', 'upstream.pool_size': 'pool_size',
    'upstream.cfproxy': 'no_cfproxy', 'upstream.h2': 'no_h2',
    'upstream.secure': 'no_secure', 'upstream.domains': 'cfproxy_domain',
    'upstream.workers': 'cfproxy_worker_domain',
}
ENV = {
    'HOST': 'server.bind_address', 'PORT': 'server.port',
    'PUBLIC_HOST': 'server.public_host', 'PUBLIC_PORT': 'server.public_port',
    'SECRET': 'access.secret', 'FAKE_TLS_DOMAIN': 'fake_tls.domain',
    'FAKE_TLS_DRS': 'fake_tls.drs', 'FAKE_TLS_SPLIT': 'fake_tls.server_hello_split',
    'FAKE_TLS_SPLIT_DELAY_MS': 'fake_tls.split_delay_ms',
    'FAKE_TLS_SPLIT_JITTER_MS': 'fake_tls.split_jitter_ms',
    'FAKE_TLS_MASK_HOST': 'masking.backend_host', 'FAKE_TLS_MASK_PORT': 'masking.port',
    'MASKING_MODE': 'masking.mode', 'CERT': 'masking.certificate', 'KEY': 'masking.private_key',
    'MODE': 'deployment.mode', 'STATE_DIR': 'deployment.state_dir',
    'TCPMSS_MSS': 'deployment.tcpmss', 'NFQWS': 'deployment.nfqws',
    'NFQWS_TTL': 'deployment.nfqws_ttl', 'DC_IPS': 'upstream.dc_ips',
    'POOL_SIZE': 'upstream.pool_size', 'CF_DOMAIN': 'upstream.domains',
    'CF_WORKER': 'upstream.workers', 'NO_H2': 'upstream.h2',
    'NO_CFPROXY': 'upstream.cfproxy', 'NO_SECURE': 'upstream.secure',
}


def boolean(value, name):
    value = value.strip().lower()
    if value not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
        raise ValueError(name + ' must be a boolean')
    return value in ('true', '1', 'yes', 'on')


def validate(key, value):
    expected, bounds = FIELDS[key]
    if type(value) is not expected:
        raise ValueError(key + ' must be ' + expected.__name__)
    strings = value if expected is list else [value] if expected is str else []
    if any(not isinstance(item, str) or any(c in item for c in '\r\n\0') for item in strings):
        raise ValueError(key + ' must contain strings without line breaks or NUL')
    if expected is int and bounds and not bounds[0] <= value <= bounds[1]:
        raise ValueError(key + ' is outside ' + str(bounds))
    if expected is str and bounds and value not in bounds:
        raise ValueError(key + ' must be one of ' + ', '.join(bounds))
    if key == 'deployment.tcpmss' and value and value < 64:
        raise ValueError(key + ' must be 0 or 64..1460')
    if key == 'upstream.dc_ips':
        try:
            for item in value:
                dc, address = item.split(':', 1)
                int(dc)
                ipaddress.IPv4Address(address)
        except ValueError:
            raise ValueError(key + ' must contain DC:IPv4 entries') from None


def read_settings(path):
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise ValueError('TOML requires Python 3.11+ or tomli: python3 -m pip install tomli') from None
    path = Path(path).expanduser().resolve()
    try:
        with path.open('rb') as stream:
            document = tomllib.load(stream)
    except tomllib.TOMLDecodeError:
        # Parser errors may quote a line containing a secret.
        raise ValueError('Invalid TOML syntax in ' + str(path)) from None
    result = {}
    for section, values in document.items():
        if section not in {key.split('.')[0] for key in FIELDS} or not isinstance(values, dict):
            raise ValueError('Unknown configuration section: ' + section)
        for name, value in values.items():
            key = section + '.' + name
            if key not in FIELDS:
                raise ValueError('Unknown configuration key: ' + key)
            validate(key, value)
            if key in PATHS and value:
                target = Path(value).expanduser()
                value = str((path.parent / target).resolve())
            result[key] = value
    return result


def apply_sources(parser, argv, *, setup=False, env=None):
    """CLI > environment > TOML > parser defaults; never search for config files."""
    env = os.environ if env is None else env
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument('--config', default=env.get('TG_WS_PROXY_CONFIG'))
    selected, _ = probe.parse_known_args(argv)
    if any(arg in ('-h', '--help') for arg in argv):
        return
    try:
        values = read_settings(selected.config) if selected.config else {}
        for suffix, key in ENV.items():
            raw = env.get('TG_WS_PROXY_' + suffix)
            if raw is None or (not raw and FIELDS[key][0] is not list):
                continue
            kind = FIELDS[key][0]
            if kind is bool:
                value = boolean(raw, 'TG_WS_PROXY_' + suffix)
                if suffix.startswith('NO_'):
                    value = not value
            elif kind is int:
                try:
                    value = int(raw)
                except ValueError:
                    raise ValueError('TG_WS_PROXY_' + suffix + ' must be an integer') from None
            else:
                value = raw.split() if kind is list else raw
            validate(key, value)
            values[key] = value
        if setup and 'TG_WS_PROXY_TCPMSS' in env:
            if not boolean(env['TG_WS_PROXY_TCPMSS'], 'TG_WS_PROXY_TCPMSS'):
                values['deployment.tcpmss'] = 0
            elif not values.get('deployment.tcpmss'):
                values['deployment.tcpmss'] = 88
        mapping = dict(COMMON)
        if setup:
            mapping.update({'server.bind_address': 'bind_address', 'fake_tls.domain': 'domain',
                            'masking.mode': 'masking_mode', 'masking.backend_host': 'mask_host',
                            'masking.port': 'mask_port', 'masking.certificate': 'cert',
                            'masking.private_key': 'key', 'deployment.mode': 'mode',
                            'deployment.state_dir': 'state_dir', 'deployment.tcpmss': 'tcpmss',
                            'deployment.nfqws': 'no_nfqws', 'deployment.nfqws_ttl': 'nfqws_ttl'})
        else:
            mapping.update({'server.bind_address': 'host', 'fake_tls.domain': 'fake_tls_domain',
                            'masking.backend_host': 'fake_tls_mask_host',
                            'masking.port': 'fake_tls_mask_port'})
            # A local site is provisioned only by the installer, not the runtime.
            if values.get('masking.mode') == 'local':
                values.setdefault('masking.backend_host', '127.0.0.1')
                values.setdefault('masking.port', 8443)
        defaults = {}
        for key, dest in mapping.items():
            if key not in values:
                continue
            value = values[key]
            if dest.startswith('no_'):
                value = not value
            if setup and dest in ('cert', 'key', 'state_dir'):
                value = Path(value) if value else None
            defaults[dest] = value
        # argparse append actions append to list defaults. Explicit CLI lists
        # replace lower-priority lists instead of accidentally combining them.
        for option in ('--dc-ip', '--cfproxy-domain', '--cfproxy-worker-domain'):
            if any(arg == option or arg.startswith(option + '=') for arg in argv):
                defaults[option[2:].replace('-', '_')] = None
        parser.set_defaults(**defaults)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
