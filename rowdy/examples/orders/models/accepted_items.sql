-- A second configuration-only profile: different keys, schema, and grain.
SELECT tenant, line_id, order_id, event_time, received_at,
       sku, quantity, unit_price,
       quantity * unit_price AS amount
FROM order_lines
WHERE quantity > 0
ORDER BY order_id, line_id
