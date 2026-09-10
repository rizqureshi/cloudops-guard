"""A real Azure Blob Storage `ReportBlobStore`
(`docs/milestones/v0.4.0-ingestion-api.md` §H, Phase 4G-A).

**Authentication boundary, deliberately separated from this class**: this
module never constructs its own `BlobServiceClient` from a connection
string or account key -- it only ever wraps a caller-supplied
`BlobServiceClient`. `create_managed_identity_blob_service_client` (below)
is the *one* function in this package that builds a real, production
`BlobServiceClient`, and it does so exclusively via
`azure.identity.DefaultAzureCredential` (managed identity in Azure
Container Apps; never a storage-account key). Local/offline testing
against Azurite constructs its own `BlobServiceClient` from Azurite's own
fixed, publicly-documented well-known development account key (never a
real Azure credential) and passes it to `AzureBlobReportBlobStore`
directly -- see `tests/ingestion_azure/test_azure_blob_store_azurite.py`.
This separation is what lets `put_if_absent`/`get`/`delete` be tested
completely offline while the *authentication* method is exercised only by
inspecting `create_managed_identity_blob_service_client`'s own,
narrowly-scoped unit test (`tests/ingestion_azure/
test_blob_store_managed_identity.py`), which never contacts a real Azure
endpoint either.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from cloudops_guard.ingestion.interfaces import ReportBlobStore

from .errors import AzureAdapterError

#: The only hostname shape this codebase's one recorded, approved region
#: (`canadacentral`, Azure public cloud -- `production_config.
#: EXPECTED_REGION`) ever uses for a real storage account: lowercase
#: alphanumeric, 3-24 characters (Azure's own storage-account naming
#: rule), under the public-cloud Blob Storage suffix. A sovereign-cloud
#: suffix (`blob.core.usgovcloudapi.net`, `blob.core.chinacloudapi.cn`,
#: etc.) is deliberately never accepted -- this codebase has never
#: recorded a decision to deploy to any cloud other than Azure public.
_APPROVED_BLOB_HOSTNAME = re.compile(r"^[a-z0-9]{3,24}\.blob\.core\.windows\.net$")


def _validate_blob_account_url(account_url: str) -> None:
    """**Correction-pass item 9**: `create_managed_identity_blob_service_
    client` previously only checked `account_url.startswith("https://")`,
    which a value like `https://evil.example/@real-account.blob.core.
    windows.net` or `https://account.blob.core.windows.net.evil.example`
    both satisfy -- either would hand `DefaultAzureCredential`'s live
    Azure Storage OAuth bearer token to an attacker-controlled host as an
    ordinary `Authorization` header on every blob request this process
    ever makes, since `BlobServiceClient` sends requests to whatever host
    `account_url` actually names, not to whatever the string merely
    *looks like* it names. Reproduced directly (see `tests/ingestion_azure/
    test_blob_store_managed_identity.py`) before this fix: both a
    userinfo-smuggled fake domain and a query/fragment/port/path-bearing
    URL were silently accepted.

    Validates, in order: exact `https` scheme; no userinfo (an `@` in the
    authority, which would let a fake "domain" precede the real host); no
    explicit port; no query string; no fragment; no path beyond the bare
    root; and a hostname matching exactly the approved
    `<account>.blob.core.windows.net` shape (`_APPROVED_BLOB_HOSTNAME`) --
    a *suffix* match would itself be exploitable via a hostname like
    `attacker-blob.core.windows.net.evil.example`, so this uses a full
    `fullmatch` against the parsed `hostname` component alone, never a
    substring/suffix check against the raw string.
    """
    try:
        parsed = urlsplit(account_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"account_url is not a valid URL: {exc}") from exc

    if parsed.scheme != "https":
        raise ValueError("account_url must use the https:// scheme.")
    if "@" in parsed.netloc:
        raise ValueError("account_url must not contain userinfo.")
    if port is not None:
        raise ValueError("account_url must not specify an explicit port.")
    if parsed.query:
        raise ValueError("account_url must not contain a query string.")
    if parsed.fragment:
        raise ValueError("account_url must not contain a fragment.")
    if parsed.path not in ("", "/"):
        raise ValueError("account_url must not contain a path.")
    hostname = parsed.hostname
    if hostname is None or not _APPROVED_BLOB_HOSTNAME.fullmatch(hostname):
        raise ValueError(
            "account_url's hostname must exactly match "
            "<account>.blob.core.windows.net (the approved Azure Blob "
            "Storage public-cloud endpoint pattern)."
        )


def create_managed_identity_blob_service_client(account_url: str) -> BlobServiceClient:
    """The **only** production `BlobServiceClient` constructor in this
    codebase. Authenticates via `DefaultAzureCredential` -- in Azure
    Container Apps, this resolves to the container app's own assigned
    managed identity; there is no storage-account key anywhere in this
    call, this module, or any container image built from it (task 4/8's
    explicit requirement). `account_url` is the blob endpoint URL only
    (e.g. `https://<account>.blob.core.windows.net`) -- never a
    connection string, and never a value containing a credential.
    Strictly validated by `_validate_blob_account_url` before any
    credential is ever constructed (correction-pass item 9).
    """
    _validate_blob_account_url(account_url)
    credential = DefaultAzureCredential()
    return BlobServiceClient(account_url=account_url, credential=credential)


class AzureBlobReportBlobStore(ReportBlobStore):
    """`storage_key` (always `f"{tenant_id}/{ingestion_id}"`,
    `storage_keys.derive_storage_key`) is used verbatim as the blob name
    within `container_name` -- Azure Blob Storage's own flat namespace
    with `/`-delimited virtual "folders" makes this a natural, safe
    mapping requiring no further escaping (the storage key was already
    validated to contain no `..`, no NUL byte, and no path separator
    *within* either identifier -- the single `/` joining tenant and
    ingestion IDs is the only one present, by construction).
    """

    def __init__(self, client: BlobServiceClient, *, container_name: str) -> None:
        self._container = client.get_container_client(container_name)

    def put(self, storage_key: str, data: bytes) -> None:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        try:
            self._container.upload_blob(name=storage_key, data=bytes(data), overwrite=True)
        except ResourceNotFoundError as exc:
            raise AzureAdapterError(
                "the configured blob container does not exist -- provisioning failure, "
                "not a request-time condition."
            ) from exc

    def put_if_absent(self, storage_key: str, data: bytes) -> bool:
        """Uses Azure Blob Storage's own conditional-create primitive
        (`overwrite=False`, which the SDK implements as an
        `If-None-Match: *` upload precondition) -- never a
        check-then-upload pair, which could not guarantee correctness
        under two concurrent callers for the same `storage_key` (the same
        reasoning `InMemoryReportBlobStore.put_if_absent`'s own docstring
        already establishes). A precondition failure (the blob already
        exists, from any earlier caller, at any earlier time) is caught
        and translated to `False` -- **never** turned into an overwrite
        (task 4's explicit requirement).
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        try:
            self._container.upload_blob(name=storage_key, data=bytes(data), overwrite=False)
            return True
        except ResourceExistsError:
            return False
        except ResourceNotFoundError as exc:
            raise AzureAdapterError(
                "the configured blob container does not exist -- provisioning failure, "
                "not a request-time condition."
            ) from exc

    def get(self, storage_key: str) -> bytes | None:
        try:
            downloader = self._container.download_blob(storage_key)
            return downloader.readall()
        except ResourceNotFoundError:
            return None

    def delete(self, storage_key: str) -> None:
        """Repeated deletion of the same key is always safe -- a missing
        blob is treated identically to a successfully deleted one, never
        raised (matches `InMemoryReportBlobStore.delete`'s own contract
        exactly).
        """
        try:
            self._container.delete_blob(storage_key)
        except ResourceNotFoundError:
            pass
