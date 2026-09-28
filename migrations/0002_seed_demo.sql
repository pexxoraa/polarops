INSERT OR IGNORE INTO organizations(id,name,country_code,operator_type,created_at)
VALUES(1,'PolarOps Demo Programme','XX','National Antarctic Programme','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO users(id,organization_id,email,name,role,password_hash,active,created_at) VALUES
(1,1,'commander@polarops.local','Expedition Commander','commander','pbkdf2_sha256$100000$cG9sYXJvcHMtY29tbWFuZGVy$9_2IqXpM2XAhYSX27LmJhX6jnxhkr7YgRhuG6R5Ap9k=',1,'2026-09-28T00:00:00+00:00'),
(2,1,'logistics@polarops.local','Logistics Officer','logistics','pbkdf2_sha256$100000$cG9sYXJvcHMtbG9naXN0aWNz$Zox1Y31lgtZ8rcC8tAN4nyVwX9EiqAXk3Q6k3U5sKZ0=',1,'2026-09-28T00:00:00+00:00'),
(3,1,'field@polarops.local','Field Team Leader','field','pbkdf2_sha256$100000$cG9sYXJvcHMtZmllbGQ=$uBGb5BkKjiPFQTb3i-HzuDmilPeLsD6FrGc2nCBlyos=',1,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO expeditions(id,organization_id,name,region,start_date,end_date,status,description,created_at)
VALUES(1,1,'Expedition Alpha','Antarctic Region','2026-12-12','2027-01-15','Active','Integrated polar research and logistics mission.','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO locations(id,expedition_id,name,type,latitude,longitude,source) VALUES
(1,1,'Base Station','Station',-75.250,123.100,'demo'),
(2,1,'Camp Alpha','Camp',-75.480,124.120,'demo'),
(3,1,'Camp Beta','Camp',-75.710,125.020,'demo'),
(4,1,'Traverse Route','Route',-75.560,124.520,'demo'),
(5,1,'Airstrip','Transport',-75.310,123.420,'demo');

INSERT OR IGNORE INTO personnel(id,expedition_id,name,role,team,location_id,status,last_checkin,contact,clearance_status,source,is_synthetic,created_at) VALUES
(1,1,'Aarav Shah','Expedition Lead','Alpha',1,'Safe','2026-09-28T00:10:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00'),
(2,1,'Riya Mehta','Medical Officer','Alpha',2,'Safe','2026-09-28T00:12:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00'),
(3,1,'Vikram Rao','Field Engineer','Bravo',4,'Moving','2026-09-28T00:15:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00'),
(4,1,'Meera Joshi','Scientist','Bravo',3,'Check-in due','2026-09-27T23:20:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00'),
(5,1,'Daniel Kim','Communications','Alpha',1,'Safe','2026-09-28T00:16:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00'),
(6,1,'Priya Nair','Geologist','Charlie',2,'Safe','2026-09-28T00:11:00+00:00','','Cleared','demo',1,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO cargo(id,expedition_id,code,name,priority,origin_location_id,destination_location_id,current_location_id,status,quantity,unit,assigned_to,created_at) VALUES
(1,1,'CRG-102','Emergency Medical Kit','Critical',1,2,1,'In Transit',1,'kit','','2026-09-28T00:00:00+00:00'),
(2,1,'CRG-104','Food Supplies','High',1,2,2,'Delivered',12,'crates','','2026-09-28T00:00:00+00:00'),
(3,1,'CRG-107','Scientific Equipment','High',1,3,3,'In Transit',3,'cases','','2026-09-28T00:00:00+00:00'),
(4,1,'CRG-119','Fuel Drums','High',1,2,2,'Delivered',8,'drums','','2026-09-28T00:00:00+00:00'),
(5,1,'CRG-131','Shelter Equipment','Medium',1,3,4,'In Transit',4,'cases','','2026-09-28T00:00:00+00:00'),
(6,1,'CRG-145','Spare Parts','Medium',1,2,2,'Delivered',2,'cases','','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO inventory_items(id,expedition_id,sku,name,location_id,quantity,min_quantity,unit,created_at) VALUES
(1,1,'INV-FUEL','Fuel',1,850,500,'L','2026-09-28T00:00:00+00:00'),
(2,1,'INV-FOOD','Food Packs',2,120,80,'packs','2026-09-28T00:00:00+00:00'),
(3,1,'INV-MED','Medical Kits',2,2,5,'kits','2026-09-28T00:00:00+00:00'),
(4,1,'INV-OXY','Oxygen Cylinders',1,4,6,'cylinders','2026-09-28T00:00:00+00:00'),
(5,1,'INV-TENT','Tents',3,10,8,'units','2026-09-28T00:00:00+00:00'),
(6,1,'INV-SAT','Satellite Units',1,6,4,'units','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO vehicles(id,expedition_id,code,name,type,location_id,status,fuel_percent,range_km,created_at) VALUES
(1,1,'V01','Snowcat 01','Ground',1,'Operational',88,190,'2026-09-28T00:00:00+00:00'),
(2,1,'V02','Tracked Carrier','Ground',3,'Operational',64,140,'2026-09-28T00:00:00+00:00'),
(3,1,'V03','Rescue Rover','Ground',4,'Operational',72,180,'2026-09-28T00:00:00+00:00'),
(4,1,'V04','Utility Rover','Ground',2,'Maintenance',23,65,'2026-09-28T00:00:00+00:00'),
(5,1,'A02','Twin Otter A02','Aircraft',5,'Operational',76,900,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO assets(id,expedition_id,code,name,category,location_id,status,serial_number,assigned_to_personnel_id,created_at) VALUES
(1,1,'AST-001','Portable Weather Station','Scientific',2,'Deployed','WX-8801',NULL,'2026-09-28T00:00:00+00:00'),
(2,1,'AST-002','Satellite Terminal','Communications',1,'Available','SAT-2205',5,'2026-09-28T00:00:00+00:00'),
(3,1,'AST-003','Ice Radar','Scientific',3,'Deployed','RAD-1470',4,'2026-09-28T00:00:00+00:00'),
(4,1,'AST-004','Emergency Generator','Power',2,'Available','GEN-0488',NULL,'2026-09-28T00:00:00+00:00'),
(5,1,'AST-005','Drone Survey Kit','Aerial',1,'Available','DRN-7741',NULL,'2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO activity(expedition_id,category,message,created_at) VALUES
(1,'personnel','Meera Joshi check-in is overdue','2026-09-28T00:00:00+00:00'),
(1,'inventory','Medical Kits at Camp Alpha below safety stock','2026-09-28T00:00:00+00:00'),
(1,'inventory','Oxygen Cylinders below safety stock','2026-09-28T00:00:00+00:00'),
(1,'vehicle','Vehicle V04 fuel is low and status is Maintenance','2026-09-28T00:00:00+00:00');

INSERT OR IGNORE INTO data_sources(name,source_url,last_status,details) VALUES
('COMNAP Facilities','https://www.comnap.aq/s/Facilities_Nov2024.csv','Never synced','Official public facilities reference; verify source terms before production use.'),
('Worker Feed','','Not configured','Authorized operator-controlled operations feed for workers, camps/bases and positions.');
