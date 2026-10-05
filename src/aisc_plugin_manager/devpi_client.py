import logging
import httpx
from packaging.version import parse as parse_version
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DevpiIndexResult(BaseModel):
    projects: list[str] = Field(default_factory=list)


class DevpiIndexResponse(BaseModel):
    result: DevpiIndexResult


class DevpiPackageResponse(BaseModel):
    result: dict[str, dict] = Field(default_factory=dict)


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
        uploaded (2026-10-05)."""
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

    def list_packages(self) -> dict[str, str]:
        """Return {package name: latest version} for the index (the catalogue shows the latest only)."""
        return {pkg: versions[-1] for pkg, versions in self.list_versions().items()}

    def close(self):
        """Close the HTTP connection pool."""
        self.client.close()