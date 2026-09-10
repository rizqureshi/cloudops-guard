// Phase 4G-A: a resource-group-scoped budget -- **alerting only**, never
// a hard spending cap. Azure Cost Management budgets generate
// notifications when actual/forecasted spend crosses a threshold; they
// do not, and cannot, stop a resource from continuing to accrue charges
// (task 9's own explicit distinction, restated in
// `docs/deployment/azure-ingestion-production.md`).
//
// **Currency**: `amount` is a plain number with no attached currency --
// Azure Cost Management bills, and evaluates this budget, in the
// subscription's own billing currency. The approved CAD $100/month
// ceiling may be passed directly (as `amount: 100`) **only if the target
// subscription's billing currency is actually CAD** -- Phase 4G-B must
// verify this before deployment (task 9's explicit requirement) and, if
// the subscription bills in a different currency, pass a conservative
// converted amount instead. This module performs no currency conversion
// itself and has no way to detect the subscription's billing currency at
// compile time.

@description('Budget name.')
param budgetName string = 'cog-ingestion-pilot-budget'

@description('Monthly budget amount, in the subscription\'s own billing currency -- see this module\'s own header comment. The Recorded human decision is CAD $100/month before tax; pass 100 only if the subscription bills in CAD.')
param monthlyAmount int = 100

@description('Placeholder-only: the budget-alert recipient email address, set at Phase 4G-B deployment time.')
param alertEmailAddress string = 'placeholder-oncall@example.invalid'

@description('Budget start date -- must be the first day of the *current* calendar month (ISO 8601, e.g. "2026-09-01") at deployment time. **Correction-pass item 10**: no default is provided, deliberately -- Azure requires a Monthly-time-grain budget\'s start date to fall within the currently selected time-grain period, so any fixed, checked-in default would eventually become a past date outside the current month and fail deployment. The deploying workflow/operator must compute this value at dispatch time (e.g. `date -u +%Y-%m-01`), never read it from a checked-in constant.')
param startDate string

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: budgetName
  properties: {
    category: 'Cost'
    amount: monthlyAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actualCostOver80Percent: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [alertEmailAddress]
      }
      actualCostOver100Percent: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: [alertEmailAddress]
      }
      forecastedCostOver100Percent: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [alertEmailAddress]
      }
    }
  }
}

output budgetId string = budget.id
