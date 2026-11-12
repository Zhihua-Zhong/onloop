-- A database at store schema version 1 (made at commit 1e3790e), dumped as SQL.
-- Live: host/alice (granted at once), db/dave (granted by the human). Waiting: host/bob, db/erin.
-- Deferred: two deliveries of preflight wait on slot:host. Gate: onloop holds it.
BEGIN TRANSACTION;
CREATE TABLE cursors (
  name TEXT PRIMARY KEY, value TEXT NOT NULL, etag TEXT, updated_at TEXT NOT NULL CHECK (updated_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);
CREATE TABLE deliveries (
  event_id TEXT NOT NULL REFERENCES events(id), handler TEXT NOT NULL,
  status   TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_at TEXT NOT NULL CHECK (next_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  claimed_until TEXT CHECK ((claimed_until IS NULL OR claimed_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
  last_error TEXT,
  PRIMARY KEY (event_id, handler)
);
INSERT INTO "deliveries" VALUES('e5dbc0b9814f495c95e5df634e4ef33fe9bb76b96b8b2c9c5a7268535e082506','preflight','pending',1,'2026-10-12T00:05:00.000Z',NULL,'deferred:slot:host: waiting for host');
INSERT INTO "deliveries" VALUES('45fedfa5b2f3dbf3a0c606cbe38eb22b06c3ac6825089c66e64db3d5849476dc','preflight','pending',1,'2026-10-12T00:05:00.000Z',NULL,'deferred:slot:host: waiting for host');
INSERT INTO "deliveries" VALUES('6d5f54617c972911e6c7300fe315b487699a1cc88757b0d9342516d84526e323','dashboard','done',1,'2026-10-12T00:00:00.000Z',NULL,NULL);
CREATE TABLE effects (
  key TEXT PRIMARY KEY, event_id TEXT NOT NULL, handler TEXT NOT NULL, kind TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'done' CHECK (state IN ('started','done')),
  detail TEXT NOT NULL CHECK (json_valid(detail)), at TEXT NOT NULL CHECK (at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  repo        TEXT NOT NULL,
  kind        TEXT NOT NULL,
  subject     TEXT NOT NULL,
  actor       TEXT,
  at TEXT NOT NULL CHECK (at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  observed_at TEXT NOT NULL CHECK (observed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
INSERT INTO "events" VALUES(1,'6d5f54617c972911e6c7300fe315b487699a1cc88757b0d9342516d84526e323','o/r','slot.requested','slot:host','alice','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"holder":"alice","reason":"run tests","ref":"pr#1","request":1,"role":"builder"}');
INSERT INTO "events" VALUES(2,'9e1aa866a0e580532b245fa870e5cb2cefcc0937278cd7e11bf3c9ef9fd917c7','o/r','slot.granted','slot:host','onloop:auto','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"expires_at":"2026-10-12T00:30:00.000Z","grant":1,"granted_by":"onloop:auto","holder":"alice","ref":"pr#1","request":1,"role":"builder","waited_since":"2026-10-12T00:00:00.000Z"}');
INSERT INTO "events" VALUES(3,'0bcb27a7c00e190df7782a2167f3a8528b369c8d49aea19da48e37ded73d58d5','o/r','slot.requested','slot:host','bob','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"holder":"bob","reason":"run tests","ref":"pr#2","request":2,"role":"builder"}');
INSERT INTO "events" VALUES(4,'6e24f03267e47eaf9f7d5a6365f83998ba7399a1f6d226027334bfa230e2c1c5','o/r','slot.requested','slot:gate','onloop','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"holder":"onloop","reason":"merge","ref":"pr#3","request":3,"role":"onloop"}');
INSERT INTO "events" VALUES(5,'f955e669308812a3ff93e6260c53c9b221bbbbbb276af81173541a53aa849e10','o/r','slot.granted','slot:gate','onloop:auto','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"expires_at":"2026-10-12T00:20:00.000Z","grant":2,"granted_by":"onloop:auto","holder":"onloop","ref":"pr#3","request":3,"role":"onloop","waited_since":"2026-10-12T00:00:00.000Z"}');
INSERT INTO "events" VALUES(6,'7af85e3ecd1aceb59e68d90579556b5d345e71b38a3777c52f8d43dc4ca67eaa','o/r','slot.requested','slot:db','dave','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"holder":"dave","reason":"migrate","ref":"issue#9","request":4,"role":"builder"}');
INSERT INTO "events" VALUES(7,'ae4ec2c47e0b23f29368e9e0e1e3377f7ddf20cdde7e7b97b599edf239a4bb65','o/r','slot.requested','slot:db','erin','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"holder":"erin","reason":"migrate","ref":"issue#10","request":5,"role":"builder"}');
INSERT INTO "events" VALUES(8,'bed5770a6e96423d799fa83ac5b919e6f2c01d7ca940f56db5ba33edc644e0d3','o/r','slot.granted','slot:db','owner-a','2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"expires_at":"2026-10-12T01:30:00.000Z","grant":3,"granted_by":"owner-a","holder":"dave","ref":"issue#9","request":4,"role":"builder","waited_since":"2026-10-12T00:00:00.000Z"}');
INSERT INTO "events" VALUES(9,'e5dbc0b9814f495c95e5df634e4ef33fe9bb76b96b8b2c9c5a7268535e082506','o/r','pr.opened','pr#1',NULL,'2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"base":"main","draft":false,"head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","title":"x"}');
INSERT INTO "events" VALUES(10,'45fedfa5b2f3dbf3a0c606cbe38eb22b06c3ac6825089c66e64db3d5849476dc','o/r','pr.opened','pr#2',NULL,'2026-10-12T00:00:00.000Z','2026-10-12T00:00:00.000Z','{"base":"main","draft":false,"head":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","title":"y"}');
CREATE TABLE grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  request_id INTEGER NOT NULL REFERENCES slot_requests(id),
  granted_at TEXT NOT NULL CHECK (granted_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'), granted_by TEXT NOT NULL,
  expires_at TEXT NOT NULL CHECK (expires_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  renewed_at TEXT CHECK ((renewed_at IS NULL OR renewed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
  released_at TEXT CHECK ((released_at IS NULL OR released_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
  release_reason TEXT,
  conditions TEXT NOT NULL CHECK (json_valid(conditions)),
  commands   TEXT NOT NULL CHECK (json_valid(commands))
);
INSERT INTO "grants" VALUES(1,'host','alice','builder',1,'2026-10-12T00:00:00.000Z','onloop:auto','2026-10-12T00:30:00.000Z',NULL,NULL,NULL,'[]','[]');
INSERT INTO "grants" VALUES(2,'gate','onloop','onloop',3,'2026-10-12T00:00:00.000Z','onloop:auto','2026-10-12T00:20:00.000Z',NULL,NULL,NULL,'[]','[]');
INSERT INTO "grants" VALUES(3,'db','dave','builder',4,'2026-10-12T00:00:00.000Z','owner-a','2026-10-12T01:30:00.000Z',NULL,NULL,NULL,'[]','[]');
CREATE TABLE pr_snapshots (
  number INTEGER PRIMARY KEY, head TEXT NOT NULL, state TEXT NOT NULL, draft INTEGER NOT NULL,
  ready_count INTEGER NOT NULL DEFAULT 0, drafted_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT, reviews_hash TEXT NOT NULL, checks_hash TEXT NOT NULL, patch_id TEXT,
  facts TEXT NOT NULL CHECK (json_valid(facts)),
  head_seen_at TEXT NOT NULL CHECK (head_seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  seen_at TEXT NOT NULL CHECK (seen_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
);
CREATE TABLE runner (
  id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, host TEXT,
  started_at TEXT CHECK ((started_at IS NULL OR started_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')),
  heartbeat_at TEXT CHECK ((heartbeat_at IS NULL OR heartbeat_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'))
);
CREATE TABLE slot_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  reason TEXT NOT NULL, ref TEXT NOT NULL,
  paths TEXT NOT NULL CHECK (json_valid(paths)),
  requested_at TEXT NOT NULL CHECK (requested_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  wait_until TEXT NOT NULL CHECK (wait_until GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
  status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting','granted','cancelled','expired'))
);
INSERT INTO "slot_requests" VALUES(1,'host','alice','builder','run tests','pr#1','[]','2026-10-12T00:00:00.000Z','2026-10-12T01:00:00.000Z','granted');
INSERT INTO "slot_requests" VALUES(2,'host','bob','builder','run tests','pr#2','[]','2026-10-12T00:00:00.000Z','2026-10-12T01:00:00.000Z','waiting');
INSERT INTO "slot_requests" VALUES(3,'gate','onloop','onloop','merge','pr#3','[]','2026-10-12T00:00:00.000Z','2026-10-13T00:00:00.000Z','granted');
INSERT INTO "slot_requests" VALUES(4,'db','dave','builder','migrate','issue#9','[]','2026-10-12T00:00:00.000Z','2026-10-12T04:00:00.000Z','granted');
INSERT INTO "slot_requests" VALUES(5,'db','erin','builder','migrate','issue#10','[]','2026-10-12T00:00:00.000Z','2026-10-12T04:00:00.000Z','waiting');
CREATE INDEX events_kind    ON events(kind, seq);
CREATE INDEX events_subject ON events(subject, seq);
CREATE INDEX deliveries_due ON deliveries(status, next_at);
CREATE INDEX effects_event ON effects(event_id);
CREATE UNIQUE INDEX one_waiting_request ON slot_requests(slot, holder, ref) WHERE status = 'waiting';
CREATE UNIQUE INDEX one_live_grant ON grants(slot) WHERE released_at IS NULL;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('slot_requests',5);
INSERT INTO "sqlite_sequence" VALUES('events',10);
INSERT INTO "sqlite_sequence" VALUES('grants',3);
COMMIT;
