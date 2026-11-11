-- Members holding a seat today, by chamber and party.
WITH cur AS (
  SELECT t.resource->'chamber'->'coding'->0->>'code' AS chamber,
         t.resource->'person'->>'reference' AS p
  FROM office_term t
  WHERE (t.resource->'period'->>'start')::date <= current_date
    AND (t.resource->'period'->>'end')::date   >= current_date
), pa AS (
  SELECT a.resource->'person'->>'reference' AS p, o.resource->>'name' AS party
  FROM party_affiliation a
  JOIN organization o ON 'Organization/' || o.id = a.resource->'party'->>'reference'
  WHERE (a.resource->'period'->>'start')::date <= current_date
    AND (a.resource->'period'->>'end')::date   >= current_date
)
SELECT chamber, party, count(*) FROM cur LEFT JOIN pa USING (p) GROUP BY 1, 2 ORDER BY 1, 2;
