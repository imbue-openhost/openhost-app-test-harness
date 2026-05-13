# openhost-app-test-harness

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
from pathlib import Path
import pytest
from openhost_test_harness import OpenhostStack

@pytest.fixture(scope="session")
def stack():
    with OpenhostStack(app_dir=Path(__file__).resolve().parent.parent) as s:
        yield s
```

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
| `[routing].health_check` | readiness probe path (defaults to `/`) |

## Pieces

- `OpenhostStack` — high-level fixture above
- `mock_router` — standalone ASGI proxy if you only need the auth-injecting front end
- `container`, `openhost_toml` — building blocks if `OpenhostStack` doesn't fit
