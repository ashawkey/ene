"""Output contracts for the resource library CLI."""

from contextlib import nullcontext

import pytest
from rich.console import Console

from ene import library_cli
from ene.library import LibraryError


@pytest.fixture
def library_output(monkeypatch):
    monkeypatch.setattr(library_cli, "conf", {"ene_lib": "test-repo"})
    monkeypatch.setattr(library_cli, "batch_session", lambda repo: nullcontext())
    monkeypatch.setattr(
        library_cli, "Console", lambda **kwargs: Console(width=100, color_system=None, **kwargs)
    )


@pytest.mark.parametrize("kind", ["skill", "persona"])
@pytest.mark.parametrize("all_installed", [False, True])
def test_update_aligned_statuses(library_output, monkeypatch, capsys, kind, all_installed):
    actions = {"alpha": "current", "longer-name": "pushed", "zeta": "pulled"}
    calls = []

    def update(repo, name, resource_kind, *, force):
        calls.append((repo, name, resource_kind, force))
        return actions[name]

    monkeypatch.setattr(library_cli, "update_resource", update)
    monkeypatch.setattr(
        library_cli, "list_local_resources", lambda kind: (dict.fromkeys(reversed(actions)), [])
    )
    argv = ["update", "--kind", kind, "--force"]
    if not all_installed:
        argv.extend(actions)

    assert library_cli.main(argv) == 0
    output = capsys.readouterr()
    assert output.out.splitlines() == [
        "alpha        up-to-date",
        "longer-name  local --> remote",
        "zeta         local <-- remote",
    ]
    assert output.err == ""
    assert calls == [("test-repo", name, kind, True) for name in actions]


def test_update_keeps_completed_rows_on_error(library_output, monkeypatch, capsys):
    def update(repo, name, kind, *, force):
        if name == "beta":
            raise LibraryError("cannot update 'beta': both copies changed")
        return "current"

    monkeypatch.setattr(library_cli, "update_resource", update)

    assert library_cli.main(["update", "alpha", "beta"]) == 1
    output = capsys.readouterr()
    assert output.out.splitlines() == ["alpha  up-to-date"]
    assert "cannot update 'beta': both copies changed" in output.err


def test_update_no_installed_resources(library_output, monkeypatch, capsys):
    monkeypatch.setattr(library_cli, "list_local_resources", lambda kind: ({}, []))

    assert library_cli.main(["update"]) == 0
    output = capsys.readouterr()
    assert output.out == output.err == ""
