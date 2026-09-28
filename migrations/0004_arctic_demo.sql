-- Synthetic Arctic demonstration mission used to separate north/south polar operations in the UI.
-- Geographic points are demo/reference locations; personnel/logistics records are synthetic.
INSERT OR IGNORE INTO expeditions(id,organization_id,name,region,start_date,end_date,status,description,created_at)
VALUES(2,1,'Expedition Borealis','Arctic Region','2027-03-01','2027-04-15','Active','Synthetic Arctic operations demonstration around Svalbard.','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO locations(id,expedition_id,name,type,latitude,longitude,source) VALUES
(21,2,'Arctic Operations Hub','Station',78.2232,15.6469,'demo-arctic'),
(22,2,'Ny-Alesund Reference Site','Station',78.9239,11.9225,'demo-arctic'),
(23,2,'Field Camp Aurora','Camp',79.2500,14.3000,'demo-arctic'),
(24,2,'Traverse North','Route',78.8000,18.2000,'demo-arctic'),
(25,2,'Air Support Point','Transport',78.2461,15.4656,'demo-arctic');

INSERT OR IGNORE INTO personnel(id,expedition_id,name,role,team,location_id,status,last_checkin,contact,clearance_status,source,is_synthetic,created_at) VALUES
(21,2,'Elena Nord','Expedition Lead','Aurora',21,'Safe','2026-09-28T05:05:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00'),
(22,2,'Mika Berg','Medical Officer','Aurora',23,'Safe','2026-09-28T05:08:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00'),
(23,2,'Sofia Lind','Field Engineer','Borealis',24,'Moving','2026-09-28T05:10:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00'),
(24,2,'Jonas Erik','Glaciologist','Borealis',22,'Safe','2026-09-28T05:04:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00'),
(25,2,'Nora Vale','Communications','Aurora',21,'Safe','2026-09-28T05:06:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00'),
(26,2,'Liam Frost','Logistics Technician','Charlie',23,'Check-in due','2026-09-28T04:00:00+00:00','','Cleared','demo-arctic',1,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO cargo(id,expedition_id,code,name,priority,origin_location_id,destination_location_id,current_location_id,status,quantity,unit,assigned_to,created_at) VALUES
(21,2,'ARC-201','Cold Weather Medical Kit','Critical',21,23,21,'In Transit',1,'kit','','2026-09-28T00:00:00+00:00'),
(22,2,'ARC-204','Food Resupply','High',21,23,23,'Delivered',10,'crates','','2026-09-28T00:00:00+00:00'),
(23,2,'ARC-207','Ice Survey Equipment','High',21,22,22,'Delivered',2,'cases','','2026-09-28T00:00:00+00:00'),
(24,2,'ARC-219','Fuel Drums','High',21,23,21,'In Transit',6,'drums','','2026-09-28T00:00:00+00:00'),
(25,2,'ARC-231','Shelter Equipment','Medium',21,23,24,'In Transit',3,'cases','','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO inventory_items(id,expedition_id,sku,name,location_id,quantity,min_quantity,unit,created_at) VALUES
(21,2,'ARC-FUEL','Fuel',21,720,450,'L','2026-09-28T00:00:00+00:00'),
(22,2,'ARC-FOOD','Food Packs',23,105,70,'packs','2026-09-28T00:00:00+00:00'),
(23,2,'ARC-MED','Medical Kits',23,3,5,'kits','2026-09-28T00:00:00+00:00'),
(24,2,'ARC-HEAT','Emergency Heaters',21,5,4,'units','2026-09-28T00:00:00+00:00'),
(25,2,'ARC-SAT','Satellite Units',21,4,3,'units','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO vehicles(id,expedition_id,code,name,type,location_id,status,fuel_percent,range_km,created_at) VALUES
(21,2,'N01','Arctic Snowcat','Ground',21,'Operational',84,185,'2026-09-28T00:00:00+00:00'),
(22,2,'N02','Tracked Carrier North','Ground',23,'Operational',69,145,'2026-09-28T00:00:00+00:00'),
(23,2,'N03','Rescue Rover North','Ground',24,'Operational',76,175,'2026-09-28T00:00:00+00:00'),
(24,2,'N04','Utility Rover North','Ground',22,'Maintenance',28,70,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO assets(id,expedition_id,code,name,category,location_id,status,serial_number,assigned_to_personnel_id,created_at) VALUES
(21,2,'ARC-A01','Portable Weather Station','Scientific',23,'Deployed','AR-WX-101',NULL,'2026-09-28T00:00:00+00:00'),
(22,2,'ARC-A02','Satellite Terminal','Communications',21,'Available','AR-SAT-22',25,'2026-09-28T00:00:00+00:00'),
(23,2,'ARC-A03','Ice Radar','Scientific',22,'Deployed','AR-RAD-47',24,'2026-09-28T00:00:00+00:00'),
(24,2,'ARC-A04','Emergency Generator','Power',23,'Available','AR-GEN-18',NULL,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO activity(expedition_id,category,message,created_at) VALUES
(2,'personnel','Liam Frost check-in is overdue','2026-09-28T05:00:00+00:00'),
(2,'inventory','Medical Kits at Field Camp Aurora below safety stock','2026-09-28T05:01:00+00:00'),
(2,'vehicle','Vehicle N04 fuel is low and status is Maintenance','2026-09-28T05:02:00+00:00');
