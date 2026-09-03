"""O contrato da porta pública: o mínimo para uma clínica existir."""

from pydantic import BaseModel, Field, field_validator

#: `bcrypt.hashpw` (bcrypt 5.0.0) LEVANTA `ValueError` acima de 72 BYTES —
#: não caracteres — de UTF-8, e não há handler para isso no `main.py`.
BCRYPT_MAX_PASSWORD_BYTES = 72


def senha_cabe_no_bcrypt(password: str) -> str:
    """Valida o limite do bcrypt em BYTES, não em caracteres.

    `Field(max_length=...)` do Pydantic conta caracteres Unicode, e num
    produto pt-BR isso é a diferença errada: uma senha de 72 caracteres com UM
    acento (2 bytes em UTF-8) já são 73 bytes na hora de `password.encode()`
    em `hash_password` — o mesmo `ValueError` sem handler, o mesmo 500, só que
    na senha comum de um usuário brasileiro, não na borda. Por isso o limite
    mora aqui, medido em bytes, e não no `max_length` do campo."""
    if len(password.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"password deve ter no máximo {BCRYPT_MAX_PASSWORD_BYTES} bytes")
    return password


class SignupRequest(BaseModel):
    """Quatro campos obrigatórios, e nenhum deles é jargão.

    Sem `slug` (gerado do nome), sem `plan_tier` (a porta pública não escolhe
    plano) e sem `timezone` (o Brasil é o mercado da v1, e a clínica troca nas
    configurações). Cada campo a mais aqui é um cadastro a menos."""

    clinic_name: str = Field(min_length=2, max_length=120)
    admin_name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    password: str = Field(min_length=8)
    phone: str | None = Field(default=None, max_length=32)

    _valida_senha = field_validator("password")(senha_cabe_no_bcrypt)
