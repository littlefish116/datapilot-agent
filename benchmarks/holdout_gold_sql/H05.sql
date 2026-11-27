-- H05: payment record count per order (exclude orders with no payment row).
WITH per_order AS (
  SELECT o.order_id, COUNT(p.payment_sequential) AS payment_rows
  FROM orders AS o
  JOIN order_payments AS p ON p.order_id = o.order_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= '2018-06-01'
    AND o.order_purchase_timestamp < '2018-07-01'
  GROUP BY o.order_id
)
SELECT ROUND(AVG(payment_rows), 4) AS avg_payments_per_order
FROM per_order;
