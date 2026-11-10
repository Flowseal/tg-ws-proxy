"""Translate container/systemd environment to CLI arguments without a shell."""
import os
import argparse
from pathlib import Path
import subprocess
import sys


def build_args(env, extra=()):
    has_config = bool(env.get('TG_WS_PROXY_CONFIG')) or any(
        arg == '--config' or arg.startswith('--config=') for arg in extra)
    args = []
    if not has_config:
        args = ['--host', env.get('TG_WS_PROXY_HOST', '0.0.0.0'),
                '--port', env.get('TG_WS_PROXY_PORT', '1443')]
    values = {
        'SECRET': '--secret', 'CF_WORKER': '--cfproxy-worker-domain',
        'CONFIG': '--config', 'PUBLIC_HOST': '--public-host', 'PUBLIC_PORT': '--public-port',
        'CF_DOMAIN': '--cfproxy-domain', 'POOL_SIZE': '--pool-size',
        'FAKE_TLS_DOMAIN': '--fake-tls-domain',
        'FAKE_TLS_MASK_HOST': '--fake-tls-mask-host',
        'FAKE_TLS_MASK_PORT': '--fake-tls-mask-port',
        'FAKE_TLS_SPLIT_DELAY_MS': '--fake-tls-split-delay-ms',
        'FAKE_TLS_SPLIT_JITTER_MS': '--fake-tls-split-jitter-ms',
    }
    for name, flag in values.items():
        value = env.get('TG_WS_PROXY_' + name, '')
        if value:
            args.extend([flag, value])
    if 'TG_WS_PROXY_DC_IPS' in env:
        entries = env['TG_WS_PROXY_DC_IPS'].split()
        if not entries:
            args.append('--dc-ip')
        for entry in entries:
            args.extend(['--dc-ip', entry])
    flags = {
        'NO_SECURE': ('--no-secure', True),
        'NO_H2': ('--no-h2', True),
        'NO_CFPROXY': ('--no-cfproxy', True),
        'FAKE_TLS_DRS': ('--no-fake-tls-drs', False),
        'FAKE_TLS_SPLIT': ('--no-fake-tls-split', False),
    }
    for name, (flag, enabled) in flags.items():
        key = 'TG_WS_PROXY_' + name
        if key not in env:
            continue
        value = env[key].strip().lower()
        if value not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
            raise ValueError(key + ' must be a boolean')
        if (value in ('true', '1', 'yes', 'on')) == enabled:
            args.append(flag)
    return args + list(extra)


def configure_docker_tcpmss(env, args):
    value = env.get('TG_WS_PROXY_TCPMSS', 'false').strip().lower()
    if value in ('false', '0', 'no', 'off'):
        return
    if value not in ('true', '1', 'yes', 'on'):
        raise ValueError('TG_WS_PROXY_TCPMSS must be a boolean')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Docker TCPMSS bootstrap requires Linux, --user 0:0 and NET_ADMIN')
    import pwd

    app = pwd.getpwnam('app')
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--port', type=int, default=1443)
    parsed, _ = parser.parse_known_args(args)
    if any(arg == '--config' or arg.startswith('--config=') for arg in args) or env.get('TG_WS_PROXY_CONFIG'):
        # Use the same source order as the listener for the firewall port.
        from configuration import apply_sources
        apply_sources(parser, args, env=env)
        parsed, _ = parser.parse_known_args(args)
    helper = Path(__file__).resolve().parent / 'linux/dpi.py'
    subprocess.run([sys.executable, str(helper), 'apply', '--tcpmss-only',
                    '--port', str(parsed.port), '--mss', env.get('TG_WS_PROXY_TCPMSS_MSS', '88'),
                    '--ipv6-optional'], check=True)
    # The listener runs without root/NET_ADMIN after the namespace is configured.
    os.setgroups([])
    os.setgid(app.pw_gid)
    os.setuid(app.pw_uid)


def main():
    try:
        args = build_args(os.environ, sys.argv[1:])
        if not any(flag in args for flag in ('--help', '-h')):
            configure_docker_tcpmss(os.environ, args)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
    os.execv(sys.executable, [sys.executable, '-u', '-m', 'proxy.tg_ws_proxy'] + args)


if __name__ == '__main__':
    main()
