"""Expected sweep events stay distinct from genuine browser errors; no browser is launched."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "studio-check-ui.cjs"


def run(code):
    node = shutil.which("node")
    if not node:
        if os.environ.get("STUDIO_TESTS_NEED_NODE") == "1":
            pytest.fail("node is required for the browser-checker regression tests")
        pytest.skip("node is not installed")
    subprocess.run(
        [node, "-e", 'const assert = require("node:assert/strict"); '
         'const {pageErrorType, confirmDownload} = require(process.argv[1]);\n' + code, str(SCRIPT)],
        check=True, capture_output=True, text=True, timeout=20,
    )


def test_only_exact_errors_from_a_blocked_write_are_expected():
    run("""
      const context = {tab: "Home", click: "+ timeline"};
      const writes = [{...context, method: "POST", url: "/api/projects"}];
      const blocked = "studio-check-ui: writes are blocked";
      assert.equal(pageErrorType(blocked, context, writes), "blocked-write");
      assert.equal(pageErrorType(blocked, context, []), "pageerror");
      assert.equal(pageErrorType(blocked, {...context, tab: "Editor"}, writes), "pageerror");
      assert.equal(pageErrorType(blocked, {...context, click: "Other"}, writes), "pageerror");
      assert.equal(pageErrorType(blocked + ": another failure", context, writes), "pageerror");
      assert.equal(pageErrorType("Cannot read properties of undefined", context, writes), "pageerror");
    """)


def test_download_confirmation_matches_only_the_aborted_get():
    run("""
      const url = "http://127.0.0.1:7871/api/export/timeline/p1.zip";
      const aborted = {type: "requestfailed", method: "GET", error: "net::ERR_ABORTED", url};
      const events = [
        {...aborted},
        {...aborted, url: url + "?other"},
        {...aborted, method: "POST"},
        {...aborted, error: "net::ERR_FAILED"},
        {...aborted, type: "pageerror"},
      ];
      confirmDownload(events, url);
      assert.deepEqual(events.map(e => e.type),
        ["download", "requestfailed", "requestfailed", "requestfailed", "pageerror"]);
      confirmDownload(events, url);
      assert.equal(events[0].type, "download");
    """)


def test_an_unconfirmed_export_abort_remains_a_failed_request():
    run("""
      const event = {type: "requestfailed", method: "GET", error: "net::ERR_ABORTED",
        url: "http://127.0.0.1:7871/api/export/timeline/p1.edl"};
      confirmDownload([event], "http://127.0.0.1:7871/files/unrelated.mp4");
      assert.equal(event.type, "requestfailed");
    """)
