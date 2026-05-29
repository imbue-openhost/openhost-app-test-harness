"""High-level fixture: build, run, and route an Openhost app for tests.

Typical use in a project's ``conftest.py``::

    import pytest
    from openhost_test_harness import OpenhostStack

    @pytest.fixture(scope="session")
    def app_url():
        # app_dir is discovered by walking up from the cwd to the nearest
        # openhost.toml; pass app_dir=... explicitly to override.
        with OpenhostStack() as stack:
            yield stack.url
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import TracebackType
from typing import Self

import attr

from openhost_test_harness.container import (
    build_image,
    container_logs,
    free_port,
    start_container,
    stop_container,
    wait_for_http,
)
from openhost_test_harness.openhost_toml import (
    OpenhostManifest,
    find_manifest_dir,
)

logger = logging.getLogger(__name__)


def _resolve_app_dir(value: Path | str | None) -> Path:
    """Coerce the ``app_dir`` argument to a ``Path``, discovering it from the cwd when not given."""
    if value is None:
        return find_manifest_dir()
    return Path(value)


def _bind_mount_temp_base() -> str | None:
    """Base directory for the harness's bind-mounted data/temp dirs.

    The dirs created here are bind-mounted into the app container, so they must
    live somewhere the container runtime can actually see. On macOS podman runs
    inside a VM that only shares a fixed set of host paths (by default /Users,
    /private and /var/folders); a sandboxed or relocated ``TMPDIR`` (e.g.
    ``/tmp/claude-501``) is not shared, so mounting a dir created there fails
    with ``statfs ... no such file or directory``.

    To make the mount work regardless of the ambient ``TMPDIR``, pin the base
    under ``$HOME`` (shared as /Users) on macOS. Elsewhere containers run on the
    host kernel with no VM boundary, so the system default tempdir is fine and
    we return ``None`` to let ``tempfile`` choose it.
    """
    if sys.platform != "darwin":
        return None
    base = Path.home() / ".cache" / "openhost-test-harness" / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


@attr.define
class OpenhostStack:
    """Build, start, and front an Openhost app with a mock router for tests.

    Use as a context manager. Prefer constructing via the keyword form so
    later additions to optional fields stay backwards-compatible::

        with OpenhostStack(rebuild=False) as stack:
            run_tests_against(stack.url)

    ``app_dir`` defaults to the nearest directory containing an ``openhost.toml``,
    found by walking up from the current working directory. Pass it explicitly to
    override (e.g. when tests run from outside the app tree).
    """

    app_dir: Path = attr.field(default=None, converter=_resolve_app_dir)
    rebuild: bool = True
    extra_env: dict[str, str] = attr.field(factory=dict)
    health_path: str | None = None
    """Override openhost.toml's [routing].health_check. Defaults to '/' if neither is set."""
    image_name: str | None = None
    container_name: str | None = None
    readiness_timeout: float = 30.0

    _manifest: OpenhostManifest = attr.field(init=False)
    _data_dir: Path = attr.field(init=False)
    _temp_dir: Path | None = attr.field(init=False, default=None)
    _app_host_port: int = attr.field(init=False)
    _router_port: int = attr.field(init=False)
    _router_proc: subprocess.Popen | None = attr.field(init=False, default=None)
    _resolved_image_name: str = attr.field(init=False)
    _resolved_container_name: str = attr.field(init=False)

    def __attrs_post_init__(self) -> None:
        self._manifest = OpenhostManifest.load(self.app_dir / "openhost.toml")
        slug = self._manifest.app.name
        self._resolved_image_name = self.image_name or f"openhost-test-{slug}"
        self._resolved_container_name = self.container_name or f"openhost-test-{slug}-container"

    # ─── Properties ───

    @property
    def url(self) -> str:
        """Router URL — what tests should hit. Includes owner-auth header injection."""
        return f"http://localhost:{self._router_port}"

    @property
    def app_url(self) -> str:
        """Direct container URL, bypassing the router. Useful for service-to-service tests."""
        return f"http://localhost:{self._app_host_port}"

    @property
    def manifest(self) -> OpenhostManifest:
        return self._manifest

    @property
    def data_dir(self) -> Path:
        """Host-side directory mounted into the container at /data/app_data/<app-name>."""
        return self._data_dir

    @property
    def temp_data_dir(self) -> Path | None:
        """Host-side dir mounted at /data/app_temp_data/<app-name>, or None.

        Present only when the manifest requests ``[data].app_temp_data``.
        """
        return self._temp_dir

    # ─── Lifecycle ───

    def __enter__(self) -> Self:
        try:
            self._setup()
        except Exception:
            self._teardown()
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._teardown()

    def _setup(self) -> None:
        if self.rebuild:
            build_image(self.app_dir, self._resolved_image_name, dockerfile=self._manifest.runtime.image)

        tmp_base = _bind_mount_temp_base()
        self._data_dir = Path(tempfile.mkdtemp(prefix=f"openhost-test-{self._manifest.app.name}-", dir=tmp_base))
        if self._manifest.data.sqlite:
            (self._data_dir / "sqlite").mkdir(parents=True, exist_ok=True)

        self._app_host_port = free_port()
        self._router_port = free_port()

        env = dict(self._manifest.env_for_data_mount(self._data_dir))
        env.update(self.extra_env)

        mounts = {self._data_dir: f"/data/app_data/{self._manifest.app.name}"}

        if self._manifest.data.app_temp_data:
            self._temp_dir = Path(tempfile.mkdtemp(prefix=f"openhost-test-{self._manifest.app.name}-temp-", dir=tmp_base))
            mounts[self._temp_dir] = f"/data/app_temp_data/{self._manifest.app.name}"

        start_container(
            image_name=self._resolved_image_name,
            container_name=self._resolved_container_name,
            host_port=self._app_host_port,
            container_port=self._manifest.runtime.port,
            mounts=mounts,
            env=env,
        )

        health = self.health_path or self._manifest.routing.health_check or "/"
        try:
            wait_for_http(
                f"http://localhost:{self._app_host_port}{health}",
                timeout=self.readiness_timeout,
                label="container",
                headers={"X-OpenHost-Is-Owner": "true"},
            )
        except RuntimeError:
            logger.error("Container failed to become ready. Logs:\n%s", container_logs(self._resolved_container_name))
            raise

        self._start_router()
        wait_for_http(
            f"{self.url}{health}",
            timeout=self.readiness_timeout,
            label="router",
        )

    def _start_router(self) -> None:
        env = {
            **os.environ,
            "UPSTREAM_HOST": "localhost",
            "UPSTREAM_PORT": str(self._app_host_port),
            "ROUTER_PORT": str(self._router_port),
        }
        self._router_proc = subprocess.Popen(
            [sys.executable, "-m", "openhost_test_harness.mock_router"],
            env=env,
        )

    def _teardown(self) -> None:
        if self._router_proc is not None:
            self._router_proc.terminate()
            try:
                self._router_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._router_proc.kill()
            self._router_proc = None

        stop_container(self._resolved_container_name)

        for attr_name in ("_data_dir", "_temp_dir"):
            d = getattr(self, attr_name, None)
            if d is not None and d.exists():
                shutil.rmtree(d, ignore_errors=True)
