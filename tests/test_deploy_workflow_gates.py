"""Adversarial tests for the highest-risk gates in
`.github/workflows/deploy-ingestion-azure.yml` (correction-pass item 1,
item 11: "Adversarial workflow tests for every gate and failure path").

These gates are plain `bash` embedded in workflow YAML -- GitHub Actions
itself never executes them outside a real dispatch, and this correction
pass is explicitly forbidden from dispatching this workflow for real. So
this file extracts each gate's own `run:` script *verbatim* from the real
YAML (via PyYAML, never a hand-retyped copy that could silently drift
from what actually ships) and executes it with `bash` against crafted
environments/git repositories -- proving the actual shipped logic behaves
correctly, not a paraphrase of it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-ingestion-azure.yml"


def _load_workflow() -> dict:
    with open(WORKFLOW_PATH) as f:
        # `on:` is a YAML 1.1 boolean key -- load with the plain Loader so
        # this parses the same way `yaml.safe_load` would for any GitHub
        # Actions file; the specific quirk is irrelevant here since this
        # module only reads `jobs`, never `on`.
        return yaml.safe_load(f)


def _find_step(job_name: str, step_name: str) -> dict:
    workflow = _load_workflow()
    job = workflow["jobs"][job_name]
    for step in job["steps"]:
        if step.get("name") == step_name:
            return step
    raise AssertionError(f"step {step_name!r} not found in job {job_name!r}")


def _run_step(
    job_name: str, step_name: str, *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    step = _find_step(job_name, step_name)
    script = step["run"]
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestTargetEnvironmentValidation:
    STEP_NAME = "Validate target_environment names a supported environment"

    def test_pilot_is_accepted(self, tmp_path: Path) -> None:
        result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=tmp_path,
            env={"TARGET_ENVIRONMENT": "pilot", "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "bad_value", ["production", "Pilot", "staging", "", "pilot ", " pilot"]
    )
    def test_every_unsupported_value_is_rejected(self, tmp_path: Path, bad_value: str) -> None:
        result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=tmp_path,
            env={"TARGET_ENVIRONMENT": bad_value, "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode != 0, f"should have rejected {bad_value!r}"


class TestApprovedBudgetAmountValidation:
    STEP_NAME = "Validate approved_budget_amount restates the actual configured budget ceiling"

    def _configured_amount(self) -> str:
        text = (REPO_ROOT / "infra" / "azure" / "modules" / "budget.bicep").read_text()
        for line in text.splitlines():
            if "param monthlyAmount int =" in line:
                return line.strip().split("=")[-1].strip()
        raise AssertionError("could not find monthlyAmount default in budget.bicep")

    def test_exact_restatement_of_the_configured_amount_is_accepted(self, tmp_path: Path) -> None:
        configured = self._configured_amount()
        result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=REPO_ROOT,
            env={"INPUT_BUDGET": f"CAD {configured}", "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "template",
        [
            "CAD {other}",  # wrong amount
            "{configured}",  # no currency at all
            "USD {configured}",  # wrong currency
            "CAD {configured} or {configured}",  # two numbers
            "",  # empty
            "cad ten",  # no digits at all
        ],
    )
    def test_every_incorrect_restatement_is_rejected(self, tmp_path: Path, template: str) -> None:
        configured = self._configured_amount()
        other = str(int(configured) + 1)
        value = template.format(configured=configured, other=other)
        result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=REPO_ROOT,
            env={"INPUT_BUDGET": value, "PATH": "/usr/bin:/bin"},
        )
        assert result.returncode != 0, f"should have rejected {value!r}"

    def test_a_change_to_the_configured_amount_changes_what_is_required(
        self, tmp_path: Path
    ) -> None:
        """Proves the gate reads the *real* configured value rather than
        embedding a copy of it that could drift -- points the step at a
        temp copy of the repo with a different `monthlyAmount` and
        confirms the previously-correct restatement is now rejected,
        while the new figure is accepted.
        """
        import shutil

        fake_repo = tmp_path / "repo"
        shutil.copytree(REPO_ROOT / "infra", fake_repo / "infra")
        budget_file = fake_repo / "infra" / "azure" / "modules" / "budget.bicep"
        original = budget_file.read_text()
        configured = self._configured_amount()
        changed = original.replace(
            f"param monthlyAmount int = {configured}", "param monthlyAmount int = 250"
        )
        assert changed != original, "fixture setup failed to change the budget default"
        budget_file.write_text(changed)

        old_result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=fake_repo,
            env={"INPUT_BUDGET": f"CAD {configured}", "PATH": "/usr/bin:/bin"},
        )
        assert old_result.returncode != 0, "the old figure should no longer be accepted"

        new_result = _run_step(
            "validate",
            self.STEP_NAME,
            cwd=fake_repo,
            env={"INPUT_BUDGET": "CAD 250", "PATH": "/usr/bin:/bin"},
        )
        assert new_result.returncode == 0, new_result.stderr


class TestSecretScanGate:
    """The scanning *logic* itself (real secrets caught regardless of
    location, the narrow allowlist, `uv.lock` exclusion) is comprehensively
    covered by `tests/test_scan_for_secrets.py` -- this class only
    confirms the workflow step is actually wired to invoke that script,
    correctly, with no stray flags or masking (`|| true`, `-o`, etc.)
    that could silently defeat it again.
    """

    STEP_NAME = (
        "Secret scan across the exact checked-out tree (correction-pass items 1/9: "
        "fail-closed, never an always-empty diff range, never a blanket tests/ exclusion)"
    )

    def test_step_invokes_the_real_scanner_script_with_no_masking(self) -> None:
        script = _find_step("verify", self.STEP_NAME)["run"].strip()
        assert script == "python3 scripts/scan_for_secrets.py"

    def test_step_never_swallows_the_scanner_scripts_own_exit_code(self) -> None:
        script = _find_step("verify", self.STEP_NAME)["run"]
        assert "|| true" not in script
        assert "|| exit 0" not in script

    def test_a_real_end_to_end_run_against_a_crafted_repo_is_caught(self, tmp_path: Path) -> None:
        """One real, end-to-end sanity check that running the exact
        step's own command line (not just the underlying `scan()`
        function `test_scan_for_secrets.py` already covers) against a
        real git repository actually fails on a real secret -- proves
        the wiring between the workflow step and the script file (a
        relative path, `scripts/scan_for_secrets.py`) resolves
        correctly relative to a repository root, mirroring exactly how
        `verify`'s own checkout step leaves the working directory.
        """
        repo = tmp_path / "repo"
        scripts_dir = repo / "scripts"
        scripts_dir.mkdir(parents=True)
        real_script = REPO_ROOT / "scripts" / "scan_for_secrets.py"
        (scripts_dir / "scan_for_secrets.py").write_text(real_script.read_text())
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        (repo / "config").mkdir()
        (repo / "config" / "production.env").write_text("SOME_KEY=AKIA1234567890ABCDEF\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=repo, check=True)

        result = _run_step(
            "verify", self.STEP_NAME, cwd=repo, env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin"}
        )
        assert result.returncode != 0, "a real secret-shaped string must fail the scan"


class TestGitDiffCheckGate:
    STEP_NAME = "git diff --check against this commit's own parent (correction-pass item 1)"

    def _git_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        return repo

    def _commit(self, repo: Path, path: str, content: str) -> str:
        file_path = repo / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=repo, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_a_commit_introducing_trailing_whitespace_is_caught(self, tmp_path: Path) -> None:
        repo = self._git_repo(tmp_path)
        self._commit(repo, "src/app.py", "print('hello')\n")
        commit_sha = self._commit(repo, "src/app.py", "print('hello')   \n")
        result = _run_step(
            "verify",
            self.STEP_NAME,
            cwd=repo,
            env={"COMMIT_SHA": commit_sha, "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        assert result.returncode != 0, (
            "trailing whitespace this commit itself introduced must be caught"
        )

    def test_a_clean_commit_passes(self, tmp_path: Path) -> None:
        repo = self._git_repo(tmp_path)
        self._commit(repo, "src/app.py", "print('hello')\n")
        commit_sha = self._commit(repo, "src/app.py", "print('hello world')\n")
        result = _run_step(
            "verify",
            self.STEP_NAME,
            cwd=repo,
            env={"COMMIT_SHA": commit_sha, "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        assert result.returncode == 0, result.stderr

    def test_an_initial_commit_with_no_parent_is_safely_skipped_not_bypassed(
        self, tmp_path: Path
    ) -> None:
        repo = self._git_repo(tmp_path)
        commit_sha = self._commit(repo, "src/app.py", "print('hello')   \n")
        result = _run_step(
            "verify",
            self.STEP_NAME,
            cwd=repo,
            env={"COMMIT_SHA": commit_sha, "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        assert result.returncode == 0, result.stderr

    def test_pre_existing_unrelated_whitespace_debt_elsewhere_never_fails_this_gate(
        self, tmp_path: Path
    ) -> None:
        """The property that made the earlier-considered "full-tree
        diff against the empty-tree hash" design rejected: pre-existing
        whitespace debt in a file *this commit does not touch* must never
        fail the gate -- only what this commit itself introduces.
        """
        repo = self._git_repo(tmp_path)
        self._commit(repo, "legacy/old.html", "<div>   \n</div>\n")  # pre-existing debt
        commit_sha = self._commit(repo, "src/app.py", "print('hello world')\n")
        result = _run_step(
            "verify",
            self.STEP_NAME,
            cwd=repo,
            env={"COMMIT_SHA": commit_sha, "PATH": "/usr/bin:/bin:/opt/homebrew/bin"},
        )
        assert result.returncode == 0, result.stderr


def _job_steps(job_name: str) -> list[dict]:
    return _load_workflow()["jobs"][job_name]["steps"]


def _step_scripts(job_name: str) -> str:
    """Concatenates every `run:` script in a job into one string -- used
    below only to prove the *absence* of a mutating command, which
    (unlike the migration-status parsing bug elsewhere in this file) is
    exactly the kind of check a textual search is appropriate for: there
    is no structured alternative to "does this job contain the substring
    `crane push` anywhere," and a false negative here (missing a real
    push hidden some other way) is exactly what the job-existence checks
    below independently also guard against.
    """
    return "\n".join(step.get("run", "") for step in _job_steps(job_name))


class TestPlanBuildNeverMutateAzure:
    """Correction pass, item 3: `action=plan` must never push an image or
    modify ACR, and no image may be published before `verify`/`plan` both
    succeed and the protected `production` Environment approves. Verified
    structurally against the real, parsed workflow YAML -- not merely
    asserted in a comment.
    """

    def test_build_job_never_logs_into_azure(self) -> None:
        steps = _job_steps("build")
        assert not any(step.get("uses", "").startswith("azure/login") for step in steps)

    def test_build_job_never_pushes_anything(self) -> None:
        scripts = _step_scripts("build")
        for forbidden in ("crane push", "docker push", "az acr login", "az acr"):
            assert forbidden not in scripts, f"build job must never run: {forbidden!r}"

    def test_plan_job_never_pushes_anything(self) -> None:
        scripts = _step_scripts("plan")
        for forbidden in ("crane push", "docker push", "az acr login", "az acr"):
            assert forbidden not in scripts, f"plan job must never run: {forbidden!r}"

    def test_plan_job_only_ever_runs_what_if_never_deployment_group_create(self) -> None:
        scripts = _step_scripts("plan")
        assert "deployment group create" not in scripts
        assert "deployment group what-if" in scripts

    def test_only_the_deploy_job_ever_pushes_an_image(self) -> None:
        workflow = _load_workflow()
        pushing_jobs = [
            job_name
            for job_name, job in workflow["jobs"].items()
            if "crane push" in _step_scripts(job_name)
        ]
        assert pushing_jobs == ["deploy"]

    def test_deploy_job_requires_verify_and_plan_to_have_already_succeeded(self) -> None:
        deploy = _load_workflow()["jobs"]["deploy"]
        assert set(deploy["needs"]) >= {"validate", "build", "verify", "plan"}

    def test_deploy_job_is_gated_by_the_protected_production_environment(self) -> None:
        deploy = _load_workflow()["jobs"]["deploy"]
        assert deploy["environment"] == "production"

    def test_push_step_only_runs_for_the_deploy_action_never_plan_or_rollback(self) -> None:
        steps = _job_steps("deploy")
        push_steps = [step for step in steps if "crane push" in step.get("run", "")]
        assert push_steps, "expected at least one push step in the deploy job"
        for step in push_steps:
            assert step.get("if") == "github.event.inputs.action == 'deploy'"

    def test_deploy_job_verifies_the_published_digest_matches_the_reviewed_one(self) -> None:
        scripts = _step_scripts("deploy")
        assert "EXPECTED_DIGEST" in scripts
        assert "published_digest" in scripts
        assert "exit 1" in scripts  # a real failure path exists, not just a log line


class TestBicepChecksumVerification:
    """Correction pass, item 5: pinning only the Bicep CLI's release URL/
    version is insufficient integrity verification -- this gate extracts
    the real `run:` script from the `verify` job's own Bicep-install step
    and executes it against a fake `curl` (so no real network access is
    needed) to prove the checksum comparison actually rejects a mismatch
    and actually accepts a match, rather than merely asserting the
    script contains the word "sha256" somewhere.
    """

    STEP_NAME = (
        "Install a pinned, checksum-verified Bicep CLI (correction-pass item 5: install "
        "BEFORE pytest, never after -- Bicep tests use `pytest.mark.skipif(bicep not "
        "found)`, so installing it afterward means every real compiled-template assertion "
        "silently never ran)"
    )

    def _fake_curl_dir(self, tmp_path: Path, content: bytes) -> Path:
        """A fake `curl` that ignores its real arguments and writes fixed
        `content` to whatever `-o <path>` names -- the step under test
        always calls `curl -sSL -o /usr/local/bin/bicep <url>`.
        """
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        fixture_path = tmp_path / "fixture-content"
        fixture_path.write_bytes(content)
        curl_script = bin_dir / "curl"
        curl_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'out=""\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then out="$2"; shift; fi\n'
            "  shift\n"
            "done\n"
            f'cp "{fixture_path}" "$out"\n'
        )
        curl_script.chmod(0o755)
        return bin_dir

    def test_rejects_a_binary_whose_checksum_does_not_match(self, tmp_path: Path) -> None:
        install_dir = tmp_path / "usr-local-bin"
        install_dir.mkdir()
        fake_bin = self._fake_curl_dir(tmp_path, content=b"not the real bicep binary")
        script = _find_step("verify", self.STEP_NAME)["run"].replace(
            "/usr/local/bin/bicep", str(install_dir / "bicep")
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": f"{fake_bin}:/usr/bin:/bin:/sbin:/opt/homebrew/bin",
                "BICEP_VERSION": "v0.47.16",
                "BICEP_SHA256": "0" * 64,  # deliberately wrong
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0, "must reject a binary that does not match BICEP_SHA256"
        assert "checksum mismatch" in result.stderr.lower()

    def test_accepts_a_binary_whose_checksum_matches(self, tmp_path: Path) -> None:
        import hashlib

        content = b"a fake but internally-consistent bicep binary\n"
        real_sha256 = hashlib.sha256(content).hexdigest()
        install_dir = tmp_path / "usr-local-bin"
        install_dir.mkdir()
        fake_bin_dir = self._fake_curl_dir(tmp_path, content=content)
        script = _find_step("verify", self.STEP_NAME)["run"].replace(
            "/usr/local/bin/bicep", str(install_dir / "bicep")
        )
        # The real step's last line invokes the freshly-downloaded binary
        # itself (`bicep --version`) -- our fixture content isn't a real
        # executable, so replace just that final invocation with a no-op
        # marker rather than trying to fake binary execution too.
        script = script.replace("bicep --version", "true # (stubbed: not a real binary)")
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": f"{fake_bin_dir}:/usr/bin:/bin:/sbin:/opt/homebrew/bin",
                "BICEP_VERSION": "v0.47.16",
                "BICEP_SHA256": real_sha256,
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "checksum mismatch" not in result.stderr.lower()


class TestCraneChecksumVerification:
    """Correction pass, item 2: pinning only crane's release URL/version
    is insufficient integrity verification, mirroring
    `TestBicepChecksumVerification` above -- extracts the real `run:`
    script from the `deploy` job's own crane-install step and executes
    it against a fake `curl` (a real, small `.tar.gz` fixture, so the
    step's own real `tar -xzf` extraction genuinely runs) to prove the
    checksum comparison rejects a mismatch and accepts a match, rather
    than merely asserting the script contains the word "sha256"
    somewhere.
    """

    STEP_NAME = "Install a pinned, checksum-verified, OCI-native registry tool (deploy action only)"

    def _fake_curl_dir(self, tmp_path: Path, tarball_bytes: bytes) -> Path:
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        fixture_path = tmp_path / "fixture.tar.gz"
        fixture_path.write_bytes(tarball_bytes)
        curl_script = bin_dir / "curl"
        curl_script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'out=""\n'
            'while [ "$#" -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then out="$2"; shift; fi\n'
            "  shift\n"
            "done\n"
            f'cp "{fixture_path}" "$out"\n'
        )
        curl_script.chmod(0o755)
        return bin_dir

    def _real_crane_tarball(self, *, version: str) -> bytes:
        """A real, valid `.tar.gz` containing one executable member named
        `crane` that prints `version` when run as `crane version` --
        genuinely extracted by the step's own real `tar -xzf` command,
        never a hand-waved stand-in.
        """
        import io
        import tarfile as tarfile_module

        script = f'#!/usr/bin/env bash\necho "{version}"\n'.encode()
        buf = io.BytesIO()
        with tarfile_module.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile_module.TarInfo(name="crane")
            info.size = len(script)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(script))
        return buf.getvalue()

    def test_rejects_a_release_archive_whose_checksum_does_not_match(self, tmp_path: Path) -> None:
        install_dir = tmp_path / "usr-local-bin"
        install_dir.mkdir()
        tarball = self._real_crane_tarball(version="0.22.1")
        fake_bin = self._fake_curl_dir(tmp_path, tarball_bytes=tarball)
        script = (
            _find_step("deploy", self.STEP_NAME)["run"]
            .replace("/tmp/crane.tar.gz", str(tmp_path / "crane.tar.gz"))
            .replace("/usr/local/bin", str(install_dir))
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": f"{fake_bin}:/usr/bin:/bin:/sbin:/opt/homebrew/bin",
                "CRANE_VERSION": "0.22.1",
                "CRANE_SHA256": "0" * 64,  # deliberately wrong
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode != 0, (
            "must reject a release archive that does not match CRANE_SHA256"
        )
        assert "checksum mismatch" in result.stderr.lower()
        assert not (install_dir / "crane").exists(), (
            "a checksum mismatch must fail before extraction, never leave a partially "
            "installed binary behind"
        )

    def test_accepts_a_release_archive_whose_checksum_matches(self, tmp_path: Path) -> None:
        import hashlib

        install_dir = tmp_path / "usr-local-bin"
        install_dir.mkdir()
        tarball = self._real_crane_tarball(version="0.22.1")
        real_sha256 = hashlib.sha256(tarball).hexdigest()
        fake_bin = self._fake_curl_dir(tmp_path, tarball_bytes=tarball)
        script = (
            _find_step("deploy", self.STEP_NAME)["run"]
            .replace("/tmp/crane.tar.gz", str(tmp_path / "crane.tar.gz"))
            .replace("/usr/local/bin", str(install_dir))
        )
        result = subprocess.run(
            ["bash", "-c", script],
            env={
                "PATH": f"{fake_bin}:{install_dir}:/usr/bin:/bin:/sbin:/opt/homebrew/bin",
                "CRANE_VERSION": "0.22.1",
                "CRANE_SHA256": real_sha256,
            },
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stderr
        assert "checksum mismatch" not in result.stderr.lower()
        assert (install_dir / "crane").is_file()

    def test_the_pinned_checksum_env_var_matches_the_real_pinned_release(self) -> None:
        """Defense in depth against this pin itself silently drifting:
        the exact `CRANE_SHA256` this workflow's `env:` block pins was
        independently computed from the real GitHub release during this
        correction pass (`shasum -a 256` against a fresh download,
        cross-checked against that release's own published
        `checksums.txt`) -- this test pins that same known-real value so
        an accidental edit to either the version or the checksum without
        re-verifying against the real release is caught.
        """
        workflow = _load_workflow()
        crane_step = _find_step("deploy", self.STEP_NAME)
        assert crane_step["env"]["CRANE_VERSION"] == "0.22.1"
        assert (
            crane_step["env"]["CRANE_SHA256"]
            == "0ab7a1d6932a213aed964ce97666c3077fe691c8606413674a8b3e0b9ec4cda0"
        )
        del workflow  # only used to confirm the file parses; nothing else needed here


class TestCraneNeverPushesTheBareTarDirectly:
    """Correction pass, item 2: `crane push` documents that a *file*
    argument is always assumed to be a Docker-format tarball, never an
    OCI layout -- reproduced directly (a real `crane push` against a
    real OCI archive tar fails with "file manifest.json not found in
    tar"). The publish step must extract the archive to a directory
    first and push that directory, never the bare `.tar` file.
    """

    def test_deploy_job_extracts_the_oci_archive_to_a_directory_before_pushing(self) -> None:
        """Correction pass, item 3: extraction is now performed by
        `extract_oci_digest.py --extract-to` -- one validator-controlled
        Python call, never a separate, unrestricted `tar -xf` (which
        would reintroduce exactly the validate/extract TOCTOU gap and
        unsafe-member risk item 3 closed).
        """
        scripts = _step_scripts("deploy")
        assert "--extract-to /tmp/oci-layout" in scripts
        assert "tar -xf" not in scripts

    def test_crane_push_is_never_invoked_against_the_bare_tar_file(self) -> None:
        for step in _job_steps("deploy"):
            run = step.get("run", "")
            if "crane push" not in run:
                continue
            for line in run.splitlines():
                if "crane push" in line:
                    assert "ingestion-api-oci.tar" not in line, (
                        f"crane push must target the extracted OCI layout directory, "
                        f"never the bare archive file: {line!r}"
                    )
                    assert "/tmp/oci-layout" in line

    def test_extraction_step_validates_the_layout_before_push(self) -> None:
        extract_step = _find_step(
            "deploy",
            "Extract the verified OCI archive to a real OCI layout directory (deploy action only)",
        )
        script = extract_step["run"]
        assert "oci-layout" in script
        assert "index.json" in script
        assert "blobs" in script


class TestNoStaleLifecycleClaims:
    """Correction pass, item 7: several workflow comments described
    behavior the workflow no longer has (or never accurately had) --
    `build` claimed to push to ACR (it never does, since a much earlier
    correction moved publishing to `deploy`), and the publish step's own
    mismatch-handling comment implied a mismatch leaves ACR untouched,
    when in fact the push has already completed by the time the mismatch
    is detected. Structurally verified against the real, current
    workflow text so these specific stale claims can never silently
    return.
    """

    def test_build_job_comment_never_claims_it_pushes_to_acr(self) -> None:
        comment_text = _job_comment_block("build")
        assert "pushes it to ACR" not in comment_text
        assert "pushes to ACR" not in comment_text

    def test_publish_step_comment_never_claims_a_mismatch_leaves_acr_unchanged(self) -> None:
        """Checks the raw workflow *text* (comments included), not just
        `run:` script bodies -- the original stale claim this test
        guards against lived in a step-level YAML comment, which a
        parsed-YAML-based check (like `_step_scripts`) never sees at
        all, since PyYAML discards comments before Python ever
        receives the document.
        """
        raw_text = _workflow_text()
        assert "leaves ACR unchanged" not in raw_text
        assert "leave ACR unchanged" not in raw_text

    def test_publish_step_comment_documents_the_real_orphan_cleanup_procedure(self) -> None:
        """The corrected comment must honestly describe what actually
        happens on a (structurally-impossible-but-unhandled) mismatch:
        ACR has already been mutated, and cleanup requires a separate,
        audited, manual operation -- never a silent workflow-side
        broadening of its own identity's permissions.
        """
        scripts = _step_scripts("deploy")
        assert "ALREADY been mutated" in scripts
        assert "AcrDelete" in scripts

    def test_deploy_identity_documentation_never_grants_acr_delete(self) -> None:
        doc_text = DOC_PATH.read_text(encoding="utf-8")
        assert "AZURE_DEPLOY_CLIENT_ID" in doc_text
        # The deploy identity's own documented permission list must
        # never include AcrDelete -- only AcrPush, mirroring the
        # workflow's own least-privilege comment.
        section_start = doc_text.index("AZURE_DEPLOY_CLIENT_ID")
        section = doc_text[section_start : section_start + 400]
        assert "AcrDelete" not in section


DOC_PATH = REPO_ROOT / "docs" / "deployment" / "azure-ingestion-production.md"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job_comment_block(job_name: str) -> str:
    """Extracts the block of `#`-prefixed YAML comment lines immediately
    preceding a top-level job key in the raw workflow text -- comments
    are discarded by `yaml.safe_load`, so verifying comment *text*
    (rather than the job's structured behavior, already covered
    elsewhere in this file) requires reading the raw file directly.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    marker = f"\n  {job_name}:\n"
    index = text.index(marker)
    # Walk backwards through contiguous comment/blank lines immediately above the job key.
    lines = text[:index].splitlines()
    block: list[str] = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            block.append(line)
        else:
            break
    return "\n".join(reversed(block))


class TestCrossFormatEquivalenceProof:
    """Correction pass, item 3: since this workflow keeps both an OCI
    archive (authoritative for the digest) and a docker-format archive
    (used only for local `docker inspect`/`docker run` commands), the
    two must be cryptographically proven to describe the same image --
    never merely assumed because "they came from the same build".
    """

    def test_verify_job_compares_docker_image_id_against_the_oci_config_digest(self) -> None:
        step = _find_step(
            "verify",
            "Cryptographically prove the loaded docker-format archive is the same image "
            "as the published OCI archive (correction pass, item 3)",
        )
        script = step["run"]
        assert "docker inspect --format='{{.Id}}'" in script
        assert "config_digest" in script
        assert "exit 1" in script

    def test_the_equivalence_check_runs_after_the_docker_load_step(self) -> None:
        steps = _job_steps("verify")
        names = [s.get("name", "") for s in steps]
        load_index = next(i for i, n in enumerate(names) if "Load the docker-format artifact" in n)
        equivalence_index = next(i for i, n in enumerate(names) if "Cryptographically prove" in n)
        assert equivalence_index > load_index


class TestSmokeTestPayloadAndCleanup:
    """Correction pass: the smoke test's own payload validity is proven
    behaviorally against the real ASGI app in
    `tests/test_smoke_test_payload.py` -- this class instead extracts and
    executes the *real* smoke-test step script itself (never a
    paraphrase), under **real GitHub Actions shell semantics**
    (`bash -e -o pipefail`, matching GitHub's own documented default of
    `bash --noprofile --norc -eo pipefail {0}` for every `run:` step) --
    a real gap this correction pass found: an earlier version of this
    test class used plain `bash -c script` (no `-e`), which does **not**
    reproduce `set -e`'s command-substitution behavior at all, so it
    could not have caught the exact bug this class now guards against --
    reproduced directly: under real `bash -e -o pipefail`, a `curl`
    *process* exit failure (7, a real transport-failure code -- not
    merely a non-2xx HTTP status) inside `x="$(curl ...)"` terminated the
    script immediately, before `DELETE` was ever reached, permanently
    orphaning the record `POST` had already created.

    **Sixth pass**: independently reproduced a second, distinct defect in
    the same step -- the step printed "synthetic smoke test passed" right
    after the post-GET integrity check succeeded, *before* the `EXIT`
    trap's own `DELETE` cleanup had run at all. Reproduced directly: a
    fake `curl` returning `500` on `DELETE` only produced a correctly
    nonzero exit code (1) but stdout still contained the literal success
    line -- a false-success statement in the logs despite the job's own
    real, correct failure. Every test in this class that exercises a
    failure path now also asserts the success message's absence, and the
    happy-path test now asserts it appears exactly once.
    """

    STEP_NAME = "Synthetic-only smoke test (upload/get/delete, dedicated smoke tenant)"

    def _fake_bin_dir(
        self,
        tmp_path: Path,
        *,
        marker_file: Path,
        get_curl_exit: int = 0,
        get_status: str = "200",
        get_ingestion_id: str = "ing_test_smoke",
        delete_curl_exit: int = 0,
        delete_status: str = "200",
    ) -> Path:
        """A fake `curl` (parsed by HTTP method, from `-X`, defaulting to
        GET) and a fake `az` (this step's first line resolves the target
        FQDN via `az containerapp show`; `az` is not installed on this
        machine at all, and even where it is, this step must never
        contact a real subscription).

        `POST` always succeeds (201) and writes a fixed
        `ingestion_id: ing_test_smoke` to whatever `-o` names -- every
        test in this class cares only about GET/DELETE behavior after a
        successful POST. `GET`/`DELETE` independently support simulating
        either a *process*-level transport failure (a nonzero exit,
        `*_curl_exit`, matching a real `curl` transport-failure/timeout
        exit code -- **no stdout is written in that case**, exactly like
        a real `curl` that never received a response) or an HTTP-level
        result (`*_status`, with `GET` additionally able to report a
        deliberately *mismatched* `ingestion_id` in its own response
        body, to exercise this step's real post-GET integrity
        assertion). `DELETE` always writes `marker_file` -- proving it
        was *attempted* -- regardless of whether it then goes on to
        "succeed" or "fail", since attempted-but-failed is exactly what
        several of this class's own tests need to distinguish from
        never-attempted-at-all.
        """
        bin_dir = tmp_path / "fakebin"
        bin_dir.mkdir()
        az_script = bin_dir / "az"
        az_script.write_text("#!/usr/bin/env bash\necho 'fake-smoke-test.example.invalid'\n")
        az_script.chmod(0o755)
        curl_script = bin_dir / "curl"
        curl_script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            "method = 'GET'\n"
            "out_path = None\n"
            "i = 0\n"
            "while i < len(args):\n"
            "    if args[i] == '-X':\n"
            "        method = args[i + 1]; i += 2; continue\n"
            "    if args[i] == '-o':\n"
            "        out_path = args[i + 1]; i += 2; continue\n"
            "    i += 1\n"
            "if method == 'POST':\n"
            "    if out_path and out_path != '/dev/null':\n"
            "        with open(out_path, 'w') as f:\n"
            "            json.dump({'ingestion_id': 'ing_test_smoke'}, f)\n"
            "    sys.stdout.write('201')\n"
            "elif method == 'DELETE':\n"
            f"    with open({str(marker_file)!r}, 'w') as f:\n"
            "        f.write('called')\n"
            f"    if {delete_curl_exit!r} != 0:\n"
            f"        sys.exit({delete_curl_exit!r})\n"
            f"    sys.stdout.write({delete_status!r})\n"
            "else:\n"
            f"    if {get_curl_exit!r} != 0:\n"
            f"        sys.exit({get_curl_exit!r})\n"
            "    if out_path and out_path != '/dev/null':\n"
            "        with open(out_path, 'w') as f:\n"
            f"            json.dump({{'ingestion_id': {get_ingestion_id!r}}}, f)\n"
            f"    sys.stdout.write({get_status!r})\n"
        )
        curl_script.chmod(0o755)
        return bin_dir

    def _run_step_under_real_shell_semantics(
        self, tmp_path: Path, fake_bin: Path, *, smoke_token: str = "fake-token-never-a-real-secret"
    ) -> subprocess.CompletedProcess:
        """Writes the real, extracted step script to a real file and runs
        it as `bash -e -o pipefail <file>` -- matching GitHub Actions'
        own documented default shell invocation for a `run:` step far
        more closely than a `bash -c "..."` string ever does (no
        additional quoting/escaping layer, and critically, identical
        `set -e`/`pipefail` semantics against a real script file).
        """
        script = _find_step("deploy", self.STEP_NAME)["run"]
        script_path = tmp_path / "smoke-step.sh"
        script_path.write_text(script)
        return subprocess.run(
            ["bash", "-e", "-o", "pipefail", str(script_path)],
            cwd=REPO_ROOT,
            env={
                "PATH": f"{fake_bin}:/usr/local/bin:/usr/bin:/bin:/sbin:/opt/homebrew/bin",
                "COG_NAME_PREFIX": "cog-test",
                "RESOURCE_GROUP": "rg-test",
                "SMOKE_TOKEN": smoke_token,
            },
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_step_builds_the_payload_via_the_shared_script_never_a_hand_typed_string(
        self,
    ) -> None:
        script = _find_step("deploy", self.STEP_NAME)["run"]
        assert "python3 scripts/smoke_test_payload.py" in script
        assert '"findings":[]' not in script, (
            "the payload must never be reintroduced as a hand-typed literal string "
            "in the workflow itself"
        )

    def test_step_never_uses_set_plus_e_across_the_whole_step(self) -> None:
        """Task requirement: relaxed error handling must be narrowly
        scoped to individual curl invocations, never the entire step.
        """
        script = _find_step("deploy", self.STEP_NAME)["run"]
        set_plus_e_count = script.count("set +e")
        set_minus_e_count = script.count("set -e")
        assert set_plus_e_count >= 3, "expected POST/GET/DELETE to each narrowly relax -e"
        assert set_minus_e_count >= set_plus_e_count, (
            "every 'set +e' must be paired with a 'set -e' restoring strict mode "
            "immediately afterward"
        )

    def test_step_declares_explicit_curl_timeouts(self) -> None:
        script = _find_step("deploy", self.STEP_NAME)["run"]
        assert script.count("--connect-timeout") >= 3
        assert script.count("--max-time") >= 3

    # -- Task item 2's own 7 named cases -----------------------------------

    def test_delete_is_attempted_after_get_http_failure_status(self, tmp_path: Path) -> None:
        """Case 1: GET returns an HTTP failure status."""
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file, get_status="500")
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists(), "DELETE must be attempted even though GET returned 500"
        assert result.returncode != 0
        assert "smoke GET failed" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout

    def test_delete_is_attempted_after_get_curl_transport_failure(self, tmp_path: Path) -> None:
        """Case 2: GET `curl` exits nonzero without returning an HTTP
        status at all -- the exact bug this correction pass reproduced
        under real `bash -e -o pipefail` semantics before fixing it.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file, get_curl_exit=7)
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists(), (
            "DELETE must be attempted even though GET's own curl process failed outright"
        )
        assert result.returncode != 0
        assert "smoke GET transport failure" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout

    def test_delete_is_attempted_after_get_times_out(self, tmp_path: Path) -> None:
        """Case 3: GET times out -- modeled as `curl`'s own real
        exit code 28 ("Operation timeout"), since a real timeout is,
        from this script's own perspective, simply another nonzero
        `curl` process exit with no HTTP status ever returned.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file, get_curl_exit=28)
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists(), "DELETE must be attempted even though GET timed out"
        assert result.returncode != 0
        assert "smoke GET transport failure (curl exit 28)" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout

    def test_delete_is_attempted_after_a_controlled_post_get_assertion_failure(
        self, tmp_path: Path
    ) -> None:
        """Case 4: GET itself succeeds (200), but this step's own
        real post-GET integrity assertion (the GET response's
        `ingestion_id` must match the one POST returned) fails --
        proving the `trap ... EXIT` cleanup fires for an arbitrary
        later failure, not merely the two curl-specific ones above.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(
            tmp_path,
            marker_file=marker_file,
            get_status="200",
            get_ingestion_id="ing_a_different_id",
        )
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists(), (
            "DELETE must be attempted even after a post-GET assertion fails"
        )
        assert result.returncode != 0
        assert "mismatched ingestion_id" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout

    def test_delete_http_failure_status_fails_the_overall_step(self, tmp_path: Path) -> None:
        """Case 5: DELETE itself returns an HTTP failure status -- must
        upgrade an otherwise-fully-successful run to an overall failure.

        Sixth pass, exact reproduction: before this pass's fix, this exact
        scenario exited nonzero (as asserted below) *but its stdout still
        contained the literal "synthetic smoke test passed" line* -- the
        success message was printed right after the GET integrity check,
        before the `EXIT` trap's own `DELETE` attempt could ever fail. The
        final assertion below is what actually catches that: it failed
        against the pre-fix script and passes against the fix.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file, delete_status="500")
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists()
        assert result.returncode != 0, "a failed cleanup DELETE must fail the overall step"
        assert "smoke cleanup: DELETE failed" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout, (
            "a failed DELETE cleanup must never leave a false success message in the logs"
        )

    def test_delete_curl_transport_failure_fails_the_overall_step(self, tmp_path: Path) -> None:
        """Case 6: DELETE's own `curl` process exits nonzero -- must
        also fail the overall step, never silently swallowed, and (sixth
        pass) must never leave a false "passed" message in stdout either.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file, delete_curl_exit=7)
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists()
        assert result.returncode != 0, "a DELETE transport failure must fail the overall step"
        assert "smoke cleanup: DELETE transport failure" in result.stderr
        assert "synthetic smoke test passed" not in result.stdout

    def test_combined_get_failure_and_delete_cleanup_failure_preserves_primary_failure(
        self, tmp_path: Path
    ) -> None:
        """Sixth pass, task requirement 4: when the *primary* operation
        (GET) fails and the subsequent cleanup DELETE *also* fails, the
        original GET failure must remain the visible, first-reported
        reason, the cleanup failure must additionally be observable (never
        silently swallowed), the overall step must still fail, and no
        success message may ever appear.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(
            tmp_path, marker_file=marker_file, get_status="500", delete_status="500"
        )
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists(), "cleanup DELETE must still be attempted"
        assert result.returncode != 0
        assert "smoke GET failed" in result.stderr, "the primary failure must be preserved"
        assert "smoke cleanup: DELETE failed" in result.stderr, (
            "the cleanup failure must also be observable, not silently dropped"
        )
        assert result.stderr.index("smoke GET failed") < result.stderr.index(
            "smoke cleanup: DELETE failed"
        ), "the primary failure must be reported before the cleanup failure"
        assert "synthetic smoke test passed" not in result.stdout

    def test_full_successful_post_get_delete_path(self, tmp_path: Path) -> None:
        """Case 7: the ordinary successful path -- POST 201, GET 200
        with a matching `ingestion_id`, DELETE 200 -- must still exit 0,
        and (sixth pass) must print the success message exactly once,
        only after DELETE cleanup has genuinely completed.
        """
        marker_file = tmp_path / "delete-was-called"
        fake_bin = self._fake_bin_dir(tmp_path, marker_file=marker_file)
        result = self._run_step_under_real_shell_semantics(tmp_path, fake_bin)
        assert marker_file.exists()
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("synthetic smoke test passed") == 1

    # -- Secret-safety, across every case above -----------------------------

    def test_step_never_prints_the_token_authorization_header_ingestion_id_or_report_content(
        self, tmp_path: Path
    ) -> None:
        """Scans stdout/stderr (task's own explicit requirement) across
        several distinct code paths -- success, GET failure, and a
        cleanup failure -- for the sentinel token, the literal
        `Authorization:` header value, the ingestion ID, and the
        synthetic report's own `cluster_context` value.
        """
        secret_token = "a-very-secret-smoke-token-value"
        scenarios = [
            {},
            {"get_status": "500"},
            {"get_curl_exit": 7},
            {"delete_status": "500"},
            {"delete_curl_exit": 7},
        ]
        for i, overrides in enumerate(scenarios):
            scenario_dir = tmp_path / f"scenario-{i}"
            scenario_dir.mkdir()
            marker_file = scenario_dir / "delete-was-called"
            fake_bin = self._fake_bin_dir(scenario_dir, marker_file=marker_file, **overrides)
            result = self._run_step_under_real_shell_semantics(
                scenario_dir, fake_bin, smoke_token=secret_token
            )
            combined_output = result.stdout + result.stderr
            assert secret_token not in combined_output, f"scenario {overrides!r} leaked the token"
            assert f"Authorization: Bearer {secret_token}" not in combined_output
            assert "ing_test_smoke" not in combined_output, (
                f"scenario {overrides!r} leaked the ingestion_id"
            )
            assert "cog-smoke-test-cluster" not in combined_output, (
                f"scenario {overrides!r} leaked report content"
            )
