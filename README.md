
# DEPRECATED

this repo is deprecated, use https://github.com/imbue-openhost/openhost/tree/main/openhost_app_test_harness instead! 

# OLD README

Test scaffolding for [Openhost](https://github.com/imbue-openhost) apps. Builds an app's Dockerfile, runs it under podman with the mounts and env vars its `openhost.toml` declares, and fronts the container with a mock Openhost router that injects the owner-auth header.

Tests get a real, containerized app to hit.

## Install

```toml
[dependency-groups]
dev = [
    "openhost-test-harness @ git+https://github.com/imbue-openhost/openhost-app-test-harness@main",
]
```

Requires podman on the host.

## Use

```python
# tests/conftest.py
import pytest
from openhost_test_harness import OpenhostStack

@pytest.fixture(scope="session")
def stack():
    with OpenhostStack() as s:  # app_dir found by walking up from the cwd to the nearest openhost.toml
        yield s
```

`app_dir` is optional — by default the harness walks up from the current working directory until it finds an `openhost.toml`. Pass `app_dir=...` to set it explicitly (e.g. when tests run from outside the app tree).

```python
# tests/test_thing.py
import httpx

def test_index(stack):
    r = httpx.get(f"{stack.url}/")
    assert r.status_code == 200
```

- `stack.url` — through the mock router (auth header injected, like a real owner request)
- `stack.app_url` — direct to the container (for service-to-service tests where you want to control headers)

## What gets read from openhost.toml

| Field | Used for |
|---|---|
| `[app].name` | image/container/data-dir naming |
| `[runtime.container].port` | container port to map |
| `[runtime.container].image` | Dockerfile path |
| `[data].sqlite` | sqlite mount + `OPENHOST_SQLITE_*` env |
| `[data].app_data` | app-data mount + `OPENHOST_APP_DATA_DIR` env |
| `[data].app_temp_data` | temp-data mount + `OPENHOST_APP_TEMP_DIR` env |
| `[routing].health_check` | readiness probe path (defaults to `/`) |

## Injected environment variables

The harness injects the same identity/router env vars the real OpenHost router gives an app, so apps boot under test without your `conftest` hand-rolling them:

| Variable | Value under the harness |
|---|---|
| `OPENHOST_APP_NAME` | `[app].name` from the manifest |
| `OPENHOST_APP_ID` | a stable test stand-in |
| `OPENHOST_APP_TOKEN` | a fixed test stand-in (the mock router doesn't verify it) |
| `OPENHOST_ROUTER_URL` | `http://host.containers.internal:<router-port>` — the mock router, reachable from inside the container |
| `OPENHOST_ZONE_DOMAIN` | `zone_domain` (default `localhost`) |
| `OPENHOST_MY_REDIRECT_DOMAIN` | `my.<zone_domain>` |
| `OPENHOST_OWNER_USERNAME` | `owner_username` (default `owner`) |
| `OPENHOST_APP_DATA_DIR`, `OPENHOST_APP_TEMP_DIR`, `OPENHOST_SQLITE_<name>` | set per the manifest's `[data]` requests |

`zone_domain` and `owner_username` are constructor args; anything you pass in `extra_env` overrides the defaults above:

```python
OpenhostStack(zone_domain="example.com", owner_username="alice")
```

Not injected, because the mock doesn't back them: `OPENHOST_APP_ARCHIVE_DIR` (S3/JuiceFS), `OPENHOST_AUTH_PUBLIC_KEY` (JWT signing keys), and the `access_all_data` / `access_vm_data` mounts.

## Pieces

- `OpenhostStack` — high-level fixture above
- `mock_router` — standalone ASGI proxy if you only need the auth-injecting front end
- `container`, `openhost_toml` — building blocks if `OpenhostStack` doesn't fit
