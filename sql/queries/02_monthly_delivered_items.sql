-- Purchase-month item value for DELIVERED orders, excluding freight.
-- This is not payment amount or recognized/net revenue.
SELECT
    SUBSTR(o.order_purchase_timestamp, 1, 7) AS purchase_month,
    COUNT(DISTINCT o.order_id) AS delivered_orders,
    ROUND(SUM(i.price), 2) AS delivered_item_value_brl,
    ROUND(SUM(i.freight_value), 2) AS freight_brl
FROM orders AS o
JOIN order_items AS i ON o.order_id = i.order_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp IS NOT NULL
GROUP BY SUBSTR(o.order_purchase_timestamp, 1, 7)
ORDER BY purchase_month;
