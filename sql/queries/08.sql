-- 第一层：计算每个月的商品金额
WITH monthly_sales AS (
    SELECT
        SUBSTR(o.order_purchase_timestamp, 1, 7) AS purchase_month,
        SUM(i.price) AS monthly_item_value
    FROM orders AS o
    JOIN order_items AS i
        ON o.order_id = i.order_id
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp IS NOT NULL
    GROUP BY purchase_month
),

-- 第二层：获取上一个月的金额
sales_with_lag AS (
    SELECT
        purchase_month,
        monthly_item_value,
        LAG(monthly_item_value) OVER (
            ORDER BY purchase_month
        ) AS previous_month_value,
        LAG(purchase_month) OVER (
            ORDER BY purchase_month
        ) AS previous_purchase_month
    FROM monthly_sales
)

-- 第三层：计算环比增长率
SELECT
    purchase_month,
    ROUND(monthly_item_value, 2) AS monthly_item_value,
    ROUND(previous_month_value, 2) AS previous_month_value,

    CASE
        -- 判断上一条记录是否恰好是上一个自然月
        WHEN previous_purchase_month =
             STRFTIME(
                 '%Y-%m',
                 DATE(purchase_month || '-01', '-1 month')
             )
        THEN ROUND(
            (monthly_item_value - previous_month_value)
            * 100.0 / NULLIF(previous_month_value, 0),
            2
        )
        ELSE NULL
    END AS mom_growth_pct

FROM sales_with_lag
ORDER BY purchase_month;