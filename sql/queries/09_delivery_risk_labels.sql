SELECT
    order_id,
    order_purchase_timestamp,
    order_estimated_delivery_date,
    order_delivered_customer_date,

    CASE
        WHEN DATE(order_delivered_customer_date)
             > DATE(order_estimated_delivery_date)
        THEN 1
        ELSE 0
    END AS is_late

FROM orders

WHERE order_status = 'delivered'
  AND order_delivered_customer_date IS NOT NULL
  AND order_estimated_delivery_date IS NOT NULL;