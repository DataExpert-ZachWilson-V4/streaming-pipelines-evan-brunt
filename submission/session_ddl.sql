CREATE TABLE IF NOT EXISTS {output_table} (
  session_id VARCHAR(100), -- unique id of the session
  user_id VARCHAR(100), -- user_id if the user was logged in, NULL otherwise
  session_start TIMESTAMP, -- when the session started
  session_end TIMESTAMP, -- when the session expired (after 5 minutes of inactivity)
  event_count BIGINT, -- number of events that occurred during the session
  session_date DATE, -- date of the session. this is used for partitioning
  city VARCHAR(100), -- city user is in
  state VARCHAR(100), -- state user is in
  country VARCHAR(100), -- country user is in
  operating_system VARCHAR(100), -- what operating system the user is using
  browser_family VARCHAR(100), -- what browser the user is using 
  logged_in BOOLEAN -- true if user is logged in, false otherwise
)
USING ICEBERG
PARTITIONED BY (session_date)
