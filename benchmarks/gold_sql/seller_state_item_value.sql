-- Seller-state 'sales' means SUM(order_items.price) for delivered orders, NOT net revenue.
-- A multi-seller order may contribute to several states; state-level distinct orders are non-additive.
SELECT s.seller_state,
       COUNT(DISTINCT o.order_id) AS delivered_orders_with_seller_state,
       ROUND(SUM(i.price), 2) AS item_value_brl
FROM orders o
JOIN order_items i ON i.order_id = o.order_id
JOIN sellers s ON s.seller_id = i.seller_id
WHERE o.order_status = 'delivered'
GROUP BY s.seller_state
ORDER BY item_value_brl DESC;
