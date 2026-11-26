-- Item freight sums are first aggregated to the order grain, then averaged.
WITH order_freight AS (
    SELECT o.order_id,
           COALESCE(SUM(i.freight_value), 0) AS freight_brl
    FROM orders o
    LEFT JOIN order_items i ON i.order_id = o.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= '2018-04-01'
      AND o.order_purchase_timestamp < '2018-07-01'
    GROUP BY o.order_id
)
SELECT COUNT(*) AS delivered_orders,
       ROUND(AVG(freight_brl), 2) AS avg_freight_per_order_brl
FROM order_freight;
