"""Podman lifecycle helpers for the OpenHost test harness."""

import logging
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


def free_port() -> int:
    """Return an unused TCP port. Small race between check and bind is acceptable."""
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def build_image(app_dir: Path, image_name: str, dockerfile: str = "Dockerfile") -> None:
    cmd = ["podman", "build", "-t", image_name, "-f", dockerfile, str(app_dir)]
    logger.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def stop_container(container_name: str) -> None:
    subprocess.run(
        ["podman", "rm", "-f", container_name],
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


def start_container(
    image_name: str,
    container_name: str,
    host_port: int,
    container_port: int,
    mounts: dict[Path, str] | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Start a detached container, replacing any existing one with the same name."""
    stop_container(container_name)
    cmd = [
        "podman",
        "run",
        "-d",
        "--name",
        container_name,
        "-p",
        f"{host_port}:{container_port}",
    ]
    for host_path, container_path in (mounts or {}).items():
        cmd += ["-v", f"{host_path}:{container_path}:Z"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd.append(image_name)
    logger.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True)


def wait_for_http(
    url: str,
    timeout: float = 30.0,
    label: str = "service",
    headers: dict[str, str] | None = None,
) -> None:
    """Poll ``url`` until any response with status < 500 is received."""
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status < 500:
                    logger.info("%s ready at %s (status %d)", label, url, resp.status)
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"{label} did not become ready at {url} within {timeout}s (last error: {last_err})")


def container_logs(container_name: str) -> str:
    result = subprocess.run(
        ["podman", "logs", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr
