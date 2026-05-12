"""Parse an app's openhost.toml manifest.

Only the fields the harness needs are modeled; unknown fields are ignored.
"""

import tomllib
from pathlib import Path

import attr


@attr.frozen
class AppSection:
    name: str
    version: str | None = None
    description: str | None = None


@attr.frozen
class RuntimeSection:
    image: str = "Dockerfile"
    port: int = 8080


@attr.frozen
class DataSection:
    sqlite: tuple[str, ...] = ()
    app_data: bool = False


@attr.frozen
class RoutingSection:
    health_check: str | None = None
    public_paths: tuple[str, ...] = ()


@attr.frozen
class OpenhostManifest:
    app: AppSection
    runtime: RuntimeSection
    data: DataSection
    routing: RoutingSection

    @classmethod
    def load(cls, path: Path) -> "OpenhostManifest":
        with open(path, "rb") as f:
            raw = tomllib.load(f)

        app_raw = raw.get("app", {})
        runtime_raw = raw.get("runtime", {}).get("container", {})
        data_raw = raw.get("data", {})
        routing_raw = raw.get("routing", {})

        return cls(
            app=AppSection(
                name=app_raw["name"],
                version=app_raw.get("version"),
                description=app_raw.get("description"),
            ),
            runtime=RuntimeSection(
                image=runtime_raw.get("image", "Dockerfile"),
                port=int(runtime_raw.get("port", 8080)),
            ),
            data=DataSection(
                sqlite=tuple(data_raw.get("sqlite", ())),
                app_data=bool(data_raw.get("app_data", False)),
            ),
            routing=RoutingSection(
                health_check=routing_raw.get("health_check"),
                public_paths=tuple(routing_raw.get("public_paths", ())),
            ),
        )

    def env_for_data_mount(self, host_data_dir: Path) -> dict[str, str]:
        """Env vars an app expects when its data dir is mounted at /data/app_data/<name>.

        The container-side path follows Openhost's convention. ``host_data_dir``
        is unused at the env-var level (the mount is set up separately) but
        accepted here so the helper composes naturally with mount setup.
        """
        del host_data_dir  # mount is configured by the caller; only paths matter here
        container_dir = f"/data/app_data/{self.app.name}"
        env: dict[str, str] = {}
        if self.data.app_data:
            env["OPENHOST_APP_DATA_DIR"] = container_dir
        for db in self.data.sqlite:
            env[f"OPENHOST_SQLITE_{db.upper()}"] = f"{container_dir}/sqlite/{db}.db"
        return env
