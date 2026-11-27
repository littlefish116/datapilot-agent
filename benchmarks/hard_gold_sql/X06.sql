-- X06: item-value grain, order count distinct inside each seller state.
SELECT s.seller_state,
       ROUND(SUM(i.price), 2) AS item_amount_brl,
       COUNT(DISTINCT o.order_id) AS distinct_orders
FROM orders o
JOIN order_items i ON o.order_id = i.order_id
JOIN sellers s ON i.seller_id = s.seller_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-07-01'
GROUP BY s.seller_state
ORDER BY SUM(i.price) DESC, s.seller_state ASC LIMIT 5;
