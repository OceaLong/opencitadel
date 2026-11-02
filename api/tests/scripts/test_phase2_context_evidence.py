from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.acceptance.phase2_evidence import EXPECTED_TESTS, validate_browser
from scripts.acceptance.runner import AcceptanceConfig, AcceptanceRunner, CommandResult


def document():
    return {
        "suites": [
            {
                "specs": [
                    {
                        "title": title,
                        "tests": [
                            {
                                "projectName": project,
                                "expectedStatus": "passed",
                                "results": [{"status": "passed"}],
                            }
                        ],
                    }
                    for title, project in EXPECTED_TESTS.items()
                ]
            }
        ],
        "errors": [],
    }


def test_focus_requires_exact_browser_collection_and_passes_without_retries():
    assert validate_browser(document())[1] == []


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "duplicate",
        "skipped",
        "retry",
        "expected_failure",
        "foreign_project",
        "global_error",
    ],
)
def test_focus_rejects_incomplete_or_ambiguous_evidence(change):
    body = document()
    specs = body["suites"][0]["specs"]
    test = specs[1]["tests"][0]
    if change == "missing":
        specs.pop()
    elif change == "duplicate":
        specs.append(specs[1])
    elif change == "skipped":
        test["results"][0]["status"] = "skipped"
    elif change == "retry":
        test["results"].insert(0, {"status": "failed"})
    elif change == "expected_failure":
        test["expectedStatus"] = "failed"
    elif change == "foreign_project":
        test["projectName"] = "execution"
    else:
        body["errors"].append({"message": "global failure"})
    assert validate_browser(body)[1]


def test_focus_is_explicit_and_release_environment_cannot_inherit_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_PHASE2_CONTEXT", "1")
    config = AcceptanceConfig(
        "phase2-unit-run",
        "phase2-unit-run",
        True,
        tmp_path,
        "http://127.0.0.1:20000",
        "http://127.0.0.1:20002",
    )
    calls = []

    def run(args, **kwargs):
        calls.append(tuple(args))
        return CommandResult(tuple(args), 0, "", "")

    options = {
        "commands": SimpleNamespace(run=run),
        "repository_root": Path(__file__).resolve().parents[3],
        "readiness_probe": lambda _: True,
    }
    release = AcceptanceRunner(config, **options)
    assert "ACCEPTANCE_PHASE2_CONTEXT" not in release._environment
    focus = AcceptanceRunner(
        config, playwright_projects=("execution",), phase2_context=True, **options
    )
    focus._run_playwright()
    assert calls[-1] == ("npx", "playwright", "test", "--config", "phase2-context.config.ts")
    assert focus._environment["ACCEPTANCE_PHASE2_CONTEXT"] == "1"
    with pytest.raises(ValueError, match="only its execution"):
        AcceptanceRunner(config, phase2_context=True, **options)
