from typing import Optional


def to_py(value):
    if value is None:
        return None
    try:
        return value.to_py()
    except Exception:
        return value


async def q_all(env, sql: str, *params) -> list[dict]:
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    result = await stmt.run()
    rows = to_py(result.results)
    return list(rows or [])


async def q_first(env, sql: str, *params) -> Optional[dict]:
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    row = to_py(await stmt.first())
    return dict(row) if row else None


async def q_value(env, sql: str, *params, key: str = "c"):
    row = await q_first(env, sql, *params)
    return row.get(key) if row else None


async def q_write(env, sql: str, *params):
    stmt = env.DB.prepare(sql)
    if params:
        stmt = stmt.bind(*params)
    return await stmt.run()


async def q_batch(env, queries: list[tuple[str, tuple]]) -> list[list[dict]]:
    statements = []
    for sql, params in queries:
        stmt = env.DB.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        statements.append(stmt)
    results = await env.DB.batch(statements)
    return [list(to_py(result.results) or []) for result in results]


async def q_write_batch(env, queries: list[tuple[str, tuple]]):
    statements = []
    for sql, params in queries:
        stmt = env.DB.prepare(sql)
        if params:
            stmt = stmt.bind(*params)
        statements.append(stmt)
    return await env.DB.batch(statements)


def last_row_id(result) -> int:
    try:
        return int(result.meta.last_row_id)
    except Exception:
        meta = to_py(getattr(result, "meta", {})) or {}
        return int(meta.get("last_row_id") or 0)
