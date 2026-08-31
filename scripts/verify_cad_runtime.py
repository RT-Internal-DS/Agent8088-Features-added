#!/usr/bin/env python3
"""Perform a real worker/export/reopen check for the isolated CAD runtime."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


def _resolve_viewer_root(viewer_root: Path) -> Path:
    """Accept either a concrete release or the installer's manifest directory."""
    viewer_root = viewer_root.resolve()
    if (viewer_root / "dist" / "index.html").is_file():
        return viewer_root
    manifest = viewer_root / "current.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        release_root = Path(str(payload.get("root") or "")).resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise RuntimeError(f"CAD Viewer manifest is missing or invalid: {manifest}") from exc
    try:
        release_root.relative_to((viewer_root / "releases").resolve())
    except ValueError as exc:
        raise RuntimeError("CAD Viewer manifest points outside its managed releases") from exc
    return release_root


def _viewer_url(workspace: Path, artifact: Path, port: int) -> str:
    directory = workspace.as_posix()
    if os.name == "nt" and not directory.startswith("/"):
        directory = "/" + directory
    path = urllib.parse.quote(directory, safe="/:")
    query = urllib.parse.urlencode({"file": artifact.relative_to(workspace).as_posix()})
    return f"http://127.0.0.1:{port}{path}?{query}"


def _viewer_browser_smoke(url: str, artifact: Path) -> None:
    """Load and render the generated model through the real browser client."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1024, "height": 720})
            page_errors: list[str] = []
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.goto(url, wait_until="networkidle", timeout=90_000)
            page.wait_for_selector("canvas", state="attached", timeout=30_000)
            page.wait_for_function(
                "name => document.body.innerText.toLowerCase().includes(name)",
                arg=artifact.stem.lower(),
                timeout=45_000,
            )
            if artifact.name.lower() not in page.title().lower():
                raise RuntimeError("CAD Viewer title did not identify the generated artifact")
            if page.locator("canvas").count() < 1:
                raise RuntimeError("CAD Viewer did not create a rendering canvas")
            if page_errors:
                raise RuntimeError("CAD Viewer browser error: " + page_errors[0][:500])
        finally:
            browser.close()


def _viewer_smoke(viewer_root: Path, workspace: Path, artifact: Path) -> None:
    viewer_root = _resolve_viewer_root(viewer_root)
    required = (viewer_root / "dist" / "index.html", viewer_root / "server_py" / "server.py")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("CAD Viewer runtime is incomplete: " + ", ".join(missing))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(viewer_root), env.get("PYTHONPATH", "")) if item
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "server_py.server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=workspace, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        server_url = f"http://127.0.0.1:{port}/__cad/server"
        deadline = time.monotonic() + 45
        payload = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"CAD Viewer exited during startup ({process.returncode})")
            try:
                with urllib.request.urlopen(server_url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(0.2)
        if not isinstance(payload, dict) or payload.get("app") != "cad-viewer":
            raise RuntimeError("CAD Viewer did not expose its expected loopback API")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            html = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
        if "<html" not in html.lower():
            raise RuntimeError("CAD Viewer did not serve its browser application")
        query = urllib.parse.urlencode({"dir": str(workspace), "file": artifact.name})
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/__cad/catalog?{query}", timeout=10
        ) as response:
            catalog = json.loads(response.read().decode("utf-8"))
        entries = catalog.get("entries") if isinstance(catalog, dict) else None
        if not isinstance(entries, list) or not any(
            str(item.get("file") or "").lower().endswith(artifact.name.lower())
            for item in entries if isinstance(item, dict)
        ):
            raise RuntimeError("CAD Viewer catalog did not include the generated STEP artifact")
        _viewer_browser_smoke(_viewer_url(workspace, artifact, port), artifact)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer-root", type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    worker = root / "src" / "agent8088" / "cad_worker.py"
    # Windows antivirus/indexers can briefly retain a handle after Chromium or
    # OpenCascade exits. That must not turn a successful installer smoke test
    # into a failure; the OS temp directory remains safe to reap later.
    with tempfile.TemporaryDirectory(
        prefix="agent8088-cad-smoke-", ignore_cleanup_errors=True
    ) as raw:
        workspace = Path(raw)
        output = workspace / "box.step"
        request = workspace / "request.json"
        # Exercise Agent8088's real owned-process JSON-RPC client, not merely a
        # direct build123d import. This catches protocol changes, missing MCP
        # tools, Windows subprocess-host incompatibilities, and export wiring.
        sys.path.insert(0, str(root / "src"))
        from agent8088 import cad_mcp

        try:
            cad_mcp.RUNTIME.begin(
                workspace, "box", {"length": 2, "width": 3, "height": 5},
                {"solid_count": 1, "bounding_box": [2, 3, 5]},
            )
            built = cad_mcp.RUNTIME.execute(
                "from build123d import *\n"
                "part = Box(2, 3, 5)\n"
                "show(part, 'box')\n",
                checkpoint="box_complete",
            )
            if "error" in built.lower():
                raise RuntimeError(built)
            measured = cad_mcp.RUNTIME.measure("box")
            if '"volume": 30' not in measured.replace(".0", ""):
                raise RuntimeError("build123d-mcp returned an unexpected box volume: " + measured)
            gate = cad_mcp.RUNTIME.validate("box")
            if '"passes_gate": true' not in gate.lower():
                raise RuntimeError("build123d-mcp validity gate failed: " + gate)
            cad_mcp.RUNTIME.export("box", ["step", "stl"], "box")
        finally:
            cad_mcp.RUNTIME.close()
        if not output.is_file() or not (workspace / "box.stl").is_file():
            raise RuntimeError("supervised build123d-mcp export did not create STEP and STL")
        report = workspace / "box.report.json"
        preview = workspace / "box.preview.png"
        request.write_text(
            json.dumps({
                "action": "validate",
                "input": str(output),
                "report": str(report),
                "preview": str(preview),
                "workspace": str(workspace),
            }),
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-I", str(worker), str(request)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            print(completed.stdout, file=sys.stderr)
            print(completed.stderr, file=sys.stderr)
            return completed.returncode

        from build123d import import_step

        reopened = import_step(output)
        assert len(reopened.solids()) == 1
        assert abs(reopened.volume - 30) < 1e-6
        assert report.stat().st_size > 0
        assert preview.stat().st_size > 0
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["validity"]["ok"] is True
        _viewer_smoke(args.viewer_root, workspace, output)
        print("CAD MCP/worker/export/reopen/render/viewer round trip: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
