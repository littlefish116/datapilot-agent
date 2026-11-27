-- H06: dates are compared as calendar dates. Invalid dates excluded.
SELECT STRFTIME('%Y-%m', order_purchase_timestamp) AS purchase_month,
       COUNT(*) AS delivered_with_dates,
       ROUND(100.0 * AVG(CASE WHEN DATE(order_delivered_customer_date) > DATE(order_estimated_delivery_date) THEN 1.0 ELSE 0.0 END), 2) AS late_rate_pct
FROM orders
WHERE order_status = 'delivered'
  AND order_purchase_timestamp >= '2018-01-01'
  AND order_purchase_timestamp < '2018-07-01'
  AND DATE(order_delivered_customer_date) IS NOT NULL
  AND DATE(order_estimated_delivery_date) IS NOT NULL
GROUP BY STRFTIME('%Y-%m', order_purchase_timestamp)
ORDER BY purchase_month ASC;
