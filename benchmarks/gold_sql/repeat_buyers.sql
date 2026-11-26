-- Repeat buyers based on stable customer_unique_id, not one-record-per-order customer_id.
WITH buyer_orders AS (
    SELECT c.customer_unique_id,
           COUNT(DISTINCT o.order_id) AS order_count
    FROM customers c
    JOIN orders o ON o.customer_id = c.customer_id
    WHERE c.customer_unique_id IS NOT NULL
      AND TRIM(c.customer_unique_id) <> ''
    GROUP BY c.customer_unique_id
)
SELECT COUNT(*) AS buyers_with_at_least_two_orders
FROM buyer_orders
WHERE order_count >= 2;
