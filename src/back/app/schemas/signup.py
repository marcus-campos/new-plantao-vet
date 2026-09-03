"""O contrato da porta pública: o mínimo para uma clínica existir."""

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    """Quatro campos obrigatórios, e nenhum deles é jargão.

    Sem `slug` (gerado do nome), sem `plan_tier` (a porta pública não escolhe
    plano) e sem `timezone` (o Brasil é o mercado da v1, e a clínica troca nas
    configurações). Cada campo a mais aqui é um cadastro a menos."""

    clinic_name: str = Field(min_length=2, max_length=120)
    admin_name: str = Field(min_length=2, max_length=120)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    #: 72, não 128: `bcrypt.hashpw` LEVANTA `ValueError` acima de 72 bytes em vez
    #: de truncar (bcrypt 5.0.0), e não há handler para isso — virava 500 no
    #: único formulário público do lançamento.
    password: str = Field(min_length=8, max_length=72)
    phone: str | None = Field(default=None, max_length=32)
