-- Every delivered order is counted once, even if it has multiple item lines.
WITH order_items_count AS (
    SELECT o.order_id,
           COUNT(i.order_item_id) AS item_count
    FROM orders o
    LEFT JOIN order_items i ON i.order_id = o.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= '2018-03-01'
      AND o.order_purchase_timestamp < '2018-04-01'
    GROUP BY o.order_id
)
SELECT COUNT(*) AS delivered_orders,
       ROUND(AVG(item_count), 4) AS avg_item_rows_per_order
FROM order_items_count;
