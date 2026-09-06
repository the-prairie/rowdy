-- A fixed-input experiment. This file is real; saving changes its Git diff.
-- Version 2 exists in the inputs. Should this model retain it?
WITH eligible_events AS (
    SELECT namespace, event_id, event_name,
           event_time, received_at,
           user_id, anonymous_id, session_id,
           schema_version, request_id, submission_id
    FROM staged_events
    WHERE schema_version = 1
)
SELECT *
FROM eligible_events
ORDER BY event_time, event_id
