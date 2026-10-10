# ZipBasket Reorder Policy (Dark Stores) v3.2

## Purpose
This policy tells store planners and the RestockPilot agents how to decide when and how much to reorder for a dark store.

## Service level
Dark stores promise 10 minute delivery, so a stock-out loses the order. The target service level is 95 percent for every SKU. Safety stock is set with z = 1.65.

## Reorder point
Reorder point = average daily demand x supplier lead time + safety stock.
Safety stock = 1.65 x standard deviation of daily demand x square root of lead time.
Place an order when stock on hand plus stock already on order falls below the reorder point.

## Order quantity
Order enough to cover demand for the lead time plus one review day, plus safety stock, minus stock on hand and on order. Round up to full cases. Never order more than the shelf capacity of the SKU.

## Festival and event uplift
Planners may raise forecast demand for a listed event in the store calendar. The uplift factor comes from the calendar, not from guesswork. News-based changes to demand are limited to between 0.7x and 1.5x and must quote the news source.

## Days of cover and priority
Days of cover = (on hand + on order) / forecast daily demand.
- P1: cover is less than supplier lead time (stock-out before the truck arrives).
- P2: cover is less than lead time + 1 day.
- P3: an order is due but there is no stock-out risk.
- P4: no order needed.
