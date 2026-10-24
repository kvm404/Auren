"""auren update against throwaway git repos: a "GitHub" repo, Auren's clone of it, and an install.sh stub
that records what it installed. Nothing touches the real install or services."""

import json
import subprocess

import pytest

from auren import notice, update

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]

STUB = """#!/usr/bin/env bash
set -e
commit="$(git rev-parse HEAD)"
echo "$commit" >>"$INSTALLS"
[[ ! -f fail ]] || exit 3
printf '{"commit": "%s", "mode": "install", "committed": 0, "remote": "%s"}' "$commit" \\
  "$(git remote get-url origin)" >"$INSTALL_INFO"
"""


def git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text="x", subject=None, release=True):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", subject or f"change {name}")
    if release:
        tags = git(repo, "tag", "--list", "v0.1.*").splitlines()
        number = max((int(t.rsplit(".", 1)[1]) for t in tags), default=-1) + 1
        git(repo, "tag", f"v0.1.{number}")
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    remote = tmp_path / "github"
    remote.mkdir()
    git(remote, "init", "-q")
    (remote / "install.sh").write_text(STUB)
    first = commit(remote, "omarchy-plugin/Panel.qml", "v1", "first")
    info = tmp_path / "share" / "install.json"
    info.parent.mkdir()
    for name, path in {"INSTALL_INFO": info, "SOURCE": tmp_path / "cache" / "source",
                       "STATUS": tmp_path / "run" / "update.json", "LOG": tmp_path / "state" / "update.log",
                       "NOTIFIED": tmp_path / "state" / "notified", "LOCK": tmp_path / "run" / "update.lock",
                       "STATE_FILE": tmp_path / "run" / "state.json"}.items():
        monkeypatch.setattr(update, name, path)
    monkeypatch.setenv("INSTALLS", str(tmp_path / "installs"))
    monkeypatch.setenv("INSTALL_INFO", str(info))
    calls, shell = [], []
    active = {"auren": True, "auren-mascot": False}
    enabled = {"auren": True, "auren-mascot": True}
    broken = set()                                     # actions that fail, like "stop"

    def systemctl(*args):
        calls.append(args)
        if args[0] == "is-active":
            return 0 if active[args[-1]] else 3
        if args[0] == "is-enabled":
            return 0 if enabled[args[-1]] else 1
        if args[0] in ("enable", "disable"):
            for u in args[1:]:
                enabled[u] = args[0] == "enable"
            return 0
        if args[0] in broken:
            return 1
        for u in args[1:]:
            active[u] = args[0] in ("start", "restart")
        return 0
    def latest_release(remote):
        from pathlib import Path
        try:
            tags = git(Path(remote), "tag", "--list", "v0.1.*").splitlines()
        except subprocess.CalledProcessError as e:
            raise update.UpdateError("Couldn't reach GitHub") from e
        if not tags:
            raise update.UpdateError("No published release is available")
        tag = max(tags, key=lambda t: int(t.rsplit(".", 1)[1]))
        return {"tag": tag, "name": tag, "url": f"https://github.com/example/auren/releases/tag/{tag}"}
    monkeypatch.setattr(update, "_latest_release", latest_release)
    monkeypatch.setattr(update, "_systemctl", systemctl)
    monkeypatch.setattr(update, "_restart_shell", lambda: shell.append(True))

    class Env:
        pass
    e = Env()
    e.remote, e.first, e.info, e.calls, e.active, e.shell, e.tmp = remote, first, info, calls, active, shell, tmp_path
    e.broken, e.enabled = broken, enabled
    e.installs = lambda: (tmp_path / "installs").read_text().split() if (tmp_path / "installs").exists() else []

    def install_info(commit, mode="install", **more):
        info.write_text(json.dumps({"commit": commit, "branch": "main", "committed": 0, "remote": str(remote),
                                    "source": str(remote), "mode": mode, **more}))
    e.install_info = install_info
    return e


def test_up_to_date(env):
    env.install_info(env.first)
    r = update.check()
    assert r["state"] == "current" and r["installed"]["short"] == env.first[:7]
    assert update.status() == r                                    # the panel reads the same


def test_new_commits_are_listed(env):
    env.install_info(env.first)
    commit(env.remote, "a.py", subject="Make it faster")
    second = commit(env.remote, "b.py", subject="Fix the bubble")
    r = update.check()
    assert r["state"] == "available" and r["count"] == 2 and r["latest"]["commit"] == second
    assert [c["subject"] for c in r["commits"]] == ["Fix the bubble", "Make it faster"]


def test_an_install_from_elsewhere_is_offered_a_release(env):
    env.install_info("f" * 40, branch="my-branch")
    r = update.check()
    assert r["state"] == "available" and "outside the release history" in r["note"]


def test_an_install_without_a_version(env):
    assert update.check()["state"] == "unknown"


def test_no_network(env):
    env.install_info(env.first, remote=str(env.tmp / "nowhere"))
    r = update.check()
    assert r["state"] == "error" and r["message"]


def test_one_notification_per_version(env):
    env.install_info(env.first)
    commit(env.remote, "a.py")
    update.check(notify=True)
    update.check(notify=True)
    assert len([c for c in notice.shown if "update available" in " ".join(c)]) == 1
    commit(env.remote, "b.py")
    update.check(notify=True)
    assert len([c for c in notice.shown if "update available" in " ".join(c)]) == 2


def test_update_installs_the_new_version_and_restarts_auren(env):
    env.install_info(env.first)
    update.check()
    second = commit(env.remote, "omarchy-plugin/Panel.qml", "v2", "New panel")
    assert update.run() == 0
    s = update.status()
    assert s["state"] == "done" and "New panel" in s["message"]
    assert env.installs() == [second] and json.loads(env.info.read_text())["commit"] == second
    assert ("stop", "auren", "auren-mascot") in env.calls
    assert env.active == {"auren": True, "auren-mascot": False}   # as they were
    assert ("restart", "auren") in env.calls                       # new files: a fresh process
    assert env.shell == [True]                                     # the bar plugin changed
    assert update.check()["state"] == "current"


def test_the_shell_stays_when_the_plugin_didnt_change(env):
    env.install_info(env.first)
    update.check()
    commit(env.remote, "src.py")
    assert update.run() == 0 and env.shell == []


def test_the_shell_restarts_when_the_mascot_qml_changed(env):
    # the panel imports the mascot's QML for its live mascot, so the shell must reload it
    env.install_info(env.first)
    update.check()
    commit(env.remote, "mascot/mascots/common/MascotBase.qml")
    assert update.run() == 0 and env.shell == [True]


def test_a_failed_install_goes_back(env):
    env.install_info(env.first)
    update.check()
    second = commit(env.remote, "fail", subject="Broken")        # its install.sh exits 3
    assert update.run() == 1
    s = update.status()
    assert s["state"] == "failed" and env.first[:7] in s["message"]
    assert env.installs() == [second, env.first]                   # tried, then put the old one back
    assert json.loads(env.info.read_text())["commit"] == env.first
    assert env.active["auren"] is True                             # running again either way


def test_it_waits_for_auren_to_be_idle(env, monkeypatch):
    env.install_info(env.first)
    commit(env.remote, "a.py")
    update.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    update.STATE_FILE.write_text(json.dumps({"state": "speaking"}))
    monkeypatch.setattr(update, "IDLE_WAIT", 0.1)
    assert update.run() == 1
    assert "busy" in update.status()["message"] and not any(c[0] == "stop" for c in env.calls)
    assert env.installs() == []


def test_a_dev_checkout_with_changes_is_left_alone(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    env.install_info(env.first, mode="dev", source=str(checkout))
    commit(env.remote, "a.py")
    (checkout / "install.sh").write_text("# my edit")
    assert update.run() == 1
    assert "update it yourself" in update.status()["message"] and env.installs() == []


def test_a_dev_checkout_on_clean_main_is_fast_forwarded(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    env.install_info(env.first, mode="dev", source=str(checkout))
    second = commit(env.remote, "a.py")
    assert update.run() == 0
    assert git(checkout, "rev-parse", "HEAD") == second and env.installs() == [second]


def test_only_one_update_at_a_time(env, monkeypatch):
    env.install_info(env.first)
    monkeypatch.setattr(update, "LOCK_WAIT", 0.2)
    with update._lock() as got:
        assert got
        assert update.run() == 1


def test_a_check_doesnt_hide_an_update_in_progress(env):
    env.install_info(env.first)
    update._write(state="updating", step="Installing…", started_at=__import__("time").time())
    assert update.check()["state"] == "updating"


def test_auren_that_wont_stop_isnt_updated(env):
    env.install_info(env.first)
    commit(env.remote, "a.py")
    env.broken.add("stop")
    assert update.run() == 1
    assert "Couldn't stop" in update.status()["message"] and env.installs() == []


def test_a_failed_dev_update_goes_back(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    env.install_info(env.first, mode="dev", source=str(checkout))
    commit(env.remote, "fail", subject="Broken")
    assert update.run() == 1
    assert git(checkout, "rev-parse", "HEAD") == env.first and not (checkout / "fail").exists()
    assert env.first[:7] in update.status()["message"]


def test_a_failed_dev_update_keeps_commits_pulled_since_the_install(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    env.install_info(env.first, mode="dev", source=str(checkout))
    pulled = commit(env.remote, "a.py", subject="Pulled")
    git(checkout, "pull", "-q")                              # pulled, not installed yet
    commit(env.remote, "fail", subject="Broken")
    assert update.run() == 1
    assert git(checkout, "rev-parse", "HEAD") == pulled      # back to where the branch was, not the install
    assert env.installs()[-1] == pulled


def test_a_disabled_mascot_stays_disabled(env):
    env.install_info(env.first)
    env.enabled["auren-mascot"] = False
    commit(env.remote, "a.py")
    assert update.run() == 0
    assert env.enabled == {"auren": True, "auren-mascot": False}


def test_an_install_from_a_local_commit_can_still_go_back(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    local = commit(checkout, "mine.py", subject="Only here")      # never pushed
    env.install_info(local, branch="mine", source=str(checkout))
    commit(env.remote, "fail", subject="Broken")
    assert update.run() == 1
    assert env.installs()[-1] == local                             # fetched from the checkout and put back


def test_an_install_that_cant_go_back_isnt_touched(env):
    env.install_info("f" * 40, branch="gone", source=str(env.tmp / "nowhere"))
    commit(env.remote, "a.py")
    assert update.run() == 1
    assert "couldn't go back" in update.status()["message"] and env.installs() == []
    assert not any(c[0] == "stop" for c in env.calls)


def test_start_runs_the_update_in_its_own_unit(env, monkeypatch):
    env.install_info(env.first)
    ran = []
    monkeypatch.setattr(update.subprocess, "run", lambda cmd, **kw: ran.append(cmd))
    assert update.start()["state"] == "updating"
    cmd = ran[0]
    assert cmd[:2] == ["systemd-run", "--user"] and cmd[-2:] == ["update", "run"]
    assert "--setenv=PATH" in cmd and not any("=" in a.split("--setenv=", 1)[-1] for a in cmd if "--setenv" in a)
    assert update.start()["state"] == "updating" and len(ran) == 1   # already going: not twice


def test_an_install_without_a_version_isnt_replaced(env):
    commit(env.remote, "a.py")
    assert update.run() == 1
    assert "version" in update.status()["message"] and env.calls == [] and env.installs() == []


def test_unreleased_main_commits_are_never_offered_or_installed(env):
    env.install_info(env.first)
    commit(env.remote, "unreleased.py", release=False)
    assert update.check(notify=True)["state"] == "current"
    assert update.run() == 0
    assert env.installs() == [] and env.calls == [] and notice.shown == []


def test_update_stops_at_release_even_when_main_is_ahead(env):
    env.install_info(env.first)
    released = commit(env.remote, "released.py")
    commit(env.remote, "unreleased.py", release=False)
    assert update.check()["latest"]["tag"] == "v0.1.1"
    assert update.run() == 0
    assert env.installs() == [released]
    assert not (update.SOURCE / "unreleased.py").exists()


def test_annotated_release_tag_is_peeled_to_a_commit(env):
    env.install_info(env.first)
    released = commit(env.remote, "a.py", release=False)
    git(env.remote, "tag", "-a", "v0.1.1", "-m", "Release")
    assert update.check()["latest"]["commit"] == released
    assert update.run() == 0 and env.installs() == [released]


def test_install_ahead_of_latest_release_is_not_downgraded(env):
    ahead = commit(env.remote, "a.py", release=False)
    env.install_info(ahead)
    assert update.check()["state"] == "current"
    assert update.run() == 0 and env.installs() == [] and env.calls == []


def test_no_release_leaves_the_install_and_services_alone(env):
    env.install_info(env.first)
    git(env.remote, "tag", "-d", "v0.1.0")
    assert update.check()["state"] == "error"
    assert update.run() == 1 and env.installs() == [] and env.calls == []


def test_a_moved_release_tag_is_refused(env):
    env.install_info(env.first)
    commit(env.remote, "a.py")
    update.check()
    commit(env.remote, "replacement.py", release=False)
    git(env.remote, "tag", "-f", "v0.1.1")
    assert update.run() == 1 and env.installs() == [] and env.calls == []


@pytest.mark.parametrize("remote", ["https://github.com/owner/repo.git", "git@github.com:owner/repo.git",
                                  "ssh://git@github.com/owner/repo.git", "https://github.com/owner/repo/"])
def test_github_remote_forms(remote):
    assert update._github_repo(remote) == "owner/repo"


@pytest.mark.parametrize("remote", ["https://evil.example/owner/repo", "https://github.com.evil.example/owner/repo",
                                  "/tmp/repo", "https://user:secret@github.com/owner/repo"])
def test_other_hosts_and_embedded_credentials_are_refused(remote):
    with pytest.raises(update.UpdateError):
        update._github_repo(remote)


@pytest.fixture
def release_api(monkeypatch):
    import io
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    calls = []
    payload = {"tag_name": "v0.1.0", "name": "First release", "draft": False, "prerelease": False}
    def urlopen(request, timeout):
        calls.append(request)
        return io.StringIO(json.dumps(payload))
    monkeypatch.setattr(update, "urlopen", urlopen)
    return payload, calls


def test_release_api_uses_published_latest_without_needing_gh(release_api):
    _, calls = release_api
    assert update._latest_release("https://github.com/owner/repo.git")["tag"] == "v0.1.0"
    assert calls[0].full_url == "https://api.github.com/repos/owner/repo/releases/latest"
    assert not calls[0].has_header("Authorization")


def test_release_api_can_authenticate_private_repositories(release_api, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "test-token")
    update._latest_release("git@github.com:owner/repo.git")
    assert release_api[1][0].get_header("Authorization") == "Bearer test-token"


@pytest.mark.parametrize("change", [{"draft": True}, {"prerelease": True}, {"tag_name": "main"},
                                    {"tag_name": "v0.2.0-rc.1"}, {"tag_name": "--upload-pack=evil"}])
def test_release_api_rejects_unpublished_and_invalid_versions(release_api, change):
    release_api[0].update(change)
    with pytest.raises(update.UpdateError):
        update._latest_release("https://github.com/owner/repo.git")


@pytest.mark.parametrize("code", [401, 403, 404, 429, 500])
def test_release_api_errors_are_actionable_and_hide_credentials(release_api, monkeypatch, code):
    from urllib.error import HTTPError
    def fail(*args, **kwargs):
        raise HTTPError("https://api.github.com", code, "secret-token", {}, None)
    monkeypatch.setattr(update, "urlopen", fail)
    with pytest.raises(update.UpdateError) as error:
        update._latest_release("https://github.com/owner/repo.git")
    assert "secret-token" not in str(error.value)


def test_standalone_tag_does_not_trigger_an_update(env, monkeypatch):
    env.install_info(env.first)
    commit(env.remote, "tagged-but-unpublished.py", release=False)
    git(env.remote, "tag", "v9.0.0")
    monkeypatch.setattr(update, "_latest_release", lambda remote: {"tag": "v0.1.0", "name": "First", "url": ""})
    assert update.check()["state"] == "current"
    assert update.run() == 0 and env.installs() == []


def test_dev_checkout_ahead_of_release_never_installs_unreleased_code(env):
    checkout = env.tmp / "checkout"
    subprocess.run([*GIT, "clone", "-q", str(env.remote), str(checkout)], check=True)
    env.install_info(env.first, mode="dev", source=str(checkout))
    commit(env.remote, "released.py")
    ahead = commit(env.remote, "unreleased.py", release=False)
    git(checkout, "pull", "-q")
    assert update.run() == 1
    assert "update it yourself" in update.status()["message"]
    assert env.installs() == [] and not any(c[0] == "stop" for c in env.calls)
    assert git(checkout, "rev-parse", "HEAD") == ahead


def test_private_releases_can_use_an_existing_gh_login(release_api, monkeypatch):
    monkeypatch.setattr(update.shutil, "which", lambda name: "/usr/bin/gh")
    def auth(cmd, **kwargs):
        assert cmd == ["gh", "auth", "token", "--hostname", "github.com"]
        return subprocess.CompletedProcess(cmd, 0, "test-login-token\n", "")
    monkeypatch.setattr(update.subprocess, "run", auth)
    update._latest_release("https://github.com/owner/repo.git")
    assert release_api[1][0].get_header("Authorization") == "Bearer test-login-token"
