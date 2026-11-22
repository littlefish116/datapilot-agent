WITH order_item_counts AS (
    SELECT
        o.order_id,
        COUNT(i.order_item_id) AS item_count
    FROM orders AS o
    LEFT JOIN order_items AS i
        ON o.order_id = i.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY o.order_id
)
SELECT
    ROUND(AVG(item_count), 2) AS avg_items_per_order
FROM order_item_counts;