-- H03: aggregate line-item value per order BEFORE AVG, excludes freight.
WITH per_order AS (
  SELECT o.order_id, SUM(oi.price) AS item_value
  FROM orders AS o
  JOIN order_items AS oi ON oi.order_id = o.order_id
  WHERE o.order_status = 'delivered'
    AND o.order_purchase_timestamp >= '2018-05-01'
    AND o.order_purchase_timestamp < '2018-06-01'
  GROUP BY o.order_id
)
SELECT ROUND(AVG(item_value), 2) AS avg_item_value_per_order_brl
FROM per_order;
