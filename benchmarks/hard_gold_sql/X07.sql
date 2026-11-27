-- X07: exactly TWO payment rows, not >=2 nor sum of payment installments.
WITH per_order AS (
  SELECT o.order_id, COUNT(*) AS payment_rows, SUM(p.payment_value) AS payment_total
  FROM orders o JOIN order_payments p ON p.order_id = o.order_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= '2018-05-01'
    AND o.order_purchase_timestamp < '2018-06-01'
  GROUP BY o.order_id
)
SELECT COUNT(*) AS two_payment_orders,
       ROUND(AVG(payment_total), 2) AS avg_payment_per_order_brl
FROM per_order WHERE payment_rows = 2;
