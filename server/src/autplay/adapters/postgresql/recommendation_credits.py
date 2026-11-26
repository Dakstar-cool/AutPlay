"""Resolve ordered artist IDs and exclusive co-billed duos for one captured universe."""

from collections.abc import Collection

from sqlalchemy import text
from sqlalchemy.orm import Session


def credit_acts(session: Session, credit_ids: Collection[str]) -> dict[str, tuple[str, ...]]:
    if not credit_ids:
        return {}
    rows = session.execute(text(_CREDITS), {"credits": list(credit_ids)})
    return {str(credit): tuple(dict.fromkeys(acts)) for credit, acts in rows}


# Release groups count distinct projects, so editions or tracks of a single album
# cannot turn a one-off collaboration into a duo. Check for independent billing
# across the catalog, including recordings outside the user's bounded snapshot.
_CREDITS = r"""
WITH wanted AS (
    SELECT n.artist_credit_id, array_agg(n.artist_id ORDER BY n.position) AS members
    FROM catalog.artist_credit_name n
    JOIN catalog.artist a USING (artist_id)
    JOIN catalog.artist_credit c USING (artist_credit_id)
    WHERE n.artist_credit_id=ANY(CAST(:credits AS uuid[])) AND c.deleted_at IS NULL
    GROUP BY n.artist_credit_id
    HAVING bool_and(a.identity_status='ACTIVE' AND a.deleted_at IS NULL)
       AND count(*) <= 100
), collaborators AS (
    SELECT DISTINCT unnest(members) AS artist_id FROM wanted WHERE cardinality(members)>1
), projects AS (
    SELECT artist_credit_id, count(*) AS releases FROM catalog.release_group
    WHERE deleted_at IS NULL AND artist_credit_id IN (
        SELECT m.artist_credit_id FROM collaborators JOIN catalog.artist_credit_name m
        USING (artist_id)
    )
    GROUP BY artist_credit_id
), project_credits AS (
    SELECT n.artist_credit_id, array_agg(n.artist_id ORDER BY n.artist_id) AS pair,
           p.releases
    FROM projects p JOIN catalog.artist_credit_name n USING (artist_credit_id)
    JOIN catalog.artist_credit c USING (artist_credit_id)
    WHERE c.deleted_at IS NULL GROUP BY n.artist_credit_id, p.releases
    HAVING count(*)=2 AND count(DISTINCT n.artist_id)=2
       AND bool_and(n.role='PRIMARY' AND n.join_phrase !~* '\m(feat|ft|featuring|with)\M')
), pairs AS (
    SELECT pair FROM project_credits GROUP BY pair HAVING sum(releases) >= 2
), duos AS (
    SELECT pair FROM pairs p WHERE NOT EXISTS (
        SELECT FROM catalog.artist_credit_name m
        JOIN catalog.artist_credit c USING (artist_credit_id)
        WHERE m.artist_id=ANY(p.pair) AND c.deleted_at IS NULL AND NOT EXISTS (
            SELECT FROM catalog.artist_credit_name partner
            WHERE partner.artist_credit_id=m.artist_credit_id AND partner.artist_id=ANY(p.pair)
              AND partner.artist_id<>m.artist_id AND partner.role=m.role
              AND NOT EXISTS (
                  SELECT FROM catalog.artist_credit_name boundary
                  WHERE boundary.artist_credit_id=m.artist_credit_id
                    AND boundary.position >= least(m.position, partner.position)
                    AND boundary.position < greatest(m.position, partner.position)
                    AND boundary.join_phrase ~* '\m(feat|ft|featuring|with)\M'
              )
        ) AND (
            EXISTS (SELECT FROM catalog.recording r WHERE r.artist_credit_id=m.artist_credit_id
                    AND r.deleted_at IS NULL)
            OR EXISTS (SELECT FROM projects g WHERE g.artist_credit_id=m.artist_credit_id)
        )
    )
), duo_members AS (
    SELECT unnest(pair) AS artist_id, 'duo:' || array_to_string(pair, ':') AS act FROM duos
)
SELECT w.artist_credit_id,
       array_agg(coalesce(d.act, m.artist_id::text) ORDER BY m.ord) AS acts
FROM wanted w CROSS JOIN LATERAL unnest(w.members) WITH ORDINALITY m(artist_id, ord)
LEFT JOIN duo_members d ON d.artist_id=m.artist_id
GROUP BY w.artist_credit_id
"""
