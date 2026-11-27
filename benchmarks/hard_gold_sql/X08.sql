-- X08: Q2 per-month order-grain averages; both detail tables preaggregated.
WITH items AS (
  SELECT order_id, SUM(price) AS item_amount FROM order_items GROUP BY order_id
), payments AS (
  SELECT order_id, SUM(payment_value) AS payment_amount FROM order_payments GROUP BY order_id
)
SELECT STRFTIME('%Y-%m', o.order_purchase_timestamp) AS purchase_month,
       COUNT(DISTINCT o.order_id) AS eligible_orders,
       ROUND(AVG(i.item_amount), 2) AS avg_item_amount_brl,
       ROUND(AVG(p.payment_amount), 2) AS avg_payment_amount_brl
FROM orders o
JOIN items i ON i.order_id = o.order_id
JOIN payments p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-07-01'
GROUP BY STRFTIME('%Y-%m', o.order_purchase_timestamp)
ORDER BY purchase_month ASC;
