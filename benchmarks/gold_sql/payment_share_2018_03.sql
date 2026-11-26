-- Scope: orders purchased in March 2018 and eventually delivered.
-- Share denominator: total payment_value for this same scope.
-- Different payment types may appear on one order; DO NOT sum distinct order counts across types.
WITH payment_by_type AS (
    SELECT
        p.payment_type,
        COUNT(DISTINCT o.order_id) AS orders_using_type,
        SUM(p.payment_value) AS amount_brl
    FROM orders AS o
    JOIN order_payments AS p ON p.order_id = o.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= '2018-03-01'
      AND o.order_purchase_timestamp < '2018-04-01'
    GROUP BY p.payment_type
)
SELECT
    payment_type,
    orders_using_type,
    ROUND(amount_brl, 2) AS amount_brl,
    ROUND(100.0 * amount_brl / NULLIF(SUM(amount_brl) OVER (), 0), 2) AS amount_share_pct
FROM payment_by_type
ORDER BY amount_brl DESC;
