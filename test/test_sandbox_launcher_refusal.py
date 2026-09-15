"""``sandbox.launcher_refusal``: the launcher's own refusal line as a typed verdict.

``wrap_argv`` raises before a spawn it cannot sandbox. The Linux launcher can
also refuse AFTER the spawn — a host that passed the probe still denies a
control at spawn time — and that refusal reaches its caller only as exit 1 plus
one ``sandbox:``-prefixed line on stderr. Read as a plain non-zero exit, a
present, signed-in kiro-cli was reported as not installed. These tests pin the
classifier to the launcher's real wording, so a drift in either side reds here
rather than in a container.
"""

from __future__ import annotations

import errno
import re
import sys

import pytest

from kiro_crew import sandbox as sb

#: The exact line a container under its runtime's default AppArmor profile
#: produces: both unshares succeed, the launcher's first mount is refused.
_MOUNT_REFUSED = (
    "sandbox: BLOCKED -- making mount propagation private on / failed: errno 13 "
    "(Permission denied). The sandbox could not establish this control, so the "
    "agent would run with the path visible. Lower sandbox_level to run without it "
    "deliberately."
)


class TestLauncherRefusalClassification:
    def test_the_container_mount_refusal_is_a_no_backend_verdict_with_the_mount_remedy(
        self,
    ) -> None:
        kind, detail, remedy = sb.launcher_refusal(_MOUNT_REFUSED)  # type: ignore[misc]

        assert kind == "no_backend"
        assert detail == _MOUNT_REFUSED
        assert remedy == sb.REMEDY_MOUNT_DENIED

    def test_the_line_is_found_anywhere_in_the_captured_output(self) -> None:
        # The supervisor and a launcher warning can precede it; the child's own
        # output cannot follow it (the launcher exits without exec).
        output = "some supervisor note\n" + _MOUNT_REFUSED + "\n"
        assert sb.launcher_refusal(output) is not None

    def test_a_refused_hiding_mount_shares_the_mount_remedy(self) -> None:
        # A bind mount refused with EACCES is the same policy that refuses the
        # propagation mount; the remedy is the same container change.
        line = "sandbox: BLOCKED -- hiding /home/dev/.aws failed: errno 13 (Permission denied)."
        assert sb.launcher_refusal(line) == ("no_backend", line, sb.REMEDY_MOUNT_DENIED)

    def test_a_refused_mount_with_an_unmapped_errno_carries_no_remedy(self) -> None:
        line = "sandbox: BLOCKED -- hiding /home/dev/.aws failed: errno 5 (Input/output error)."
        assert sb.launcher_refusal(line) == ("no_backend", line, "")

    def test_the_unshare_steps_classify_exactly_as_the_probe_does(self) -> None:
        newns = "sandbox: unshare(NEWNS) failed: errno 1"
        newuser = "sandbox: unshare(NEWUSER) failed: errno 1"

        assert sb.launcher_refusal(newns) == ("no_backend", newns, sb.REMEDY_APPARMOR_USERNS)
        assert sb.launcher_refusal(newuser) == ("no_backend", newuser, sb.REMEDY_USERNS_DENIED)

    @pytest.mark.parametrize(
        "line",
        [
            "sandbox: BLOCKED — failed to set NO_NEW_PRIVS (prctl returned -1)",
            "sandbox: BLOCKED — failed to install seccomp-BPF filter (prctl returned -1)",
            "sandbox: BLOCKED — no seccomp syscall table for machine sparc64",
            "sandbox: BLOCKED — libc exposes no prctl(2), so neither the NO_NEW_PRIVS nor "
            "the seccomp step can run",
        ],
    )
    def test_the_filter_installs_are_no_backend_with_no_mechanism_token(self, line: str) -> None:
        # The em dash is the launcher's own spelling at these sites.
        assert sb.launcher_refusal(line) == ("no_backend", line, "")

    def test_a_broken_handshake_is_transient(self) -> None:
        line = "sandbox: FATAL - child did not publish its namespace readiness"
        assert sb.launcher_refusal(line) == ("transient", line, "")

    def test_a_hardlinked_credential_is_not_a_sandbox_failure(self) -> None:
        # The sandbox WORKED and found host state it must not expose. Calling that
        # "no backend" would push the operator to disable isolation instead of
        # fixing the file the line names.
        line = (
            "sandbox: BLOCKED — found hardlink(s) to protected credential path(s): "
            "/home/dev/.ssh/id_ed25519 -> /home/dev/backup/key"
        )
        assert sb.launcher_refusal(line) is None

    def test_an_unreadable_known_hosts_is_host_state_not_a_verdict(self) -> None:
        line = (
            "sandbox: FATAL — cannot read /home/dev/.ssh/known_hosts (Permission denied). "
            "Refusing to continue: proceeding without it would leave host-key "
            "verification accepting any new key."
        )
        assert sb.launcher_refusal(line) is None

    def test_a_launcher_invoked_with_no_command_is_the_callers_defect(self) -> None:
        # ``sandbox_launcher: no command given`` is a usage error in the spawn
        # itself; it says nothing about the host and must not become a verdict.
        assert sb.launcher_refusal("sandbox_launcher: no command given") is None

    def test_an_advisory_warning_the_launcher_continued_past_is_not_a_refusal(self) -> None:
        line = (
            "sandbox: WARNING -- widening /run/x failed (errno 13); continuing with the path sealed"
        )
        assert sb.launcher_refusal(line) is None

    @pytest.mark.parametrize(
        "output",
        [
            "",
            "kiro-cli 1.18.0\n",
            "Error: not logged in\n",
            "Kiro sandbox: enabled (internal)\n",  # a mid-line mention is not the launcher
            "the sandbox: BLOCKED story\n",
        ],
    )
    def test_a_child_that_failed_on_its_own_is_never_a_sandbox_verdict(self, output: str) -> None:
        assert sb.launcher_refusal(output) is None


class TestCorroboration:
    """The line is a hint; the verdict is the probe's.

    The child is the unverified candidate itself, so its stderr can carry the
    launcher's exact line. A caller that trusted it would let a planted binary
    make the gate announce a sandbox failure and offer the isolation opt-out.
    ``corroborate_launcher_refusal`` therefore asks the host again and reports
    only what the probe found.
    """

    @staticmethod
    def _probe(
        monkeypatch: pytest.MonkeyPatch, verdict: tuple[bool, bool, str, str] | None
    ) -> list[int]:
        calls: list[int] = []

        def fake_probe() -> tuple[bool, bool, str, str]:
            calls.append(1)
            assert verdict is not None, "the probe must not run for this input"
            return verdict

        monkeypatch.setattr(sb, "_probe_unshare_once", fake_probe)
        monkeypatch.setattr(sb.sys, "platform", "linux")
        return calls

    def test_a_forged_line_with_a_working_sandbox_is_not_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._probe(monkeypatch, (True, False, "ok", ""))

        assert sb.corroborate_launcher_refusal(_MOUNT_REFUSED) is None
        assert calls == [1], "the line must trigger exactly one fresh probe"

    def test_a_corroborated_refusal_reports_the_probe_not_the_line(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The candidate printed the launcher's mount line; the host's own probe
        # reports a different mechanism. The probe wins — the line's text never
        # reaches the verdict, so a child cannot choose the remedy it is shown.
        reason = f"{sb._PROBE_STEP_NEWNS} failed with errno 1 (EPERM)"
        self._probe(monkeypatch, (False, False, reason, sb.REMEDY_APPARMOR_USERNS))
        monkeypatch.setattr(sb, "_inside_macos_sandbox", lambda: False)

        assert sb.corroborate_launcher_refusal(_MOUNT_REFUSED) == (
            "no_backend",
            reason,
            sb.REMEDY_APPARMOR_USERNS,
        )

    def test_the_container_case_end_to_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The issue's shape: the launcher's mount refused AND the fresh probe
        # refuses the same step, so the verdict carries the mount remedy.
        reason = f"{sb._PROBE_STEP_MOUNT_PRIVATE} failed with errno 13 (EACCES)"
        self._probe(monkeypatch, (False, False, reason, sb.REMEDY_MOUNT_DENIED))
        monkeypatch.setattr(sb, "_inside_macos_sandbox", lambda: False)

        assert sb.corroborate_launcher_refusal(_MOUNT_REFUSED) == (
            "no_backend",
            reason,
            sb.REMEDY_MOUNT_DENIED,
        )

    def test_a_transient_probe_failure_is_reported_transient(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._probe(monkeypatch, (False, True, "fork failed with errno 11 (EAGAIN)", ""))
        monkeypatch.setattr(sb, "_inside_macos_sandbox", lambda: False)

        kind, _reason, remedy = sb.corroborate_launcher_refusal(_MOUNT_REFUSED)  # type: ignore[misc]
        assert (kind, remedy) == ("transient", "")

    def test_no_launcher_line_means_no_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._probe(monkeypatch, None)

        assert sb.corroborate_launcher_refusal("Error: not logged in\n") is None
        assert calls == []

    def test_a_host_state_refusal_means_no_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._probe(monkeypatch, None)
        line = "sandbox: BLOCKED — found hardlink(s) to protected credential path(s): /x -> /y"

        assert sb.corroborate_launcher_refusal(line) is None
        assert calls == []

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_off_linux_a_matching_line_is_the_childs_own_text(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        # The prefixes are the Linux launcher's, and the probe is Linux-only: on
        # another platform a fresh probe would FAIL for reasons of its own (no
        # unshare in libc) and corroborate a forged line.
        calls = self._probe(monkeypatch, None)
        monkeypatch.setattr(sb.sys, "platform", platform)

        assert sb.corroborate_launcher_refusal(_MOUNT_REFUSED) is None
        assert calls == []


class TestOneOwnerForTheLauncherPrefixes:
    def test_the_clone_probe_classifier_reads_the_sandbox_tuple(self) -> None:
        # Two spellings of what the launcher says would drift apart the first time
        # the launcher changed; the module that generates the launcher owns the one.
        from kiro_crew.apps.builtins.auto_improvement.backend import clone_setup

        assert clone_setup._LAUNCHER_EXIT_PREFIXES is sb.LAUNCHER_EXIT_PREFIXES


@pytest.mark.skipif(sys.platform != "linux", reason="the namespace launcher is Linux-only")
class TestClassifierMatchesTheLauncher:
    """The wording lives in the launcher template; the classifier must keep up."""

    def test_every_launcher_line_is_a_known_refusal_or_the_advisory_prefix(self) -> None:
        source = sb._build_launcher_script("strict")
        spellings = set(re.findall(r'"(sandbox: [^"%{]+)', source))
        assert spellings, "the launcher template no longer spells its lines this way"
        for spelling in spellings:
            recognized = spelling.startswith(sb.LAUNCHER_EXIT_PREFIXES) or spelling.startswith(
                "sandbox: WARNING"
            )
            assert recognized, f"a new launcher line the classifier does not know: {spelling!r}"

    def test_the_mount_or_die_wording_parses_to_the_mount_step(self) -> None:
        # Rendered the way ``_mount_or_die`` renders it, with the errno the
        # container case produces.
        rendered = (
            "sandbox: BLOCKED -- %s failed: errno %d (%s). The sandbox could not "
            "establish this control, so the agent would run with the path "
            "visible. Lower sandbox_level to run without it deliberately."
        ) % ("making mount propagation private on /", errno.EACCES, "Permission denied")
        assert "_mount_or_die" in sb._build_launcher_script("strict")
        assert sb.launcher_refusal(rendered) == ("no_backend", rendered, sb.REMEDY_MOUNT_DENIED)
