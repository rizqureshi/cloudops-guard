#!/usr/bin/env python3
"""Validates a real OCI Image Layout archive's *complete* descriptor
graph, extracts its manifest digest, and (via
`validate_and_extract_oci_layout`) safely extracts exactly its validated
member set into a fresh directory -- **without ever contacting a
registry** -- this is what lets `build` compute the exact digest the
image will carry once published, before any push happens at all, and
lets `deploy` obtain a real OCI layout directory for `crane push`
without ever running an unrestricted `tar -xf` against untrusted content.

**The gap the original version had**: it trusted the digest *string*
recorded in `index.json` without checking that the manifest blob it
names actually exists, that its content genuinely hashes to that
digest, or that anything in the referenced manifest (its config, its
layers) is itself structurally valid. Reproduced directly: an archive
with a plausible-looking `index.json` but no corresponding
`blobs/sha256/<digest>` file passed the original implementation without
error.

**This correction pass hardens two further gaps, both independently
reproduced before being fixed**:

1. **Platform validation was index-only and merely optional.** The prior
   version only checked the *index* manifest entry's own `platform`
   field, and only if that field happened to be present -- an archive
   whose index omitted `platform` entirely passed unconditionally, even
   though its actual config blob (the authoritative source of an
   image's real platform) could declare a completely different
   architecture. Reproduced: a hand-built archive with no index
   `platform` field and a config blob declaring `"architecture":
   "arm64"` was accepted even when `expected_architecture="amd64"` was
   requested. Fixed: when `expected_os`/`expected_architecture` are
   supplied, the config blob is now parsed as bounded JSON and its own
   `os`/`architecture` fields are the mandatory source of truth; an
   index-level `platform`, when present, must additionally agree with
   both the expected values and the config blob -- never merely with
   one or the other.
2. **The tar-member safety check only covered "tracked" paths, and
   layer/config/index media-type checks used prefix matching.**
   Anything with a name outside the small set of paths this validator
   itself reads (`oci-layout`, `index.json`, `blobs/sha256/<digest>`)
   was never inspected at all -- a symlink, hardlink, device, or FIFO at
   an arbitrary unrelated path passed through completely unexamined,
   and the workflow then ran a generic, unrestricted `tar -xf` against
   the same archive, extracting whatever that member turned out to be.
   Reproduced: a hand-built archive containing a symlink member named
   `evil` pointing at `/etc/passwd` passed the original validator
   without error. Separately, layer media types were accepted via
   `str.startswith(prefix)`, so `application/vnd.oci.image.layer.v1.tarX`
   (an invented, non-existent media type sharing only a textual prefix
   with a real one) was wrongly accepted. Fixed: every single member in
   the archive is now classified against an explicit, closed "permitted
   OCI layout envelope" (exactly `oci-layout`/`index.json` as regular
   files, `blobs`/`blobs/sha256` as directories, and `blobs/sha256/
   <64 lowercase hex characters>` as regular files) -- anything else,
   anywhere in the archive, of any type, at any path, is rejected
   outright; and every media type (index, manifest, config, and each
   layer) is now checked against an explicit, closed set of exact
   strings, never a prefix.
3. **The closed envelope still accepted, and extracted, any
   *unreferenced* blob.** Fixing item 2 above closed "any path/type
   outside the envelope," but a regular file at a syntactically valid
   `blobs/sha256/<64 lowercase hex characters>` path was still accepted
   -- and extracted -- even when no descriptor (the manifest itself, its
   config, or any layer) ever referenced it. Reproduced directly: an
   archive with a fully valid manifest/config/layer set, plus one extra
   file at `blobs/sha256/<64 zeros>` whose content did not even hash to
   that all-zeros name, was accepted by `validate_oci_archive` and then
   written to disk unverified by `validate_and_extract_oci_layout`.
   Fixed by deriving the *exact* expected blob set from the descriptor
   graph itself (the manifest digest, its config digest, and every layer
   digest) and requiring the archive's actual blob-member set to equal
   that exact set -- any extra blob is rejected outright, regardless of
   whether its filename is syntactically valid or its content genuinely
   matches its own filename. Extraction was independently hardened too,
   as defense in depth: it now writes only the explicit,
   descriptor-graph-derived referenced set, never "every member that
   happened to pass envelope classification."

**What this version verifies, fail-closed, before trusting any digest**:

* Every tar member is one of the permitted OCI layout envelope's own
  exact paths/types (see above) -- no symlink, hardlink, device, FIFO,
  absolute path, traversal path, duplicate member, or unexpected member
  type anywhere in the archive, not merely at the paths this validator
  itself happens to read.
* `oci-layout` declares an exact, explicitly supported
  `imageLayoutVersion` (`"1.0.0"` -- never merely a `"1."` prefix match).
* `index.json` declares the exact expected `schemaVersion`/`mediaType`
  and names exactly one manifest descriptor of an accepted, exact
  media type.
* The descriptor's `digest`/`size` fields are well-formed, and the
  manifest blob they name genuinely exists, has exactly the declared
  size (bounded, and re-checked against a hard metadata-size ceiling),
  and hashes to exactly the declared digest -- the actual bytes are
  always re-hashed, never merely path-matched.
* The manifest blob itself parses as bounded JSON, declares
  `schemaVersion: 2` and an accepted, exact `mediaType`, and has a
  well-formed `config` descriptor and a non-empty `layers` array of
  well-formed descriptors, each with an accepted, exact media type.
* Every one of those referenced blobs (config and every layer) is
  independently resolved, size-checked, and re-hashed the same way.
* When `expected_os`/`expected_architecture` are supplied, the config
  blob (parsed as bounded JSON) must declare matching `os`/
  `architecture` fields -- mandatory, not merely checked if present --
  and, if the index descriptor also carries a `platform` field, it must
  be a valid object agreeing with both the expected values and the
  config blob's own fields.

**Independently, empirically verified** (not assumed) that the
resulting digest is genuinely preserved through to a real registry, and
that the safely-extracted layout directory this module produces
(`validate_and_extract_oci_layout`) still pushes correctly: built a real
image from this project's own `Dockerfile`, exported it as an OCI
archive, extracted its manifest digest via this exact method, extracted
its layout via this exact safe-extraction method, then pushed that
extracted directory to a real local Docker registry (`registry:2`) using
`crane push` -- the resulting registry digest, queried directly from the
registry's own HTTP API (never merely trusting `crane`'s own stdout),
matched exactly. Separately confirmed that `docker push` (which re-wraps
the manifest into Docker's own legacy Distribution schema rather than
preserving the OCI manifest bytes) does **not** preserve this digest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile

_ACCEPTED_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
    }
)
_ACCEPTED_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)
_ACCEPTED_CONFIG_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.config.v1+json",
        "application/vnd.docker.container.image.v1+json",
    }
)
_ACCEPTED_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.v1.tar+zstd",
        "application/vnd.oci.image.layer.nondistributable.v1.tar",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+zstd",
        "application/vnd.docker.image.rootfs.diff.tar.gzip",
        "application/vnd.docker.image.rootfs.diff.tar.zstd",
    }
)
_SUPPORTED_LAYOUT_VERSIONS = frozenset({"1.0.0"})
_ACCEPTED_INDEX_SCHEMA_VERSION = 2
_ACCEPTED_MANIFEST_SCHEMA_VERSION = 2
_MAX_JSON_BYTES = 10 * 1024 * 1024  # a manifest/config/index is metadata, never megabytes of it

#: The complete, closed set of paths/types a real OCI Image Layout
#: archive may contain, as far as this validator is concerned -- every
#: tar member is classified against exactly this envelope; anything
#: else (any path, any type) is rejected. `blobs/sha256/<hex>` is
#: matched structurally (via `_BLOB_HEX_PATTERN`), not listed literally.
_CRITICAL_FILES = frozenset({"oci-layout", "index.json"})
_BLOB_DIRECTORIES = frozenset({"blobs", "blobs/sha256"})
_BLOB_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OciDigestExtractionError(Exception):
    """Raised for any fail-closed condition below."""


def _is_well_formed_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        return False
    hex_part = value[len("sha256:") :]
    return all(c in "0123456789abcdef" for c in hex_part)


def _require_well_formed_descriptor(descriptor: object, *, what: str) -> tuple[str, int]:
    if not isinstance(descriptor, dict):
        raise OciDigestExtractionError(f"{what} descriptor is not an object.")
    digest = descriptor.get("digest")
    if not _is_well_formed_digest(digest):
        raise OciDigestExtractionError(f"{what} descriptor has a malformed 'digest': {digest!r}")
    size = descriptor.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise OciDigestExtractionError(f"{what} descriptor has a malformed 'size': {size!r}")
    if size > _MAX_JSON_BYTES and what in ("the index's manifest entry", "config"):
        # Layers may legitimately be large; the manifest and config blobs
        # are always small metadata documents, and a claimed multi-megabyte
        # size for either is itself a fail-closed signal.
        raise OciDigestExtractionError(f"{what} descriptor declares an implausible size: {size}")
    return digest, size  # type: ignore[return-value]


def _classify_member(member: tarfile.TarInfo) -> str:
    """Classifies `member` against the complete, closed permitted OCI
    layout envelope, raising for absolutely anything else -- an absolute
    or traversal path, a symlink/hardlink/device/FIFO at *any* name (not
    only the paths this validator otherwise reads), a name outside the
    exact envelope, or a regular file at a directory-only path (or vice
    versa). Returns `"file"` or `"directory"`.
    """
    name = member.name
    if name.startswith("/") or ".." in name.split("/"):
        raise OciDigestExtractionError(f"unsafe tar member path: {name!r}")

    if name in _CRITICAL_FILES:
        if not member.isfile():
            raise OciDigestExtractionError(
                f"{name!r} must be a regular file, got type {member.type!r} "
                "-- a real OCI archive never uses a symlink/hardlink/device/FIFO here."
            )
        return "file"

    if name in _BLOB_DIRECTORIES:
        if not member.isdir():
            raise OciDigestExtractionError(
                f"{name!r} must be a directory, got type {member.type!r}."
            )
        return "directory"

    if name.startswith("blobs/sha256/"):
        hex_part = name[len("blobs/sha256/") :]
        if _BLOB_HEX_PATTERN.fullmatch(hex_part) and member.isfile():
            return "file"
        raise OciDigestExtractionError(
            f"unexpected member under blobs/sha256/: {name!r} (type {member.type!r}) -- "
            "expected a regular file named by exactly 64 lowercase hex characters."
        )

    raise OciDigestExtractionError(
        f"unexpected archive member outside the permitted OCI layout envelope: {name!r} "
        f"(type {member.type!r})"
    )


def _safe_members(tar: tarfile.TarFile) -> tuple[dict[str, tarfile.TarInfo], frozenset[str]]:
    """Classifies and validates every member in `tar` against the closed
    permitted OCI layout envelope (see `_classify_member`), rejecting any
    symlink/hardlink/device/FIFO, unsafe path, unexpected member, or
    duplicate name -- regardless of whether this validator itself ever
    reads that particular member. Returns `(files_by_name, directory_names)`.
    """
    files: dict[str, tarfile.TarInfo] = {}
    directories: set[str] = set()
    for member in tar.getmembers():
        category = _classify_member(member)
        name = member.name
        if category == "file":
            if name in files:
                raise OciDigestExtractionError(f"duplicate tar member: {name!r}")
            files[name] = member
        else:
            if name in directories:
                raise OciDigestExtractionError(f"duplicate tar member: {name!r}")
            directories.add(name)
    return files, frozenset(directories)


def _read_json_member(
    tar: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str, *, what: str
) -> object:
    member = members.get(name)
    if member is None:
        raise OciDigestExtractionError(
            f"archive does not contain {what} ({name!r}) -- is this a real OCI archive "
            "(docker buildx build --output type=oci)?"
        )
    if member.size > _MAX_JSON_BYTES:
        raise OciDigestExtractionError(f"{name!r} is implausibly large ({member.size} bytes).")
    fileobj = tar.extractfile(member)
    if fileobj is None:
        raise OciDigestExtractionError(f"{name!r} is not a regular file.")
    raw = fileobj.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OciDigestExtractionError(f"{name!r} is not valid JSON.") from exc


def _verify_blob(
    tar: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    *,
    digest: str,
    size: int,
    what: str,
) -> bytes:
    """Resolves the blob `digest` names, requires it to exist as a
    regular file, requires its declared size to match, and re-hashes
    its actual content -- never trusting the filename or the caller's
    own declared digest/size in isolation. Returns the blob's bytes
    (small metadata blobs -- manifest/config -- only; never called for
    a layer's own, potentially large, content beyond its hash).
    """
    blob_path = "blobs/" + digest.replace(":", "/", 1)
    member = members.get(blob_path)
    if member is None:
        raise OciDigestExtractionError(f"{what} blob {digest!r} is referenced but does not exist.")
    if member.size != size:
        raise OciDigestExtractionError(
            f"{what} blob {digest!r} has size {member.size}, but its descriptor declared {size}."
        )
    fileobj = tar.extractfile(member)
    if fileobj is None:
        raise OciDigestExtractionError(f"{what} blob {digest!r} is not a regular file.")
    hasher = hashlib.sha256()
    content = bytearray()
    while chunk := fileobj.read(1024 * 1024):
        hasher.update(chunk)
        if len(content) <= _MAX_JSON_BYTES:
            content.extend(chunk)
    actual_digest = f"sha256:{hasher.hexdigest()}"
    if actual_digest != digest:
        raise OciDigestExtractionError(
            f"{what} blob's actual content hashes to {actual_digest}, "
            f"not its declared digest {digest!r} -- refusing a corrupted or tampered blob."
        )
    return bytes(content)


def _validate_descriptor_graph(
    tar: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    *,
    expected_os: str | None,
    expected_architecture: str | None,
) -> dict[str, str | frozenset[str]]:
    """The complete OCI descriptor-graph validation -- layout version,
    index schema, the single manifest descriptor, the manifest's own
    schema/media type, every blob (manifest, config, every layer), and
    platform agreement -- operating on an already-open `tar` and an
    already-validated `members` map (see `_safe_members`). Shared by
    `validate_oci_archive` (validate only) and
    `validate_and_extract_oci_layout` (validate, then extract within the
    same open handle -- no separate re-open, so no validate/extract
    TOCTOU window).
    """
    layout = _read_json_member(tar, members, "oci-layout", what="the OCI layout marker")
    if not isinstance(layout, dict):
        raise OciDigestExtractionError("'oci-layout' does not contain a JSON object.")
    layout_version = layout.get("imageLayoutVersion")
    if layout_version not in _SUPPORTED_LAYOUT_VERSIONS:
        raise OciDigestExtractionError(
            f"unsupported 'imageLayoutVersion' in oci-layout: {layout_version!r} "
            f"(supported: {sorted(_SUPPORTED_LAYOUT_VERSIONS)})"
        )

    index = _read_json_member(tar, members, "index.json", what="the OCI index")
    if not isinstance(index, dict):
        raise OciDigestExtractionError("'index.json' does not contain a JSON object.")
    index_schema_version = index.get("schemaVersion")
    if index_schema_version != _ACCEPTED_INDEX_SCHEMA_VERSION:
        raise OciDigestExtractionError(
            f"'index.json' schemaVersion is {index_schema_version!r}, "
            f"expected {_ACCEPTED_INDEX_SCHEMA_VERSION}."
        )
    index_media_type = index.get("mediaType")
    if index_media_type not in _ACCEPTED_INDEX_MEDIA_TYPES:
        raise OciDigestExtractionError(
            f"'index.json' mediaType is unaccepted: {index_media_type!r}"
        )

    manifests = index.get("manifests")
    if not isinstance(manifests, list):
        raise OciDigestExtractionError("'index.json' has no 'manifests' array.")
    if len(manifests) != 1:
        raise OciDigestExtractionError(
            f"expected exactly one manifest entry in 'index.json', found {len(manifests)} "
            "-- this project's build is single-platform; a multi-platform index is "
            "unexpected and must be investigated, not silently resolved."
        )

    entry = manifests[0]
    if not isinstance(entry, dict):
        raise OciDigestExtractionError("the manifest entry in 'index.json' is not an object.")

    entry_media_type = entry.get("mediaType")
    if entry_media_type not in _ACCEPTED_MANIFEST_MEDIA_TYPES:
        raise OciDigestExtractionError(
            f"the index's manifest entry has an unaccepted mediaType: {entry_media_type!r} "
            "-- a nested image *index* here would indicate an unexpected multi-platform "
            "or manifest-list archive."
        )

    manifest_digest, manifest_size = _require_well_formed_descriptor(
        entry, what="the index's manifest entry"
    )

    manifest_bytes = _verify_blob(
        tar, members, digest=manifest_digest, size=manifest_size, what="manifest"
    )
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise OciDigestExtractionError("the manifest blob is not valid JSON.") from exc
    if not isinstance(manifest, dict):
        raise OciDigestExtractionError("the manifest blob does not contain a JSON object.")
    if manifest.get("schemaVersion") != _ACCEPTED_MANIFEST_SCHEMA_VERSION:
        raise OciDigestExtractionError(
            f"the manifest blob's schemaVersion is {manifest.get('schemaVersion')!r}, "
            f"expected {_ACCEPTED_MANIFEST_SCHEMA_VERSION}."
        )
    manifest_own_media_type = manifest.get("mediaType")
    if (
        manifest_own_media_type is not None
        and manifest_own_media_type not in _ACCEPTED_MANIFEST_MEDIA_TYPES
    ):
        raise OciDigestExtractionError(
            f"the manifest blob's own mediaType is unaccepted: {manifest_own_media_type!r}"
        )

    config = manifest.get("config")
    config_digest, config_size = _require_well_formed_descriptor(config, what="config")
    config_media_type = config.get("mediaType") if isinstance(config, dict) else None
    if config_media_type not in _ACCEPTED_CONFIG_MEDIA_TYPES:
        raise OciDigestExtractionError(
            f"config descriptor has an unaccepted mediaType: {config_media_type!r}"
        )
    config_bytes = _verify_blob(tar, members, digest=config_digest, size=config_size, what="config")
    config_doc = _parse_config_document(config_bytes)

    _validate_platform(
        config_doc,
        index_entry=entry,
        expected_os=expected_os,
        expected_architecture=expected_architecture,
    )

    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        raise OciDigestExtractionError(
            "the manifest blob's 'layers' is missing, not a list, or empty."
        )
    layer_digests: list[str] = []
    for i, layer in enumerate(layers):
        layer_digest, layer_size = _require_well_formed_descriptor(layer, what=f"layer[{i}]")
        layer_media_type = layer.get("mediaType") if isinstance(layer, dict) else None
        if layer_media_type not in _ACCEPTED_LAYER_MEDIA_TYPES:
            raise OciDigestExtractionError(
                f"layer[{i}] has an unaccepted mediaType: {layer_media_type!r}"
            )
        _verify_blob(tar, members, digest=layer_digest, size=layer_size, what=f"layer[{i}]")
        layer_digests.append(layer_digest)

    # Correction pass: the descriptor graph is the *only* legitimate source
    # of which blobs may exist -- every blob member actually present in the
    # archive must be reachable from the manifest descriptor, its config
    # descriptor, or a layer descriptor. `_verify_blob` above already
    # proves every *referenced* blob exists and is genuine; this closes the
    # other direction, rejecting any *extra*, unreferenced blob outright --
    # regardless of whether its filename is syntactically valid or its
    # content genuinely hashes to its own filename. Reproduced directly:
    # an archive with a real, valid manifest/config/layer set plus one
    # extra file at `blobs/sha256/<64 zeros>` (whose content did not even
    # hash to that name) was accepted by the prior version of this
    # validator, which never checked for extras at all.
    referenced_digests = frozenset({manifest_digest, config_digest, *layer_digests})
    for name in members:
        if not name.startswith("blobs/sha256/"):
            continue
        member_digest = "sha256:" + name[len("blobs/sha256/") :]
        if member_digest not in referenced_digests:
            raise OciDigestExtractionError(
                f"unreferenced blob present in archive: {name!r} -- every blob member must be "
                "reachable from the manifest descriptor, its config descriptor, or a layer "
                "descriptor; an extra blob is rejected regardless of whether its content "
                "matches its own filename."
            )

    return {
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "referenced_digests": referenced_digests,
    }


def _parse_config_document(config_bytes: bytes) -> dict:
    """Unconditionally required (regardless of whether a platform is
    ever checked): the config blob must be a bounded, valid JSON
    *object* -- never merely bytes that happen to hash correctly.
    """
    if len(config_bytes) > _MAX_JSON_BYTES:
        raise OciDigestExtractionError("the config blob is implausibly large for a JSON document.")
    try:
        config_doc = json.loads(config_bytes)
    except json.JSONDecodeError as exc:
        raise OciDigestExtractionError("the config blob is not valid JSON.") from exc
    if not isinstance(config_doc, dict):
        raise OciDigestExtractionError("the config blob does not contain a JSON object.")
    return config_doc


def _validate_platform(
    config_doc: dict,
    *,
    index_entry: dict,
    expected_os: str | None,
    expected_architecture: str | None,
) -> None:
    """Correction pass: the config blob (never the index entry alone) is
    the mandatory source of platform truth whenever a platform is
    expected at all. Fails closed on a platform-less config blob (when a
    platform is expected), on a config platform that disagrees with what
    was expected, and, when the index entry also carries a `platform`
    field, on that field being malformed or disagreeing with either the
    expected platform or the config blob's own platform.
    """
    if expected_os is None and expected_architecture is None:
        return

    config_os = config_doc.get("os")
    config_arch = config_doc.get("architecture")

    if expected_os is not None and (not isinstance(config_os, str) or config_os != expected_os):
        raise OciDigestExtractionError(
            f"config blob 'os' is {config_os!r}, expected {expected_os!r}."
        )
    if expected_architecture is not None and (
        not isinstance(config_arch, str) or config_arch != expected_architecture
    ):
        raise OciDigestExtractionError(
            f"config blob 'architecture' is {config_arch!r}, expected {expected_architecture!r}."
        )

    index_platform = index_entry.get("platform")
    if index_platform is None:
        return
    if not isinstance(index_platform, dict):
        raise OciDigestExtractionError(
            "the index manifest entry's 'platform' is present but not an object."
        )
    index_os = index_platform.get("os")
    index_arch = index_platform.get("architecture")
    if expected_os is not None and index_os != expected_os:
        raise OciDigestExtractionError(
            f"index platform.os is {index_os!r}, expected {expected_os!r}."
        )
    if expected_architecture is not None and index_arch != expected_architecture:
        raise OciDigestExtractionError(
            f"index platform.architecture is {index_arch!r}, expected {expected_architecture!r}."
        )
    if index_os != config_os:
        raise OciDigestExtractionError(
            f"index platform.os ({index_os!r}) conflicts with the config blob's own "
            f"os ({config_os!r})."
        )
    if index_arch != config_arch:
        raise OciDigestExtractionError(
            f"index platform.architecture ({index_arch!r}) conflicts with the config "
            f"blob's own architecture ({config_arch!r})."
        )


def validate_oci_archive(
    archive_path: str,
    *,
    expected_os: str | None = None,
    expected_architecture: str | None = None,
) -> dict[str, str | frozenset[str]]:
    """Validates the complete OCI descriptor graph in the archive at
    `archive_path`. Fails closed (raises `OciDigestExtractionError`) on
    any structural defect, missing/corrupted/tampered blob, unsafe or
    unexpected tar member, or platform mismatch/contradiction. Returns
    `{"manifest_digest": ..., "config_digest": ...}` once every check has
    passed.
    """
    try:
        with tarfile.open(archive_path, "r") as tar:
            members, _directories = _safe_members(tar)
            return _validate_descriptor_graph(
                tar, members, expected_os=expected_os, expected_architecture=expected_architecture
            )
    except tarfile.ReadError as exc:
        raise OciDigestExtractionError(f"{archive_path!r} is not a readable tar archive.") from exc
    except FileNotFoundError as exc:
        raise OciDigestExtractionError(f"{archive_path!r} does not exist.") from exc


def validate_and_extract_oci_layout(
    archive_path: str,
    dest_dir: str,
    *,
    expected_os: str | None = None,
    expected_architecture: str | None = None,
) -> dict[str, str | frozenset[str]]:
    """Correction pass, item 3: validates the archive exactly as
    `validate_oci_archive` does, then -- within the **same** open tar
    handle, so there is no window between validation and extraction in
    which the underlying archive file could change (no validate/extract
    TOCTOU) -- extracts exactly the validated, safe member set (the two
    blob directories, plus every validated regular file: `oci-layout`,
    `index.json`, and every blob) into `dest_dir`, which must not already
    exist. Never a generic `tarfile.extractall()`/`tar -xf`, which would
    extract whatever the archive contains, safe or not -- every byte
    written to disk here has already been independently validated as
    part of the permitted OCI layout envelope.
    """
    if os.path.exists(dest_dir):
        raise OciDigestExtractionError(
            f"destination directory already exists: {dest_dir!r} -- refusing to extract "
            "into a non-fresh directory."
        )
    try:
        with tarfile.open(archive_path, "r") as tar:
            members, directories = _safe_members(tar)
            result = _validate_descriptor_graph(
                tar, members, expected_os=expected_os, expected_architecture=expected_architecture
            )

            # Correction pass: extraction never trusts `members` (every
            # syntactically-valid-envelope member) directly for blobs --
            # only the explicit, descriptor-graph-derived referenced set
            # `_validate_descriptor_graph` just proved is complete and
            # correct. This is deliberate defense in depth: even if the
            # unreferenced-blob check above this call were ever weakened
            # or bypassed by a future change, extraction itself still
            # could not be tricked into writing an extra blob to disk,
            # because it never iterates "every blob-shaped member" at all.
            extraction_targets = {"oci-layout", "index.json"} | {
                "blobs/" + digest.replace(":", "/", 1) for digest in result["referenced_digests"]
            }

            os.makedirs(dest_dir)
            try:
                for directory_name in sorted(directories):
                    os.makedirs(os.path.join(dest_dir, directory_name), exist_ok=True)
                for name in sorted(extraction_targets):
                    member = members.get(name)
                    if member is None:
                        raise OciDigestExtractionError(
                            f"internal error: expected member {name!r} missing after "
                            "validation already succeeded -- refusing to extract an "
                            "incomplete layout."
                        )
                    target_path = os.path.join(dest_dir, name)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    fileobj = tar.extractfile(member)
                    if fileobj is None:
                        raise OciDigestExtractionError(
                            f"{name!r} could not be extracted (not a regular file)."
                        )
                    with open(target_path, "wb") as out:
                        shutil.copyfileobj(fileobj, out)
            except Exception:
                # Never leave a partially-written destination directory
                # behind -- a caller (e.g. the deploy workflow) must be
                # able to treat "extraction raised" and "the directory
                # does not exist" as the same, unambiguous failure state.
                shutil.rmtree(dest_dir, ignore_errors=True)
                raise
    except tarfile.ReadError as exc:
        raise OciDigestExtractionError(f"{archive_path!r} is not a readable tar archive.") from exc
    except FileNotFoundError as exc:
        raise OciDigestExtractionError(f"{archive_path!r} does not exist.") from exc

    return result


def extract_manifest_digest(archive_path: str) -> str:
    """Backward-compatible entry point: runs the complete descriptor-
    graph validation above and returns just the manifest digest.
    """
    return validate_oci_archive(archive_path)["manifest_digest"]


def extract_config_digest(archive_path: str) -> str:
    """Returns the validated archive's own config blob digest -- used to
    cryptographically prove a separately-loaded Docker-format export of
    the *same* build represents the identical image: a locally loaded
    Docker image's own `docker inspect --format='{{.Id}}'` is exactly
    its config blob's digest, so comparing the two (never assuming they
    match merely because both came from "the same build") closes the
    "two archive formats" gap for a workflow that keeps both exports for
    local inspection convenience. Empirically confirmed identical for a
    real single-invocation dual-export build during an earlier
    correction pass.
    """
    return validate_oci_archive(archive_path)["config_digest"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_path", help="Path to the OCI archive (index.json inside a tar).")
    parser.add_argument(
        "--field",
        choices=("manifest_digest", "config_digest"),
        default="manifest_digest",
        help="Which validated digest to print (default: manifest_digest).",
    )
    parser.add_argument(
        "--expected-os",
        default=None,
        help="Fail closed unless the config blob's own 'os' (and the index platform, if present) "
        "match exactly.",
    )
    parser.add_argument(
        "--expected-architecture",
        default=None,
        help="Fail closed unless the config blob's own 'architecture' (and the index platform, "
        "if present) match exactly.",
    )
    parser.add_argument(
        "--extract-to",
        default=None,
        metavar="DIR",
        help="Correction pass, item 3: instead of only validating, also safely extract exactly "
        "the validated OCI layout member set into this fresh directory (must not already "
        "exist) -- replaces a separate, unrestricted 'tar -xf' with validator-controlled "
        "extraction, in the same open tar handle used for validation (no TOCTOU window).",
    )
    args = parser.parse_args(argv)

    try:
        if args.extract_to is not None:
            result = validate_and_extract_oci_layout(
                args.archive_path,
                args.extract_to,
                expected_os=args.expected_os,
                expected_architecture=args.expected_architecture,
            )
        else:
            result = validate_oci_archive(
                args.archive_path,
                expected_os=args.expected_os,
                expected_architecture=args.expected_architecture,
            )
    except OciDigestExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(result[args.field])
    return 0


if __name__ == "__main__":
    sys.exit(main())
