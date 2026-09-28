-- Default readiness checklist for existing expeditions. These are operational checklist
-- prompts, not assertions that a mission is safe or ready.
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Personnel','Personnel roster and field-team assignments confirmed','Pending','Expedition Lead','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z'
FROM expeditions e WHERE NOT EXISTS (SELECT 1 FROM readiness_items r WHERE r.expedition_id=e.id);

INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Medical','Medical kits and evacuation plan checked','Pending','Medical Officer','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Communications','Primary and backup communications tested','Pending','Communications','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Vehicles','Vehicle serviceability and recovery equipment checked','Pending','Logistics','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Fuel','Fuel reserve verified against planned traverse','Pending','Logistics','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Food','Food and field rations checked against team duration','Pending','Logistics','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Emergency','Emergency shelters, beacons and recovery equipment checked','Pending','Expedition Lead','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Permits','Required permits and operating documents reviewed','Pending','Expedition Lead','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Weather','Forecast and operating thresholds reviewed','Pending','Expedition Lead','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
INSERT INTO readiness_items(expedition_id,category,label,status,owner,created_at,updated_at)
SELECT e.id,'Route','Route, alternates and check-in points reviewed','Pending','Field Team Lead','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z' FROM expeditions e;
