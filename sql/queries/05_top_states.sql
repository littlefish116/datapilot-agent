SELECT
    c.customer_state,
    COUNT(*) AS delivered_order_count
FROM orders AS o
JOIN customers AS c
    ON o.customer_id = c.customer_id

-- TODO 1：筛选已交付的订单
WHERE o.order_status = 'delivered'

-- TODO 2：按照客户所在州进行分组
GROUP BY c.customer_state

-- TODO 3：按订单数从高到低排序
ORDER BY delivered_order_count DESC

LIMIT 10;