-- H08: distinct buyer keys and order keys, group by customer state.
SELECT c.customer_state,
       COUNT(DISTINCT c.customer_unique_id) AS unique_buyers,
       COUNT(DISTINCT o.order_id) AS delivered_orders
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-07-01'
GROUP BY c.customer_state
HAVING COUNT(DISTINCT o.order_id) >= 300
ORDER BY delivered_orders DESC, c.customer_state ASC
LIMIT 10;
