
-- 第一层：把商品和物流信息聚合到订单粒度
WITH item_stats AS (
    SELECT
        i.order_id,

        COUNT(*) AS item_count,
        ROUND(SUM(i.price), 2) AS item_value,
        ROUND(SUM(i.freight_value), 2) AS freight_value,

        -- 仅当全部商品重量有记录时才计算总重量
        CASE
            WHEN COUNT(*) = COUNT(p.product_weight_g)
            THEN SUM(p.product_weight_g)
            ELSE NULL
        END AS total_weight_g,

        COUNT(DISTINCT i.seller_id) AS seller_count,

        -- 只要有一名卖家与客户不同州，就标为1
        CASE
            WHEN COUNT(*) = COUNT(s.seller_state)
            THEN MAX(
                CASE
                    WHEN s.seller_state <> c.customer_state
                    THEN 1 ELSE 0
                END
            )
            ELSE NULL
        END AS has_interstate_seller

    FROM order_items AS i

    JOIN orders AS o
        ON i.order_id = o.order_id

    JOIN customers AS c
        ON o.customer_id = c.customer_id

    LEFT JOIN products AS p
        ON i.product_id = p.product_id

    LEFT JOIN sellers AS s
        ON i.seller_id = s.seller_id

    GROUP BY i.order_id
)

-- 第二层：生成一行一订单的模型数据
SELECT
    o.order_id,
    o.order_purchase_timestamp,
    c.customer_state,

    CAST(STRFTIME('%m', o.order_purchase_timestamp)
        AS INTEGER) AS purchase_month,

    CAST(STRFTIME('%w', o.order_purchase_timestamp)
        AS INTEGER) AS purchase_weekday,

    CAST(STRFTIME('%H', o.order_purchase_timestamp)
        AS INTEGER) AS purchase_hour,

    COALESCE(st.item_count, 0) AS item_count,
    COALESCE(st.item_value, 0) AS item_value,
    st.freight_value,
    st.total_weight_g,
    COALESCE(st.seller_count, 0) AS seller_count,
    st.has_interstate_seller,

    CASE
        WHEN DATE(o.order_delivered_customer_date)
             > DATE(o.order_estimated_delivery_date)
        THEN 1
        ELSE 0
    END AS is_late

FROM orders AS o

JOIN customers AS c
    ON o.customer_id = c.customer_id

LEFT JOIN item_stats AS st
    ON o.order_id = st.order_id

WHERE o.order_status = 'delivered'
  AND DATE(o.order_delivered_customer_date) IS NOT NULL
  AND DATE(o.order_estimated_delivery_date) IS NOT NULL
  AND o.order_purchase_timestamp IS NOT NULL

ORDER BY o.order_purchase_timestamp;
