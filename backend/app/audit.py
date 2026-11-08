from psycopg.types.json import Jsonb


async def audit(conn, user: dict, action: str, target_type: str | None = None, target_id=None, **details) -> None:
    await conn.execute("INSERT INTO audit_log (org_id, actor_id, actor_name, action, target_type, target_id, details) "
                       "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (user["org_id"], user["id"], user["name"], action, target_type, str(target_id) if target_id else None,
                        Jsonb({k: v for k, v in details.items() if v is not None})))
