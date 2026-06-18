"""
Application configuration.

Reads all settings from environment variables (which are populated
from the .env file during local development).

Using Pydantic's BaseSettings gives us:
- Automatic type conversion (string "5432" becomes integer 5432)
- Automatic validation (missing required variables raise an error on startup)
- IDE autocomplete for all settings
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    All application settings.
    
    Pydantic reads these from environment variables automatically.
    The variable name in the .env file must match the field name here
    (case-insensitive).
    """
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "smartretail"
    db_user: str = "smartretail_user"
    db_password: str = "smartretail_pass_2024"
    
    # API
    api_secret_key: str = "change-in-production"
    api_key: str = "dev-api-key-12345"
    
    # MLflow
    mlflow_tracking_uri: str = "http://localhost:5000"
    
    @property
    def database_url(self) -> str:
        """
        Constructs the full database connection string.
        Format: postgresql://user:password@host:port/database
        """
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
    
    class Config:
        env_file = ".env"           # Which file to read from
        env_file_encoding = "utf-8"


# Create a single instance that the whole application imports
# This is called the Singleton pattern
settings = Settings()