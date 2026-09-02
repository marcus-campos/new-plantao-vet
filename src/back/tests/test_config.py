from app.core.config import Settings


def test_settings_have_dev_defaults():
    settings = Settings(_env_file=None)
    assert settings.env == "dev"
    assert settings.jwt_secret == "dev-secret-change-me"
    assert settings.database_url.startswith("postgresql+asyncpg://")
