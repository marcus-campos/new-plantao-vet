"""Cria (ou promove) um operador da plataforma: quem vende e dá suporte.

    uv run python -m scripts.platform_operator "Seu Nome" voce@plantao.vet

A senha é pedida no terminal, sem eco. É o único jeito de criar o PRIMEIRO
operador em produção: a interface da plataforma exige um operador para
criar outro, e o seed da demo só existe na demo.
"""

import asyncio
import getpass
import sys

import sqlalchemy as sa

from app.core.db import async_session_factory
from app.core.security import hash_password
from app.models.user import User


async def main(name: str, email: str, password: str) -> None:
    async with async_session_factory() as session:
        user = await session.scalar(sa.select(User).where(User.email == email.lower()))
        if user is None:
            session.add(
                User(
                    name=name,
                    email=email.lower(),
                    password_hash=hash_password(password),
                    is_platform_operator=True,
                )
            )
            print(f"Operador criado: {email}")
        else:
            user.is_platform_operator = True
            user.password_hash = hash_password(password)
            user.is_active = True
            print(f"Usuário existente promovido a operador da plataforma: {email}")
        await session.commit()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    senha = getpass.getpass("Senha do operador: ")
    if len(senha) < 8:
        print("A senha precisa de ao menos 8 caracteres.")
        raise SystemExit(2)
    asyncio.run(main(sys.argv[1], sys.argv[2], senha))
