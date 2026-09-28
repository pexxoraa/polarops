from database.d1 import q_all


async def list_arctic_research_stations(env) -> list[dict]:
    return await q_all(
        env,
        """
        SELECT id,source_key,name,location,operating_country,established,
               summer_population,winter_population,latitude,longitude,
               coordinate_precision,coordinate_source,source_kind,source_url,
               verification_status,verification_source,verification_url,
               geographic_scope,verification_note,created_at,updated_at
        FROM arctic_research_stations
        ORDER BY
          CASE verification_status
            WHEN 'verified_current' THEN 0
            WHEN 'verified_component' THEN 1
            ELSE 2
          END,
          name COLLATE NOCASE
        """,
    )
