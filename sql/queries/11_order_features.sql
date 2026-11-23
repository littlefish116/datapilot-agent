SELECT
    o.order_id,
    o.order_purchase_timestamp,

    -- 特征1：客户所在州
    c.customer_state,

    -- 特征2：购买月份
    CAST(
        STRFTIME('%m', o.order_purchase_timestamp)
        AS INTEGER
    ) AS purchase_month,

    -- 特征3：购买星期
    CAST(
        STRFTIME('%w', o.order_purchase_timestamp)
        AS INTEGER
    ) AS purchase_weekday,

    -- 特征4：商品条目数
    COUNT(i.order_item_id) AS item_count,

    -- 特征5：订单商品金额
    ROUND(
        COALESCE(SUM(i.price), 0), 2
    ) AS item_value,

    -- 预测标签：是否延期
    CASE
        WHEN DATE(o.order_delivered_customer_date)
             > DATE(o.order_estimated_delivery_date)
        THEN 1
        ELSE 0
    END AS is_late

FROM orders AS o

JOIN customers AS c
    ON o.customer_id = c.customer_id

LEFT JOIN order_items AS i
    ON o.order_id = i.order_id

WHERE o.order_status = 'delivered'
  AND DATE(o.order_delivered_customer_date) IS NOT NULL
  AND DATE(o.order_estimated_delivery_date) IS NOT NULL
  AND o.order_purchase_timestamp IS NOT NULL

GROUP BY o.order_id

ORDER BY o.order_purchase_timestamp;