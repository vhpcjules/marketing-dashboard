"""Repository hygiene: the boring guards.

None of these tests touch a number. They protect the things that break a
deploy or leak a credential without any test in the numeric suite noticing:
the v1 bookmarks, the ignore list, the CI contract, and the absence of
tokens in tracked source and data.

Everything is read relative to this file so the suite still works when
test_mutation.py copies the tree to a temp directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The six v1 pages leadership has bookmarked, and where each lands now.
# Changing a destination is a decision; dropping a line is a regression.
V1_REDIRECTS = {
    "/Leadership_Dashboard.html": "/executive",
    "/Budget_Performance.html": "/executive",
    "/YoY_Performance.html": "/executive",
    "/Social_Media_Performance.html": "/marketing-ops",
    "/Marketing_Activity.html": "/marketing-ops",
    "/Marketing_Pipeline_for_Sales.html": "/sales",
}

# Common credential shapes. Kept deliberately narrow so a hit is always a
# real problem, never something to allowlist around.
TOKEN_PATTERNS = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "openai_style_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "github_pat": re.compile(r"ghp_[A-Za-z0-9]{36}"),
    "bearer_token": re.compile(r"Bearer [A-Za-z0-9._\-]{20,}"),
}

SCANNED_SUFFIXES = {".py", ".sql", ".json", ".md", ".txt", ".csv", ".toml", ".yaml", ".yml"}


def _redirect_rules() -> dict[str, tuple[str, str]]:
    """Parse public/_redirects into {source: (destination, status)}."""
    rules = {}
    for line in (REPO / "public" / "_redirects").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        assert len(parts) == 3, f"malformed redirect line: {line!r}"
        rules[parts[0]] = (parts[1], parts[2])
    return rules


class TestRedirects:
    @pytest.mark.parametrize("source,dest", sorted(V1_REDIRECTS.items()))
    def test_v1_url_is_permanently_redirected(self, source, dest):
        rules = _redirect_rules()
        assert source in rules, f"v1 bookmark {source} has no redirect"
        got_dest, status = rules[source]
        assert got_dest == dest
        assert status == "301", f"{source} must be permanent (301), got {status}"

    def test_root_serves_executive_in_place(self):
        assert _redirect_rules()["/"] == ("/executive", "200")


class TestGitignore:
    @pytest.fixture
    def patterns(self) -> list[str]:
        lines = (REPO / ".gitignore").read_text().splitlines()
        return [l.strip() for l in lines if l.strip() and not l.startswith("#")]

    def test_built_output_is_ignored(self, patterns):
        assert any(p.rstrip("/") == "dist" for p in patterns)

    @pytest.mark.parametrize("kept", ["data", "reports"])
    def test_historical_record_is_not_ignored(self, patterns, kept):
        # Neither the directory itself nor anything that would swallow it.
        offenders = [p for p in patterns
                     if p.strip("/") == kept or p.startswith(kept + "/") or p in ("*", "*.json", "*.md")]
        assert not offenders, f"{kept}/ is the historical record and must stay tracked: {offenders}"


class TestCI:
    @pytest.fixture
    def ci(self) -> str:
        return (REPO / ".github" / "workflows" / "ci.yml").read_text()

    def test_ci_runs_pytest_over_tests(self, ci):
        assert re.search(r"python3 -m pytest tests\b", ci)

    def test_ci_does_not_skip_the_mutation_suite(self, ci):
        assert "--ignore=tests/test_mutation.py" not in ci

    def test_ci_runs_on_pull_requests_and_main(self, ci):
        assert "pull_request:" in ci
        assert re.search(r"branches:\s*\[main\]", ci)

    def test_deploy_check_is_main_only(self):
        text = (REPO / ".github" / "workflows" / "deploy-check.yml").read_text()
        assert "pull_request" not in text.replace("# ", "")  # not even as a trigger in a comment
        assert re.search(r"branches:\s*\[main\]", text)


class TestNoCredentials:
    def _files(self):
        for root in ("data", "src"):
            for p in (REPO / root).rglob("*"):
                if p.is_file() and p.suffix in SCANNED_SUFFIXES and "__pycache__" not in p.parts:
                    yield p

    @pytest.mark.parametrize("name,pattern", sorted(TOKEN_PATTERNS.items()))
    def test_no_token_shaped_strings(self, name, pattern):
        hits = []
        for p in self._files():
            try:
                text = p.read_text(errors="replace")
            except OSError:
                continue
            for m in pattern.finditer(text):
                hits.append(f"{p.relative_to(REPO)}: ...{m.group(0)[:12]}...")
        assert not hits, f"{name} pattern matched tracked files:\n" + "\n".join(hits)
