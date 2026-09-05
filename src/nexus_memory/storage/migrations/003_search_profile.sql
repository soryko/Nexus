CREATE TABLE search_profile (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  profile TEXT NOT NULL
);
INSERT INTO search_profile(id, profile) VALUES (1, 'exact')
