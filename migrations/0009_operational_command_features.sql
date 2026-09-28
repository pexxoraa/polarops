-- PolarOps operational command feature set: timeline, routes/geofences, alerts,
-- science records, communications, readiness, incident command, handover and audit.

CREATE TABLE IF NOT EXISTS mission_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'Operations',
  status TEXT NOT NULL DEFAULT 'Planned',
  priority TEXT NOT NULL DEFAULT 'Normal',
  start_at TEXT,
  due_at TEXT,
  assigned_to TEXT,
  location_id INTEGER,
  notes TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id),
  FOREIGN KEY(location_id) REFERENCES locations(id)
);
CREATE INDEX IF NOT EXISTS idx_mission_tasks_exp_due ON mission_tasks(expedition_id,due_at,status);

CREATE TABLE IF NOT EXISTS planned_routes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  start_lat REAL NOT NULL,
  start_lon REAL NOT NULL,
  end_lat REAL NOT NULL,
  end_lon REAL NOT NULL,
  waypoints_json TEXT NOT NULL DEFAULT '[]',
  distance_km REAL,
  eta_minutes INTEGER,
  fuel_liters REAL,
  vehicle_id INTEGER,
  personnel_id INTEGER,
  status TEXT NOT NULL DEFAULT 'Planned',
  risk_summary TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id),
  FOREIGN KEY(vehicle_id) REFERENCES vehicles(id),
  FOREIGN KEY(personnel_id) REFERENCES personnel(id)
);
CREATE INDEX IF NOT EXISTS idx_routes_exp_status ON planned_routes(expedition_id,status);

CREATE TABLE IF NOT EXISTS geofences (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'Safe zone',
  center_lat REAL NOT NULL,
  center_lon REAL NOT NULL,
  radius_m REAL NOT NULL DEFAULT 1000,
  severity TEXT NOT NULL DEFAULT 'Warning',
  active INTEGER NOT NULL DEFAULT 1,
  notes TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id)
);
CREATE INDEX IF NOT EXISTS idx_geofences_exp_active ON geofences(expedition_id,active);

CREATE TABLE IF NOT EXISTS ops_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  severity TEXT NOT NULL DEFAULT 'Advisory',
  source TEXT NOT NULL DEFAULT 'Manual',
  title TEXT NOT NULL,
  detail TEXT,
  status TEXT NOT NULL DEFAULT 'Open',
  assigned_to TEXT,
  entity_type TEXT,
  entity_id INTEGER,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  acknowledged_at TEXT,
  resolved_at TEXT,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id)
);
CREATE INDEX IF NOT EXISTS idx_ops_alerts_exp_status ON ops_alerts(expedition_id,status,severity);

CREATE TABLE IF NOT EXISTS science_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  project TEXT NOT NULL,
  sample_id TEXT,
  record_type TEXT NOT NULL DEFAULT 'Observation',
  title TEXT NOT NULL,
  latitude REAL,
  longitude REAL,
  collected_at TEXT,
  researcher_id INTEGER,
  storage_location TEXT,
  notes TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id),
  FOREIGN KEY(researcher_id) REFERENCES personnel(id)
);
CREATE INDEX IF NOT EXISTS idx_science_exp_collected ON science_records(expedition_id,collected_at);

CREATE TABLE IF NOT EXISTS comms_checkins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  channel TEXT NOT NULL DEFAULT 'Satellite',
  expected_at TEXT NOT NULL,
  actual_at TEXT,
  status TEXT NOT NULL DEFAULT 'Expected',
  notes TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id)
);
CREATE INDEX IF NOT EXISTS idx_comms_exp_expected ON comms_checkins(expedition_id,expected_at,status);

CREATE TABLE IF NOT EXISTS readiness_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  category TEXT NOT NULL,
  label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending',
  owner TEXT,
  due_at TEXT,
  notes TEXT,
  updated_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id)
);
CREATE INDEX IF NOT EXISTS idx_readiness_exp_status ON readiness_items(expedition_id,status,category);

CREATE TABLE IF NOT EXISTS incident_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id INTEGER NOT NULL,
  task TEXT NOT NULL,
  owner TEXT,
  status TEXT NOT NULL DEFAULT 'Open',
  due_at TEXT,
  notes TEXT,
  created_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(incident_id) REFERENCES incidents(id)
);
CREATE INDEX IF NOT EXISTS idx_incident_actions_incident ON incident_actions(incident_id,status);

CREATE TABLE IF NOT EXISTS shift_handovers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  expedition_id INTEGER NOT NULL,
  shift_name TEXT NOT NULL,
  author_user_id INTEGER,
  summary TEXT NOT NULL,
  unresolved_alerts TEXT,
  deployed_teams TEXT,
  vehicle_issues TEXT,
  weather_notes TEXT,
  cargo_priorities TEXT,
  science_ops TEXT,
  next_tasks TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(expedition_id) REFERENCES expeditions(id)
);
CREATE INDEX IF NOT EXISTS idx_handovers_exp_created ON shift_handovers(expedition_id,created_at);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  organization_id INTEGER NOT NULL,
  expedition_id INTEGER,
  user_id INTEGER,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id INTEGER,
  detail_json TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_org_exp_created ON audit_events(organization_id,expedition_id,created_at);
