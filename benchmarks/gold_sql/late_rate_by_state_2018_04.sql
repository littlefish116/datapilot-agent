-- Denominator: delivered April-2018 purchases with both dates, at order grain.
SELECT c.customer_state,
       COUNT(*) AS delivered_with_dates,
       SUM(CASE WHEN DATE(o.order_delivered_customer_date) > DATE(o.order_estimated_delivery_date)
           THEN 1 ELSE 0 END) AS late_orders,
       ROUND(100.0 * AVG(CASE WHEN DATE(o.order_delivered_customer_date) > DATE(o.order_estimated_delivery_date)
           THEN 1.0 ELSE 0.0 END), 2) AS late_rate_pct
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.order_status = 'delivered'
  AND o.order_purchase_timestamp >= '2018-04-01'
  AND o.order_purchase_timestamp < '2018-05-01'
  AND DATE(o.order_delivered_customer_date) IS NOT NULL
  AND DATE(o.order_estimated_delivery_date) IS NOT NULL
GROUP BY c.customer_state
HAVING COUNT(*) >= 100
ORDER BY late_rate_pct DESC, delivered_with_dates DESC;
