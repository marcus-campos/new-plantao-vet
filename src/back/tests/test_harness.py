import sqlalchemy as sa


async def test_db_session_connects_to_test_database(db_session):
    name = (await db_session.execute(sa.text("SELECT current_database()"))).scalar_one()
    # Banco por processo: nunca o de produção, nunca o de outra execução.
    assert name.startswith("plantaovet_test")
    assert name != "plantaovet"


async def test_rollback_isolation_step1_creates_scratch_table(db_session):
    await db_session.execute(sa.text("CREATE TABLE rollback_probe (id int)"))
    await db_session.execute(sa.text("INSERT INTO rollback_probe VALUES (1)"))
    count = (await db_session.execute(sa.text("SELECT count(*) FROM rollback_probe"))).scalar_one()
    assert count == 1


async def test_rollback_isolation_step2_scratch_table_is_gone(db_session):
    exists = (
        await db_session.execute(sa.text("SELECT to_regclass('rollback_probe') IS NOT NULL"))
    ).scalar_one()
    assert exists is False


async def test_session_commit_stays_inside_test_transaction(db_session):
    await db_session.execute(sa.text("CREATE TABLE commit_probe (id int)"))
    await db_session.commit()
    exists = (
        await db_session.execute(sa.text("SELECT to_regclass('commit_probe') IS NOT NULL"))
    ).scalar_one()
    assert exists is True


async def test_client_serves_health_with_overridden_session(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
