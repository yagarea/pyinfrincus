import logging
import os
import shlex
import subprocess
import tempfile
import time
from random import randbytes

import pytest

log = logging.getLogger(__name__)


#####
# These tests can only be run if you have the incus cli set up
#####

# Environment variable is set locally to a second, non default incus server.
REMOTES = ["", os.environ["PYINFRINCUS_REMOTE_SERVER"]]

def run(*args, **kwargs):
    cmd = args[0] if args else kwargs.get("args", [])
    log.info("$ %s", shlex.join(cmd))
    result = subprocess.run(*args, **kwargs)
    if result.stdout:
        log.debug(f"|> \n{result.stdout}")
    return result


@pytest.fixture(scope="module", params=REMOTES, ids=lambda r: r.rstrip(":") or "local")
def container(request):
    remote = request.param
    name = f"pyinfrincus-{randbytes(4).hex()}"
    qualified = f"{remote}{name}"

    run(["incus", "launch", "images:debian/13", qualified], check=True)

    # Wait for container to be RUNNING
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = run(
            ["incus", "info", qualified],
            capture_output=True,
            text=True,
        )
        if "Status: RUNNING" in result.stdout:
            break
        time.sleep(0.5)
    else:
        raise TimeoutError(f"Container {qualified} not RUNNING after 20s\n{result}")

    yield qualified
    run(["incus", "delete", "--force", qualified], check=True)


def run_pyinfra(container, *args):
    return run(
        ["pyinfra", "-y", f"@incus/{container}", *args],
        capture_output=True,
        text=True,
    )


def test_container_exists(container):
    result = run(["incus", "info", container])
    assert result.returncode == 0


def test_linux_distribution_fact(container):
    result = run_pyinfra(container, "fact", "server.LinuxDistribution")
    assert result.returncode == 0, f"pyinfra failed:\n{result.stderr}"
    assert "Debian" in result.stderr, f"Expected 'Debian' in output:\n{result.stderr}"


def test_file_lifecycle(container):
    remote_path = "/tmp/pyinfrincus-test"

    with tempfile.TemporaryDirectory() as tmpdir:
        upload_path = os.path.join(tmpdir, "upload.txt")
        download_path = os.path.join(tmpdir, "download.txt")

        # Create a local file with "Hello"
        with open(upload_path, "w") as f:
            f.write("Hello")

        # Upload it
        result = run_pyinfra(container, "files.put", f"src={upload_path}", f"dest={remote_path}")
        assert result.returncode == 0, f"files.put failed:\n{result.stderr}"

        # Append " World"
        result = run_pyinfra(
            container, "exec", "--",
            f"echo -n ' World' >> {remote_path}",
        )
        assert result.returncode == 0, f"exec failed:\n{result.stderr}"

        # Download and verify
        result = run_pyinfra(container, "files.get", f"src={remote_path}", f"dest={download_path}")
        assert result.returncode == 0, f"files.get failed:\n{result.stderr}"

        # incus file pull preserves the source's uid/gid/mode, so the
        # root-owned file lands unreadable to our user.
        os.chmod(download_path, 0o644)
        contents = open(download_path).read()
        assert contents == "Hello World", f"Expected 'Hello World', got: {contents!r}"
