SELECT
    payment_type,
    ROUND(SUM(payment_value), 2) AS total_payment_value,
    ROUND(
        100.0 * SUM(payment_value) /
        (SELECT SUM(payment_value)
         FROM order_payments),
        2
    ) AS payment_percentage
FROM order_payments
GROUP BY payment_type
ORDER BY total_payment_value DESC;