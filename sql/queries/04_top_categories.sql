-- Delivered-order item value by category, excluding freight and payments.
SELECT
    COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
    COUNT(*) AS item_rows,
    COUNT(DISTINCT o.order_id) AS orders_containing_category,
    ROUND(SUM(i.price), 2) AS delivered_item_value_brl
FROM order_items AS i
JOIN orders AS o ON i.order_id = o.order_id
LEFT JOIN products AS p ON i.product_id = p.product_id
LEFT JOIN category_translation AS t ON p.product_category_name = t.product_category_name
WHERE o.order_status = 'delivered'
GROUP BY COALESCE(t.product_category_name_english, p.product_category_name, 'unknown')
ORDER BY delivered_item_value_brl DESC
LIMIT 20;
