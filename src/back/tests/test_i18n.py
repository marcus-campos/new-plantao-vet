import pytest

from app.i18n.catalog import catalog_keys, translate


def test_catalogs_have_identical_keys():
    assert catalog_keys("pt-BR") == catalog_keys("en")


def test_translate_interpolates_params():
    assert translate("task.check", "pt-BR", name="Fluidoterapia") == "Checagem: Fluidoterapia"
    assert translate("task.check", "en", name="Fluid therapy") == "Check: Fluid therapy"


def test_translate_falls_back_to_pt_br_for_unknown_locale():
    assert translate("ceremony.owner_contact", "xx-XX") == "Contato com o tutor"


def test_missing_key_raises_key_error():
    with pytest.raises(KeyError):
        translate("missing.key", "pt-BR")
