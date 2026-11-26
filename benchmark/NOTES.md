# Case notes

Why each case has its label. See docs/personal-data-policy.md.

- **pos-storage-001** (positive, django): Phone number and date of birth (tier A) added to Customer as plain fields with no encryption and no retention rule. Model lines required; migration optional. The admin change is unrelated noise.
- **pos-storage-002** (positive, express-prisma): Account holder name and IBAN (tier A) added to Payout in plain text, no encryption or retention. Prisma schema required; SQL migration optional. Fee rounding change is noise.
- **pos-storage-003** (positive, spring-boot): Diagnosis (tier S, special category: health) added to Patient as a plain column with no encryption or retention. Entity field required; Flyway migration optional. Logging level change is noise.
- **pos-logging-001** (positive, django): Signup view logs the user's email (tier A) at info level. The new test uses fake example.com data and is not an issue (policy rule 6).
- **pos-logging-002** (positive, express-prisma): Checkout error handler logs the whole request body, which holds the shipping address and email (policy rule 7). Timeout constant change is noise.
- **pos-logging-003** (positive, spring-boot): Analytics event carries the customer's email (tier A) and client IP (tier B in analytics, policy rule 3). Rounding fix is noise.
- **pos-transfer-001** (positive, django): New CRM sync sends name, email and phone (tier A) to an outside CRM API on every customer save. Payload and call are one issue. Settings line is supporting change.
- **pos-transfer-002** (positive, express-prisma): Delivery addresses (tier A) sent to an outside geocoding service for every order. MAX_STOPS change is noise.
- **pos-transfer-003** (positive, spring-boot): Plain unsalted SHA-256 of customer emails sent to an ad platform for audience matching. A plain hash is still personal data (policy rule 1).
- **pos-erasure-001** (positive, django): Account deletion no longer anonymises the user's orders, which keep shipping addresses. Removed call is required (old side); the removed import is optional. Message text change is noise.
- **pos-erasure-002** (positive, express-prisma): The retention job that deletes users inactive for 24 months is deleted. The whole deleted file is the required issue (old side); removing its registration is optional. The new exchange-rate job is noise.
- **pos-erasure-003** (positive, spring-boot): Hard delete of a user account replaced by a soft-delete flag; the row keeps name, email and address. Weakened erasure, labelled on the new lines.
- **neg-001** (negative, django): Product list pagination refactor. No personal data.
- **neg-002** (negative, express-prisma): In-memory cache for the product catalogue. No personal data.
- **neg-003** (negative, spring-boot): Dependency version bumps. No personal data.
- **neg-004** (negative, express-prisma): Health-check endpoint. No personal data.
- **neg-005** (negative, django): Database index on orders.created_at. No new data stored.
- **neg-006** (negative, spring-boot): Retry around an existing carrier status call. Sends only a tracking number to an existing integration; no new transfer, no personal data.
- **neg-007** (negative, express-prisma): New feature flag for the redesigned basket page. No personal data.
- **neg-008** (negative, django): Template copy fix on the order confirmation page. The template already showed the customer's name; nothing new is stored, logged or sent.
- **neg-009** (negative, spring-boot): Price rounding bug fix. No personal data.
- **neg-010** (negative, express-prisma): Stock sync job sends SKU quantities to the warehouse API. A third party, but no personal data.
- **trk-keyed-hash** (tricky, spring-boot): Login analytics identify the user by an HMAC of the email with a server-side secret: a keyed hash is a pseudonym, not flagged (policy rule 1). Contrast with pos-transfer-003.
- **trk-user-id-log** (tricky, django): Order view logs the order number and internal user id only (policy rule 2).
- **trk-test-fixtures** (tricky, express-prisma): Test fixtures generate fake customers with Faker and example.com emails (policy rule 6).
- **trk-ip-access-log** (tricky, express-prisma): Access-log middleware records method, path, status, duration and client IP in ordinary request logging (policy rule 3).
- **trk-encrypted-column** (tricky, django): New backup email stored in an encrypted field and added to the retention schedule: storage done right, not flagged. Contrast with pos-storage-001.
- **trk-moved-deletion** (tricky, spring-boot): Anonymisation lines move out of UserAccountService into a new AnonymizationService that is still called on deletion: a refactor, not a regression.
- **trk-masked-log** (tricky, django): Password-reset logging masks the email before logging it (policy rule 4).
- **trk-company-data** (tricky, spring-boot): Supplier gets a company name and VAT number: data about a legal person, not personal data (policy rule 5).
