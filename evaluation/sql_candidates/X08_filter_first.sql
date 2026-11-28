-- Research candidate only: filter the relevant delivered orders before
-- aggregating item/payment details. NOT automatically generated or deployed.
-- Q2 2018, order-level average for orders with BOTH item/payment records.
WITH eligible AS (
    SELECT order_id,
           STRFTIME('%Y-%m', order_purchase_timestamp) AS purchase_month
    FROM orders
    WHERE order_status = 'delivered'
      AND order_purchase_timestamp >= '2018-04-01'
      AND order_purchase_timestamp < '2018-07-01'
),
items AS (
    SELECT i.order_id, SUM(i.price) AS item_amount
    FROM order_items AS i
    JOIN eligible AS e ON e.order_id = i.order_id
    GROUP BY i.order_id
),
payments AS (
    SELECT p.order_id, SUM(p.payment_value) AS payment_amount
    FROM order_payments AS p
    JOIN eligible AS e ON e.order_id = p.order_id
    GROUP BY p.order_id
)
SELECT e.purchase_month,
       COUNT(*) AS eligible_orders,
       ROUND(AVG(i.item_amount), 2) AS avg_item_amount_brl,
       ROUND(AVG(p.payment_amount), 2) AS avg_payment_amount_brl
FROM eligible AS e
JOIN items AS i ON i.order_id = e.order_id
JOIN payments AS p ON p.order_id = e.order_id
GROUP BY e.purchase_month
ORDER BY e.purchase_month;
