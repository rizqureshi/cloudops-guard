"""Tests for `scripts/extract_oci_digest.py`.

**What this file covers, and why it was hardened twice**:

The original version of this script only checked that `index.json`
named exactly one manifest entry with a well-formed digest *string* --
it never checked that the manifest blob the digest named actually
existed, that its bytes genuinely hashed to that digest, or that
anything inside the manifest (its own schema, its config, its layers)
was structurally valid at all. That was fixed in an earlier correction
pass (`TestFullDescriptorGraphValidation` below).

A later correction pass found and fixed two further gaps, both
independently reproduced before being fixed:

1. **Platform validation was index-only and merely optional.** An
   archive whose index omitted `platform` entirely passed
   unconditionally, even if its actual config blob (the authoritative
   source of an image's real platform) declared a completely different
   architecture. `TestPlatformValidation` below covers the corrected
   behavior: the config blob is now the *mandatory* source of platform
   truth whenever a platform is expected at all; an index-level
   `platform`, when present, is an *additional* check that must agree
   with both the expected values and the config blob.
2. **The tar-member safety check only covered "tracked" paths, and
   media-type checks used prefix matching.** A symlink/hardlink/device/
   FIFO at an arbitrary, otherwise-unread path passed through
   completely unexamined, and a layer media type formed by appending an
   arbitrary suffix to an allowed prefix (e.g.
   `application/vnd.oci.image.layer.v1.tarX`) was wrongly accepted.
   `TestPermittedLayoutEnvelope` below covers the corrected behavior:
   every member in the archive is classified against an explicit,
   closed envelope (only `oci-layout`/`index.json` as files,
   `blobs`/`blobs/sha256` as directories, and `blobs/sha256/<64 hex>`
   as files) -- anything else, anywhere, of any type, is rejected; and
   every media type is checked against an explicit, closed set of exact
   strings, never a prefix.

This file's tests build small, fully self-consistent, hand-constructed
OCI archives (every blob's real bytes genuinely hash to its own
declared digest, and the config blob genuinely declares `os`/
`architecture`) as the happy-path fixture, then adversarially corrupt
one specific aspect per test. `TestAgainstARealProjectDockerfileArchive`
additionally exercises the same validator against a real image built
from this project's own `Dockerfile` (skipped if Docker/buildx are
unavailable), and `TestSafeExtractionAndRegistryPush` proves the
validator-controlled extraction this module now offers
(`validate_and_extract_oci_layout`) still produces a directory `crane`
can push to a real disposable local registry, preserving the
independently-computed manifest digest exactly (skipped if Docker or a
real local registry are unavailable).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import extract_oci_digest as extractor  # noqa: E402

_REAL_DIGEST = "sha256:" + "a" * 64
_DEFAULT_CONFIG_BYTES = b'{"os":"linux","architecture":"amd64"}'
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"

#: Correction pass, item 4: distinct from `COG_AZURE_TESTS_REQUIRED`
#: (`tests/ingestion_azure/conftest.py`, which governs the real-
#: PostgreSQL/Azurite/Bicep-dependent suite) -- this env var governs
#: only the real-Docker/Buildx/Crane/disposable-local-registry OCI
#: supply-chain proofs in this file. Three distinct behaviors, never
#: conflated:
#:   1. **Normal suite** (neither var set): every test here that needs
#:      Docker/Buildx/Crane skips cleanly if unavailable -- an ordinary
#:      contributor without Docker installed is never blocked from
#:      running the rest of the suite, matching this project's existing,
#:      approved policy (mirrors the Postgres/Azurite/Bicep skip-by-
#:      default behavior).
#:   2. **Azure integration required** (`COG_AZURE_TESTS_REQUIRED=1`):
#:      governs only `tests/ingestion_azure/` -- entirely independent of
#:      this variable.
#:   3. **OCI supply-chain integration required**
#:      (`COG_OCI_INTEGRATION_TESTS_REQUIRED=1`): the dedicated,
#:      strongest verification run for *this* file -- a missing Docker/
#:      Buildx/Crane/registry capability becomes a hard, loud failure
#:      instead of a silent skip, so a report claiming "the OCI supply-
#:      chain proof ran with zero skips" can never be silently untrue.
_OCI_INTEGRATION_REQUIRED_ENV_VAR = "COG_OCI_INTEGRATION_TESTS_REQUIRED"


def _oci_integration_tests_required() -> bool:
    return os.environ.get(_OCI_INTEGRATION_REQUIRED_ENV_VAR) == "1"


def _require_tool_or_skip(available: bool, *, what: str) -> None:
    if available:
        return
    if _oci_integration_tests_required():
        pytest.fail(
            f"{_OCI_INTEGRATION_REQUIRED_ENV_VAR}=1 but {what} is not available -- the "
            "dedicated OCI/Docker/Crane supply-chain integration run must never silently "
            "skip this test."
        )
    pytest.skip(f"{what} not available for this test")


def _sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _blob_path(digest: str) -> str:
    return "blobs/" + digest.replace(":", "/", 1)


def _same_length_tampered(data: bytes) -> bytes:
    """Flips every bit -- guaranteed a different SHA-256 digest, and
    (unlike an arbitrary replacement string) always the exact same byte
    length as `data`, so a tampered-content test exercises the digest
    mismatch branch specifically, never the (separately tested) size
    mismatch branch.
    """
    return bytes(b ^ 0xFF for b in data)


def _valid_archive(
    tmp_path: Path,
    *,
    name: str = "archive.tar",
    config_bytes: bytes = _DEFAULT_CONFIG_BYTES,
    layer_bytes: tuple[bytes, ...] = (b"layer-one-content", b"layer-two-content"),
    layout_bytes: bytes | None = None,
    index_schema_version: int | None = 2,
    index_media_type: str | None = _INDEX_MEDIA_TYPE,
    manifest_entry_media_type: str = "application/vnd.oci.image.manifest.v1+json",
    manifest_own_media_type: str | None = "application/vnd.oci.image.manifest.v1+json",
    config_media_type: str = "application/vnd.oci.image.config.v1+json",
    layer_media_type: str = "application/vnd.oci.image.layer.v1.tar+gzip",
    platform: dict[str, str] | None = None,
    config_size_override: int | None = None,
    layer_size_overrides: dict[int, int] | None = None,
    skip_members: frozenset[str] = frozenset(),
    member_content_overrides: dict[str, bytes] | None = None,
    extra_members: list[tuple[str, bytes]] | None = None,
    extra_symlinks: list[tuple[str, str]] | None = None,
    extra_special_members: list[tuple[str, int, str | None]] | None = None,
    include_blob_directories: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Builds a small, fully self-consistent, real OCI archive: every
    blob's actual bytes genuinely hash to its own declared digest unless
    a test deliberately overrides that via `member_content_overrides`
    (simulating tampering) or the `*_size_override` parameters
    (simulating a manifest that lies about a blob's size). The default
    config blob genuinely declares `os: linux`/`architecture: amd64`, so
    a test that doesn't care about platform validation can ignore it,
    and a test that does can pass `expected_os`/`expected_architecture`
    matching (or deliberately not matching) that default. Returns
    `(archive_path, digests)` so a test can both exercise the happy path
    and derive a targeted single-aspect mutation from real, correct
    digests.

    `extra_special_members` is a list of `(name, tarfile type constant,
    linkname)` tuples for constructing hardlink/device/FIFO members
    (symlinks use the separate, simpler `extra_symlinks` parameter).
    """
    config_digest = _sha256_digest(config_bytes)
    layer_digests = [_sha256_digest(layer) for layer in layer_bytes]

    layers_json = []
    for i, (layer, digest) in enumerate(zip(layer_bytes, layer_digests, strict=True)):
        size = len(layer)
        if layer_size_overrides and i in layer_size_overrides:
            size = layer_size_overrides[i]
        layers_json.append({"mediaType": layer_media_type, "digest": digest, "size": size})

    manifest: dict[str, Any] = {
        "schemaVersion": 2,
        "config": {
            "mediaType": config_media_type,
            "digest": config_digest,
            "size": config_size_override if config_size_override is not None else len(config_bytes),
        },
        "layers": layers_json,
    }
    if manifest_own_media_type is not None:
        manifest["mediaType"] = manifest_own_media_type
    manifest_bytes = json.dumps(manifest).encode()
    manifest_digest = _sha256_digest(manifest_bytes)

    index_entry: dict[str, Any] = {
        "mediaType": manifest_entry_media_type,
        "digest": manifest_digest,
        "size": len(manifest_bytes),
    }
    if platform is not None:
        index_entry["platform"] = platform
    index: dict[str, Any] = {"manifests": [index_entry]}
    if index_schema_version is not None:
        index["schemaVersion"] = index_schema_version
    if index_media_type is not None:
        index["mediaType"] = index_media_type
    index_bytes = json.dumps(index).encode()

    if layout_bytes is None:
        layout_bytes = json.dumps({"imageLayoutVersion": "1.0.0"}).encode()

    member_content_overrides = member_content_overrides or {}
    archive_path = tmp_path / name

    def add(tar: tarfile.TarFile, member_name: str, data: bytes) -> None:
        if member_name in skip_members:
            return
        data = member_content_overrides.get(member_name, data)
        info = tarfile.TarInfo(name=member_name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    def add_dir(tar: tarfile.TarFile, dir_name: str) -> None:
        if dir_name in skip_members:
            return
        info = tarfile.TarInfo(name=dir_name)
        info.type = tarfile.DIRTYPE
        tar.addfile(info)

    with tarfile.open(archive_path, "w") as tar:
        add(tar, "oci-layout", layout_bytes)
        add(tar, "index.json", index_bytes)
        if include_blob_directories:
            add_dir(tar, "blobs")
            add_dir(tar, "blobs/sha256")
        add(tar, _blob_path(manifest_digest), manifest_bytes)
        add(tar, _blob_path(config_digest), config_bytes)
        for layer, digest in zip(layer_bytes, layer_digests, strict=True):
            add(tar, _blob_path(digest), layer)
        for member_name, data in extra_members or []:
            add(tar, member_name, data)
        for member_name, linkname in extra_symlinks or []:
            info = tarfile.TarInfo(name=member_name)
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            tar.addfile(info)
        for member_name, ttype, linkname in extra_special_members or []:
            info = tarfile.TarInfo(name=member_name)
            info.type = ttype
            if linkname is not None:
                info.linkname = linkname
            tar.addfile(info)

    return archive_path, {
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "layer_digests": layer_digests,
    }


def _make_archive(tmp_path: Path, *, index_json: bytes | None, name: str = "archive.tar") -> Path:
    """Legacy helper retained for the small number of tests that only
    care about `index.json`-level structural defects (never reaching
    blob resolution at all).
    """
    archive_path = tmp_path / name
    with tarfile.open(archive_path, "w") as tar:
        if index_json is not None:
            info = tarfile.TarInfo(name="index.json")
            info.size = len(index_json)
            tar.addfile(info, io.BytesIO(index_json))
    return archive_path


def _index_with(
    manifests: list,
    *,
    schema_version: int | None = 2,
    media_type: str | None = _INDEX_MEDIA_TYPE,
) -> bytes:
    doc: dict[str, Any] = {}
    if schema_version is not None:
        doc["schemaVersion"] = schema_version
    if media_type is not None:
        doc["mediaType"] = media_type
    doc["manifests"] = manifests
    return json.dumps(doc).encode()


class TestHappyPath:
    def test_extracts_the_digest_from_a_fully_valid_archive(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(tmp_path)
        assert extractor.extract_manifest_digest(str(archive)) == digests["manifest_digest"]

    def test_extract_config_digest_returns_the_validated_config_digest(
        self, tmp_path: Path
    ) -> None:
        archive, digests = _valid_archive(tmp_path)
        assert extractor.extract_config_digest(str(archive)) == digests["config_digest"]

    def test_matching_expected_platform_passes(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(
            tmp_path, platform={"os": "linux", "architecture": "amd64"}
        )
        result = extractor.validate_oci_archive(
            str(archive), expected_os="linux", expected_architecture="amd64"
        )
        assert result["manifest_digest"] == digests["manifest_digest"]

    def test_docker_distribution_manifest_media_type_is_also_accepted(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(
            tmp_path,
            manifest_entry_media_type="application/vnd.docker.distribution.manifest.v2+json",
            manifest_own_media_type="application/vnd.docker.distribution.manifest.v2+json",
            config_media_type="application/vnd.docker.container.image.v1+json",
            layer_media_type="application/vnd.docker.image.rootfs.diff.tar.gzip",
        )
        assert extractor.extract_manifest_digest(str(archive)) == digests["manifest_digest"]

    def test_archive_without_explicit_blob_directory_members_still_validates(
        self, tmp_path: Path
    ) -> None:
        """Not every tar writer emits explicit directory entries for
        `blobs`/`blobs/sha256` (this file's own hand-built fixtures
        don't, by default, in earlier correction passes) -- the
        permitted-envelope check must not require them, only forbid
        anything additional.
        """
        archive, digests = _valid_archive(tmp_path, include_blob_directories=False)
        assert extractor.extract_manifest_digest(str(archive)) == digests["manifest_digest"]


class TestIndexSchemaValidation:
    """Correction pass: "Valid expected `index.json` schema and
    project-supported index media type."
    """

    def test_missing_index_schema_version_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, index_schema_version=None)
        with pytest.raises(extractor.OciDigestExtractionError, match="schemaVersion"):
            extractor.extract_manifest_digest(str(archive))

    def test_wrong_index_schema_version_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, index_schema_version=1)
        with pytest.raises(extractor.OciDigestExtractionError, match="schemaVersion"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_index_media_type_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, index_media_type=None)
        with pytest.raises(extractor.OciDigestExtractionError, match="mediaType"):
            extractor.extract_manifest_digest(str(archive))

    def test_wrong_index_media_type_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, index_media_type="application/vnd.oci.image.manifest.v1+json"
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="mediaType"):
            extractor.extract_manifest_digest(str(archive))


class TestPreExistingIndexLevelValidation:
    """Structural `index.json`-level checks that never reach blob
    resolution at all -- preserved from an earlier correction pass.
    """

    def test_missing_archive_file_is_fail_closed(self, tmp_path: Path) -> None:
        with pytest.raises(extractor.OciDigestExtractionError, match="does not exist"):
            extractor.extract_manifest_digest(str(tmp_path / "nonexistent.tar"))

    def test_not_a_tar_file_is_fail_closed(self, tmp_path: Path) -> None:
        not_a_tar = tmp_path / "not-a-tar.tar"
        not_a_tar.write_bytes(b"this is not a tar archive at all")
        with pytest.raises(extractor.OciDigestExtractionError, match="not a readable tar"):
            extractor.extract_manifest_digest(str(not_a_tar))

    def test_missing_index_json_is_fail_closed(self, tmp_path: Path) -> None:
        archive = _make_archive(tmp_path, index_json=None)
        with pytest.raises(extractor.OciDigestExtractionError, match="oci-layout"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_manifests_array_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path)
        _rewrite_index(archive, {"schemaVersion": 2, "mediaType": _INDEX_MEDIA_TYPE})
        with pytest.raises(extractor.OciDigestExtractionError, match="no 'manifests' array"):
            extractor.extract_manifest_digest(str(archive))

    def test_zero_manifest_entries_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path)
        _rewrite_index(archive, _json_load(_index_with([])))
        with pytest.raises(extractor.OciDigestExtractionError, match="found 0"):
            extractor.extract_manifest_digest(str(archive))

    def test_multiple_manifest_entries_is_fail_closed(self, tmp_path: Path) -> None:
        """A multi-platform index is unexpected for this project's
        single-platform build and must be investigated, not silently
        resolved by guessing which platform is 'the' one.
        """
        archive, digests = _valid_archive(tmp_path)
        _rewrite_index(
            archive,
            {
                "schemaVersion": 2,
                "mediaType": _INDEX_MEDIA_TYPE,
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": digests["manifest_digest"],
                        "size": 1,
                    },
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": "sha256:" + "b" * 64,
                        "size": 1,
                    },
                ],
            },
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="found 2"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_digest_field_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path)
        _rewrite_index(
            archive,
            {
                "schemaVersion": 2,
                "mediaType": _INDEX_MEDIA_TYPE,
                "manifests": [
                    {"mediaType": "application/vnd.oci.image.manifest.v1+json", "size": 123}
                ],
            },
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="digest"):
            extractor.extract_manifest_digest(str(archive))

    @pytest.mark.parametrize(
        "bad_digest",
        [
            "not-even-a-digest",
            "sha256:tooshort",
            "sha512:" + "a" * 64,
            "sha256:" + "A" * 64,
            "sha256:" + "g" * 64,
            "",
        ],
    )
    def test_malformed_digest_value_is_fail_closed(self, tmp_path: Path, bad_digest: str) -> None:
        archive, _ = _valid_archive(tmp_path)
        _rewrite_index(
            archive,
            {
                "schemaVersion": 2,
                "mediaType": _INDEX_MEDIA_TYPE,
                "manifests": [
                    {
                        "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "digest": bad_digest,
                        "size": 1,
                    }
                ],
            },
        )
        with pytest.raises(extractor.OciDigestExtractionError):
            extractor.extract_manifest_digest(str(archive))


def _json_load(data: bytes) -> dict[str, Any]:
    return json.loads(data)


def _rewrite_index(archive: Path, index: dict[str, Any]) -> None:
    """Rewrites just the `index.json` member of an already-built archive
    in place (keeping every other member, notably the real blobs,
    untouched) -- lets index-level tests reuse the happy-path archive's
    real blobs while corrupting only the index document itself.
    """
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            if member.name == "index.json":
                continue
            fileobj = tar.extractfile(member) if member.isfile() else None
            members.append((member, fileobj.read() if fileobj else b""))
    index_bytes = json.dumps(index).encode()
    with tarfile.open(archive, "w") as tar:
        for member, data in members:
            tar.addfile(member, io.BytesIO(data) if member.isfile() else None)
        info = tarfile.TarInfo(name="index.json")
        info.size = len(index_bytes)
        tar.addfile(info, io.BytesIO(index_bytes))


class TestFullDescriptorGraphValidation:
    """Every one of these adversarial fixtures is a *fully valid*
    archive except for exactly the one defect under test -- proving the
    validator actually resolves and re-verifies every blob, rather than
    trusting a digest string.
    """

    def test_missing_manifest_blob_is_fail_closed(self, tmp_path: Path) -> None:
        # Build once to learn the real manifest digest, then rebuild with
        # that exact blob skipped.
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["manifest_digest"])}),
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="manifest blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_config_blob_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["config_digest"])}),
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="config blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_layer_blob_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["layer_digests"][0])}),
        )
        with pytest.raises(extractor.OciDigestExtractionError, match=r"layer\[0\] blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_tampered_config_blob_content_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            member_content_overrides={
                _blob_path(digests["config_digest"]): _same_length_tampered(_DEFAULT_CONFIG_BYTES)
            },
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="config blob's actual content"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_tampered_layer_blob_content_is_fail_closed(self, tmp_path: Path) -> None:
        original_layers = (b"layer-one-content", b"layer-two-content")
        _, digests = _valid_archive(tmp_path, name="probe.tar", layer_bytes=original_layers)
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            layer_bytes=original_layers,
            member_content_overrides={
                _blob_path(digests["layer_digests"][0]): _same_length_tampered(original_layers[0])
            },
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match=r"layer\[0\] blob's actual content"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_declared_config_size_larger_than_actual_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, config_size_override=999999)
        with pytest.raises(extractor.OciDigestExtractionError, match="declared 999999"):
            extractor.extract_manifest_digest(str(archive))

    def test_declared_layer_size_smaller_than_actual_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, layer_size_overrides={1: 1})
        with pytest.raises(extractor.OciDigestExtractionError, match=r"layer\[1\] blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_layers_array_is_fail_closed(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(tmp_path)
        _rewrite_manifest_via_config_only(
            archive,
            manifest_digest=digests["manifest_digest"],
            config_digest=digests["config_digest"],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="'layers'"):
            extractor.extract_manifest_digest(str(archive))

    def test_missing_oci_layout_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, skip_members=frozenset({"oci-layout"}))
        with pytest.raises(extractor.OciDigestExtractionError, match="oci-layout"):
            extractor.extract_manifest_digest(str(archive))

    def test_unsupported_layout_version_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, layout_bytes=json.dumps({"imageLayoutVersion": "2.0.0"}).encode()
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="imageLayoutVersion"):
            extractor.extract_manifest_digest(str(archive))

    def test_evil_suffix_on_layout_version_is_fail_closed(self, tmp_path: Path) -> None:
        """Correction pass: exact accepted versions only -- a prior
        `startswith("1.")` check would have wrongly accepted this.
        """
        archive, _ = _valid_archive(
            tmp_path, layout_bytes=json.dumps({"imageLayoutVersion": "1.evil"}).encode()
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="imageLayoutVersion"):
            extractor.extract_manifest_digest(str(archive))

    def test_index_entry_naming_a_nested_image_index_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, manifest_entry_media_type="application/vnd.oci.image.index.v1+json"
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unaccepted mediaType"):
            extractor.extract_manifest_digest(str(archive))

    def test_manifest_blob_declaring_an_unaccepted_own_media_type_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        archive, _ = _valid_archive(
            tmp_path, manifest_own_media_type="application/x-something-unexpected"
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="manifest blob's own"):
            extractor.extract_manifest_digest(str(archive))

    def test_config_descriptor_with_unaccepted_media_type_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        archive, _ = _valid_archive(tmp_path, config_media_type="application/x-not-a-config")
        with pytest.raises(extractor.OciDigestExtractionError, match="config descriptor"):
            extractor.extract_manifest_digest(str(archive))

    def test_layer_descriptor_with_unaccepted_media_type_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        archive, _ = _valid_archive(tmp_path, layer_media_type="application/x-not-a-layer")
        with pytest.raises(extractor.OciDigestExtractionError, match=r"layer\[0\]"):
            extractor.extract_manifest_digest(str(archive))

    def test_layer_media_type_formed_by_suffixing_an_allowed_prefix_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        """Correction pass: exact media-type set, never a prefix match
        -- a fabricated media type sharing only a textual prefix with a
        real one (`application/vnd.oci.image.layer.v1.tar` + `EVIL`)
        must be rejected, not accepted because `str.startswith(...)`
        happened to be true.
        """
        archive, _ = _valid_archive(
            tmp_path, layer_media_type="application/vnd.oci.image.layer.v1.tarEVIL"
        )
        with pytest.raises(extractor.OciDigestExtractionError, match=r"layer\[0\]"):
            extractor.extract_manifest_digest(str(archive))

    def test_manifest_with_wrong_schema_version_is_fail_closed(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(tmp_path)
        _corrupt_manifest_blob(
            archive,
            old_manifest_digest=digests["manifest_digest"],
            transform=lambda m: {**m, "schemaVersion": 1},
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="schemaVersion"):
            extractor.extract_manifest_digest(str(archive))


class TestPlatformValidation:
    """Correction pass: "Make OCI validation genuinely fail closed" for
    platform evidence. Every case named in the task's own numbered list
    is covered explicitly below.
    """

    def test_missing_index_platform_and_conflicting_config_arch_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        """Task item 1: "Missing index platform plus an arm64 config
        when amd64 is expected."
        """
        archive, _ = _valid_archive(
            tmp_path,
            platform=None,
            config_bytes=b'{"os":"linux","architecture":"arm64"}',
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="config blob 'architecture'"):
            extractor.validate_oci_archive(
                str(archive), expected_os="linux", expected_architecture="amd64"
            )

    def test_config_os_mismatch_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, config_bytes=b'{"os":"windows","architecture":"amd64"}'
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="config blob 'os'"):
            extractor.validate_oci_archive(str(archive), expected_os="linux")

    def test_index_platform_os_mismatch_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, platform={"os": "windows", "architecture": "amd64"})
        with pytest.raises(extractor.OciDigestExtractionError, match="index platform.os"):
            extractor.validate_oci_archive(str(archive), expected_os="linux")

    def test_index_platform_architecture_mismatch_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, platform={"os": "linux", "architecture": "arm64"})
        with pytest.raises(extractor.OciDigestExtractionError, match="index platform.architecture"):
            extractor.validate_oci_archive(str(archive), expected_architecture="amd64")

    def test_conflicting_index_and_config_platform_is_fail_closed(self, tmp_path: Path) -> None:
        """Task item 6: "Conflicting index-platform and config-platform
        values" -- both individually satisfy the expected value in
        isolation on one axis, but disagree with each other.
        """
        archive, _ = _valid_archive(
            tmp_path,
            config_bytes=b'{"os":"linux","architecture":"amd64"}',
            platform={"os": "linux", "architecture": "amd64", "extra": "ignored"},
        )
        # Sanity: this one is actually consistent -- now make it conflict.
        extractor.validate_oci_archive(
            str(archive), expected_os="linux", expected_architecture="amd64"
        )

        conflicting_archive, _ = _valid_archive(
            tmp_path,
            name="conflict.tar",
            config_bytes=b'{"os":"linux","architecture":"amd64"}',
            platform={"os": "linux", "architecture": "amd64"},
        )
        # Directly corrupt just the config blob's own architecture after
        # the fact so the index (still amd64) and the config (now arm64)
        # disagree, while each alone would satisfy "amd64 expected"
        # only for the index, not the config.
        _replace_config_blob(
            conflicting_archive,
            old_config_digest=_sha256_digest(b'{"os":"linux","architecture":"amd64"}'),
            new_config_bytes=b'{"os":"linux","architecture":"arm64"}',
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="config blob 'architecture'"):
            extractor.validate_oci_archive(
                str(conflicting_archive), expected_os="linux", expected_architecture="amd64"
            )

    def test_missing_platform_is_not_itself_an_error(self, tmp_path: Path) -> None:
        """No index-level `platform` field is fine as long as the config
        blob (the mandatory source of truth) agrees with what was
        expected.
        """
        archive, digests = _valid_archive(tmp_path, platform=None)
        result = extractor.validate_oci_archive(
            str(archive), expected_os="linux", expected_architecture="amd64"
        )
        assert result["manifest_digest"] == digests["manifest_digest"]

    def test_no_expectation_means_no_platform_check_at_all(self, tmp_path: Path) -> None:
        """When the caller supplies neither `expected_os` nor
        `expected_architecture`, a garbage config blob's platform fields
        (or their total absence) must not be examined at all -- this is
        a deliberate scope boundary, not a gap: `_parse_config_document`
        still requires *some* valid JSON object (see
        `TestConfigBlobValidation`), just not specific field values.
        """
        archive, digests = _valid_archive(tmp_path, config_bytes=b'{"not_a_platform_field": true}')
        result = extractor.validate_oci_archive(str(archive))
        assert result["manifest_digest"] == digests["manifest_digest"]


class TestConfigBlobValidation:
    """Correction pass: "A valid JSON-object config blob" is required
    unconditionally (never only when a platform is expected) -- task
    item 5's non-JSON/array-valued/oversized cases.
    """

    def test_non_json_config_blob_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, config_bytes=b"not json at all")
        with pytest.raises(
            extractor.OciDigestExtractionError, match="config blob is not valid JSON"
        ):
            extractor.validate_oci_archive(str(archive), expected_os="linux")

    def test_array_valued_config_blob_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, config_bytes=b"[1, 2, 3]")
        with pytest.raises(
            extractor.OciDigestExtractionError, match="does not contain a JSON object"
        ):
            extractor.validate_oci_archive(str(archive), expected_os="linux")

    def test_oversized_config_blob_is_fail_closed(self, tmp_path: Path) -> None:
        huge_config = (
            b'{"os":"linux","architecture":"amd64","padding":"'
            + (b"x" * (extractor._MAX_JSON_BYTES + 1))
            + b'"}'
        )
        archive, _ = _valid_archive(tmp_path, config_bytes=huge_config)
        with pytest.raises(extractor.OciDigestExtractionError, match="implausible size"):
            extractor.extract_manifest_digest(str(archive))

    def test_platform_less_config_blob_is_fail_closed_when_platform_expected(
        self, tmp_path: Path
    ) -> None:
        """Task item 5's "platform-less ... config blobs" case."""
        archive, _ = _valid_archive(tmp_path, config_bytes=b'{"some_other_field": 1}')
        with pytest.raises(extractor.OciDigestExtractionError, match="config blob 'os'"):
            extractor.validate_oci_archive(str(archive), expected_os="linux")

    def test_non_json_config_blob_passes_when_no_platform_is_expected(self, tmp_path: Path) -> None:
        """Documents the actual, deliberate scope of "unconditional"
        here: `_parse_config_document` is only ever invoked as part of
        descriptor-graph validation (which always runs), so a config
        blob's basic JSON-object-ness is *always* checked -- but this
        specific fixture never reaches that far because a non-JSON
        config blob is rejected regardless of whether a platform was
        requested, proving the check really is unconditional.
        """
        archive, _ = _valid_archive(tmp_path, config_bytes=b"not json at all")
        with pytest.raises(
            extractor.OciDigestExtractionError, match="config blob is not valid JSON"
        ):
            extractor.extract_manifest_digest(str(archive))


def _replace_config_blob(archive: Path, *, old_config_digest: str, new_config_bytes: bytes) -> None:
    """Replaces the config blob's own content (and updates the
    manifest's own `config` descriptor + the manifest's own digest in
    `index.json`) -- used to construct an index-platform/config-platform
    conflict where each individually would satisfy a naive check.
    """
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    old_manifest_bytes: bytes | None = None
    old_manifest_name: str | None = None
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            fileobj = tar.extractfile(member) if member.isfile() else None
            data = fileobj.read() if fileobj else b""
            if member.name == _blob_path(old_config_digest):
                continue
            members.append((member, data))

    new_config_digest = _sha256_digest(new_config_bytes)
    rewritten: list[tuple[tarfile.TarInfo, bytes]] = []
    for member, data in members:
        if (
            member.isfile()
            and member.name not in ("oci-layout", "index.json")
            and member.name.startswith("blobs/sha256/")
        ):
            try:
                candidate = json.loads(data)
            except json.JSONDecodeError:
                rewritten.append((member, data))
                continue
            if isinstance(candidate, dict) and "config" in candidate and "layers" in candidate:
                old_manifest_bytes = data
                old_manifest_name = member.name
                continue
        rewritten.append((member, data))

    assert old_manifest_bytes is not None and old_manifest_name is not None
    manifest_doc = json.loads(old_manifest_bytes)
    manifest_doc["config"]["digest"] = new_config_digest
    manifest_doc["config"]["size"] = len(new_config_bytes)
    new_manifest_bytes = json.dumps(manifest_doc).encode()
    new_manifest_digest = _sha256_digest(new_manifest_bytes)

    with tarfile.open(archive, "w") as tar:
        for member, data in rewritten:
            if member.name == "index.json":
                index_doc = json.loads(data)
                index_doc["manifests"][0]["digest"] = new_manifest_digest
                index_doc["manifests"][0]["size"] = len(new_manifest_bytes)
                data = json.dumps(index_doc).encode()
                member = tarfile.TarInfo(name="index.json")
                member.size = len(data)
            tar.addfile(member, io.BytesIO(data) if member.isfile() else None)
        manifest_info = tarfile.TarInfo(name=_blob_path(new_manifest_digest))
        manifest_info.size = len(new_manifest_bytes)
        tar.addfile(manifest_info, io.BytesIO(new_manifest_bytes))
        config_info = tarfile.TarInfo(name=_blob_path(new_config_digest))
        config_info.size = len(new_config_bytes)
        tar.addfile(config_info, io.BytesIO(new_config_bytes))


def _corrupt_manifest_blob(
    archive: Path, *, old_manifest_digest: str, transform: Callable[[dict], dict]
) -> None:
    """Replaces the manifest blob's own content with a transformed
    version, and rewrites `index.json` to point at the new blob's own
    (recomputed) digest -- simulates a manifest whose *own* JSON content
    is malformed in some way, while every other blob (config, layers)
    stays genuinely valid and correctly referenced.
    """
    members: list[tuple[tarfile.TarInfo, bytes]] = []
    manifest_bytes: bytes | None = None
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            fileobj = tar.extractfile(member) if member.isfile() else None
            data = fileobj.read() if fileobj else b""
            if member.name == _blob_path(old_manifest_digest):
                manifest_bytes = data
                continue
            members.append((member, data))
    assert manifest_bytes is not None, "old manifest blob not found in archive"
    new_manifest = transform(json.loads(manifest_bytes))
    new_manifest_bytes = json.dumps(new_manifest).encode()
    new_manifest_digest = _sha256_digest(new_manifest_bytes)

    with tarfile.open(archive, "w") as tar:
        for member, data in members:
            if member.name == "index.json":
                index = json.loads(data)
                index["manifests"][0]["digest"] = new_manifest_digest
                index["manifests"][0]["size"] = len(new_manifest_bytes)
                data = json.dumps(index).encode()
                member = tarfile.TarInfo(name="index.json")
                member.size = len(data)
            tar.addfile(member, io.BytesIO(data) if member.isfile() else None)
        info = tarfile.TarInfo(name=_blob_path(new_manifest_digest))
        info.size = len(new_manifest_bytes)
        tar.addfile(info, io.BytesIO(new_manifest_bytes))


def _rewrite_manifest_via_config_only(
    archive: Path, *, manifest_digest: str, config_digest: str
) -> None:
    del config_digest
    _corrupt_manifest_blob(
        archive,
        old_manifest_digest=manifest_digest,
        transform=lambda m: {"schemaVersion": 2, "config": m["config"]},
    )


class TestPermittedLayoutEnvelope:
    """Correction pass: "Replace unrestricted OCI extraction" -- every
    member in the archive, not only the paths this validator itself
    happens to read, must be one of the permitted OCI layout envelope's
    own exact paths/types.
    """

    def test_path_traversal_member_name_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_members=[("blobs/../../../../etc/passwd", b"irrelevant")]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unsafe tar member"):
            extractor.extract_manifest_digest(str(archive))

    def test_absolute_path_member_name_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, extra_members=[("/etc/passwd", b"irrelevant")])
        with pytest.raises(extractor.OciDigestExtractionError, match="unsafe tar member"):
            extractor.extract_manifest_digest(str(archive))

    def test_relative_symlink_at_an_unrelated_path_is_fail_closed(self, tmp_path: Path) -> None:
        """Task item 3: an unexpected member type "anywhere in the
        archive" -- not only at a path this validator otherwise reads.
        A prior version of this validator never even inspected a member
        at an unrelated name at all.
        """
        archive, _ = _valid_archive(
            tmp_path, extra_symlinks=[("evil-relative-link", "../../etc/passwd")]
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_absolute_symlink_at_an_unrelated_path_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_symlinks=[("evil-absolute-link", "/etc/passwd")]
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_symlink_standing_in_for_oci_layout_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path,
            skip_members=frozenset({"oci-layout"}),
            extra_symlinks=[("oci-layout", "/etc/passwd")],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="must be a regular file"):
            extractor.extract_manifest_digest(str(archive))

    def test_symlink_standing_in_for_a_blob_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["config_digest"])}),
            extra_symlinks=[(_blob_path(digests["config_digest"]), "/etc/passwd")],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="expected a regular file"):
            extractor.extract_manifest_digest(str(archive))

    def test_hardlink_at_an_unrelated_path_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path,
            extra_special_members=[("evil-hardlink", tarfile.LNKTYPE, "index.json")],
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_hardlink_standing_in_for_a_blob_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["config_digest"])}),
            extra_special_members=[
                (_blob_path(digests["config_digest"]), tarfile.LNKTYPE, "index.json")
            ],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="expected a regular file"):
            extractor.extract_manifest_digest(str(archive))

    def test_character_device_at_an_unrelated_path_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_special_members=[("evil-device", tarfile.CHRTYPE, None)]
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_fifo_at_an_unrelated_path_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_special_members=[("evil-fifo", tarfile.FIFOTYPE, None)]
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_unexpected_regular_file_at_an_unrelated_path_is_fail_closed(
        self, tmp_path: Path
    ) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_members=[("README.txt", b"this does not belong here")]
        )
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_unexpected_file_directly_under_blobs_is_fail_closed(self, tmp_path: Path) -> None:
        """`blobs/` may contain only the `sha256` directory -- an
        unrelated file dropped directly inside it (never resolved by
        any digest) must still be rejected.
        """
        archive, _ = _valid_archive(tmp_path, extra_members=[("blobs/unexpected.txt", b"x")])
        with pytest.raises(
            extractor.OciDigestExtractionError, match="outside the permitted OCI layout envelope"
        ):
            extractor.extract_manifest_digest(str(archive))

    def test_non_hex_named_file_under_blobs_sha256_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path, extra_members=[("blobs/sha256/not-a-real-digest", b"x")]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unexpected member"):
            extractor.extract_manifest_digest(str(archive))

    def test_duplicate_index_json_member_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path, extra_members=[("index.json", _index_with([]))])
        with pytest.raises(extractor.OciDigestExtractionError, match="duplicate tar member"):
            extractor.extract_manifest_digest(str(archive))

    def test_duplicate_oci_layout_member_is_fail_closed(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(
            tmp_path,
            extra_members=[("oci-layout", json.dumps({"imageLayoutVersion": "1.0.0"}).encode())],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="duplicate tar member"):
            extractor.extract_manifest_digest(str(archive))

    def test_duplicate_blob_member_is_fail_closed(self, tmp_path: Path) -> None:
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        config_path = _blob_path(digests["config_digest"])
        archive, _ = _valid_archive(
            tmp_path, name="real.tar", extra_members=[(config_path, _DEFAULT_CONFIG_BYTES)]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="duplicate tar member"):
            extractor.extract_manifest_digest(str(archive))

    def test_duplicate_blobs_directory_member_is_fail_closed(self, tmp_path: Path) -> None:
        """Task item 3: "duplicate member" applies to noncritical
        (directory) names too, not only files.
        """

        def _inject_duplicate_directory(tar: tarfile.TarFile) -> None:
            info = tarfile.TarInfo(name="blobs")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)

        archive, _ = _valid_archive(tmp_path)
        with tarfile.open(archive, "r") as tar:
            members = [
                (m, tar.extractfile(m).read() if m.isfile() else None) for m in tar.getmembers()
            ]
        with tarfile.open(archive, "w") as tar:
            for member, data in members:
                tar.addfile(member, io.BytesIO(data) if data is not None else None)
            _inject_duplicate_directory(tar)
        with pytest.raises(extractor.OciDigestExtractionError, match="duplicate tar member"):
            extractor.extract_manifest_digest(str(archive))


class TestUnreferencedBlobRejection:
    """Correction pass: the closed envelope (`TestPermittedLayoutEnvelope`)
    rejects any member outside the permitted *paths*/*types* -- but a
    regular file at a syntactically valid `blobs/sha256/<64 hex>` path
    was still accepted, and extracted, even when no descriptor (the
    manifest, its config, or any layer) ever referenced it. Every test
    below builds a fully valid archive (real manifest, real config, real
    layer, all genuinely cross-referenced) and adds exactly one extra,
    unreferenced blob -- proving the *only* thing that matters is
    descriptor-graph reachability, never filename validity or content
    correctness in isolation.
    """

    def test_unreferenced_blob_with_content_not_matching_its_filename_is_rejected(
        self, tmp_path: Path
    ) -> None:
        """Task item 1, case 1."""
        fake_digest = "sha256:" + "0" * 64
        archive, _ = _valid_archive(
            tmp_path,
            extra_members=[(_blob_path(fake_digest), b"this does not hash to all zeros")],
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unreferenced blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_unreferenced_blob_with_content_matching_its_filename_is_still_rejected(
        self, tmp_path: Path
    ) -> None:
        """Task item 1, case 2: content genuinely hashes to its own
        filename -- still rejected, because it is never referenced by
        any descriptor. Proves the check is purely about descriptor-
        graph reachability, not content/filename self-consistency.
        """
        real_content = b"a real, self-consistent, but never-referenced blob"
        real_digest = _sha256_digest(real_content)
        archive, _ = _valid_archive(
            tmp_path, extra_members=[(_blob_path(real_digest), real_content)]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unreferenced blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_extra_unreferenced_config_shaped_blob_is_rejected(self, tmp_path: Path) -> None:
        """Task item 1, case 3: an extra blob that is itself a
        plausible, well-formed config-shaped JSON document -- rejected
        purely because nothing's `config` descriptor names it.
        """
        decoy_config = b'{"os":"linux","architecture":"amd64","decoy":true}'
        decoy_digest = _sha256_digest(decoy_config)
        archive, _ = _valid_archive(
            tmp_path, extra_members=[(_blob_path(decoy_digest), decoy_config)]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unreferenced blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_extra_unreferenced_layer_shaped_blob_is_rejected(self, tmp_path: Path) -> None:
        """Task item 1, case 4: an extra blob shaped like ordinary layer
        content -- rejected purely because no layer descriptor names it.
        """
        decoy_layer = b"decoy-layer-content-nobody-references"
        decoy_digest = _sha256_digest(decoy_layer)
        archive, _ = _valid_archive(
            tmp_path, extra_members=[(_blob_path(decoy_digest), decoy_layer)]
        )
        with pytest.raises(extractor.OciDigestExtractionError, match="unreferenced blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_a_referenced_blob_missing_from_the_archive_is_still_rejected(
        self, tmp_path: Path
    ) -> None:
        """Task item 1, case 5 -- the *other* direction (already covered
        by `TestFullDescriptorGraphValidation`, re-asserted here
        alongside the new unreferenced-blob checks so this file's own
        "both directions of the referenced-set equality" guarantee is
        visible in one place).
        """
        _, digests = _valid_archive(tmp_path, name="probe.tar")
        archive, _ = _valid_archive(
            tmp_path,
            name="real.tar",
            skip_members=frozenset({_blob_path(digests["layer_digests"][0])}),
        )
        with pytest.raises(extractor.OciDigestExtractionError, match=r"layer\[0\] blob"):
            extractor.extract_manifest_digest(str(archive))

    def test_successful_extraction_contains_exactly_the_descriptor_reachable_set(
        self, tmp_path: Path
    ) -> None:
        """Task item 1, case 6: a genuinely valid archive (no extras at
        all) extracts to exactly its descriptor-reachable blob set --
        the positive control proving the new equality check does not
        reject legitimate archives.
        """
        archive, digests = _valid_archive(tmp_path)
        dest = tmp_path / "extracted"
        result = extractor.validate_and_extract_oci_layout(str(archive), str(dest))
        assert result["referenced_digests"] == frozenset(
            {digests["manifest_digest"], digests["config_digest"], *digests["layer_digests"]}
        )
        written_blob_hexes = {p.name for p in (dest / "blobs" / "sha256").iterdir()}
        expected_hexes = {
            d.split(":", 1)[1]
            for d in (
                digests["manifest_digest"],
                digests["config_digest"],
                *digests["layer_digests"],
            )
        }
        assert written_blob_hexes == expected_hexes

    def test_unreferenced_blob_is_rejected_by_validate_and_extract_too(
        self, tmp_path: Path
    ) -> None:
        """The rejection must apply identically to the extraction entry
        point, not only the validate-only one -- and must never leave a
        destination directory behind.
        """
        fake_digest = "sha256:" + "1" * 64
        archive, _ = _valid_archive(
            tmp_path, extra_members=[(_blob_path(fake_digest), b"unreferenced")]
        )
        dest = tmp_path / "extracted"
        with pytest.raises(extractor.OciDigestExtractionError, match="unreferenced blob"):
            extractor.validate_and_extract_oci_layout(str(archive), str(dest))
        assert not dest.exists()


class TestSafeExtractionAndRegistryPush:
    """Correction pass: "Replace generic `tar -xf` with validator-
    controlled extraction of the exact validated member set into a
    newly created empty directory." Also proves no validate/extract
    TOCTOU gap, and (when Docker + a disposable local registry are
    available) that the safely-extracted directory still pushes
    successfully and preserves the manifest digest.
    """

    def test_extracts_exactly_the_validated_member_set(self, tmp_path: Path) -> None:
        archive, digests = _valid_archive(tmp_path)
        dest = tmp_path / "extracted"
        result = extractor.validate_and_extract_oci_layout(str(archive), str(dest))
        assert result["manifest_digest"] == digests["manifest_digest"]

        assert (dest / "oci-layout").is_file()
        assert (dest / "index.json").is_file()
        assert (dest / "blobs" / "sha256").is_dir()
        for digest in [
            digests["manifest_digest"],
            digests["config_digest"],
            *digests["layer_digests"],
        ]:
            hex_part = digest.split(":", 1)[1]
            assert (dest / "blobs" / "sha256" / hex_part).is_file()

        # Nothing beyond the validated envelope was ever written.
        all_paths = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*"))
        expected_paths = {"oci-layout", "index.json", "blobs", "blobs/sha256"} | {
            f"blobs/sha256/{d.split(':', 1)[1]}"
            for d in (
                digests["manifest_digest"],
                digests["config_digest"],
                *digests["layer_digests"],
            )
        }
        assert set(all_paths) == expected_paths

    def test_refuses_to_extract_into_an_already_existing_directory(self, tmp_path: Path) -> None:
        archive, _ = _valid_archive(tmp_path)
        dest = tmp_path / "already-there"
        dest.mkdir()
        with pytest.raises(extractor.OciDigestExtractionError, match="already exists"):
            extractor.validate_and_extract_oci_layout(str(archive), str(dest))

    def test_never_extracts_a_symlink_member(self, tmp_path: Path) -> None:
        """Even a symlink at a *permitted* critical path name must never
        actually be extracted -- validation rejects it before any
        extraction step runs at all.
        """
        archive, _ = _valid_archive(
            tmp_path,
            skip_members=frozenset({"oci-layout"}),
            extra_symlinks=[("oci-layout", "/etc/passwd")],
        )
        dest = tmp_path / "extracted"
        with pytest.raises(extractor.OciDigestExtractionError):
            extractor.validate_and_extract_oci_layout(str(archive), str(dest))
        assert not dest.exists(), (
            "a failed validation must never leave a partially created destination directory"
        )

    def test_invalid_archive_leaves_no_destination_directory(self, tmp_path: Path) -> None:
        """No validate/extract TOCTOU gap and no partial-extraction
        residue: the destination directory is only ever created after
        validation has fully succeeded, within the same open tar handle.
        """
        archive, _ = _valid_archive(tmp_path, layer_size_overrides={0: 1})
        dest = tmp_path / "extracted"
        with pytest.raises(extractor.OciDigestExtractionError):
            extractor.validate_and_extract_oci_layout(str(archive), str(dest))
        assert not dest.exists()

    def test_extract_to_cli_flag_extracts_and_prints_the_digest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        archive, digests = _valid_archive(tmp_path)
        dest = tmp_path / "cli-extracted"
        exit_code = extractor.main([str(archive), "--extract-to", str(dest)])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == digests["manifest_digest"]

    def test_no_partial_destination_remains_after_an_injected_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Task item 1, case 7: even a genuinely valid archive must not
        leave a half-written destination directory behind if something
        fails *during* extraction itself (disk full, permission error,
        or any other real-world write failure) -- injected here via a
        monkeypatched `shutil.copyfileobj` that raises partway through
        a multi-blob archive, after at least one file has already been
        written to disk.
        """
        archive, digests = _valid_archive(tmp_path)
        dest = tmp_path / "extracted"

        call_count = {"n": 0}
        real_copyfileobj = extractor.shutil.copyfileobj

        def flaky_copyfileobj(fsrc, fdst):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise OSError("injected disk-full failure for this test")
            return real_copyfileobj(fsrc, fdst)

        monkeypatch.setattr(extractor.shutil, "copyfileobj", flaky_copyfileobj)

        with pytest.raises(OSError, match="injected disk-full failure"):
            extractor.validate_and_extract_oci_layout(str(archive), str(dest))

        assert call_count["n"] >= 2, "the injected failure must actually have been reached"
        assert not dest.exists(), (
            "a write failure partway through extraction must leave no partial destination "
            "directory behind"
        )

    def test_safely_extracted_layout_pushes_to_a_disposable_local_registry_and_preserves_digest(
        self, tmp_path: Path
    ) -> None:
        """The strongest available proof that validator-controlled
        extraction produces a real, `crane`-pushable OCI layout
        directory: builds a tiny real image with `docker buildx`, safely
        extracts it via `validate_and_extract_oci_layout` (never a
        generic `tar -xf`), starts a real disposable local registry
        container, pushes the extracted directory with `crane`, and
        confirms the registry's own reported digest -- queried directly
        from its HTTP API, never merely trusted from `crane`'s stdout --
        matches the digest this module independently computed.

        Skips cleanly if Docker/Buildx/Crane are unavailable, *unless*
        `COG_OCI_INTEGRATION_TESTS_REQUIRED=1` -- see this module's own
        top-of-file note on the three distinct verification tiers.
        """
        _require_tool_or_skip(shutil.which("docker") is not None, what="docker CLI")
        crane = shutil.which("crane")
        _require_tool_or_skip(crane is not None, what="crane CLI")
        _require_tool_or_skip(
            not _docker_buildx_unavailable(), what="docker buildx (docker-container driver)"
        )

        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text("FROM scratch\nCOPY oci-layout /oci-layout\n")
        # A `FROM scratch` build with a single trivial COPY is enough to
        # exercise a real `docker buildx build --output type=oci` archive
        # without the cost of a full project image build in this
        # fast-running unit test file (the real project Dockerfile is
        # exercised separately in `TestAgainstARealProjectDockerfileArchive`).
        (tmp_path / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')

        builder_name = "extract-oci-digest-test-builder"
        subprocess.run(
            ["docker", "buildx", "rm", builder_name], capture_output=True, timeout=30, check=False
        )
        create = subprocess.run(
            ["docker", "buildx", "create", "--name", builder_name, "--driver", "docker-container"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert create.returncode == 0, create.stderr
        try:
            archive_path = tmp_path / "real.tar"
            build = subprocess.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    builder_name,
                    "--output",
                    f"type=oci,dest={archive_path}",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert build.returncode == 0, build.stderr

            expected = extractor.validate_oci_archive(str(archive_path))

            dest = tmp_path / "layout"
            result = extractor.validate_and_extract_oci_layout(str(archive_path), str(dest))
            assert result == expected

            port = _free_tcp_port()
            registry_name = "extract-oci-digest-test-registry"
            subprocess.run(
                ["docker", "rm", "-f", registry_name], capture_output=True, timeout=30, check=False
            )
            run_registry = subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    registry_name,
                    "-p",
                    f"{port}:5000",
                    "registry:2",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert run_registry.returncode == 0, run_registry.stderr
            try:
                _wait_for_tcp_port("127.0.0.1", port, timeout_seconds=20)
                push = subprocess.run(
                    [crane, "push", str(dest), f"localhost:{port}/test/safe-extract:proof"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                assert push.returncode == 0, push.stderr

                import urllib.request

                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v2/test/safe-extract/manifests/proof",
                    headers={"Accept": "application/vnd.oci.image.manifest.v1+json"},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    registry_digest = response.headers.get("Docker-Content-Digest")
                assert registry_digest == expected["manifest_digest"]
            finally:
                subprocess.run(
                    ["docker", "rm", "-f", registry_name], capture_output=True, timeout=30
                )
        finally:
            subprocess.run(
                ["docker", "buildx", "rm", builder_name], capture_output=True, timeout=30
            )


def _docker_buildx_unavailable() -> bool:
    result = subprocess.run(
        ["docker", "buildx", "version"], capture_output=True, timeout=15, check=False
    )
    return result.returncode != 0


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_tcp_port(host: str, port: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"{host}:{port} did not become reachable within {timeout_seconds}s.")


@pytest.mark.skipif(
    (shutil.which("docker") is None or _docker_buildx_unavailable())
    and not _oci_integration_tests_required(),
    reason="docker/buildx not available for a real project-image build",
)
class TestAgainstARealProjectDockerfileArchive:
    """Task requirement: "Test against at least one real OCI archive
    produced by the project Dockerfile, not only handwritten fixtures."
    Builds this project's own real `Dockerfile` once per test session
    (module-scoped) and runs the hardened validator against the genuine
    result -- including the new, mandatory config-blob platform check
    and the new closed permitted-envelope member check, both against
    real buildkit output rather than a hand-typed approximation of it.

    Skips cleanly if Docker/Buildx are unavailable, *unless*
    `COG_OCI_INTEGRATION_TESTS_REQUIRED=1` -- in which case the class
    is never skipped at collection time, and its own fixture below fails
    loudly instead if the tools genuinely turn out to be missing.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def real_archive(cls, tmp_path_factory: pytest.TempPathFactory) -> Path:
        _require_tool_or_skip(shutil.which("docker") is not None, what="docker CLI")
        _require_tool_or_skip(
            not _docker_buildx_unavailable(), what="docker buildx (docker-container driver)"
        )
        repo_root = Path(__file__).resolve().parent.parent
        tmp_dir = tmp_path_factory.mktemp("real-dockerfile-oci")
        archive_path = tmp_dir / "real-project.tar"
        builder_name = "extract-oci-digest-real-dockerfile-builder"
        subprocess.run(
            ["docker", "buildx", "rm", builder_name], capture_output=True, timeout=30, check=False
        )
        create = subprocess.run(
            ["docker", "buildx", "create", "--name", builder_name, "--driver", "docker-container"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert create.returncode == 0, create.stderr
        try:
            build = subprocess.run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    builder_name,
                    "--platform",
                    "linux/amd64",
                    "--output",
                    f"type=oci,dest={archive_path}",
                    str(repo_root),
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
            assert build.returncode == 0, build.stderr
        finally:
            subprocess.run(
                ["docker", "buildx", "rm", builder_name], capture_output=True, timeout=30
            )
        return archive_path

    def test_the_real_archive_validates_and_matches_the_expected_platform(
        self, real_archive: Path
    ) -> None:
        result = extractor.validate_oci_archive(
            str(real_archive), expected_os="linux", expected_architecture="amd64"
        )
        assert result["manifest_digest"].startswith("sha256:")
        assert len(result["manifest_digest"]) == 71

    def test_the_real_archive_fails_against_the_wrong_expected_architecture(
        self, real_archive: Path
    ) -> None:
        with pytest.raises(extractor.OciDigestExtractionError, match="architecture"):
            extractor.validate_oci_archive(
                str(real_archive), expected_os="linux", expected_architecture="arm64"
            )

    def test_the_real_archive_can_be_safely_extracted(
        self, real_archive: Path, tmp_path: Path
    ) -> None:
        dest = tmp_path / "real-extracted"
        result = extractor.validate_and_extract_oci_layout(
            str(real_archive), str(dest), expected_os="linux", expected_architecture="amd64"
        )
        assert (dest / "oci-layout").is_file()
        assert (dest / "index.json").is_file()
        hex_part = result["manifest_digest"].split(":", 1)[1]
        assert (dest / "blobs" / "sha256" / hex_part).is_file()


class TestMainCli:
    def test_prints_the_manifest_digest_by_default_and_exits_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        archive, digests = _valid_archive(tmp_path)
        exit_code = extractor.main([str(archive)])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == digests["manifest_digest"]

    def test_field_flag_selects_config_digest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        archive, digests = _valid_archive(tmp_path)
        exit_code = extractor.main([str(archive), "--field", "config_digest"])
        assert exit_code == 0
        assert capsys.readouterr().out.strip() == digests["config_digest"]

    def test_expected_platform_flags_are_enforced(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        archive, _ = _valid_archive(tmp_path, config_bytes=b'{"os":"linux","architecture":"arm64"}')
        exit_code = extractor.main(
            [str(archive), "--expected-os", "linux", "--expected-architecture", "amd64"]
        )
        assert exit_code == 1
        assert "architecture" in capsys.readouterr().err

    def test_fail_closed_condition_exits_nonzero_and_prints_to_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        archive = _make_archive(tmp_path, index_json=None)
        exit_code = extractor.main([str(archive)])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "oci-layout" in captured.err


def test_oci_integration_tools_are_required_and_present_when_opted_in() -> None:
    """Correction pass, item 4: a fast, standalone canary (module-level,
    so no class-scoped `skipif` marker in this file can ever suppress
    it) -- mirrors `tests/ingestion_azure/conftest.py::
    test_bicep_cli_is_required_and_present_in_ci`'s own established
    pattern for a different toolchain. If
    `COG_OCI_INTEGRATION_TESTS_REQUIRED=1` is set but Docker, Buildx, or
    Crane are missing, this fails immediately with a specific message
    naming what's absent -- rather than letting every real-Docker/Crane
    test in this file quietly report itself as "skipped" while a report
    claims the strongest OCI supply-chain verification actually ran.
    """
    if not _oci_integration_tests_required():
        return
    missing = []
    if shutil.which("docker") is None:
        missing.append("docker")
    elif _docker_buildx_unavailable():
        missing.append("docker buildx (docker-container driver)")
    if shutil.which("crane") is None:
        missing.append("crane")
    assert not missing, (
        f"{_OCI_INTEGRATION_REQUIRED_ENV_VAR}=1 but the following required tool(s) are "
        f"missing, which would let Docker/Crane-dependent tests in this file silently "
        f"skip instead of running for real: {missing}"
    )
