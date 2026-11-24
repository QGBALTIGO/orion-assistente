"""Catalog fragments for existing send modules; no public endpoint or writes."""
import re


def extend_catalog(sql):
    """Extend the existing `WITH catalog AS (` query without changing grouping.

    Canonical audience data ranks before legacy names. A removed overlay blocks
    every older source for the same phone; group imports cannot revive it.
    """
    start = "WITH catalog AS ("
    if not sql.startswith(start):
        raise ValueError("Unexpected catalog contract")
    old = sql[len(start):]
    # Keep the old inner CTE exactly, including all tenant predicates, then
    # rename it and overlay new data in the outward catalog CTE.
    if not old.rstrip().endswith(")"):
        raise ValueError("Unexpected catalog terminator")
    old = old.rstrip()[:-1]
    return "WITH legacy_catalog AS (" + old + """), catalog AS (
        SELECT a.phone,a.name,-1 AS priority,a.id::text AS ordering,NULL::uuid AS group_id,
          jsonb_build_object('kind','audience','id',a.id,'phone',a.phone,'version',a.version) AS origin
        FROM subscription_audience_contacts a WHERE a.tenant_id=:t AND a.removed_at IS NULL AND a.phone IS NOT NULL
        UNION ALL
        SELECT l.* FROM legacy_catalog l WHERE NOT EXISTS(
          SELECT 1 FROM subscription_audience_contacts a WHERE a.tenant_id=:t AND a.phone=l.phone AND a.removed_at IS NOT NULL)
    ) """


def extend_source_valid(sql, alias="r"):
    if re.fullmatch(r"[a-z][a-z0-9_]*", alias) is None:
        raise ValueError("Internal SQL alias required")
    return f"""(NOT EXISTS(SELECT 1 FROM subscription_audience_contacts tomb
        WHERE tomb.tenant_id={alias}.tenant_id AND tomb.phone={alias}.phone AND tomb.removed_at IS NOT NULL)
        AND (({sql}) OR EXISTS(
          SELECT 1 FROM jsonb_to_recordset({alias}.origins) o(kind text,id uuid,phone text,version bigint)
          JOIN subscription_audience_contacts a ON a.tenant_id={alias}.tenant_id AND a.id=o.id
          WHERE o.kind='audience' AND o.phone={alias}.phone AND a.phone=o.phone
            AND a.version=o.version AND a.removed_at IS NULL)))"""
