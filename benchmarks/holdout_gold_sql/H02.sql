-- H02: customer-state top 5 by delivered order count, Q1 2018.
SELECT c.customer_state,
       COUNT(DISTINCT o.order_id) AS delivered_orders
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-01-01'
  AND o.order_purchase_timestamp < '2018-04-01'
GROUP BY c.customer_state
ORDER BY delivered_orders DESC, c.customer_state ASC
LIMIT 5;
