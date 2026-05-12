"""High-level fixture: build, run, and route an Openhost app for tests.

Typical use in a project's ``conftest.py``::

    import pytest
    from pathlib import Path
    from openhost_test_harness import OpenhostStack

    @pytest.fixture(scope="session")
    def app_url():
        with OpenhostStack(app_dir=Path(__file__).resolve().parent.parent) as stack:
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
from openhost_test_harness.openhost_toml import OpenhostManifest

logger = logging.getLogger(__name__)


@attr.define
class OpenhostStack:
    """Build, start, and front an Openhost app with a mock router for tests.

    Use as a context manager. Prefer constructing via the keyword form so
    later additions to optional fields stay backwards-compatible::

        with OpenhostStack(app_dir=Path("..."), rebuild=False) as stack:
            run_tests_against(stack.url)
    """

    app_dir: Path = attr.field(converter=Path)
    rebuild: bool = True
    extra_env: dict[str, str] = attr.field(factory=dict)
    health_path: str | None = None
    """Override openhost.toml's [routing].health_check. Defaults to '/' if neither is set."""
    image_name: str | None = None
    container_name: str | None = None
    readiness_timeout: float = 30.0

    _manifest: OpenhostManifest = attr.field(init=False)
    _data_dir: Path = attr.field(init=False)
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

        self._data_dir = Path(tempfile.mkdtemp(prefix=f"openhost-test-{self._manifest.app.name}-"))
        if self._manifest.data.sqlite:
            (self._data_dir / "sqlite").mkdir(parents=True, exist_ok=True)

        self._app_host_port = free_port()
        self._router_port = free_port()

        env = dict(self._manifest.env_for_data_mount(self._data_dir))
        env.update(self.extra_env)

        mounts = {self._data_dir: f"/data/app_data/{self._manifest.app.name}"}

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

        data_dir = getattr(self, "_data_dir", None)
        if data_dir is not None and data_dir.exists():
            shutil.rmtree(data_dir, ignore_errors=True)
