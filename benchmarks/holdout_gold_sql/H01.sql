-- H01: new-period delivered order count.
SELECT COUNT(*) AS delivered_orders
FROM orders
WHERE order_status = 'delivered'
  AND order_purchase_timestamp >= '2017-12-01'
  AND order_purchase_timestamp < '2018-01-01';
