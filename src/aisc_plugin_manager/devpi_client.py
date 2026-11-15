import logging
import re

import httpx
from packaging.utils import (
    InvalidWheelFilename,
    canonicalize_name,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version
from packaging.version import parse as parse_version
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DevpiIndexResult(BaseModel):
    projects: list[str] = Field(default_factory=list)


class DevpiIndexResponse(BaseModel):
    result: DevpiIndexResult


class DevpiPackageResponse(BaseModel):
    result: dict[str, dict] = Field(default_factory=dict)


#: a link of a simple page: its file name and the sha256 the index gives in its fragment
_SIMPLE_LINK = re.compile(r'href="[^"#]*/([^/"#]+)#sha256=([0-9a-fA-F]{64})"')


class DevpiClient:
    def __init__(self, base_url: str, index: str, user: str | None = None, password: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.index = index if index.startswith("/") else f"/{index}"

        self.full_index_url = f"{self.base_url}{self.index}"
        self.simple_index_url = f"{self.full_index_url}/+simple/"

        if (user is not None) != (password is not None):
            raise ValueError("Both user and password must be provided for an authenticated registry")

        self.user = user
        self.password = password

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Accept": "application/json"},
            timeout=10.0
        )

        self._initialized = False

    def _initialize_devpi(self):
        if self._initialized:
            return

        # Log in only with real (non-empty) credentials. Empty strings mean a public index:
        # listing it needs no login, and POST /+login with empty credentials answers 401, which
        # would break registry discovery.
        if self.user and self.password:
            response = self.client.post(
                "/+login",
                json={"user": self.user, "password": self.password}
            )
            response.raise_for_status()

        self._initialized = True

    def list_versions(self) -> dict[str, list[str]]:
        """Return {package name: every version in the index}; a package whose versions cannot be read is
        skipped. The loader needs them all: a project keeps the version it installed after a newer one is
        uploaded."""
        self._initialize_devpi()
        result = {}

        try:
            index_response = self.client.get(self.index)
            index_response.raise_for_status()

            index_data = DevpiIndexResponse.model_validate(index_response.json())
            projects = index_data.result.projects

            for pkg in projects:
                try:
                    pkg_response = self.client.get(f"{self.index}/{pkg}")
                    pkg_response.raise_for_status()

                    pkg_data = DevpiPackageResponse.model_validate(pkg_response.json())
                    if pkg_data.result:
                        result[pkg] = sorted(pkg_data.result, key=parse_version)

                except Exception as e:
                    logger.warning(f"Failed to fetch versions for {pkg}: {e}")

        except Exception as e:
            logger.error(f"Failed to fetch package list from devpi: {e}")

        return result
    def wheel_sha256(self, package: str, version: str) -> str | None:
        """The sha256 (64 lowercase hex) of the wheel the index holds for that version of the package, read
        from its simple page; None when the index lists no wheel of that version or cannot be read."""
        self._initialize_devpi()
        try:
            wanted = Version(version)
            response = self.client.get(f"{self.index}/+simple/{canonicalize_name(package)}/",
                                       headers={"Accept": "text/html"})
            response.raise_for_status()
        except (InvalidVersion, httpx.HTTPError) as exc:
            logger.warning(f"The index could not say which wheel of {package} {version} it holds: {exc}")
            return None
        for filename, digest in _SIMPLE_LINK.findall(response.text):
            if not filename.endswith(".whl"):
                continue
            try:
                name, found, _build, _tags = parse_wheel_filename(filename)
            except InvalidWheelFilename:
                continue
            if name == canonicalize_name(package) and found == wanted:
                return digest.lower()
        return None
