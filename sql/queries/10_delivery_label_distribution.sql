WITH labels AS (
    SELECT
        CASE
            WHEN DATE(order_delivered_customer_date)
                 > DATE(order_estimated_delivery_date)
            THEN 1
            ELSE 0
        END AS is_late
    FROM orders
    WHERE order_status = 'delivered'
      AND DATE(order_delivered_customer_date) IS NOT NULL
      AND DATE(order_estimated_delivery_date) IS NOT NULL
)

SELECT
    COUNT(*) AS total_samples,
    SUM(is_late) AS late_orders,
    COUNT(*) - SUM(is_late) AS on_time_orders,
    ROUND(AVG(is_late) * 100.0, 2) AS late_rate_pct
FROM labels;