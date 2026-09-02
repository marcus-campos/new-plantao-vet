import pytest

from app.compliance import get_profile
from app.i18n.catalog import catalog_keys

PROFILES = ("br", "br_human")


def test_get_profile_br():
    profile = get_profile("br")
    assert profile.name == "br"
    assert profile.license_authority_label_key == "compliance.br.license_authority_label"
    assert profile.requires_daily_progress_note is True
    assert profile.retention_years == 5
    assert profile.responsible_label_key == "responsible.owner"


def test_get_profile_br_human():
    """Saúde humana: mesmo produto, retenção e registro profissional diferentes."""
    profile = get_profile("br_human")
    assert profile.retention_years == 20
    assert profile.license_authority_label_key == "compliance.br_human.license_authority_label"
    assert profile.responsible_label_key == "responsible.guardian"


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("atlantis")


@pytest.mark.parametrize("name", PROFILES)
def test_todas_as_chaves_do_perfil_existem_nos_dois_catalogos(name):
    """Perfil que aponta para chave inexistente quebra a tela do cliente."""
    profile = get_profile(name)
    keys = {profile.license_authority_label_key, profile.responsible_label_key}
    keys |= {kind.label_key for kind in profile.patient_identifier_kinds}
    for locale in ("pt-BR", "en"):
        assert keys <= catalog_keys(locale), f"{name} / {locale}"


@pytest.mark.parametrize("name", PROFILES)
def test_perfil_declara_ao_menos_um_identificador(name):
    assert get_profile(name).patient_identifier_kinds
