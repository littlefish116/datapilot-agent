SELECT
    order_id,
    SUM(payment_value) AS total_payment
FROM order_payments
GROUP BY order_id;