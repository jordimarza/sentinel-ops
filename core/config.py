"""
Configuration management with Secret Manager + local fallback.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    """
    Application settings with Secret Manager and environment variable support.

    Priority: Secret Manager > Environment Variable > Default
    """
    # Odoo connection (production)
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_username: str = ""
    odoo_password: str = ""

    # Odoo development instance (for testing)
    odoo_dev_url: str = ""
    odoo_dev_db: str = ""
    odoo_dev_username: str = ""  # Falls back to odoo_username if empty
    odoo_dev_password: str = ""  # Falls back to odoo_password if empty

    # BigQuery
    bq_project: str = ""
    bq_dataset: str = "sentinel_ops"
    bq_audit_table: str = "audit_log"
    bq_kpi_table: str = "job_kpis"

    # Slack alerts
    slack_webhook_url: str = ""
    slack_channel: str = "#sentinel-alerts"

    # GCP
    gcp_project: str = ""

    # Runtime
    environment: str = "development"
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load settings from environment variables."""
        return cls(
            odoo_url=os.getenv("ODOO_URL", ""),
            odoo_db=os.getenv("ODOO_DB", ""),
            odoo_username=os.getenv("ODOO_USERNAME", ""),
            odoo_password=os.getenv("ODOO_PASSWORD", ""),
            # Dev Odoo (same credentials by default)
            odoo_dev_url=os.getenv("ODOO_DEV_URL", ""),
            odoo_dev_db=os.getenv("ODOO_DEV_DB", ""),
            odoo_dev_username=os.getenv("ODOO_DEV_USERNAME", ""),
            odoo_dev_password=os.getenv("ODOO_DEV_PASSWORD", ""),
            # BigQuery
            bq_project=os.getenv("BQ_PROJECT", os.getenv("GCP_PROJECT", "")),
            bq_dataset=os.getenv("BQ_DATASET", "sentinel_ops"),
            bq_audit_table=os.getenv("BQ_AUDIT_TABLE", "audit_log"),
            bq_kpi_table=os.getenv("BQ_KPI_TABLE", "job_kpis"),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL", ""),
            slack_channel=os.getenv("SLACK_CHANNEL", "#sentinel-alerts"),
            gcp_project=os.getenv("GCP_PROJECT", ""),
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    @classmethod
    def from_secret_manager(cls, project_id: str, prefix: str = "sentinel-ops") -> "Settings":
        """
        Load settings from Google Secret Manager.

        Secret naming convention: {prefix}-{setting_name}
        Example: sentinel-ops-odoo-password
        """
        try:
            from google.cloud import secretmanager
            client = secretmanager.SecretManagerServiceClient()
        except ImportError:
            logger.warning("Secret Manager client not available, falling back to environment")
            return cls.from_environment()
        except Exception as e:
            logger.warning(f"Failed to initialize Secret Manager: {e}, falling back to environment")
            return cls.from_environment()

        def get_secret(name: str, default: str = "") -> str:
            """Get a secret value, falling back to environment or default."""
            secret_name = f"{prefix}-{name}".replace("_", "-")
            try:
                secret_path = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
                response = client.access_secret_version(request={"name": secret_path})
                return response.payload.data.decode("UTF-8")
            except Exception:
                # Fall back to environment variable
                env_name = name.upper().replace("-", "_")
                return os.getenv(env_name, default)

        return cls(
            odoo_url=get_secret("odoo-url"),
            odoo_db=get_secret("odoo-db"),
            odoo_username=get_secret("odoo-username"),
            odoo_password=get_secret("odoo-password"),
            # Dev Odoo (optional, for testing with use_dev=true)
            odoo_dev_url=get_secret("odoo-dev-url"),
            odoo_dev_db=get_secret("odoo-dev-db"),
            odoo_dev_username=get_secret("odoo-dev-username"),  # Falls back to odoo_username
            odoo_dev_password=get_secret("odoo-dev-password"),  # Falls back to odoo_password
            # BigQuery
            bq_project=get_secret("bq-project", os.getenv("GCP_PROJECT", "")),
            bq_dataset=get_secret("bq-dataset", "sentinel_ops"),
            bq_audit_table=get_secret("bq-audit-table", "audit_log"),
            bq_kpi_table=get_secret("bq-kpi-table", "job_kpis"),
            slack_webhook_url=get_secret("slack-webhook-url"),
            slack_channel=get_secret("slack-channel", "#sentinel-alerts"),
            gcp_project=project_id,
            environment=os.getenv("ENVIRONMENT", "production"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"

    def get_dev_odoo_config(self) -> dict:
        """
        Get development Odoo configuration.

        Returns dict with url, db, username, password.
        Username/password fall back to production credentials if not set.
        """
        return {
            "url": self.odoo_dev_url,
            "db": self.odoo_dev_db,
            "username": self.odoo_dev_username or self.odoo_username,
            "password": self.odoo_dev_password or self.odoo_password,
        }

    def is_dev_odoo_configured(self) -> bool:
        """Check if development Odoo is properly configured."""
        return bool(self.odoo_dev_url and self.odoo_dev_db)

    # Placeholder values that indicate unconfigured settings
    PLACEHOLDER_VALUES = {
        "https://your-odoo-instance.com",
        "your_database_name",
        "your_api_username",
        "your_api_password",
        "your-gcp-project",
        "your-email@alohas.com",
        "your-api-key-or-password",
    }

    def _is_placeholder(self, value: str) -> bool:
        """Check if a value is a placeholder."""
        return value in self.PLACEHOLDER_VALUES

    def validate(self) -> list[str]:
        """
        Validate required settings.

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if not self.odoo_url:
            errors.append("ODOO_URL is required")
        if not self.odoo_db:
            errors.append("ODOO_DB is required")
        if not self.odoo_username:
            errors.append("ODOO_USERNAME is required")
        if not self.odoo_password:
            errors.append("ODOO_PASSWORD is required")

        return errors

    def validate_for_job(self) -> None:
        """
        Validate settings required for job execution.

        Raises:
            ValueError: With clear message if configuration is invalid
        """
        errors = []

        if not self.odoo_url or self._is_placeholder(self.odoo_url):
            errors.append("ODOO_URL not configured (still has placeholder value)")
        if not self.odoo_db or self._is_placeholder(self.odoo_db):
            errors.append("ODOO_DB not configured (still has placeholder value)")
        if not self.odoo_username or self._is_placeholder(self.odoo_username):
            errors.append("ODOO_USERNAME not configured")
        if not self.odoo_password or self._is_placeholder(self.odoo_password):
            errors.append("ODOO_PASSWORD not configured")

        if errors:
            error_msg = "\n".join([f"  - {e}" for e in errors])
            raise ValueError(
                f"\nConfiguration Error:\n{error_msg}\n\n"
                f"Please configure your .env.local file:\n"
                f"  cp .env.local.template .env.local\n"
                f"  # Then edit .env.local with your credentials\n"
            )

    def is_bq_configured(self) -> bool:
        """Check if BigQuery is properly configured (not placeholder)."""
        if not self.bq_project:
            return False
        return not self._is_placeholder(self.bq_project)


def _load_dotenv():
    """Load .env.local file if it exists."""
    try:
        from dotenv import load_dotenv
        import os

        # Try .env.local first, then .env
        env_local = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")
        env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

        if os.path.exists(env_local):
            load_dotenv(env_local)
            logger.debug(f"Loaded settings from {env_local}")
        elif os.path.exists(env_file):
            load_dotenv(env_file)
            logger.debug(f"Loaded settings from {env_file}")
    except ImportError:
        pass


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Get application settings (cached).

    In production (GCP_PROJECT set), tries Secret Manager first.
    Otherwise, loads from environment/.env.local.
    """
    _load_dotenv()

    gcp_project = os.getenv("GCP_PROJECT")

    if gcp_project and os.getenv("ENVIRONMENT", "").lower() == "production":
        logger.info("Loading settings from Secret Manager")
        return Settings.from_secret_manager(gcp_project)
    else:
        logger.info("Loading settings from environment")
        return Settings.from_environment()
