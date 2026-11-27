-- H07: four-way join; translation INNER JOIN limits to mapped categories.
SELECT ct.product_category_name_english AS category_english,
       ROUND(SUM(oi.price), 2) AS item_value_brl
FROM orders AS o
JOIN order_items AS oi ON oi.order_id = o.order_id
JOIN products AS pr ON pr.product_id = oi.product_id
JOIN category_translation AS ct ON ct.product_category_name = pr.product_category_name
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-07-01'
GROUP BY ct.product_category_name_english
ORDER BY item_value_brl DESC, category_english ASC
LIMIT 5;
