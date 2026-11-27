-- X01: date parseability rather than only non-null, order grain.
SELECT COUNT(*) AS eligible_orders,
       ROUND(100.0 * SUM(CASE WHEN DATE(order_delivered_customer_date) > DATE(order_estimated_delivery_date) THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0), 2) AS late_rate_pct
FROM orders
WHERE order_status = 'delivered'
  AND order_purchase_timestamp >= '2018-03-01'
  AND order_purchase_timestamp < '2018-04-01'
  AND DATE(order_delivered_customer_date) IS NOT NULL
  AND DATE(order_estimated_delivery_date) IS NOT NULL;
