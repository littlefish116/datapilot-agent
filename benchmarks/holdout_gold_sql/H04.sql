-- H04: top-3 payment types by payment value, November 2017.
SELECT p.payment_type,
       ROUND(SUM(p.payment_value), 2) AS payment_amount_brl
FROM orders AS o
JOIN order_payments AS p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2017-11-01'
  AND o.order_purchase_timestamp < '2017-12-01'
GROUP BY p.payment_type
ORDER BY payment_amount_brl DESC, p.payment_type ASC
LIMIT 3;
