"""tests/ui — the audit made permanent (plan §V, Wave 5).

These are REAL end-to-end tests: a session-scoped uvicorn server on an
ephemeral port with its own throwaway SQLite DB, seeded like dev, driven
by Playwright. Every finding from the UI/UX audit (C-1 … C-14) that the
redesign fixed gets a regression test here — the same discipline the
backend suite applies to F-01 defects.

Isolation from the unit suite: every module carries `pytestmark =
pytest.mark.ui`; the unit CI job runs `-m "not ui"`, this directory is
run explicitly (`pytest tests/ui -m ui`). Markers are registered in
pytest.ini so an unmarked run never silently skips.
"""
import base64
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parents[2]
AXE_SRC = (REPO / "scripts" / "vendor" / "axe.min.js").read_text()

# NOTE: each test module declares its own `pytestmark = pytest.mark.ui`
# (conftest-level pytestmark does not propagate). pytest.ini's default
# `-m "not ui"` keeps the unit suite fast; CI's ui-e2e job runs
# `pytest tests/ui -m ui`.

# The server env is set HERE, before the app process ever boots, so the
# throwaway DB/storage/live-dir can never collide with dev or CI state.
_DB = f"sqlite:///./ui_e2e_{secrets.token_hex(4)}.db"
_ROOT = Path(f"./ui_e2e_artifacts_{secrets.token_hex(4)}")
SERVER_ENV = {
    **os.environ,
    "APP_ENV": "test",
    "DATABASE_URL": _DB,
    "JWT_SECRET": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "MASTER_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    "BOOTSTRAP_ADMIN_EMAIL": "admin@test.com",
    "BOOTSTRAP_ADMIN_PASSWORD": "UiE2e-Pw-123456!",
    "STORAGE_LOCAL_ROOT": str(_ROOT / "storage"),
    "LOCALSIGHT_LIVE_DIR": str(_ROOT / "live"),
    "AI_IDENTITY_RECOGNITION_ENABLED": "false",
    "SSRF_ALLOWLIST": "192.168.99.0/24",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server():
    """Boot the real app under the real ASGI server; yield (base, admin_pw).

    _boot.py seeds the throwaway DB first (the demo dataset), then serves —
    one subprocess owns the app so the runtime is built exactly once.
    """
    port = _free_port()
    env = dict(SERVER_ENV)
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "_boot.py"), str(port)],
        cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    # health-wait: uvicorn boots in <2s; ffmpeg-agnostic /health/live is the
    # readiness signal the probes already use. Host/port are in-process
    # values (kernel-assigned ephemeral port) — no operator input.
    for _ in range(100):
        try:
            if httpx.get(f"{base}/health/live", timeout=1).status_code == 200:
                break
        except (httpx.HTTPError, ConnectionError, OSError):
            time.sleep(0.2)
    else:
        proc.kill()
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        raise RuntimeError(f"ui-e2e server never became healthy:\n{out[-2000:]}")

    yield {"base": base, "admin_password": env["BOOTSTRAP_ADMIN_PASSWORD"]}

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    # Throw away the DB + artifacts: nothing from a UI run persists.
    import shutil
    shutil.rmtree(_ROOT, ignore_errors=True)
    for p in REPO.glob("ui_e2e_*.db"):
        p.unlink(missing_ok=True)


@pytest.fixture()
def page(server, playwright):
    """One fresh page per test, console/page errors captured into a list."""
    browser = playwright.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    errors: list = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg._lv_errors = errors  # test-local channel
    yield pg
    assert errors == [], f"page errors: {errors}"
    ctx.close()
    browser.close()


@pytest.fixture()
def logged_in(page, server):
    """A page already authenticated as the bootstrap admin."""
    page.goto(server["base"] + "/")
    page.fill("#email", "admin@test.com")
    page.fill("#password", server["admin_password"])
    page.click("button[type=submit]")
    page.wait_for_selector("#app:not(.hidden)")
    return page


@pytest.fixture()
def axe(page):
    """Run axe-core against the CURRENT page state. Injects lazily so a
    test that navigates after fixture-time still gets a working axe."""
    def run():
        page.evaluate(AXE_SRC)
        return page.evaluate("async () => window.axe.run(document)")
    return run


def api_call(base: str, path: str, method: str = "GET", body=None, token: str | None = None):
    """Tiny JSON client for the in-process test server (admin login etc.)."""
    r = httpx.request(
        method, f"{base}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}"} if token else {},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


@pytest.fixture(scope="session")
def admin_token(server):
    return api_call(server["base"], "/api/auth/login", "POST",
                    {"email": "admin@test.com",
                     "password": server["admin_password"]})["access_token"]


# ── CI diagnosis: emit every failure as a workflow annotation ──────────────
# GitHub surfaces ::error:: lines from the log on the run summary page,
# which is readable WITHOUT log-download auth. One pytest failure = one
# annotation naming the test + the assert, so a red ui-e2e run answers
# "which test, what drift" from the public run page alone.

def _annotation_for(report):
    if report.when != "call" or report.passed:
        return None
    node = getattr(report, "nodeid", "")
    long = getattr(report, "longrepr", None)
    msg = str(long).replace("\n", " | ")[:350]
    return f"::{report.outcome} file=tests/ui/{node}::LOCALSIGHT-CI {node} -> {msg}"


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    line = _annotation_for(report)
    if line:
        print(line, flush=True)


def pytest_terminal_summary(terminalreporter):
    """Print a machine- and human-readable one-block summary of every
    non-passed test: name + the LAST assertion line. This lands in the
    log tail (always visible in the Actions UI without expanding) and
    doubles as workflow-command annotations."""
    failed = terminalreporter.stats.get("failed", []) + terminalreporter.stats.get("error", [])
    if not failed:
        return
    print("\n==== LOCALSIGHT-CI SUMMARY ====", flush=True)
    for r in failed:
        node = getattr(r, "nodeid", "?")
        longrepr = str(getattr(r, "longrepr", "")).splitlines()
        # take the whole assertion tail: the drift-band localization lives
        # on the message lines AFTER the bare 'assert' line.
        idx = next((i for i, ln in enumerate(reversed(longrepr)) if ln.startswith("AssertionError") or "assert" in ln), None)
        tail = longrepr[-(idx + 1):] if idx is not None and idx else longrepr[-3:]
        msg = " | ".join(x.strip() for x in tail if x.strip())[:600]
        print(f"FAIL {node} :: {msg}", flush=True)
        print(f"::error title=ui-e2e failure::FAIL {node} :: {msg[:250]}", flush=True)
    print("==== END SUMMARY ====", flush=True)
