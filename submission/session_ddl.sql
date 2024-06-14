-- Followed the homework prompt to create these columns

CREATE TABLE IF NOT EXISTS {output_table} (
  session_id VARCHAR(100),
  user_id VARCHAR(100),
  session_start TIMESTAMP, 
  session_end TIMESTAMP,
  event_count INTEGER, 
  session_date DATE,
  city VARCHAR(100),
  state VARCHAR(100),
  country VARCHAR(100),
  operating_system VARCHAR(100),
  browser VARCHAR(100),
  logged_in BOOLEAN
)
USING ICEBERG
