-- X05: denominator is all payment methods, not just Top 3.
WITH pay_by_type AS (
  SELECT p.payment_type, SUM(p.payment_value) AS payment_amount
  FROM orders o JOIN order_payments p ON p.order_id = o.order_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= '2018-04-01'
    AND o.order_purchase_timestamp < '2018-05-01'
  GROUP BY p.payment_type
)
SELECT payment_type,
       ROUND(payment_amount, 2) AS payment_amount_brl,
       ROUND(100.0 * payment_amount / NULLIF((SELECT SUM(payment_amount) FROM pay_by_type), 0), 2) AS payment_share_pct
FROM pay_by_type
ORDER BY payment_amount DESC, payment_type ASC LIMIT 3;
