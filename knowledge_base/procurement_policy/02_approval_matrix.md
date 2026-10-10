# Purchase Order Approval Matrix v2.0

## Auto-approve
A purchase order line can be placed automatically when all of these are true:
- order value is Rs 25,000 or less
- supplier status is "approved"
- no risk flag from market news on that supplier or SKU
- the agent's supplier choice matches the policy checks

## Store manager approval
The store manager must approve a line when any of these are true:
- order value is above Rs 25,000 and up to Rs 2,00,000
- supplier status is "new" (not yet vetted)
- market news raises a risk on the supplier or the SKU
- the agent asked for a demand change from news

## Never allowed (system blocks)
- any order to a supplier with status "blocked"
- any single line above Rs 2,00,000 (needs category head approval outside this tool)
- payment terms above 45 days for a supplier registered as MSME
- quantity above shelf capacity
- purchase price at or above MRP

## Audit
Every decision, approval and rejection is written to the purchase order log in the database with the name of the approver.
