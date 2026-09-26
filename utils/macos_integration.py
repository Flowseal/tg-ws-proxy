"""macOS login startup and staged application updates (no GUI dependencies)."""
from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request

BUNDLE_ID = 'com.tgwsproxy.app'
ASSET_NAME = 'TgWsProxy_macos_universal.dmg'


def app_bundle():
    if not getattr(sys, 'frozen', False):
        return None
    executable = Path(sys.executable).resolve()
    for parent in executable.parents:
        if parent.suffix == '.app' and (parent / 'Contents/Info.plist').is_file():
            return parent
    return None


def startup_path():
    return Path.home() / 'Library/LaunchAgents/com.tgwsproxy.app.plist'


def startup_enabled(app, path=None):
    path = path or startup_path()
    try:
        data = plistlib.loads(path.read_bytes())
        return (data.get('Label') == BUNDLE_ID and data.get('RunAtLoad') is True
                and data.get('ProgramArguments') == ['/usr/bin/open', '-g', str(app)])
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def set_startup(app, enabled, path=None):
    """Persist a per-user login agent; do not start a second instance now."""
    path = path or startup_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    if not app or not (app / 'Contents/Info.plist').is_file():
        raise ValueError('Install the application before enabling login startup')
    if str(app).startswith('/Volumes/') or '/AppTranslocation/' in str(app):
        raise ValueError('Move the application to Applications first')
    data = {'Label': BUNDLE_ID, 'ProgramArguments': ['/usr/bin/open', '-g', str(app)],
            'RunAtLoad': True, 'LimitLoadToSessionType': 'Aqua'}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.tgws-startup-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            plistlib.dump(data, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def update_asset(status):
    for asset in status.get('assets') or []:
        if asset.get('name') == ASSET_NAME:
            url = asset.get('url', '')
            parsed = urlparse(url)
            digest = asset.get('digest', '')
            if (parsed.scheme == 'https' and parsed.netloc == 'github.com'
                    and parsed.path.startswith('/Flowseal/tg-ws-proxy/releases/download/')
                    and re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest)):
                return asset
    raise ValueError('No macOS release with a SHA-256 digest is available')


def download_release(asset, destination, opener):
    digest = hashlib.sha256()
    with opener.open(Request(asset['url']), timeout=30) as response, destination.open('wb') as out:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ValueError('Release image exceeds 512 MB')
            digest.update(chunk)
            out.write(chunk)
    if 'sha256:' + digest.hexdigest() != asset['digest'].lower():
        raise ValueError('Release SHA-256 mismatch')


def validate_bundle(app):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    executable = info.get('CFBundleExecutable', '')
    if (info.get('CFBundleIdentifier') != BUNDLE_ID or not executable
            or Path(executable).name != executable):
        raise ValueError('Unexpected application in release image')
    binary = app / 'Contents/MacOS' / executable
    binary.resolve().relative_to(app.resolve())
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('Release executable is missing')


def cleanup_old_updates(app, keep=1):
    """Keep the newest valid recovery bundle and remove older ones."""
    parent = app.parent.resolve()
    candidates = []
    for path in app.parent.glob('.tgws-update-*'):
        try:
            if path.is_symlink() or not path.is_dir() or path.resolve().parent != parent:
                continue
            if not (path / 'install.log').is_file():
                continue
            validate_bundle(path / 'previous.app')
            candidates.append((path.stat().st_mtime_ns, path))
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
    candidates.sort(reverse=True)
    for _, path in candidates[max(0, keep):]:
        shutil.rmtree(path)


# Arguments are passed separately, never interpolated into shell source.
UPDATE_HELPER = '''#!/bin/sh
set -eu
pid=$1
target=$2
stage=$3
backup=$4
count=0
while kill -0 "$pid" 2>/dev/null; do
    count=$((count + 1))
    if [ "$count" -ge 60 ]; then
        echo 'Application did not exit; installation cancelled' >&2
        exit 1
    fi
    sleep 1
done
if [ -e "$backup" ]; then exit 1; fi
if ! /bin/mv "$target" "$backup"; then exit 1; fi
if ! /bin/mv "$stage" "$target"; then
    /bin/mv "$backup" "$target"
    /usr/bin/open -n "$target"
    exit 1
fi
if ! /usr/bin/open -n "$target"; then
    /bin/mv "$target" "$stage"
    /bin/mv "$backup" "$target"
    /usr/bin/open -n "$target"
    exit 1
fi
# Keep the previous bundle in this private directory for manual recovery.
'''


def prepare_update(app, status, opener, progress=lambda key: None):
    """Download and stage before exiting; return private staging directory."""
    validate_bundle(app)
    if str(app).startswith('/Volumes/') or '/AppTranslocation/' in str(app):
        raise ValueError('Move the application to Applications before updating')
    asset = update_asset(status)
    # Same filesystem makes the final rename atomic. Permission failures occur now.
    work = Path(tempfile.mkdtemp(prefix='.tgws-update-', dir=app.parent))
    mount = work / 'mount'
    mount.mkdir()
    attached = False
    try:
        progress('update.downloading')
        download_release(asset, work / 'release.dmg', opener)
        progress('update.mac_preparing')
        subprocess.run(['/usr/bin/hdiutil', 'attach', str(work / 'release.dmg'),
                        '-readonly', '-nobrowse', '-plist', '-mountpoint', str(mount)],
                       check=True, capture_output=True, timeout=120)
        attached = True
        source = mount / 'TG WS Proxy.app'
        validate_bundle(source)
        staged = work / 'next.app'
        subprocess.run(['/usr/bin/ditto', str(source), str(staged)], check=True,
                       capture_output=True, timeout=180)
        validate_bundle(staged)
        subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(staged)],
                       check=True, capture_output=True, timeout=60)
        subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], check=True,
                       capture_output=True, timeout=60)
        attached = False
        (work / 'release.dmg').unlink()
        (work / 'install.sh').write_text(UPDATE_HELPER)
        return work
    except BaseException:
        if attached or os.path.ismount(mount):
            # Never recursively remove a directory while the disk image is mounted.
            result = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                                    capture_output=True, timeout=60)
            if result.returncode:
                raise RuntimeError('Could not detach update image: ' + str(mount)) from None
        shutil.rmtree(work)
        raise


def launch_installer(app, work):
    with (work / 'install.log').open('ab') as log:
        return subprocess.Popen(
            ['/bin/sh', str(work / 'install.sh'), str(os.getpid()), str(app),
             str(work / 'next.app'), str(work / 'previous.app')],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        )
