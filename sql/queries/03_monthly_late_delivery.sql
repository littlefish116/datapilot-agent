-- Late = delivered on a calendar DATE later than estimated delivery DATE.
-- Denominator excludes undelivered orders and delivered orders without dates.
SELECT
    SUBSTR(order_purchase_timestamp, 1, 7) AS purchase_month,
    COUNT(*) AS delivered_with_dates,
    SUM(CASE WHEN DATE(order_delivered_customer_date) > DATE(order_estimated_delivery_date)
             THEN 1 ELSE 0 END) AS late_orders,
    ROUND(100.0 * AVG(CASE WHEN DATE(order_delivered_customer_date) > DATE(order_estimated_delivery_date)
             THEN 1.0 ELSE 0.0 END), 2) AS late_rate_pct
FROM orders
WHERE order_status = 'delivered'
  AND order_purchase_timestamp IS NOT NULL
  AND DATE(order_delivered_customer_date) IS NOT NULL
  AND DATE(order_estimated_delivery_date) IS NOT NULL
GROUP BY SUBSTR(order_purchase_timestamp, 1, 7)
ORDER BY purchase_month;
