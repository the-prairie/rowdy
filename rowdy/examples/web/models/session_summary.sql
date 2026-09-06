-- A session is not a person: preserve the namespace and session key.
SELECT namespace, session_id,
       CASE WHEN COUNT(DISTINCT user_id) = 1 THEN MAX(user_id) END AS user_id,
       MIN(event_time) AS event_time,
       MAX(received_at) AS received_at,
       COUNT(*) AS event_count,
       SUM(CASE WHEN event_name = 'Form submitted' THEN 1 ELSE 0 END) AS completions
FROM unified_events
GROUP BY namespace, session_id
ORDER BY event_time
