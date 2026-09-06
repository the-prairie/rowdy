-- Preserve competing deliveries before choosing the latest representation.
WITH deliveries AS (
    SELECT *,
        ROW_NUMBER() OVER (
            PARTITION BY namespace, event_id
            ORDER BY received_at DESC, delivery_id DESC
        ) AS delivery_rank
    FROM raw_events
)
SELECT namespace, event_id, event_name, event_time, received_at,
       user_id, anonymous_id, session_id, schema_version,
       request_id, submission_id
FROM deliveries
WHERE delivery_rank = 1
