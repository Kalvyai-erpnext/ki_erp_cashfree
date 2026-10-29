### Cashfree Payments for ERPNext

Technical name: `ki_erp_cashfree` · ERPNext v16 · by kalvyai

Customers pay Payment Requests (from Sales Orders, Sales Invoices or the Webshop cart) with UPI,
cards, net banking or wallets on the Cashfree checkout. The Payment Entry is created automatically.

### Features

- **Cashfree Settings:** App ID, Secret Key, Sandbox / Production, Test Connection. Saving creates
  the *Cashfree* Payment Gateway and Payment Gateway Account.
- **Checkout:** the payment link opens the Cashfree checkout. The Cashfree order is created when
  the customer opens the link, so emailed links don't expire; retries reuse the active order.
- **Verified result:** the payment status is always read from the Cashfree API, never from the
  browser. The amount and currency are checked before the request is marked paid.
- **Signed webhook:** HMAC-SHA256 signature and timestamp checked; catches payments completed
  after the customer closed the browser.
- **Automatic Payment Entry** against the Sales Order / Invoice, also for guest pay links.
- **Check Status with Cashfree** button on the Payment Request, and an hourly check of unpaid
  orders from the last 72 hours (for sites webhooks can't reach).
- **Webshop Pay button:** on the customer's order page, *Pay* opens the Cashfree checkout,
  even when the Webshop checkout uses another gateway (e.g. Cash on Delivery): customers choose
  to pay online or on delivery. Only the customer's own orders can be paid this way (on ERPNext
  v16 the standard button fails for website customers). Can be turned off in Cashfree Settings
  (*Use Cashfree for the Webshop Pay Button*), e.g. to leave the button to another gateway app.

### Requirements

- ERPNext with the `payments` app.
- A Cashfree merchant account (Sandbox or Production App ID and Secret Key).

### Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app ki_erp_cashfree
```

### Setup

1. Open **Cashfree Settings**, enter the App ID and Secret Key, choose the environment and save.
2. Copy the **Webhook URL** into the Cashfree dashboard (Developers → Webhooks, payment events).
   Cashfree only calls HTTPS addresses.
3. Create a Payment Request with the *Cashfree - INR* Payment Gateway Account. Webshop customers
   get the Cashfree checkout from the *Pay* button of their orders.

### Limitations

- One Cashfree account per site.
- When another installed app also overrides the Webshop Pay button (e.g. `ki_erp_payu`), the
  app installed last handles it; turn its Pay button setting off to hand the button to the other.
- Cashfree needs a mobile number for every payment: it's taken from the Payment Request, the
  contact on the order or invoice, or the Customer; set a fallback number in Cashfree Settings.
- Refunds are made from the Cashfree dashboard.

### Contributing

This app uses `pre-commit` (ruff, eslint, prettier, pyupgrade). Tests:

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app ki_erp_cashfree
```

### License

mit
