import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(sa.Text)
    email: Mapped[str] = mapped_column(sa.Text, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.Text)
    locale: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    #: Quem opera a PLATAFORMA (quem vende e dá suporte), não uma clínica.
    #:
    #: É um flag no usuário, e não uma tabela nova, porque login, senha e
    #: nome já existem aqui. O que muda é o token: `kind="platform"`, que
    #: nenhuma rota de clínica aceita e que só as rotas `/platform/*` leem.
    #: Um operador da plataforma não é membro de clínica nenhuma por esse
    #: caminho: ver a clínica do cliente é ato de suporte, registrado na
    #: trilha da clínica com o nome de quem olhou.
    is_platform_operator: Mapped[bool] = mapped_column(sa.Boolean, default=False)
