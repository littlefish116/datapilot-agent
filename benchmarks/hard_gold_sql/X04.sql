-- X04: repeat buyers scoped to Q1 2018 and customer_unique_id.
WITH per_buyer AS (
  SELECT c.customer_unique_id AS buyer_key, COUNT(DISTINCT o.order_id) AS order_count
  FROM orders o JOIN customers c ON c.customer_id = o.customer_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= '2018-01-01'
    AND o.order_purchase_timestamp < '2018-04-01'
  GROUP BY c.customer_unique_id
)
SELECT COUNT(*) AS repeat_buyers,
       COALESCE(SUM(order_count), 0) AS repeat_buyer_orders
FROM per_buyer WHERE order_count >= 2;
