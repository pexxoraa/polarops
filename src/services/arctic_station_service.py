from repositories.arctic_stations import list_arctic_research_stations


USER_REFERENCE_URL = "https://en.wikipedia.org/wiki/List_of_research_stations_in_the_Arctic"
INTERACT_URL = "https://eu-interact.org/accessing-the-arctic/infrastructures/"
NYALESUND_URL = "https://nyalesundresearch.no/host-institutions-on-site/"


async def get_arctic_research_network(env) -> dict:
    rows = await list_arctic_research_stations(env)
    reference = [row for row in rows if row["source_kind"] == "user_reference"]
    additions = [row for row in rows if row["source_kind"] == "current_network_addition"]
    verified_reference = [
        row for row in reference
        if str(row.get("verification_status") or "").startswith("verified_")
    ]
    reference_only = [
        row for row in reference
        if not str(row.get("verification_status") or "").startswith("verified_")
    ]
    mapped = [
        row for row in rows
        if row.get("latitude") is not None and row.get("longitude") is not None
    ]
    arctic_circle = [row for row in mapped if row.get("geographic_scope") == "arctic_circle"]
    subarctic = [row for row in mapped if row.get("geographic_scope") == "subarctic"]
    northern_alpine = [row for row in mapped if row.get("geographic_scope") == "northern_or_alpine"]

    return {
        "items": rows,
        "reference_items": reference,
        "current_additions": additions,
        "summary": {
            "total": len(rows),
            "user_reference_rows": len(reference),
            "verified_reference_rows": len(verified_reference),
            "reference_unverified_rows": len(reference_only),
            "current_network_additions": len(additions),
            "mapped_rows": len(mapped),
            "arctic_circle_rows": len(arctic_circle),
            "subarctic_rows": len(subarctic),
            "northern_or_alpine_rows": len(northern_alpine),
        },
        "sources": [
            {
                "name": "User-supplied Arctic station reference",
                "url": USER_REFERENCE_URL,
                "role": "Reference dataset; population values are reference values, not live occupancy.",
            },
            {
                "name": "INTERACT current infrastructures",
                "url": INTERACT_URL,
                "role": "Current research-infrastructure verification and current-network additions.",
            },
            {
                "name": "Ny-Ålesund Research Station host institutions",
                "url": NYALESUND_URL,
                "role": "Current Ny-Ålesund host/facility verification.",
            },
        ],
        "warning": (
            "The supplied 58-row table is retained as a reference dataset. A row is only labelled "
            "verified current when a named current research-network or official operator source supports it. "
            "Population fields are historical/reference capacity values, not live occupancy."
        ),
    }
