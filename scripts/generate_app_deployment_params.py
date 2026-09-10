#!/usr/bin/env python3
"""Generates a real, ephemeral ARM parameters file for `infra/azure/
app.bicep` (Phase 4G-A correction-pass item 2) from validated sources
ONLY: the already-applied `foundation` deployment's real, live outputs
(queried via `az deployment group show`), already-validated workflow
variables/secrets, and the exact image reference this invocation is
given -- **never** the checked-in, placeholder-valued
`infra/azure/app.bicepparam`, which must never reach a real `az
deployment` call (every value in it is a `REPLACE-WITH-*`/`REPLACE-AT-
DEPLOYMENT-TIME*` placeholder by design).

Used identically by both `.github/workflows/deploy-ingestion-azure.yml`'s
read-only `plan` job (to run `app.bicep`'s what-if against the exact
parameters `deploy` will use) and its `deploy` job itself -- the
generated file is uploaded as a workflow artifact by `plan` and
downloaded unchanged by `deploy` for the `deploy` action, so there is no
code path by which the reviewed plan and the applied deployment can
diverge. (The `rollback` action, which never runs `plan`, calls this
script itself with the rollback digest -- see the workflow file.)

Fails closed (raises `ParamGenerationError`, writes no output file) if:
- `az deployment group show` itself fails (foundation was never
  deployed, wrong resource group/deployment name, or the caller's
  identity lacks read access).
- Any required foundation output is missing, empty, or not a string.
- Any required foundation output, `name_prefix`, or `alert_email_address`
  still carries a checked-in `REPLACE-WITH-*`/`REPLACE-AT-DEPLOYMENT-
  TIME*` placeholder value -- which would mean foundation was deployed
  from an unedited `foundation.bicepparam` by mistake.
- `image_ref` does not *exactly* match
  `<registry>/cloudops-guard-ingestion-api@sha256:<64 lowercase hex
  characters>` (correction-pass item 7: a full-string match, never a
  substring/tag check -- rejects a wrong registry, a mutable tag, a
  suffix/query/second-`@` appended after a valid-looking digest,
  whitespace, and non-lowercase-hex/wrong-length digests), or whose
  registry component does not exactly equal the live `registryLoginServer`
  foundation output.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

_PLACEHOLDER_PREFIXES = ("REPLACE-WITH-", "REPLACE-AT-DEPLOYMENT-TIME")

#: Every `app.bicep` parameter sourced from a `foundation.bicep` output,
#: mapped to that output's exact name -- kept as one explicit list so a
#: parameter this script forgets to source is a loud `KeyError` in
#: `build_parameters_document`, never a silently-omitted ARM parameter.
_REQUIRED_FOUNDATION_OUTPUTS = (
    "environmentId",
    "registryLoginServer",
    "appIdentityId",
    "appIdentityClientId",
    "operatorIdentityId",
    "operatorIdentityClientId",
    "migrationIdentityId",
    "migrationIdentityClientId",
    "keyVaultUri",
    "postgresServerId",
    "blobEndpoint",
    "reportContainerName",
)


class ParamGenerationError(Exception):
    """Raised for any fail-closed condition below. Never caught silently
    by `main` -- always reported to stderr and turned into a non-zero
    exit code.
    """


def _looks_like_placeholder(value: str) -> bool:
    return any(value.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES)


def fetch_foundation_outputs(
    *,
    resource_group: str,
    deployment_name: str,
    az_command: list[str] | None = None,
) -> dict[str, str]:
    """Queries the real, already-applied `foundation` deployment's
    outputs via `az deployment group show`. `az_command` is overridable
    only for tests (a fake `az` shim) -- production callers always use
    the default, real `az` binary.
    """
    command = [
        *(az_command or ["az"]),
        "deployment",
        "group",
        "show",
        "--resource-group",
        resource_group,
        "--name",
        deployment_name,
        "--query",
        "properties.outputs",
        "-o",
        "json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    except subprocess.CalledProcessError as exc:
        raise ParamGenerationError(
            f"az deployment group show failed for deployment {deployment_name!r} in "
            f"resource group {resource_group!r} (was foundation ever actually deployed under "
            f"that exact name?): {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ParamGenerationError("az deployment group show timed out.") from exc
    except FileNotFoundError as exc:
        raise ParamGenerationError(f"could not run az: {exc}") from exc

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ParamGenerationError("az deployment group show did not return valid JSON.") from exc

    if not isinstance(raw, dict) or not raw:
        raise ParamGenerationError(
            f"foundation deployment {deployment_name!r} returned no outputs at all -- was it "
            "ever actually deployed, or was an empty/wrong deployment name given?"
        )

    outputs: dict[str, str] = {}
    for key in _REQUIRED_FOUNDATION_OUTPUTS:
        entry = raw.get(key)
        value = entry.get("value") if isinstance(entry, dict) else None
        if not value or not isinstance(value, str):
            raise ParamGenerationError(f"foundation output {key!r} is missing or empty.")
        if _looks_like_placeholder(value):
            raise ParamGenerationError(
                f"foundation output {key!r} is still a checked-in placeholder value "
                f"({value!r}) -- foundation was never really deployed."
            )
        outputs[key] = value
    return outputs


#: The one, exact shape a real image_ref must have -- a registry host
#: (no embedded `/`, so a second `@`/path/query cannot smuggle a
#: different registry or extra content past this check), the fixed
#: repository name, and a digest of exactly 64 lowercase hex characters,
#: with **nothing else** anywhere in the string (`fullmatch`, never
#: `search`/`in`, and no trailing/leading whitespace since `\s` is not
#: part of the pattern and the anchors are the true string boundaries,
#: not per-line `^`/`$`).
_IMAGE_REF_PATTERN = re.compile(
    r"^(?P<registry>[^/\s]+)/cloudops-guard-ingestion-api@sha256:(?P<digest>[0-9a-f]{64})$"
)


def validate_image_ref(image_ref: str, *, expected_registry_login_server: str) -> None:
    """**Correction pass, item 7**: the original version of this check
    only tested for the substring `'@sha256:'` anywhere in `image_ref`
    and a `':latest'` suffix -- reproduced directly, before this fix,
    accepting every one of: a completely different registry hostname
    (`evil.com/repo@sha256:...`), a trailing suffix appended after a
    valid-looking digest, an uppercase-hex digest, leading/trailing
    whitespace, a second `@` smuggling a different registry/path after
    the digest, a too-short digest, and a non-hexadecimal digest. Every
    one of those is now rejected by requiring an *exact, full-string*
    match against `_IMAGE_REF_PATTERN` -- and the registry component of
    that match must equal `expected_registry_login_server` (the live
    `foundation` deployment's own real output) exactly, byte for byte,
    never merely "looks plausible."
    """
    if not image_ref or _looks_like_placeholder(image_ref):
        raise ParamGenerationError(f"image_ref is missing or a placeholder: {image_ref!r}")
    match = _IMAGE_REF_PATTERN.fullmatch(image_ref)
    if not match:
        raise ParamGenerationError(
            "image_ref must exactly match "
            "'<registry>/cloudops-guard-ingestion-api@sha256:<64 lowercase hex characters>' "
            f"with nothing else (no tag, path, query, extra '@', or whitespace): {image_ref!r}"
        )
    if match.group("registry") != expected_registry_login_server:
        raise ParamGenerationError(
            f"image_ref's registry ({match.group('registry')!r}) does not match the live "
            f"foundation output registryLoginServer ({expected_registry_login_server!r}) -- "
            "refusing to deploy an image reference naming a different registry."
        )


def build_parameters_document(
    *,
    name_prefix: str,
    foundation_outputs: dict[str, str],
    image_ref: str,
    alert_email_address: str,
) -> dict:
    if not name_prefix or _looks_like_placeholder(name_prefix):
        raise ParamGenerationError(f"name_prefix is missing or a placeholder: {name_prefix!r}")
    if not alert_email_address or _looks_like_placeholder(alert_email_address):
        raise ParamGenerationError(
            f"alert_email_address is missing or a placeholder: {alert_email_address!r}"
        )
    missing = [key for key in _REQUIRED_FOUNDATION_OUTPUTS if key not in foundation_outputs]
    if missing:
        raise ParamGenerationError(f"foundation_outputs is missing required key(s): {missing}")
    validate_image_ref(
        image_ref, expected_registry_login_server=foundation_outputs["registryLoginServer"]
    )

    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "namePrefix": {"value": name_prefix},
            "environmentId": {"value": foundation_outputs["environmentId"]},
            "registryLoginServer": {"value": foundation_outputs["registryLoginServer"]},
            "appIdentityId": {"value": foundation_outputs["appIdentityId"]},
            "appIdentityClientId": {"value": foundation_outputs["appIdentityClientId"]},
            "operatorIdentityId": {"value": foundation_outputs["operatorIdentityId"]},
            "operatorIdentityClientId": {"value": foundation_outputs["operatorIdentityClientId"]},
            "migrationIdentityId": {"value": foundation_outputs["migrationIdentityId"]},
            "migrationIdentityClientId": {"value": foundation_outputs["migrationIdentityClientId"]},
            "keyVaultUri": {"value": foundation_outputs["keyVaultUri"]},
            "imageDigest": {"value": image_ref},
            "postgresServerId": {"value": foundation_outputs["postgresServerId"]},
            "blobAccountUrl": {"value": foundation_outputs["blobEndpoint"]},
            "blobContainerName": {"value": foundation_outputs["reportContainerName"]},
            "alertEmailAddress": {"value": alert_email_address},
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--foundation-deployment-name", required=True)
    parser.add_argument("--name-prefix", required=True)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--alert-email-address", required=True)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args(argv)

    try:
        foundation_outputs = fetch_foundation_outputs(
            resource_group=args.resource_group,
            deployment_name=args.foundation_deployment_name,
        )
        document = build_parameters_document(
            name_prefix=args.name_prefix,
            foundation_outputs=foundation_outputs,
            image_ref=args.image_ref,
            alert_email_address=args.alert_email_address,
        )
    except ParamGenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    with open(args.output_path, "w") as f:
        json.dump(document, f, indent=2)
    print(f"wrote {args.output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
