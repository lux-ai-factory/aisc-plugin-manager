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
    def __init__(self, base_url: str, index: str, user: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.index = index if index.startswith("/") else f"/{index}"

        self.full_index_url = f"{self.base_url}{self.index}"
        self.simple_index_url = f"{self.full_index_url}/+simple/"

        self.user = user
        self.password = password

        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Accept": "application/json"},
            timeout=10.0
        )

        self._initialized = False

    def _initialize_devpi(self):
        if not self._initialized:
            response = self.client.post(
                "/+login",
                json={"user": self.user, "password": self.password}
            )
            response.raise_for_status()
            self._initialized = True

    def list_packages(self) -> dict[str, str]:
        """Returns a dictionary of {package_name: latest_version}"""
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
                    versions = pkg_data.result

                    if versions:
                        latest_version = max(versions.keys(), key=parse_version)
                        result[pkg] = str(latest_version)

                except Exception as e:
                    logger.warning(f"Failed to fetch versions for {pkg}: {e}")

        except Exception as e:
            logger.error(f"Failed to fetch package list from devpi: {e}")

        return result

    def close(self):
        """Helper to cleanly shut down the HTTP connection pool."""
        self.client.close()