"""Tests for `scripts/generate_app_deployment_params.py` (Phase 4G-A
correction-pass item 2) -- the script that generates `app.bicep`'s real
deployment parameters from `foundation`'s live outputs instead of the
checked-in, placeholder-valued `infra/azure/app.bicepparam`. Pure stdlib;
no Azure extra required. `fetch_foundation_outputs`'s `az_command`
parameter is exercised with a real, executable fake `az` shell script
(never a `subprocess.run` mock) so this test actually exercises argument
parsing and subprocess plumbing exactly as production code will.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import generate_app_deployment_params as gadp  # noqa: E402

_GOOD_OUTPUTS = {
    "environmentId": "/subscriptions/x/resourceGroups/rg/providers/Microsoft.App/"
    "managedEnvironments/env",
    "registryLoginServer": "myacr.azurecr.io",
    "appIdentityId": "/subscriptions/x/.../app-identity",
    "appIdentityClientId": "11111111-1111-1111-1111-111111111111",
    "operatorIdentityId": "/subscriptions/x/.../operator-identity",
    "operatorIdentityClientId": "22222222-2222-2222-2222-222222222222",
    "migrationIdentityId": "/subscriptions/x/.../migration-identity",
    "migrationIdentityClientId": "33333333-3333-3333-3333-333333333333",
    "keyVaultUri": "https://mykv.vault.azure.net/",
    "postgresServerId": "/subscriptions/x/.../pg-server",
    "blobEndpoint": "https://mystorage.blob.core.windows.net/",
    "reportContainerName": "reports",
}

_GOOD_IMAGE_REF = "myacr.azurecr.io/cloudops-guard-ingestion-api@sha256:" + "a" * 64


def _make_fake_az(tmp_path: Path, *, stdout: str = "", exit_code: int = 0) -> list[str]:
    """Writes a real, executable fake `az` script that ignores its
    arguments and emits `stdout`/`exit_code` -- proves this module's own
    subprocess invocation (argument shape, `capture_output`, JSON
    parsing) actually works, not just that a mock was called correctly.
    """
    script = tmp_path / "fake-az"
    script.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.stdout.write({stdout!r})\nsys.exit({exit_code})\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return [sys.executable, str(script)]


def _outputs_json(outputs: dict[str, str]) -> str:
    return json.dumps({key: {"value": value} for key, value in outputs.items()})


class TestFetchFoundationOutputs:
    def test_returns_every_required_output_on_success(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_outputs_json(_GOOD_OUTPUTS))
        result = gadp.fetch_foundation_outputs(
            resource_group="rg", deployment_name="foundation", az_command=az
        )
        assert result == _GOOD_OUTPUTS

    def test_az_command_failure_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="", exit_code=1)
        with pytest.raises(gadp.ParamGenerationError, match="az deployment group show failed"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    def test_invalid_json_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="not json")
        with pytest.raises(gadp.ParamGenerationError, match="valid JSON"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    def test_empty_outputs_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="{}")
        with pytest.raises(gadp.ParamGenerationError, match="no outputs at all"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    def test_null_outputs_is_fail_closed(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="null")
        with pytest.raises(gadp.ParamGenerationError, match="no outputs at all"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    @pytest.mark.parametrize("missing_key", sorted(_GOOD_OUTPUTS))
    def test_missing_required_output_is_fail_closed(self, tmp_path: Path, missing_key: str) -> None:
        incomplete = {k: v for k, v in _GOOD_OUTPUTS.items() if k != missing_key}
        az = _make_fake_az(tmp_path, stdout=_outputs_json(incomplete))
        with pytest.raises(gadp.ParamGenerationError, match=f"{missing_key!r} is missing"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    def test_empty_string_output_is_fail_closed(self, tmp_path: Path) -> None:
        broken = {**_GOOD_OUTPUTS, "keyVaultUri": ""}
        az = _make_fake_az(tmp_path, stdout=_outputs_json(broken))
        with pytest.raises(gadp.ParamGenerationError, match="'keyVaultUri' is missing or empty"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )

    @pytest.mark.parametrize(
        "placeholder_value",
        [
            "REPLACE-WITH-FOUNDATION-OUTPUT-keyVaultUri",
            "REPLACE-AT-DEPLOYMENT-TIME-NEVER-COMMITTED",
        ],
    )
    def test_placeholder_output_is_fail_closed(
        self, tmp_path: Path, placeholder_value: str
    ) -> None:
        """The exact defect item 2 names: a checked-in placeholder value
        must never silently pass through to a real deployment. Reproduces
        it directly against this function before proving the guard
        stops it.
        """
        broken = {**_GOOD_OUTPUTS, "keyVaultUri": placeholder_value}
        az = _make_fake_az(tmp_path, stdout=_outputs_json(broken))
        with pytest.raises(gadp.ParamGenerationError, match="checked-in placeholder value"):
            gadp.fetch_foundation_outputs(
                resource_group="rg", deployment_name="foundation", az_command=az
            )


_GOOD_REGISTRY = "myacr.azurecr.io"
_GOOD_DIGEST_HEX = "a" * 64


class TestValidateImageRef:
    """Correction pass, item 7: `validate_image_ref` previously only
    checked for the substring `'@sha256:'` and a `':latest'` suffix --
    reproduced directly (before this fix) accepting a wrong registry
    hostname, a suffix appended after a valid-looking digest, an
    uppercase-hex digest, leading/trailing whitespace, a second `@`
    smuggling a different registry/path, a too-short digest, and a
    non-hexadecimal digest. Every case below is a real, independently
    reproduced acceptance under the *old* check -- confirmed to now be
    rejected under an exact full-string match requiring the registry
    component to equal the live `registryLoginServer` foundation output
    byte for byte.
    """

    def test_accepts_a_genuine_digest_pinned_reference(self) -> None:
        gadp.validate_image_ref(
            _GOOD_IMAGE_REF, expected_registry_login_server=_GOOD_REGISTRY
        )  # must not raise

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "",
            "REPLACE-WITH-DIGEST-PINNED-IMAGE-REFERENCE",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api:latest",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api:build-abc123",  # no digest at all
            # A different registry entirely -- the exact bug that would
            # have let a candidate be pushed to/pulled from an
            # attacker-controlled or merely-wrong registry.
            f"evil.example/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}",
            # A suffix appended after an otherwise-valid digest --
            # the old substring check ('@sha256:' in ref) never
            # noticed trailing content.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}-extra",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX};rm -rf /",
            # Uppercase-hex digest -- OCI/registry digests are always
            # lowercase; accepting uppercase risks two different string
            # values being treated as the same, real digest downstream.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{'A' * 64}",
            # Leading/trailing whitespace.
            f" {_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX} ",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}\n",
            # A second '@' smuggling a different registry/path after the
            # digest.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}@evil.example/x",
            # Too-short / too-long digest.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{'a' * 63}",
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{'a' * 65}",
            # Non-hexadecimal digest.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{'g' * 64}",
            # A path component smuggled into the registry position (the
            # pattern's own `[^/\\s]+` for the registry group must not
            # allow an embedded slash to redefine the repository).
            f"{_GOOD_REGISTRY}/evil-path/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}",
            # A query string appended.
            f"{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}?x=1",
        ],
    )
    def test_rejects_every_unsafe_image_ref(self, bad_ref: str) -> None:
        with pytest.raises(gadp.ParamGenerationError):
            gadp.validate_image_ref(bad_ref, expected_registry_login_server=_GOOD_REGISTRY)

    def test_rejects_a_ref_naming_a_different_registry_than_expected(self) -> None:
        """Even a perfectly well-formed reference must be rejected if its
        registry does not match the live foundation output exactly --
        this is a distinct guarantee from "is the string shaped like a
        digest reference," which the parametrized cases above already
        cover for the wrong-registry case in isolation.
        """
        ref = (
            f"a-completely-different-registry.azurecr.io/"
            f"cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}"
        )
        with pytest.raises(gadp.ParamGenerationError, match="does not match"):
            gadp.validate_image_ref(ref, expected_registry_login_server=_GOOD_REGISTRY)

    def test_registry_comparison_is_exact_not_a_substring_match(self) -> None:
        """A registry name that merely *contains* the expected one as a
        substring (e.g. as a suffix of a longer, attacker-chosen
        hostname) must still be rejected -- proves the comparison is
        `==`, never `in`/`endswith`.
        """
        ref = f"evil-{_GOOD_REGISTRY}/cloudops-guard-ingestion-api@sha256:{_GOOD_DIGEST_HEX}"
        with pytest.raises(gadp.ParamGenerationError):
            gadp.validate_image_ref(ref, expected_registry_login_server=_GOOD_REGISTRY)


class TestBuildParametersDocument:
    def test_produces_the_expected_arm_parameters_shape(self) -> None:
        document = gadp.build_parameters_document(
            name_prefix="cog-ingestion-pilot",
            foundation_outputs=_GOOD_OUTPUTS,
            image_ref=_GOOD_IMAGE_REF,
            alert_email_address="oncall@example.com",
        )
        assert document["parameters"]["namePrefix"]["value"] == "cog-ingestion-pilot"
        assert document["parameters"]["imageDigest"]["value"] == _GOOD_IMAGE_REF
        assert document["parameters"]["blobAccountUrl"]["value"] == _GOOD_OUTPUTS["blobEndpoint"]
        assert (
            document["parameters"]["blobContainerName"]["value"]
            == _GOOD_OUTPUTS["reportContainerName"]
        )
        # Every parameter's value is a plain, non-placeholder string --
        # never a checked-in REPLACE-WITH-*/REPLACE-AT-DEPLOYMENT-TIME*
        # sentinel reaching the generated file.
        for entry in document["parameters"].values():
            assert not gadp._looks_like_placeholder(entry["value"])

    @pytest.mark.parametrize("bad_name_prefix", ["", "REPLACE-WITH-NAME-PREFIX"])
    def test_rejects_a_placeholder_name_prefix(self, bad_name_prefix: str) -> None:
        with pytest.raises(gadp.ParamGenerationError, match="name_prefix"):
            gadp.build_parameters_document(
                name_prefix=bad_name_prefix,
                foundation_outputs=_GOOD_OUTPUTS,
                image_ref=_GOOD_IMAGE_REF,
                alert_email_address="oncall@example.com",
            )

    @pytest.mark.parametrize("bad_email", ["", "REPLACE-WITH-ONCALL-EMAIL-ADDRESS"])
    def test_rejects_a_missing_or_placeholder_alert_email(self, bad_email: str) -> None:
        with pytest.raises(gadp.ParamGenerationError, match="alert_email_address"):
            gadp.build_parameters_document(
                name_prefix="cog-ingestion-pilot",
                foundation_outputs=_GOOD_OUTPUTS,
                image_ref=_GOOD_IMAGE_REF,
                alert_email_address=bad_email,
            )


class TestMainEndToEnd:
    def test_writes_a_real_parameters_file_on_success(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout=_outputs_json(_GOOD_OUTPUTS))
        output_path = tmp_path / "app-deployment-params.json"

        # main() always shells out to the literal `az` on PATH -- prepend
        # our fake az's directory so `main`'s own `subprocess.run(["az",
        # ...])` call resolves to it, proving the full CLI entrypoint
        # (argparse -> fetch -> build -> write) end to end.
        fake_az_dir = str(Path(az[1]).parent)
        os.symlink(az[1], Path(fake_az_dir) / "az")
        old_path = os.environ["PATH"]
        os.environ["PATH"] = fake_az_dir + os.pathsep + old_path
        try:
            exit_code = gadp.main(
                [
                    "--resource-group",
                    "rg",
                    "--foundation-deployment-name",
                    "foundation",
                    "--name-prefix",
                    "cog-ingestion-pilot",
                    "--image-ref",
                    _GOOD_IMAGE_REF,
                    "--alert-email-address",
                    "oncall@example.com",
                    "--output-path",
                    str(output_path),
                ]
            )
        finally:
            os.environ["PATH"] = old_path

        assert exit_code == 0
        written = json.loads(output_path.read_text())
        assert written["parameters"]["imageDigest"]["value"] == _GOOD_IMAGE_REF

    def test_writes_nothing_on_failure(self, tmp_path: Path) -> None:
        az = _make_fake_az(tmp_path, stdout="", exit_code=1)
        fake_az_dir = str(Path(az[1]).parent)
        os.symlink(az[1], Path(fake_az_dir) / "az")
        output_path = tmp_path / "app-deployment-params.json"
        old_path = os.environ["PATH"]
        os.environ["PATH"] = fake_az_dir + os.pathsep + old_path
        try:
            exit_code = gadp.main(
                [
                    "--resource-group",
                    "rg",
                    "--foundation-deployment-name",
                    "foundation",
                    "--name-prefix",
                    "cog-ingestion-pilot",
                    "--image-ref",
                    _GOOD_IMAGE_REF,
                    "--alert-email-address",
                    "oncall@example.com",
                    "--output-path",
                    str(output_path),
                ]
            )
        finally:
            os.environ["PATH"] = old_path

        assert exit_code == 1
        assert not output_path.exists()
