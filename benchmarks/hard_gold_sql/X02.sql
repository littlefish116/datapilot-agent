-- X02: aggregate separate one-to-many tables BEFORE joining them.
WITH items AS (
  SELECT order_id, SUM(price) AS item_amount FROM order_items GROUP BY order_id
), payments AS (
  SELECT order_id, SUM(payment_value) AS payment_amount FROM order_payments GROUP BY order_id
)
SELECT ROUND(SUM(i.item_amount), 2) AS item_amount_brl,
       ROUND(SUM(p.payment_amount), 2) AS payment_amount_brl
FROM orders o
JOIN items i ON i.order_id = o.order_id
JOIN payments p ON p.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-07-01';
